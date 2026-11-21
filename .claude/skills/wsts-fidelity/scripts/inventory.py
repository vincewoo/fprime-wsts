#!/usr/bin/env python3
"""What a simulated deployment or system is made of, and where it differs from flight.

The inventory is part of fprime-wsts (`fprime_wsts/fidelity.py`), so that it follows the report format of the toolkit it ships with.

Usage:
  inventory.py GENERATED_DIR [--summary RUN.json | --live] [--json]

Needs fprime-wsts installed (pip install -e ./python) and its lib/fprime checked out.
"""

import sys
from pathlib import Path

WSTS_ROOT = Path(__file__).resolve().parents[4]  # <checkout>/.claude/skills/<skill>/scripts

try:
    from fprime_wsts.fidelity import main
except ImportError as error:  # pragma: no cover - environment problem, reported plainly
    sys.exit(
        f"inventory.py: cannot import fprime_wsts.fidelity ({error}).\n"
        f"Activate the virtual environment where it is installed, or install it:\n"
        f"  pip install -r {WSTS_ROOT}/lib/fprime/requirements.txt -e {WSTS_ROOT}/python"
    )

if __name__ == "__main__":
    sys.exit(main())
