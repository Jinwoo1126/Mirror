#!/usr/bin/env bash
# Puts META_API_KEY into .env (git ignores it) and restarts the containers so they can learn
# from new memories. The key is typed at a hidden prompt: it never reaches a chat or a history.
set -euo pipefail
cd "$(dirname "$0")/.."
read -r -s -p "Meta API key (hidden): " KEY; echo
[ -n "$KEY" ] || { echo "empty key"; exit 1; }
touch .env; chmod 600 .env
grep -v '^META_API_KEY=' .env > .env.tmp || true
printf 'META_API_KEY=%s\n' "$KEY" >> .env.tmp
mv .env.tmp .env; chmod 600 .env
docker compose up -d user-a user-b >/dev/null 2>&1 && echo "Containers restarted with the key."
