"""What has moved under a simulation project since it was generated (drift.py): reports
compared by area, the flight project's commits, and generation that fails after a change"""

import json
import subprocess

from fprime_wsts.drift import diff_reports, drift, find_manifest, render

from mission import SYSTEM, copy_flight, copy_sim, needs_fpp

import pytest


def report(**changes):
    base = {
        "topology": "Dep.Dep",
        "substitutions": [{"instance": "Dep.i2cBus", "flight_component": "Drv.LinuxI2cDriver",
                           "surrogate": "Wsts.SimI2c", "chosen_by": "catalog", "endpoint": "i2cBus",
                           "endpoint_kind": "i2c"}],
        "models": [{"name": "imu", "class": "Wsts::RegisterMapI2c", "endpoint": "i2cBus", "kind": "i2c",
                    "address": 0x68}],
        "dictionary": {"removed": {"events": ["Dep.i2cBus.OpenError"]}, "flight_ids_unchanged": True},
        "capacity": {"command_table": {"used": 31, "size": 80}},
        "project_modules": ["Components/Imu"],
    }
    base.update(changes)
    return base


def by_area(changes):
    return {(c["area"], c["change"], c["subject"]): c for c in changes}


def test_identical_reports_have_no_changes():
    assert diff_reports(report(), report()) == []


def test_a_new_driver_is_a_substitution_and_an_endpoint_that_nothing_answers():
    new = report()
    new["substitutions"] = new["substitutions"] + [
        {"instance": "Dep.ledPin", "flight_component": "Drv.LinuxGpioDriver", "surrogate": "Wsts.SimGpio",
         "chosen_by": "catalog", "endpoint": "ledPin", "endpoint_kind": "gpio"}]
    new["project_modules"] = ["Components/Imu", "Components/Led"]
    new["dictionary"] = {"removed": {"events": ["Dep.i2cBus.OpenError", "Dep.ledPin.OpenChip"]},
                         "flight_ids_unchanged": True}
    new["capacity"] = {"command_table": {"used": 70, "size": 80}}
    changes = by_area(diff_reports(report(), new))
    assert "does endpoint 'ledPin' need a model?" in changes[("substitutions", "added", "Dep.ledPin")]["decide"]
    assert "nothing answering" in changes[("endpoints", "added", "ledPin")]["detail"]
    assert "reaches hardware" in changes[("project modules", "added", "Components/Led")]["decide"]
    assert ("dictionary: events missing", "added", "Dep.ledPin.OpenChip") in changes
    assert changes[("capacity", "changed", "command_table")]["decide"].startswith("nearly full")


def test_a_removed_instance_says_what_to_drop_from_the_manifest():
    changes = by_area(diff_reports(report(), report(substitutions=[], models=[])))
    assert "drop what the manifest says" in changes[("substitutions", "removed", "Dep.i2cBus")]["decide"]
    assert ("models", "removed", "imu") in changes and ("endpoints", "removed", "i2cBus") in changes


def test_a_renamed_instance_is_one_change_and_the_rest_follows_its_new_name():
    renamed = json.loads(json.dumps(report()).replace("i2cBus", "sensorBus"))
    changes = diff_reports(report(), renamed)
    assert [(c["area"], c["change"], c["subject"], c["detail"]) for c in changes] == \
        [("substitutions", "renamed", "Dep.i2cBus", "-> Dep.sensorBus")]
    assert "name sensorBus in the manifest" in changes[0]["decide"]
    # Two of a kind gone and two new: which is which is not known, so no rename is claimed
    two = report(substitutions=report()["substitutions"] * 2)
    two["substitutions"][1] = {**two["substitutions"][1], "instance": "Dep.i2cBus2", "endpoint": "i2cBus2"}
    other = json.loads(json.dumps(two).replace("i2cBus", "busX"))
    assert not any(c["change"] == "renamed" for c in diff_reports(two, other))


def test_an_instance_that_is_gone_is_looked_for_under_another_name(tmp_path):
    from fprime_wsts.drift import suggest_instances

    (tmp_path / "Top").mkdir()
    (tmp_path / "Top" / "instances.fpp").write_text("instance i2cBus2: Drv.LinuxI2cDriver base id 0x100\n"
                                                    "instance other: Svc.RateGroupDriver base id 0x200\n")
    error = "fprime-wsts: error: manifest names instance 'i2cBus', which is not in topology Dep.Dep"
    hints = suggest_instances(error, {"Dep": report()}, [tmp_path])
    assert len(hints) == 1 and "flight now defines i2cBus2" in hints[0]
    (tmp_path / "Top" / "instances.fpp").write_text("instance other: Svc.RateGroupDriver base id 0x200\n")
    assert "flight defines no new instance" in suggest_instances(error, {"Dep": report()}, [tmp_path])[0]


def test_the_manifest_of_a_simulation_project_is_found(tmp_path):
    assert find_manifest(SYSTEM.parent) == SYSTEM
    (tmp_path / "a.yaml").write_text("deployment: A\n")
    assert find_manifest(tmp_path) == tmp_path / "a.yaml"
    (tmp_path / "b.yaml").write_text("deployment: B\n")
    with pytest.raises(Exception, match="several manifests"):
        find_manifest(tmp_path)


def git(directory, *args):
    subprocess.run(["git", "-C", str(directory), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
                   check=True, capture_output=True)


@needs_fpp
def test_drift_after_flight_changes(tmp_path):
    """Up to date after generating; a new GPIO pin in flight shows as a substitution and an
    endpoint without a model, with the flight commit; a renamed instance the manifest names
    makes generation fail with the generator's message"""
    from fprime_wsts.system import generate_system, load_system

    flight = copy_flight(tmp_path, "mission")
    sim = copy_sim(tmp_path, "mission-sim")
    for name in ("cdh.yaml", "payload.yaml"):  # the copy of the flight project beside it
        path = sim / name
        text = path.read_text()
        start = text.index("\nproject: ") + 1
        path.write_text(text[:start] + "project: ../mission" + text[text.index("\n", start):])
    generate_system(load_system(sim / "wsts-system.yaml"))
    for directory in (flight, sim):
        git(directory, "init", "-q")
        git(directory, "add", "-A")
        git(directory, "commit", "-qm", "start")

    result = drift(sim)
    assert result["generation"]["generates"] and result["generation"]["up_to_date"]
    assert all(not e["changes"] for e in result["elements"].values())
    assert result["flight"][0]["commits_since_generated"] == 0

    top = flight / "Payload" / "Top"
    instances = (top / "instances.fpp").read_text()
    (top / "instances.fpp").write_text(instances.replace(
        "  instance spiBus:", "  instance ledPin: Drv.LinuxGpioDriver base id 0x20038000\n\n  instance spiBus:", 1))
    topology = (top / "topology.fpp").read_text()
    (top / "topology.fpp").write_text(topology.replace("    instance switchPin\n",
                                                       "    instance switchPin\n    instance ledPin\n", 1))
    git(flight, "commit", "-qam", "Add the status LED")
    result = drift(sim)
    assert result["generation"]["generates"] and not result["generation"]["up_to_date"]
    changes = by_area(result["elements"]["payload"]["changes"])
    assert ("substitutions", "added", "Payload.ledPin") in changes
    assert ("endpoints", "added", "ledPin") in changes
    assert not result["elements"]["cdh"]["changes"]
    assert [c.split(" ", 2)[2] for c in result["flight"][0]["commits"]] == ["Add the status LED"]
    text = render(result)
    assert "Generating again would change" in text and "| endpoints | added | `ledPin` |" in text

    for name in ("instances.fpp", "topology.fpp", "PayloadTopology.cpp"):
        path = top / name
        path.write_text(path.read_text().replace("fpgaRegs", "fpgaBlock"))
    result = drift(sim)
    assert not result["generation"]["generates"]
    assert "fpgaRegs" in result["generation"]["error"]
    assert "flight now defines fpgaBlock" in result["generation"]["hints"][0]
    # Renamed in the manifest too: one rename, under which the rest compares as before
    manifest = sim / "payload.yaml"
    manifest.write_text(manifest.read_text().replace("fpgaRegs", "fpgaBlock"))
    changes = by_area(drift(sim)["elements"]["payload"]["changes"])
    assert changes[("substitutions", "renamed", "Payload.fpgaRegs")]["detail"] == "-> Payload.fpgaBlock"
    assert not [key for key in changes if key[0] in ("register blocks", "models") or key[0].startswith("dictionary:")]
    assert "**Generation fails now:**" in render(result)
    assert json.dumps(result)  # all of it is JSON
