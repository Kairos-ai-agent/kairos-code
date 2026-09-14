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
mem() {
  # Memory attribution: the shard that "hung" for 30 minutes was actually killed by
  # the OOM killer (exit 137), which is invisible in a per-test log. Printing free
  # memory around every file turns "somewhere in these 23 files" into a name.
  if command -v free >/dev/null 2>&1; then
    free -m | awk 'NR==2 {printf "%.0f MB used", $3}'
  else
    echo "n/a"
  fi
}

procs() {
  # And *which* process holds it: a stray child left behind by an earlier file
  # looks identical to a spike inside the current one unless the big processes are
  # listed by name.
  if command -v ps >/dev/null 2>&1; then
    ps -eo rss=,comm= --sort=-rss 2>/dev/null | head -3 \
      | awk '{printf "%s(%.0fMB) ", $2, $1/1024}'
  fi
}

for f in $FILES; do
  before=$(mem)
  before_p=$(procs)
  t0=$(date +%s)
  # `python -m pytest`, not `pytest`: a console script in a venv carries an
  # absolute interpreter path in its shebang and silently dies (exit 1, no output)
  # if the checkout has moved since the venv was made.
  #
  # --timeout-method=signal (POSIX only): on timeout pytest-timeout raises inside the
  # test's own thread, so the log names the stuck test and prints its traceback. The
  # default (thread) dumps only the *other* threads — which is how a Linux-only hang in
  # shard 5 produced a page of asyncio-waitpid stacks and no hint of the test waiting
  # for them, leaving the actual culprit invisible.
  METHOD="--timeout-method=signal"
  case "$(uname -s)" in Linux*) ;; *) METHOD="" ;; esac
  python -m pytest "$f" -q --timeout=150 $METHOD --durations=5
  code=$?
  secs=$(( $(date +%s) - t0 ))
  case "$code" in
    0) echo "  ok      ${f}  (${secs}s, mem ${before} -> $(mem), top: ${before_p})" ;;
    5) echo "  skip    ${f}  (no tests collected, ${secs}s, mem ${before} -> $(mem), top: ${before_p})" ;;
    124) echo "  TIMEOUT ${f}  (blocked for ${PER_FILE}s, mem ${before} -> $(mem), top: ${before_p})"
         echo "  ^^^ this file blocked; --timeout cannot see a module that blocks during import"
         rc=1 ;;
    137) echo "  KILLED  ${f}  (SIGKILL after ${secs}s, mem ${before} -> $(mem), top: ${before_p})"
         echo "  ^^^ OOM killer: this file is what ate the runner"
         rc=1 ;;
    *) echo "  FAIL    ${f}  (exit ${code} after ${secs}s, mem ${before} -> $(mem), top: ${before_p})"
       rc=1 ;;
  esac
done

echo "finished: $(date -u +%H:%M:%S)  (exit ${rc})"
exit $rc
