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


def test_a_file_that_is_not_yaml_is_an_error_of_the_user(tmp_path, capsys):
    """Not a traceback: the file, the line and the column, as for any other mistake in a manifest"""
    from fprime_wsts.cli import main
    from fprime_wsts.system import is_system_manifest, load_system

    broken = tmp_path / "wsts.yaml"
    broken.write_text("deployment: [Payload\nkeep: [comDriver]\n")
    for load in (load_manifest, load_catalog, is_system_manifest, load_system):
        with pytest.raises(WstsError, match=r"wsts\.yaml:2:5: not valid YAML: expected ',' or '\]'"):
            load(broken)
    assert main(["generate", str(broken)]) == 2
    assert "fprime-wsts: error: " in capsys.readouterr().err
    a_list = tmp_path / "list.yaml"
    a_list.write_text("- deployment: Payload\n")
    with pytest.raises(WstsError, match="is a YAML mapping .* not a list"):
        load_manifest(a_list)
    empty = tmp_path / "empty.yaml"
    empty.write_text("# nothing yet\n")
    with pytest.raises(WstsError, match="'deployment' is required"):
        load_manifest(empty)


def test_a_warning_with_ways_out_is_laid_out_for_a_terminal():
    from fprime_wsts.cli import format_warning

    text = ("module Components/Imager links '${SDK_ROOT}/lib/libcamsdk.a': SDK_ROOT is not set in the simulated build. "
            "The topology needs the module for Payload.imager. Either take the module's stub variant (cmake: "
            "{stub_variants: [Components/Imager]}); or substitute that instance; or leave the module out.")
    lines = format_warning(text, width=80).splitlines()
    assert lines[0].startswith("  warning: module Components/Imager links") and all(len(line) <= 80 for line in lines)
    assert "    Ways out:" in lines
    bullets = [line.strip() for line in lines if line.strip().startswith("- ")]
    assert [b.split()[1] for b in bullets] == ["Take", "Substitute", "Leave"], "one way out to a bullet"
    assert " ".join(line.strip() for line in lines).count("Components/Imager") == 2, "nothing is lost"
    plain = format_warning("cmake.cache sets CMAKE_BUILD_TYPE, which project() has cached already.", width=100)
    assert plain == "  warning: cmake.cache sets CMAKE_BUILD_TYPE, which project() has cached already."


@pytest.mark.parametrize("data, message", [
    ({"deployment": "D", "substitutes": []}, r"unknown key\(s\): substitutes \(did you mean 'substitute'\?\)"),
    ({"deployment": "D", "stub_variants": ["A"]}, r"stub_variants \(it goes under 'cmake:'\)"),
    ({"deployment": "D", "cmake": {"stub_variant": ["A"]}}, r"cmake: unknown key\(s\): stub_variant \(did you mean 'stub_variants'\?\)"),
    ({"deployment": "D", "zzz": 1, "modles": []}, r"unknown key\(s\): modles \(did you mean 'models'\?\), zzz$"),
    ({"deployment": "D", "substitute": [{"instance": "a", "with": "B", "endpiont": "x"}]}, r"did you mean 'endpoint'"),
])
def test_an_unknown_key_names_the_one_that_was_meant(data, message):
    with pytest.raises(WstsError, match=message):
        parse_manifest(data)


def test_time_mode_of_a_manifest():
    assert parse_manifest({"deployment": "D"}).time_mode == "real-time"
    assert parse_manifest({"deployment": "D", "time": {"mode": "stepped"}}).time_mode == "stepped"
    assert parse_manifest({"deployment": "D", "time": {"mode": "realtime"}}).time_mode == "real-time"
    with pytest.raises(WstsError, match="time.mode must be 'realtime' or 'stepped', not 'fast'"):
        parse_manifest({"deployment": "D", "time": {"mode": "fast"}})
    with pytest.raises(WstsError, match=r"time: unknown key\(s\): modes \(did you mean 'mode'\?\)"):
        parse_manifest({"deployment": "D", "time": {"modes": "stepped"}})


def test_epoch_of_simulated_time():
    import datetime

    def epoch(value):
        return parse_manifest({"deployment": "D", "time": {"mode": "stepped", "epoch": value}}).time_epoch

    assert parse_manifest({"deployment": "D"}).time_epoch is None
    assert epoch(1893456000) == 1893456000
    assert epoch("2030-01-01T00:00:00Z") == 1893456000
    assert epoch("2030-01-01T01:00:00+01:00") == 1893456000
    assert epoch("2030-01-01") == 1893456000, "a date alone is midnight, UTC"
    assert epoch(datetime.date(2030, 1, 1)) == 1893456000, "which YAML reads as a date"
    for bad in ("next tuesday", 0, 2 ** 40):
        with pytest.raises(WstsError, match="time.epoch"):
            epoch(bad)
