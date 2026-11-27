#!/usr/bin/env sh
cd "$(dirname "$0")" || exit 1
command -v node >/dev/null || { echo "Instale o Node.js (https://nodejs.org) e rode de novo."; exit 1; }
[ -d node_modules ] || npm install --omit=dev
( sleep 2; (xdg-open http://localhost:8080/admin || open http://localhost:8080/admin) >/dev/null 2>&1 ) &
exec node server.js
