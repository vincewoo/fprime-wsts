"""`fprime-wsts survey` and `fprime-wsts init` on the example's flight project"""

import json
from pathlib import Path

from fprime_wsts.cli import main
from fprime_wsts.manifest import load_manifest
from fprime_wsts.system import load_system

from mission import MISSION, needs_fpp


@needs_fpp
def test_init_writes_a_system_that_loads(tmp_path, capsys):
    sim = tmp_path / "mission-sim"
    assert main(["init", str(MISSION), "--output", str(sim), "--no-trial"]) == 0
    printed = capsys.readouterr().out
    assert "Next:" in printed and f"fprime-wsts build {sim / 'wsts-system.yaml'}" in printed
    assert sorted(p.name for p in sim.iterdir()) == [".gitignore", "cdh.yaml", "payload.yaml", "wsts-system.yaml"]
    system = load_system(sim / "wsts-system.yaml")
    assert system.name == "Mission" and system.ground == "cdh" and {e.name for e in system.elements} == {"cdh", "payload"}
    assert (system.links[0].a, system.links[0].b) == (("cdh", "hubDriver"), ("payload", "hubDriver"))
    payload = load_manifest(sim / "payload.yaml")
    assert payload.project_root() == MISSION.resolve() and payload.deployment == "Payload"
    assert [(s.instance, s.surrogate) for s in payload.substitutions] == [("Payload.fpgaRegs", "Wsts.SimRegister")]
    # A second run changes nothing: what the user has edited since is theirs
    (sim / "payload.yaml").write_text("deployment: Payload\n")
    assert main(["init", str(MISSION), "--output", str(sim), "--no-trial"]) == 0
    assert "Nothing written" in capsys.readouterr().out and (sim / "payload.yaml").read_text() == "deployment: Payload\n"


@needs_fpp
def test_init_of_one_deployment_and_survey(tmp_path, capsys):
    sim = tmp_path / "sim"
    assert main(["init", str(MISSION), "--output", str(sim), "--deployment", "Payload", "--no-trial", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert [Path(p).name for p in report["starters_written"]] == ["wsts.yaml", ".gitignore"]
    manifest = load_manifest(sim / "wsts.yaml")
    assert manifest.bridge == "tcp:50100" and manifest.deployment == "Payload"
    # The survey alone writes nothing, and reads a project inside the toolkit's checkout when asked to
    assert main(["survey", str(MISSION), "--deployment", "Cdh", "--no-trial", "--json"]) == 0
    (project,) = json.loads(capsys.readouterr().out)["projects"]
    (cdh,) = project["deployments"]
    assert cdh["name"] == "Cdh" and not cdh["has_main"]


def test_init_refuses_what_is_not_a_project_and_the_flight_project_itself(tmp_path, capsys):
    assert main(["init", str(tmp_path)]) == 2
    assert "is not an F Prime project root" in capsys.readouterr().err
    assert main(["init", str(MISSION), "--output", str(MISSION / "sim")]) == 2
    assert "only reads" in capsys.readouterr().err
    assert main(["survey", str(tmp_path)]) == 1


@needs_fpp
def test_the_survey_says_whether_stepped_time_will_work(capsys):
    """From the trial generation, before a manifest is written or anything built"""
    assert main(["survey", str(MISSION), "--deployment", "Payload", "--json"]) == 0
    (project,) = json.loads(capsys.readouterr().out)["projects"]
    (payload,) = project["deployments"]
    waiting = payload["trial"]["waits_on_host"]
    # The hub driver is kept by a manifest of one deployment: a socket driver, known to wait on it
    assert [(i["instances"], i["components"], i["effect"]) for i in waiting] == [
        (["Payload.hubDriver"], ["Drv.TcpClient"], "not_repeatable")]
    assert main(["survey", str(MISSION), "--deployment", "Payload"]) == 0
    printed = capsys.readouterr().out
    assert "- stepped time: One module of this deployment does something that waits on the host" in printed
    assert "  - `Payload.hubDriver` (Drv.TcpClient): a socket driver" in printed
