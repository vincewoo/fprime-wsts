"""FMI co-simulation FMUs as plants (fprime_wsts.fmi, docs/dynamics.md): the example's thermal
plant as an FMI 2.0 FMU and the same model as an FMI 3.0 one, built here from C"""

import shutil
import zipfile
from pathlib import Path

import pytest

from fprime_wsts.dynamics import load_python, parse_specs
from fprime_wsts.fmi import FmuError, FmuPlant, build_fmu, read_model_description
from fprime_wsts.lockstep import Conductor, LockstepError
from fprime_wsts.manifest import TOOLKIT_ROOT
from test_dynamics import PlantElement, one_element_system

PLANTS = TOOLKIT_ROOT / "examples" / "mission-sim" / "plants"
HERE = Path(__file__).resolve().parent

pytestmark = pytest.mark.skipif(not (shutil.which("cc") or shutil.which("gcc")), reason="needs a C compiler")


@pytest.fixture(scope="module")
def fmus(tmp_path_factory):
    out = tmp_path_factory.mktemp("fmus")
    return {
        "2.0": build_fmu([PLANTS / "thermal_fmu" / "thermal.c"], PLANTS / "thermal_fmu" / "modelDescription.xml",
                         out / "Thermal.fmu"),
        "3.0": build_fmu([HERE / "fmus" / "thermal3.c"], HERE / "fmus" / "thermal3.xml", out / "Thermal3.fmu"),
    }


def heating(plant, steps=1200):
    """Heat for 33 s from 2 s on, then cool, a step of 50 ms at a time"""
    found = []
    for i in range(steps):
        found.append(plant.step(i * 0.05, 0.05, {"heater": 1.0 if 40 <= i < 700 else 0.0})["T"])
    return found


@pytest.mark.parametrize("version", ["2.0", "3.0"])
def test_an_fmu_computes_what_the_python_plant_does(fmus, version):
    plant = FmuPlant(fmus[version], {"C": 5.0, "T0": 21.5}, inputs=["heater"], outputs=["T"])
    plant.start(0.0)
    try:
        from_fmu = heating(plant)
    finally:
        plant.stop()
    from_python = heating(load_python(PLANTS / "thermal.py", {"C": 5.0}))
    assert from_fmu == from_python, "the same arithmetic in the same order: the same bits"
    assert max(from_fmu) > 45.0 and from_fmu[-1] < max(from_fmu)


def test_the_model_descriptions_are_read(fmus):
    with zipfile.ZipFile(fmus["2.0"]) as archive:
        two = read_model_description(archive.read("modelDescription.xml").decode())
        assert "binaries/linux64/thermal.so" in archive.namelist() or "binaries/darwin64/thermal.dylib" in \
            archive.namelist()
    assert (two.version, two.identifier, two.can_serialize_state) == ("2.0", "thermal", True)
    assert two.of("input") == ["heater"] and two.of("output") == ["T"]
    assert two.variables["C"].causality == "parameter" and two.variables["C"].start == "5.0"
    with zipfile.ZipFile(fmus["3.0"]) as archive:
        three = read_model_description(archive.read("modelDescription.xml").decode())
    assert (three.version, three.identifier, three.can_save_state) == ("3.0", "thermal3", True)
    assert three.variables["steps"].type == "Int32" and three.variables["disabled"].type == "Boolean"


@pytest.mark.parametrize("text, message", [
    ('<fmiModelDescription fmiVersion="1.0"/>', "FMI 1.0: FMI 2.0 and 3.0 are supported"),
    ('<fmiModelDescription fmiVersion="2.0"><ModelExchange modelIdentifier="m"/></fmiModelDescription>',
     "not a co-simulation FMU \\(ModelExchange\\)"),
    ('<fmiModelDescription fmiVersion="3.0"><CoSimulation/></fmiModelDescription>', "no modelIdentifier"),
    ("<fmiModelDescription", "modelDescription.xml: "),
])
def test_what_is_not_a_co_simulation_fmu(text, message):
    with pytest.raises(FmuError, match=message):
        read_model_description(text)


def test_parameters_of_every_type_are_set_by_name(fmus):
    plant = FmuPlant(fmus["3.0"], {"disabled": True, "gain": 2.5, "P": 20.0}, inputs=["heater"],
                     outputs=["T", "steps"])
    plant.start(0.0)
    try:
        first = plant.step(0.0, 0.05, {"heater": 1.0})
        second = plant.step(0.05, 0.05, {"heater": 1.0})
    finally:
        plant.stop()
    assert first == {"T": 21.5, "steps": 1} and second["steps"] == 2, "disabled: the heater does nothing"


@pytest.mark.parametrize("params, inputs, outputs, message", [
    ({"Cap": 5.0}, ["heater"], ["T"], "has no parameter 'Cap' \\(parameters: C, P, G, T_env, T0, substep\\)"),
    ({}, ["power"], ["T"], "has no input 'power' \\(inputs: heater\\)"),
    ({}, ["heater"], ["temperature"], "has no output 'temperature' \\(outputs: T\\)"),
    ({}, ["T"], ["T"], "has no input 'T'"),
])
def test_names_the_fmu_does_not_have(fmus, params, inputs, outputs, message):
    with pytest.raises(FmuError, match=message):
        FmuPlant(fmus["2.0"], params, inputs=inputs, outputs=outputs)


def test_a_failure_of_the_fmu_is_reported_with_what_it_logged(fmus):
    plant = FmuPlant(fmus["3.0"], {"C": 0.0}, outputs=["T"])
    with pytest.raises(FmuError, match="fmi3ExitInitializationMode returned Error .*C must be positive"):
        plant.start(0.0)
    plant.stop()
    melting = FmuPlant(fmus["3.0"], {"P": 1e6}, inputs=["heater"], outputs=["T"])
    melting.start(0.0)
    with pytest.raises(FmuError, match="asked to end the simulation at 0.05 s"):
        melting.step(0.0, 0.05, {"heater": 1.0})
    melting.stop()


def test_an_fmu_without_a_library_for_this_machine(tmp_path, fmus):
    stripped = tmp_path / "Stripped.fmu"
    with zipfile.ZipFile(fmus["2.0"]) as source, zipfile.ZipFile(stripped, "w") as target:
        for name in source.namelist():
            if not name.startswith("binaries/"):
                target.writestr(name, source.read(name))
            else:
                target.writestr(name.replace("binaries/", "binaries/").rsplit("/", 2)[0] + "/win64/thermal.dll",
                                b"")
    with pytest.raises(FmuError, match="no library for this machine .*it has binaries/win64/thermal.dll"):
        FmuPlant(stripped)
    (tmp_path / "plain.fmu").write_text("not a zip")
    with pytest.raises(FmuError, match="not an FMU"):
        FmuPlant(tmp_path / "plain.fmu")


@pytest.mark.parametrize("version", ["2.0", "3.0"])
def test_a_saved_state_carries_on_where_it_was(fmus, version):
    first = FmuPlant(fmus[version], inputs=["heater"], outputs=["T"])
    first.start(0.0)
    for i in range(300):
        first.step(i * 0.05, 0.05, {"heater": 1.0})
    state = first.save_state()
    later = [first.step((300 + i) * 0.05, 0.05, {"heater": 0.0})["T"] for i in range(300)]
    first.stop()
    second = FmuPlant(fmus[version], inputs=["heater"], outputs=["T"])
    second.start(0.0)
    second.load_state(state)
    again = [second.step((300 + i) * 0.05, 0.05, {"heater": 0.0})["T"] for i in range(300)]
    second.stop()
    assert again == later


def test_building_reports_what_the_compiler_said(tmp_path):
    (tmp_path / "broken.c").write_text("int f( {")
    with pytest.raises(FmuError, match="building Broken.fmu failed:\n.*error"):
        build_fmu([tmp_path / "broken.c"], PLANTS / "thermal_fmu" / "modelDescription.xml", tmp_path / "Broken.fmu")


def test_the_conductor_steps_an_fmu_as_it_does_a_python_plant(tmp_path, fmus):
    shutil.copy(fmus["2.0"], tmp_path / "Thermal.fmu")
    written = {}
    for kind, path in (("fmu", "Thermal.fmu"), ("python", str(PLANTS / "thermal.py"))):
        payload = PlantElement("payload", 50_000_000)
        payload.pins["heaterPin"] = True
        [spec] = parse_specs([{"name": "thermal", kind: path, "params": {"P": 10.0},
                               "inputs": {"heater": {"gpio": "heaterPin"}},
                               "outputs": {"T": {"model": "fpga", "key": "reg.0x0C", "scale": 10, "round": True}}}],
                             "wsts.yaml")
        with Conductor({**one_element_system(payload), "directory": str(tmp_path)}, request_timeout=2,
                       dynamics=[spec]) as conductor:
            conductor.run(instants=40)
        written[kind] = payload.batches
    assert written["fmu"] == written["python"], "the same values, written at the same ticks"
    assert len(written["fmu"]) >= 35, "a tenth of a degree more nearly every instant"
    [last] = written["fmu"][-1][1]
    assert last["via"] == "thermal" and 240 < int(last["value"]) < 260


def test_an_fmu_input_that_is_not_a_number_stops_the_conductor(tmp_path, fmus):
    shutil.copy(fmus["2.0"], tmp_path / "Thermal.fmu")
    payload = PlantElement("payload", 50_000_000)
    [spec] = parse_specs([{"name": "thermal", "fmu": "Thermal.fmu", "inputs": {"heater": {"model": "tempSensor",
                                                                                          "key": "text"}},
                           "outputs": {"T": {"model": "fpga", "key": "reg.0x0C"}}}], "wsts.yaml")
    with Conductor({**one_element_system(payload), "directory": str(tmp_path)}, request_timeout=2,
                   dynamics=[spec]) as conductor:
        with pytest.raises(LockstepError, match="failed to step .*ValueError"):
            conductor.run(instants=1)
