#!/bin/bash
# Runs fetch.py (optionally --fast), unlocked — the fast (~20s) and full
# discovery (~2-3min) jobs run their network fetching fully in parallel,
# each writing docs/data.json + events.json atomically (temp file + rename)
# so a concurrent writer can never interleave with it.
#
# Only the git bookkeeping after that is serialized against the other job,
# via a short-lived lock dir — that step is fast (a few seconds), so this
# rarely blocks. Locking the ENTIRE fetch.py run here previously (including
# the full job's ~2-3min network scan) meant the fast job spent most of its
# time waiting on the lock and timing out — that was the real cause of the
# dashboard lagging behind, not the poll/refresh intervals themselves.
set -e
cd "$(dirname "$0")"

# launchd runs this script directly (no login shell), so PATH is whatever
# launchd's own minimal default is — it does NOT include Homebrew or
# ~/.local/bin, where gh/git usually live. That silently broke every run:
# `gh: command not found` on every call, which fetch.py's old error
# handling swallowed into a generic JSON parse error on every PR at once.
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

python3 fetch.py "$@"

LOCKDIR="/tmp/prcheckmonitor.lock"
n=0
while ! mkdir "$LOCKDIR" 2>/dev/null; do
  age=$(( $(date +%s) - $(stat -f %m "$LOCKDIR" 2>/dev/null || echo 0) ))
  if [ "$age" -gt 60 ]; then rmdir "$LOCKDIR" 2>/dev/null || true; fi
  sleep 0.3
  n=$((n+1))
  if [ "$n" -gt 200 ]; then echo "lock timeout, skipping push this cycle"; exit 0; fi
done
trap 'rmdir "$LOCKDIR" 2>/dev/null || true' EXIT

git add docs/data.json docs/events.json
git commit -m "update pr status" --quiet || true
git pull --rebase --autostash --quiet || true
git push --quiet || true
