from pathlib import Path

import pytest

from fprime_wsts.fpp import WstsError
from fprime_wsts.manifest import DEFAULT_CATALOG, load_catalog, load_manifest, parse_manifest


def test_minimal_manifest_defaults():
    manifest = parse_manifest({"deployment": "Ref/"})
    assert manifest.deployment == "Ref"
    assert manifest.control_enabled and manifest.bridge == "none" and manifest.tick_hz == 10


def test_full_manifest():
    manifest = parse_manifest({
        "deployment": "Dep",
        "keep": ["comDriver"],
        "substitute": [{"instance": "radio", "with": "Proj.SimRadio", "endpoint": "rf", "options": "a=1"}],
        "models": [{"name": "imu", "class": "Wsts::RegisterMapI2c", "endpoint": "bus", "kind": "I2C",
                    "address": "0x68", "params": {"0x75": 104}}],
        "control": {"enabled": False, "base_id": "0x7000"},
        "main": {"generate": True, "start": "go()", "stop": "halt()"},
        "cmake": {"extra_subdirectories": ["Lib"], "depends": ["Proj_Lib"]},
    })
    assert manifest.substitutions[0].surrogate == "Proj.SimRadio"
    model = manifest.models[0]
    assert (model.kind, model.address, model.params) == ("i2c", 0x68, {"0x75": "104"})
    assert not manifest.control_enabled and manifest.control_base_id == 0x7000
    assert manifest.main.generate and manifest.extra_depends == ["Proj_Lib"]


@pytest.mark.parametrize("control", ["none", False, {"enabled": False}])
def test_control_can_be_disabled(control):
    assert not parse_manifest({"deployment": "D", "control": control}).control_enabled


@pytest.mark.parametrize("data, message", [
    ({}, "'deployment' is required"),
    ({"deployment": "D", "modles": []}, "unknown key"),
    ({"deployment": "D", "substitute": [{"instance": "x"}]}, "needs 'with'"),
    ({"deployment": "D", "models": [{"name": "m", "class": "C", "endpoint": "e", "kind": "can"}]}, "kind must be"),
    ({"deployment": "D", "models": [{"name": "m"}]}, "missing 'class'"),
    ({"deployment": "D", "main": {"generate": True}}, "main.start"),
    ({"deployment": "D", "tick_hz": "fast"}, "not a number"),
])
def test_manifest_errors_say_what_is_wrong(data, message):
    with pytest.raises(WstsError, match=message):
        parse_manifest(data)


def test_default_catalog_covers_the_standard_drivers():
    catalog = {s.name: s for s in load_catalog(DEFAULT_CATALOG)}
    replaced = {component for s in catalog.values() for component in s.replaces}
    assert {"Drv.LinuxI2cDriver", "Drv.LinuxSpiDriver", "Drv.LinuxGpioDriver", "Drv.PosixUartDriver",
            "Svc.LinuxTimer", "VxWorksDrv.VxWorksUartDriver", "VxWorksSvc.VxWatchDogTimer"} <= replaced
    tick = catalog["Wsts.SimTick"]
    assert "mode=thread" in tick.options_when_replacing("VxWorksSvc.VxWatchDogTimer")
    assert tick.options_when_replacing("Svc.LinuxTimer") == "mode=block"
    assert tick.cpp_name == "::Wsts::SimTick"


def test_standalone_simulation_project_paths(tmp_path):
    sim = tmp_path / "sim"
    sim.mkdir()
    (sim / "wsts.yaml").write_text("project: ../flight\ndeployment: Dep\ncatalogs: [cat.yaml]\noutput: out\n")
    manifest = load_manifest(sim / "wsts.yaml")
    assert manifest.sim_root == sim.resolve()
    flight = manifest.project_root()
    assert flight == (tmp_path / "flight").resolve()
    # Flight paths stay relative to the flight project, simulation paths to the manifest
    assert manifest.resolve("Dep", flight) == flight / "Dep"
    assert manifest.resolve_sim("cat.yaml", flight) == sim.resolve() / "cat.yaml"


def test_in_tree_manifest_paths_are_relative_to_the_project():
    manifest = parse_manifest({"deployment": "Dep"})
    assert manifest.sim_root is None and manifest.project_root() is None
    assert manifest.resolve_sim("cat.yaml", Path("/proj")) == Path("/proj/cat.yaml").resolve()
