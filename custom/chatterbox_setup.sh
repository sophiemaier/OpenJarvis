#!/usr/bin/env bash
# Richtet Chatterbox Multilingual als zweite, hochwertige Stimme für Sophia ein.
# Installiert in eine eigene Python-Umgebung und startet einen lokalen Server
# (nur auf 127.0.0.1 erreichbar), der beim Login automatisch mitstartet.
#
# Optionen:
#   --no-autostart   Server nicht als Hintergrunddienst einrichten
#   --uninstall      Hintergrunddienst entfernen und Stimme auf Piper zurückstellen
set -euo pipefail

OJ_HOME="${OPENJARVIS_HOME:-$HOME/.openjarvis}"
HERE="$(cd "$(dirname "$0")" && pwd)"
VENV="$OJ_HOME/chatterbox-venv"
PORT="${CHATTERBOX_PORT:-8765}"
LABEL="ch.sophia.chatterbox"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$OJ_HOME/logs"
AUTOSTART=1

case "${1:-}" in
  --no-autostart) AUTOSTART=0 ;;
  --uninstall)
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    jarvis config set speech.tts_backend piper
    jarvis config set speech.voice_id de_DE-kerstin-low
    echo "Chatterbox-Dienst entfernt, Sophia spricht wieder mit Piper."
    echo "Die Umgebung $VENV kannst du bei Bedarf löschen."
    exit 0 ;;
esac

UV="$(command -v uv || echo "$HOME/.local/bin/uv")"
[ -x "$UV" ] || { echo "uv nicht gefunden"; exit 1; }

echo "==> 1/5 Eigene Python-Umgebung anlegen ($VENV)"
[ -x "$VENV/bin/python" ] || "$UV" venv --python 3.11 "$VENV"

echo "==> 2/5 Chatterbox installieren (lädt PyTorch, dauert etwas)"
"$UV" pip install --python "$VENV/bin/python" "chatterbox-tts==0.1.7"

mkdir -p "$OJ_HOME/voices/chatterbox" "$LOG_DIR"

echo "==> 3/5 Server starten"
if [ "$AUTOSTART" = 1 ]; then
  mkdir -p "$(dirname "$PLIST")"
  cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$VENV/bin/python</string>
    <string>$HERE/chatterbox_server/server.py</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>OPENJARVIS_HOME</key><string>$OJ_HOME</string>
    <key>CHATTERBOX_PORT</key><string>$PORT</string>
    <key>PYTORCH_ENABLE_MPS_FALLBACK</key><string>1</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$LOG_DIR/chatterbox.log</string>
  <key>StandardErrorPath</key><string>$LOG_DIR/chatterbox.log</string>
</dict>
</plist>
PLISTEOF
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
else
  PYTORCH_ENABLE_MPS_FALLBACK=1 OPENJARVIS_HOME="$OJ_HOME" CHATTERBOX_PORT="$PORT" \
    nohup "$VENV/bin/python" "$HERE/chatterbox_server/server.py" \
    >> "$LOG_DIR/chatterbox.log" 2>&1 &
fi

echo "==> 4/5 Warten, bis das Modell geladen ist (beim ersten Mal Download von einigen GB)"
for i in $(seq 1 180); do
  if curl -fs "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
    echo "   Server bereit."; break
  fi
  [ "$i" = 180 ] && { echo "   Zeitüberschreitung. Log: $LOG_DIR/chatterbox.log"; exit 1; }
  sleep 5
done

echo "==> 5/5 Sophia auf Chatterbox umstellen und testen"
jarvis config set speech.tts_backend chatterbox
jarvis config set speech.voice_id ""
TMP="$(mktemp -t sophia).wav"
curl -fs -X POST "http://127.0.0.1:$PORT/synthesize" \
  -H "Content-Type: application/json" \
  -d '{"text":"Hallo Sophie, hier ist Sophia mit meiner neuen Stimme. Klingt das besser?","language":"de"}' \
  -o "$TMP" && afplay "$TMP"

echo
echo "Fertig. Eigene Stimme: WAV (ca. 10 s, eine Person, ohne Musik) nach"
echo "  $OJ_HOME/voices/chatterbox/NAME.wav legen, dann:"
echo "  jarvis config set speech.voice_id NAME"
echo "Log: $LOG_DIR/chatterbox.log   Entfernen: $0 --uninstall"
