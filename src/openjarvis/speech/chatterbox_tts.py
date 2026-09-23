"""Chatterbox TTS backend: talks to a local Chatterbox server over HTTP.

Chatterbox Multilingual (Resemble AI, MIT) runs in its own Python
environment (see ``custom/chatterbox_server``) because it pins torch and
transformers to exact versions. This backend only needs the standard library.

``voice_id`` is the name of a reference clip in
``~/.openjarvis/voices/chatterbox/<name>.wav``; an empty ``voice_id`` uses
Chatterbox's built-in voice. The language comes from ``speech.language``
(default ``de``).

Environment:
  CHATTERBOX_URL           server URL (default http://127.0.0.1:8765)
  CHATTERBOX_EXAGGERATION  expressiveness 0..1+ (default 0.5)
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import List

from openjarvis.core.registry import TTSRegistry
from openjarvis.speech.tts import TTSBackend, TTSResult

_DEFAULT_URL = "http://127.0.0.1:8765"


def _configured_language() -> str:
    try:
        from openjarvis.core.config import load_config

        lang = (load_config().speech.language or "").strip().lower()
        return lang[:2] or "de"
    except Exception:
        return "de"


@TTSRegistry.register("chatterbox")
class ChatterboxTTSBackend(TTSBackend):
    """Chatterbox Multilingual via local HTTP server (German, voice cloning)."""

    backend_id = "chatterbox"

    def __init__(
        self,
        *,
        url: str = "",
        language: str = "",
        exaggeration: float | None = None,
        timeout: float = 120.0,
    ) -> None:
        self._url = (url or os.environ.get("CHATTERBOX_URL", _DEFAULT_URL)).rstrip("/")
        self._language = language or _configured_language()
        if exaggeration is None:
            try:
                exaggeration = float(os.environ.get("CHATTERBOX_EXAGGERATION", "0.5"))
            except ValueError:
                exaggeration = 0.5
        self._exaggeration = exaggeration
        self._timeout = timeout

    def _get_json(self, path: str, timeout: float) -> dict:
        with urllib.request.urlopen(self._url + path, timeout=timeout) as resp:
            return json.loads(resp.read())

    def synthesize(
        self,
        text: str,
        *,
        voice_id: str = "",
        speed: float = 1.0,  # Chatterbox hat keine Tempo-Steuerung
        output_format: str = "wav",
    ) -> TTSResult:
        payload = json.dumps(
            {
                "text": text,
                "language": self._language,
                "voice": voice_id or "",
                "exaggeration": self._exaggeration,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            self._url + "/synthesize",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                audio = resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            try:
                detail = json.loads(detail).get("error", detail)
            except ValueError:
                pass
            raise RuntimeError(f"Chatterbox-Server: {detail}") from exc

        sample_rate = 24000
        duration = 0.0
        if audio[:4] == b"RIFF":
            import io
            import wave

            with wave.open(io.BytesIO(audio)) as wav_file:
                sample_rate = wav_file.getframerate()
                duration = wav_file.getnframes() / sample_rate

        fmt = (output_format or "wav").lower()
        if fmt != "wav" and audio:
            import io

            import soundfile as sf

            data, sr = sf.read(io.BytesIO(audio), dtype="int16")
            buf = io.BytesIO()
            sf.write(buf, data, sr, format=fmt.upper())
            audio = buf.getvalue()

        return TTSResult(
            audio=audio,
            format=fmt,
            voice_id=voice_id,
            sample_rate=sample_rate,
            duration_seconds=duration,
            metadata={"backend": "chatterbox", "language": self._language},
        )

    def available_voices(self) -> List[str]:
        try:
            return [""] + list(self._get_json("/health", 2.0).get("voices", []))
        except Exception:
            return [""]

    def health(self) -> bool:
        try:
            return bool(self._get_json("/health", 1.5).get("ok"))
        except Exception:
            return False


__all__ = ["ChatterboxTTSBackend"]
