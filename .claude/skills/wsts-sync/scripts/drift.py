#!/usr/bin/env python3
"""What has moved under a simulation project since it was generated.

The comparison is part of fprime-wsts (`fprime_wsts/drift.py`), so that it follows the report format of the toolkit it ships with.

Usage:
  drift.py [MANIFEST or SIM PROJECT] [--json]

Needs fprime-wsts installed (pip install -e ./python) and its lib/fprime checked out.
"""

import sys
from pathlib import Path

WSTS_ROOT = Path(__file__).resolve().parents[4]  # <checkout>/.claude/skills/<skill>/scripts

try:
    from fprime_wsts.drift import main
except ImportError as error:  # pragma: no cover - environment problem, reported plainly
    sys.exit(
        f"drift.py: cannot import fprime_wsts.drift ({error}).\n"
        f"Activate the virtual environment where it is installed, or install it:\n"
        f"  pip install -r {WSTS_ROOT}/lib/fprime/requirements.txt -e {WSTS_ROOT}/python"
    )

if __name__ == "__main__":
    sys.exit(main())
