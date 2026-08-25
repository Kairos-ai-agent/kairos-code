"""Allow `python -m kairos` to launch either the server (default)
or the new `kairos exec` non-interactive mode.

Layout:
    python -m kairos                # starts the server (legacy)
    python -m kairos serve          # same as above
    python -m kairos exec "task"    # one-shot, prints result, exits
"""
from kairos.cli import main
import sys

if __name__ == "__main__":
    sys.exit(main())
