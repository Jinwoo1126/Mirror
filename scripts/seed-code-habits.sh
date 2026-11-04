#!/usr/bin/env bash
# Gives each demo user the coding habits used in the "code" case (docs/cases/code).
# They are stored with Mirror's "tell Mirror about you" feature, so no model key is needed.
# Safe to run twice: a statement that is already there is skipped.
set -euo pipefail
cd "$(dirname "$0")/.."
seed() {  # $1 = port, $2 = file
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    body=$(python3 -c 'import json,sys; print(json.dumps({"statement": sys.argv[1]}))' "$line")
    curl -fsS -m 30 -X POST -H 'Content-Type: application/json' -d "$body" "http://127.0.0.1:$1/api/declare" >/dev/null \
      && echo "  :$1  $line"
  done < "$2"
}
echo "User A"; seed 8011 docs/cases/code/habits-a.txt
echo "User B"; seed 8012 docs/cases/code/habits-b.txt
