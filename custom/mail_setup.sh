#!/usr/bin/env bash
# Gibt Sophia Lesezugriff auf Apple Mail (alle dort eingerichteten Konten).
# Nur lesend: kein Senden, Verschieben, Löschen oder Als-gelesen-Markieren.
set -euo pipefail

OJ_HOME="${OPENJARVIS_HOME:-$HOME/.openjarvis}"
PY="$OJ_HOME/.venv/bin/python"
HERE="$(cd "$(dirname "$0")" && pwd)"

echo "==> 1/4 Persona aktualisieren"
mkdir -p "$OJ_HOME/personas/sophia"
cp "$HERE/personas/sophia/SOUL.md" "$OJ_HOME/personas/sophia/SOUL.md"

echo "==> 2/4 Werkzeuge festlegen (ohne Terminal- und Code-Ausführung)"
jarvis config set tools.enabled "web_search,calculator,think,apple_mail_unread,apple_mail_search,apple_mail_read"

echo "==> 3/4 Agent mit Werkzeugnutzung aktivieren"
jarvis config set agent.default_agent native_react

echo "==> 4/4 Zugriff testen"
echo "   macOS fragt gleich, ob das Terminal Mail steuern darf: bitte 'OK' klicken."
"$PY" - <<'PYEOF'
from openjarvis.tools.apple_mail import AppleMailUnreadTool
r = AppleMailUnreadTool().execute(max_results=3)
print("   OK" if r.success else "   FEHLER")
print(r.content)
PYEOF

echo
echo "Fertig. Frag Sophia zum Beispiel: 'Habe ich neue Mails?'"
