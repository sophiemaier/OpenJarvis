"""Tests for the Chatterbox backend against the real local server code.

The server runs in-process with a fake model, so no torch or model
download is needed.
"""

from __future__ import annotations

import importlib.util
import io
import threading
import wave
from http.server import ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pytest

_SERVER_PATH = (
    Path(__file__).resolve().parents[2] / "custom" / "chatterbox_server" / "server.py"
)


def _load_server_module():
    spec = importlib.util.spec_from_file_location("cb_server", _SERVER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeModel:
    sr = 24000

    def __init__(self) -> None:
        self.conds = "builtin"
        self.calls = []
        self.prepared = []

    def prepare_conditionals(self, path, exaggeration=0.5):
        self.prepared.append(Path(path).stem)
        self.conds = f"voice:{Path(path).stem}"

    def generate(self, text, language_id, exaggeration=0.5, cfg_weight=0.5):
        self.calls.append((text, language_id, self.conds))
        return np.full((1, 2400), 0.1, dtype=np.float32)  # 0.1 s


@pytest.fixture
def server(tmp_path, monkeypatch):
    mod = _load_server_module()
    monkeypatch.setattr(mod, "VOICES_DIR", tmp_path)
    model = _FakeModel()
    synth = mod.Synthesizer(model, "cpu")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), mod.make_handler(synth))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", model, tmp_path, mod
    httpd.shutdown()


def _backend(url, **kw):
    from openjarvis.speech.chatterbox_tts import ChatterboxTTSBackend

    return ChatterboxTTSBackend(url=url, language="de", **kw)


def test_registered():
    from openjarvis.core.registry import TTSRegistry
    from openjarvis.speech.chatterbox_tts import ChatterboxTTSBackend

    if not TTSRegistry.contains("chatterbox"):
        TTSRegistry.register_value("chatterbox", ChatterboxTTSBackend)
    assert TTSRegistry.get("chatterbox") is ChatterboxTTSBackend


def test_health_up_and_down(server):
    url, *_ = server
    assert _backend(url).health() is True
    assert _backend("http://127.0.0.1:9").health() is False


def test_synthesize_builtin_voice_german(server):
    url, model, *_ = server
    result = _backend(url).synthesize("Hallo Sophie.")
    assert result.format == "wav"
    assert result.sample_rate == 24000
    assert result.duration_seconds == pytest.approx(0.1)
    assert model.calls == [("Hallo Sophie.", "de", "builtin")]


def test_reference_voice_prepared_once(server):
    url, model, voices_dir, _ = server
    (voices_dir / "sophia.wav").write_bytes(b"RIFF")
    backend = _backend(url)
    backend.synthesize("Eins.", voice_id="sophia")
    backend.synthesize("Zwei.", voice_id="sophia")
    backend.synthesize("Drei.")
    assert model.prepared == ["sophia"]
    assert [c[2] for c in model.calls] == ["voice:sophia", "voice:sophia", "builtin"]
    assert "sophia" in backend.available_voices()


def test_unknown_voice_gives_clear_error(server):
    url, *_ = server
    with pytest.raises(RuntimeError, match="nicht gefunden"):
        _backend(url).synthesize("Test.", voice_id="gibtsnicht")


def test_path_traversal_rejected(server):
    url, *_ = server
    with pytest.raises(RuntimeError, match="Ungültiger Stimmname"):
        _backend(url).synthesize("Test.", voice_id="../../etc/passwd")


def test_long_text_split_and_joined(server):
    url, model, *_ = server
    text = " ".join(f"Das ist Satz Nummer {i} mit etwas Inhalt." for i in range(20))
    result = _backend(url).synthesize(text)
    assert len(model.calls) > 1
    assert all(len(c[0]) <= 250 for c in model.calls)
    with wave.open(io.BytesIO(result.audio)) as wav_file:
        assert wav_file.getnframes() > 2400 * len(model.calls)


def test_split_text_handles_overlong_sentence():
    mod = _load_server_module()
    chunks = mod.split_text("a, " * 300)
    assert chunks and all(len(c) <= 250 for c in chunks)
