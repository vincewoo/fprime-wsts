# The VxWorks API shim

Flight code written for VxWorks often calls the kernel directly: a task spawned with
`taskSpawn`, a semaphore a watchdog gives, a message queue between two tasks. Such code
does not compile on a workstation, and guarding it with `#ifndef WSTS_SIM` takes it out of
the simulation. The shim compiles and runs it unchanged. It is a POSIX-backed subset of
the VxWorks kernel API, implemented by the toolkit (`Wsts/VxWorks`). Its tasks, waits and
clock follow the simulation's own time and rules, in real time and in stepped time.

The example's `Cdh/Heartbeat` (`examples/mission/Cdh/Heartbeat`) is such code: a watchdog
gives a binary semaphore every six ticks of the system clock, and a task of its own counts
the beats under a mutual-exclusion semaphore and takes a new period from a message queue.

## Turning it on

```yaml title="cdh.yaml"
shims: [vxworks]
```

The overlay then:

- puts `Wsts/VxWorks/include` on the include path of every module, after the module's own
  directories: `#include <vxWorks.h>`, `<taskLib.h>`, … resolve to the shim's headers;
- links `Wsts_VxWorks` into the deployment as a whole archive, so any module can call it
  however the static libraries are ordered;
- counts the shim's headers as provided in `check-host` and in the report's host
  requirements;
- leaves the calls the shim implements out of the report's "Code that waits on the host"
  (`taskDelay`, `tickGet`, `taskSpawn`, `semTake`, `msgQReceive`).

Without the key, a module that includes one of the shim's headers without a guard gets a
warning from `generate` that names the key. `check-host` lists the headers as missing,
with `shims: [vxworks]` as the first remedy.

`WSTS_VXWORKS_SHIM` is defined by every header of the shim. Code that has to tell the shim
from the real kernel can test it. `TGT_OS_TYPE_VXWORKS` is **not** defined: code guarded
by it stays out of the simulated build, as before.

## What it provides

| Header | Calls |
|---|---|
| `vxWorks.h` | `STATUS`, `BOOL`, `OK`, `ERROR`, `NO_WAIT`, `WAIT_FOREVER`, `FUNCPTR`, `VOIDFUNCPTR`, `_Vx_usr_arg_t`, `_Vx_ticks_t`, `INT8`…`UINT64`, `UCHAR`, `UINT`, `ULONG`, `TRUE`, `FALSE` |
| `taskLib.h` | `taskSpawn`, `taskCreate`, `taskActivate`, `taskDelete`, `taskDeleteForce`, `taskExit`, `taskSuspend`, `taskResume`, `taskDelay`, `taskIdSelf`, `taskIdVerify`, `taskName`, `taskNameToId`, `taskPrioritySet`, `taskPriorityGet`, `taskLock`, `taskUnlock`, `taskSafe`, `taskUnsafe`; `TASK_ID`, `VX_FP_TASK` and the other options |
| `semLib.h` | `semBCreate`, `semCCreate`, `semMCreate`, `semTake`, `semGive`, `semFlush`, `semDelete`; `SEM_Q_FIFO`, `SEM_Q_PRIORITY`, `SEM_INVERSION_SAFE`, `SEM_DELETE_SAFE`, `SEM_EMPTY`, `SEM_FULL` |
| `msgQLib.h` | `msgQCreate`, `msgQSend`, `msgQReceive`, `msgQNumMsgs`, `msgQDelete`; `MSG_Q_FIFO`, `MSG_Q_PRIORITY`, `MSG_PRI_NORMAL`, `MSG_PRI_URGENT` |
| `wdLib.h` | `wdCreate`, `wdStart`, `wdCancel`, `wdDelete` |
| `sysLib.h` | `sysClkRateGet`, `sysClkRateSet` |
| `tickLib.h` | `tickGet`, `tick64Get` |
| `intLib.h` | `intContext`, `intLock`, `intUnlock` |
| `logLib.h` | `logMsg` |
| `errnoLib.h` | `errnoGet`, `errnoSet` |
| `objLib.h` | The `S_objLib_*` error codes: `OBJ_ID_ERROR`, `OBJ_UNAVAILABLE`, `OBJ_DELETED`, `OBJ_TIMEOUT` |
| `ioLib.h` | The host's `open`, `close`, `read`, `write`, `ioctl` (`<fcntl.h>`, `<unistd.h>`, `<sys/ioctl.h>`) |

Anything else does not compile, and that is the point at which to decide: guard the code,
or write a [surrogate](surrogate-guide.md) for the component.

`OK`, `ERROR`, `NO_WAIT`, `READ` and `NONE` are enumerators, not macros, as F´'s VxWorks
platform (`fprime-vxworks`) makes them: F´'s own headers use those names for enumerators of
their own. The `VXWORKS_OK` and `VXWORKS_ERROR` names of that platform exist too. Error
codes (`errno`) follow VxWorks's scheme: a module number in the upper half. Their values
are the shim's, and are only meant to be compared with the names.

## How it behaves

**Tasks** are `Os::Task`s of the simulated deployment, so in stepped time they take their
turn with flight software's other tasks. The stack size and the options are accepted and
not used: tasks get the host's default stack. The host's scheduler does not see a task's
priority. Priority decides which of several tasks waiting on one object is served first
and, in stepped time, the order in which ready tasks run: flight software's own tasks
first, then the shim's, by priority (0 first) and then in the order they were spawned.
`taskLock` and `taskUnlock` do nothing (there is no preemption to lock out), and neither
do `taskSafe` and `taskUnsafe`.

**Deleting or suspending another task** takes effect at its next call into the shim. A
task that is blocked in the shim is released at once: deleted, it ends there; suspended,
it does not go on until it is resumed. A task that deletes itself, or calls `taskExit`,
ends at once. Threads the shim did not start (flight software's own tasks, the main thread)
have an ID and a name (`taskIdSelf`, `taskName`), and cannot be deleted or suspended from
another task.

**Waiting.** A give, a send or a flush with tasks pending hands the object to the first of
them directly, by priority with `SEM_Q_PRIORITY` or `MSG_Q_PRIORITY`, else in the order
they pended, as the kernel does. Which task gets it never depends on which thread the host
runs first. Mutual-exclusion semaphores are recursive and owned: only the task that holds
one can give it. Inversion safety and deletion safety are accepted and not used. Deleting
a semaphore or a queue releases its waiters with `S_objLib_OBJ_DELETED`. A message longer
than the receiver's buffer is cut to it. `MSG_PRI_URGENT` puts a message at the head of
the queue.

**The system clock** ticks in simulated time, `sysClkRateGet()` times a second (60 unless
`sysClkRateSet` says otherwise), from the first simulation tick after the shim first needs
it. It is a cycle source of the simulation (a `Wsts::CycleClock`), like a
[clock](reference.md#clocks) in the manifest: it stops while the simulation is paused and
follows the time scale. `taskDelay`, time-outs and watchdogs are counted in its ticks, so
they all happen in simulated time. `tickGet` counts its ticks since then. A delay of 0
lets other ready tasks run first.

**Watchdog routines** run at interrupt level (`intContext()` is true) on the thread of the
system clock, at the tick they are due. Two due at the same tick run in the order they
were started. `intLock` keeps them out until the matching `intUnlock`, and does not stop
tasks. A routine can give semaphores, send messages with `NO_WAIT` and restart its
watchdog; it cannot take a semaphore.

**`logMsg`** writes at once to the deployment's standard error (`wsts-logs/sim.log`), with
the task's ID and name, or `interrupt:` from a watchdog routine.

## In stepped time

The shim takes part in [stepped time](stepped-time.md) as flight software's own queues and
tasks do:

- The system clock's ticks are issued by the stepper, each at its instant within the
  simulation tick, and each when the work of the one before is finished.
- A blocked task waits for its turn, which comes when it is woken and no task of a lower
  rank is ready.
- Whatever wakes a task (a give, a send, the clock) counts a piece of work for it. The
  task holds that piece until it blocks again, so a tick is not finished while a task it
  woke is still at work. A task just spawned is at work until it first blocks.

So a watchdog that gives a semaphore, and the task that takes it, both fall in the tick
the watchdog was due in, however slowly the host runs the task. Two stepped runs do the
same thing in the same order. `test_cdh.py::test_in_stepped_time_every_beat_is_counted_in_its_tick`
holds this for the example, and `Wsts/VxWorks/test/ut` for the shim itself.

## Limits

- **The subset above, and nothing else.** No `semExchange`, `semMGiveForce`, event
  libraries (`eventLib`), POSIX message queues of the kernel, task hooks, task variables,
  `taskCpuAffinitySet` (code under `_WRS_CONFIG_SMP` stays out) or RTP calls.
  `sioLib` and the VxWorks `ioctl` codes (`FIOSETOPTIONS`, `SIO_*`) are not defined: a
  serial driver that uses them is replaced, as `VxWorksDrv.VxWorksUartDriver` is.
- **Target paths** given to `open` are used as written: the raw API of the kernel is below
  F Prime's `Os::File`, and the sandbox of the run is [what `Os::File` and its file system
  and directory do](reference.md#files), not this.
- **A task of the shim that waits outside it** (on a POSIX semaphore, in `sleep`, on a
  socket) does what the report's [Time section](stepped-time.md) says of such code: in
  stepped time it keeps the work it was handed until it blocks in the shim again.
- **A task blocked with `intLock` held** keeps watchdog routines out, in stepped time the
  whole tick: it is an error on the target too.
- **Priority is not preemption.** In real time the host runs every task that is ready, at
  once. A higher-priority task does not stop a lower one, and a busy loop that waits for
  another task to change a variable spins on the host as on a multi-core target.
- **Switching to stepped time while running.** Ticks of the system clock granted in real
  time before `set_stepped` are still issued by the wall clock. A run that starts stepped
  (`time.mode: stepped`, `run --stepped`) has none.
- **Memory** of a kernel object is freed when it is deleted. A task's record is kept for
  the life of the process, so that `taskName` stays valid.
