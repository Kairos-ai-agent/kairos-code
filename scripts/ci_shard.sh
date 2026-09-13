#!/usr/bin/env bash
# Run one shard of the test suite, one file at a time, with a hard bound on each
# file.
#
# Why this exists: a shard whose files add up to ~44 seconds on a 2-vCPU runner
# burned a full 30 minutes twice and was then cancelled — and a cancelled job keeps
# no logs at all, so there was nothing to diagnose with. The block is invisible to
# pytest's --timeout because that only wraps tests: a module that blocks while
# being *imported or collected* never reaches one. Running one file at a time with
# a bound turns "a shard hung somewhere" into "this file blocked for 420 seconds",
# in a log that exists because the shard always exits on its own.
#
# Usage: bounds on the whole shard are applied by the caller, e.g.
#   timeout 2400 bash scripts/ci_shard.sh 3 6
set -u

SHARD="${1:?usage: ci_shard.sh <shard> [shards]}"
SHARDS="${2:-6}"
PER_FILE="${PER_FILE_TIMEOUT:-420}"

FILES=$(python scripts/split_tests.py --shard "$SHARD" --shards "$SHARDS")
echo "shard ${SHARD}/${SHARDS}: $(echo "$FILES" | wc -w) files"
echo "started: $(date -u +%H:%M:%S)"

rc=0
for f in $FILES; do
  t0=$(date +%s)
  # `python -m pytest`, not `pytest`: a console script in a venv carries an
  # absolute interpreter path in its shebang and silently dies (exit 1, no output)
  # if the checkout has moved since the venv was made.
  if python -m pytest "$f" -q --timeout=150 --durations=5; then
    echo "  ok    ${f}  ($(( $(date +%s) - t0 ))s)"
  else
    code=$?
    echo "  FAIL  ${f}  (exit ${code} after $(( $(date +%s) - t0 ))s)"
    if [ "$code" = "124" ]; then
      echo "  ^^^ TIMEOUT: ${f} blocked for ${PER_FILE}s — this is the file to look at"
    fi
    rc=1
  fi
done

echo "finished: $(date -u +%H:%M:%S)  (exit ${rc})"
exit $rc
