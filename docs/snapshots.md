# Snapshots (design)

**Status: built, except for ground commands, which are not journaled.** Snapshots of a running
simulation that a later run is restored from, so a test can start where a long start-up, a
commissioning sequence or a fault left off, and a fault campaign can branch many runs from
one instant. How to use them is in the user guide ([Snapshots](user-guide/snapshots.md)).
[Phase 0: what it showed](#phase-0-what-it-showed) has the results of the spike, which
changed the design in two places; [Phase 1: what is built](#phase-1-what-is-built) is exact
replay in stepped time, which the rest stands on; [Phases 2 to 4: what is built](#phases-2-to-4-what-is-built)
are the snapshots themselves, for a deployment and for a system, and where they part from
the design below.

## The problem

Getting a simulation to an interesting state takes time: a deployment boots, a sequence
runs, a model warms up, a fault is armed and develops. Every test that needs that state
pays for it again, and a campaign that tries twenty faults from one instant pays twenty
times. What is wanted is to save a run at a tick and start another from there, as often as
needed, and to know that the restored run is the one that was saved.

## What a snapshot is here

A snapshot is **not an image of the process**:

- An F´ deployment is a multi-threaded process with open sockets (the ground link, the
  hub, the bridge). `fork()` copies one thread only, so the child cannot carry on.
- Checkpointing the whole process (CRIU) needs privileges and kernel support that a
  workstation, a container or CI does not reliably give, and does not restore the GDS and
  the other elements of a system that hold the other ends of those sockets.
- F´ components have no general way to serialise their state, and the toolkit does not
  own the flight code that would have to add one.

Instead, a snapshot is **a run's inputs up to a tick**. In
[stepped time](stepped-time.md) two runs given the same inputs at the same ticks record the
same bytes (`tests/e2e/test_stepped.py` holds it), so a run is determined by:

- the build (the binary, which `wsts-report.json` and the dictionary's project version name);
- the files the run started with (the [sandbox](reference.md#files));
- the manifest's run settings that reach flight software (epoch, `run.env`, models and
  their parameters, the tick source);
- every input from outside, at the tick it took effect: bridge actions (faults, injected
  bytes, GPIO, register writes, model parameters) and ground commands.

To **restore** is to start a new run of the same build on a copy of those files and apply
those inputs at their ticks, in stepped time, as fast as the host computes, up to the
snapshot's tick, and then hand the run over paused (or running) to whoever asked. In the
example, Payload runs about sixty times real time in stepped time, so ten minutes of flight
time comes back in about ten seconds. A restored run can be snapshotted again, and so on.

Restoring also **checks** itself. The snapshot keeps the recording of the saved run (every
bus transaction, which the recorder writes already). The restore records too and compares
as it goes: the first record that differs stops the restore and names the tick, the
endpoint and both records. A snapshot that cannot be reproduced is reported, never
silently handed over as if it were the saved state.

## Phase 0: what it showed

The spike (a Payload run in stepped time, paused, given a fault, injected bytes and two
GPIO changes from a bridge client between steps, recorded; then the recording replayed as a
scenario in stepped time to the same tick) confirms the approach and found two things to
fix:

- **The fault replayed exactly**, and the run reached the same tick. Of 1525 device
  records (I2C, SPI, registers, the serial sensor, GPIO), 1517 were identical.
- **A recorded action replays one tick early.** The 8 that differed were at the ticks of
  the injected bytes and of the second GPIO change. A bridge action in stepped time falls
  between two ticks and is recorded with the number of the last tick taken (477), but a
  scenario applies its step at the *start* of that tick, before its work, so the replay
  put the bytes into tick 477 instead of after it. The recorder should write an action from
  outside with the tick it takes effect in (478): the same as a scenario step that applies
  it there. This is a fix to recording as it is today, not only for snapshots.
- **The simulation's own events are flight-visible.** Recording and loading a scenario post
  `SimLog` events (`Wsts.SimControl`) that name the files, and a replay posts one more than
  the live run did. Those events go through flight software's event manager and, in the
  example, over the hub, so its event counters and packet sequence numbers differed from
  tick 0 onwards (5604 of 5643 hub records). A restore must post exactly what the saved run
  posted: what the toolkit does to journal and replay inputs must not emit events of its
  own, and events that name files must not differ between the two runs. Building phase 1
  showed the same of run control: `fprime-wsts run --duration` steps a stepped run where
  the recorded one ran freely, and its `SimResumed` differed in the same way.

## Phase 1: what is built

- **Actions at the tick they take effect in.** `Control::exec` records an action from
  outside (a bridge client, a GDS command) at the next tick to begin (`m_tick + 1`), and one
  a scenario applies inside a tick at that tick. A recording made before this replays its
  actions from outside one tick earlier.
- **Scenario steps between ticks in stepped time.** `Control::awaitTick` applies the steps
  due before the next tick (`scenarioBetween`) under an `ActionGate` taken on the tick
  thread, as a client's action would be: after the last tick's work is finished, with what
  the action causes finished before the tick, and also while paused, so that a replay of a
  paused run takes its first step itself. A `pause` there leaves the tick untaken. Recorded
  device answers (`responses` mode) are still applied at the start of their tick, where
  they were recorded. In real time scenarios run as before, at the start of each tick;
  in lockstep, between ticks too (since phase 3).
- **How a run is driven stays out of flight software in stepped time.** `Wsts.SimControl`
  posts `SimPaused`, `SimResumed`, `SimRateSet` and the scenario and recording events to
  flight software in real time only, and writes `SimRunState`, `SimTimeScale` and the
  scenario and recording channels in real time only, like `Speed` of `wstsTlm`. They stay
  in the simulation's log, on the bridge's event stream and in `status`.
- **Held by a test.** `test_a_run_driven_by_a_client_replays_to_the_same_bytes` in
  `tests/e2e/test_stepped.py` repeats the spike in CI: every record of the replay equals the
  original, the hub's events and telemetry included (6784 records, 5281 of them on the hub,
  in the run it was built with). `InSteppedTimeAReplayAppliesRecordedActionsBetweenTheSameTicks`
  holds the order in the core's unit tests.

## Phases 2 to 4: what is built

- **The journal** (`fprime-wsts run --stepped --journal`, `time.journal: true`): the run's
  recording from its first tick (`<log dir>/journal.jsonl`), a copy of the files flight
  software starts with (`journal-files/`), and `journal.json`: the SHA-256 of the build's
  executable, whether the run started paused, its environment. A journaled run's flight
  software gets a sandbox of its own, `<log dir>/files`, filled from `files.sandbox` when the
  manifest has one: by default flight software's files land in the log directory, where an
  earlier run's would change how this one starts. A journal needs stepped time (lockstep, for
  a system), and `run` refuses one otherwise.
- **A snapshot** (`fprime-wsts snapshot <dir> <path>`, `snapshots.take`, the MCP `snapshot`
  tool) pauses the run between two ticks, waits until it is quiescent, and copies the journal
  so far, which ends with that pause, the files it started with, and `snapshot.json` (the
  build, the tick, the period, how many bus records). A run that was running goes on. A
  system is paused as a whole, and copied once the conductor holds and nothing is
  outstanding: one snapshot of every element, at its own tick of one instant.
  `fprime-wsts snapshots <dir>` lists them.
- **A restore** (`fprime-wsts run --from <path>`, the `wsts_restore` fixture, `run_start` with
  `from_snapshot`) refuses another build, starts the same one held on a copy of the files,
  loads the journal as a scenario once the run's Python models are on their endpoints (not
  at start-up, where a journal could let the run go before they are), and lets it replay as
  fast as the host computes: the run let go as the original was, paused and stepped as a
  client paused and stepped it, faults and injections where they were. It stops at the
  snapshot's pause. Then every bus record of the replay is compared with the journal's
  (`snapshots.compare`); the first that differs ends the run with its index, tick, endpoint
  and both records. The restored run keeps a journal of its own, from tick 0, so a snapshot
  of it is a snapshot like any other. `--duration` counts from the snapshot.
- **A system restore** starts every element on its journal; the conductor grants each the
  ticks it lacks (`Conductor.advance_to`) rather than holding at the first replayed pause:
  in the run that was snapshotted, the pause reached the elements one after another, and an
  element in lockstep takes a granted tick paused or not. It waits until every element has
  replayed its pause, then checks each. For that, an element's scenario now applies its
  actions between ticks in lockstep too, as in plain stepped time.
- **Plants** ([dynamics.md](dynamics.md#phase-3-what-is-built)): a snapshot of a coupled run
  keeps each plant's trace, and a restore re-drives the plants along it while the journal
  replays, comparing every exchange, so that the restored run goes on coupled.
- **Where it parts from the design:** the GDS of a restored run starts with it, and sees the
  replay's downlink, rather than starting at the end; and **ground commands are not
  journaled**. A command from the GDS reaches flight software over the deployment's own
  ground link, outside the bridge, so the journal does not hold it: a restore of a run that
  was given one differs from the record the command first changed, and the check says so (its
  message names ground commands among the causes). Journaling them means holding the run at
  an instant to send each, and sending it again through a GDS at the same instant of the
  restore; that is left for later. Commands of `Wsts.SimControl` are actions, and are in the
  journal.

## Design

### 1. The journal

A run that may be snapshotted keeps a **journal** from its first tick: the recording it
writes already (bus transactions and actions), with the fix above, plus what the recording
does not have:

- **Ground commands** sent through the toolkit (`send_command` of the MCP server, a helper
  for pytest), journaled with the tick they were sent at. In stepped time they are sent
  while paused, so they are handled before the next tick and land in a defined place, as
  `stepped-time.md` asks of a test that needs a command at an exact tick. A command typed
  into the GDS GUI, or sent with fprime-gds's own test API, does not pass through the
  toolkit and is not journaled: a restore of such a run fails its check at the first tick
  the command changed something, and says so.
- **Python models** (`run.models`) are part of the run and are restarted with it; they must
  be deterministic (`Signal`s are). A bridge client that serves a device itself
  (`serve_i2c`) is outside: a run that has one cannot be restored without it.

The journal costs a line per transaction, as `--record` does now. Whether every stepped run
keeps one is a decision below.

### 2. A snapshot

A directory: the journal up to the tick, the sandbox as it was when the run started, the run
settings, the build's identity, and the tick. `snapshot` of a running simulation writes it
between two ticks (the bridge's `ActionGate`), so it is consistent.

### 3. Restore

`fprime-wsts run <dir> --from <snapshot>` (and `WstsBridge`, pytest and MCP equivalents):
checks the build is the one the snapshot was taken from, copies the sandbox, starts in
stepped time paused, applies the journal tick by tick while comparing the recording, stops
at the snapshot's tick, and then pauses, resumes, or switches to real time, as asked. The
GDS starts at the end, not during the replay, so ground procedures see the restored run
from the snapshot's tick.

### 4. Systems

A system in lockstep is one time base: a system snapshot is each element's journal up to
the same instant, and the conductor restores them together. What crosses links is
reproduced by the elements themselves, as lockstep already guarantees.

## What changes for a user

```bash
fprime-wsts run wsts/MyDeployment --stepped --journal      # a run that can be snapshotted
fprime-wsts ctl wsts/MyDeployment snapshot path=after-boot  # between two ticks
fprime-wsts run wsts/MyDeployment --from after-boot --gds   # paused at that tick
```

```python
snap = snapshots.take("wsts/MyDeployment", "after-boot")   # from fprime_wsts import snapshots
for fault in campaign:
    with wsts_restore(snap) as branch:   # a new run, held at the snapshot's tick
        branch.fault_set(**fault)
        branch.step(200)
```

## Phases

| Phase | Delivers | Size | Proves |
|---|---|---|---|
| 0. Spike | A recorded stepped run replayed to the same tick and compared | Small | Done: the approach holds; two corrections ([above](#phase-0-what-it-showed)) |
| 1. Exact replay | Actions recorded at the tick they take effect in; scenario steps between ticks; how a run is driven kept out of flight software; a test that a stepped run driven from a bridge client replays to the same bytes, hub included | Small to medium | Built ([above](#phase-1-what-is-built)) |
| 2. One deployment | The journal, `snapshot`, `run --from` with the check, the Python and pytest API, ground commands through the toolkit | Medium | Built, but for ground commands ([above](#phases-2-to-4-what-is-built)) |
| 3. Systems | System snapshots and restore through the conductor | Medium | Built: the example system restored end to end (`tests/e2e/test_snapshots.py`) |
| 4. Tools | The MCP tools, `fprime-wsts snapshot` and `snapshots` (listing), docs | Small | Built; there is no pruning: a snapshot is a directory to delete |

## Risks

- **Nondeterminism the check finds late.** Code that waits on the host (the report's Time
  section) makes two runs differ; the restore then fails its check rather than handing over
  a different state. A deployment that cannot be stepped cannot be snapshotted.
- **Restore time grows with the snapshot's tick.** A restore re-runs the whole journal. A
  snapshot of a snapshot does not shorten it, and an hour of flight time at sixty times real
  time is a minute of replay. Faster restores would need state saved by the components
  themselves, which this design leaves out.
- **Ground commands outside the toolkit.** They cannot be journaled without a relay on the
  ground link, which the toolkit does not have (the example keeps the flight TCP client).
- **Files written outside the sandbox** (the raw I/O API of the VxWorks shim with absolute
  host paths) are not copied and may differ.

## What to decide

*Settled: 1 on request, with `time.journal` for a default; 2 through the toolkit, though no
ground command is journaled yet ([above](#phases-2-to-4-what-is-built)); 3 yes, and built;
4 systems too.*

1. **Journal always, or on request?** Always (every stepped run can be snapshotted later)
   costs the recording's disk and time on every run. On request (`--journal`) costs
   nothing otherwise, but a run nobody thought to journal cannot be snapshotted.
   *Recommendation: on request, with `time: {mode: stepped}` manifests able to make it the
   default.*
2. **Ground commands.** Journal the ones sent through the toolkit while paused (phase 2),
   or also build a relay on the ground link that holds uplink to tick boundaries.
   *Recommendation: through the toolkit only; the check reports any other.*
3. **Phase 1 first, by itself?** Exact replay in stepped time fixes recording as it is now
   and is small. *Decided: yes; built.*
4. **Systems in the first version?** *Recommendation: one deployment first (phase 2), then
   systems.*
