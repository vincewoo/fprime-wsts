"""Snapshots (docs/snapshots.md): what can be checked without a running deployment. The
end-to-end tests take and restore real ones (tests/e2e/test_snapshots.py)."""

import json
from types import SimpleNamespace

import pytest

from fprime_wsts import snapshots
from fprime_wsts.fpp import WstsError
from fprime_wsts.runner import _run_cli


def write_snapshot(path, directory, kind="deployment", **meta):
    path.mkdir(parents=True)
    (path / snapshots.JOURNAL).write_text('{"rec":"i2c","tick":1,"endpoint":"i2cBus"}\n')
    (path / snapshots.SANDBOX).mkdir()
    (path / snapshots.SNAPSHOT_META).write_text(json.dumps({
        "format": snapshots.FORMAT, "kind": kind, "deployment": "Payload", "directory": str(directory),
        "binary_sha256": "0" * 64, "tick": 40, "tick_period_ns": 50_000_000, "sim_seconds": 2.0,
        "start_paused": False, "env": {}, "records": 1, "created": "2026-01-01T00:00:00Z", **meta}))
    return path


def test_the_first_record_that_differs_is_named():
    same = ['{"rec":"i2c","tick":1,"endpoint":"i2cBus"}', '{"rec":"spi","tick":2,"endpoint":"spiBus"}']
    snapshots.compare("Payload", same, list(same))
    with pytest.raises(snapshots.RestoreMismatch, match="at tick 2 on spiBus, bus record 1") as error:
        snapshots.compare("Payload", same, [same[0], '{"rec":"spi","tick":2,"endpoint":"spiBus","read":"00"}'])
    assert error.value.index == 1 and "a ground command" in str(error.value)
    with pytest.raises(snapshots.RestoreMismatch, match="stopped short"):
        snapshots.compare("Payload", same, same[:1])


def test_snapshots_are_loaded_and_found(tmp_path):
    first = write_snapshot(tmp_path / "a" / "boot", tmp_path / "dep", created="2026-01-01T00:00:00Z")
    write_snapshot(tmp_path / "b", tmp_path / "dep", created="2026-01-02T00:00:00Z", tick=80)
    loaded = snapshots.load(first)
    assert (loaded.tick, loaded.is_system, loaded.directory) == (40, False, tmp_path / "dep")
    assert [s.tick for s in snapshots.find(tmp_path)] == [40, 80], "oldest first"
    with pytest.raises(snapshots.SnapshotError, match="not a snapshot"):
        snapshots.load(tmp_path)
    (first / snapshots.SNAPSHOT_META).write_text(json.dumps({"format": 99}))
    with pytest.raises(snapshots.SnapshotError, match="format 99"):
        snapshots.load(first)


def test_a_system_snapshot_holds_its_elements(tmp_path):
    system = tmp_path / "sys"
    system.mkdir()
    (system / snapshots.SNAPSHOT_META).write_text(json.dumps({
        "format": snapshots.FORMAT, "kind": "system", "system": "Mission", "directory": str(tmp_path / "gen"),
        "elements": {"cdh": 20, "payload": 40}, "sim_seconds": 2.0}))
    write_snapshot(system / "cdh", tmp_path / "gen" / "cdh", kind="element", tick=20)
    write_snapshot(system / "payload", tmp_path / "gen" / "payload", kind="element")
    loaded = snapshots.load(system)
    assert loaded.is_system and sorted(loaded.elements) == ["cdh", "payload"]
    assert loaded.elements["cdh"].tick == 20
    assert [s.path for s in snapshots.find(tmp_path)] == [system], "an element's snapshot is part of its system's"


def test_a_run_from_a_snapshot_says_what_does_not_go_with_it(tmp_path):
    snap = write_snapshot(tmp_path / "snap", tmp_path / "dep")
    args = SimpleNamespace(directory=".", from_snapshot=str(snap), stepped=None, scenario=None, record=["x.jsonl"])
    with pytest.raises(WstsError, match="--record does not go with --from"):
        _run_cli(args)
    args = SimpleNamespace(directory=str(tmp_path / "other"), from_snapshot=str(snap), stepped=None, scenario=None,
                           record=None)
    with pytest.raises(WstsError, match="is a snapshot of"):
        _run_cli(args)
    args = SimpleNamespace(directory=".", from_snapshot=str(snap), stepped=False, scenario=None, record=None)
    with pytest.raises(WstsError, match="--realtime does not go with --from"):
        _run_cli(args)


def test_a_snapshot_of_a_run_without_a_journal_says_how_to_keep_one(tmp_path):
    with pytest.raises(snapshots.SnapshotError, match="--journal"):
        snapshots.read_journal_meta(tmp_path)
    with pytest.raises(snapshots.SnapshotError, match="nothing is running"):
        snapshots.take(tmp_path, tmp_path / "snap")
    (tmp_path / "taken").mkdir()
    with pytest.raises(snapshots.SnapshotError, match="exists already"):
        snapshots.take(tmp_path, tmp_path / "taken")
