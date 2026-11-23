"""42 as a plant (fprime_wsts.fortytwo, docs/dynamics.md phase 4): against a stand-in that
speaks 42's socket protocol (fortytwo/fake42), and against 42 itself when WSTS_42 names the
directory it was built in"""

import json
import math
import os
import shutil
from pathlib import Path

import pytest

from fprime_wsts.dynamics import parse_specs
from fprime_wsts.fortytwo import (FortyTwoError, FortyTwoPlant, component, parse_message, prepare_case,
                                  read_case)
from fprime_wsts.lockstep import Conductor, LockstepError
from test_dynamics import PlantElement, one_element_system

HERE = Path(__file__).resolve().parent / "fortytwo"
FAKE = str(HERE / "fake42")


@pytest.fixture
def case(tmp_path):
    shutil.copytree(HERE / "case", tmp_path / "case")
    return tmp_path / "case"


def edit(path, line, text):
    lines = path.read_text().splitlines()
    lines[line] = text
    path.write_text("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Cases and messages
# ---------------------------------------------------------------------------

def test_a_case_is_read_for_what_the_coupling_needs(case):
    read = read_case(case)
    assert (read.time_mode, read.dtsim, read.graphics, read.port) == ("FAST", 0.05, False, 10001)
    assert read.prefixes == ["SC[0].wn", "SC[0].qn"]


@pytest.mark.parametrize("file, line, text, message", [
    ("Inp_Sim.txt", 2, "REAL   ! Time Mode", "time mode REAL: .*FAST"),
    ("Inp_Sim.txt", 6, "TRUE   ! Graphics Front End?", "runs headless"),
    ("Inp_IPC.txt", 3, "TX     ! IPC Mode", "IPC 0 is TX: .*set the others OFF"),
    ("Inp_IPC.txt", 3, "OFF    ! IPC Mode", "one TXRX socket .* it has 0"),
    ("Inp_IPC.txt", 5, "CLIENT ! Socket Role", "not a SERVER"),
    ("Inp_IPC.txt", 7, "FALSE  ! Allow Blocking", "does not block"),
    ("Inp_IPC.txt", 1, "two    ! Number of Sockets", "not 42's Inp_IPC.txt"),
])
def test_what_a_coupled_case_must_be(case, file, line, text, message):
    edit(case / file, line, text)
    with pytest.raises(FortyTwoError, match=message):
        read_case(case)


def test_another_socket_must_be_off(case):
    text = (case / "Inp_IPC.txt").read_text().splitlines()
    block = text[2:12]
    block[1] = "TX                                      ! IPC Mode"
    text[1] = "2                                       ! Number of Sockets"
    (case / "Inp_IPC.txt").write_text("\n".join(text + block) + "\n")
    with pytest.raises(FortyTwoError, match="IPC 1 is TX: .*OFF"):
        read_case(case)


def test_42s_messages_are_read_by_name_and_component():
    seconds, values = parse_message("TIME 2014-172-00:01:02.500000000\n"
                                    "SC[0].qn = [1.0e-01 2.0e-01 3.0e-01 9.2e-01]\n"
                                    "SC[0].Whl[1].H = -2.5e-03\nnot a line\n")
    assert seconds == pytest.approx(((172 * 24) * 60 + 1) * 60 + 2.5)
    assert component(values, "SC[0].qn[2]") == 0.3 and component(values, "SC[0].Whl[1].H") == -2.5e-3
    assert component(values, "SC[0].qn[4]") is None and component(values, "SC[0].wn[0]") is None


def test_a_case_of_42_is_prepared_for_the_coupling(case, tmp_path):
    edit(case / "Inp_Sim.txt", 2, "REAL                            !  Time Mode")
    edit(case / "Inp_Sim.txt", 6, "TRUE                            !  Graphics Front End?")
    prepared = prepare_case(case, tmp_path / "coupled", prefixes=["SC[0].svb", "SC[0].bvb"], step=0.1)
    assert (prepared.time_mode, prepared.dtsim, prepared.graphics) == ("FAST", 0.1, False)
    assert prepared.prefixes == ["SC[0].svb", "SC[0].bvb"]
    with pytest.raises(FortyTwoError, match="exists already"):
        prepare_case(case, tmp_path / "coupled")


# ---------------------------------------------------------------------------
# The plant, against the stand-in
# ---------------------------------------------------------------------------

def plant(case, tmp_path, inputs=("SC[0].B[0].wn[2]",), outputs=("SC[0].wn[2]", "SC[0].qn[2]"), **kwargs):
    return FortyTwoPlant(case, FAKE, name="att", inputs=list(inputs), outputs=list(outputs),
                         log=tmp_path / "logs" / "dynamics-att.log", **kwargs)


def test_42_is_started_on_a_copy_of_its_case_and_stepped(case, tmp_path):
    (tmp_path / "logs").mkdir()
    att = plant(case, tmp_path)
    att.start(0.0)
    try:
        first = att.step(0.0, 0.05, {"SC[0].B[0].wn[2]": 0.2})
        later = [att.step(0.05 * i, 0.1, {"SC[0].B[0].wn[2]": 0.2}) for i in range(1, 11)]
    finally:
        att.stop()
    # The input acts over the step it is given for, as in 42: what comes back has turned by it
    assert first == {"SC[0].wn[2]": 0.2, "SC[0].qn[2]": pytest.approx(math.sin(0.2 * 0.05 / 2), abs=1e-12)}
    assert att.steps == 1 + 1 + 20, "its first step, before any input; then one per 0.05 s"
    assert later[-1]["SC[0].qn[2]"] == pytest.approx(math.sin(0.2 * 0.05 * 21 / 2), abs=1e-12)
    copied = tmp_path / "logs" / "dynamics-att-case"
    assert "Echo to stdout" in (copied / "Inp_IPC.txt").read_text().splitlines()[8]
    assert (copied / "Inp_IPC.txt").read_text().splitlines()[8].startswith("FALSE")
    assert "fake 42:" in (tmp_path / "logs" / "dynamics-att.log").read_text()


def test_an_input_keeps_the_components_42_sent(case, tmp_path):
    att = plant(case, tmp_path, inputs=("SC[0].wn[0]",), outputs=("SC[0].wn[0]",))
    att.start(0.0)
    try:
        att._send({"SC[0].wn[0]": 1.5})
    finally:
        att.stop()
    # SC[0].wn is in 42's message, so its other components are those it sent; the stand-in
    # does not read SC[0].wn, and nothing changes: what was sent is what counts here
    assert att.last["SC[0].wn"] == [0.0, 0.0, 0.0]


@pytest.mark.parametrize("env, message", [
    ({"FAKE42_SKEW": "3"}, "42's time moved 0.100000000 s in one step, not its step of 0.05 s"),
    ({"FAKE42_EXIT": "4"}, "42 closed its socket"),
])
def test_42_not_keeping_step_stops_the_plant(case, tmp_path, monkeypatch, env, message):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    att = plant(case, tmp_path)
    att.start(0.0)
    try:
        with pytest.raises(FortyTwoError, match=message):
            for i in range(5):
                att.step(0.05 * i, 0.05, {"SC[0].B[0].wn[2]": 0.0})
    finally:
        att.stop()


def test_what_the_plant_refuses(case, tmp_path, monkeypatch):
    with pytest.raises(FortyTwoError, match="does not send SC\\[0\\].svb\\[0\\] .*prefixes are SC\\[0\\].wn, SC\\[0\\].qn"):
        att = plant(case, tmp_path, outputs=("SC[0].svb[0]",))
        try:
            att.start(0.0)
        finally:
            att.stop()
    att = plant(case, tmp_path)
    att.start(0.0)
    try:
        with pytest.raises(FortyTwoError, match="not a whole number of 42's steps of 0.05 s"):
            att.step(0.0, 0.07, {})
    finally:
        att.stop()
    with pytest.raises(FortyTwoError, match="parameters from its case's files"):
        plant(case, tmp_path, params={"C": 1})
    monkeypatch.delenv("FORTYTWO", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(FortyTwoError, match="give fortytwo.executable"):
        FortyTwoPlant(case, None)


def test_the_conductor_steps_42_between_instants(case, tmp_path):
    (tmp_path / "logs").mkdir()
    payload = PlantElement("payload", 50_000_000)
    payload.models[("imu", "gz")] = "0.5"
    [spec] = parse_specs([{"name": "att", "fortytwo": {"case": str(case), "executable": FAKE}, "step": 0.1,
                           "inputs": {"SC[0].B[0].wn[2]": {"model": "imu", "key": "gz", "scale": 0.1}},
                           "outputs": {"SC[0].qn[2]": {"model": "fpga", "key": "reg.0x0C", "scale": 1e6,
                                                       "round": True}}}], "wsts.yaml")
    description = {**one_element_system(payload), "directory": str(tmp_path), "log_dir": str(tmp_path / "logs")}
    with Conductor(description, request_timeout=5, dynamics=[spec]) as conductor:
        conductor.run(instants=20)
        state = conductor.dynamics("att")
    assert state["time"] == pytest.approx(1.0) and state["inputs"] == {"SC[0].B[0].wn[2]": 0.05}
    assert state["outputs"]["SC[0].qn[2]"] == pytest.approx(math.sin(0.05 * 1.0 / 2), abs=1e-12), \
        "the rate read at 0 s acts over every step from there: 1 s of it at 1 s"
    assert [action["value"] for _, batch in payload.batches for action in batch][-1] == \
        str(int(math.floor(state["outputs"]["SC[0].qn[2]"] * 1e6 + 0.5)))


def test_a_manifest_names_42_by_its_case(tmp_path, case):
    [spec] = parse_specs([{"name": "att", "fortytwo": "sim42/case",
                           "outputs": {"SC[0].qn[3]": {"model": "m", "key": "k"}}}], "wsts.yaml")
    assert (spec["kind"], spec["path"], spec["executable"]) == ("fortytwo", "sim42/case", None)
    from fprime_wsts.dynamics import DynamicsError, relocate

    with pytest.raises(DynamicsError, match="is not a case of 42"):
        relocate([spec], lambda path: tmp_path / path, str)
    [spec] = parse_specs([{"name": "att", "fortytwo": {"case": "case", "executable": "../bin/42"},
                           "outputs": {"SC[0].qn[3]": {"model": "m", "key": "k"}}}], "wsts.yaml")
    [moved] = relocate([spec], lambda path: (tmp_path / path).resolve(),
                       lambda path: os.path.relpath(path, tmp_path / "out" / "gen"))
    assert moved["path"] == "../../case" and moved["executable"] == "../../../bin/42"
    with pytest.raises(DynamicsError, match="unknown key"):
        parse_specs([{"name": "att", "fortytwo": {"case": "c", "binary": "42"}, "outputs": {"x": {"gpio": "p"}}}], "w")


# ---------------------------------------------------------------------------
# 42 itself
# ---------------------------------------------------------------------------

REAL = os.environ.get("WSTS_42")


@pytest.mark.skipif(not REAL, reason="WSTS_42 names the directory 42 was built in (its executable and its Tx case)")
def test_42_itself_turns_at_the_rate_it_is_given_and_runs_the_same_twice(tmp_path):
    home = Path(REAL)
    prepared = prepare_case(home / "Tx", tmp_path / "case", prefixes=["SC[0].qn", "SC[0].wn"], step=0.05)
    sc = (prepared.path / "SC_Simple.txt")
    sc.write_text(sc.read_text().replace("INSTANT_FSW", "PASSIVE_FSW", 1))
    runs = []
    for run in range(2):
        (tmp_path / f"logs{run}").mkdir()
        att = FortyTwoPlant(prepared.path, str(home / "42"), name="att", inputs=["SC[0].B[0].wn[0]"],
                            outputs=["SC[0].wn[0]", "SC[0].qn[0]", "SC[0].qn[3]"],
                            log=tmp_path / f"logs{run}" / "dynamics-att.log")
        att.start(0.0)
        try:
            runs.append([att.step(0.1 * i, 0.1, {"SC[0].B[0].wn[0]": 0.02 if i < 100 else 0.0}) for i in range(200)])
        finally:
            att.stop()
    assert json.dumps(runs[0]) == json.dumps(runs[1]), "42 gives the same numbers twice"
    assert runs[0][0]["SC[0].wn[0]"] == 0.02 and runs[0][-1]["SC[0].wn[0]"] == 0.0
    assert runs[0][99]["SC[0].qn[3]"] != runs[0][0]["SC[0].qn[3]"], "it turned while it had the rate"
    assert runs[0][-1] == runs[0][150], "and stood still once the rate was zero"
