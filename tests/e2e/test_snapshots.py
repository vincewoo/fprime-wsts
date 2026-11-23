"""Snapshots (docs/snapshots.md): runs in stepped time that keep a journal, snapshotted between
two ticks, and runs restored from them, checked record by record against what was snapshotted.

Every run here is a process of its own on free ports: the system of the session runs in real
time without a journal."""

import json
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from fprime_wsts import snapshots
from fprime_wsts.bridge import WstsBridge, WstsSystem
from fprime_wsts.runner import read_run_file

T = 60


def start(command, log_dir):
    """`fprime-wsts run ...` in the background; returns the process and the run file once it is up"""
    process = subprocess.Popen([sys.executable, "-m", "fprime_wsts", "run", *command, "--auto-ports",
                                "--log-dir", str(log_dir)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    deadline = time.monotonic() + T
    while (found := read_run_file(log_dir)) is None:
        assert process.poll() is None and time.monotonic() < deadline, process.stderr.read() if process.poll() else ""
        time.sleep(0.1)
    return process, found


def stop(process):
    process.send_signal(signal.SIGINT)
    process.communicate(timeout=60)


@pytest.fixture
def payload_dir(request):
    return Path(request.config.getoption("--wsts-system")) / "payload"


@pytest.fixture
def payload_snapshot(payload_dir, tmp_path):
    """A journaled Payload run that a client drove (pauses, steps, a fault, injected bytes),
    snapshotted while it ran on, then stopped"""
    process, run = start([str(payload_dir), "--stepped", "--journal"], tmp_path / "original")
    try:
        with WstsBridge(run["bridge"], request_timeout=T) as sim:
            sim.pause()
            sim.step(40)
            sim.fault_set("spw", "drop", direction="to_flight", count=2)
            sim.step(30)
            sim.inject_text("sensorUart", "$TEMP,95.5\n")
            sim.step(50)
            sim.resume()
            time.sleep(0.3)
        snapshot = snapshots.take(tmp_path / "original", tmp_path / "snapshot")
        with WstsBridge(run["bridge"]) as sim:
            status = sim.status()
            assert not status["paused"] and status["tick"] >= snapshot.tick, "it was running: it goes on"
    finally:
        stop(process)
    return snapshot


def test_a_deployment_is_restored_where_it_was_snapshotted(payload_snapshot, wsts_restore):
    assert payload_snapshot.tick > 120 and payload_snapshot.meta["records"] > 1000
    with wsts_restore(payload_snapshot) as sim:
        status = sim.status()
        assert status["paused"] and status["tick"] == payload_snapshot.tick, "held at the snapshot's tick"
        # It goes on from there, as any stepped run does
        sim.step(20)
        assert sim.status()["tick"] == payload_snapshot.tick + 20


def test_two_runs_branch_from_one_snapshot(payload_snapshot, wsts_restore, tmp_path):
    """The same state twice, at once: a fault in one branch and not in the other"""
    with wsts_restore(payload_snapshot) as calm, wsts_restore(payload_snapshot) as faulty:
        faulty.fault_set("i2cBus", "error", arg=3)
        for branch in (calm, faulty):
            branch.subscribe()
            branch.step(10)
        errors = [m for m in faulty.events() if m.get("rec") == "i2c" and m.get("status")]
        assert errors and not [m for m in calm.events() if m.get("rec") == "i2c" and m.get("status")]
        # Each keeps a journal of its own: a snapshot of a snapshot, restored in turn
        again = snapshots.take(calm.log_dir, tmp_path / "again")
        assert again.tick == payload_snapshot.tick + 10
    with wsts_restore(again) as sim:
        assert sim.status()["tick"] == again.tick


def test_a_restore_that_differs_names_where(payload_snapshot, wsts_restore, tmp_path):
    """A journal that does not hold everything flight software was given: the replay differs,
    and the check names the first record that does"""
    journal = payload_snapshot.journal
    lines = journal.read_text().splitlines()
    index = next(i for i, line in enumerate(lines) if '"rec":"bs_rx"' in line and '"src":"inject"' not in line
                 and '"sensorUart"' in line)
    removed = lines.pop(index)  # as if the sensor had said nothing then
    journal.write_text("\n".join(lines) + "\n")
    with pytest.raises(snapshots.RestoreMismatch) as error:
        with wsts_restore(payload_snapshot):
            pass
    # The first record that differs is the one taken out: the restored run has it, the journal not
    assert error.value.index == sum(1 for line in lines[:index] if '"rec"' in line)
    assert error.value.found == removed and "bus record" in str(error.value)


def test_another_build_is_refused(payload_snapshot, wsts_restore):
    meta = json.loads((payload_snapshot.path / snapshots.SNAPSHOT_META).read_text())
    meta["binary_sha256"] = "f" * 64
    (payload_snapshot.path / snapshots.SNAPSHOT_META).write_text(json.dumps(meta))
    with pytest.raises(snapshots.SnapshotError, match="another build"):
        with wsts_restore(payload_snapshot.path):
            pass


def test_a_system_is_restored_at_one_instant(request, wsts_restore, tmp_path):
    """The whole system in lockstep: both elements, and what the hub carries between them"""
    system_dir = Path(request.config.getoption("--wsts-system"))
    process, _ = start([str(system_dir), "--stepped", "--journal"], tmp_path / "original")
    try:
        time.sleep(2)
        subprocess.run([sys.executable, "-m", "fprime_wsts", "ctl", str(tmp_path / "original"), "--element", "payload",
                        "fault_set", "endpoint=spw", "kind=drop", "dir=to_flight", "count=3"], check=False)
        time.sleep(1)
        snapshot = snapshots.take(tmp_path / "original", tmp_path / "snapshot")
    finally:
        stop(process)
    assert snapshot.is_system and set(snapshot.elements) == {"cdh", "payload"}
    ticks = snapshot.meta["elements"]
    # Cdh at 10 Hz, Payload at 20 Hz: one instant
    assert abs(ticks["payload"] - 2 * ticks["cdh"]) <= 2
    with wsts_restore(snapshot) as system:
        assert isinstance(system, WstsSystem)
        statuses = system.status()
        assert {name: s["tick"] for name, s in statuses.items()} == ticks
        assert all(s["paused"] for s in statuses.values())
