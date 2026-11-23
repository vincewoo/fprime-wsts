"""Snapshots of a running simulation, and runs restored from them (docs/snapshots.md).

A snapshot is not an image of the process. It is a run's inputs up to a tick: in stepped
time a run given the same inputs at the same ticks records the same bytes, so a new run of
the same build, started on the same files and given the same inputs, reaches the same state.

- A run that may be snapshotted keeps a **journal** (`fprime-wsts run --journal`, or
  `time.journal` in the manifest): its recording from the first tick (every bus transaction
  and every action, at the tick it took effect in), a copy of the files flight software
  started with, and `journal.json` (the build, whether the run started paused, its
  environment). Its flight software gets a sandbox of its own, `<log dir>/files`.
- **Taking** a snapshot pauses the run between two ticks, waits until flight software has
  finished what it was given, and copies the journal so far: it ends with that pause.
- **Restoring** starts the same build held, on a copy of the snapshot's files, loads the
  journal as a scenario once the run's Python models are on their endpoints, and lets it
  replay: it stops where the pause was, at the snapshot's tick. Every bus transaction of the
  replay is compared with the journal's; the first that differs ends the restore and names
  the tick and endpoint. A restored run keeps a journal of its own, so it can be
  snapshotted again.

A system in lockstep is snapshotted as a whole: every element's journal up to the instant
the system was held at, restored together under the conductor.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .fpp import WstsError

FORMAT = 1
#: In the log directory of a run (of each element, in a system)
JOURNAL = "journal.jsonl"
JOURNAL_META = "journal.json"
JOURNAL_FILES = "journal-files"
#: The sandbox of a journaled or restored run's flight software, in its log directory
SANDBOX = "files"
#: In a snapshot (of each element, in a system)
SNAPSHOT_META = "snapshot.json"
#: Wall-clock seconds a restore may go without its tick advancing before it is given up
STALL_LIMIT = 60.0


class SnapshotError(WstsError):
    """A snapshot cannot be taken or loaded"""


class RestoreMismatch(WstsError):
    """A restored run did not do what the snapshotted run did. `index` is the bus record
    (counted from 0) where they first differ, `expected` and `found` the two records."""

    def __init__(self, element: str, index: int, expected: Optional[str], found: Optional[str]):
        self.element = element
        self.index = index
        self.expected = expected
        self.found = found
        where = ""
        for line in (expected, found):
            if line:
                try:
                    record = json.loads(line)
                    where = f" at tick {record.get('tick')} on {record.get('endpoint')}"
                except ValueError:
                    pass
                break
        super().__init__(
            f"{element}: the restored run differs from the snapshotted one{where}, bus record {index}:\n"
            f"  snapshot: {expected or '(none: the restored run did more)'}\n"
            f"  restored: {found or '(none: the restored run stopped short)'}\n"
            "Something reached flight software that the journal does not hold: a ground command, a "
            "bridge client that served a device, code that waits on the host (the report's Time section), "
            "or another build")


def binary_digest(path: Path) -> str:
    """The SHA-256 of a deployment's executable: the build a snapshot belongs to"""
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def bus_records(path: Path) -> List[str]:
    """The bus transactions of a recording, as written: what flight software did and was given"""
    with open(path) as file:
        return [line.rstrip("\n") for line in file if '"rec"' in line]


def compare(element: str, expected: List[str], found: List[str]) -> None:
    """Raise RestoreMismatch at the first record that differs"""
    for index in range(max(len(expected), len(found))):
        a = expected[index] if index < len(expected) else None
        b = found[index] if index < len(found) else None
        if a != b:
            raise RestoreMismatch(element, index, a, b)


# ----------------------------------------------------------------------
# The journal
# ----------------------------------------------------------------------


def prepare_journal(simulation, sandbox: Optional[Path]) -> Path:
    """Before a journaled run starts: its recording, its own sandbox, and a copy of the files
    flight software starts with. Returns the sandbox the run's flight software is to use.
    `sandbox` is the manifest's (files.sandbox), or None."""
    log_dir = simulation.log_dir
    own = log_dir / SANDBOX
    if simulation.restore is None:
        shutil.rmtree(own, ignore_errors=True)
        if sandbox is not None and sandbox.is_dir():
            shutil.copytree(sandbox, own)  # the manifest's files, which the run must not change
        else:
            own.mkdir(parents=True)
    copy = log_dir / JOURNAL_FILES
    shutil.rmtree(copy, ignore_errors=True)
    shutil.copytree(own, copy)
    if simulation.record is None:
        simulation.record = str(log_dir / JOURNAL)
    return own


def write_journal_meta(simulation, start_paused: bool, env: Dict[str, str]) -> None:
    meta = {
        "format": FORMAT,
        "deployment": simulation.binary.name,
        "directory": str(simulation.directory),
        "binary_sha256": binary_digest(simulation.binary),
        "journal": str(Path(simulation.record).resolve()),
        "files": str((simulation.log_dir / JOURNAL_FILES).resolve()),
        "start_paused": start_paused,
        "env": env,
        "restored_from": str(simulation.restore.path) if simulation.restore is not None else None,
    }
    (simulation.log_dir / JOURNAL_META).write_text(json.dumps(meta, indent=2) + "\n")


def read_journal_meta(log_dir: Path) -> dict:
    path = Path(log_dir) / JOURNAL_META
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        raise SnapshotError(f"the run in {log_dir} keeps no journal: start it with fprime-wsts run --stepped "
                            "--journal (or time.journal in the manifest) to be able to snapshot it") from None


# ----------------------------------------------------------------------
# Snapshots
# ----------------------------------------------------------------------


@dataclass
class Snapshot:
    """A snapshot on disk: of one deployment, or of a system (`elements`, one each)"""
    path: Path
    meta: dict
    elements: Dict[str, "Snapshot"] = field(default_factory=dict)

    @property
    def is_system(self) -> bool:
        return self.meta.get("kind") == "system"

    @property
    def tick(self) -> int:
        return int(self.meta["tick"])

    @property
    def journal(self) -> Path:
        return self.path / JOURNAL

    @property
    def files(self) -> Path:
        return self.path / SANDBOX

    @property
    def directory(self) -> Path:
        """The generated directory of the deployment (or system) it was taken of"""
        return Path(self.meta["directory"])

    def describe(self) -> dict:
        found = {"path": str(self.path), **{k: v for k, v in self.meta.items() if k != "format"}}
        if self.is_system:
            found["elements"] = {name: element.describe() for name, element in self.elements.items()}
        return found


def load(path) -> Snapshot:
    path = Path(path).resolve()
    try:
        meta = json.loads((path / SNAPSHOT_META).read_text())
    except (OSError, ValueError):
        raise SnapshotError(f"{path} is not a snapshot (no {SNAPSHOT_META})") from None
    if meta.get("format") != FORMAT:
        raise SnapshotError(f"{path}: a snapshot of format {meta.get('format')}; this toolkit reads format {FORMAT}")
    snapshot = Snapshot(path, meta)
    if snapshot.is_system:
        snapshot.elements = {name: load(path / name) for name in meta["elements"]}
    return snapshot


def find(directory) -> List[Snapshot]:
    """The snapshots under a directory, oldest first"""
    found = []
    for meta in sorted(Path(directory).rglob(SNAPSHOT_META)):
        try:
            snapshot = load(meta.parent)
        except SnapshotError:
            continue
        if snapshot.meta.get("kind") == "element":
            continue  # part of a system snapshot
        found.append(snapshot)
    return sorted(found, key=lambda s: s.meta.get("created", ""))


def _copy_element(log_dir: Path, target: Path, tick: int, status: dict, kind: str) -> dict:
    meta = read_journal_meta(log_dir)
    target.mkdir(parents=True)
    shutil.copyfile(meta["journal"], target / JOURNAL)
    shutil.copytree(meta["files"], target / SANDBOX)
    snapshot = {
        "format": FORMAT,
        "kind": kind,
        "deployment": meta["deployment"],
        "directory": meta["directory"],
        "binary_sha256": meta["binary_sha256"],
        "tick": tick,
        "tick_period_ns": int(status.get("tick_period_ns", 0)),
        "sim_seconds": float(status.get("sim_seconds", 0.0)),
        "start_paused": meta["start_paused"],
        "env": meta.get("env", {}),
        "records": len(bus_records(target / JOURNAL)),
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "taken_from": str(log_dir),
    }
    (target / SNAPSHOT_META).write_text(json.dumps(snapshot, indent=2) + "\n")
    return snapshot


def take(target, path, resume: Optional[bool] = None, timeout: float = 30.0) -> Snapshot:
    """Snapshot the run of a generated directory (or of a log directory): one deployment, or a
    whole system. The run is paused between two ticks for it; `resume` (default: whether it
    was running) lets it go on afterwards. `path` must not exist."""
    from .bridge import WstsBridge, WstsSystem
    from .runner import read_run_file

    path = Path(path).resolve()
    if path.exists():
        raise SnapshotError(f"{path} exists already: a snapshot is written to a new directory")
    target = Path(target).resolve()
    run = read_run_file(target)
    if run is None:
        raise SnapshotError(f"nothing is running from {target}")
    log_dir = target / "wsts-logs" if (target / "wsts-logs" / "wsts-run.json").is_file() else target
    if "elements" in run:
        return _take_system(target, log_dir, run, path, resume, timeout)
    with WstsBridge(run["bridge"], request_timeout=timeout) as sim:
        before = sim.status()
        if not before.get("stepped") or before.get("lockstep"):
            raise SnapshotError("a snapshot needs a run in stepped time (fprime-wsts run --stepped --journal); "
                                "an element of a system in lockstep is snapshotted with its system")
        read_journal_meta(log_dir)  # before the run is touched
        sim.pause()
        sim.wait_quiescent(timeout=timeout)
        status = sim.status()
        try:
            meta = _copy_element(log_dir, path, int(status["tick"]), status, "deployment")
        except BaseException:
            shutil.rmtree(path, ignore_errors=True)
            raise
        if resume if resume is not None else not before.get("paused"):
            sim.resume()
    return Snapshot(path, meta)


def _take_system(target: Path, log_dir: Path, run: dict, path: Path, resume: Optional[bool],
                 timeout: float) -> Snapshot:
    from .bridge import WstsSystem

    description = json.loads((target / "wsts-system.json").read_text()) if (target / "wsts-system.json").is_file() \
        else None
    if description is None:
        raise SnapshotError(f"{target} is not the generated directory of a system (no wsts-system.json)")
    for element in description["elements"]:
        element["bridge"] = run["elements"][element["name"]]
    with WstsSystem(description) as system:
        before = system.status()
        if not all(status.get("lockstep") for status in before.values()):
            raise SnapshotError("a snapshot of a system needs it in lockstep (fprime-wsts run <system> --stepped "
                                "--journal)")
        for name in before:
            read_journal_meta(log_dir / name)
        was_running = not any(status.get("paused") for status in before.values())
        system.pause()
        # The conductor takes the instant it is in, sees the pause and holds: wait until every
        # element stays where it is, with nothing outstanding
        settled = _wait_held(system, timeout)
        path.mkdir(parents=True)
        try:
            elements = {}
            for name, status in settled.items():
                elements[name] = _copy_element(log_dir / name, path / name, int(status["tick"]), status, "element")
            meta = {
                "format": FORMAT,
                "kind": "system",
                "system": description["system"],
                "directory": str(target),
                "elements": {name: element["tick"] for name, element in elements.items()},
                "sim_seconds": max(element["sim_seconds"] for element in elements.values()),
                "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            (path / SNAPSHOT_META).write_text(json.dumps(meta, indent=2) + "\n")
        except BaseException:
            shutil.rmtree(path, ignore_errors=True)
            raise
        if resume if resume is not None else was_running:
            system.resume()
    return load(path)


def _wait_held(system, timeout: float) -> Dict[str, dict]:
    deadline = time.monotonic() + timeout
    last: Optional[Dict[str, int]] = None
    since = time.monotonic()
    while time.monotonic() < deadline:
        statuses = system.status()
        ticks = {name: int(status["tick"]) for name, status in statuses.items()}
        quiet = all(status.get("paused") and not status.get("activity") for status in statuses.values())
        if quiet and ticks == last and time.monotonic() - since >= 0.3:
            return statuses
        if ticks != last or not quiet:
            last, since = ticks, time.monotonic()
        time.sleep(0.05)
    raise SnapshotError(f"the system did not come to rest within {timeout:g} s of being paused")


# ----------------------------------------------------------------------
# Restoring
# ----------------------------------------------------------------------


def check_build(simulation, snapshot: Snapshot) -> None:
    digest = binary_digest(simulation.binary)
    if digest != snapshot.meta["binary_sha256"]:
        raise SnapshotError(
            f"{snapshot.path} was taken of another build of {snapshot.meta['deployment']} (its executable's "
            f"SHA-256 is {snapshot.meta['binary_sha256'][:12]}…, this one's {digest[:12]}…). A snapshot replays "
            "the run's inputs, which only the same build answers the same way: rebuild from the state it was "
            "taken at, or take a new one")


def prepare_restore(simulation, snapshot: Snapshot) -> Path:
    """Before a restored run starts: the build, and its sandbox from the snapshot's files.
    Returns the sandbox."""
    check_build(simulation, snapshot)
    own = simulation.log_dir / SANDBOX
    shutil.rmtree(own, ignore_errors=True)
    shutil.copytree(snapshot.files, own)
    return own


def start_replay(simulation, snapshot: Snapshot) -> None:
    """Once a restored run is up (held, its models on their endpoints): load the journal and
    let it replay. A run that started running is let go, as that one was."""
    from .bridge import WstsBridge

    with WstsBridge(simulation.bridge, request_timeout=30) as sim:
        sim.request("scenario_load", path=str(snapshot.journal), mode="stimulus")
        sim.request("scenario_start")
        if not snapshot.meta.get("start_paused"):
            sim.resume()


def replay_done(status: dict, snapshot: Snapshot) -> bool:
    return (status.get("paused") and status.get("scenario_state") == "completed"
            and int(status.get("tick", -1)) == snapshot.tick)


def check_replay(simulation, snapshot: Snapshot, element: str) -> None:
    """Compare every bus record of the replay with the journal's"""
    compare(element, bus_records(snapshot.journal), bus_records(Path(simulation.record)))


def complete_restore(simulation, snapshot: Snapshot) -> dict:
    """Wait until a restored deployment has replayed its journal to the snapshot's tick, then
    check it. Returns its status there."""
    from .bridge import WstsBridge

    with WstsBridge(simulation.bridge, request_timeout=30) as sim:
        last_tick, since = -1, time.monotonic()
        while True:
            if simulation.sim is not None and simulation.sim.poll() is not None:
                raise WstsError(f"the restored run exited (code {simulation.sim.poll()}) at tick {last_tick}; see "
                                f"{simulation.log_dir / 'sim.log'}")
            status = sim.status()
            if status.get("stuck"):
                raise WstsError(f"the restored run did not finish tick {status.get('tick')}: {status['stuck']}")
            tick = int(status.get("tick", 0))
            if replay_done(status, snapshot):
                break
            if status.get("paused") and status.get("scenario_state") == "completed":
                raise RestoreMismatch(snapshot.meta["deployment"], -1,
                                      f'{{"tick": {snapshot.tick}}}', f'{{"tick": {tick}}}')
            if tick != last_tick:
                last_tick, since = tick, time.monotonic()
            elif time.monotonic() - since > STALL_LIMIT:
                raise WstsError(f"the restored run has not moved from tick {tick} for {STALL_LIMIT:g} s "
                                f"(the snapshot is at tick {snapshot.tick})")
            time.sleep(0.02)
        sim.wait_quiescent(timeout=30)
        check_replay(simulation, snapshot, snapshot.meta["deployment"])
        return sim.status()
