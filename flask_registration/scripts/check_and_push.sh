#!/usr/bin/env bash
# Run the full test suite on exactly the committed code and push only when it
# is green (the working rule in CLAUDE.md). Run from anywhere:
#
#     scripts/check_and_push.sh
#
# Stops without pushing when:
#   - flask_registration/ has uncommitted changes to tracked files (the suite
#     would not test exactly what gets pushed);
#   - app/ or tests/ hold new files that are not committed (pytest would
#     collect or import them, but they would not be pushed); files that
#     .gitignore covers do not count;
#   - pytest exits with anything but 0;
#   - the commit or the tracked files changed while the suite was running.
# The full pytest output goes to .check_and_push.log; only the last lines
# are shown. The test order is random (pytest-randomly, requirements-dev.txt):
# the script picks the seed, shows it before the run, writes it at the top of
# the log and shows it again on a red run, to repeat exactly that order.
#
# The suite runs in parallel (pytest-xdist, -n auto: one worker per core, each
# with its own order from the same seed). Emergency brake: GUF_SERIAL=1 runs
# it in one process, as before:
#
#     GUF_SERIAL=1 scripts/check_and_push.sh
set -euo pipefail

cd "$(dirname "$0")/.."
LOG=.check_and_push.log
PY=.venv/bin/python
TAIL_LINES=15

dirty() {
    git status --porcelain --untracked-files=no -- .
}

# New files in app/ or tests/ that git does not track (ignored files are left out).
untracked() {
    git status --porcelain --untracked-files=all -- app tests | grep '^??' || true
}

if [ -n "$(dirty)" ]; then
    echo "Stopped: uncommitted changes in flask_registration/; commit them first:" >&2
    dirty >&2
    exit 1
fi

if [ -n "$(untracked)" ]; then
    echo "Stopped: new files in app/ or tests/ that are not committed; the suite would run them" >&2
    echo "but they would not be pushed. Commit, move or delete them first:" >&2
    untracked >&2
    exit 1
fi

HEAD_BEFORE=$(git rev-parse HEAD)
BRANCH=$(git branch --show-current)
SEED=$("$PY" -c 'import random; print(random.randrange(1, 2**31))')

if [ "${GUF_SERIAL:-}" = "1" ]; then
    PARALLEL=()
    MODE="serial (GUF_SERIAL=1)"
else
    if ! "$PY" -c 'import xdist' 2>/dev/null; then
        echo "Stopped: pytest-xdist is missing; pip install -r requirements-dev.txt (or GUF_SERIAL=1)." >&2
        exit 1
    fi
    PARALLEL=(-n auto)
    MODE="parallel (-n auto)"
fi
echo "Running the full suite on $(git log --oneline -1), $MODE, test order seed $SEED (log: $LOG) ..."

# pytest's own exit code, not that of a pipe: no pipe at all.
status=0
echo "Test order seed: $SEED, $MODE" >"$LOG"
"$PY" -m pytest -q -p no:cacheprovider -p randomly --randomly-seed="$SEED" ${PARALLEL[@]+"${PARALLEL[@]}"} \
    >>"$LOG" 2>&1 || status=$?

tail -n "$TAIL_LINES" "$LOG"

if [ "$status" -ne 0 ]; then
    echo "Stopped: pytest exited with $status; nothing pushed. Failures:" >&2
    grep '^FAILED\|^ERROR' "$LOG" >&2 || true
    echo "Test order seed: $SEED (repeat: $PY -m pytest -p no:cacheprovider --randomly-seed=$SEED ${PARALLEL[*]:-})" >&2
    exit "$status"
fi

if [ "$(git rev-parse HEAD)" != "$HEAD_BEFORE" ] || [ -n "$(dirty)" ] || [ -n "$(untracked)" ]; then
    echo "Stopped: the code changed while the suite was running; nothing pushed. Run again." >&2
    exit 1
fi

echo "Green on $HEAD_BEFORE; pushing $BRANCH."
git push origin "$BRANCH"
