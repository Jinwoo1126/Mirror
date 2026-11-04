#!/usr/bin/env bash
# The "code" case: the same three coding tasks, given to Muse Code twice, once with each user's
# Mirror attached over MCP. Run it where `muse` can reach its login (for example with `!` in the
# session, or in a terminal where the keychain is unlocked).
#
#   1. review a small file            -> shaped by what that user's Mirror knows about code
#   2. write a commit message          -> shaped by how that user writes them
#   3. record a decision (needs META_API_KEY in .env, see scripts/set-local-key.sh)
#                                      -> Muse Code saves it into that user's Mirror
#
# Results go to docs/cases/code/out/. Your Muse settings are put back when it finishes.
set -uo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"; OUT="$ROOT/docs/cases/code/out"; SAMPLE="$ROOT/docs/cases/code/sample"
SETTINGS="$HOME/.config/muse/settings.json"
mkdir -p "$OUT"

command -v muse >/dev/null || { echo "muse is not installed"; exit 1; }
[ -f "$SETTINGS" ] || { echo "no Muse settings at $SETTINGS (run muse login first)"; exit 1; }

# 1. tokens and edges for both users (an edge refuses to start without its token)
touch .env; chmod 600 .env
for u in A B; do
  grep -q "^MIRROR_TOKEN_$u=" .env || echo "MIRROR_TOKEN_$u=$(openssl rand -hex 24)" >> .env
  tok=$(grep "^MIRROR_TOKEN_$u=" .env | cut -d= -f2-)
  printf '%s' "$tok" > "$HOME/.mirror-token-$(echo $u | tr A-Z a-z)"; chmod 600 "$HOME/.mirror-token-$(echo $u | tr A-Z a-z)"
done
docker compose --profile edge up -d user-a user-b edge-a edge-b >/dev/null 2>&1 || { echo "could not start the containers"; exit 1; }
for i in $(seq 1 30); do
  curl -s -m 2 -o /dev/null http://127.0.0.1:8111/mcp && curl -s -m 2 -o /dev/null http://127.0.0.1:8112/mcp && break; sleep 2
done

if [ -z "$(docker exec mirror-user-a printenv META_API_KEY 2>/dev/null)" ]; then
  echo "NOTE: META_API_KEY is not set in the containers, so step 3 (saving a decision) will fail."
  echo "      Run scripts/set-local-key.sh first if you want it."
fi

# 2. put Muse's settings back however this ends
cp "$SETTINGS" "$OUT/.muse-settings.backup"
restore() { cp "$OUT/.muse-settings.backup" "$SETTINGS"; chmod 600 "$SETTINGS"; rm -f "$OUT/.muse-settings.backup"; }
trap restore EXIT

point_muse_at() {   # $1 = a|b, $2 = port
  python3 - "$SETTINGS" "$2" "$HOME/.mirror-token-$1" <<'PY'
import json, sys
path, port, tokfile = sys.argv[1:4]
cfg = json.load(open(path))
cfg.setdefault("mcp_servers", {})["mirror"] = {
    "transport": "streamable_http", "url": f"http://127.0.0.1:{port}/mcp",
    "headers": {"Authorization": "Bearer " + open(tokfile).read().strip()}}
json.dump(cfg, open(path, "w"), indent=2)
PY
}

run_user() {  # $1 = a|b, $2 = edge port, $3 = the decision this user states
  local u="$1" port="$2" decision="$3" dir; dir="$(mktemp -d)"
  cp "$SAMPLE/invoice.py" "$dir/"
  point_muse_at "$u" "$port"
  echo "=== user $u"
  ( cd "$dir"
    muse exec "Look at invoice.py in this folder. Before you answer, call the mirror_context tool with a one-line description of this task, and let what it returns shape both how you review it and how you suggest shipping it. Give the review in at most 8 lines. Do not mention the tool or that you used it." \
      > "$OUT/$u-1-review.txt" 2> "$OUT/$u-1-review.err"
    muse exec "Write the commit message for a fix that makes apply_discount reject a negative quantity. Call mirror_context first and follow what it says about how I write commit messages. Output only the message, nothing else." \
      > "$OUT/$u-2-commit.txt" 2> "$OUT/$u-2-commit.err"
    muse exec "I decided: $decision Save this with the mirror_remember tool, using my own words, then reply with one short sentence confirming it was saved." \
      > "$OUT/$u-3-remember.txt" 2> "$OUT/$u-3-remember.err" )
  rm -rf "$dir"
  for f in 1-review 2-commit 3-remember; do echo "  $f: $(wc -c < "$OUT/$u-$f.txt" | tr -d ' ') bytes"; done
}

run_user a 8111 "I will write the tests for apply_discount first and ship the change on Monday, not Friday."
run_user b 8112 "I will ship the apply_discount fix behind a flag today and see what happens."
echo
echo "Done. Results are in docs/cases/code/out/ . Tell Claude or look at them there."
