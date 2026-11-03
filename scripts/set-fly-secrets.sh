#!/usr/bin/env bash
# Sets Mirror's secrets on Fly in one go. Run it in your own terminal: the DB password
# and API keys are typed at hidden prompts, so they never reach a chat transcript or a
# shell history. Values are staged; nothing changes on the running app until `fly deploy`.
set -euo pipefail

APP="${1:-mirror-jinwoo1126}"
command -v fly >/dev/null || { echo "fly is not installed"; exit 1; }

echo "Supabase dashboard -> Connect -> Session pooler: copy the connection string."
read -r -p "Connection string (keeps the [YOUR-PASSWORD] placeholder): " TEMPLATE
case "$TEMPLATE" in
  *'[YOUR-PASSWORD]'*) ;;
  *) echo "The string must contain [YOUR-PASSWORD]. Paste it as the dashboard shows it."; exit 1 ;;
esac
case "$TEMPLATE" in
  *:6543/*) echo "That is the Transaction pooler (6543). Use the Session pooler (5432): the worker needs session locks."; exit 1 ;;
esac
read -r -s -p "Database password (hidden): " PW; echo
[ -n "$PW" ] || { echo "empty password"; exit 1; }

# The password goes into a URL, so special characters must be percent-encoded.
DSN="$(TEMPLATE="$TEMPLATE" PW="$PW" python3 -c \
  'import os, urllib.parse as u; print(os.environ["TEMPLATE"].replace("[YOUR-PASSWORD]", u.quote(os.environ["PW"], safe="")))')"

echo "Project Settings -> API Keys: the anon / publishable key (public, safe to see)."
read -r -p "Supabase anon key: " ANON
[ -n "$ANON" ] || { echo "empty key"; exit 1; }
read -r -s -p "Meta API key (hidden, Enter to skip for now): " META; echo
read -r -p "Operator name shown in the privacy notice: " OPERATOR
read -r -p "Contact email shown in the privacy notice: " CONTACT

{
  printf 'MIRROR_PG_DSN=%s\n' "$DSN"
  printf 'SUPABASE_ANON_KEY=%s\n' "$ANON"
  printf 'MIRROR_OPERATOR_NAME=%s\n' "$OPERATOR"
  printf 'MIRROR_CONTACT_EMAIL=%s\n' "$CONTACT"
  [ -n "$META" ] && printf 'META_API_KEY=%s\n' "$META"
} | fly secrets import -a "$APP" --stage

echo
fly secrets list -a "$APP"
echo "Staged. Tell Claude it is done and it will deploy."
