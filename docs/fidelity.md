# Fidelity

A simulated deployment is the flight software with specific things replaced. This page
says what those are. Each generated deployment also carries its own `wsts-report.md` with
the concrete list for that deployment.

## What is the same as flight

- All component code above the substitution seam: managers, applications, command and data
  handling, communications framing, sequencing.
- The topology: instance names, base IDs, connections, rate-group membership and order.
- The hand-written topology set-up code, compiled unchanged.
- Command, event and telemetry identifiers of every flight component that is not replaced.
- The ground interface: the deployment's own communication stack talks to the GDS.

## What is different

| Area | In simulation | Consequence |
|---|---|---|
| Drivers | Replaced at their port interface | Driver internals (register sequences, device files, interrupt handling, DMA) are not exercised |
| Register blocks (a register interface component replaced) | The device driver is flight code; a model answers its register reads and writes and raises its interrupts | Register semantics are exactly what the model implements, no more. Bus timing, caches, the order of accesses from several cores, and access widths other than 4 and 8 bytes are not reproduced. A read that fails gives flight all ones |
| DMA | Not modelled by the toolkit. A model may read and write a buffer whose address a driver wrote into a register, once the project supplies the address translation (`Wsts::Dma`) | A DMA engine that moves data by itself needs a model of its own. Cache maintenance calls do nothing useful on a host |
| Device behaviour | Supplied by models | Only as good as the model. Timing inside a transaction is not modelled |
| Operating system | Linux/POSIX OSAL instead of the target's | Priorities are advisory; no priority inheritance or preemption guarantees; stack sizes are not enforced the same way |
| Time | Host wall clock, optionally scaled | Not cycle-exact. A loaded host stretches ticks; the tick source does not burst to catch up |
| Stepped time (`--stepped`, experimental) | A tick is taken when flight software has finished the last one. Tasks run one at a time, in the order of their queues; time is simulated and stands still within a tick | A rate group that would overrun into the next cycle on the target does not: the overlap is removed, not reproduced, and a queue that overflows in flight because a consumer is slow may not overflow here. Priorities and preemption play no part. A rate group measures a cycle time of zero. Code that waits on the host, or on another task from inside a handler, cannot be stepped: the report's Time section lists what does ([below](#code-that-waits-on-the-host)) |
| Systems in lockstep (`run <system> --stepped`) | The elements are stepped together on one time base; what a link carries is delivered at the receiver's first tick after the time it was sent at | A link has a latency of up to one of the receiver's tick periods, which the wire does not have. Within an instant the elements run at once, each in its own process, and see nothing of each other's work until the next |
| Time source (`Svc.PosixTime`, `Svc.ChronoTime`, `Svc.OsTime`) | Replaced by `Wsts.SimTime` | The host's clock in real time, as before; simulated time in stepped time |
| Queue, task and raw time of the framework | The toolkit's (`Wsts_Os_Queue`, `Wsts_Os_Task`, `Wsts_Os_RawTime`): the framework's generic queue and the POSIX task and raw time, with a count and the stepped behaviour around them | In real time they behave as the ones they wrap. A task pinned to a core the host does not offer the process starts unpinned (one line on stderr says so) |
| Interrupts | Delivered on ordinary threads: a register block's on the thread that raises it (the tick thread for a model), one at a time | Code that relies on interrupt context (no blocking, masking) is not checked. Priorities, masking and nesting of the target's interrupts are not reproduced |
| Word size, byte order | Host's (64-bit little-endian on x86-64) | Code that depends on the target's representation behaves differently |
| Dictionary | Entries of replaced drivers are missing unless a mirror surrogate is used (channels the surrogate lacks are also dropped from the topology's telemetry packets); WSTS entries are added | Ground procedures that use a replaced driver's telemetry need a mirror surrogate |
| File system | Mapped into a sandbox of the run (`Os::File`, `Os::FileSystem`, `Os::Directory`; see "Files" in the reference) | What the target had as mounts is a directory tree made on demand; a path cannot name anything outside the sandbox. The raw I/O API of the VxWorks shim is the host's, as written |
| SpaceWire ([packet endpoints](user-guide/spacewire.md)) | The driver is replaced at its buffer interface; each packet is routed by its address bytes to the node at its logical address, and a node's reply reaches flight software during the send | The link's state, rate, time-codes and the time a packet takes are not modelled, nor routers beyond consuming path addresses. An error end of packet has no fault of its own. A client whose send path holds a lock its receive port takes would deadlock here, as it would not on the board, where replies come from the receive interrupt |
| Links between computers ([systems](systems.md)) | Each send delivered whole and immediately to the other element | Framing errors, partial reads, line rate and latency are not reproduced unless injected as faults. Connection set-up and loss of a socket link are not modelled |
| Time across computers | Each element has its own tick, paced by the host clock | Elements are not synchronised to each other; pause and rate changes reach them one after another |

## Choosing a seam

- Replace the **driver** (the default) when the device manager's logic is what you want to
  test. You write or reuse a device model.
- Replace the **device manager** when the device protocol is complex and not the subject of
  the test. You write one project surrogate; less flight code runs.
- Replace the **register interface component** when the device is an FPGA block or an SoC
  peripheral and its driver is what you want to test. You write a model of the block's
  registers; the driver, and everything above it, is flight code.
- A **mirror surrogate** costs the same as a driver surrogate plus keeping its model in
  step with the flight component, and gives a flight-identical dictionary.

## Repeatability

A scenario applies its actions at fixed ticks, and a recording replays at the ticks it was
recorded at. A merged recording (`--record-merged`) replays with each element's ticks
relative to its own replay start, so cross-element timing shifts by the difference in when
each element's recording started. In real time, the order in which flight threads run within
a tick is decided by the host scheduler: tests should assert on outcomes (an event occurred,
a channel reached a value) rather than on exact interleavings. In
[stepped time](user-guide/control.md#stepped-time-experimental) the order is fixed and two
runs record the same bytes, as long as nothing in the deployment waits on the host.

## Code that waits on the host

Stepped time sees what flight software does through the OSAL. Code that sleeps, reads the
host's clock, starts a thread of its own, waits for another thread or blocks on a device
works around it. Each generated `wsts-report.md` has a **Time** section that lists such
code in the components the deployment builds, read from their sources, with what each
costs in stepped time:

| What the code does | In stepped time |
|---|---|
| Sleeps on the wall clock | The same result, more slowly: the sleep holds the turn |
| Reads the host's clock, or the host's state | Two runs differ; time stamps and time-outs follow the host |
| Waits on a device or socket | What arrives lands in whichever tick is being taken |
| Starts a thread outside `Os::Task` | Its work is not counted and does not wait for the turn: two runs differ |
| Waits for another thread from a handler | The tick never finishes, and is reported stuck |

The list is read from text, so it shows calls whether or not a run reaches them, and does
not see a wait reached through another module. `fprime-wsts survey` gives the same list
before a manifest is written.

Pausing the simulation stops the flight rate groups, which also stops downlink: the
response to `SIM_PAUSE` arrives after the resume. Uplink does not depend on the rate
groups, so `SIM_RESUME` gets through.
