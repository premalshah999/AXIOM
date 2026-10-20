#!/bin/bash
# A throwaway ApplyPilot for showing sign-up and the setup guide without touching production.
#
#   scripts/demo_instance.sh            # http://127.0.0.1:8197, fresh every start
#   PORT=8300 scripts/demo_instance.sh
#   KEEP=1 scripts/demo_instance.sh     # keep the previous demo account and answers
#
# Fresh SQLite database in its own folder, no application workers, no Telegram, no server mailbox.
# Resume reading still uses the language model configured in .env (a fraction of a cent per resume).
# Build the dashboard first if the frontend changed: (cd frontend && npm run build)
set -euo pipefail
cd "$(dirname "$0")/.."
PORT=${PORT:-8197}
DIR=${DEMO_DIR:-.data/demo-$PORT}
if [ -z "${KEEP:-}" ] && [ -d "$DIR" ]; then
  case "$DIR" in .data/demo-*) rm -rf -- "$DIR" ;; *) echo "Refusing to clear $DIR"; exit 1 ;; esac
fi
mkdir -p "$DIR"
export DATA_DIR="$DIR" DATABASE_URL="sqlite:///$PWD/$DIR/demo.db" DBOS_DATABASE_URL=""
export APP_TOKEN=${APP_TOKEN:-demo-access-token}
export ENABLE_WORKERS=false ENABLE_DEMO=false
export TELEGRAM_BOT_TOKEN="" TELEGRAM_USER_ID="" TELEGRAM_ENABLED=false
export GMAIL_ADDRESS="" GMAIL_APP_PASSWORD=""
export SECURE_COOKIES=false BASE_URL="http://127.0.0.1:$PORT"
echo "Demo: $BASE_URL  (fresh sign-up; access token: $APP_TOKEN)"
exec .venv/bin/jobpilot serve --host 127.0.0.1 --port "$PORT"
