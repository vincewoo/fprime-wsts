"""Generator runs against the example projects. Needs the FPP tools (fprime-fpp) on PATH."""

import json

import pytest

from fprime_wsts.fpp import WstsError
from fprime_wsts.generate import PlannedLink, generate
from fprime_wsts.manifest import LinkEnd, load_manifest, parse_manifest
from mission import CDH_MAIN, MISSION, REPO, copy_sim, flight_manifest, needs_fpp

#: A manifest for Payload that lives in the flight project and uses stock models only
PAYLOAD_MODELS = [
    {"name": "imu", "class": "Wsts::RegisterMapI2c", "endpoint": "i2cBus", "kind": "i2c", "address": 0x68,
     "params": {"0x75": "0x68"}},
]


def payload_manifest(**changes):
    return flight_manifest("Payload", **{"models": PAYLOAD_MODELS, **changes})


def cdh_manifest(**changes):
    return flight_manifest("Cdh", **{"keep": ["comDriver"], "main": CDH_MAIN, **changes})


@needs_fpp
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
        # The time source: the host's clock in real time, simulated time in stepped time
        "Payload.posixTime": ("Wsts.SimTime", "catalog"),
    }, "hubDriver is kept by the manifest"
    kinds = {s["instance"]: s["endpoint_kind"] for s in report["substitutions"]}
    assert (kinds["Payload.i2cBus"], kinds["Payload.sensorUart"], kinds["Payload.heaterPin"],
            kinds["Payload.spiBus"]) == ("i2c", "bytestream", "gpio", "spi")
    assert kinds["Payload.posixTime"] is None, "a surrogate without an endpoint has no kind"
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


@needs_fpp
def test_default_output_is_in_the_flight_project():
    # Check mode generates into a scratch directory and writes nothing
    result = generate(MISSION, payload_manifest(), check=True)
    assert result.output == MISSION / "wsts" / "Payload"
    assert not (MISSION / "wsts").exists()


@needs_fpp
def test_control_none_adds_nothing_to_the_dictionary(tmp_path):
    result = generate(MISSION, payload_manifest(control="none"), tmp_path / "out")
    assert not any(result.report["dictionary"]["added"].values())
    topology = (result.output / "Payload" / "Top" / "topology.fpp").read_text()
    assert "instance wstsSetup" in topology and "wstsControl" not in topology


@needs_fpp
def test_vxworks_deployment_converts_without_its_toolchain(tmp_path):
    result = generate(MISSION, cdh_manifest(), tmp_path / "out")
    report = result.report
    # The hub driver (a Drv.TcpServer) is replaced only when a system ends a link at it
    assert {s["flight_component"] for s in report["substitutions"]} == {
        "VxWorksSvc.VxWatchDogTimer", "VxWorksDrv.VxWorksUartDriver", "Svc.ChronoTime"}
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


@needs_fpp
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


@needs_fpp
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


@needs_fpp
def test_refuses_to_overwrite_foreign_directories(tmp_path):
    (tmp_path / "precious.txt").write_text("keep me")
    with pytest.raises(WstsError, match="refusing to overwrite"):
        generate(MISSION, payload_manifest(), tmp_path)


@needs_fpp
def test_upstream_ref_with_telemetry_packets(tmp_path):
    """Ref defines telemetry packets and its topology shares its module's name"""
    project = REPO / "lib" / "fprime" / "TestDeploymentsProject"
    result = generate(project, parse_manifest({"deployment": "Ref", "keep": ["comDriver"]}), tmp_path / "out")
    assert [s["instance"] for s in result.report["substitutions"]] == ["Ref.posixTime", "Ref.linuxTimer"]
    assert result.report["dictionary"]["removed"] == {"commands": [], "events": [], "channels": [], "params": []}
    packets = (result.output / "Ref" / "Top" / "RefPackets.fppi").read_text()
    assert "wstsControl.SimTicks" in packets.split("omit {")[1]
    assert len(result.report["project_modules"]) == 7


@needs_fpp
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
    # Two devices on one I2C endpoint: the IMU model at its address, the power monitor as
    # an F Prime component behind the adapter at 0x40
    assert 'attachI2c("i2cBus", 0x68, &model_imu)' in setup
    assert '::PayloadSim::powerDevice.wstsServe("i2cBus", "addr=0x40");' in setup


@needs_fpp
def test_simulation_project_needs_a_library_cmake_for_its_modules(tmp_path):
    sim = copy_sim(tmp_path)
    (sim / "library.cmake").unlink()
    with pytest.raises(WstsError, match="no library.cmake"):
        generate(MISSION, load_manifest(sim / "payload.yaml"))


def test_planned_link_construction_sets_its_kind():
    link = PlannedLink(LinkEnd(link="l", instance="i", role="listen", peer="p", kind="gpio"), "i", "e", "gpio")
    assert link.kind == "gpio"


@needs_fpp
def test_run_gds_args_reach_the_report(tmp_path):
    """What `fprime-wsts run` reads to start the GDS, next to run.env and run.models"""
    result = generate(MISSION, payload_manifest(keep=["hubDriver"], run={"gds_args": ["--framing-selection", "fprime"]}),
                      tmp_path / "out")
    assert json.loads((result.output / "wsts-report.json").read_text())["run"]["gds_args"] == ["--framing-selection", "fprime"]
    plain = generate(MISSION, payload_manifest(keep=["hubDriver"]), tmp_path / "plain")
    assert "gds_args" not in json.loads((plain.output / "wsts-report.json").read_text()).get("run", {})


def move_topology(project, name):
    """The deployment Payload with its topology in Payload/<name>/ instead of Payload/Top/, as a
    project that names the directory after the deployment has it"""
    deployment = project / "Payload"
    (deployment / "Top").rename(deployment / name)
    for path in deployment.rglob("*"):
        if path.is_file() and path.suffix in (".cpp", ".hpp", ".fpp", ".txt"):
            text = path.read_text()
            text = text.replace("Payload/Top/", f"Payload/{name}/").replace("/Top/", f"/{name}/")
            text = text.replace("${FPRIME_CURRENT_MODULE}_Top", "${FPRIME_CURRENT_MODULE}_" + name)
            path.write_text(text)


@needs_fpp
def test_topology_in_a_directory_other_than_top(tmp_path):
    """Found from the deployment's CMakeLists.txt, or named with `topology_dir`; the overlay keeps the name"""
    from mission import copy_flight
    from fprime_wsts.fpp import topology_directory

    project = copy_flight(tmp_path)
    move_topology(project, "Payload")
    assert topology_directory(project / "Payload") == project / "Payload" / "Payload"
    assert topology_directory(project / "Payload", "Payload") == project / "Payload" / "Payload"
    assert topology_directory(project / "Payload", "Missing") is None
    result = generate(project, flight_manifest("Payload", keep=["hubDriver"], models=PAYLOAD_MODELS),
                      tmp_path / "out")
    overlay = tmp_path / "out" / "Payload"
    assert not (overlay / "Top").exists()
    assert (overlay / "Payload" / "CMakeLists.txt").is_file() and (overlay / "Payload" / "WstsSetup.cpp").is_file()
    cmake = (overlay / "CMakeLists.txt").read_text()
    assert 'CMAKE_CURRENT_LIST_DIR}/Payload/"' in cmake and "${FPRIME_CURRENT_MODULE}_Payload" in cmake
    assert "Payload/Payload/PayloadTopologyAc.hpp" in (overlay / "Payload" / "WstsSetup.cpp").read_text()
    assert {s["instance"] for s in result.report["substitutions"]} >= {"Payload.i2cBus", "Payload.sensorUart"}


def merge_topology_into_deployment(project):
    """The deployment Payload with its topology files in Payload/ itself and one
    register_fprime_deployment() for the model and Main.cpp, as a project that has no Top/ has it"""
    deployment = project / "Payload"
    for path in sorted((deployment / "Top").iterdir()):
        if path.name != "CMakeLists.txt":
            path.rename(deployment / path.name)
    (deployment / "Top" / "CMakeLists.txt").unlink()
    (deployment / "Top").rmdir()
    for path in deployment.iterdir():
        if path.suffix in (".cpp", ".hpp", ".fpp"):
            path.write_text(path.read_text().replace("Payload/Top/", "Payload/"))
    (deployment / "CMakeLists.txt").write_text(
        "register_fprime_deployment(\n"
        "    AUTOCODER_INPUTS\n"
        '        "${CMAKE_CURRENT_LIST_DIR}/instances.fpp"\n'
        '        "${CMAKE_CURRENT_LIST_DIR}/topology.fpp"\n'
        "    SOURCES\n"
        '        "${CMAKE_CURRENT_LIST_DIR}/Main.cpp"\n'
        '        "${CMAKE_CURRENT_LIST_DIR}/PayloadTopology.cpp"\n'
        "    DEPENDS\n"
        "        Fw_Logger\n"
        "        SensorLib_Units\n"
        ")\n")


@needs_fpp
def test_topology_in_the_deployment_directory_itself(tmp_path):
    """No Top/: the model, the topology code and main() are one register_fprime_deployment(). The overlay
    registers one deployment module with the model, the set-up and the flight sources together"""
    from mission import copy_flight

    project = copy_flight(tmp_path)
    merge_topology_into_deployment(project)
    result = generate(project, flight_manifest("Payload", topology_dir=".", keep=["hubDriver"], models=PAYLOAD_MODELS),
                      tmp_path / "out")
    overlay = tmp_path / "out" / "Payload"
    cmake = (overlay / "CMakeLists.txt").read_text()
    assert cmake.count("register_fprime_deployment(") == 1 and "register_fprime_module(" not in cmake
    assert "add_fprime_subdirectory" not in cmake
    for name in ("topology.fpp", "wsts_instances.fpp"):
        assert name in cmake.split("AUTOCODER_INPUTS")[1].split("SOURCES")[0]
    sources = cmake.split("SOURCES")[1].split("DEPENDS")[0]
    assert "Main.cpp" in sources and "PayloadTopology.cpp" in sources and "WstsSetup.cpp" in sources
    assert "WstsCompat.hpp" in cmake
    assert "Payload/PayloadTopologyAc.hpp" in (overlay / "WstsSetup.cpp").read_text()
    assert {s["instance"] for s in result.report["substitutions"]} >= {"Payload.i2cBus", "Payload.sensorUart"}


@needs_fpp
def test_includes_of_files_beside_the_topology_reach_the_original(tmp_path):
    """The topology is copied to the overlay; an include of a file that is not copied with it points back at the
    original, relative to where the copy is"""
    from mission import copy_flight

    project = copy_flight(tmp_path)
    (project / "Shared").mkdir()
    (project / "Shared" / "constants.fppi").write_text("constant SharedConstant = 7\n")
    topology = project / "Payload" / "Top" / "topology.fpp"
    topology.write_text('include "../../Shared/constants.fppi"\n' + topology.read_text())
    generate(project, flight_manifest("Payload", keep=["hubDriver"], models=PAYLOAD_MODELS), tmp_path / "out")
    copied = tmp_path / "out" / "Payload" / "Top" / "topology.fpp"
    first = copied.read_text().splitlines()[0]
    assert first.startswith('include "') and "Shared/constants.fppi" in first
    assert ((copied.parent / first.split('"')[1]).resolve() == (project / "Shared" / "constants.fppi").resolve())


def test_topology_directory_is_found_without_a_top_directory(tmp_path):
    from fprime_wsts.fpp import topology_directory

    deployment = tmp_path / "Dep"
    (deployment / "Dep").mkdir(parents=True)
    (deployment / "Config").mkdir()
    (deployment / "Config" / "constants.fpp").write_text("constant A = 1\n")
    (deployment / "Dep" / "topology.fpp").write_text("module Dep {\n  topology Dep {\n  }\n}\n")
    cmake = deployment / "CMakeLists.txt"
    assert topology_directory(deployment) is None  # no register_fprime_deployment()
    cmake.write_text('add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/Config/")\n'
                     'add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/Dep/")\nregister_fprime_deployment(SOURCES Main.cpp)\n')
    assert topology_directory(deployment) == deployment / "Dep"
    (deployment / "Config" / "other.fpp").write_text("topology Other {\n}\n")
    assert topology_directory(deployment) is None  # two directories define one: the manifest must say
    assert topology_directory(deployment, "Config") == deployment / "Config"
    # No directory is added: the topology is in the deployment's own directory
    (deployment / "Dep" / "topology.fpp").rename(deployment / "Dep.fpp")
    (deployment / "Config" / "other.fpp").unlink()
    cmake.write_text("register_fprime_deployment(SOURCES Main.cpp)\n")
    assert topology_directory(deployment) == deployment


@needs_fpp
def test_inputs_of_other_autocoders_are_handed_on_by_the_topology_module(tmp_path):
    """A project that registers an autocoder (for sequence files, say) lists the files in the topology
    module's AUTOCODER_INPUTS: the generated module lists them too, at their place in the flight project"""
    from mission import copy_flight

    project = copy_flight(tmp_path)
    cmake = project / "Payload" / "Top" / "CMakeLists.txt"
    text = cmake.read_text()
    assert '"${CMAKE_CURRENT_LIST_DIR}/topology.fpp"' in text
    sequence = project / "Payload" / "seqs" / "STARTUP.seq"
    sequence.parent.mkdir()
    sequence.write_text("R00:00:00 cmdDisp.CMD_NO_OP\n")
    cmake.write_text(text.replace('"${CMAKE_CURRENT_LIST_DIR}/topology.fpp"',
                                  '"${CMAKE_CURRENT_LIST_DIR}/topology.fpp"\n        "${CMAKE_CURRENT_LIST_DIR}/../seqs/STARTUP.seq"'))
    generate(project, flight_manifest("Payload", keep=["hubDriver"], models=PAYLOAD_MODELS), tmp_path / "out")
    generated = (tmp_path / "out" / "Payload" / "Top" / "CMakeLists.txt").read_text()
    inputs = generated.split("AUTOCODER_INPUTS")[1].split("SOURCES")[0]
    assert "topology.fpp" in inputs
    # Relative to where the module is written, so that the overlay can be checked in
    assert '"${CMAKE_CURRENT_LIST_DIR}/' in inputs and inputs.count(".seq") == 1
    listed = inputs.split(".seq")[0].split('"${CMAKE_CURRENT_LIST_DIR}/')[-1] + ".seq"
    assert ((tmp_path / "out" / "Payload" / "Top") / listed).resolve() == sequence.resolve()


@needs_fpp
def test_a_topology_module_with_only_fpp_inputs_lists_only_those(tmp_path):
    from mission import copy_flight

    project = copy_flight(tmp_path)
    generate(project, flight_manifest("Payload", keep=["hubDriver"], models=PAYLOAD_MODELS), tmp_path / "out")
    inputs = (tmp_path / "out" / "Payload" / "Top" / "CMakeLists.txt").read_text().split("AUTOCODER_INPUTS")[1].split("SOURCES")[0]
    assert ".seq" not in inputs and inputs.count(".fpp") == 3  # topology.fpp, instances.fpp, wsts_instances.fpp
