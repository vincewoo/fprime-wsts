# Snapshots

A **snapshot** saves a running simulation at a tick, so that other runs start from there:
after a long start-up, a commissioning sequence or a fault that took time to develop, and
as many times as you like. A fault campaign takes one snapshot and branches a run from it
per fault.

A snapshot is not an image of the process. It holds the run's **inputs** up to that tick:
in [stepped time](control.md#stepped-time-experimental) a run given the same inputs at the
same ticks does the same to the byte, so a new run of the same build replays them, as fast
as the host computes, and arrives at the same state. Every bus transaction of the replay is
checked against the snapshot's: a restored run that would differ says so and where, rather
than handing you a state that is not the one you saved. The design is in
[Snapshots (design)](../snapshots.md).

## Keep a journal

Only a run that keeps a **journal** can be snapshotted. It must run in stepped time:

```bash
fprime-wsts run wsts/MyDeployment --stepped --journal
```

or make it the default in the manifest:

```yaml
time:
  mode: stepped
  journal: true
```

The journal is in the run's log directory: `journal.jsonl` (the run's recording from its
first tick), `journal-files/` (the files flight software started with) and `journal.json`.
A journaled run's flight software gets a file sandbox of its own, `<log dir>/files`, filled
from `files.sandbox` when the manifest has one, so that what an earlier run left does not
change how this one starts.

A system keeps a journal in lockstep: `fprime-wsts run wsts/MySystem --stepped --journal`.

## Take a snapshot

While the run is up:

```bash
fprime-wsts snapshot wsts/MyDeployment after-boot
```

The run is paused between two ticks, flight software is left to finish what it was given,
and the journal so far is saved in the new directory `after-boot`. A run that was running
goes on (`--stay-paused` keeps it paused). A system is snapshotted as a whole, at one
instant: give its directory. From Python, `snapshots.take(directory, path)`
(`from fprime_wsts import snapshots`); from an agent, the MCP `snapshot` tool.

`fprime-wsts snapshots <dir>` lists the snapshots under a directory, and describes one.

## Start from it

```bash
fprime-wsts run --from after-boot --gds
```

The deployment (or system) the snapshot is of starts held, replays the journal, checks it,
and holds there, paused at the snapshot's tick: resume it through the bridge or the GDS, or
give `--duration`, which then counts from the snapshot. The restored run keeps a journal of
its own, so it can be snapshotted again.

In a test, the `wsts_restore` fixture of the pytest plugin starts a restored run on free
ports, with logs of its own, and stops it after the block:

```python
def test_each_fault_after_boot(wsts_restore):
    for kind in ("drop", "corrupt", "error"):
        with wsts_restore("after-boot") as sim:        # a WstsBridge; a WstsSystem for a system
            sim.fault_set("i2cBus", kind, arg=3)
            sim.step(200)
            ...
```

Several can run at once: `with wsts_restore(snap) as a, wsts_restore(snap) as b:` gives two
branches of one state. `sim.log_dir` is the restored run's log directory, to snapshot it
from. An agent passes `from_snapshot` to `run_start`.

## When a restore fails

| Message | Why |
|---|---|
| `... was taken of another build` | The executable is not the one the snapshot was taken of (their SHA-256 differ): rebuild from the state it was taken at, or take a new snapshot |
| `the restored run differs from the snapshotted one at tick T on E, bus record N` | Flight software was given something the journal does not hold. The message shows both records |
| `a snapshot needs a run in stepped time`, `... keeps no journal` | Start the run with `--stepped --journal` |

What a journal does not hold, and so what makes a restore differ:

- **Commands from the GDS.** They reach flight software over the deployment's own ground
  link, which the toolkit does not see. A run that was given one restores only up to the
  first record the command changed. The commands of `Wsts.SimControl` are actions, and are
  in the journal. Drive a run you mean to snapshot through the bridge, or send flight
  commands after the snapshot.
- **A bridge client that served a device** (`serve_i2c`, `serve_packet`, …). It is not
  there when the run is restored. Python models of the manifest (`run.models`) are: they
  start with the restored run, before its journal replays.
- **Code that waits on the host**, which the report's Time section lists: it keeps any two
  runs from being the same, a restore included.

A restore re-runs the whole journal: an hour of flight time at sixty times real time takes a
minute. A snapshot of a restored run does not shorten that; it holds the history from tick 0.
