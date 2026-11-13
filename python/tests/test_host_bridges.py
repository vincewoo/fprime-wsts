"""Bridging what the target's toolchain provides and a Linux host lacks: the stub variant
of one module (`cmake.stub_variants`), compiler flags for the host (`cmake.cxx_flags`),
warnings about BUILD_TESTING for the whole build, and the host check.

The example has the shapes (`examples/README.md`): `Components/Imager` links a file of an
SDK and has a stub under `if (BUILD_TESTING)`; `lib/sensor-lib/SensorLib/Units` exports an
include directory in its real variant only and uses memcpy without `<cstring>`.

The generator tests need the FPP tools; the tests of generated CMake need cmake; the rest
read text only.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from fprime_wsts import flight_cmake, hostcheck
from fprime_wsts.cli import main
from fprime_wsts.fpp import WstsError
from fprime_wsts.generate import generate
from fprime_wsts.manifest import ALL_MODULES, parse_manifest

from mission import MISSION, SIM, copy_flight, element_data, element_manifest, flight_manifest, needs_fpp

IMAGER = "Components/Imager"
UNITS = "lib/sensor-lib/SensorLib/Units"
SDK_FILE = "${SDK_ROOT}/3rdparty/lib/libcamsdk.a"

needs_cmake = pytest.mark.skipif(shutil.which("cmake") is None, reason="needs cmake on PATH")
needs_compiler = pytest.mark.skipif(not hostcheck.host_compiler()[0], reason="needs a C++ compiler on PATH")


def payload(**cmake):
    """The example's manifest for Payload with keys of its `cmake` section changed (None removes one)"""
    section = dict(element_data("payload")["cmake"])
    for key, value in cmake.items():
        if value is None:
            section.pop(key, None)
        else:
            section[key] = value
    return element_manifest("payload", cmake=section)


@pytest.fixture(scope="module")
def default(tmp_path_factory):
    """Payload as the example has it: the Imager's stub variant, flags for one module"""
    return generate(MISSION, payload(), tmp_path_factory.mktemp("bridges") / "out")


@pytest.fixture(scope="module")
def no_stub(tmp_path_factory):
    """Payload without `cmake.stub_variants`: the Imager would link the camera SDK"""
    return generate(MISSION, payload(stub_variants=None), tmp_path_factory.mktemp("bridges") / "out")


def root_of(result) -> str:
    return (result.output / "CMakeLists.txt").read_text()


def write_cmake(directory: Path, text: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "CMakeLists.txt").write_text(text)
    return directory


# ----------------------------------------------------------------------
# Manifest
# ----------------------------------------------------------------------


def test_manifest_keys():
    manifest = parse_manifest({
        "deployment": "D",
        "cmake": {"stub_variants": ["lib/x/Cam", " Components/Y "], "cxx_flags": ["-include cstring", "-fpermissive"]},
        "host": {"ignore": ["vendor/gen.h", "-lcodec", "Codec"]},
    })
    assert manifest.stub_variants == ["lib/x/Cam", "Components/Y"]
    assert manifest.cxx_flags == {ALL_MODULES: ["-include cstring", "-fpermissive"]}
    assert manifest.host_ignore == ["vendor/gen.h", "-lcodec", "Codec"]
    per_module = parse_manifest({"deployment": "D", "cmake": {"cxx_flags": {
        "lib/x/Algo/": ["-include  cstring"], "*": "-Wno-error"}}})
    assert per_module.cxx_flags == {"lib/x/Algo": ["-include cstring"], ALL_MODULES: ["-Wno-error"]}
    assert parse_manifest({"deployment": "D"}).stub_variants == []


@pytest.mark.parametrize("change, message", [
    ({"cmake": {"stub_variants": "Components/Y"}}, "cmake.stub_variants must be a list of module directories"),
    ({"cmake": {"cxx_flags": ["include cstring"]}}, r"'include cstring' is not a plain compiler flag \(it must start with '-'"),
    ({"cmake": {"cxx_flags": ['-DNAME="a b"']}}, "is not a plain compiler flag"),
    ({"cmake": {"cxx_flags": {"Components/Y": [3]}}}, r"cmake.cxx_flags.Components/Y must be a list of compiler flags \(one entry per flag"),
    ({"host": {"skip": ["x.h"]}}, "host: unknown key"),
    ({"host": {"ignore": "x.h"}}, "host.ignore must be a list"),
])
def test_manifest_errors(change, message):
    with pytest.raises(WstsError, match=message):
        parse_manifest({"deployment": "D", **change})


# ----------------------------------------------------------------------
# Reading BUILD_TESTING branches
# ----------------------------------------------------------------------

BRANCHES = '''
register_fprime_config(HEADERS "${CMAKE_CURRENT_LIST_DIR}/Cfg.hpp")
if (BUILD_TESTING)
    set(ALGO_DEP Algo_Stubs)
    add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/Stubs")
else()
    set(ALGO_DEP Algo::lib)
    if (ALGO_FAST)
        add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/Fast")
    endif()
endif()
if (NOT BUILD_TESTING)
    target_include_directories(Algo PUBLIC include)
elseif (ALGO_TRACE)
    add_compile_definitions(ALGO_TRACE)
endif()
if (BUILD_TESTING AND ALGO_UT_EXTRAS)
    register_fprime_ut(SOURCES test/ut/Main.cpp)
endif()
# if (BUILD_TESTING) in a comment is nothing
'''


def test_statements_under_build_testing(tmp_path):
    states = {c.text.split("(")[0].strip() + ":" + (c.args[0] if c.args else ""): flight_cmake.testing_state(c)
              for c in flight_cmake.read_commands(BRANCHES)}
    assert states["register_fprime_config:HEADERS"] is None
    assert states["set:ALGO_DEP"] is False, "the last one read: the else() branch"
    assert states["add_fprime_subdirectory:${CMAKE_CURRENT_LIST_DIR}/Stubs"] is True
    assert states["add_fprime_subdirectory:${CMAKE_CURRENT_LIST_DIR}/Fast"] is False, "an if() inside the else()"
    assert states["target_include_directories:Algo"] is False
    assert states["register_fprime_ut:SOURCES"] is True, "a condition that combines it with something else"
    module = write_cmake(tmp_path / "Algo", BRANCHES)
    branches = flight_cmake.testing_branches(module)
    assert branches and branches.mentioned
    assert [c.name for c in branches.when_on] == ["set", "add_fprime_subdirectory", "register_fprime_ut"]
    described = branches.describe()
    assert described["registers_unit_tests"]
    assert described["variant_with_build_testing_lacks"]["include_directories"] == ["target_include_directories(Algo PUBLIC include)"]
    plain = flight_cmake.testing_branches(write_cmake(tmp_path / "Plain", "# BUILD_TESTING\nregister_fprime_module()\n"))
    assert not plain and not plain.mentioned, "a comment is not a branch"


def test_sdk_dependencies_are_read_per_variant(tmp_path):
    module = MISSION / IMAGER
    assert [d.entry for d in flight_cmake.sdk_dependencies(module, {})] == [SDK_FILE]
    assert flight_cmake.sdk_dependencies(module, {}, testing=True) == [], "the stub variant links no SDK file"
    assert [d.entry for d in flight_cmake.sdk_dependencies(module, {}, testing=None)] == [SDK_FILE]
    described = flight_cmake.testing_branches(module).describe()
    assert described["variant_with_build_testing_lacks"]["depends"] == [SDK_FILE, "-lcamcodec"]
    assert described["variant_with_build_testing_lacks"]["functions"] == ["selfTest"], "defined by CameraSdk.cpp only"
    units = flight_cmake.testing_branches(MISSION / UNITS).describe()["variant_with_build_testing_lacks"]
    assert units["functions"] == ["reading16"] and "PUBLIC" in units["include_directories"][0]


def test_build_tree_follows_what_the_host_reads(tmp_path):
    library = tmp_path / "lib"
    write_cmake(library / "Config", "register_fprime_config()\n")
    write_cmake(library / "Rtos", "register_fprime_module()\n")
    write_cmake(library / "Cam", "if (BUILD_TESTING)\n  add_fprime_subdirectory(Stub)\nelse()\n  add_fprime_subdirectory(Sdk)\nendif()\n")
    write_cmake(library / "Cam" / "Stub", "register_fprime_module()\n")
    write_cmake(library / "Cam" / "Sdk", "register_fprime_module()\n")
    for name in ("A", "B"):
        write_cmake(library / "Sim" / name, "register_fprime_module()\n")
    (library / "library.cmake").write_text(
        'add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/Config")\n'
        'add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/Cam")\n'
        'foreach (MODULE IN LISTS SIM_MODULES)\n  add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/Sim/${MODULE}")\nendforeach()\n'
        "restrict_platforms(vxworks)\n"
        'add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/Rtos")\n')

    def names(tree):
        return sorted(d.relative_to(library).as_posix() for d in tree.directories)

    tree = flight_cmake.read_tree(library / "library.cmake", variables={"SIM_MODULES": ["A"]})
    assert names(tree) == ["Cam", "Cam/Sdk", "Config", "Sim/A"], "nothing after the restriction; the list from the build"
    assert not tree.unread
    stub = flight_cmake.read_tree(library / "library.cmake", [library / "Cam"], variables={"SIM_MODULES": []})
    assert names(stub) == ["Cam", "Cam/Stub", "Config"]
    unknown = flight_cmake.read_tree(library / "library.cmake")
    assert [command.args[0] for _, command in unknown.unread] == ["${CMAKE_CURRENT_LIST_DIR}/Sim/${MODULE}"]


# ----------------------------------------------------------------------
# Stub variants: plan and output
# ----------------------------------------------------------------------


@needs_fpp
def test_redirection_sets_build_testing_for_the_listed_module(default):
    root = root_of(default)
    listed = re.search(r"set\(WSTS_STUB_VARIANT_MODULES\n(.*?)\n\)", root, re.S).group(1).split()
    assert len(listed) == 1 and listed[0].endswith(f'/{IMAGER}"'), "the Imager and nothing else"
    function = root[root.index("function(add_fprime_subdirectory FP_SOURCE_DIR)"):root.index("endfunction()")]
    assert "set(BUILD_TESTING ON)" in function and "set(__FPRIME_NO_UT_GEN__ ON)" in function
    assert 'set(BUILD_TESTING "${WSTS_BUILD_TESTING_OUTSIDE}")' in function, "a module the listed one adds gets the build's value"
    assert 'set(FPRIME_TESTING_REQUIRED_COMPILE_FLAGS "")' in function, "no coverage flags for the module"
    # Not for the whole build: no cache entry, no option, nothing outside the function
    outside = root.replace(function, "")
    assert "BUILD_TESTING ON" not in outside and "BUILD_TESTING" not in (default.output / "settings.ini").read_text()
    assert not re.search(r"set\(BUILD_TESTING\b[^)]*CACHE", root)
    assert "ENABLE_SANITIZER" not in root and "BUILD_UT" not in root.replace("BUILD_UT is not defined", "")
    # A listed module that is never added stops the configure step
    assert root.index("WSTS_STUB_VARIANTS_READ)\nforeach") < root.index(f'add_fprime_subdirectory("${{CMAKE_CURRENT_LIST_DIR}}/Payload")')
    assert "cmake.stub_variants in the manifest lists ${WSTS_MODULE}, and nothing in this" in root
    assert default.report["stub_variants"] == [IMAGER]


SCOPE_PROJECT = '''
cmake_minimum_required(VERSION 3.16)
project(Scope NONE)
option(BUILD_TESTING "as F Prime declares it" OFF)
# Stand-ins for what the function uses of F Prime
function(add_fprime_subdirectory FP_SOURCE_DIR)
    get_filename_component(NAME "${FP_SOURCE_DIR}" NAME)
    add_subdirectory("${FP_SOURCE_DIR}" "${CMAKE_BINARY_DIR}/first/${NAME}")
endfunction()
function(fprime_setup_included_code)
endfunction()
set(__FPRIME_NO_UT_GEN__ OFF)
set(FPRIME_TESTING_REQUIRED_COMPILE_FLAGS "--coverage")
@REDIRECT@
foreach (MODULE IN ITEMS Listed Other)
    add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/${MODULE}")
endforeach()
message(STATUS "SEEN root testing=${BUILD_TESTING} no_ut=${__FPRIME_NO_UT_GEN__} flags=${FPRIME_TESTING_REQUIRED_COMPILE_FLAGS}")
@VERIFY@
'''
SEEN = 'message(STATUS "SEEN {0} testing=${{BUILD_TESTING}} no_ut=${{__FPRIME_NO_UT_GEN__}} flags=${{FPRIME_TESTING_REQUIRED_COMPILE_FLAGS}}")\n'


def scope_project(tmp_path: Path, root: str, listed: str = "Listed") -> Path:
    """A CMake project that runs the generated redirection over directories of its own"""
    start = root.index("set(WSTS_REPLACED_MODULES")
    redirect = root[start:root.index("fprime_setup_included_code()", start)]
    redirect = re.sub(r"(set\(WSTS_STUB_VARIANT_MODULES\n)[^)]*\)", rf'\1    "${{CMAKE_CURRENT_LIST_DIR}}/{listed}"\n)', redirect)
    redirect = re.sub(r"set\(WSTS_DEFERRED_MODULES\n[^)]*\)", "set(WSTS_DEFERRED_MODULES)", redirect)
    verify = root[root.index("# Every module listed under `cmake.stub_variants`"):]
    verify = verify[:verify.index("endforeach()") + len("endforeach()")]
    project = tmp_path / "scope"
    project.mkdir()
    (project / "CMakeLists.txt").write_text(SCOPE_PROJECT.replace("@REDIRECT@", redirect).replace("@VERIFY@", verify))
    write_cmake(project / "Listed", SEEN.format("listed") + 'add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/Child")\n'
                + SEEN.format("listed-after-child"))
    write_cmake(project / "Listed" / "Child", SEEN.format("child"))
    write_cmake(project / "Other", SEEN.format("other"))
    return project


def configure(project: Path, *options: str):
    run = subprocess.run(["cmake", "-S", str(project), "-B", str(project / "b"), *options], capture_output=True, text=True)
    seen = {name: {"testing": testing, "no_ut": no_ut, "flags": flags}
            for name, testing, no_ut, flags in re.findall(r"SEEN (\S+) testing=(\S*) no_ut=(\S*) flags=(\S*)", run.stdout)}
    return run, seen


@needs_fpp
@needs_cmake
def test_build_testing_is_scoped_to_the_listed_module_and_restored(default, tmp_path):
    """The generated add_fprime_subdirectory(), run by CMake over a listed module, a module
    that one adds, and a module that is not listed"""
    run, seen = configure(scope_project(tmp_path, root_of(default)))
    assert run.returncode == 0, run.stderr
    assert seen["listed"] == {"testing": "ON", "no_ut": "ON", "flags": ""}, "the stub variant, no unit test, no coverage flags"
    assert seen["listed-after-child"] == seen["listed"]
    for other in ("child", "other", "root"):
        assert seen[other] == {"testing": "OFF", "no_ut": "OFF", "flags": "--coverage"}, f"{other}: the build's own values"
    assert "Listed is read with BUILD_TESTING set (cmake.stub_variants)" in run.stdout


@needs_fpp
@needs_cmake
def test_unit_test_build_of_the_overlay_is_left_alone(default, tmp_path):
    """With BUILD_TESTING on for the whole build (`fprime-util generate --ut`) the function changes nothing"""
    run, seen = configure(scope_project(tmp_path, root_of(default)), "-DBUILD_TESTING=ON")
    assert run.returncode == 0, run.stderr
    assert {entry["testing"] for entry in seen.values()} == {"ON"}
    assert {entry["no_ut"] for entry in seen.values()} == {"OFF"} and seen["listed"]["flags"] == "--coverage"


@needs_fpp
@needs_cmake
def test_listed_module_that_is_never_added_stops_the_configure_step(default, tmp_path):
    run, _ = configure(scope_project(tmp_path, root_of(default), listed="Listed/Child/Missing"))
    assert run.returncode != 0
    assert "cmake.stub_variants in the manifest lists" in run.stderr and "Listed/Child/Missing" in run.stderr
    assert "Remove the entry, or list the directory that is added" in " ".join(run.stderr.split())


@needs_fpp
def test_stub_variant_of_a_library_module_and_of_a_patched_copy(tmp_path):
    # A module that a library's library.cmake adds (through another module of the library)
    library = generate(MISSION, payload(stub_variants=[IMAGER, UNITS]), tmp_path / "library")
    listed = re.search(r"set\(WSTS_STUB_VARIANT_MODULES\n(.*?)\n\)", root_of(library), re.S).group(1).split()
    assert [entry.rsplit("/mission/", 1)[1].rstrip('"') for entry in listed] == [IMAGER, UNITS]
    assert all(entry["selected"] for entry in library.report["testing_variants"])
    # A module that is built from a patched copy: SensorLink holds the substituted UART of Cdh.
    # The entry names the original; the copy keeps the branch and is read in its place.
    project = copy_flight(tmp_path)
    link = project / "lib" / "sensor-lib" / "SensorLib" / "SensorLink" / "CMakeLists.txt"
    link.write_text(link.read_text() + "if (BUILD_TESTING)\n    add_compile_definitions(SENSOR_LINK_LOOPBACK)\nendif ()\n")
    patched = generate(project, flight_manifest(
        "Cdh", keep=["comDriver"], main=element_data("cdh")["main"],
        cmake={"stub_variants": ["lib/sensor-lib/SensorLib/SensorLink"], "cxx_flags": ["-include cstring"]}), tmp_path / "patched")
    root = root_of(patched)
    replaced = re.search(r"set\(WSTS_REPLACED_MODULES\n(.*?)\n\)", root, re.S).group(1).strip()
    stubbed = re.search(r"set\(WSTS_STUB_VARIANT_MODULES\n(.*?)\n\)", root, re.S).group(1).strip()
    assert replaced == stubbed and replaced.endswith('SensorLib/SensorLink"'), "the same directory is requested for both"
    function = root[root.index("function(add_fprime_subdirectory FP_SOURCE_DIR)"):root.index("endfunction()")]
    assert function.index("set(BUILD_TESTING ON)") < function.index('add_subdirectory("${WSTS_SOURCE}"'), "set before the copy is added"
    copy = (patched.output / "SensorLib" / "SensorLink" / "CMakeLists.txt").read_text()
    assert "if (BUILD_TESTING)\n    add_compile_definitions(SENSOR_LINK_LOOPBACK)" in copy
    assert patched.report["stub_variants"] == ["lib/sensor-lib/SensorLib/SensorLink"]
    assert not [w for w in patched.report["warnings"] if w.startswith("cmake.stub_variants lists")]


@needs_fpp
def test_manifest_without_the_keys_generates_no_new_cmake(no_stub, tmp_path):
    """Nothing of the new statements unless the manifest asks for them"""
    result = generate(MISSION, payload(stub_variants=None, cxx_flags=None, include_early=None), tmp_path / "out")
    root = root_of(result)
    for new in ("STUB_VARIANT", "BUILD_TESTING", "__FPRIME_NO_UT_GEN__", "CMAKE_CXX_FLAGS", "target_compile_options"):
        assert new not in root, new
    for key in ("stub_variants", "cxx_flags", "configure_options"):
        assert key not in result.report
    # An overlay that needed no redirection before gets one for the stub variant alone
    flight = generate(MISSION, flight_manifest("Payload", cmake={"stub_variants": [IMAGER]}), tmp_path / "flight")
    assert "function(add_fprime_subdirectory FP_SOURCE_DIR)" in root_of(flight)
    plain = generate(MISSION, flight_manifest("Payload"), tmp_path / "plain")
    assert "function(add_fprime_subdirectory" not in root_of(plain), "as before: none without a reason"


@needs_fpp
def test_report_lists_modules_that_change_under_build_testing(default, no_stub):
    by_module = {entry["module"]: entry for entry in default.report["testing_variants"]}
    assert sorted(by_module) == [IMAGER, UNITS]
    imager, units = by_module[IMAGER], by_module[UNITS]
    assert imager["selected"] and not units["selected"]
    assert imager["instances"] == ["Payload.imager (Components.Imager)"] and units["instances"] == []
    assert imager["registers_unit_tests"]
    assert any("CameraStub.cpp" in statement for statement in imager["with_build_testing"])
    assert "find_package(CamCodec REQUIRED)" in imager["without"]
    assert units["variant_with_build_testing_lacks"]["include_directories"]
    text = (default.output / "wsts-report.md").read_text()
    assert "## Modules that change under BUILD_TESTING" in text
    assert f"- `{IMAGER}` (used by `Payload.imager (Components.Imager)`): **read with `BUILD_TESTING` set**" in text
    assert f"- `{UNITS}` (used by no instance of the topology): read with `BUILD_TESTING` off" in text
    assert "Its unit tests (`register_fprime_ut`) are not built in either case" in text
    # The section is there without the key too: it answers what the stub variant would do
    assert [entry["selected"] for entry in no_stub.report["testing_variants"]] == [False, False]


@needs_fpp
def test_sdk_warning_names_the_stub_variant_first(default, no_stub):
    (warning,) = [w for w in no_stub.report["warnings"] if w.startswith(f"module {IMAGER} links")]
    assert f"links '{SDK_FILE}': SDK_ROOT is not set in the simulated build" in warning
    assert "The topology needs the module for Payload.imager (Components.Imager)" in warning
    options = warning.split(". Either ", 1)[1].split("; or ")
    assert options[0].startswith("take the module's stub variant, the one its CMakeLists.txt has under if (BUILD_TESTING)")
    assert f"cmake: {{stub_variants: [{IMAGER}]}}" in options[0] and "only this module is read with BUILD_TESTING set" in options[0]
    assert "it builds CameraStub.cpp in place of CameraSdk.cpp" in warning, "what the stub is"
    assert "its sources do not define selfTest" in warning, "and what it does not provide"
    for other in ("substitute that instance", f"exclude_subdirectories: [{IMAGER}]", "cache: {SDK_ROOT: <path>}"):
        assert any(other in option for option in options[1:]), other
    # With the stub variant selected the module links no SDK file: nothing to warn about
    assert not [w for w in default.report["warnings"] if "links '" in w]
    assert default.report["host_requirements"]["sdk_paths"] == []


# ----------------------------------------------------------------------
# Errors and warnings
# ----------------------------------------------------------------------


@needs_fpp
@pytest.mark.parametrize("entry, message", [
    ("Components/Imagr", r"cmake.stub_variants in the manifest names 'Components/Imagr' \(.*\), which is not a module "
                         r"directory: there is no CMakeLists.txt in it. Paths are relative to the flight project"),
    ("Payload/Top", r"names 'Payload/Top': the generator writes the deployment's own CMakeLists.txt"),
    ("../mission-sim/profiles", "which is not a module directory"),
    ("../../python", r"which is not a module directory"),
])
def test_stub_variant_that_is_no_module_is_an_error(tmp_path, entry, message):
    with pytest.raises(WstsError, match=message):
        generate(MISSION, payload(stub_variants=[entry]), tmp_path / "out")


@needs_fpp
def test_stub_variant_outside_the_build_is_an_error(tmp_path):
    # A module of the flight project that this deployment does not build
    with pytest.raises(WstsError) as error:
        generate(MISSION, flight_manifest("Cdh", keep=["comDriver"], main=element_data("cdh")["main"],
                                          cmake={"stub_variants": [IMAGER]}), tmp_path / "out")
    message = str(error.value)
    assert f"cmake.stub_variants in the manifest names '{IMAGER}', which is not a module of the simulated build" in message
    assert "Modules of the flight project that are built: config/project" in message, "says what could be meant"
    assert "cmake.extra_subdirectories" in message
    # Left out and listed
    with pytest.raises(WstsError, match="which cmake.exclude_subdirectories leaves out of the build: remove one of the two"):
        generate(MISSION, payload(stub_variants=[UNITS], exclude_subdirectories=[UNITS]), tmp_path / "out2")
    # A library that the host stops reading before the module
    with pytest.raises(WstsError, match=r"(?s)fprime-vxworks does not\s+add on a Linux host.*restrict_platforms"):
        generate(MISSION, flight_manifest("Cdh", keep=["comDriver"], main=element_data("cdh")["main"],
                                          cmake={"stub_variants": ["../../lib/fprime-vxworks/VxWorks/Os"]}), tmp_path / "out3")


@needs_fpp
def test_stub_variant_without_a_branch_is_a_warning(tmp_path):
    result = generate(MISSION, payload(stub_variants=[IMAGER, "Components/Adc"]), tmp_path / "out")
    (warning,) = [w for w in result.report["warnings"] if w.startswith("cmake.stub_variants lists")]
    assert "lists Components/Adc, and its CMakeLists.txt has nothing under if (BUILD_TESTING)" in warning
    assert "set that one with cmake.cache" in warning, "says what to do"
    assert result.report["stub_variants"] == [IMAGER, "Components/Adc"], "it is still read that way"


@needs_fpp
def test_build_testing_for_the_whole_build_is_a_warning(tmp_path, default):
    cache = {**element_data("payload")["cmake"]["cache"], "BUILD_TESTING": True}
    result = generate(MISSION, payload(cache=cache), tmp_path / "out")
    (warning,) = [w for w in result.report["warnings"] if w.startswith("BUILD_TESTING is ON")]
    assert "for the whole simulated build: cmake.cache in the manifest sets it" in warning
    for effect in ("every module's unit-test executable", "address, leak and undefined-behaviour sanitizers",
                   "sanitizer runtime that matches the host compiler", "BUILD_UT", "stubs included (2 module(s) of this build do"):
        assert effect in warning, effect
    assert "cmake:\n    stub_variants: [<module directory, relative to the flight project>]" in warning
    assert result.report["host_requirements"]["sanitizers"] == ["address", "leak", "undefined"]
    assert all(entry["selected"] for entry in result.report["testing_variants"]), "every module's variant, then"
    assert "- Sanitizer runtime for: address, leak, undefined" in (result.output / "wsts-report.md").read_text()
    # Off, or not set: no warning and no sanitizer to look for
    assert not [w for w in default.report["warnings"] if "BUILD_TESTING is ON" in w or "default_cmake_options" in w]
    assert default.report["host_requirements"]["sanitizers"] == []
    off = generate(MISSION, payload(cache={**cache, "BUILD_TESTING": False}), tmp_path / "off")
    assert not [w for w in off.report["warnings"] if "BUILD_TESTING is ON" in w]


@needs_fpp
def test_build_testing_in_the_flight_project_is_a_warning(tmp_path):
    project = copy_flight(tmp_path)
    settings = project / "settings.ini"
    settings.write_text(settings.read_text().replace("    VX_TARGET_TYPE=DKM\n", "    VX_TARGET_TYPE=DKM\n    BUILD_TESTING=ON\n"))
    result = generate(project, flight_manifest("Payload", cmake={"stub_variants": [IMAGER]}), tmp_path / "out")
    (warning,) = [w for w in result.report["warnings"] if "default_cmake_options" in w]
    assert warning.startswith("The flight project's default_cmake_options (settings.ini) set BUILD_TESTING to ON; it is "
                              "not carried over to the simulated build.")
    assert "stub_variants: [<module directory" in warning
    assert "BUILD_TESTING=ON" in result.report["dropped_cmake_options"]
    assert "BUILD_TESTING" not in (result.output / "settings.ini").read_text()
    assert result.report["host_requirements"]["sanitizers"] == []
    # A root CMakeLists.txt that sets it before F Prime is read: plain values are carried over
    root = project / "CMakeLists.txt"
    root.write_text(root.read_text().replace("set(SENSOR_LIB_BOARD cdh-rev-b)\n", "set(SENSOR_LIB_BOARD cdh-rev-b)\nset(BUILD_TESTING ON)\n"))
    carried = generate(project, flight_manifest("Payload"), tmp_path / "carried")
    (warning,) = [w for w in carried.report["warnings"] if w.startswith("BUILD_TESTING is ON")]
    assert "CMakeLists.txt sets it before F Prime is read" in warning and "{cache: {BUILD_TESTING: OFF}}" in warning
    assert carried.report["host_requirements"]["sanitizers"] == ["address", "leak", "undefined"]
    kept_off = generate(project, flight_manifest("Payload", cmake={"cache": {"BUILD_TESTING": "OFF"}}), tmp_path / "kept-off")
    assert not [w for w in kept_off.report["warnings"] if "BUILD_TESTING is ON" in w]


# ----------------------------------------------------------------------
# Compiler flags and configure options
# ----------------------------------------------------------------------


@needs_fpp
def test_compiler_flags_are_applied_by_the_generated_project(default, tmp_path):
    root = root_of(default)
    assert 'target_compile_options(SensorLib_Units PRIVATE "$<$<COMPILE_LANGUAGE:CXX>:SHELL:-include cstring>")' in root
    assert root.index("target_compile_options(SensorLib_Units") > root.index("set(WSTS_ADDING_DEFERRED ON)"), "once the target exists"
    assert "CMAKE_CXX_FLAGS" not in root, "for that module only"
    assert default.report["cxx_flags"] == {UNITS: ["-include cstring"]}
    everywhere = generate(MISSION, payload(cxx_flags=["-include cstring", "-fno-strict-aliasing"]), tmp_path / "out")
    lines = root_of(everywhere).splitlines()
    flags = lines.index('string(APPEND CMAKE_CXX_FLAGS " -include cstring -fno-strict-aliasing")')
    assert lines.index("project(PayloadSim VERSION 1.0.0 LANGUAGES C CXX)") < flags < next(
        i for i, line in enumerate(lines) if line.startswith("find_package(FPrime"))
    assert everywhere.report["cxx_flags"] == {"all modules": ["-include cstring", "-fno-strict-aliasing"]}
    assert everywhere.report["host_requirements"]["search"]["cxx_flags"] == ["-include cstring", "-fno-strict-aliasing"]
    with pytest.raises(WstsError, match=r"cmake.cxx_flags in the manifest names 'lib/sensor-lib/SensorLib/Unit'"):
        generate(MISSION, payload(cxx_flags={"lib/sensor-lib/SensorLib/Unit": ["-include cstring"]}), tmp_path / "out2")


FLAGS_PROJECT = '''
cmake_minimum_required(VERSION 3.16)
project(Flags LANGUAGES C CXX)
@EARLY@
add_library(SensorLib_Units STATIC a.cpp b.c)
add_library(Other STATIC c.cpp)
@LATE@
'''


@needs_fpp
@needs_cmake
@needs_compiler
def test_include_flag_reaches_the_compile_line_and_cache_entry_does_not(default, tmp_path):
    """The statements the generator writes for `cmake.cxx_flags` and for a `cmake.cache`
    entry of CMAKE_CXX_FLAGS, in a project of their own: the first compile a source that
    uses memcpy without <cstring>, the second changes nothing"""
    cached = generate(MISSION, payload(
        cxx_flags=["-include cstring"],
        cache={**element_data("payload")["cmake"]["cache"], "CMAKE_CXX_FLAGS": "-DFROM_CACHE", "CMAKE_BUILD_TYPE": "Debug"}),
        tmp_path / "out")
    root = root_of(cached)
    early = [line for line in root.splitlines() if line.startswith(("string(APPEND CMAKE_CXX_FLAGS", "set(CMAKE_CXX_FLAGS"))]
    assert len(early) == 2
    late = next(line for line in root_of(default).splitlines() if "target_compile_options(SensorLib_Units" in line).strip()
    source = "void* copy(void* to, const void* from) { return memcpy(to, from, 1); }\n"
    results = {}
    for name, (before, after) in {"all": (early, ""), "module": ([early[1]], late), "cache": ([early[1]], "")}.items():
        project = tmp_path / name
        project.mkdir()
        (project / "CMakeLists.txt").write_text(FLAGS_PROJECT.replace("@EARLY@", "\n".join(before)).replace("@LATE@", after))
        (project / "a.cpp").write_text(source)
        (project / "c.cpp").write_text("int other() { return 0; }\n")
        (project / "b.c").write_text("int in_c(void) { return 0; }\n")
        generator = ["-G", "Ninja"] if shutil.which("ninja") else []
        assert subprocess.run(["cmake", "-S", str(project), "-B", str(project / "b"), *generator,
                               "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON"], capture_output=True).returncode == 0
        commands = {Path(entry["file"]).name: entry["command"] for entry in json.loads((project / "b" / "compile_commands.json").read_text())}
        results[name] = (commands, subprocess.run(["cmake", "--build", str(project / "b")], capture_output=True, text=True))
    commands, build = results["all"]
    assert "-include cstring" in commands["a.cpp"] and "-include cstring" in commands["c.cpp"]
    assert "-include" not in commands["b.c"], "C++ sources only: <cstring> is no C header"
    assert build.returncode == 0, build.stdout
    commands, build = results["module"]
    assert "-include cstring" in commands["a.cpp"] and "-include" not in commands["c.cpp"] and "-include" not in commands["b.c"]
    assert build.returncode == 0, build.stdout
    commands, build = results["cache"]
    assert "FROM_CACHE" not in commands["a.cpp"], "project() has set the cache variable: the entry leaves it alone"
    if "memcpy" in build.stdout + build.stderr:
        # This host's compiler does not bring <cstring> in by itself: the recipe is what made it build
        assert build.returncode != 0
    # Said when generating, with what to use instead
    (flags,) = [w for w in cached.report["warnings"] if w.startswith("cmake.cache sets CMAKE_CXX_FLAGS")]
    assert "which has no effect" in flags and "cmake:\n    cxx_flags: ['-DFROM_CACHE']" in flags
    (build_type,) = [w for w in cached.report["warnings"] if w.startswith("cmake.cache sets CMAKE_BUILD_TYPE")]
    assert "fprime-util generate -DCMAKE_BUILD_TYPE=Debug (directly, without '--'" in build_type
    assert cached.report["configure_options"] == ["-DCMAKE_BUILD_TYPE=Debug"]


def test_fprime_util_takes_cmake_options_directly():
    """The form the documentation and the printed build line use, against the installed fprime-util"""
    cli = pytest.importorskip("fprime.util.cli")
    parsed, cmake_args, *_ = cli.parse_args(["generate", "-DCMAKE_BUILD_TYPE=Debug"])
    assert cmake_args == {"CMAKE_BUILD_TYPE": "Debug"} and parsed.platform == "default"
    parsed, cmake_args, *_ = cli.parse_args(["generate", "--", "-DCMAKE_BUILD_TYPE=Debug"])
    assert parsed.platform == "-DCMAKE_BUILD_TYPE=Debug" and not cmake_args, "after '--' it is the name of a toolchain"
    parsed, cmake_args, *_ = cli.parse_args(["generate", "-DCMAKE_CXX_FLAGS=-include cstring"])
    assert not cmake_args, "a value with a space is not taken as an option: cmake.cxx_flags is for those"


@needs_fpp
def test_build_line_has_the_configure_options(tmp_path, capsys, monkeypatch):
    sim = tmp_path / "sim"
    shutil.copytree(SIM, sim, ignore=shutil.ignore_patterns("wsts"))
    text = (sim / "payload.yaml").read_text().replace("project: ../mission", f"project: {MISSION}")
    (sim / "payload.yaml").write_text(text.replace("  cache:\n", "  cache:\n    CMAKE_BUILD_TYPE: Debug\n"))
    assert main(["generate", str(sim / "payload.yaml"), "--output", str(tmp_path / "out")]) == 0
    printed = capsys.readouterr().out
    assert f"Build it with: cd {tmp_path / 'out'} && fprime-util generate -DCMAKE_BUILD_TYPE=Debug && fprime-util build" in printed
    assert (tmp_path / "out" / "wsts-host.md").is_file(), "the host check of this machine, beside the report"
    assert main(["generate", str(sim / "payload.yaml"), "--output", str(tmp_path / "out"), "--check"]) == 0, \
        "which is not part of what --check compares"


@needs_fpp
def test_output_below_a_symbolic_link_is_said(tmp_path, default):
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "link").symlink_to(real)
    result = generate(MISSION, payload(), tmp_path / "link" / "out")
    (notice,) = result.notices
    assert result.output == real / "out"
    assert f"{tmp_path / 'link' / 'out'} is reached through a symbolic link: the output directory is {real / 'out'}" in notice
    assert "differs from CMake cache" in notice and "fprime-util generate -f" in notice
    assert not default.notices
    assert not [w for w in result.report["warnings"] if "symbolic link" in w], "about the run, not in the checked-in report"


# ----------------------------------------------------------------------
# Host check: what the build needs
# ----------------------------------------------------------------------


def test_includes_a_host_compile_reaches(tmp_path):
    header = tmp_path / "x.hpp"
    header.write_text(
        "#ifndef X_HPP\n#define X_HPP\n#include <always.h>\n"
        "#ifdef TGT_OS_TYPE_VXWORKS\n#include <vxWorks.h>\n#else\n#include <unistd.h>\n#endif\n"
        "#if defined(_WIN32)\n#include <windows.h>\n#elif defined(BOARD_HAS_FPGA)\n#include <fpga/regs.h>\n#else\n#include <generic.h>\n#endif\n"
        "#ifdef WSTS_SIM\n#include <sim.h>\n#endif\n"
        "#if __has_include(<optional.h>)\n#include <optional.h>\n#endif\n"
        "#ifndef MISSION_FLAG\n#include \"no_flag.h\"\n#endif\n"
        "#if 0\n#include <never.h>\n#endif\n#endif\n")
    found = {name: (angle, condition) for name, angle, _, condition in hostcheck.read_includes(header, hostcheck.DEFINED)}
    assert set(found) == {"always.h", "unistd.h", "fpga/regs.h", "generic.h", "sim.h", "no_flag.h"}
    assert found["always.h"] == (True, ""), "an include guard is no condition"
    assert found["unistd.h"][1] == "" and found["sim.h"][1] == ""
    assert found["fpga/regs.h"][1] == "#elif defined(BOARD_HAS_FPGA)", "not known either way: conditional"
    assert found["generic.h"][1].startswith("#else of") and found["no_flag.h"] == (False, "#ifndef MISSION_FLAG")
    defined = {name for name, *_ in hostcheck.read_includes(header, {*hostcheck.DEFINED, "MISSION_FLAG", "BOARD_HAS_FPGA"})}
    assert "no_flag.h" not in defined and "generic.h" not in defined and "fpga/regs.h" in defined


def test_headers_from_outside_the_build_roots(tmp_path):
    project, library = tmp_path / "project", tmp_path / "lib"
    module = write_cmake(project / "Cam", "register_fprime_module()\n")
    (module / "Cam.cpp").write_text(
        '#include "Cam.hpp"\n#include <vector>\n#include <stdint.h>\n#include <sys/ioctl.h>\n#include <Cam/CamComponentAc.hpp>\n'
        "#include <algo/filter.h>\n#include <vendor/sdk.h>\n")
    (module / "Cam.hpp").write_text("#include <png.h>\n#ifdef BOARD_REV_B\n#include <revb/io.h>\n#endif\n")
    (library / "Algo" / "include" / "algo").mkdir(parents=True)
    (library / "Algo" / "include" / "algo" / "filter.h").write_text("#include <zmq.h>\n#include <cstring>\n")
    index = hostcheck.HeaderIndex([project, library], walked=[project, library])
    needs = hostcheck.scan_headers([(module / "Cam.cpp", "Cam")], index, lambda path: True, [],
                                   lambda path: path.relative_to(tmp_path).as_posix())
    assert list(needs) == ["png.h", "revb/io.h", "sys/ioctl.h", "vendor/sdk.h", "zmq.h"], \
        "not the standard ones, the autocoded one, or the one in a module's own include directory"
    assert needs["vendor/sdk.h"] == [{"module": "Cam", "file": "project/Cam/Cam.cpp", "line": 7}]
    assert needs["png.h"] == [{"module": "Cam", "file": "project/Cam/Cam.hpp", "line": 1}], "through the module's header"
    assert needs["zmq.h"][0]["file"] == "lib/Algo/include/algo/filter.h", "and through a library's header"
    assert needs["revb/io.h"][0]["condition"] == "#ifdef BOARD_REV_B"


@needs_fpp
def test_requirements_of_the_example(default, no_stub):
    clean = default.report["host_requirements"]
    # Payload/Main.cpp, and sensor-lib's register interface, which maps a device on the flight
    # board; <board/revision.h> is in the simulation project
    assert list(clean["system_headers"]) == ["fcntl.h", "getopt.h", "sys/mman.h", "unistd.h"]
    assert clean["libraries"] == {} and clean["packages"] == {} and clean["sdk_paths"] == []
    assert clean["search"]["include_directories"] == [os.path.relpath(SIM / "host" / "include", default.output)]
    modules = {m["module"]: m for m in clean["modules"]}
    assert modules[IMAGER] == {"module": IMAGER, "instances": ["Payload.imager (Components.Imager)"],
                               "has_stub_variant": True, "stub_variant": True}
    assert modules[UNITS]["has_stub_variant"] and "stub_variant" not in modules[UNITS]
    assert clean["flight_toolchain"] == "vxworks" and clean["flight_compiler"]["toolchain_file"].endswith("toolchain/vxworks.cmake")
    text = (default.output / "wsts-report.md").read_text()
    assert "## What the build takes from the host" in text and "- System headers: `fcntl.h`, `getopt.h`" in text
    assert "standard library headers include one another differently between compilers and versions" in text
    # Without the stub variant the Imager's SDK sources are what is compiled
    needed = no_stub.report["host_requirements"]
    assert {"camsdk/camera.h", "camcodec.h"} <= set(needed["system_headers"])
    (use,) = needed["system_headers"]["camsdk/camera.h"]
    assert use["module"] == IMAGER and use["file"].endswith("Components/Imager/CameraSdk.cpp") and use["line"] == 8
    assert list(needed["libraries"]) == ["camcodec"] and needed["libraries"]["camcodec"][0]["module"] == IMAGER
    assert needed["packages"]["CamCodec"]["required"] and needed["packages"]["CamCodec"]["needed_by"][0]["line"] == 19
    (sdk,) = needed["sdk_paths"]
    assert sdk["entry"] == SDK_FILE and sdk["module"] == IMAGER and sdk["variables"] == ["SDK_ROOT"]


# ----------------------------------------------------------------------
# Host check: asking the host
# ----------------------------------------------------------------------


def kinds(result) -> dict:
    found = {}
    for item in result["missing"]:
        found.setdefault(item["kind"], []).append(item["name"])
    return found


@needs_fpp
@needs_compiler
@needs_cmake
def test_host_check_reports_what_is_missing(no_stub):
    result = hostcheck.probe(no_stub.report["host_requirements"], no_stub.output)
    assert not result["ok"]
    assert kinds(result) == {"header": ["camcodec.h", "camsdk/camera.h"], "library": ["camcodec"],
                             "package": ["CamCodec"], "sdk file": [SDK_FILE]}
    by_name = {item["name"]: item for item in result["missing"]}
    header = by_name["camsdk/camera.h"]
    assert header["needed_by"][0]["module"] == IMAGER and header["needed_by"][0]["file"].endswith("CameraSdk.cpp")
    remedies = " | ".join(header["remedies"])
    assert "install the development package that has it" in remedies
    assert "include_directories(<dir>) in a file named under cmake.include_early" in remedies
    assert f"take its stub variant (cmake.stub_variants: [{IMAGER}])" in remedies, "this module has one"
    assert f"cmake.exclude_subdirectories: [{IMAGER}]" in remedies and "substitute" in remedies
    assert "link_directories(<dir>)" in " ".join(by_name["camcodec"]["remedies"])
    assert "CamCodec_DIR" in " ".join(by_name["CamCodec"]["remedies"])
    assert "cmake.cache: {SDK_ROOT: <path>}" in " ".join(by_name[SDK_FILE]["remedies"])
    text = hostcheck.render(result, no_stub.report["host_requirements"])
    assert "Missing on this host (5):" in text and f"needed by {IMAGER} (" in text
    assert "list its name under host.ignore in the manifest" in text
    assert result["compiler"]["version"] and "Host compiler: " in text
    assert any("flight toolchain" in note and "cmake.cxx_flags" in note for note in result["notes"]), \
        "another compiler than flight's: said as a note"
    assert hostcheck.summary(result)[0].startswith("this host lacks 5 thing(s) the build needs (header: camcodec.h, camsdk/camera.h; library: camcodec")


@needs_fpp
@needs_compiler
def test_host_check_of_a_clean_project(default):
    result = hostcheck.probe(default.report["host_requirements"], default.output)
    assert result["ok"] and result["missing"] == [] and result["warnings"] == []
    assert "Nothing is missing on this host." in hostcheck.render(result, default.report["host_requirements"])
    assert hostcheck.summary(result) == []


@needs_fpp
def test_host_check_without_a_compiler_is_a_warning(no_stub):
    env = {"PATH": "/nonexistent", "CXX": "/nonexistent/bin/c++"}
    result = hostcheck.probe(no_stub.report["host_requirements"], no_stub.output, env)
    assert [item["kind"] for item in result["missing"]] == ["sdk file"], "what needs no compiler is still checked"
    (compiler, packages) = result["warnings"]
    assert compiler.startswith("no C++ compiler to ask (CXX=/nonexistent/bin/c++): headers, libraries and the sanitizer runtime were not checked")
    assert packages.startswith("find_package() calls were not checked: cmake did not run. Packages the build asks for: CamCodec")
    clean = dict(no_stub.report["host_requirements"], sdk_paths=[])
    assert hostcheck.probe(clean, no_stub.output, env)["ok"], "a check that cannot be done is not a failure"


@needs_fpp
@needs_compiler
@needs_cmake
def test_host_directories_from_an_early_cmake_file_are_used(tmp_path):
    """The recipe: include_directories() and link_directories() in a `cmake.include_early`
    file, and the SDK variable from `cmake.cache`"""
    sim = tmp_path / "sim"
    shutil.copytree(SIM, sim, ignore=shutil.ignore_patterns("wsts"))
    sdk = tmp_path / "host-sdk"
    (sdk / "include" / "camsdk").mkdir(parents=True)
    (sdk / "include" / "camsdk" / "camera.h").write_text("")
    (sdk / "include" / "camcodec.h").write_text("")
    (sdk / "3rdparty" / "lib").mkdir(parents=True)
    (sdk / "3rdparty" / "lib" / "libcamsdk.a").write_bytes(b"!<arch>\n")
    (sdk / "lib").mkdir()
    (sdk / "lib" / "libcamcodec.a").write_bytes(b"!<arch>\n")
    (sdk / "lib" / "CamCodecConfig.cmake").write_text("set(CamCodec_FOUND TRUE)\n")
    with (sim / "host.cmake").open("a") as early:
        early.write(f'include_directories(SYSTEM "{sdk}/include")\nlink_directories("{sdk}/lib")\n')
    data = element_data("payload")
    data["project"] = str(MISSION)
    data["cmake"].pop("stub_variants")
    before = generate(MISSION, parse_manifest(data, sim / "payload.yaml"), tmp_path / "before")
    result = hostcheck.probe(before.report["host_requirements"], before.output)
    assert kinds(result) == {"package": ["CamCodec"], "sdk file": [SDK_FILE]}, "headers and library are found there"
    data["cmake"]["cache"] = {**data["cmake"]["cache"], "SDK_ROOT": str(sdk), "CamCodec_DIR": str(sdk / "lib")}
    after = generate(MISSION, parse_manifest(data, sim / "payload.yaml"), tmp_path / "after")
    assert not [w for w in after.report["warnings"] if "links '" in w]
    result = hostcheck.probe(after.report["host_requirements"], after.output)
    assert result["ok"], result["missing"]
    root = root_of(after)
    assert root.index("find_package(FPrime") < root.index('host.cmake")') < root.index("function(add_fprime_subdirectory"), \
        "before the framework and the libraries are read"


@needs_fpp
@needs_compiler
@needs_cmake
def test_false_positive_is_silenced_in_the_manifest(tmp_path):
    data = element_data("payload")
    data["cmake"].pop("stub_variants")
    data["host"] = {"ignore": ["camsdk/camera.h", "camcodec.h", "-lcamcodec", "CamCodec", SDK_FILE]}
    result = generate(MISSION, parse_manifest(data, SIM / "payload.yaml"), tmp_path / "out")
    assert result.report["host_requirements"]["ignore"] == data["host"]["ignore"]
    assert set(result.report["host_requirements"]["system_headers"]) >= {"camsdk/camera.h"}, "still listed as needed"
    assert hostcheck.probe(result.report["host_requirements"], result.output)["ok"]


@needs_compiler
def test_sanitizer_runtime_is_checked_only_when_enabled():
    base = {"modules": [], "system_headers": {}, "libraries": {}, "packages": {}, "sdk_paths": [], "ignore": []}
    assert hostcheck.probe({**base, "sanitizers": []}, Path.cwd())["ok"]
    result = hostcheck.probe({**base, "sanitizers": ["address", "undefined"]}, Path.cwd())
    # Whether this host has the runtime or not, the answer is about that and nothing else
    assert [item["kind"] for item in result["missing"]] in ([], ["sanitizer runtime"])
    for item in result["missing"]:
        assert item["name"] == "-fsanitize=address,undefined" and "cmake.stub_variants" in " ".join(item["remedies"])
    missing = hostcheck.probe({**base, "sanitizers": ["wsts-no-such-sanitizer"]}, Path.cwd())
    assert not missing["ok"] and missing["missing"][0]["kind"] == "sanitizer runtime"


def test_flight_compiler_from_a_flight_build_directory(tmp_path):
    project = tmp_path / "flight"
    detected = project / "build-fprime-automatic-board" / "CMakeFiles" / "3.28.3"
    detected.mkdir(parents=True)
    (detected / "CMakeCXXCompiler.cmake").write_text(
        'set(CMAKE_CXX_COMPILER "/opt/board/bin/board-g++")\nset(CMAKE_CXX_COMPILER_ID "GNU")\nset(CMAKE_CXX_COMPILER_VERSION "15.1.0")\n')
    toolchain = project / "cmake" / "toolchain"
    toolchain.mkdir(parents=True)
    (toolchain / "board.cmake").write_text('set(CMAKE_SYSTEM_NAME Linux)\nset(CMAKE_CXX_COMPILER "board-g++")\n')
    named = hostcheck.flight_compiler("board", [project], lambda path: path.relative_to(tmp_path).as_posix())
    assert named == {"toolchain_file": "flight/cmake/toolchain/board.cmake", "cxx": "board-g++"}
    assert hostcheck.flight_compiler("native", [project], str) is None
    requirements = {"flight_project": "flight", "flight_toolchain": "board", "flight_compiler": named}
    found = hostcheck.flight_build_compiler(requirements, tmp_path)
    assert found["id"] == "GNU" and found["version"] == "15.1.0" and found["cxx"] == "/opt/board/bin/board-g++"
    assert found["from"] == "build-fprime-automatic-board, the flight build directory"
    if hostcheck.host_compiler()[0]:
        result = hostcheck.probe({**requirements, "modules": [], "ignore": []}, tmp_path)
        number = result["compiler"].get("number", "")
        if not (result["compiler"]["id"] == "GNU" and number.split(".")[0] == "15"):
            (note,) = result["notes"]
            assert note.startswith("the flight build uses GNU 15.1.0 (build-fprime-automatic-board, the flight build directory), this host ")
            assert "memcpy" in note and "cmake.cxx_flags: ['-include cstring']" in note
        assert result["ok"], "a difference is a note"


# ----------------------------------------------------------------------
# Host check: the command
# ----------------------------------------------------------------------


@needs_fpp
@needs_compiler
@needs_cmake
def test_check_host_command_and_exit_status(tmp_path, capsys):
    sim = tmp_path / "sim"
    shutil.copytree(SIM, sim, ignore=shutil.ignore_patterns("wsts"))
    manifest = sim / "payload.yaml"
    manifest.write_text(manifest.read_text().replace("project: ../mission", f"project: {MISSION}"))
    # A manifest: nothing is written
    assert main(["check-host", str(manifest)]) == 0
    printed = capsys.readouterr().out
    assert "Nothing is missing on this host." in printed and "Host compiler: " in printed
    assert not (sim / "wsts").exists()
    # A generated directory
    assert main(["generate", str(manifest)]) == 0
    capsys.readouterr()
    assert main(["check-host", str(sim / "wsts" / "Payload"), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    # Something missing: said when generating, and exit status 1 from the check
    manifest.write_text(manifest.read_text().replace("  stub_variants: [Components/Imager]\n", ""))
    assert main(["generate", str(manifest)]) == 0, "generating is not the check"
    printed = capsys.readouterr().out
    assert "  host: this host lacks 5 thing(s) the build needs (header: camcodec.h, camsdk/camera.h; library: camcodec; " \
           "package: CamCodec; sdk file: " in printed
    assert f"  host: details and what to do: fprime-wsts check-host {sim / 'wsts' / 'Payload'}" in printed
    assert main(["check-host", str(sim / "wsts" / "Payload")]) == 1
    printed = capsys.readouterr().out
    assert "Missing on this host (5):" in printed and "  header camsdk/camera.h" in printed
    assert "(usually" not in printed.split("header camsdk/camera.h")[1].split("\n  ")[1], "no package is guessed for an unknown header"
    host = (sim / "wsts" / "Payload" / "wsts-host.md").read_text()
    assert "Missing on this host (5):" in host
    assert main(["report", str(sim / "wsts" / "Payload")]) == 0
    assert "Host compiler: " in capsys.readouterr().out, "the report shows the host check of this machine with it"
    assert main(["check-host", str(tmp_path)]) == 2, "not a generated directory"


def test_package_hint_says_it_is_a_hint(tmp_path):
    requirements = {"modules": [], "libraries": {}, "packages": {}, "sdk_paths": [], "ignore": [],
                    "system_headers": {"wsts-no-such-dir/png.h": [], "png.h": [{"module": "M", "file": "M/a.cpp", "line": 3}]}}
    if not hostcheck.host_compiler()[0]:
        pytest.skip("needs a C++ compiler on PATH")
    result = hostcheck.probe(requirements, tmp_path)
    for item in result["missing"]:
        if item["name"] == "png.h":
            assert item["hint"] == "libpng-dev"
            assert "usually libpng-dev; a hint, names differ between distributions" in item["remedies"][0]
    assert "wsts-no-such-dir/png.h" in [item["name"] for item in result["missing"]]


@needs_fpp
@needs_compiler
def test_check_host_of_the_example_system():
    """The checked-in example is clean on a development host: CI runs the same command"""
    assert main(["check-host", str(SIM / "wsts-system.yaml"), "--json"]) == 0
