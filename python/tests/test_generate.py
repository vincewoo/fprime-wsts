"""Generator runs against the example projects. Needs the FPP tools (fprime-fpp) on PATH."""

import json
import shutil
from pathlib import Path

import pytest
import yaml

from fprime_sse.fpp import SseError
from fprime_sse.generate import generate
from fprime_sse.manifest import parse_manifest

REPO = Path(__file__).resolve().parents[2]
LINUX = REPO / "examples" / "linux-demo"
VX = REPO / "examples" / "vx-demo"

pytestmark = pytest.mark.skipif(
    shutil.which("fpp-to-json") is None or not (REPO / "lib" / "fprime" / "cmake").is_dir(),
    reason="needs the FPP tools on PATH and the lib/fprime submodule",
)


def manifest_of(project: Path, deployment: str, **changes):
    data = yaml.safe_load((project / deployment / "sse.yaml").read_text())
    data.update(changes)
    return parse_manifest(data)


def test_linux_demo_plan_and_output(tmp_path):
    result = generate(LINUX, manifest_of(LINUX, "LinuxDemo"), tmp_path / "out")
    report = result.report
    chosen = {s["instance"]: (s["surrogate"], s["chosen_by"]) for s in report["substitutions"]}
    assert chosen == {
        "LinuxDemo.linuxTimer": ("Sse.SimTick", "catalog"),
        "LinuxDemo.imuBus": ("Sse.SimI2c", "catalog"),
        "LinuxDemo.sensorUart": ("Sse.SimByteStream", "catalog"),
        "LinuxDemo.heaterPin": ("Sse.SimGpio", "catalog"),
        "LinuxDemo.switchPin": ("Sse.SimGpio", "catalog"),
    }, "comDriver is kept by the manifest"
    instances = (result.output / "LinuxDemo" / "Top" / "instances.fpp").read_text()
    assert "instance comDriver: Drv.TcpClient" in instances
    assert "instance imuBus: Sse.SimI2c base id 0x10022000" in instances, "base IDs do not move"
    # SSE instances sit above every flight ID
    assert report["control"]["setup_base_id"] > 0x10025000
    assert report["dictionary"]["flight_ids_unchanged"]
    assert any(item.endswith("sseControl.FAULT_SET") for item in report["dictionary"]["added"]["commands"])
    setup = (result.output / "LinuxDemo" / "Top" / "SseSetup.cpp").read_text()
    assert '::LinuxDemo::imuBus.sseBind("imuBus", "");' in setup
    assert 'attachI2c("imuBus", 0x68, &model_imu)' in setup
    compat = (result.output / "LinuxDemo" / "Top" / "SseCompat.hpp").read_text()
    assert "namespace Drv { using LinuxGpioDriver = ::Sse::SimGpio; }" in compat
    assert json.loads((result.output / "sse-report.json").read_text())["topology"] == "LinuxDemo.LinuxDemo"


def test_checked_in_examples_are_current():
    for project, deployment in ((LINUX, "LinuxDemo"), (VX, "VxDemo")):
        result = generate(project, manifest_of(project, deployment), check=True)
        assert result.changed == [], f"regenerate {deployment}: fprime-sse generate {project / deployment / 'sse.yaml'}"


def test_control_none_adds_nothing_to_the_dictionary(tmp_path):
    result = generate(LINUX, manifest_of(LINUX, "LinuxDemo", control="none"), tmp_path / "out")
    assert not any(result.report["dictionary"]["added"].values())
    topology = (result.output / "LinuxDemo" / "Top" / "topology.fpp").read_text()
    assert "instance sseSetup" in topology and "sseControl" not in topology


def test_vxworks_deployment_converts_without_its_toolchain(tmp_path):
    result = generate(VX, manifest_of(VX, "VxDemo"), tmp_path / "out")
    report = result.report
    assert {s["flight_component"] for s in report["substitutions"]} == {
        "VxWorksSvc.VxWatchDogTimer", "VxWorksDrv.VxWorksUartDriver"}
    timer = next(s for s in report["substitutions"] if s["surrogate"] == "Sse.SimTick")
    assert "mode=thread" in timer["options"]
    assert any("fprime-vxworks" in library for library in report["libraries"]["dropped"])
    assert report["dropped_cmake_options"] == ["VX_TARGET_TYPE=DKM", "FPRIME_PLATFORM=VxWorks"]
    settings = (result.output / "settings.ini").read_text()
    assert "vxworks" not in settings.lower() and "default_toolchain: native" in settings
    assert "vxDemoStart(hostname, port);" in (result.output / "VxDemo" / "SseMain.cpp").read_text()


def test_unmapped_restricted_component_is_an_error_with_candidates(tmp_path):
    manifest = manifest_of(VX, "VxDemo")
    manifest.catalogs = []
    import fprime_sse.generate as generator

    original = generator.load_catalog
    try:
        # Catalog that knows the surrogates but not which VxWorks components they replace
        def stripped(path):
            entries = original(path)
            for entry in entries:
                entry.replaces = [r for r in entry.replaces if not r.startswith("VxWorks")]
            return entries

        generator.load_catalog = stripped
        result = generate(VX, manifest, tmp_path / "out")
    finally:
        generator.load_catalog = original
    # Exactly one surrogate fits each instance's connected ports, so both resolve structurally
    assert {s["chosen_by"] for s in result.report["substitutions"]} == {"structural"}


@pytest.mark.parametrize("changes, message", [
    ({"keep": ["noSuchInstance"]}, "not in topology"),
    ({"models": [{"name": "m", "class": "C", "endpoint": "nowhere", "kind": "i2c"}]}, "no surrogate is bound"),
    ({"substitute": [{"instance": "imu", "with": "Sse.SimGpio"}]}, "cannot replace"),
    ({"substitute": [{"instance": "imuBus", "with": "Sse.Missing"}]}, "unknown component"),
    ({"control": {"base_id": "0x10022000"}}, "overlaps"),
])
def test_plan_errors(tmp_path, changes, message):
    with pytest.raises(SseError, match=message):
        generate(LINUX, manifest_of(LINUX, "LinuxDemo", **changes), tmp_path / "out")


def test_refuses_to_overwrite_foreign_directories(tmp_path):
    (tmp_path / "precious.txt").write_text("keep me")
    with pytest.raises(SseError, match="refusing to overwrite"):
        generate(LINUX, manifest_of(LINUX, "LinuxDemo"), tmp_path)


def test_upstream_ref_with_telemetry_packets(tmp_path):
    """Ref defines telemetry packets and its topology shares its module's name"""
    project = REPO / "lib" / "fprime" / "TestDeploymentsProject"
    result = generate(project, parse_manifest({"deployment": "Ref", "keep": ["comDriver"]}), tmp_path / "out")
    assert [s["instance"] for s in result.report["substitutions"]] == ["Ref.linuxTimer"]
    assert result.report["dictionary"]["removed"] == {"commands": [], "events": [], "channels": [], "params": []}
    packets = (result.output / "Ref" / "Top" / "RefPackets.fppi").read_text()
    assert "sseControl.SimTicks" in packets.split("omit {")[1]
    assert len(result.report["project_modules"]) == 7
