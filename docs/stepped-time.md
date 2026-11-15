# Stepped Time (design draft)

**Status: phases 0 to 4 are built (stepped time is experimental).** It is item 1 of the
[roadmap](design.md#roadmap). The choices under [What to decide](#what-to-decide) were
settled as recommended. [Phase 0: what it showed](#phase-0-what-it-showed) has the results
of the spike, and one correction to the design that came out of it;
[Phase 1](#phase-1-what-is-built), [Phase 2](#phase-2-what-is-built),
[Phase 3](#phase-3-what-is-built) and [Phase 4](#phase-4-what-is-built) say what stepped
time does today and what it leaves undone.

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
| Ground link (`Drv.TcpClient`), bridge, link sockets | Their own threads, blocked in `recv` | Outside inputs: see [Inputs from outside](#4-inputs-from-outside) |

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

A queue that is *polled* is counted differently (a finding of phase 0). A queued
component has a queue and no task: its messages wait until whoever dispatches it, usually
a rate group on a later tick, takes them. Waiting there they are not unfinished work, and
counting them would keep a deployment with `Svc.Health` from ever being quiescent. The
queue tells the two apart by how it is read: a queue read with a blocking receive belongs
to a task; one read with a non-blocking receive is polled, and its messages count only
from the moment they are taken until their handler returns.

The count is zero exactly when every message for a task has been received and its handler
has returned, and no handler of a polled queue is running. Synchronous and guarded port calls need nothing: they run inside the handler
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
- `generate` already reports kept components that supply time or ticks. It also reports
  components whose sources call `sleep`, `usleep`, `nanosleep`, `clock_gettime`,
  `std::chrono` clocks, `Os::Task::delay`, start threads of their own or wait for one:
  candidates for trouble in stepped mode, in the report's Time section (phase 3).

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
  delivered at the start of the receiver's first tick after it (lockstep, below).
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

This is phase 4 ([built](#phase-4-what-is-built)).

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
waiting on the host (built in phase 3).

## Phases

| Phase | Delivers | Size | Proves |
|---|---|---|---|
| 0. Spike | `Wsts_Os_Queue` with the activity count, chosen by the overlay; the example's Payload reaches quiescence after each tick | Small | That implementations can be chosen from the overlay as assumed, and that quiescence is reached with the example's framework components (ground link, hub, health) |
| 1. Stepped ticks | The stepper, `--stepped`, exact `step`, bridge actions between ticks, the stuck-task report | Medium | Faster than real time; tests without timeouts |
| 2. Simulated time | `Wsts_Os_RawTime`, `Wsts.SimTime`, `Os::Task::delay`, clocks without threads, `delay` faults, serialised bus | Medium | Two runs of one scenario give identical recordings and time stamps: a test in CI that compares them |
| 3. Reporting | The generator's report of code that waits on the host; fidelity and user guide pages | Small | A project learns before it builds whether stepped mode will work (built: [Phase 3](#phase-3-what-is-built)) |
| 4. Lockstep | The conductor for systems, time-tagged links, system-wide pause, step and scenarios | Medium to large | The example system repeatable end to end (built: [Phase 4](#phase-4-what-is-built)) |

Each phase ends with something usable and can be merged by itself. Phase 0 is the one to
do first whatever else is decided: if a framework component cannot be made quiescent, the
design changes.

## Phase 0: what it showed

Built: `Wsts::Activity` (the count, in `Wsts/Core`), `Wsts_Os_Queue` (`Wsts/Os`: F´'s
generic priority queue with the count around it), the overlay choosing it for every
simulated deployment, scopes around what the simulation's own threads do for flight
software (a tick, a clock cycle, a bus delivery, a bridge action), `activity`, `work` and
`busy_ticks` in `status`, and `wait_quiescent` on the bridge. Nothing is stepped yet:
ticks are still paced by the wall clock.

| Question | Answer |
|---|---|
| Can an overlay replace the framework's queue? | Yes. `CHOOSES_IMPLEMENTATIONS Wsts_Os_Queue` on the overlay's `register_fprime_deployment()` overrides the platform's choice; a unit test fails if it ever stops doing so |
| Does it still behave as an `Os::Queue`? | The framework's own queue tests (`Os/test/ut/queue`) are built against it and pass |
| Do the example's computers go quiet after a tick? | Yes, both, with the hub link and a GDS connected: after each of 40 single steps, within a millisecond. Running freely at 10 Hz and 20 Hz, no tick started while work of the one before was outstanding |
| Do the hub and the ground link keep it from going quiet? | No. Their receive tasks block in the driver between messages and put nothing on a queue |
| What did the design get wrong? | Polled queues, above: Cdh stood at five outstanding messages for ever, all of them ping replies waiting for `Svc.Health`'s next dispatch |

What it did not test: a deployment with a component that sleeps or polls by itself, a
flight project other than the example, and anything across elements (bytes in flight on a
link belong to neither element's count: phase 4).

The end-to-end tests now hold this: Payload and Cdh are stepped tick by tick and must be
quiescent after each.

## Phase 1: what is built

The stepper. `fprime-wsts run --stepped` (or `time: {mode: stepped}` in the manifest, or
`set_stepped` on a running simulation) takes each tick when flight software has finished
the last one.

| | |
|---|---|
| Tick sources | `Wsts.SimTick` and the built-in ticker bracket each tick with `Control::awaitTick()` and `finishTick()`. A project's own tick surrogate that only calls `onTick()` is not stepped until it does the same |
| A run of a set duration | `--duration` counts simulated seconds: the deployment starts paused and is given one `step` of that many ticks, so it ends at exactly that time. The step is given once the tick source has said how long a tick is (it does when start-up code starts it), waited for up to 20 s |
| Actions from a client | Held by `Control::ActionGate` in the bridge: applied between two ticks, and finished (the bytes an injection delivers are handled) before the next tick starts. A waiting action goes before the next tick, so a pause reaches a simulation that runs flat out |
| A pace | `set_rate` in stepped time is a limit (1 is real time); 0, the default, is none |
| A tick that does not finish | After `WSTS_STEP_LIMIT_MS` of wall time (10 s) the simulation pauses, `status` has `stuck` with the queues that hold work, the log says so, and `run` ends with exit status 1 |
| Clocks (`Wsts.SimCycle`) | Cycles a tick grants count as its work and are issued at once, in order, also when the step ends in a pause |
| Systems | `run --stepped` on a system is refused: the elements would drift apart. One element can be stepped through its bridge |

Measured on the example's Payload by itself: 600 simulated seconds (12,000 ticks at
20 Hz) in 0.7 to 1.2 s of wall time, ending at exactly tick 12,000, with the same number
of pieces of flight work and the same number of I2C, SPI and register transactions in two
runs.

After phase 1 two such runs still differed byte for byte: time stamps were the host's,
`Wsts.SimControl` published by the wall clock, and tasks ran at once within a tick. Phase
2 took those away.

## Phase 2: what is built

Two runs of one deployment in stepped time now give the same recording, byte for byte:
every bus transaction and every packet of events and telemetry flight software sends,
with its time stamp, in the same order. An end-to-end test runs each of the example's
computers three times and compares the files.

| | |
|---|---|
| Simulated time | `Control::simTimeNs()`: an epoch (2000-01-01T00:00:00Z, or `time.epoch` in the manifest) plus ticks times the tick period |
| The time component | `Wsts.SimTime` replaces `Svc.PosixTime` and `Svc.ChronoTime` from the catalog: the host's clock in real time, simulated time in stepped time. A project's own time component needs a surrogate that asks `Control::flightTime()` (the example's Cdh has one) |
| `Os::RawTime` | `Wsts_Os_RawTime`: the POSIX one in real time, simulated time in stepped time. Time stands still within a tick, so a rate group that times its cycle measures zero in every run |
| One at a time | In stepped time flight code runs on one thread at a time (`Activity::enter()`, `awaitTurn()`). When the turn is free it goes to the ready contender of lowest rank: threads of the simulation (a tick, a delivery) first, then tasks that are starting, in the order they were started, then tasks at their queues, in the order the queues were created. A task is known from the moment it is started (`Activity::expectTask()`, on the thread that starts it), not from when its thread first runs: no one ranked after it is given the turn before it comes. A task gives up its turn and queues for the next in one step, so who runs next does not depend on how fast a thread comes back |
| `Os::Task` | `Wsts_Os_Task`: the POSIX one; in stepped time a task takes the turn before its routine runs, so what it does before it first waits for a message is ordered too |
| Start-up | From the start of the runtime to the first tick the tasks are held: the deployment's start-up code runs alone. When the tick source first asks for a tick, the tasks begin, in their order, and the first tick waits until they have settled (each at its queue, nothing outstanding) and `Wsts.SimControl` has been told. An action from outside that arrives meanwhile is applied after that. If no tick is asked for within 5 s the tasks are let go, and the log says so |
| Clocks | No clock thread in stepped time: the stepper issues the cycles a tick grants itself, in the order of their instants, each when the one before is finished |
| `Wsts.SimControl` | Told at fixed points only: when start-up has settled, on every tick, after every action from outside, and when a tick leaves the simulation paused (the end of a step, a tick that did not finish). Not while paused by the wall clock, as in real time. Telemetry once a simulated second. An action's events (`SimPaused`, `FaultArmed`) are sent before the next tick, and whatever was done between two ticks is finished before the next one starts |
| Actions from outside | Applied when nothing is under way, not only between ticks: an action meets the same state in every run |

What the order of tasks is **not**: the flight scheduler's. Tasks run in the order of
their queues, whatever their priorities, and none preempts another.

Not built, of what phase 2 listed:

- **`Os::Task::delay` and `delay` faults still sleep on the wall clock.** They hold the
  turn meanwhile, so the result is the same in every run, only slow.
- **A handler that waits for another task** (a semaphore the other gives, a blocking
  send to a full queue whose receiver must run) cannot be served one at a time. A
  blocking send lets go of the turn while it waits; anything else ends as a tick that
  does not finish, reported after `WSTS_STEP_LIMIT_MS`.
- **What arrives from outside is not placed.** A ground command or bytes from another
  element land in whichever tick is being taken (ground link: by design; links: placed in
  lockstep, phase 4). `busy_ticks` counts the ticks that started while such a delivery was
  under way.
- **Telemetry that reads the host** (`Svc.SystemResources`: processor and memory use)
  differs between runs as it does between machines.

## Phase 3: what is built

A project learns before it builds whether stepped time will work for a deployment.
`fprime-wsts generate` reads the sources of the components the simulated deployment
builds and reports the calls that wait on the host, in a **Time** section of
`wsts-report.md` (`time.waits_on_host` in `wsts-report.json`); `fprime-wsts survey` gives
the same from its trial generation, before a manifest is written. The code is in
`python/fprime_wsts/hostwait.py`.

| | |
|---|---|
| What is read | The kept components of the topology (flight project and flight libraries), the simulation project's surrogates and its simulators: the C and C++ files their module's `CMakeLists.txt` names, in the variant the build takes (`cmake.stub_variants`), or every such file of the directory when it names none. Comments and string literals are left out. Text only: the report is the same on every machine |
| What is not | The framework's components go through the OSAL; the few that do not are known by name (socket, serial and GPIO drivers, `Svc.PosixTime`, `Svc.ChronoTime`, `Svc.LinuxTimer`, `Svc.SystemResources`) and listed with what they do when they are kept. The toolkit's own components are not read |
| Sleeps on the wall clock | `sleep`, `usleep`, `nanosleep`, `std::this_thread::sleep_for`, `taskDelay`, `Os::Task::delay`: *slower*, the result the same |
| Reads the host's clock | `clock_gettime`, `gettimeofday`, `time(NULL)`, `std::chrono` clocks: *not repeatable* |
| Reads the state of the host | `Svc.SystemResources`: *not repeatable* |
| Waits on a device or socket | `select`, `poll`, `recv` and the like: *not repeatable* (what arrives is outside input) |
| Starts a thread outside `Os::Task` | `pthread_create`, `std::thread`, `std::async`, `taskSpawn`: *not repeatable* (its work is not counted and does not take the turn) |
| Waits for another thread | `sem_wait`, `pthread_cond_wait`, `std::condition_variable`, `Os::ConditionVariable`: *may stall a tick* (one thread runs at a time) |
| Warnings | When the manifest asks for stepped time (`time.mode: stepped`), each module that is not merely slower is also a warning of `generate`. In real time the report says it and nothing is printed |
| The time source | The Time section also says what gives time stamps: `Wsts.SimTime`, a surrogate of the simulation project (simulated time only if it asks `Control::flightTime()`), or the flight component kept |

The example's Payload has nothing to report. Cdh has its ground link, `Drv.TcpClient`,
which is kept on purpose. Phase 3 found one real case in the example: Cdh's time
surrogate (`SensorSim.SimTime`) read `std::chrono::system_clock`, so Cdh's time stamps
were the host's in stepped time too. It now asks `Control::flightTime()`.

What it is **not**: an analysis of what is reached. A call in a branch that never runs
costs nothing and is still listed; a wait reached through a function of another module,
or through a variable that holds a function, is not seen. Whether a sleep is in a handler
or in a task of its own is not known either: the report gives the consequence for each.

Not built, of what the design listed: the slowest ticks in host time in the run summary
([decision 4](#what-to-decide)).

**Found while building it: three races of phase 2**, where the host's timing still
decided something. CI failed `test_two_runs_in_stepped_time_are_the_same_byte_for_byte`
now and then, and `test_a_whole_run_in_stepped_time` saw a busy first tick:

- A task joined the contest for the turn only when its thread first ran. When start-up
  let the tasks go, one whose thread came early could begin before one started earlier
  (two rate groups' first events swapped places). A task is now known when it is started.
- The first tick was taken as soon as the tasks were let go, with what start-up had left
  on the queues still unhandled (the busy first tick), and a `step` from the runner could
  land before, during or after the tasks began. Start-up now settles before the first tick
  and before any action.
- While paused, the tick source told `Wsts.SimControl` whenever the wall clock let it look
  again, so its first telemetry went out before or after a `step`, and a tick could start
  while it was handled. In stepped time it is now told at fixed points only.

The runner also gave up when, on a loaded host, the tick source had not yet said how long
a tick is; it now waits. Under every processor kept busy, 60 runs gave one recording per
element and no busy tick.

## Phase 4: what is built

Lockstep. The elements of a system are stepped together, on one time base, and two runs of
the example system record the same on every element, byte for byte: every bus
transaction, every packet each computer sends the other through the hub, and the tick each
arrived in. An end-to-end test runs the system three times for a simulated minute and
compares the files.

```bash
fprime-wsts run examples/mission-sim/wsts/Mission --stepped --duration 600 --record run.jsonl
```

| | |
|---|---|
| An element in lockstep | Stepped time, and a tick is taken only when it is granted (`Control::grant`, the bridge's `advance`), whatever the pause says. Its own `step` is refused. `WSTS_LOCKSTEP=1`, or `set_stepped` with `lockstep: true` on a running element |
| The conductor | `fprime_wsts.lockstep.Conductor`, in Python, with bridge connections of its own. An *instant* is the next simulated time at which any element has a tick: Cdh at 10 Hz and Payload at 20 Hz give one every 50 ms, Payload's alone, then both. The conductor grants one tick to every element due, at once (they run in parallel, each in its own process), and waits until all have finished it. Time is ticks times the tick period from start-up, so elements of different rates meet exactly; one that is behind (switched from real time, its clock started later) is brought up first |
| Links | Each message carries the simulated time it was sent at. In lockstep the receiving end holds it, and delivers it at the start of its first tick later than that time, before anything else of the tick. What two elements send each other in one instant therefore goes to their next ticks, whichever process got there first. Before granting a tick the conductor tells the receiving end how many messages the other end had sent by the end of the last instant (`expect.<link>`), and the tick waits for them: a slow host cannot make one arrive a tick late. If they do not come within `WSTS_STEP_LIMIT_MS`, the tick does not finish and `stuck` names the link. A message sent before the peer is connected waits for it instead of being lost |
| `fprime-wsts run <system> --stepped` | Starts every element in lockstep, waits until each has settled after start-up and its tick source has said how long a tick is, then conducts: for `--duration` simulated seconds, or until interrupted. A pause of any element (`SIM_PAUSE` from the GDS, a client's `pause`) holds the whole system at the end of an instant until it is resumed. The summary has `lockstep`, `sim_seconds`, `speed` and, for a tick that did not finish, `stuck` by element. A manifest of an element with `time.mode: stepped` makes it the default |
| A client | `WstsSystem.set_stepped()` switches a running system, `step(seconds=…)` or `step(instants=…)` advances it and returns when every tick is finished, `time()` is the system's time, `run_scenario()` plays a system scenario. The pytest plugin's `--wsts-stepped` starts a `--wsts-system` in lockstep, held until a test steps it |
| System scenarios | Actions at simulated times (`time`, seconds), each for an `element`, or a `link` for faults ([reference](reference.md#system-scenarios)). Applied between two instants, once the system's time has reached theirs. A plain `--scenario` of a system run in lockstep |

Measured on the example: a simulated minute of the system (600 ticks of Cdh, 1200 of
Payload, 1200 instants) in about 3 s of wall time, about 20 times real time. Most of it is
Cdh's own ticks, each about 3.5 ms: its 125 Hz clock issues twelve or thirteen cycles a
tick, each finished before the next. A grant from the conductor costs about 0.15 ms, and
the elements of one instant run at once, so the system goes about as fast as its slowest
element by itself.

What it is **not**:

- **Not the wire.** What a link carries reaches the receiver at its next tick: a latency of
  up to one of its tick periods (100 ms for Cdh) that a UART or Ethernet does not have, and
  none of the wire's own. A `delay` fault still sleeps on the wall clock.
- **The ground link is still outside input.** A command from the GDS reaches Cdh whenever
  the GDS sends it. Through the hub it then goes on in simulated time, and only while the
  system is stepped.
- **One conductor at a time.** A client that steps a system that `run` conducts freely
  races it: pause the system first, then step it.
- **An element restarted in the middle** comes back with its link counts at zero: the
  other end then waits for messages that will not come, and the tick is reported stuck.
- **No merged recording.** Each element records its own, at its own ticks.
- **Python device models** answer between instants, whenever their process gets to it,
  except those attached as I2C, SPI or register devices, which answer within the
  transaction.

## Risks

- **A framework component that never goes quiet.** Not the hub or the ground link (phase 0);
  other framework components that loop by themselves have not been met yet.
- **The implementation choice.** Shown for `Os_Queue` in phase 0. `Os_Task` and
  `Os_RawTime` are chosen the same way and are expected to behave the same.
- **VxWorks deployments with their own tasks.** Project code that starts threads outside
  `Os::Task` is invisible to the count. Tasks started through the
  [VxWorks API shim](vxworks-shim.md) (`taskSpawn`) are `Os::Task`s, and its semaphores,
  message queues, delays and watchdogs take part in the count and the turn: a tick waits
  for the tasks it wakes.
- **Queue overflow behaves differently.** With the stepper waiting for each tick to
  finish, a queue that overflows in flight because a consumer is slow may not overflow
  here. This is the price of removing overlap, and belongs in the fidelity page.
- **Maintenance.** `Wsts_Os_Queue` wraps the generic queue rather than copying it, so it
  follows the framework; the task and time implementations are small. All three need the
  framework's own OS conformance tests run against them.

## What to decide

*All five were settled as recommended.*

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
