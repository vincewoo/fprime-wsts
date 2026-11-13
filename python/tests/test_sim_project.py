"""What a simulation project can bring besides simulators: clocks of its own, further
libraries, CMake around the flight modules, configuration overrides that beat flight's,
surrogates without a bus endpoint, and run-time environment. Needs the FPP tools."""

import shutil
from pathlib import Path

import pytest
import yaml

from fprime_wsts import fpp
from fprime_wsts.fpp import WstsError
from fprime_wsts.generate import generate, module_name
from fprime_wsts.manifest import LinkEnd, parse_manifest
from fprime_wsts.runner import parse_env

from mission import MISSION as FLIGHT
from mission import SIM, needs_fpp

#: The element of the example system whose manifest uses all of these
ELEMENT = "cdh.yaml"


def manifest_of(sim_project: Path = SIM, **changes):
    data = yaml.safe_load((sim_project / ELEMENT).read_text())
    data["project"] = str(FLIGHT)
    for key, value in changes.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    return parse_manifest(data, sim_project / ELEMENT)


def run(sim_project: Path, out: Path, **changes):
    return generate(FLIGHT, manifest_of(sim_project, **changes), out)


@pytest.fixture(scope="module")
def default(tmp_path_factory):
    return run(SIM, tmp_path_factory.mktemp("simproject") / "out")


@pytest.fixture
def project(tmp_path):
    copy = tmp_path / "sim"
    shutil.copytree(SIM, copy, ignore=shutil.ignore_patterns("wsts"))
    return copy


def replace_in(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text, old
    path.write_text(text.replace(old, new))


# ----------------------------------------------------------------------
# Manifest
# ----------------------------------------------------------------------


def test_manifest_keys():
    manifest = parse_manifest({
        "deployment": "D",
        "simulators": {"instances": ["Sim.clock"]},
        "clocks": [{"instance": "Sim.clock", "hz": 1000}, {"instance": "Sim.slow", "hz": "2.5"}],
        "libraries": {"sim": ["lib/common", "../shared-sim"], "keep": ["lib/x"]},
        "cmake": {"include": ["sim.cmake"], "include_early": ["early.cmake"]},
        "run": {"env": {"SIM_DATA": "./data/stars.bin", "SIM_SEED": 42, "SIM_EMPTY": None}},
        "substitute": [{"instance": "camera", "with": "Sim.Camera", "endpoint": "none"},
                       {"instance": "uart", "with": "Wsts.SimByteStream", "endpoint": "radio"},
                       {"instance": "gps", "with": "Wsts.SimByteStream"}],
    })
    assert [(c.instance, c.hz) for c in manifest.clocks] == [("Sim.clock", 1000.0), ("Sim.slow", 2.5)]
    assert manifest.sim_libraries == ["lib/common", "../shared-sim"]
    assert manifest.cmake_include == ["sim.cmake"] and manifest.cmake_include_early == ["early.cmake"]
    assert manifest.run_env == {"SIM_DATA": "./data/stars.bin", "SIM_SEED": "42", "SIM_EMPTY": ""}
    camera, uart, gps = manifest.substitutions
    assert camera.no_endpoint and camera.endpoint is None
    assert not uart.no_endpoint and uart.endpoint == "radio"
    assert not gps.no_endpoint and gps.endpoint is None, "no endpoint key: named after the instance, as before"


@pytest.mark.parametrize("change, message", [
    ({"clocks": [{"instance": "Sim.clock", "hz": 0}]}, r"hz must be above 0 and at most 100000 \(it is 0\)"),
    ({"clocks": [{"instance": "Sim.clock", "hz": -5}]}, "hz must be above 0"),
    ({"clocks": [{"instance": "Sim.clock", "hz": 2000000}]}, r"at most 100000 \(it is 2000000\)"),
    ({"clocks": [{"instance": "Sim.clock", "hz": "fast"}]}, "is not a number"),
    ({"clocks": [{"instance": "Sim.clock"}]}, "needs 'instance' and 'hz'"),
    ({"clocks": [{"instance": "Sim.clock", "hz": 5, "rate": 2}]}, "unknown key"),
    ({"libraries": {"simulation": ["x"]}}, "libraries: unknown key"),
    ({"run": {"environment": {}}}, "run: unknown key"),
    ({"run": {"env": {"BAD-NAME": 1}}}, "not an environment variable name"),
])
def test_manifest_errors(change, message):
    with pytest.raises(WstsError, match=message):
        parse_manifest({"deployment": "D", **change})


def test_env_options():
    assert parse_env(["A=1", "B=x=y", "EMPTY="]) == {None: {"A": "1", "B": "x=y", "EMPTY": ""}}
    assert parse_env(["A=1", "payload.B=2"], ["cdh", "payload"]) == {None: {"A": "1"}, "payload": {"B": "2"}}
    assert parse_env(None) == {}
    with pytest.raises(WstsError, match="expected NAME=VALUE"):
        parse_env(["JUSTANAME"])
    with pytest.raises(WstsError, match="'rover' is not an element of this system"):
        parse_env(["rover.A=1"], ["cdh", "payload"])
    with pytest.raises(WstsError, match="this is a single deployment"):
        parse_env(["cdh.A=1"])


# ----------------------------------------------------------------------
# Clocks
# ----------------------------------------------------------------------


@needs_fpp
def test_clock_is_set_up_and_reported(default):
    setup = (default.output / "Cdh" / "Top" / "WstsSetup.cpp").read_text()
    assert '    ::SensorSim::simClock.wstsClock(125.0, "");\n' in setup
    assert setup.index("wstsClock") < setup.index("Wsts::Runtime::start(config);")
    assert default.report["simulators"]["clocks"] == [
        {"instance": "SensorSim.simClock", "hz": 125.0, "drives": ["SensorSim.fastGroup.CycleIn"]}]
    assert "`SensorSim.simClock` at 125 Hz drives `SensorSim.fastGroup.CycleIn`" in (default.output / "wsts-report.md").read_text()
    # The clock adds nothing to the dictionary, and the flight tick source is untouched
    clock = next(i for i in default.report["simulators"]["instances"] if i["instance"] == "SensorSim.simClock")
    assert not any(clock["dictionary_items"].values()) and clock["last_id"] is None
    assert default.report["time"]["tick_source"] == "Cdh.vxTimer"


@needs_fpp
@pytest.mark.parametrize("clocks, message", [
    ([{"instance": "SensorSim.fastGroup", "hz": 125}],
     r"SensorSim.fastGroup is a Svc.PassiveRateGroup, not a Wsts.SimCycle. Define an instance of Wsts.SimCycle"),
    ([{"instance": "SensorSim.nothing", "hz": 125}], r"there is no such instance.*adds with 'simulators'"),
    ([{"instance": "rateGroupDriver", "hz": 125}], r"it is a flight instance"),
    ([{"instance": "simClock", "hz": 125}, {"instance": "SensorSim.simClock", "hz": 50}], r"listed twice"),
    ([], r"(?s)SensorSim.simClock is a Wsts.SimCycle with no rate: add it to 'clocks'.*hz: <cycles per second>"),
])
def test_clock_errors(tmp_path, clocks, message):
    with pytest.raises(WstsError, match=message):
        run(SIM, tmp_path / "out", clocks=clocks or None)


@needs_fpp
def test_clock_must_be_connected(project, tmp_path):
    replace_in(project / "SensorSim" / "Top" / "SensorSim.fpp", "      simClock.CycleOut -> fastGroup.CycleIn\n", "")
    with pytest.raises(WstsError, match=r"SensorSim.simClock.CycleOut is not connected to anything"):
        run(project, tmp_path / "out")


# ----------------------------------------------------------------------
# Libraries and CMake of the simulation
# ----------------------------------------------------------------------


@needs_fpp
def test_extra_library_is_a_root_of_its_own(default):
    settings = (default.output / "settings.ini").read_text()
    locations = next(line for line in settings.splitlines() if line.startswith("library_locations:")).split(":")
    names = [Path(entry.strip()).name for entry in locations[1:]]
    assert names[-2:] == ["sim-common", "mission-sim"], "the vendored library, then the project that builds on it"
    assert default.report["simulation_libraries"][0].endswith("lib/sim-common")
    # In the analysis: SensorSim.GyroSim uses SimCommon.Motion, which only that library defines
    gyro = next(i for i in default.report["simulators"]["instances"] if i["instance"] == "SensorSim.gyroSim")
    assert "SensorSim.gyroSim.Profile" in gyro["dictionary_items"]["channels"]
    # Module names are relative to the library's own root, although it lies inside the
    # simulation project: what GyroSim's CMakeLists.txt names in DEPENDS
    types = SIM / "lib" / "sim-common" / "SimCommon" / "Types"
    assert module_name(types, [SIM, SIM / "lib" / "sim-common"]) == "SimCommon_Types"
    assert module_name(types, [SIM]) == "lib_sim-common_SimCommon_Types", "what it would be called otherwise"
    assert "SimCommon_Types" in (SIM / "SensorSim" / "GyroSim" / "CMakeLists.txt").read_text()


@needs_fpp
def test_cmake_is_included_at_the_documented_points(default, project, tmp_path):
    root = (default.output / "CMakeLists.txt").read_text()
    order = [root.index(marker) for marker in (
        "find_package(FPrime",
        "\nfprime_setup_included_code()",
        'add_fprime_subdirectory("${WSTS_FLIGHT_PROJECT}/config/project")',
        "set(WSTS_ADDING_DEFERRED ON)",
        'sim.cmake")',
        'add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/Cdh")',
    )]
    assert order == sorted(order), "after every flight, library and simulation module; before the deployment"
    assert default.report["cmake_includes"]["before_libraries"] == []
    assert default.report["cmake_includes"]["after_modules"][0].endswith("sim.cmake")

    (project / "early.cmake").write_text("# options and functions for this project's library.cmake\n")
    result = run(project, tmp_path / "out", cmake={
        "cache": {"MISSION_SIM_MODULES": [f"SensorSim/{m}" for m in ("SerialSensorSim", "GyroSim", "SimTime", "Top")]},
        "include": ["sim.cmake"], "include_early": ["early.cmake"]})
    root = (result.output / "CMakeLists.txt").read_text()
    assert root.index("find_package(FPrime") < root.index('early.cmake")') < root.index("\nfprime_setup_included_code()")
    assert root.index('early.cmake")') < root.index("function(add_fprime_subdirectory")
    assert root.count("\nfprime_setup_included_code()") == 1
    with pytest.raises(WstsError, match=r"cmake.include names .*missing.cmake, which does not exist"):
        run(project, tmp_path / "out2", cmake={"include": ["missing.cmake"]})


@needs_fpp
def test_early_include_without_anything_else(tmp_path):
    """A simulation project that only needs CMake read before the libraries"""
    sim = tmp_path / "sim"
    sim.mkdir()
    (sim / "early.cmake").write_text("set(SOME_LIBRARY_OPTION ON)\n")
    manifest = parse_manifest({"project": str(FLIGHT), "deployment": "Payload",
                               "cmake": {"include_early": ["early.cmake"]}}, sim / "wsts.yaml")
    result = generate(FLIGHT, manifest, tmp_path / "out")
    root = (result.output / "CMakeLists.txt").read_text()
    assert "set(FPRIME_INCLUDE_FRAMEWORK_CODE OFF)" in root and "function(add_fprime_subdirectory" not in root
    assert root.index("find_package(FPrime") < root.index('early.cmake")') < root.index("fprime_setup_included_code()")


@needs_fpp
def test_library_and_include_errors(project, tmp_path):
    with pytest.raises(WstsError, match=r"libraries.sim names 'SensorSim' .*is not an F Prime library: there is no library.cmake"):
        run(project, tmp_path / "out", libraries={"sim": ["SensorSim"]})
    with pytest.raises(WstsError, match=r"libraries.sim names 'nowhere'"):
        run(project, tmp_path / "out", libraries={"sim": ["nowhere"]})


# ----------------------------------------------------------------------
# Configuration overrides of the simulation
# ----------------------------------------------------------------------


@needs_fpp
def test_simulation_override_wins_in_the_analysis_and_is_built_last(default):
    report = default.report
    simulation = {o["file"]: o for o in report["simulation_config_overrides"]}
    assert sorted(simulation) == ["AcConstants.fpp", "CommandDispatcherImplCfg.hpp"]
    constants = simulation["AcConstants.fpp"]
    assert constants["uses"].endswith("mission-sim/config/AcConstants.fpp") or constants["uses"] == "../../config/AcConstants.fpp"
    assert [Path(r).parent.name for r in constants["replaces"]] == ["project", "config"], \
        "the flight project's override, and the framework default under it"
    # Flight's own overrides are listed apart, without the files the simulation took over
    assert sorted(o["file"] for o in report["config_overrides"]) == ["CdhCoreConfig.fpp", "SensorLinkConfig.fpp"]

    # The value in force is the simulation's: the flight project gives its rate groups 6
    # ports, and the patched model uses a seventh (FPP has checked it)
    top = (default.output / "Cdh" / "Top" / "topology.fpp").read_text()
    assert "rateGroup1Hz.RateGroupMemberOut[6] -> SensorSim.txBuffers.schedIn" in top
    assert "ActiveRateGroupOutputPorts = 6" in (FLIGHT / "config" / "project" / "AcConstants.fpp").read_text()
    assert "ActiveRateGroupOutputPorts = 10" in (SIM / "config" / "AcConstants.fpp").read_text()
    # Table sizes are read from the file the build uses
    table = report["capacity"]["command_table"]
    assert table["size"] == 80 and table["source"].endswith("config/CommandDispatcherImplCfg.hpp")
    assert "mission/config" not in table["source"]
    assert table["used"] > 36, "more opcodes than the flight project's table of 36 holds"

    # The build agrees: F Prime lets the last registration win, and the simulation's
    # configuration module is held back until the flight project's has been registered
    root = (default.output / "CMakeLists.txt").read_text()
    assert "set(WSTS_DEFERRED_MODULES\n" in root and "if (NOT WSTS_ADDING_DEFERRED)" in root
    flight = root.index('add_fprime_subdirectory("${WSTS_FLIGHT_PROJECT}/config/project")')
    simulation_module = root.rindex("/config\")")
    assert root.index("\nfprime_setup_included_code()") < flight < root.index("set(WSTS_ADDING_DEFERRED ON)") < simulation_module
    text = (default.output / "wsts-report.md").read_text()
    assert "## Configuration overrides of the simulation" in text and "## Configuration overrides applied" in text


@needs_fpp
def test_without_the_simulation_override_the_flight_limits_apply(project, tmp_path):
    # Rate group ports: flight's 6 are not enough, and the message says where an override goes
    replace_in(project / "config" / "CMakeLists.txt", '        "${CMAKE_CURRENT_LIST_DIR}/AcConstants.fpp"\n', "")
    (project / "config" / "AcConstants.fpp").unlink()
    with pytest.raises(WstsError) as error:
        run(project, tmp_path / "out")
    message = str(error.value)
    assert "Cdh.rateGroup1Hz (Svc.ActiveRateGroup) has no free RateGroupMemberOut port: all 6 are connected" in message
    assert "ActiveRateGroupOutputPorts" in message and "'clocks'" in message
    assert 'register_fprime_config(' in message and '"${CMAKE_CURRENT_LIST_DIR}/AcConstants.fpp"' in message
    assert "simulation project's library.cmake" in message and "wins even when flight overrides the same file" in message


@needs_fpp
def test_opcode_table_of_the_flight_project_is_too_small(project, tmp_path):
    replace_in(project / "config" / "CMakeLists.txt", '        "${CMAKE_CURRENT_LIST_DIR}/CommandDispatcherImplCfg.hpp"\n', "")
    with pytest.raises(WstsError) as error:
        run(project, tmp_path / "out")
    message = str(error.value)
    assert "registers 42 opcodes" in message and "table holds 36" in message
    assert "mission/config/project/CommandDispatcherImplCfg.hpp" in message
    assert '"${CMAKE_CURRENT_LIST_DIR}/CommandDispatcherImplCfg.hpp"' in message


@needs_fpp
def test_override_of_a_file_nothing_supplies(project, tmp_path):
    (project / "config" / "SimOnlyCfg.fpp").write_text("constant SIM_ONLY = 1\n")
    replace_in(project / "config" / "CMakeLists.txt", "    CONFIGURATION_OVERRIDES\n",
               '    CONFIGURATION_OVERRIDES\n        "${CMAKE_CURRENT_LIST_DIR}/SimOnlyCfg.fpp"\n')
    with pytest.raises(WstsError, match=r"(?s)SimOnlyCfg.fpp is listed under CONFIGURATION_OVERRIDES, but no configuration "
                                        r"module.*F Prime rejects an override of nothing.*AUTOCODER_INPUTS or HEADERS"):
        run(project, tmp_path / "out")


@needs_fpp
def test_overrides_registered_in_library_cmake_cannot_be_held_back(project, tmp_path):
    replace_in(project / "library.cmake", 'add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/config")\n',
               'register_fprime_config(SimConfig CONFIGURATION_OVERRIDES "${CMAKE_CURRENT_LIST_DIR}/config/AcConstants.fpp" INTERFACE)\n')
    with pytest.raises(WstsError, match=r"library.cmake registers CONFIGURATION_OVERRIDES directly.*CMakeLists.txt of its own directory"):
        run(project, tmp_path / "out")


def test_simulation_may_override_a_file_only_the_flight_project_supplies(tmp_path):
    framework, flight, sim = tmp_path / "fw", tmp_path / "flight", tmp_path / "sim"
    for directory in (framework / "default/config", flight / "Config", sim / "config"):
        directory.mkdir(parents=True)
    for file in (flight / "Config/VehicleCfg.fpp", sim / "config/VehicleCfg.fpp"):
        file.write_text("")
    (flight / "Config/CMakeLists.txt").write_text(
        'register_fprime_config(AUTOCODER_INPUTS "${CMAKE_CURRENT_LIST_DIR}/VehicleCfg.fpp")\n')
    (sim / "config/CMakeLists.txt").write_text(
        'register_fprime_config(CONFIGURATION_OVERRIDES "${CMAKE_CURRENT_LIST_DIR}/VehicleCfg.fpp" INTERFACE)\n')
    overrides, warnings = fpp.find_config_overrides([framework, flight, sim])
    assert warnings == [] and len(overrides) == 1
    assert overrides[0].winner == sim / "config/VehicleCfg.fpp" and overrides[0].replaced == [flight / "Config/VehicleCfg.fpp"]
    assert fpp.find_override_modules([sim]) == ([sim / "config"], [])
    assert fpp.find_override_modules([flight]) == ([], [])


def test_table_sizes_follow_header_overrides(tmp_path):
    framework, flight, sim = tmp_path / "fw", tmp_path / "flight", tmp_path / "sim"
    for directory in (framework / "default/config", flight / "cfg", sim / "cfg"):
        directory.mkdir(parents=True)
    for directory, size in ((framework / "default/config", 150), (flight / "cfg", 250), (sim / "cfg", 0x200)):
        (directory / "CommandDispatcherImplCfg.hpp").write_text(
            f"enum {{ CMD_DISPATCHER_DISPATCH_TABLE_SIZE = {size:#x} if size > 250 else size, }};\n".replace(
                f"{size:#x} if size > 250 else size", f"{size:#x}" if size > 250 else str(size)))
    (framework / "default/config/CMakeLists.txt").write_text(
        'register_fprime_config(HEADERS "${CMAKE_CURRENT_LIST_DIR}/CommandDispatcherImplCfg.hpp")\n')
    for root in (flight, sim):
        (root / "cfg/CMakeLists.txt").write_text(
            'register_fprime_config(CONFIGURATION_OVERRIDES "${CMAKE_CURRENT_LIST_DIR}/CommandDispatcherImplCfg.hpp" INTERFACE)\n')
    name, constant = "CommandDispatcherImplCfg.hpp", "CMD_DISPATCHER_DISPATCH_TABLE_SIZE"
    assert fpp.config_constant([framework], framework, name, constant) == (150, framework / "default/config" / name)
    assert fpp.config_constant([framework, flight], framework, name, constant) == (250, flight / "cfg" / name)
    assert fpp.config_constant([framework, flight, sim], framework, name, constant) == (512, sim / "cfg" / name)


# ----------------------------------------------------------------------
# Surrogates without a bus endpoint
# ----------------------------------------------------------------------


@needs_fpp
def test_substitution_without_an_endpoint(default):
    setup = (default.output / "Cdh" / "Top" / "WstsSetup.cpp").read_text()
    assert "chronoTime.wstsBind" not in setup
    assert "// ::Cdh::chronoTime: SensorSim.SimTime does not use the bus" in setup
    assert '::SensorLink::uart.wstsBind("uart", "");' in setup
    assert "instance chronoTime: SensorSim.SimTime base id 0x10010000" in (default.output / "Cdh/Top/instances.fpp").read_text()
    compat = (default.output / "Cdh" / "Top" / "WstsCompat.hpp").read_text()
    assert "namespace Svc { using ChronoTime = ::SensorSim::SimTime; }" in compat
    time_source = next(s for s in default.report["substitutions"] if s["instance"] == "Cdh.chronoTime")
    assert time_source["endpoint"] is None and time_source["chosen_by"] == "manifest"
    assert "| `Cdh.chronoTime` | `Svc.ChronoTime` | `SensorSim.SimTime` | manifest | no bus endpoint |" in \
        (default.output / "wsts-report.md").read_text()
    assert any("Cdh.chronoTime is replaced by SensorSim.SimTime, which has no bus endpoint" in note
               and "do not reach that link" in note for note in default.report["fidelity_notes"])


@needs_fpp
def test_nothing_attaches_to_a_substitution_without_an_endpoint(tmp_path):
    model = [{"name": "clock", "class": "Wsts::ScriptedByteStream", "endpoint": "chronoTime", "kind": "bytestream"}]
    with pytest.raises(WstsError, match=r"model 'clock' attaches to endpoint 'chronoTime', which no surrogate is bound to. "
                                        r"Cdh.chronoTime is substituted with 'endpoint: none'"):
        run(SIM, tmp_path / "out", models=model)
    with pytest.raises(WstsError, match=r"devices: SensorSim.sensorPort: there is no endpoint 'chronoTime'. "
                                        r"Cdh.chronoTime is substituted with 'endpoint: none'"):
        run(SIM, tmp_path / "out", devices=[{"instance": "SensorSim.sensorPort", "endpoint": "chronoTime"}])
    manifest = manifest_of(SIM)
    manifest.system, manifest.element = "Pair", "a"
    manifest.links = [LinkEnd(link="wire", instance="chronoTime", role="listen", peer="b.x")]
    with pytest.raises(WstsError, match=r"link 'wire' ends at Cdh.chronoTime, which is substituted with 'endpoint: none'"):
        generate(FLIGHT, manifest, tmp_path / "out")


@needs_fpp
def test_endpoint_none_from_a_catalog(project, tmp_path):
    (project / "catalog.yaml").write_text(yaml.safe_dump({"surrogates": [
        {"surrogate": "SensorSim.SimTime", "replaces": ["Svc.ChronoTime"], "endpoint": "none"}]}))
    result = run(project, tmp_path / "out", catalogs=["catalog.yaml"], substitute=None)
    time_source = next(s for s in result.report["substitutions"] if s["instance"] == "Cdh.chronoTime")
    assert (time_source["chosen_by"], time_source["endpoint"]) == ("catalog", None)
    assert "chronoTime.wstsBind" not in (result.output / "Cdh" / "Top" / "WstsSetup.cpp").read_text()
    (project / "catalog.yaml").write_text(yaml.safe_dump({"surrogates": [
        {"surrogate": "SensorSim.SimTime", "replaces": ["Svc.ChronoTime"], "endpoint": "clock"}]}))
    with pytest.raises(WstsError, match=r"in a catalog, 'endpoint' can only be 'none'"):
        run(project, tmp_path / "out", catalogs=["catalog.yaml"], substitute=None)


# ----------------------------------------------------------------------
# Run-time environment
# ----------------------------------------------------------------------


@needs_fpp
def test_run_environment_is_carried_to_the_overlay(default, tmp_path):
    run_section = default.report["run"]
    assert run_section["env_paths"] == ["SENSOR_SIM_PROFILE"]
    # Kept relative to the overlay, so the checked-in report does not depend on where it is
    profile = (default.output / run_section["env"]["SENSOR_SIM_PROFILE"]).resolve()
    assert profile == SIM / "profiles" / "calm.txt" and profile.is_file()
    result = run(SIM, tmp_path / "out", run={"env": {"SIM_SEED": 7, "SIM_MODE": "relative/not/a/path"}})
    assert result.report["run"] == {"env": {"SIM_SEED": "7", "SIM_MODE": "relative/not/a/path"}, "env_paths": []}
    assert "Environment: `SENSOR_SIM_PROFILE=" in (default.output / "wsts-report.md").read_text()
