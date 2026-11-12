"""Generator runs against the example projects. Needs the FPP tools (fprime-fpp) on PATH."""

import json

import pytest

from fprime_wsts.fpp import WstsError
from fprime_wsts.generate import generate
from fprime_wsts.manifest import load_manifest, parse_manifest
from mission import CDH_MAIN, MISSION, REPO, copy_sim, flight_manifest, needs_fpp

pytestmark = needs_fpp

#: A manifest for Payload that lives in the flight project and uses stock models only
PAYLOAD_MODELS = [
    {"name": "imu", "class": "Wsts::RegisterMapI2c", "endpoint": "i2cBus", "kind": "i2c", "address": 0x68,
     "params": {"0x75": "0x68"}},
]


def payload_manifest(**changes):
    return flight_manifest("Payload", **{"models": PAYLOAD_MODELS, **changes})


def cdh_manifest(**changes):
    return flight_manifest("Cdh", **{"keep": ["comDriver"], "main": CDH_MAIN, **changes})


def test_manifest_in_the_flight_project(tmp_path):
    """No simulation project: drivers replaced from the toolkit catalog, stock models"""
    result = generate(MISSION, payload_manifest(keep=["hubDriver"]), tmp_path / "out")
    report = result.report
    chosen = {s["instance"]: (s["surrogate"], s["chosen_by"]) for s in report["substitutions"]}
    assert chosen == {
        "Payload.linuxTimer": ("Wsts.SimTick", "catalog"),
        "Payload.i2cBus": ("Wsts.SimI2c", "catalog"),
        "Payload.sensorUart": ("Wsts.SimByteStream", "catalog"),
        "Payload.heaterPin": ("Wsts.SimGpio", "catalog"),
        "Payload.switchPin": ("Wsts.SimGpio", "catalog"),
        "Payload.spiBus": ("Wsts.SimSpi", "catalog"),
    }, "hubDriver is kept by the manifest"
    assert "simulation_project" not in report
    instances = (result.output / "Payload" / "Top" / "instances.fpp").read_text()
    assert "instance hubDriver: Drv.TcpClient" in instances
    assert "instance i2cBus: Wsts.SimI2c base id 0x20032000" in instances, "base IDs do not move"
    # WSTS instances sit above every flight ID
    assert report["control"]["setup_base_id"] > 0x20036000
    assert report["dictionary"]["flight_ids_unchanged"]
    assert any(item.endswith("wstsControl.FAULT_SET") for item in report["dictionary"]["added"]["commands"])
    setup = (result.output / "Payload" / "Top" / "WstsSetup.cpp").read_text()
    assert '::Payload::i2cBus.wstsBind("i2cBus", "");' in setup
    assert 'attachI2c("i2cBus", 0x68, &model_imu)' in setup
    compat = (result.output / "Payload" / "Top" / "WstsCompat.hpp").read_text()
    assert "namespace Drv { using LinuxGpioDriver = ::Wsts::SimGpio; }" in compat
    assert json.loads((result.output / "wsts-report.json").read_text())["topology"] == "Payload.Payload"


def test_default_output_is_in_the_flight_project():
    # Check mode generates into a scratch directory and writes nothing
    result = generate(MISSION, payload_manifest(), check=True)
    assert result.output == MISSION / "wsts" / "Payload"
    assert not (MISSION / "wsts").exists()


def test_control_none_adds_nothing_to_the_dictionary(tmp_path):
    result = generate(MISSION, payload_manifest(control="none"), tmp_path / "out")
    assert not any(result.report["dictionary"]["added"].values())
    topology = (result.output / "Payload" / "Top" / "topology.fpp").read_text()
    assert "instance wstsSetup" in topology and "wstsControl" not in topology


def test_vxworks_deployment_converts_without_its_toolchain(tmp_path):
    result = generate(MISSION, cdh_manifest(), tmp_path / "out")
    report = result.report
    # The hub driver (a Drv.TcpServer) is replaced only when a system ends a link at it
    assert {s["flight_component"] for s in report["substitutions"]} == {
        "VxWorksSvc.VxWatchDogTimer", "VxWorksDrv.VxWorksUartDriver"}
    timer = next(s for s in report["substitutions"] if s["surrogate"] == "Wsts.SimTick")
    assert "mode=thread" in timer["options"]
    # The platform library stays, for one file: the project's configuration module overrides
    # the VxWorksCfg.hpp it supplies, and F Prime rejects an override of nothing. Nothing else
    # of the library is built on the host (test_flight_shapes.py has the details).
    assert not report["libraries"]["dropped"]
    assert any("fprime-vxworks" in library for library in report["libraries"]["kept"])
    assert [entry["supplies"] for entry in report["libraries"]["kept_for_configuration"]] == ["VxWorksCfg.hpp"]
    assert report["dropped_cmake_options"] == ["VX_TARGET_TYPE=DKM", "FPRIME_PLATFORM=VxWorks"]
    settings = (result.output / "settings.ini").read_text()
    assert "default_toolchain: native" in settings and "FPRIME_PLATFORM" not in settings
    assert "cdhStart(hostname, port, 50050);" in (result.output / "Cdh" / "WstsMain.cpp").read_text()


def test_unmapped_restricted_component_is_an_error_with_candidates(tmp_path):
    manifest = cdh_manifest()
    manifest.catalogs = []
    import fprime_wsts.generate as generator

    original = generator.load_catalog
    try:
        # Catalog that knows the surrogates but not which VxWorks components they replace
        def stripped(path):
            entries = original(path)
            for entry in entries:
                entry.replaces = [r for r in entry.replaces if not r.startswith("VxWorks")]
            return entries

        generator.load_catalog = stripped
        result = generate(MISSION, manifest, tmp_path / "out")
    finally:
        generator.load_catalog = original
    # Exactly one surrogate fits each instance's connected ports, so both resolve structurally
    vxworks = [s for s in result.report["substitutions"] if s["flight_component"].startswith("VxWorks")]
    assert len(vxworks) == 2 and {s["chosen_by"] for s in vxworks} == {"structural"}


@pytest.mark.parametrize("changes, message", [
    ({"keep": ["noSuchInstance"]}, "not in topology"),
    ({"models": [{"name": "m", "class": "C", "endpoint": "nowhere", "kind": "i2c"}]}, "no surrogate is bound"),
    ({"substitute": [{"instance": "imu", "with": "Wsts.SimGpio"}]}, "cannot replace"),
    ({"substitute": [{"instance": "i2cBus", "with": "Wsts.Missing"}]}, "unknown component"),
    ({"control": {"base_id": "0x20032000"}}, "overlaps"),
])
def test_plan_errors(tmp_path, changes, message):
    with pytest.raises(WstsError, match=message):
        generate(MISSION, payload_manifest(**changes), tmp_path / "out")


def test_refuses_to_overwrite_foreign_directories(tmp_path):
    (tmp_path / "precious.txt").write_text("keep me")
    with pytest.raises(WstsError, match="refusing to overwrite"):
        generate(MISSION, payload_manifest(), tmp_path)


def test_upstream_ref_with_telemetry_packets(tmp_path):
    """Ref defines telemetry packets and its topology shares its module's name"""
    project = REPO / "lib" / "fprime" / "TestDeploymentsProject"
    result = generate(project, parse_manifest({"deployment": "Ref", "keep": ["comDriver"]}), tmp_path / "out")
    assert [s["instance"] for s in result.report["substitutions"]] == ["Ref.linuxTimer"]
    assert result.report["dictionary"]["removed"] == {"commands": [], "events": [], "channels": [], "params": []}
    packets = (result.output / "Ref" / "Top" / "RefPackets.fppi").read_text()
    assert "wstsControl.SimTicks" in packets.split("omit {")[1]
    assert len(result.report["project_modules"]) == 7


def test_standalone_simulation_project(tmp_path):
    sim = copy_sim(tmp_path)
    flight_before = sorted(p for p in MISSION.rglob("*") if "build-" not in str(p))
    result = generate(MISSION, load_manifest(sim / "payload.yaml"))
    assert result.output == sim / "wsts" / "Payload", "default output is in the simulation project"
    assert sorted(p for p in MISSION.rglob("*") if "build-" not in str(p)) == flight_before, "flight project not written"

    chosen = {s["instance"]: s["surrogate"] for s in result.report["substitutions"]}
    assert chosen["Payload.heaterPin"] == chosen["Payload.switchPin"] == "Sim.SimLinuxGpio"
    assert not any("Pin." in e for e in result.report["dictionary"]["removed"]["events"]), "mirror keeps GPIO events"
    assert result.report["simulation_project"] == "../.."

    settings = (result.output / "settings.ini").read_text()
    locations = [l for l in settings.splitlines() if l.startswith("library_locations:")][0]
    assert "../.." in locations.split(": ", 1)[1].split(":"), "the simulation project is a library location"
    compat = (result.output / "Payload" / "Top" / "WstsCompat.hpp").read_text()
    assert "#include <Surrogates/SimLinuxGpio/SimLinuxGpio.hpp>" in compat
    setup = (result.output / "Payload" / "Top" / "WstsSetup.cpp").read_text()
    assert "#include <Models/ImuModel.hpp>" in setup and "Sim::ImuModel model_imu;" in setup
    # Two models on one I2C endpoint, at their own addresses
    assert 'attachI2c("i2cBus", 0x68, &model_imu)' in setup and 'attachI2c("i2cBus", 0x40, &model_power)' in setup


def test_simulation_project_needs_a_library_cmake_for_its_modules(tmp_path):
    sim = copy_sim(tmp_path)
    (sim / "library.cmake").unlink()
    with pytest.raises(WstsError, match="no library.cmake"):
        generate(MISSION, load_manifest(sim / "payload.yaml"))
