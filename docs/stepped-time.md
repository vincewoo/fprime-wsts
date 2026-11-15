# Stepped Time (design draft)

**Status: a proposal, nothing of it is built.** It is item 1 of the
[roadmap](design.md#roadmap). [What to decide](#what-to-decide) lists the choices that
need an answer before work starts.

## The problem

A simulated deployment is paced by the host's clock. `Wsts.SimTick` sleeps until the next
tick is due, and within a tick the flight threads run whenever the host schedules them.
Three things follow:

- **A run takes as long as it simulates.** Ten minutes of flight time are ten minutes of
  CI, or less with `set_rate`, until the host cannot keep up and ticks stretch.
- **A run is repeatable to the tick, and no further.** Whether a handler ran before or
  after an injected byte, or whether a rate group finished before the next tick, differs
  from run to run. Tests assert on outcomes with timeouts for that reason.
- **The elements of a system drift.** Each has its own tick thread; a pause reaches them
  one after another.

The `step` action already lets a test decide how far time goes. It does not change what
happens inside a tick, and each tick still waits for the wall clock.

## What "stepped" means here

In stepped mode the simulation does not look at the wall clock. It takes one tick, waits
until flight software has **finished everything that tick caused**, and only then takes
the next one, or stops, if nobody asked for another.

| | Today | Stepped |
|---|---|---|
| When the next tick is taken | When the wall clock says so | When the last one is finished and one has been asked for |
| Speed | Real time times `rate` | As fast as the host computes |
| Time flight software sees | The host's clock | Tick count times tick period |
| A rate group that overruns | Overlaps the next tick, as in flight | Finishes first; the overrun is counted and reported |
| The same scenario twice | The same ticks, different interleavings | The same bus traffic, record for record (see [limits](#what-it-does-not-give)) |

Stepped mode is chosen per run, not per build where that can be had (see
[decision 1](#what-to-decide)). Real-time mode stays the default and behaves as today.

### What it does not give

- **Not the flight scheduler.** Priorities, preemption and a rate group overrunning into
  the next cycle are properties of the target's scheduler. Stepped time removes the
  overlap instead of reproducing it. [fidelity.md](fidelity.md) will say so.
- **Not a single-threaded execution.** Within one tick, several active components may
  still run at once on separate host threads. What is fixed is the set of work in each
  tick, and with the bus serialised (below) the order of bus transactions. Two components
  that race on shared memory without a port between them stay a race.
- **Not for code that waits on the host.** A component that calls `usleep`, reads
  `clock_gettime` itself or blocks in a system call is outside what the toolkit sees. Such
  components are found and reported (below); they need a surrogate or stay in real time.

## How flight software waits, and where the wall clock gets in

Read from F´ v4.4.2 and from the toolkit as it is:

| Where | What it does today | In stepped mode |
|---|---|---|
| Active components | Each is an `Os::Task` blocked in `Os::Queue::receive`. The default queue on Linux is `Os_Generic_PriorityQueue`, built on `Os::Mutex` and `Os::ConditionVariable` | The seam for knowing when work is finished |
| `Os::Task::delay` | `nanosleep` | Waits for simulated time |
| `Os::RawTime` | `clock_gettime`; rate groups time their cycles with it | Simulated time |
| The time component (`Svc.PosixTime`, `Svc.ChronoTime`, a project's) | Reads the host clock directly, not through `Os::RawTime` | A catalog surrogate that gives simulated time |
| `Wsts.SimTick`, the built-in ticker | `sleep_until` the next tick | Takes a tick when the stepper grants one |
| `Wsts.SimCycle` clocks | Their own thread spaces the granted cycles by the wall clock | Cycles issued in order of their instants, each waited for |
| `delay` faults | `sleep_for(ms)` on the calling thread | Held for simulated time |
| `Wsts.SimControl` | Telemetry once a wall-clock second | Once a simulated second |
| Ground link (`Drv.TcpClient`), bridge, link sockets | Their own threads, blocked in `recv` | Outside inputs: see [Inputs from outside](#inputs-from-outside) |

Implementations of the `Os` interfaces are chosen at link time
(`CHOOSES_IMPLEMENTATIONS`), by the platform and, last, by the deployment. The overlay
writes the deployment's `CMakeLists.txt`, so it can choose its own without touching flight
code or the framework.

## Design

### 1. Knowing when a tick is finished

"Finished" means **quiescent**: every flight task is blocked on an empty queue, no bus
transaction is in progress, and nothing is waiting to be delivered.

`Wsts_Os_Queue` is the generic priority queue of F´ with a counter around it. A global
*activity count* is the number of messages sent and not yet fully handled:

- `send` adds one;
- a `receive` that returns a message does not subtract yet: the handler is about to run;
- the *next* blocking `receive` of the same task subtracts one for the message it has
  just finished, before it blocks.

The count is zero exactly when every queued message has been received and its handler has
returned. Synchronous and guarded port calls need nothing: they run inside the handler
that makes them. The stepper waits on a condition variable for the count to reach zero.

This is the standard way to do it and its weak point is the standard one: **work that
does not go through a queue.** A task that loops on its own (a driver's receive thread, a
project thread started with `Os::Task` that polls) is never counted. Three measures:

- `Wsts_Os_Task` keeps a registry of tasks and what each is doing (in a handler, blocked
  on its queue, in `delay`, never seen at a queue). The stepper can name the task that
  holds a tick up, and a tick that does not finish within a wall-clock limit ends the run
  with that name rather than hanging.
- The toolkit's own threads (bus delivery, link readers, bridge injection) take and
  release the activity count explicitly around what they deliver.
- `generate` already reports kept components that supply time or ticks. It will also
  report kept components whose sources call `sleep`, `usleep`, `nanosleep`,
  `clock_gettime`, `std::chrono` clocks or `Os::Task::delay` in a loop: candidates for
  trouble in stepped mode, listed in the fidelity report.

### 2. Simulated time

The control core already counts ticks and knows the tick period. Simulated time is
`epoch + tick × period`, plus the offset of the clock cycle being issued, if any.

- `Wsts_Os_RawTime::now()` returns it. Rate groups then measure a cycle time of zero, or
  of the simulated time their handlers waited; `RgCycleSlip` no longer depends on host
  load. That is a fidelity change: stated in the report.
- `Wsts.SimTime`, a new stock surrogate for `Svc.PosixTime` and `Svc.ChronoTime` in the
  catalog, gives the same time on the time port, so event and telemetry time stamps are
  the same in two runs. The epoch is fixed by default and can be set
  (`time: {epoch: 2030-01-01T00:00:00Z}`).
- `Wsts_Os_Task::delay` registers a wake-up at `now + interval` in simulated time and
  blocks; the task counts as idle while it waits. The stepper wakes it when time passes
  the instant. A delay shorter than a tick wakes at the next tick boundary, or at its
  exact instant if clocks are in use (the same mechanism as clock cycles).

In real-time mode the same implementations fall through to the host clock, so one build
serves both modes.

### 3. The stepper

A small state machine in the control core replaces the sleep in the tick source:

```
wait for a grant            (step N, or free-running: always granted)
advance simulated time to the next instant: a tick, a clock cycle, or a delay that is due
issue it                    (cycle the rate groups, or a clock, or wake the task)
wait for quiescence
repeat
```

- `step` (the action that exists) becomes the grant. From a client, `step(5)` returns
  when five ticks have been taken *and finished*, which is stronger than today.
- `resume` in stepped mode means free-running: ticks back to back, as fast as they
  finish. `set_rate` then has no meaning and is refused, or (decision 3) paces
  free-running stepped time against the wall clock for a GDS operator to watch.
- Faults with `ticks`, scenarios and model `onTick` are driven by the tick count already
  and need no change.
- **The bus is serialised in stepped mode.** One mutex around bus transactions fixes
  their order within a tick as far as one task's handlers go; across tasks the order is
  that of the queue priorities, because (decision 2) the stepper lets one task run at a
  time. Recordings then compare byte for byte.

`Wsts.SimCycle` loses its thread in stepped mode: the stepper issues each clock's cycles
at their instants, in order, interleaved with the flight tick, each waited for.

### 4. Inputs from outside

Anything that arrives from another process arrives at an arbitrary moment: a ground
command over TCP, a bridge action, bytes from another element. Stepped mode gives each a
defined place:

- **Bridge actions** are applied between instants, never inside one. A client that sends
  `inject_bytes` and then `step(1)` knows the bytes are there before the tick. This is
  the part that makes tests exact.
- **Link traffic between elements** is tagged with the sender's simulated time and
  delivered at the start of the receiver's next instant at or after it (lockstep, below).
- **The ground link** stays a real socket and stays asynchronous: a command from the GDS
  lands in whichever tick is current when it arrives. Its handling is counted like any
  other work, so the tick still finishes properly. A test that needs a command at an
  exact tick sends it while paused and then steps.

### 5. Systems: lockstep

With every element stepped, `fprime-wsts run` (or a `WstsSystem` client) becomes the
conductor. Elements have different tick rates (10 Hz and 20 Hz in the example), so the
conductor advances *simulated time*, not ticks: it grants each element time up to the
next instant any of them has, waits for all to be quiescent and for the links to be
drained, and repeats. Pause, resume and step then act on the system as one thing, and a
system scenario with one time base becomes possible.

This is phase 4 and can wait: stepped time for one deployment is useful by itself.

## Alternatives considered

- **Faster wall clock only** (`set_rate(100)`). Exists. Ticks stretch as soon as the host
  cannot keep up, and nothing is repeatable. It stays, as the cheap option.
- **Cooperative, single-threaded scheduling** (F´'s `isCooperative` tasks: no threads,
  the stepper calls each component's dispatch loop in a fixed order). Fully deterministic
  and the simplest to reason about. Rejected as the first step because flight code that
  blocks (a driver thread in `recv`, a handler that waits on a semaphore another task
  gives) deadlocks in it, and a deployment would have to be free of such code to run at
  all. It remains the natural end point of decision 2.
- **Intercepting `libc`** (`LD_PRELOAD` of `clock_gettime`, `nanosleep`, `pthread_cond_timedwait`,
  as `libfaketime` does). Catches code the OSAL does not, but says nothing about when
  work is finished, and breaks under sanitizers and static linking. Possibly useful later
  for the leftover direct calls, not as the mechanism.
- **Quiescence by polling** (sample the queues until they look empty twice). No changes
  to the queue, and wrong whenever a handler is between receive and send.

## What changes for a user

```yaml
time:
  mode: stepped        # or realtime (default); or chosen at run time, see decision 1
  epoch: 2030-01-01T00:00:00Z
```

```bash
fprime-wsts run <dir> --stepped --duration 600     # 600 simulated seconds, as fast as they compute
```

```python
sim.pause()
sim.inject_text("sensorUart", "$TEMP,95.5\n")
sim.step(1)                       # the tick that reads it has run to completion
assert sim.gpio_get("heaterPin") is False     # no timeout, no retry
```

`--duration` counts simulated seconds in stepped mode. The `--json` summary gains the
simulated time reached and the ratio to wall time. The fidelity report gains a "Time"
section: the mode, the surrogate for the time component, and the components reported as
waiting on the host.

## Phases

| Phase | Delivers | Size | Proves |
|---|---|---|---|
| 0. Spike | `Wsts_Os_Queue` with the activity count, chosen by the overlay; the example's Payload reaches quiescence after each tick | Small | That implementations can be chosen from the overlay as assumed, and that quiescence is reached with the example's framework components (ground link, hub, health) |
| 1. Stepped ticks | The stepper, `--stepped`, exact `step`, bridge actions between ticks, the stuck-task report | Medium | Faster than real time; tests without timeouts |
| 2. Simulated time | `Wsts_Os_RawTime`, `Wsts.SimTime`, `Os::Task::delay`, clocks without threads, `delay` faults, serialised bus | Medium | Two runs of one scenario give identical recordings and time stamps: a test in CI that compares them |
| 3. Reporting | The generator's report of code that waits on the host; fidelity and user guide pages | Small | A project learns before it builds whether stepped mode will work |
| 4. Lockstep | The conductor for systems, time-tagged links, system-wide pause, step and scenarios | Medium to large | The example system repeatable end to end |

Each phase ends with something usable and can be merged by itself. Phase 0 is the one to
do first whatever else is decided: if a framework component cannot be made quiescent, the
design changes.

## Risks

- **A framework component that never goes quiet.** `Svc.GenericHub` and the ground
  link's receive task block in the driver, not on a queue; they are expected to be idle
  between messages, but phase 0 has to show it.
- **The implementation choice.** That a deployment's choice overrides the platform's for
  `Os_Queue`, `Os_Task` and `Os_RawTime` is read from `cmake/implementation.cmake`, not
  yet tried from an overlay.
- **VxWorks deployments with their own tasks.** Project code that starts threads outside
  `Os::Task` (the POSIX shim of roadmap item 2 would cover it) is invisible to the count.
- **Queue overflow behaves differently.** With the stepper waiting for each tick to
  finish, a queue that overflows in flight because a consumer is slow may not overflow
  here. This is the price of removing overlap, and belongs in the fidelity page.
- **Maintenance.** `Wsts_Os_Queue` wraps the generic queue rather than copying it, so it
  follows the framework; the task and time implementations are small. All three need the
  framework's own OS conformance tests run against them.

## What to decide

1. **Per run or per build?** Per run (`--stepped`, one binary serves both modes) costs a
   branch in each OS implementation and means the simulated build always uses the
   toolkit's queue, task and time. Per build (`time: {mode: stepped}` in the manifest)
   keeps real-time builds exactly as they are today. *Recommendation: per run, with the
   manifest setting the default.* A simulation that can only be stepped by rebuilding
   will be stepped less.
2. **One task at a time, or all at once, inside a tick?** One at a time (the stepper
   hands a token to the highest-priority ready task) gives full repeatability and costs
   parallel speed. All at once is faster and leaves the order of bus transactions across
   tasks to the host. *Recommendation: one at a time.* Repeatability is the point, and
   flight computers of this class are mostly single-core.
3. **Does free-running stepped time keep a pace?** For an operator at a GDS, time that
   runs a hundred times too fast is useless. *Recommendation: `set_rate` in stepped mode
   caps the speed (rate 1 is real time, as now), and `--stepped` without a rate runs
   flat out.*
4. **What happens to an overrun?** A rate group whose handlers take longer than a period
   of host time is invisible in stepped mode. *Recommendation: measure host time per tick
   anyway and report the slowest ticks in the run summary, as a hint, not as a failure.*
5. **Scope of the first merge.** *Recommendation: phases 0 and 1 together*, behind
   `--stepped` and documented as experimental, then phase 2 as the change that makes the
   repeatability claim and adds the CI test for it.
