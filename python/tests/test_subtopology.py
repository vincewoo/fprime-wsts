"""Deployments built from subtopologies: instances defined outside Top, configuration
overrides, tick sources. Needs the FPP tools (fprime-fpp) on PATH."""

import shutil

import pytest

from fprime_wsts import fpp
from fprime_wsts.fpp import WstsError
from fprime_wsts.generate import Plan, _check_tick_sources, generate, resolve_instance
from fprime_wsts.manifest import LinkEnd, parse_manifest
from fprime_wsts.system import parse_system
from mission import CDH_MAIN, MISSION, REPO, SYSTEM, copy_flight, flight_manifest, needs_fpp

UART = "SensorLink.uart"

#: Cdh with a manifest in the flight project: the UART is inside the SensorLink
#: subtopology of lib/sensor-lib, and a stock model answers behind it
CDH = {
    "deployment": "Cdh",
    "keep": ["comDriver"],
    "main": CDH_MAIN,
    "models": [{"name": "tempSensor", "class": "Wsts::ScriptedByteStream", "endpoint": "uart", "kind": "bytestream",
                "params": {"text": "$TEMP,21.5\n", "period": "10"}}],
}


def manifest_of(**changes):
    data = {**CDH, **changes}
    return parse_manifest({k: v for k, v in data.items() if v is not None})


def chosen(result):
    return {s["instance"]: (s["surrogate"], s["chosen_by"], s["endpoint"]) for s in result.report["substitutions"]}


@pytest.fixture(scope="module")
def default(tmp_path_factory):
    """Cdh generated from that manifest"""
    return generate(MISSION, manifest_of(), tmp_path_factory.mktemp("subtop") / "out")


@pytest.fixture
def two_uarts(tmp_path):
    """A copy of the project with a second `uart`, in the deployment's own Top"""
    project = copy_flight(tmp_path, "project")
    top = project / "Cdh" / "Top"
    instances = (top / "instances.fpp").read_text()
    (top / "instances.fpp").write_text(instances.replace(
        "  # The serial sensor's driver is not here",
        "  instance uart: VxWorksDrv.VxWorksUartDriver base id 0x10023000\n\n  # The serial sensor's driver is not here"))
    topology = (top / "topology.fpp").read_text()
    (top / "topology.fpp").write_text(
        topology.replace("    instance vxTimer\n", "    instance vxTimer\n    instance uart\n")
        .replace("    connections Communications {",
                 "    connections SecondUart {\n      rateGroup1Hz.RateGroupMemberOut[5] -> uart.run\n    }\n\n"
                 "    connections Communications {"))
    return project


# ----------------------------------------------------------------------
# Naming instances
# ----------------------------------------------------------------------


def test_resolve_instance():
    names = ["Top.timer", "LibA.Sub.uart", "LibB.Sub.uart", "Top.gps"]
    assert resolve_instance("LibA.Sub.uart", names) == "LibA.Sub.uart"
    assert resolve_instance("gps", names) == "Top.gps"
    assert resolve_instance("missing", names) is None
    assert resolve_instance("art", names) is None, "only whole name segments match"
    with pytest.raises(WstsError, match="ambiguous.*LibA.Sub.uart, LibB.Sub.uart"):
        resolve_instance("uart", names)
    with pytest.raises(WstsError, match="ambiguous"):
        resolve_instance("Sub.uart", names)


def test_system_link_takes_a_qualified_instance():
    system = parse_system({
        "system": "S", "elements": {"a": "A/wsts.yaml", "b": "B/wsts.yaml"},
        "links": [{"name": "wire", "a": "a.SensorLink.uart", "b": "b.uart"}],
    })
    assert system.links[0].a == ("a", "SensorLink.uart") and system.links[0].b == ("b", "uart")


# ----------------------------------------------------------------------
# Plan
# ----------------------------------------------------------------------


@needs_fpp
def test_subtopology_instance_is_substituted_from_the_catalog(default):
    assert chosen(default) == {
        "Cdh.vxTimer": ("Wsts.SimTick", "catalog", "vxTimer"),
        UART: ("Wsts.SimByteStream", "catalog", "uart"),
    }


@needs_fpp
@pytest.mark.parametrize("name", [UART, "uart"])
def test_subtopology_instance_named_in_the_manifest(tmp_path, name):
    models = [{**CDH["models"][0], "endpoint": "sensorPort"}]
    manifest = manifest_of(
        substitute=[{"instance": name, "with": "Wsts.SimByteStream", "endpoint": "sensorPort"}], models=models)
    result = generate(MISSION, manifest, tmp_path / "out")
    assert chosen(result)[UART] == ("Wsts.SimByteStream", "manifest", "sensorPort")
    assert '::SensorLink::uart.wstsBind("sensorPort", "");' in (result.output / "Cdh/Top/WstsSetup.cpp").read_text()


@needs_fpp
def test_keep_with_the_qualified_name(tmp_path):
    result = generate(MISSION, manifest_of(keep=["comDriver", UART], models=None), tmp_path / "out")
    assert UART not in chosen(result)
    assert result.report["kept_hardware_instances"] == [UART]
    assert "replaced_modules" not in result.report
    assert not (result.output / "SensorLib").exists(), "nothing to patch: the library builds as it is"
    assert "FPRIME_INCLUDE_FRAMEWORK_CODE" not in (result.output / "CMakeLists.txt").read_text()


@needs_fpp
def test_link_ends_at_a_subtopology_instance(tmp_path):
    manifest = manifest_of(models=None)
    manifest.system, manifest.element = "Pair", "a"
    manifest.links = [LinkEnd(link="wire", instance=UART, role="listen", peer="b.uart")]
    result = generate(MISSION, manifest, tmp_path / "out")
    assert result.report["links"] == [
        {"link": "wire", "instance": UART, "endpoint": "uart", "role": "listen", "peer": "b.uart"}]


@needs_fpp
def test_ambiguous_base_name_is_an_error(two_uarts, tmp_path):
    with pytest.raises(WstsError, match=r"'uart' is ambiguous.*Cdh\.uart, SensorLink\.uart"):
        generate(two_uarts, manifest_of(keep=["comDriver", "uart"]), tmp_path / "out")


@needs_fpp
def test_two_instances_with_one_base_name_need_endpoints(two_uarts, tmp_path):
    with pytest.raises(WstsError, match=r"(?s)endpoint 'uart' is used by both .*endpoint: <name>"):
        generate(two_uarts, manifest_of(), tmp_path / "out")
    manifest = manifest_of(substitute=[
        {"instance": "Cdh.uart", "with": "Wsts.SimByteStream", "endpoint": "spare"},
    ])
    result = generate(two_uarts, manifest, tmp_path / "out")
    assert {name: values[2] for name, values in chosen(result).items()} == {
        "Cdh.vxTimer": "vxTimer", "Cdh.uart": "spare", UART: "uart"}
    removed = result.report["dictionary"]["removed"]["events"]
    assert "SensorLink.uart.OpenError" in removed and "Cdh.uart.OpenError" in removed


# ----------------------------------------------------------------------
# Output
# ----------------------------------------------------------------------


@needs_fpp
def test_patched_subtopology_takes_the_place_of_the_original(default):
    out = default.output
    patched = (out / "SensorLib" / "SensorLink" / "SensorLink.fpp").read_text()
    original = (MISSION / "lib/sensor-lib/SensorLib/SensorLink/SensorLink.fpp").read_text()
    assert "instance uart: Wsts.SimByteStream base id SensorLinkConfig.BASE_ID + 0x1000" in patched
    assert patched == original.replace("VxWorksDrv.VxWorksUartDriver base id", "Wsts.SimByteStream base id"), \
        "only the component type changes: the phase code still names the flight driver"
    # The baud rate is a field of the subtopology's state, of the flight driver's type
    assert "VxWorksDrv::VxWorksUartDriver::NO_FLOW" in patched and "state.sensorLink.baud" in patched
    assert original == (MISSION / "lib/sensor-lib/SensorLib/SensorLink/SensorLink.fpp").read_text()

    module = (out / "SensorLib" / "SensorLink" / "CMakeLists.txt").read_text()
    assert '"${CMAKE_CURRENT_LIST_DIR}/SensorLink.fpp"' in module
    assert '"${WSTS_ORIGINAL_DIR}/SubtopologyTopologyDefs.hpp"' in module, "headers stay in the library"
    assert 'add_fprime_subdirectory("${WSTS_ORIGINAL_DIR}/SensorLinkConfig/")' in module
    assert "VxWorks_Drv_VxWorksUartDriver" not in module, "the flight driver's module leaves the build"
    assert "SensorLib_SerialSensor" in module and "EXCLUDE_FROM_ALL" in module and "INTERFACE" in module

    root = (out / "CMakeLists.txt").read_text()
    assert "lib/sensor-lib/SensorLib/SensorLink\"" in root and "set(FPRIME_INCLUDE_FRAMEWORK_CODE OFF)" in root
    assert root.index("function(add_fprime_subdirectory") < root.index("fprime_setup_included_code()")
    # The library itself is still a library of the overlay: its other modules and its
    # include root come from it
    assert "lib/sensor-lib" in (out / "settings.ini").read_text()
    (module_report,) = default.report["replaced_modules"]
    assert module_report["original"].endswith("lib/sensor-lib/SensorLib/SensorLink")
    assert {k: v for k, v in module_report.items() if k != "original"} == {
        "module": "SensorLib_SensorLink", "copy": "SensorLib/SensorLink", "files": ["SensorLink.fpp"]}

    compat = (out / "Cdh" / "Top" / "WstsCompat.hpp").read_text()
    assert "namespace VxWorksDrv { using VxWorksUartDriver = ::Wsts::SimByteStream; }" in compat
    assert (out / "Cdh" / "Top" / "system.fpp").read_text() == (MISSION / "Cdh/Top/system.fpp").read_text()


@needs_fpp
def test_original_subtopology_is_not_in_the_patched_model(default, tmp_path):
    """The model the overlay builds from passes fpp-check with the copy and without the original"""
    out = default.output
    settings = fpp.read_settings(MISSION)
    roots = [settings.framework_path, *settings.library_locations, MISSION, REPO / "Wsts"]
    overrides, _ = fpp.find_config_overrides(roots)
    original = MISSION / "lib/sensor-lib/SensorLib/SensorLink/SensorLink.fpp"
    top = sorted((out / "Cdh" / "Top").glob("*.fpp"))
    candidates = [c for c in fpp.select_candidates(fpp.find_fpp_files(roots), settings.framework_path, overrides)
                  if MISSION / "Cdh" not in c.parents]
    copy = out / "SensorLib" / "SensorLink" / "SensorLink.fpp"
    fpp.locate_defs(tmp_path, [c for c in candidates if c != original] + [copy])
    closure = fpp.depend(tmp_path, top)
    assert copy in closure and original not in closure
    fpp.check(tmp_path, closure + top)
    # With both, every definition of the subtopology is seen twice
    fpp.locate_defs(tmp_path, candidates + [copy])
    with pytest.raises(WstsError, match="inconsistent location"):
        fpp.depend(tmp_path, top)


@needs_fpp
def test_report_lists_the_qualified_instance(default):
    report = default.report
    removed = report["dictionary"]["removed"]
    assert "SensorLink.uart.OpenError" in removed["events"] and "SensorLink.uart.BytesSent" in removed["channels"]
    text = (default.output / "wsts-report.md").read_text()
    assert "| `SensorLink.uart` | `VxWorksDrv.VxWorksUartDriver` | `Wsts.SimByteStream` | catalog | `uart` |" in text
    assert "## Modules built from a patched copy" in text and "`SensorLib_SensorLink`" in text
    # sensor-lib because the model uses it; fprime-vxworks only because the project overrides
    # a configuration header it supplies, which the report says
    assert any(library.endswith("lib/sensor-lib") for library in report["libraries"]["kept"])
    assert any("fprime-vxworks" in library for library in report["libraries"]["kept"])
    assert [entry["library"].rsplit("/", 1)[-1] for entry in report["libraries"]["kept_for_configuration"]] == ["fprime-vxworks"]


# ----------------------------------------------------------------------
# Configuration overrides
# ----------------------------------------------------------------------


def test_find_config_overrides_last_registration_wins(tmp_path):
    framework, library, project = tmp_path / "fw", tmp_path / "lib", tmp_path / "proj"
    for directory in (framework / "default/config", framework / "Svc/Sub/SubConfig", library / "cfg", project / "cfg"):
        directory.mkdir(parents=True)
    for file in (framework / "default/config/AcConstants.fpp", framework / "default/config/FpConfig.fpp",
                 framework / "Svc/Sub/SubConfig/SubConfig.fpp", library / "cfg/AcConstants.fpp",
                 project / "cfg/AcConstants.fpp", project / "cfg/SubConfig.fpp", project / "cfg/Extra.fpp"):
        file.write_text("")
    (framework / "default/config/CMakeLists.txt").write_text(
        'register_fprime_config(AUTOCODER_INPUTS "${CMAKE_CURRENT_LIST_DIR}/AcConstants.fpp"\n'
        '  "${CMAKE_CURRENT_LIST_DIR}/FpConfig.fpp" HEADERS FpConfig.h GLOBAL_IMPLICIT_DEPENDENCY)\n')
    (framework / "Svc/Sub/SubConfig/CMakeLists.txt").write_text(
        "register_fprime_config(\n  EXCLUDE_FROM_ALL\n  AUTOCODER_INPUTS\n    SubConfig.fpp  # relative\n  INTERFACE\n)\n")
    (library / "library.cmake").write_text(
        '# a second call in the same file is read too\n'
        'register_fprime_config(LibOther HEADERS "${CMAKE_CURRENT_LIST_DIR}/cfg/Other.hpp" INTERFACE)\n'
        'register_fprime_config(LibConfig CONFIGURATION_OVERRIDES "${CMAKE_CURRENT_LIST_DIR}/cfg/AcConstants.fpp"\n'
        '  "${CMAKE_CURRENT_LIST_DIR}/cfg/FpConfig.h" INTERFACE)\n')
    (project / "cfg/CMakeLists.txt").write_text(
        'if (FPRIME_PLATFORM STREQUAL "VxWorks")\n'
        '  register_fprime_config(CONFIGURATION_OVERRIDES "${CMAKE_CURRENT_LIST_DIR}/AcConstants.fpp"\n'
        '    "${CMAKE_CURRENT_LIST_DIR}/SubConfig.fpp" "${SOME_DIR}/Unknown.fpp" INTERFACE)\n'
        'endif()\n')
    overrides, warnings = fpp.find_config_overrides([framework, library, project])
    assert warnings == []
    found = {o.name: (o.winner, o.replaced) for o in overrides}
    assert found == {
        "AcConstants.fpp": (project / "cfg/AcConstants.fpp",
                            sorted([framework / "default/config/AcConstants.fpp", library / "cfg/AcConstants.fpp"])),
        "SubConfig.fpp": (project / "cfg/SubConfig.fpp", [framework / "Svc/Sub/SubConfig/SubConfig.fpp"]),
    }
    files = fpp.find_fpp_files([framework, library, project])
    kept = fpp.select_candidates(files, framework, overrides)
    assert sorted(kept) == sorted([
        framework / "default/config/FpConfig.fpp", project / "cfg/AcConstants.fpp", project / "cfg/SubConfig.fpp",
        project / "cfg/Extra.fpp"])


def test_default_config_convention_still_applies_without_overrides(tmp_path):
    framework, project = tmp_path / "fw", tmp_path / "proj"
    (framework / "default/config").mkdir(parents=True)
    (project / "config").mkdir(parents=True)
    default, own = framework / "default/config/AcConstants.fpp", project / "config/AcConstants.fpp"
    default.write_text("")
    own.write_text("")
    assert fpp.select_candidates([default, own], framework) == [own]


@needs_fpp
def test_project_overrides_reach_the_analysis(default):
    report = default.report
    found = {o["file"]: (o["uses"], o["replaces"]) for o in report["config_overrides"]}
    assert sorted(found) == ["AcConstants.fpp", "CdhCoreConfig.fpp", "SensorLinkConfig.fpp"]
    uses, replaces = found["CdhCoreConfig.fpp"]
    assert uses.endswith("mission/config/project/CdhCoreConfig.fpp") or uses == "../../config/project/CdhCoreConfig.fpp"
    assert len(replaces) == 1 and replaces[0].endswith("lib/fprime/Svc/Subtopologies/CdhCore/CdhCoreConfig/CdhCoreConfig.fpp")
    uses, replaces = found["SensorLinkConfig.fpp"]
    assert uses.endswith("config/project/SensorLinkConfig.fpp")
    assert len(replaces) == 1 and replaces[0].endswith("SensorLib/SensorLink/SensorLinkConfig/SensorLinkConfig.fpp")
    uart = next(s for s in report["substitutions"] if s["instance"] == UART)
    assert uart["base_id"] == 0x06001000, "the library default would give 0x30001000"
    assert "## Configuration overrides applied" in (default.output / "wsts-report.md").read_text()
    assert "config/project" in (default.output / "CMakeLists.txt").read_text(), "the build applies them itself"


# ----------------------------------------------------------------------
# Tick sources
# ----------------------------------------------------------------------


@needs_fpp
def test_kept_tick_source_is_reported(tmp_path):
    result = generate(MISSION, manifest_of(keep=["comDriver", "vxTimer"]), tmp_path / "out")
    warnings = [w for w in result.report["warnings"] if "cycles the flight rate groups" in w]
    assert len(warnings) == 1 and warnings[0].startswith("Cdh.vxTimer (VxWorksSvc.VxWatchDogTimer)")
    assert "Cdh.rateGroupDriver" in warnings[0] and "project surrogate" in warnings[0]
    assert any("rate groups may never run" in note for note in result.report["fidelity_notes"])


@needs_fpp
def test_no_tick_warning_for_the_examples(default, tmp_path):
    from fprime_wsts.system import generate_system, load_system

    reports = [default.report]
    reports.append(generate(MISSION, flight_manifest("Payload"), tmp_path / "payload").report)
    system = generate_system(load_system(SYSTEM), tmp_path / "system")
    reports += [element.report for element in system.elements.values()]
    for report in reports:
        assert not [w for w in report["warnings"] if "rate groups" in w or "time comes from" in w], report["topology"]


def _model(tmp_path, timer_component: str, time_component: str):
    def component(name, ports, file):
        made = fpp.Component(name, "passive", file)
        made.ports = {p.name: p for p in ports}
        return made

    framework, project = tmp_path / "fw", tmp_path / "proj"
    cycle_out = fpp.Port("CycleOut", "Output", "Svc.Cycle")
    cycle_in = fpp.Port("CycleIn", "SyncInput", "Svc.Cycle")
    components = {
        "Svc.RateGroupDriver": component("Svc.RateGroupDriver", [cycle_in, cycle_out], framework / "Svc/RGD/RGD.fpp"),
        "Svc.ActiveRateGroup": component("Svc.ActiveRateGroup", [cycle_in], framework / "Svc/ARG/ARG.fpp"),
        "Svc.LinuxTimer": component("Svc.LinuxTimer", [cycle_out], framework / "Svc/LinuxTimer/LinuxTimer.fpp"),
        "Svc.ChronoTime": component("Svc.ChronoTime", [], framework / "Svc/ChronoTime/ChronoTime.fpp"),
        "Fc.TimeUnit": component("Fc.TimeUnit", [cycle_out], project / "Fc/TimeUnit/TimeUnit.fpp"),
    }
    instances = {
        name: fpp.Instance(name, kind, 0, 0, project / "Top/instances.fpp", 1, 1, 1, 1, kind)
        for name, kind in (("Fc.timer", timer_component), ("Fc.clock", time_component),
                           ("Fc.rgDriver", "Svc.RateGroupDriver"), ("Fc.rg1", "Svc.ActiveRateGroup"))
    }
    topology = fpp.Topology("Fc.Fc", project / "Top/topology.fpp", 1, 1, instances=list(instances), time_source="Fc.clock")
    topology.connections = [
        fpp.Connection("RateGroups", "Fc.timer", "CycleOut", "Fc.rgDriver", "CycleIn"),
        fpp.Connection("RateGroups", "Fc.rgDriver", "CycleOut", "Fc.rg1", "CycleIn"),
    ]
    return fpp.Model(components, instances, {topology.name: topology}, []), topology, framework


def test_project_time_unit_that_drives_the_rate_groups(tmp_path):
    # One project driver is both the time source and the rate group tick, as on a flight
    # computer whose timer interrupt comes through a register interface
    model, topology, framework = _model(tmp_path, "Fc.TimeUnit", "Fc.TimeUnit")
    plan = Plan(topology)
    _check_tick_sources(plan, model, framework)
    assert len(plan.warnings) == 2
    assert plan.warnings[0].startswith("Fc.timer (Fc.TimeUnit) cycles the flight rate groups (it drives Fc.rgDriver)")
    assert plan.warnings[1].startswith("time comes from Fc.clock (Fc.TimeUnit)")
    assert len(plan.fidelity_notes) == 2


def test_framework_timer_and_time_are_fine(tmp_path):
    model, topology, framework = _model(tmp_path, "Svc.LinuxTimer", "Svc.ChronoTime")
    plan = Plan(topology)
    _check_tick_sources(plan, model, framework)
    assert plan.warnings == [] and plan.fidelity_notes == []


# ----------------------------------------------------------------------
# Tool versions
# ----------------------------------------------------------------------


def test_fpp_version_mismatch_is_reported(tmp_path, monkeypatch):
    (tmp_path / "requirements.txt").write_text("fprime-gds==4.4.0\nfprime-fpp==3.4.0\n")
    assert fpp.pinned_fpp_version(tmp_path) == "3.4.0"
    monkeypatch.setattr(fpp, "installed_fpp_version", lambda: "3.2.0")
    warning = fpp.check_tool_version(tmp_path)
    assert "3.2.0" in warning and "fprime-fpp==3.4.0" in warning
    with pytest.raises(WstsError) as error:
        fpp._run("fpp-check", ["no-such-file.fpp"], tmp_path)
    assert "version 3.2.0" in str(error.value) and "fprime-fpp==3.4.0" in str(error.value)
    monkeypatch.setattr(fpp, "installed_fpp_version", lambda: "3.4.0")
    assert fpp.check_tool_version(tmp_path) is None
    with pytest.raises(WstsError) as error:
        fpp._run("fpp-check", ["no-such-file.fpp"], tmp_path)
    assert "pins" not in str(error.value)


@needs_fpp
def test_installed_fpp_version_is_read():
    assert fpp.installed_fpp_version() == fpp.pinned_fpp_version(REPO / "lib" / "fprime")


def test_java_requirement_is_explained(tmp_path, monkeypatch):
    tool = tmp_path / "fpp-check"
    tool.write_text("#!/bin/sh\necho \"[ERROR] fpp-check requires Java 25 or later, but 'java' is Java 17.\" >&2\nexit 1\n")
    tool.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path), prepend=":")
    monkeypatch.setenv("JAVA_HOME", "/opt/old-java")
    monkeypatch.setattr(fpp, "_version_hint", "")
    with pytest.raises(WstsError, match="needs Java 25 or later.*JAVA_HOME"):
        fpp._run("fpp-check", [], tmp_path)


# ----------------------------------------------------------------------
# Build file of a replaced module
# ----------------------------------------------------------------------


def test_replaced_module_build_file(tmp_path):
    from fprime_wsts.generate import ReplacedModule, render_replaced_cmake

    original = tmp_path / "lib" / "Lib" / "Sub"
    (original / "SubConfig").mkdir(parents=True)
    for name in ("Sub.fpp", "instances.fpp", "Defs.hpp"):
        (original / name).write_text("")
    (original / "CMakeLists.txt").write_text(
        "# The radio link subtopology\n"
        "restrict_platforms(vxworks)\n"
        "add_fprime_subdirectory(SubConfig)\n"
        "register_fprime_module(\n"
        "    EXCLUDE_FROM_ALL\n"
        "    AUTOCODER_INPUTS\n"
        '        "${CMAKE_CURRENT_LIST_DIR}/Sub.fpp"  # topology (see Defs.hpp)\n'
        "        instances.fpp\n"
        "    HEADERS\n"
        "        ${CMAKE_CURRENT_SOURCE_DIR}/Defs.hpp\n"
        "    INTERFACE\n"
        "    DEPENDS\n"
        "        Lib_Sub_SubConfig\n"
        "        VxWorks_Drv_VxWorksUartDriver\n"
        "        ${EXTRA_DEPENDS}\n"
        ")\n")
    module = ReplacedModule(original, tmp_path / "lib", "Lib/Sub", [original / "instances.fpp"])
    text = render_replaced_cmake(module, "../../../lib/Lib/Sub", {"VxWorks_Drv_VxWorksUartDriver"})
    assert 'set(WSTS_ORIGINAL_DIR "${CMAKE_CURRENT_LIST_DIR}/../../../lib/Lib/Sub")' in text
    assert "restrict_platforms" not in text, "the copy is for the host"
    assert 'add_fprime_subdirectory("${WSTS_ORIGINAL_DIR}/SubConfig")' in text
    assert '"${WSTS_ORIGINAL_DIR}/Sub.fpp"\n' in text, "a file that is not patched stays the original's"
    assert '        "${CMAKE_CURRENT_LIST_DIR}/instances.fpp"\n' in text, "the patched file is the copy"
    assert "${WSTS_ORIGINAL_DIR}/Defs.hpp" in text
    assert "VxWorksUartDriver" not in text and "Lib_Sub_SubConfig\n        ${EXTRA_DEPENDS}" in text
    assert "radio link" not in text and "see Defs" not in text

    module.files = [original / "Other.fpp"]
    with pytest.raises(WstsError, match="does not list Other.fpp"):
        render_replaced_cmake(module, "../x", set())


@needs_fpp
def test_subtopology_inside_the_flight_project(tmp_path):
    """The same mechanism when the subtopology is a project module, not a library's"""
    project = tmp_path / "project"
    shutil.copytree(MISSION, project, ignore=shutil.ignore_patterns("wsts", "lib"))
    shutil.copytree(MISSION / "lib" / "sensor-lib" / "SensorLib", project / "SensorLib")
    (project / "settings.ini").write_text(
        "[fprime]\n"
        f"framework_path: {REPO / 'lib' / 'fprime'}\n"
        "project_root: .\n"
        f"library_locations: {REPO / 'lib' / 'fprime-vxworks'}\n"
        "default_toolchain: vxworks\n"
    )
    result = generate(project, manifest_of(), tmp_path / "out")
    assert chosen(result)[UART] == ("Wsts.SimByteStream", "catalog", "uart")
    (module,) = result.report["replaced_modules"]
    assert module["module"] == "SensorLib_SensorLink" and module["copy"] == "SensorLib/SensorLink"
    assert "Wsts.SimByteStream" in (result.output / "SensorLib" / "SensorLink" / "SensorLink.fpp").read_text()
    added = [line for line in (result.output / "CMakeLists.txt").read_text().splitlines()
             if line.startswith("add_fprime_subdirectory(")]
    # The project module is still added from the flight project, and redirected to the
    # copy; the module that overrides its configuration comes after it
    names = [line.split("/")[-1].rstrip('")') for line in added]
    assert sorted(names) == ["Cdh", "SensorLink", "SerialSensor", "project"]
    assert names.index("project") > names.index("SensorLink")
    assert 'add_fprime_subdirectory("${WSTS_FLIGHT_PROJECT}/SensorLib/SensorLink")' in added
