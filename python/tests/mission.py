"""The example the tests run against: examples/mission (flight) and examples/mission-sim
(its simulation project, with the system manifest and one manifest per element).

Tests that need a variation copy the projects into a temporary directory or build a
manifest from scratch; nothing here writes to the examples.
"""

import shutil
from pathlib import Path
from typing import Optional

import pytest
import yaml

from fprime_wsts.manifest import parse_manifest

REPO = Path(__file__).resolve().parents[2]
MISSION = REPO / "examples" / "mission"
SIM = REPO / "examples" / "mission-sim"
SYSTEM = SIM / "wsts-system.yaml"

needs_fpp = pytest.mark.skipif(
    shutil.which("fpp-to-json") is None or not (REPO / "lib" / "fprime" / "cmake").is_dir(),
    reason="needs the FPP tools on PATH and the lib/fprime submodule",
)


def element_data(element: str) -> dict:
    """The checked-in manifest of a system element (`cdh` or `payload`), as data"""
    return yaml.safe_load((SIM / f"{element}.yaml").read_text())


def element_manifest(element: str, path: Optional[Path] = None, **changes):
    """The checked-in manifest of an element, with top-level keys changed (None removes
    one). It keeps its place in the simulation project unless `path` says otherwise."""
    data = element_data(element)
    data.update(changes)
    return parse_manifest({k: v for k, v in data.items() if v is not None}, path or SIM / f"{element}.yaml")


def flight_manifest(deployment: str, **data):
    """A manifest that lives in the flight project (no simulation project)"""
    return parse_manifest({"deployment": deployment, **data})


#: The Cdh entry points, for manifests of Cdh written in a test
CDH_MAIN = element_data("cdh")["main"]


def copy_sim(tmp_path: Path, name: str = "sim") -> Path:
    """The simulation project, copied without its generated output, its manifests pointing
    at the flight project in place"""
    sim = tmp_path / name
    shutil.copytree(SIM, sim, ignore=shutil.ignore_patterns("wsts"))
    for manifest in ("cdh.yaml", "payload.yaml"):
        text = (sim / manifest).read_text().replace("project: ../mission", f"project: {MISSION}")
        (sim / manifest).write_text(text)
    return sim


def copy_flight(tmp_path: Path, name: str = "flight") -> Path:
    """The flight project, copied without generated output, with absolute paths to the
    framework and the VxWorks library"""
    project = tmp_path / name
    shutil.copytree(MISSION, project, ignore=shutil.ignore_patterns("wsts", "build-*"))
    text = (project / "settings.ini").read_text()
    text = text.replace("../../lib/fprime-vxworks", str(REPO / "lib" / "fprime-vxworks"))
    text = text.replace("../../lib/fprime", str(REPO / "lib" / "fprime"))
    (project / "settings.ini").write_text(text)
    return project
