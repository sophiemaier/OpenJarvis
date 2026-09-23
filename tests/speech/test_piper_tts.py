"""Tests for the Piper TTS backend (no real model download needed)."""

from __future__ import annotations

import io
import sys
import types
import wave

import pytest

from openjarvis.core.registry import TTSRegistry


class _FakeChunk:
    def __init__(self, data: bytes) -> None:
        self.audio_int16_bytes = data


class _FakeVoice:
    loads = 0

    def __init__(self) -> None:
        self.config = types.SimpleNamespace(sample_rate=22050)
        self.last_syn_config = None

    @classmethod
    def load(cls, model_path):
        cls.loads += 1
        return cls()

    def synthesize(self, text, syn_config=None):
        self.last_syn_config = syn_config
        yield _FakeChunk(b"\x01\x00" * 100)
        yield _FakeChunk(b"\x02\x00" * 100)


class _FakeSynthesisConfig:
    def __init__(self, speaker_id=None, length_scale=None, **kwargs):
        self.speaker_id = speaker_id
        self.length_scale = length_scale


@pytest.fixture
def fake_piper(monkeypatch, tmp_path):
    _FakeVoice.loads = 0
    downloaded = []

    def fake_download(voice, download_dir, force_redownload=False):
        downloaded.append(voice)
        (download_dir / f"{voice}.onnx").write_bytes(b"model")
        (download_dir / f"{voice}.onnx.json").write_text("{}")

    piper_mod = types.ModuleType("piper")
    piper_mod.PiperVoice = _FakeVoice
    piper_mod.SynthesisConfig = _FakeSynthesisConfig
    dl_mod = types.ModuleType("piper.download_voices")
    dl_mod.download_voice = fake_download
    monkeypatch.setitem(sys.modules, "piper", piper_mod)
    monkeypatch.setitem(sys.modules, "piper.download_voices", dl_mod)
    return downloaded


def _backend(tmp_path):
    from openjarvis.speech.piper_tts import PiperTTSBackend

    return PiperTTSBackend(voices_dir=str(tmp_path))


def test_piper_registered():
    from openjarvis.speech.piper_tts import PiperTTSBackend

    if not TTSRegistry.contains("piper"):
        TTSRegistry.register_value("piper", PiperTTSBackend)
    assert TTSRegistry.get("piper") is PiperTTSBackend


def test_synthesize_returns_valid_wav(fake_piper, tmp_path):
    result = _backend(tmp_path).synthesize("Hallo, ich bin Sophia.")
    assert result.format == "wav"
    assert result.sample_rate == 22050
    with wave.open(io.BytesIO(result.audio)) as wav_file:
        assert wav_file.getframerate() == 22050
        assert wav_file.getnframes() == 200
    assert result.duration_seconds == pytest.approx(200 / 22050)


def test_auto_download_once_and_cache(fake_piper, tmp_path):
    backend = _backend(tmp_path)
    backend.synthesize("Eins", voice_id="de_DE-thorsten-high")
    backend.synthesize("Zwei", voice_id="de_DE-thorsten-high")
    assert fake_piper == ["de_DE-thorsten-high"]
    assert _FakeVoice.loads == 1


def test_speed_and_speaker_mapping(fake_piper, tmp_path):
    backend = _backend(tmp_path)
    backend.synthesize("Test", voice_id="de_DE-mls-medium:12", speed=2.0)
    voice = backend._voices["de_DE-mls-medium"]
    assert voice.last_syn_config.speaker_id == 12
    assert voice.last_syn_config.length_scale == pytest.approx(0.5)


def test_invalid_speaker_raises(fake_piper, tmp_path):
    with pytest.raises(ValueError):
        _backend(tmp_path).synthesize("Test", voice_id="de_DE-mls-medium:abc")


def test_no_auto_download_raises(fake_piper, tmp_path):
    from openjarvis.speech.piper_tts import PiperTTSBackend

    backend = PiperTTSBackend(voices_dir=str(tmp_path), auto_download=False)
    with pytest.raises(RuntimeError, match="not found"):
        backend.synthesize("Test")


def test_available_voices_include_german_and_downloaded(fake_piper, tmp_path):
    (tmp_path / "en_GB-alan-medium.onnx").write_bytes(b"m")
    voices = _backend(tmp_path).available_voices()
    assert "de_DE-kerstin-low" in voices
    assert "en_GB-alan-medium" in voices


def test_health_true_with_piper(fake_piper, tmp_path):
    assert _backend(tmp_path).health() is True
