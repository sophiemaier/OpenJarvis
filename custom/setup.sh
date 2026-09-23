#!/usr/bin/env bash
# Richtet Sophia ein: Piper-Stimme (Deutsch), Persona, Spracheingabe, Datenschutz.
# Mehrfach ausführbar. Bestehende MEMORY.md / USER.md werden nicht überschrieben.
set -euo pipefail

OJ_HOME="${OPENJARVIS_HOME:-$HOME/.openjarvis}"
SRC="$OJ_HOME/src"
PY="$OJ_HOME/.venv/bin/python"
HERE="$(cd "$(dirname "$0")" && pwd)"
VOICE="${SOPHIA_VOICE:-de_DE-kerstin-low}"
VOICES_DIR="$OJ_HOME/voices/piper"

UV="$(command -v uv || echo "$HOME/.local/bin/uv")"
[ -x "$UV" ] || { echo "uv nicht gefunden"; exit 1; }

echo "==> 1/5 Abhängigkeiten installieren (Spracheingabe + Piper)"
"$UV" pip install --python "$PY" -e "$SRC[speech,piper]"

echo "==> 2/5 Persona 'sophia' installieren"
DEST="$OJ_HOME/personas/sophia"
mkdir -p "$DEST"
cp "$HERE/personas/sophia/SOUL.md" "$DEST/SOUL.md"
for f in USER.md MEMORY.md; do
  [ -f "$DEST/$f" ] || cp "$HERE/personas/sophia/$f" "$DEST/$f"
done

echo "==> 3/5 Stimme $VOICE herunterladen"
mkdir -p "$VOICES_DIR"
"$PY" -m piper.download_voices "$VOICE" --download-dir "$VOICES_DIR"

echo "==> 4/5 Konfiguration setzen"
jarvis config set memory_files.persona_name sophia
jarvis config set speech.tts_backend piper
jarvis config set speech.voice_id "$VOICE"
jarvis config set speech.backend faster-whisper
jarvis config set speech.language de
jarvis config set speech.model small
jarvis config set speech.compute_type int8
jarvis config set security.profile personal
jarvis config set analytics.enabled false
if ollama list 2>/dev/null | grep -q "qwen3.5:9b"; then
  jarvis config set intelligence.default_model qwen3.5:9b
else
  echo "   (qwen3.5:9b noch nicht fertig geladen, Modell später umstellen)"
fi

echo "==> 5/5 Stimmtest"
"$PY" - "$VOICE" <<'PYEOF'
import sys, tempfile, subprocess
import openjarvis.speech  # registriert Backends
from openjarvis.core.registry import TTSRegistry
backend = TTSRegistry.get("piper")()
res = backend.synthesize("Hallo Sophie, ich bin Sophia. Schön, dass ich jetzt Deutsch sprechen kann.", voice_id=sys.argv[1])
with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
    f.write(res.audio)
subprocess.run(["afplay", f.name], check=False)
print(f"   OK: {res.duration_seconds:.1f} s Audio mit {sys.argv[1]}")
PYEOF

echo
echo "Fertig. Starte Sophia mit Sprache:  jarvis chat --voice"
