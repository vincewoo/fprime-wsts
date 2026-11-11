import pytest

from fprime_sse.fpp import SseError
from fprime_sse.manifest import DEFAULT_CATALOG, load_catalog, parse_manifest


def test_minimal_manifest_defaults():
    manifest = parse_manifest({"deployment": "Ref/"})
    assert manifest.deployment == "Ref"
    assert manifest.control_enabled and manifest.bridge == "none" and manifest.tick_hz == 10


def test_full_manifest():
    manifest = parse_manifest({
        "deployment": "Dep",
        "keep": ["comDriver"],
        "substitute": [{"instance": "radio", "with": "Proj.SimRadio", "endpoint": "rf", "options": "a=1"}],
        "models": [{"name": "imu", "class": "Sse::RegisterMapI2c", "endpoint": "bus", "kind": "I2C",
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
    with pytest.raises(SseError, match=message):
        parse_manifest(data)


def test_default_catalog_covers_the_standard_drivers():
    catalog = {s.name: s for s in load_catalog(DEFAULT_CATALOG)}
    replaced = {component for s in catalog.values() for component in s.replaces}
    assert {"Drv.LinuxI2cDriver", "Drv.LinuxSpiDriver", "Drv.LinuxGpioDriver", "Drv.PosixUartDriver",
            "Svc.LinuxTimer", "VxWorksDrv.VxWorksUartDriver", "VxWorksSvc.VxWatchDogTimer"} <= replaced
    tick = catalog["Sse.SimTick"]
    assert "mode=thread" in tick.options_when_replacing("VxWorksSvc.VxWatchDogTimer")
    assert tick.options_when_replacing("Svc.LinuxTimer") == "mode=block"
    assert tick.cpp_name == "::Sse::SimTick"
