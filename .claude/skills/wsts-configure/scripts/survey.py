#!/usr/bin/env python3
"""Survey F´ projects checked out beside fprime-wsts, for configuring a simulation.

The survey itself is part of fprime-wsts (`fprime_wsts/survey.py`; `fprime-wsts survey` and
`fprime-wsts init` are the same code). This script adds the skill's defaults: it searches
the siblings of the fprime-wsts checkout, and writes starter manifests with --starter-dir.

Usage:
  survey.py [DIR ...] [--deployment NAME]... [--no-trial] [--json]
            [--starter-dir DIR [--no-submodule]]

A new simulation project written with --starter-dir gets this checkout as a git submodule at
lib/fprime-wsts, as `fprime-wsts init` gives it.

Needs fprime-wsts installed (pip install -e ./python) and its lib/fprime checked out.
"""

import sys
from pathlib import Path

WSTS_ROOT = Path(__file__).resolve().parents[4]  # <checkout>/.claude/skills/<skill>/scripts

try:
    from fprime_wsts.survey import main
except ImportError as error:  # pragma: no cover - environment problem, reported plainly
    sys.exit(
        f"survey.py: cannot import fprime_wsts ({error}).\n"
        f"Activate the virtual environment where it is installed, or install it:\n"
        f"  pip install -r {WSTS_ROOT}/lib/fprime/requirements.txt -e {WSTS_ROOT}/python"
    )

if __name__ == "__main__":
    sys.exit(main(default_dirs=[WSTS_ROOT.parent]))
