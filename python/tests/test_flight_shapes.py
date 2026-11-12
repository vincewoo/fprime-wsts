"""Shapes of real flight projects that used to stop the generated project from configuring
or building: a library that only supplies configuration, flight code that includes the
header of a replaced driver, a module that adds another module, a root CMakeLists.txt that
does more than include F Prime, a module that links a file of the target's SDK, and an
assert at start-up. The example has the first four (`examples/README.md`).

The generator tests need the FPP tools; the rest read text only.
"""

import json
import shutil
from pathlib import Path

import pytest

from fprime_wsts import flight_cmake, fpp
from fprime_wsts.fpp import WstsError
from fprime_wsts.generate import alias_declaration, generate
from fprime_wsts.runner import explain_assert, find_assert, read_file_ids, topology_instances

from mission import CDH_MAIN, MISSION, REPO, copy_flight, flight_manifest, needs_fpp

UART_HEADER = "VxWorks/Drv/VxWorksUartDriver/VxWorksUartDriver.hpp"


def cdh(**changes):
    """A manifest for Cdh that lives in the flight project"""
    return flight_manifest("Cdh", **{"keep": ["comDriver"], "main": CDH_MAIN, **changes})


def payload(**changes):
    return flight_manifest("Payload", **changes)


@pytest.fixture(scope="module")
def default(tmp_path_factory):
    return generate(MISSION, cdh(), tmp_path_factory.mktemp("shapes") / "out")


def replace_in(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text, old
    path.write_text(text.replace(old, new))


def root_lines(result) -> list:
    return (result.output / "CMakeLists.txt").read_text().splitlines()


# ----------------------------------------------------------------------
# A library that supplies configuration the build overrides
# ----------------------------------------------------------------------


def test_override_records_say_who_supplies_and_who_overrides(tmp_path):
    library, project = tmp_path / "lib", tmp_path / "project"
    (library / "cfg").mkdir(parents=True)
    (project / "config").mkdir(parents=True)
    (library / "cfg" / "CMakeLists.txt").write_text(
        'register_fprime_config(\n    HEADERS\n        "${CMAKE_CURRENT_LIST_DIR}/PlatCfg.hpp"\n'
        '    SOURCES\n        "${CMAKE_CURRENT_LIST_DIR}/Tables.cpp"\n    INTERFACE\n)\n')
    (project / "config" / "CMakeLists.txt").write_text(
        'register_fprime_config(\n    CONFIGURATION_OVERRIDES\n        "${CMAKE_CURRENT_LIST_DIR}/PlatCfg.hpp"\n'
        '        "${CMAKE_CURRENT_LIST_DIR}/Tables.cpp"\n)\n')
    for directory in (library / "cfg", project / "config"):
        (directory / "PlatCfg.hpp").write_text("")
        (directory / "Tables.cpp").write_text("")
    headers, _ = fpp.find_config_overrides([library, project], (".hpp",))
    (header,) = headers
    assert header.suppliers == [(library / "cfg" / "CMakeLists.txt", library / "cfg" / "PlatCfg.hpp")]
    assert header.overriders == [(project / "config" / "CMakeLists.txt", project / "config" / "PlatCfg.hpp")]
    # Whatever the kind of file: a source file under SOURCES is configuration too
    everything, warnings = fpp.find_config_overrides([library, project], ("",))
    assert [o.name for o in everything] == ["PlatCfg.hpp", "Tables.cpp"] and not warnings


@needs_fpp
def test_library_that_only_supplies_configuration_is_kept(default):
    """Nothing of fprime-vxworks is in the patched model, but the project's configuration
    module overrides the VxWorksCfg.hpp it supplies"""
    report = default.report
    assert not report["libraries"]["dropped"]
    (reason,) = report["libraries"]["kept_for_configuration"]
    assert reason["library"].endswith("lib/fprime-vxworks")
    assert reason["supplies"] == "VxWorksCfg.hpp" and reason["overridden_by"] == "config/project"
    assert reason["file"].endswith("VxWorks/library/config/VxWorksCfg.hpp")
    text = (default.output / "wsts-report.md").read_text()
    assert "- Library built only for its configuration: `" in text
    assert "(supplies `VxWorksCfg.hpp`, overridden by `config/project`)" in text
    assert "Libraries not built for simulation" not in text
    settings = (default.output / "settings.ini").read_text()
    locations = next(line for line in settings.splitlines() if line.startswith("library_locations:"))
    assert "fprime-vxworks" in locations
    # Its library.cmake registers the configuration and then stops on a host: no warning
    assert not [w for w in report["warnings"] if "fprime-vxworks" in w]


@needs_fpp
def test_library_is_dropped_when_nothing_overrides_its_configuration(tmp_path):
    project = copy_flight(tmp_path)
    replace_in(project / "config" / "project" / "CMakeLists.txt", '        "${CMAKE_CURRENT_LIST_DIR}/VxWorksCfg.hpp"\n', "")
    result = generate(project, cdh(), tmp_path / "out")
    assert any("fprime-vxworks" in library for library in result.report["libraries"]["dropped"])
    assert "kept_for_configuration" not in result.report["libraries"]
    assert "fprime-vxworks" not in (result.output / "settings.ini").read_text()


@needs_fpp
def test_dropping_a_library_whose_configuration_is_overridden_is_an_error(tmp_path):
    with pytest.raises(WstsError) as error:
        generate(MISSION, cdh(libraries={"drop": ["../../lib/fprime-vxworks"]}), tmp_path / "out")
    message = str(error.value)
    assert "libraries.drop names" in message and "fprime-vxworks" in message
    assert "VxWorksCfg.hpp" in message and "config/project overrides" in message.replace(str(MISSION) + "/", "")
    assert "Remove it from libraries.drop" in message, "says what to do"
    assert "exclude_subdirectories: [config/project]" in message, "and the alternative"


@needs_fpp
def test_configuration_library_without_a_platform_guard_is_reported(tmp_path):
    """A library kept for its configuration is read whole. One whose library.cmake does not
    stop on the host adds its other modules there too"""
    project = copy_flight(tmp_path)
    library = tmp_path / "board-lib"
    (library / "cfg").mkdir(parents=True)
    (library / "Bsp").mkdir()
    (library / "library.cmake").write_text(
        'add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/cfg")\n'
        'add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/Bsp")\n')
    (library / "cfg" / "CMakeLists.txt").write_text(
        'register_fprime_config(\n    HEADERS\n        "${CMAKE_CURRENT_LIST_DIR}/BoardCfg.hpp"\n    INTERFACE\n)\n')
    (library / "cfg" / "BoardCfg.hpp").write_text("#define BOARD_UARTS 2\n")
    (library / "Bsp" / "CMakeLists.txt").write_text('register_fprime_module(\n    SOURCES\n        "${CMAKE_CURRENT_LIST_DIR}/Bsp.cpp"\n)\n')
    (library / "Bsp" / "Bsp.cpp").write_text("#include <bspLib.h>\n")
    replace_in(project / "settings.ini", ":lib/sensor-lib", f":lib/sensor-lib:{library}")
    config = project / "config" / "project"
    (config / "BoardCfg.hpp").write_text("#define BOARD_UARTS 4\n")
    replace_in(config / "CMakeLists.txt", '        "${CMAKE_CURRENT_LIST_DIR}/VxWorksCfg.hpp"\n',
               '        "${CMAKE_CURRENT_LIST_DIR}/VxWorksCfg.hpp"\n        "${CMAKE_CURRENT_LIST_DIR}/BoardCfg.hpp"\n')

    result = generate(project, cdh(), tmp_path / "out")
    supplied = {entry["supplies"]: entry["library"] for entry in result.report["libraries"]["kept_for_configuration"]}
    assert sorted(supplied) == ["BoardCfg.hpp", "VxWorksCfg.hpp"] and supplied["BoardCfg.hpp"].endswith("board-lib")
    (warning,) = [w for w in result.report["warnings"] if "board-lib" in w]
    assert "is built only for the configuration it supplies" in warning and "also adds modules" in warning
    assert ": Bsp. " in warning, "the module that has no restriction, not the configuration module"
    assert "exclude_subdirectories: [" in warning and "board-lib/Bsp]" in warning

    # Following that advice: the overlay's add_fprime_subdirectory() leaves the module out
    excluded = Path("..") / library.name / "Bsp"
    result = generate(project, cdh(cmake={"exclude_subdirectories": [str(excluded)]}), tmp_path / "out2")
    assert not [w for w in result.report["warnings"] if "board-lib" in w]
    root = (result.output / "CMakeLists.txt").read_text()
    assert "set(WSTS_EXCLUDED_MODULES\n" in root and "board-lib/Bsp\"\n)" in root
    assert "foreach (WSTS_MODULE IN LISTS WSTS_EXCLUDED_MODULES)" in root
    assert result.report["excluded_modules"][0].endswith("board-lib/Bsp")


# ----------------------------------------------------------------------
# Headers of replaced drivers
# ----------------------------------------------------------------------


@needs_fpp
def test_driver_header_is_generated_at_the_flight_include_path(default):
    """SensorLink's SubtopologyTopologyDefs.hpp includes the UART driver's header and has a
    field of its baud rate type; Main and the topology include that through the TopologyDefs"""
    defs = (MISSION / "lib" / "sensor-lib" / "SensorLib" / "SensorLink" / "SubtopologyTopologyDefs.hpp").read_text()
    assert f"#include <{UART_HEADER}>" in defs and "VxWorksDrv::VxWorksUartDriver::UartBaudRate baud" in defs

    header = (default.output / "wsts-include" / UART_HEADER).read_text()
    assert "#include <Wsts/Drv/SimByteStream/SimByteStream.hpp>" in header
    assert "namespace VxWorksDrv { using VxWorksUartDriver = ::Wsts::SimByteStream; }" in header
    # The same text as in WstsCompat.hpp, which still declares the alias: one source for both
    compat = (default.output / "Cdh" / "Top" / "WstsCompat.hpp").read_text()
    assert "namespace VxWorksDrv { using VxWorksUartDriver = ::Wsts::SimByteStream; }" in compat
    # The flight driver's own module, where a build still has it, gets its real header
    assert "#ifdef WSTS_FLIGHT_DRIVER_BUILD\n" in header and f"#include_next <{UART_HEADER}>" in header
    # The autocoded header, which the build no longer generates, leads to the same place
    autocoded = (default.output / "wsts-include" / UART_HEADER.replace(".hpp", "ComponentAc.hpp")).read_text()
    assert f"#include <{UART_HEADER}>" in autocoded

    # First on the include path of every module, Main's included: set for the whole project,
    # before F Prime adds a module
    lines = root_lines(default)
    include = lines.index('include_directories(BEFORE "${CMAKE_CURRENT_LIST_DIR}/wsts-include")')
    assert include < next(i for i, line in enumerate(lines) if line.startswith("find_package(FPrime"))
    marked = next(line for line in lines if line.startswith("foreach (WSTS_MODULE IN ITEMS"))
    assert "VxWorks_Drv_VxWorksUartDriver" in marked.split() and "VxWorks_Svc_VxWatchDogTimer" in marked

    entry = next(e for e in default.report["driver_headers"]["components"] if e["component"] == "VxWorksDrv.VxWorksUartDriver")
    assert entry["surrogate"] == "Wsts.SimByteStream" and entry["headers"][0] == UART_HEADER
    assert "## Headers of replaced drivers" in (default.output / "wsts-report.md").read_text()


@needs_fpp
def test_driver_header_names_follow_the_component(tmp_path):
    """Headers the flight code may name: <Name>.hpp, and the older <Name>ComponentImpl.hpp
    where that is what includes the autocoded header"""
    result = generate(MISSION, payload(), tmp_path / "out")
    include = result.output / "wsts-include"
    for name in ("LinuxSpiDriver.hpp", "LinuxSpiDriverComponentImpl.hpp", "LinuxSpiDriverComponentAc.hpp"):
        assert (include / "Drv" / "LinuxSpiDriver" / name).is_file(), name
    assert sorted(p.name for p in (include / "Drv" / "LinuxI2cDriver").iterdir()) == [
        "LinuxI2cDriver.hpp", "LinuxI2cDriverComponentAc.hpp"]
    spi = (include / "Drv" / "LinuxSpiDriver" / "LinuxSpiDriver.hpp").read_text()
    # Declarations that lived in the driver's header outside its class (the catalog's
    # `compat`): once per translation unit, whichever of the two headers comes first
    assert "#if !defined(WSTS_GENERATED_COMPAT_HPP) && !defined(WSTS_COMPAT_WSTS_SIMSPI)" in spi
    assert "enum SpiFrequency {" in spi
    assert "enum SpiFrequency {" in (result.output / "Payload" / "Top" / "WstsCompat.hpp").read_text()


@needs_fpp
def test_no_driver_header_while_the_flight_module_is_still_built(tmp_path):
    """Both pins are Drv.LinuxGpioDriver. With one kept, the component is still used: a
    stand-in would shadow the real header that instance needs"""
    result = generate(MISSION, payload(keep=["switchPin"]), tmp_path / "out")
    include = result.output / "wsts-include"
    assert not (include / "Drv" / "LinuxGpioDriver").exists()
    assert (include / "Drv" / "LinuxI2cDriver" / "LinuxI2cDriver.hpp").is_file()
    components = [entry["component"] for entry in result.report["driver_headers"]["components"]]
    assert "Drv.LinuxGpioDriver" not in components and "Drv.LinuxI2cDriver" in components
    compat = (result.output / "Payload" / "Top" / "WstsCompat.hpp").read_text()
    assert "// Drv.LinuxGpioDriver: no alias, another instance still uses the flight component" in compat
    marked = next(line for line in root_lines(result) if line.startswith("foreach (WSTS_MODULE IN ITEMS"))
    assert "Drv_LinuxGpioDriver" not in marked


@needs_fpp
def test_no_include_directory_without_substitutions(tmp_path):
    project = REPO / "lib" / "fprime" / "TestDeploymentsProject"
    result = generate(project, flight_manifest("Ref", keep=["comDriver", "linuxTimer"]), tmp_path / "out")
    assert not result.report["substitutions"] and "driver_headers" not in result.report
    assert not (result.output / "wsts-include").exists()
    assert "include_directories" not in (result.output / "CMakeLists.txt").read_text()


def test_alias_text_has_one_source():
    class Stub:
        def __init__(self, **values):
            self.__dict__.update(values)

    def substitution(original, cpp_name):
        return Stub(original=Stub(name=original), catalog=Stub(cpp_name=cpp_name))

    assert alias_declaration(substitution("Drv.LinuxGpioDriver", "::Wsts::SimGpio")) == \
        "namespace Drv { using LinuxGpioDriver = ::Wsts::SimGpio; }"
    assert alias_declaration(substitution("A.B.Driver", "::Sim::Driver")) == "namespace A::B { using Driver = ::Sim::Driver; }"
    assert alias_declaration(substitution("Driver", "::Sim::Driver")) == "using Driver = ::Sim::Driver;"


# ----------------------------------------------------------------------
# A module that adds another module
# ----------------------------------------------------------------------


def module(directory: Path, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "CMakeLists.txt").write_text(body)
    return directory


def test_directories_a_module_adds(tmp_path):
    parent = module(tmp_path / "config", (
        '# add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/commented_out")\n'
        'add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/dep_config")\n'
        "add_fprime_subdirectory(relative)\n"
        'include("${CMAKE_CURRENT_LIST_DIR}/more.cmake")\n'
        'add_fprime_subdirectory("${PROJECT_EXTRAS}/unseen")\n'
        "register_fprime_config(CONFIGURATION_OVERRIDES X.hpp)\n"))
    module(parent / "commented_out", "")
    child = module(parent / "dep_config", 'add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/deeper/")\n')
    deeper = module(child / "deeper", "register_fprime_module()\n")
    relative = module(parent / "relative", "register_fprime_module()\n")
    helper = module(parent / "helper", "register_fprime_module()\n")
    (parent / "more.cmake").write_text('add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/helper")\n')
    found = flight_cmake.added_directories(parent)
    assert set(found) == {child, deeper, relative, helper}, "nested calls and a helper file; not a comment, not a variable"
    assert found[deeper] == child / "CMakeLists.txt" and found[helper] == parent / "more.cmake"


def test_nested_module_is_left_to_its_parent(tmp_path):
    parent = module(tmp_path / "config", 'add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/dep_config")\n')
    child = module(parent / "dep_config", "")
    other = module(tmp_path / "Components", "")
    # The order they are found in: the child first, as the closure of an FPP model has it
    order = flight_cmake.order_modules([child, parent, other], {parent}, {}, {}, tmp_path)
    assert order.roots == [other, parent], "the child is not added by itself; the overriding module stays last"
    assert order.nested == {child: (parent, parent / "CMakeLists.txt")}
    assert order.members[parent] == [parent, child]
    # Nothing nested: exactly the order there always was, and `last` after everything
    flat = flight_cmake.order_modules([parent, other, child], {parent}, {}, {}, tmp_path, last=[child])
    assert flat.roots == [other, parent] and flat.nested
    separate = module(tmp_path / "Extra", "")
    flat = flight_cmake.order_modules([parent, other, separate], {parent}, {}, {}, tmp_path, last=[separate])
    assert flat.roots == [other, parent, separate] and not flat.nested


def test_parent_moves_ahead_of_what_overrides_its_child(tmp_path):
    """The child supplies a file that another module overrides: the parent, which brings
    the child in, has to be added before that module"""
    parent = module(tmp_path / "config", 'add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/dep_config")\n')
    child = module(parent / "dep_config", "")
    other = module(tmp_path / "other", "")
    order = flight_cmake.order_modules(
        [other, parent], {other, parent}, {child: {"Ids.fpp"}}, {other: {"Ids.fpp"}, parent: {"AcConstants.fpp"}}, tmp_path)
    assert order.roots == [parent, other]


def test_child_that_cannot_be_ordered_is_an_error(tmp_path):
    parent = module(tmp_path / "config", 'add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/dep_config")\n')
    child = module(parent / "dep_config", "")
    other = module(tmp_path / "other", "")
    with pytest.raises(WstsError) as error:
        flight_cmake.order_modules(
            [other, parent], {other, parent},
            {child: {"Ids.fpp"}, other: {"Limits.hpp"}}, {other: {"Ids.fpp"}, parent: {"Limits.hpp"}}, tmp_path)
    message = " ".join(str(error.value).split())
    assert "config/dep_config supplies Ids.fpp, which other overrides, so it has to be registered before other." in message
    assert "config/dep_config is added by config (config/CMakeLists.txt)" in message
    assert "config has to come after other: it overrides Limits.hpp, which other supplies" in message
    assert "exclude_subdirectories: [other]" in message, "says what to do"


@needs_fpp
def test_module_added_by_another_appears_once(default):
    """`config/project` adds `config/project/dep_config`; both hold FPP the model uses"""
    assert 'add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/dep_config")' in \
        (MISSION / "config" / "project" / "CMakeLists.txt").read_text()
    root = (default.output / "CMakeLists.txt").read_text()
    assert root.count("dep_config") == 0, "its parent adds it"
    assert root.count('add_fprime_subdirectory("${WSTS_FLIGHT_PROJECT}/config/project")') == 1
    assert default.report["project_modules"] == ["config/project"]
    assert default.report["nested_modules"] == [
        {"module": "config/project/dep_config", "added_by": "config/project", "in": "config/project/CMakeLists.txt"}]
    assert "`config/project/dep_config` is added by `config/project`" in (default.output / "wsts-report.md").read_text()
    # The constants it holds reached the analysis: the project's override takes its base ID from them
    uart = next(s for s in default.report["substitutions"] if s["instance"] == "SensorLink.uart")
    assert uart["base_id"] == 0x06001000
    # The backstop for what cannot be read from the text: the generated function adds a directory once
    assert "get_property(WSTS_ADDED GLOBAL PROPERTY WSTS_ADDED_DIRECTORIES)" in root


@needs_fpp
def test_child_module_that_cannot_be_ordered(tmp_path):
    """The child supplies configuration that a second module overrides, and the parent
    overrides configuration of that second module: no place for the parent"""
    project = copy_flight(tmp_path)
    config = project / "config" / "project"
    # The constants become configuration of the child, which another module then replaces
    replace_in(config / "dep_config" / "CMakeLists.txt", "register_fprime_module(", "register_fprime_config(")
    other = module(project / "config" / "vehicle", (
        'register_fprime_config(\n    HEADERS\n        "${CMAKE_CURRENT_LIST_DIR}/VehicleCfg.hpp"\n'
        '    CONFIGURATION_OVERRIDES\n        "${CMAKE_CURRENT_LIST_DIR}/MissionIds.fpp"\n    INTERFACE\n)\n'))
    shutil.copy(config / "dep_config" / "MissionIds.fpp", other / "MissionIds.fpp")
    (other / "VehicleCfg.hpp").write_text("#define VEHICLE_TANKS 2\n")
    (config / "VehicleCfg.hpp").write_text("#define VEHICLE_TANKS 4\n")
    replace_in(config / "CMakeLists.txt", '        "${CMAKE_CURRENT_LIST_DIR}/VxWorksCfg.hpp"\n',
               '        "${CMAKE_CURRENT_LIST_DIR}/VxWorksCfg.hpp"\n        "${CMAKE_CURRENT_LIST_DIR}/VehicleCfg.hpp"\n')
    with pytest.raises(WstsError) as error:
        generate(project, cdh(), tmp_path / "out")
    message = " ".join(str(error.value).split())
    assert "config/project/dep_config supplies MissionIds.fpp, which config/vehicle overrides" in message
    assert "config/project has to come after config/vehicle: it overrides VehicleCfg.hpp" in message
    assert "exclude_subdirectories: [config/vehicle]" in message


# ----------------------------------------------------------------------
# The flight project's root CMakeLists.txt
# ----------------------------------------------------------------------

ROOT = """\
cmake_minimum_required(VERSION 3.26)
project(Flight VERSION 1.0.0 LANGUAGES C CXX)   # set(IGNORED in_a_comment)

set(SDK_BOARD rev-b)
set(SDK_FEATURES uart spi)
set(SDK_CACHED "two words" CACHE STRING "kept for later")
option(SDK_TRACE "Trace the SDK" ON)
option(SDK_QUIET "Say little")
set(MANIFEST_HAS_IT flight)
set(SDK_ROOT "$ENV{SDK_HOME}/sdk")
set(CMAKE_CXX_STANDARD 17)
set(FPRIME_INCLUDE_FRAMEWORK_CODE OFF)
if (SDK_BOARD STREQUAL "rev-b")
    set(SDK_REVISION 2)
endif()
add_compile_definitions(VEHICLE=2 HAS_RADIO)
add_compile_definitions(TRACE_LEVEL=1)
add_compile_options(-mcpu=e6500 -Werror)
include_directories("${SDK_ROOT}/include")
include("${CMAKE_CURRENT_LIST_DIR}/cmake/board.cmake")
function(board_helper name)
    set(INSIDE_A_FUNCTION 1)
endfunction()

find_package(FPrime REQUIRED PATHS "${CMAKE_CURRENT_LIST_DIR}/lib/fprime/cmake")
fprime_setup_included_code()

add_compile_definitions(PROJECT_ONLY=1)
add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/Components")
set_target_properties(Lib_Unused PROPERTIES EXCLUDE_FROM_ALL TRUE)
target_link_libraries(Components_Radio PUBLIC "${SDK_ROOT}/lib/libradio.a")
add_library(Radio ALIAS Components_Radio)
set(LATE_VALUE 1)
"""


@pytest.fixture
def flight_root(tmp_path):
    project = tmp_path / "flight"
    (project / "cmake").mkdir(parents=True)
    (project / "CMakeLists.txt").write_text(ROOT)
    (project / "cmake" / "board.cmake").write_text("set(BOARD_FROM_FILE 1)\n")
    sim = tmp_path / "sim"
    sim.mkdir()
    return flight_cmake.read_root(project, project / "lib" / "fprime", ["MANIFEST_HAS_IT"], ["TRACE_LEVEL=3"],
                                  sim, "../flight/CMakeLists.txt")


def test_root_statements_that_are_carried_over(flight_root):
    # Plain values set before F Prime is read become cache entries; a list stays a list
    assert flight_root.cache == {
        "SDK_BOARD": ["rev-b"], "SDK_FEATURES": ["uart", "spi"], "SDK_CACHED": ["two words"],
        "SDK_TRACE": ["ON"], "SDK_QUIET": ["OFF"]}
    # Definitions are copied where flight makes them: before F Prime is read they reach every
    # module, after it only the modules added from then on
    assert flight_root.defines_early == ["VEHICLE=2", "HAS_RADIO"]
    assert flight_root.defines_late == ["PROJECT_ONLY=1"]
    carried = {entry["statement"]: entry["as"] for entry in flight_root.carried}
    assert carried["set(SDK_BOARD rev-b)"] == "cache entry SDK_BOARD=rev-b"
    assert carried["set(SDK_FEATURES uart spi)"] == "cache entry SDK_FEATURES=uart;spi"
    assert "as in flight" in carried["add_compile_definitions(PROJECT_ONLY=1)"]
    # What the manifest sets is the manifest's
    assert carried["set(MANIFEST_HAS_IT flight)"] == "not needed: cmake.cache in the manifest sets MANIFEST_HAS_IT"
    assert carried["add_compile_definitions(TRACE_LEVEL=1)"].endswith("the manifest's 'defines' set TRACE_LEVEL")
    assert "TRACE_LEVEL=1" not in flight_root.defines_early
    assert [entry["line"] for entry in flight_root.carried] == sorted(entry["line"] for entry in flight_root.carried)


def test_root_statements_that_are_reported(flight_root):
    left = {entry["statement"]: entry for entry in flight_root.not_carried}
    # F Prime's own lines, and what the overlay sets itself, are neither carried nor reported
    assert not [s for s in left if s.startswith(("project(", "find_package(", "fprime_setup", "add_fprime_subdirectory("))]
    assert not [s for s in left if "FPRIME_INCLUDE_FRAMEWORK_CODE" in s or "IGNORED" in s or "INSIDE_A_FUNCTION" in s.split("...")[0]]
    assert {s: e["key"] for s, e in left.items()} == {
        'set(SDK_ROOT "$ENV{SDK_HOME}/sdk")': "cmake.cache",
        "set(CMAKE_CXX_STANDARD 17)": "cmake.cache",
        "set(SDK_REVISION 2)": "cmake.cache",
        "add_compile_options(-mcpu=e6500 -Werror)": "cmake.include_early",
        'include_directories("${SDK_ROOT}/include")': "cmake.include_early",
        'include("${CMAKE_CURRENT_LIST_DIR}/cmake/board.cmake")': "cmake.include_early",
        "function(board_helper name) ... endfunction()": "cmake.include_early",
        "set_target_properties(Lib_Unused PROPERTIES EXCLUDE_FROM_ALL TRUE)": "cmake.include",
        'target_link_libraries(Components_Radio PUBLIC "${SDK_ROOT}/lib/libradio.a")': "cmake.include",
        "add_library(Radio ALIAS Components_Radio)": "cmake.include",
        "set(LATE_VALUE 1)": "cmake.include",
    }
    assert "not a plain one" in left['set(SDK_ROOT "$ENV{SDK_HOME}/sdk")']["why"]
    assert "toolchain" in left["set(CMAKE_CXX_STANDARD 17)"]["why"]
    assert left["set(SDK_REVISION 2)"]["why"] == 'it is inside if (SDK_BOARD STREQUAL "rev-b"), which is not evaluated'

    # One warning per manifest key, each with the statements, their lines and YAML to paste
    cache, early, late = flight_root.warnings
    assert cache.startswith("../flight/CMakeLists.txt: 3 statement(s)") and "    line 10: set(SDK_ROOT" in cache
    assert "    cmake:\n      cache:\n        SDK_ROOT: <value for the simulated build>\n        CMAKE_CXX_STANDARD: 17\n        SDK_REVISION: 2" in cache
    assert "    line 18: add_compile_options(-mcpu=e6500 -Werror)" in early
    assert "    cmake:\n      include_early: [flight-early.cmake]" in early
    # A file the flight project includes can be named as it is, relative to the simulation project
    assert "      include_early: [../flight/cmake/board.cmake]" in early
    assert "    line 30: set_target_properties(Lib_Unused PROPERTIES EXCLUDE_FROM_ALL TRUE)" in late
    assert "    cmake:\n      include: [flight-late.cmake]" in late and "if (TARGET ...)" in late


def test_root_without_anything_else_is_silent(tmp_path):
    """The root CMakeLists.txt of a plain F Prime project, and the framework added by hand"""
    (tmp_path / "CMakeLists.txt").write_text(
        "cmake_policy(SET CMP0048 NEW)\ncmake_minimum_required(VERSION 3.26)\nproject(Ref VERSION 1.0.0 LANGUAGES C CXX)\n"
        'set(FPRIME_INCLUDE_FRAMEWORK_CODE OFF)\nfind_package(FPrime REQUIRED PATHS "${CMAKE_CURRENT_LIST_DIR}/..")\n'
        'add_subdirectory("${CMAKE_CURRENT_LIST_DIR}/fprime" "F-Prime")\n'
        'add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/Ref/")\n')
    root = flight_cmake.read_root(tmp_path, tmp_path / "fprime", [], [], tmp_path)
    assert not root and not root.warnings and not root.cache
    assert not flight_cmake.read_root(tmp_path / "nowhere", tmp_path, [], [], tmp_path)


@needs_fpp
def test_flight_root_is_carried_into_the_generated_project(default):
    """The example sets SENSOR_LIB_BOARD, which sensor-lib's library.cmake stops without, and
    defines MISSION_VEHICLE_ID, which Entry.cpp and Main.cpp refuse to compile without"""
    assert "message(FATAL_ERROR" in (MISSION / "lib" / "sensor-lib" / "library.cmake").read_text()
    assert "#ifndef MISSION_VEHICLE_ID\n#error" in (MISSION / "Cdh" / "Entry.cpp").read_text()
    lines = root_lines(default)
    found = next(i for i, line in enumerate(lines) if line.startswith("find_package(FPrime"))
    cache = lines.index('set(SENSOR_LIB_BOARD "cdh-rev-b" CACHE STRING "Carried over by fprime-wsts from the '
                        "flight project's CMakeLists.txt\")")
    assert cache < found, "before F Prime reads the libraries"
    assert lines.index("add_compile_definitions(MISSION_VEHICLE_ID=2)") < found
    assert lines[lines.index("add_compile_definitions(MISSION_VEHICLE_ID=2)") - 2] == "add_compile_definitions(WSTS_SIM=1)"
    flight = default.report["flight_cmake"]
    assert flight["file"].endswith("mission/CMakeLists.txt") and not flight["not_carried_over"]
    assert [(entry["statement"], entry["as"]) for entry in flight["carried_over"]] == [
        ("set(SENSOR_LIB_BOARD cdh-rev-b)", "cache entry SENSOR_LIB_BOARD=cdh-rev-b"),
        ("add_compile_definitions(MISSION_VEHICLE_ID=2)", "compile definition MISSION_VEHICLE_ID=2")]
    text = (default.output / "wsts-report.md").read_text()
    assert "## Flight root CMakeLists.txt" in text and "- Carried over, line " in text
    assert not default.report["warnings"]


@needs_fpp
def test_manifest_wins_over_the_flight_root(tmp_path):
    result = generate(MISSION, cdh(cmake={"cache": {"SENSOR_LIB_BOARD": "bench"}}, defines=["MISSION_VEHICLE_ID=9"]),
                      tmp_path / "out")
    root = (result.output / "CMakeLists.txt").read_text()
    assert 'set(SENSOR_LIB_BOARD "bench" CACHE STRING "Set by fprime-wsts from cmake.cache in the manifest")' in root
    assert root.count("set(SENSOR_LIB_BOARD") == 1 and "Carried over" not in root
    assert "add_compile_definitions(WSTS_SIM=1 MISSION_VEHICLE_ID=9)" in root and "MISSION_VEHICLE_ID=2" not in root
    carried = {entry["statement"]: entry["as"] for entry in result.report["flight_cmake"]["carried_over"]}
    assert carried["set(SENSOR_LIB_BOARD cdh-rev-b)"].startswith("not needed: cmake.cache in the manifest")
    assert carried["add_compile_definitions(MISSION_VEHICLE_ID=2)"] == "the manifest's 'defines' set MISSION_VEHICLE_ID"


@needs_fpp
def test_flight_root_statements_that_cannot_be_carried_over(tmp_path):
    project = copy_flight(tmp_path)
    replace_in(project / "CMakeLists.txt", "add_compile_definitions(MISSION_VEHICLE_ID=2)\n", (
        "add_compile_definitions(MISSION_VEHICLE_ID=2)\n"
        "add_compile_options(-mlongcall)\n"
        'set(SDK_ROOT "$ENV{WIND_BASE}/sdk")\n'))
    (project / "CMakeLists.txt").write_text(
        (project / "CMakeLists.txt").read_text()
        + "set_target_properties(SensorLib_SerialSensor PROPERTIES EXCLUDE_FROM_ALL TRUE)\n")
    result = generate(project, cdh(), tmp_path / "out")
    root = (result.output / "CMakeLists.txt").read_text()
    assert "mlongcall" not in root and "SDK_ROOT" not in root and "set_target_properties" not in root
    assert "add_compile_definitions(MISSION_VEHICLE_ID=2)" in root, "what can be carried over still is"
    cache, early, late = [w for w in result.report["warnings"] if "root CMakeLists.txt" in w]
    assert "set(SDK_ROOT" in cache and "cmake:\n      cache:\n        SDK_ROOT: <value for the simulated build>" in cache
    assert "add_compile_options(-mlongcall)" in early and "include_early: [flight-early.cmake]" in early
    assert "set_target_properties(SensorLib_SerialSensor PROPERTIES EXCLUDE_FROM_ALL TRUE)" in late
    assert "include: [flight-late.cmake]" in late
    keys = [entry["key"] for entry in result.report["flight_cmake"]["not_carried_over"]]
    assert keys == ["cmake.include_early", "cmake.cache", "cmake.include"]
    text = (result.output / "wsts-report.md").read_text()
    assert "- Not carried over, line " in text and "`cmake.include_early` in the manifest reproduces it" in text


# ----------------------------------------------------------------------
# Modules that link files of the target's SDK
# ----------------------------------------------------------------------


def test_dependencies_that_are_sdk_files(tmp_path):
    directory = module(tmp_path / "Camera", (
        "set(LOCAL_LIBS m)\n"
        "register_fprime_module(\n    SOURCES\n        \"${CMAKE_CURRENT_LIST_DIR}/Camera.cpp\"\n    DEPENDS\n"
        "        Fw_Logger\n        ${FPRIME_CURRENT_MODULE}_Types\n        ${LOCAL_LIBS}\n        -lrt\n"
        "        ${SDK_ROOT}/3rdparty/lib/libcam.a\n        ${SDK_LIBS}\n        /opt/vendor/lib/libdsp.so.2\n"
        "        # ${COMMENTED}/x.a\n)\n"))
    found = {d.entry: d for d in flight_cmake.sdk_dependencies(directory, {})}
    assert sorted(found) == ["${SDK_LIBS}", "${SDK_ROOT}/3rdparty/lib/libcam.a", "/opt/vendor/lib/libdsp.so.2"]
    assert found["${SDK_ROOT}/3rdparty/lib/libcam.a"].variables == ["SDK_ROOT"]
    assert "SDK_ROOT is not set in the simulated build" in found["${SDK_ROOT}/3rdparty/lib/libcam.a"].problem
    assert "does not exist on this host" in found["/opt/vendor/lib/libdsp.so.2"].problem
    # A variable the simulated build sets: the entry is checked where it then points
    host = tmp_path / "host-sdk" / "3rdparty" / "lib"
    host.mkdir(parents=True)
    (host / "libcam.a").write_text("")
    found = [d.entry for d in flight_cmake.sdk_dependencies(directory, {"SDK_ROOT": [str(tmp_path / "host-sdk")], "SDK_LIBS": ["x"]})]
    assert found == ["/opt/vendor/lib/libdsp.so.2"]
    # The older way of declaring dependencies
    old = module(tmp_path / "Old", "set(MOD_DEPS Fw/Logger ${BSP_DIR}/libbsp.a)\nregister_fprime_module()\n")
    assert [d.entry for d in flight_cmake.sdk_dependencies(old, {})] == ["${BSP_DIR}/libbsp.a"]


@needs_fpp
def test_module_that_links_an_sdk_file_is_reported_before_the_build(tmp_path):
    project = copy_flight(tmp_path)
    cmake = project / "Components" / "Adc" / "CMakeLists.txt"
    text = cmake.read_text()
    assert "DEPENDS" not in text
    cmake.write_text(text.rstrip().rstrip(")") + "    DEPENDS\n        ${SDK_ROOT}/3rdparty/lib/libadc.a\n)\n")
    result = generate(project, payload(), tmp_path / "out")
    (warning,) = [w for w in result.report["warnings"] if "libadc.a" in w]
    assert warning.startswith("module Components/Adc links '${SDK_ROOT}/3rdparty/lib/libadc.a': SDK_ROOT is not set")
    assert "The topology needs the module for Payload.adc (Components.Adc)" in warning
    for option in ("substitute that instance", "exclude_subdirectories: [Components/Adc]", "cache: {SDK_ROOT: <path>}"):
        assert option in warning, option
    assert "libraries: {drop" not in warning, "a project module, not a library's"

    # Pointing the variable at a host build settles it
    sdk = tmp_path / "host-sdk"
    (sdk / "3rdparty" / "lib").mkdir(parents=True)
    (sdk / "3rdparty" / "lib" / "libadc.a").write_text("")
    result = generate(project, payload(cmake={"cache": {"SDK_ROOT": str(sdk)}}), tmp_path / "out2")
    assert not [w for w in result.report["warnings"] if "libadc.a" in w]


# ----------------------------------------------------------------------
# An assert at start-up
# ----------------------------------------------------------------------

LOG_BY_ID = """\
[WSTS] bridge listening on tcp:50100
EVENT: (1280) (2:1700000000,0) DIAGNOSTIC: (cmdDisp) OpCodeRegistered : Opcode 0x500 registered
Assert: 0x0c0ffee1:17 2147483648 4096
Assert: 0xdeadbeef:118 4096 2048
"""

TOPOLOGY_HEADER = """\
#include "Cdh/Top/CdhTopologyDefs.hpp"
#include "Drv/DmaWindow/DmaWindow.hpp"
#include "Drv/TcpClient/TcpClient.hpp"
#include "Wsts/Drv/SimByteStream/SimByteStream.hpp"

namespace Cdh {

  //! comDriver
  extern Drv::TcpClient comDriver;

}

namespace Cdh {

  //! hubDriver
  extern Wsts::SimByteStream hubDriver;

}

namespace Fpga {

  //! window
  extern Drv::DmaWindow window;

}
"""


@pytest.fixture
def built(tmp_path):
    """What a build leaves in a generated directory, as far as the runner reads it"""
    cache = tmp_path / "build-fprime-automatic-native"
    (cache / "Cdh" / "Top").mkdir(parents=True)
    (cache / "Cdh" / "Top" / "CdhTopologyAc.hpp").write_text(TOPOLOGY_HEADER)
    (cache / "hashes.txt").write_text(
        "Fw/Types/Assert.cpp: 0x00c0ffee\n"
        "Drv/DmaWindow/DmaWindow.cpp: 0xdeadbeef\n"
        "Drv/DmaWindow/DmaWindow.cpp: 0xdeadbeef\n"  # appended again each time CMake runs
        "Wsts/Drv/SimByteStream/SimByteStream.cpp: 0x0c0ffee1\n")
    report = {"bridge": "tcp:50100", "substitutions": [{"instance": "Cdh.hubDriver"}]}
    (tmp_path / "wsts-report.json").write_text(json.dumps(report))
    return tmp_path, report


def test_assert_is_found_in_the_log():
    assert find_assert(LOG_BY_ID) == {"id": "0xdeadbeef", "line": 118}, "the last one"
    assert find_assert('Assert: "Drv/DmaWindow/DmaWindow.cpp:118" 4096\n') == {"file": "Drv/DmaWindow/DmaWindow.cpp", "line": 118}
    assert find_assert("Assert: 0xbeef:7\n") == {"id": "0x0000beef", "line": 7}
    assert find_assert("EVENT: (1) nothing asserted\n") is None


def test_assert_file_id_is_resolved_with_the_builds_own_table(built):
    directory, report = built
    assert read_file_ids(directory)["0xdeadbeef"] == ["Drv/DmaWindow/DmaWindow.cpp"]
    assert {i["instance"]: i["header"] for i in topology_instances(directory)} == {
        "Cdh.comDriver": "Drv/TcpClient/TcpClient.hpp",
        "Cdh.hubDriver": "Wsts/Drv/SimByteStream/SimByteStream.hpp",
        "Fpga.window": "Drv/DmaWindow/DmaWindow.hpp"}
    found = explain_assert(directory, LOG_BY_ID, report)
    assert (found["file"], found["line"], found["id"]) == ("Drv/DmaWindow/DmaWindow.cpp", 118, "0xdeadbeef")
    # The file is the component of an instance the simulation kept: name it, and say what to do
    assert found["instances"] == [{"instance": "Fpga.window", "type": "Drv::DmaWindow", "kept": True}]
    assert found["message"].startswith("F Prime assert at Drv/DmaWindow/DmaWindow.cpp:118. That file belongs to "
                                       "Drv::DmaWindow, the component of Fpga.window, which the simulation keeps")
    assert "substitute:\n    - instance: Fpga.window\n      with: <surrogate component>" in found["message"]


def test_assert_in_a_surrogate_or_an_unknown_file(built):
    directory, report = built
    # In the surrogate of a substituted instance: named, without the advice to substitute
    found = explain_assert(directory, "Assert: 0x0c0ffee1:17\n", report)
    assert found["instances"] == [{"instance": "Cdh.hubDriver", "type": "Wsts::SimByteStream", "kept": False}]
    assert "substitute:" not in found["message"] and "the component of Cdh.hubDriver" in found["message"]
    # In no component's directory
    found = explain_assert(directory, "Assert: 0x00c0ffee:40\n", report)
    assert found["file"] == "Fw/Types/Assert.cpp" and found["instances"] == []
    assert found["message"] == "F Prime assert at Fw/Types/Assert.cpp:40"
    # An ID the build does not know
    found = explain_assert(directory, "Assert: 0x12345678:9\n", report)
    assert found["file"] is None and found["id"] == "0x12345678"
    assert "file ID 0x12345678, line 9" in found["message"] and "hashes.txt" in found["message"]
    # A path in the log needs no table: an absolute one still finds its instance
    found = explain_assert(directory, 'Assert: "/work/fsw/Drv/DmaWindow/DmaWindow.cpp:118"\n', report)
    assert found["file"] == "/work/fsw/Drv/DmaWindow/DmaWindow.cpp" and "id" not in found
    assert [i["instance"] for i in found["instances"]] == ["Fpga.window"]
    assert explain_assert(directory, "nothing\n", report) is None


def test_run_names_the_assert_when_the_deployment_stops_at_start_up(built, capsys):
    """`fprime-wsts run --json` on a deployment that asserts before it opens its bridge"""
    from fprime_wsts.cli import main

    directory, _ = built
    binary = directory / "build-artifacts" / "Linux" / "Cdh" / "bin" / "Cdh"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\necho 'Assert: 0xdeadbeef:118 4096 2048'\nkill -ABRT $$\n")
    binary.chmod(0o755)
    code = main(["run", str(directory), "--duration", "5", "--json"])
    output = capsys.readouterr()
    assert code == 2
    summary = json.loads(output.out)
    assert summary["exited_early"] and summary["exit_code"] == -6
    assert summary["assert"] == {
        "file": "Drv/DmaWindow/DmaWindow.cpp", "line": 118, "id": "0xdeadbeef",
        "instances": [{"instance": "Fpga.window", "type": "Drv::DmaWindow", "kept": True}]}
    assert "it exited with code -6): F Prime assert at Drv/DmaWindow/DmaWindow.cpp:118" in output.err
    assert "instance: Fpga.window" in output.err
