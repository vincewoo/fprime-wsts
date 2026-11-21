"""The fidelity inventory of a generated deployment or system (fidelity.py): what answers on
each endpoint, and the gaps it finds, from the reports and optionally a run's traffic"""

import json

import pytest

from fprime_wsts.fidelity import NO_MODEL, inventory, render, traffic_from_summary
from fprime_wsts.fpp import WstsError

from mission import SIM

GENERATED = SIM / "wsts" / "Mission"


def endpoints(inv, element):
    return {e["endpoint"]: e for e in inv["elements"][element]["endpoints"]}


def test_every_endpoint_of_the_example_says_what_answers_it():
    inv = inventory(GENERATED)
    payload = endpoints(inv, "payload")
    assert payload["hubDriver"]["fidelity"] == "link" and payload["hubDriver"]["served_by"][0]["peer"] == "cdh.hubDriver"
    assert payload["sensorUart"]["fidelity"] == "stock model"
    assert payload["fpgaRegs"]["kind"] == "register" and payload["fpgaRegs"]["flight_drivers"] == ["Payload.fpga"]
    # Two devices on one bus: the power monitor's simulator component and the IMU model
    i2c = {s["fidelity"] for s in payload["i2cBus"]["served_by"]}
    assert i2c == {"simulator component", "project model"}
    imu = next(s for s in payload["i2cBus"]["served_by"] if s.get("class") == "Sim::ImuModel")
    assert imu["address"] == "0x68" and "Models/ImuModel.hpp" in imu["source"]
    assert payload["heaterPin"]["fidelity"] == "nothing" and payload["heaterPin"]["flight_sees"] == NO_MODEL["gpio"]
    assert "linuxTimer" not in payload, "a tick source is not a device"
    assert endpoints(inv, "cdh")["uart"]["fidelity"] == "simulator component"


def test_the_gaps_of_the_example_are_ranked_and_say_what_to_do():
    inv = inventory(GENERATED)
    gaps = inv["gaps"]
    assert [g["severity"] for g in gaps] == sorted((g["severity"] for g in gaps), key=["high", "medium", "low"].index)
    by_subject = {(g["element"], g["subject"]): g for g in gaps}
    assert by_subject[("payload", "Components/Imager")]["area"] == "build"
    assert by_subject[("payload", "i2cBus @0x68")]["finding"].startswith("model Sim::ImuModel has not been checked")
    assert by_subject[("payload", "sensorUart")]["severity"] == "low"
    assert by_subject[("payload", "replaced drivers")]["area"] == "dictionary"
    assert all(g["next_step"] for g in gaps)
    text = render(inv)
    assert "| `fpgaRegs` | register | Wsts::RegisterFile | stock model |" in text and "## Gaps" in text


def write_element(tmp_path, report):
    out = tmp_path / "sim" / "wsts" / "Dep"
    out.mkdir(parents=True)
    (out / "wsts-report.json").write_text(json.dumps({"simulation_project": "../..", **report}))
    return out


def test_a_skeleton_and_a_checked_model_are_told_apart(tmp_path):
    out = write_element(tmp_path, {"topology": "Dep.Dep", "models": [
        {"name": "baro", "class": "Sim::BaroModel", "endpoint": "i2cBus", "kind": "i2c", "address": 0x76},
        {"name": "gps", "class": "Sim::GpsModel", "endpoint": "gpsUart", "kind": "bytestream", "address": 0}]})
    models = tmp_path / "sim" / "Models"
    models.mkdir()
    (models / "BaroModel.hpp").write_text("class BaroModel : public Wsts::I2cDevice {};  // TODO: the registers\n")
    (models / "GpsModel.hpp").write_text("namespace Sim { class GpsModel {}; }\n")
    captures = tmp_path / "sim" / "devices" / "gps" / "captures"
    captures.mkdir(parents=True)
    (captures / "2026-11-01-nmea.jsonl").write_text("{}\n")
    (captures.parent / "spec.md").write_text("# GPS\n")
    inv = inventory(out)
    eps = endpoints(inv, "Dep")
    assert eps["i2cBus"]["fidelity"] == "project model (skeleton: has TODOs)"
    gps = eps["gpsUart"]["served_by"][0]
    assert gps["fidelity"] == "project model" and gps["evidence"] == {"device_dir": "devices/gps",
                                                                       "spec": "devices/gps/spec.md", "captures": 1}
    subjects = {g["subject"]: g for g in inv["gaps"]}
    assert subjects["i2cBus @0x76"]["severity"] == "medium" and "gpsUart" not in subjects


def test_traffic_names_what_flight_used_and_nothing_answered(tmp_path):
    out = write_element(tmp_path, {
        "substitutions": [
            {"instance": "Dep.i2cBus", "flight_component": "Drv.LinuxI2cDriver", "surrogate": "Wsts.SimI2c",
             "chosen_by": "catalog", "endpoint": "i2cBus", "endpoint_kind": "i2c"},
            {"instance": "Dep.spiBus", "flight_component": "Drv.LinuxSpiDriver", "surrogate": "Wsts.SimSpi",
             "chosen_by": "catalog", "endpoint": "spiBus", "endpoint_kind": "spi"}],
        "models": [{"name": "imu", "class": "Wsts::RegisterMapI2c", "endpoint": "i2cBus", "kind": "i2c",
                    "address": 0x68}]})
    summary = tmp_path / "summary.json"
    summary.write_text(json.dumps({"deployment": "Dep", "logs": str(out / "wsts-logs"), "final_status": {"bus_traffic": {
        "i2cBus": {"transactions": 40, "errors": 20, "unanswered": 20,
                   "addresses": {"0x68": {"transactions": 20}, "0x1e": {"transactions": 20, "unanswered": 20}}},
        "spiBus": {"transactions": 0}}}}))
    inv = inventory(out, traffic_from_summary(summary))
    assert inv["with_traffic"]
    gaps = {(g["subject"], g["severity"]): g for g in inv["gaps"]}
    unanswered = gaps[("i2cBus", "high")]
    assert "at 0x1e went unanswered" in unanswered["finding"]
    assert "did not use it in the run" in gaps[("spiBus", "low")]["finding"]
    imu = endpoints(inv, "Dep")["i2cBus"]["served_by"][0]
    assert imu["transactions_in_run"] == 20


def test_a_device_flight_software_never_addressed_is_named(tmp_path):
    out = write_element(tmp_path, {"models": [
        {"name": "baro", "class": "Wsts::RegisterMapI2c", "endpoint": "i2cBus", "kind": "i2c", "address": 0x76}]})
    traffic = {"Dep": {"i2cBus": {"transactions": 5, "addresses": {"0x68": {"transactions": 5}}}}}
    gap = next(g for g in inventory(out, traffic)["gaps"] if g["subject"] == "i2cBus @0x76")
    assert gap["finding"].endswith("flight software did not address it in the run")


def test_a_directory_that_was_not_generated_is_refused(tmp_path):
    with pytest.raises(WstsError, match="not a generated simulation deployment or system"):
        inventory(tmp_path)
