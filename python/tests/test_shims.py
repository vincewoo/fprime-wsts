"""Operating system shims (`shims` in the manifest): the VxWorks API shim of Wsts/VxWorks.

What the generator does with it: the shim's headers on every module's include path, the
shim linked into the deployment, its headers found by the host check and its calls left
out of the code that waits on the host. Without it: a warning that names it, and the
host check's remedy. The example's Cdh/Heartbeat is VxWorks code without guards.

The generator tests need the FPP tools; the host check needs a compiler.
"""

from pathlib import Path

import pytest

from fprime_wsts import hostcheck, hostwait
from fprime_wsts.fpp import WstsError
from fprime_wsts.generate import generate
from fprime_wsts.manifest import SHIMS, TOOLKIT_ROOT, parse_manifest

from mission import MISSION, element_manifest, needs_fpp

needs_compiler = pytest.mark.skipif(not hostcheck.host_compiler()[0], reason="needs a C++ compiler on PATH")

VXWORKS_HEADERS = ["logLib.h", "msgQLib.h", "semLib.h", "taskLib.h", "tickLib.h", "vxWorks.h", "wdLib.h"]
HEARTBEAT = "Cdh/Heartbeat"


# ----------------------------------------------------------------------
# Manifest
# ----------------------------------------------------------------------


def test_the_manifest_names_shims():
    assert parse_manifest({"deployment": "D"}).shims == []
    assert parse_manifest({"deployment": "D", "shims": ["VxWorks", "vxworks"]}).shims == ["vxworks"]


@pytest.mark.parametrize("value, message", [
    ("vxworks", "shims must be a list"),
    (["rtems"], "no shim for rtems; there is one for: vxworks"),
])
def test_a_shim_that_does_not_exist_is_an_error(value, message):
    with pytest.raises(WstsError, match=message):
        parse_manifest({"deployment": "D", "shims": value})


def test_every_shim_has_headers():
    for name, directory in SHIMS.items():
        assert (TOOLKIT_ROOT / directory / "include").is_dir(), name
    assert set(VXWORKS_HEADERS) <= set(hostcheck.shim_headers())
    assert hostcheck.shim_headers()["taskLib.h"] == "vxworks"


# ----------------------------------------------------------------------
# Text only: the header index, code that waits on the host
# ----------------------------------------------------------------------


def test_a_shims_headers_are_found_only_when_it_is_used(tmp_path):
    module = tmp_path / "Beat"
    module.mkdir()
    (module / "CMakeLists.txt").write_text("register_fprime_module()\n")
    (module / "Beat.cpp").write_text("#include <vxWorks.h>\n#include <taskLib.h>\n#include <vendor/sdk.h>\n")
    include = TOOLKIT_ROOT / SHIMS["vxworks"] / "include"
    shown = lambda path: path.name  # noqa: E731

    def needs(**shim):
        index = hostcheck.HeaderIndex([tmp_path, TOOLKIT_ROOT], walked=[tmp_path], fixed=[TOOLKIT_ROOT / "Wsts"], **shim)
        return list(hostcheck.scan_headers([(module / "Beat.cpp", "Beat")], index, lambda path: False, [], shown))

    assert needs(exclude=[include]) == ["taskLib.h", "vendor/sdk.h", "vxWorks.h"], "the toolkit has them, the build does not"
    assert needs(extra_roots=[include]) == ["vendor/sdk.h"]


def test_calls_the_shim_implements_do_not_wait_on_the_host(tmp_path):
    module = tmp_path / "Beat"
    module.mkdir()
    (module / "Beat.cpp").write_text(
        "void f() {\n  taskDelay(1);\n  semTake(s, WAIT_FOREVER);\n  msgQReceive(q, b, 4, NO_WAIT);\n"
        "  taskSpawn(n, 1, 0, 0, f, 0,0,0,0,0,0,0,0,0,0);\n  t = tickGet();\n  usleep(5);\n}\n")
    every = [f.call for f in hostwait.scan_module(module)]
    assert every == ["taskDelay", "semTake", "msgQReceive", "taskSpawn", "tickGet", "usleep"]
    assert [f.call for f in hostwait.scan_module(module, shims=["vxworks"])] == ["usleep"], \
        "simulated time and the turn, with the shim; the host's sleep still sleeps"


# ----------------------------------------------------------------------
# The example's Cdh, with and without the shim
# ----------------------------------------------------------------------


@pytest.fixture(scope="module")
def shimmed(tmp_path_factory):
    return generate(MISSION, element_manifest("cdh"), tmp_path_factory.mktemp("shims") / "out")


@pytest.fixture(scope="module")
def unshimmed(tmp_path_factory):
    return generate(MISSION, element_manifest("cdh", shims=None), tmp_path_factory.mktemp("shims") / "out")


@needs_fpp
def test_the_overlay_includes_and_links_the_shim(shimmed):
    root = (shimmed.output / "CMakeLists.txt").read_text()
    assert 'include_directories(AFTER "${CMAKE_CURRENT_LIST_DIR}/' in root
    assert root.split('include_directories(AFTER "', 1)[1].split('")', 1)[0].endswith("Wsts/VxWorks/include")
    deployment = (shimmed.output / "Cdh" / "CMakeLists.txt").read_text()
    assert ('target_link_libraries(${FPRIME_CURRENT_MODULE} PRIVATE "$<LINK_LIBRARY:WHOLE_ARCHIVE,Wsts_VxWorks>")'
            in deployment)
    assert "Wsts_VxWorks" not in deployment.split("register_fprime_deployment(", 1)[1].split(")", 1)[0], \
        "not in DEPENDS as well: CMake refuses a library with and without the feature"
    assert shimmed.report["shims"] == ["vxworks"]
    assert "## Operating system shims" in (shimmed.output / "wsts-report.md").read_text()


@needs_fpp
def test_with_the_shim_its_headers_and_calls_ask_nothing_of_the_host(shimmed):
    requirements = shimmed.report["host_requirements"]
    assert not set(VXWORKS_HEADERS) & set(requirements["system_headers"])
    assert not any("shims: [vxworks]" in warning for warning in shimmed.report["warnings"])
    assert not any("Cdh.Heartbeat" in item["components"] for item in shimmed.report["time"]["waits_on_host"])


@needs_fpp
def test_without_the_shim_the_generator_names_it(unshimmed):
    requirements = unshimmed.report["host_requirements"]
    assert set(VXWORKS_HEADERS) <= set(requirements["system_headers"])
    assert requirements["system_headers"]["taskLib.h"][0]["module"] == HEARTBEAT
    (warning,) = [w for w in unshimmed.report["warnings"] if "shims: [vxworks]" in w]
    assert warning.startswith(", ".join(VXWORKS_HEADERS) + " are included without a guard")
    assert f"module {HEARTBEAT}" in warning and "#ifndef WSTS_SIM" in warning
    assert "shims" not in unshimmed.report
    assert "Operating system shims" not in (unshimmed.output / "wsts-report.md").read_text()
    assert "Wsts_VxWorks" not in (unshimmed.output / "Cdh" / "CMakeLists.txt").read_text()
    (heartbeat,) = [i for i in unshimmed.report["time"]["waits_on_host"] if "Cdh.Heartbeat" in i["components"]]
    assert {c["call"] for c in heartbeat["calls"]} == {"taskSpawn", "semTake", "msgQReceive", "tickGet"}
    assert heartbeat["instances"] == ["Cdh.heartbeat"]


@needs_fpp
@needs_compiler
def test_the_host_check_names_the_shim_first(unshimmed, shimmed):
    result = hostcheck.probe(unshimmed.report["host_requirements"], unshimmed.output)
    missing = {item["name"]: item for item in result["missing"]}
    assert set(VXWORKS_HEADERS) <= set(missing)
    assert missing["semLib.h"]["remedies"][0].startswith("shims: [vxworks] in the manifest")
    assert hostcheck.probe(shimmed.report["host_requirements"], shimmed.output)["ok"]
