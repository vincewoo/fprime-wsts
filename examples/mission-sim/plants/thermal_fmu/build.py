"""Build Thermal.fmu, the thermal plant of ../thermal.py as an FMI 2.0 co-simulation FMU,
for this machine:

    python examples/mission-sim/plants/thermal_fmu/build.py [output]   # default: Thermal.fmu here

To couple it instead of the Python plant, name it in wsts-system.yaml (`fmu:` in place of
`python:`) and generate again.
"""

import sys
from pathlib import Path

from fprime_wsts.fmi import build_fmu

HERE = Path(__file__).resolve().parent

if __name__ == "__main__":
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "Thermal.fmu"
    print(build_fmu([HERE / "thermal.c"], HERE / "modelDescription.xml", output))
