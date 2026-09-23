"""Piper TTS backend: fully local, open-source, with German voices.

Requires: pip install piper-tts
Falls back gracefully if not installed.

Voice IDs are Piper voice names, e.g. ``de_DE-kerstin-low`` or
``de_DE-thorsten-high``. Multi-speaker voices accept a speaker index
after a colon, e.g. ``de_DE-mls-medium:12``.

Voice models are stored under ``<config-dir>/voices/piper/`` and are
downloaded automatically from Hugging Face on first use.
"""

from __future__ import annotations

import io
import threading
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from openjarvis.core.config import get_config_dir
from openjarvis.core.registry import TTSRegistry
from openjarvis.speech.tts import TTSBackend, TTSResult

_DEFAULT_VOICE_ID = "de_DE-kerstin-low"

# German Piper voices (see https://huggingface.co/rhasspy/piper-voices).
_GERMAN_VOICES: List[str] = [
    "de_DE-kerstin-low",  # female
    "de_DE-ramona-low",  # female
    "de_DE-eva_k-x_low",  # female
    "de_DE-thorsten-high",  # male, best quality
    "de_DE-thorsten-medium",  # male
    "de_DE-thorsten_emotional-medium",  # male, 8 emotional styles
    "de_DE-karlsson-low",  # male
    "de_DE-pavoque-low",  # male
    "de_DE-mls-medium",  # multi-speaker (use "de_DE-mls-medium:<n>")
]


def _parse_voice_id(voice_id: str) -> Tuple[str, Optional[int]]:
    """Split ``name:speaker`` into (name, speaker_id)."""
    voice_id = (voice_id or _DEFAULT_VOICE_ID).strip()
    if ":" in voice_id:
        name, _, speaker = voice_id.partition(":")
        try:
            return name, int(speaker)
        except ValueError as exc:
            raise ValueError(
                f"Invalid Piper speaker index in voice_id {voice_id!r}"
            ) from exc
    return voice_id, None


@TTSRegistry.register("piper")
class PiperTTSBackend(TTSBackend):
    """Piper TTS: local neural voices, including German."""

    backend_id = "piper"

    def __init__(
        self,
        *,
        voices_dir: str = "",
        auto_download: bool = True,
        max_cached_voices: int = 2,
    ) -> None:
        self._voices_dir = (
            Path(voices_dir).expanduser()
            if voices_dir
            else get_config_dir() / "voices" / "piper"
        )
        self._auto_download = auto_download
        self._max_cached = max(1, max_cached_voices)
        self._voices: Dict[str, Any] = {}
        self._lock = threading.Lock()

    # -- internals ---------------------------------------------------------

    def _model_path(self, name: str) -> Path:
        return self._voices_dir / f"{name}.onnx"

    def _ensure_downloaded(self, name: str) -> Path:
        model = self._model_path(name)
        config = model.with_suffix(".onnx.json")
        if model.exists() and config.exists():
            return model
        if not self._auto_download:
            raise RuntimeError(
                f"Piper voice {name!r} not found in {self._voices_dir}. "
                f"Download it with: python -m piper.download_voices {name} "
                f"--download-dir {self._voices_dir}"
            )
        from piper.download_voices import download_voice

        self._voices_dir.mkdir(parents=True, exist_ok=True)
        download_voice(name, self._voices_dir)
        if not model.exists():
            raise RuntimeError(f"Piper voice {name!r} could not be downloaded")
        return model

    def _load_voice(self, name: str) -> Any:
        voice = self._voices.get(name)
        if voice is not None:
            return voice
        from piper import PiperVoice

        model = self._ensure_downloaded(name)
        voice = PiperVoice.load(model)
        if len(self._voices) >= self._max_cached:
            self._voices.pop(next(iter(self._voices)))
        self._voices[name] = voice
        return voice

    # -- TTSBackend API ----------------------------------------------------

    def synthesize(
        self,
        text: str,
        *,
        voice_id: str = _DEFAULT_VOICE_ID,
        speed: float = 1.0,
        output_format: str = "wav",
    ) -> TTSResult:
        from piper import SynthesisConfig

        name, speaker_id = _parse_voice_id(voice_id)
        speed = speed if speed and speed > 0 else 1.0
        syn_config = SynthesisConfig(
            speaker_id=speaker_id,
            length_scale=1.0 / speed,  # Piper: larger = slower
        )

        with self._lock:
            voice = self._load_voice(name)
            sample_rate = int(voice.config.sample_rate)
            pcm = b"".join(
                chunk.audio_int16_bytes for chunk in voice.synthesize(text, syn_config)
            )

        meta = {"backend": "piper", "voice": name, "speaker_id": speaker_id}
        if not pcm:
            return TTSResult(
                audio=b"",
                format=output_format,
                voice_id=voice_id,
                sample_rate=sample_rate,
                metadata=meta,
            )

        fmt = (output_format or "wav").lower()
        if fmt == "wav":
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(sample_rate)
                wav_file.writeframes(pcm)
            audio = buf.getvalue()
        else:
            import numpy as np
            import soundfile as sf

            samples = np.frombuffer(pcm, dtype=np.int16)
            buf = io.BytesIO()
            sf.write(buf, samples, sample_rate, format=fmt.upper())
            audio = buf.getvalue()

        return TTSResult(
            audio=audio,
            format=fmt,
            voice_id=voice_id,
            sample_rate=sample_rate,
            duration_seconds=(len(pcm) // 2) / sample_rate,
            metadata=meta,
        )

    def available_voices(self) -> List[str]:
        downloaded = (
            sorted(p.stem for p in self._voices_dir.glob("*.onnx"))
            if self._voices_dir.exists()
            else []
        )
        return list(dict.fromkeys(downloaded + _GERMAN_VOICES))

    def health(self) -> bool:
        try:
            import piper  # noqa: F401
        except ImportError:
            return False
        return True


__all__ = ["PiperTTSBackend"]
