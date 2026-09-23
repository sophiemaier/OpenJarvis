"""Lokaler Chatterbox-TTS-Server für Sophia.

Läuft in einer eigenen Python-Umgebung (Chatterbox pinnt torch, transformers
usw. auf feste Versionen, die mit OpenJarvis kollidieren können). OpenJarvis
spricht ihn über HTTP an, das Modell bleibt dabei dauerhaft geladen.

Endpunkte:
  GET  /health       -> {"ok": true, "device": "mps", "voices": [...]}
  POST /synthesize   JSON {text, language?, voice?, exaggeration?, cfg_weight?}
                     -> audio/wav

Referenzstimmen: WAV-Dateien in ~/.openjarvis/voices/chatterbox/<name>.wav
(ca. 10 Sekunden, eine Person, ohne Musik). Leere voice = eingebaute Stimme.

Umgebungsvariablen:
  CHATTERBOX_HOST    (Standard 127.0.0.1)
  CHATTERBOX_PORT    (Standard 8765)
  CHATTERBOX_DEVICE  (Standard: mps falls verfügbar, sonst cpu)
  CHATTERBOX_VOICES  (Standard ~/.openjarvis/voices/chatterbox)
"""

from __future__ import annotations

import io
import json
import os
import re
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional

VOICES_DIR = Path(
    os.environ.get(
        "CHATTERBOX_VOICES",
        str(Path(os.environ.get("OPENJARVIS_HOME", "~/.openjarvis")).expanduser()
            / "voices" / "chatterbox"),
    )
).expanduser()

_MAX_CHARS_PER_CHUNK = 250  # Chatterbox bricht bei sehr langen Eingaben ab
_MAX_TEXT_CHARS = 5000
_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


def split_text(text: str, max_chars: int = _MAX_CHARS_PER_CHUNK) -> List[str]:
    """Text an Satzgrenzen in Stücke von höchstens max_chars teilen."""
    sentences = re.split(r"(?<=[.!?…])\s+", text.strip())
    chunks: List[str] = []
    current = ""
    for sentence in sentences:
        if not sentence:
            continue
        # Überlange Einzelsätze an Kommas bzw. hart teilen
        while len(sentence) > max_chars:
            cut = sentence.rfind(",", 0, max_chars)
            cut = cut + 1 if cut > max_chars // 3 else max_chars
            chunks.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if current and len(current) + 1 + len(sentence) > max_chars:
            chunks.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        chunks.append(current)
    return chunks


def to_wav_bytes(samples: Any, sample_rate: int) -> bytes:
    """Float-Samples (-1..1) als 16-bit-Mono-WAV kodieren."""
    import numpy as np

    arr = np.clip(np.asarray(samples, dtype=np.float32).reshape(-1), -1.0, 1.0)
    pcm = (arr * 32767.0).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm)
    return buf.getvalue()


class Synthesizer:
    """Hält das Modell und die vorbereiteten Stimmen im Speicher."""

    def __init__(self, model: Any, device: str) -> None:
        self.model = model
        self.device = device
        self.sample_rate = int(model.sr)
        self._lock = threading.Lock()
        self._builtin_conds = getattr(model, "conds", None)
        self._voice_conds: Dict[str, Any] = {}

    def voices(self) -> List[str]:
        if not VOICES_DIR.exists():
            return []
        return sorted(p.stem for p in VOICES_DIR.glob("*.wav"))

    def _select_voice(self, voice: str, exaggeration: float) -> None:
        if not voice:
            if self._builtin_conds is None:
                raise ValueError("Keine eingebaute Stimme verfügbar, bitte voice angeben")
            self.model.conds = self._builtin_conds
            return
        if not _NAME_RE.match(voice):
            raise ValueError(f"Ungültiger Stimmname: {voice!r}")
        if voice not in self._voice_conds:
            ref = VOICES_DIR / f"{voice}.wav"
            if not ref.exists():
                raise ValueError(f"Stimme {voice!r} nicht gefunden in {VOICES_DIR}")
            self.model.prepare_conditionals(str(ref), exaggeration=exaggeration)
            self._voice_conds[voice] = self.model.conds
        self.model.conds = self._voice_conds[voice]

    def synthesize(
        self,
        text: str,
        *,
        language: str = "de",
        voice: str = "",
        exaggeration: float = 0.5,
        cfg_weight: float = 0.5,
    ) -> bytes:
        import numpy as np

        text = (text or "").strip()
        if not text:
            raise ValueError("Leerer Text")
        if len(text) > _MAX_TEXT_CHARS:
            raise ValueError(f"Text zu lang (max. {_MAX_TEXT_CHARS} Zeichen)")

        pause = np.zeros(int(self.sample_rate * 0.15), dtype=np.float32)
        parts: List[Any] = []
        with self._lock:
            self._select_voice(voice, exaggeration)
            for chunk in split_text(text):
                wav = self.model.generate(
                    chunk,
                    language_id=language,
                    exaggeration=exaggeration,
                    cfg_weight=cfg_weight,
                )
                arr = wav.detach().cpu().numpy() if hasattr(wav, "detach") else wav
                parts.append(np.asarray(arr, dtype=np.float32).reshape(-1))
                parts.append(pause)
        return to_wav_bytes(np.concatenate(parts[:-1]), self.sample_rate)


def make_handler(synth: Synthesizer):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:  # leiser loggen
            pass

        def _json(self, status: int, payload: Dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path == "/health":
                self._json(200, {"ok": True, "device": synth.device,
                                 "sample_rate": synth.sample_rate,
                                 "voices": synth.voices()})
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self) -> None:
            if self.path != "/synthesize":
                self._json(404, {"error": "not found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 100_000:
                    raise ValueError("Ungültige Anfragegrösse")
                req = json.loads(self.rfile.read(length))
                audio = synth.synthesize(
                    str(req.get("text", "")),
                    language=str(req.get("language") or "de"),
                    voice=str(req.get("voice") or ""),
                    exaggeration=float(req.get("exaggeration", 0.5)),
                    cfg_weight=float(req.get("cfg_weight", 0.5)),
                )
            except ValueError as exc:
                self._json(400, {"error": str(exc)})
                return
            except Exception as exc:  # Modellfehler
                self._json(500, {"error": f"{type(exc).__name__}: {exc}"})
                return
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(audio)))
            self.end_headers()
            self.wfile.write(audio)

    return Handler


def load_model(device: Optional[str] = None) -> Synthesizer:
    import torch
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS

    if not device:
        device = "mps" if torch.backends.mps.is_available() else "cpu"
    model = ChatterboxMultilingualTTS.from_pretrained(device=device)
    return Synthesizer(model, device)


def main() -> None:
    host = os.environ.get("CHATTERBOX_HOST", "127.0.0.1")
    port = int(os.environ.get("CHATTERBOX_PORT", "8765"))
    VOICES_DIR.mkdir(parents=True, exist_ok=True)
    print("Lade Chatterbox Multilingual (beim ersten Start wird das Modell geladen) ...")
    synth = load_model(os.environ.get("CHATTERBOX_DEVICE") or None)
    print(f"Bereit auf http://{host}:{port} (Gerät: {synth.device})")
    ThreadingHTTPServer((host, port), make_handler(synth)).serve_forever()


if __name__ == "__main__":
    main()
