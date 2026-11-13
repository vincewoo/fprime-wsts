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
| Interrupts | Delivered on ordinary threads: a register block's on the thread that raises it (the tick thread for a model), one at a time | Code that relies on interrupt context (no blocking, masking) is not checked. Priorities, masking and nesting of the target's interrupts are not reproduced |
| Word size, byte order | Host's (64-bit little-endian on x86-64) | Code that depends on the target's representation behaves differently |
| Dictionary | Entries of replaced drivers are missing unless a mirror surrogate is used; WSTS entries are added | Ground procedures that use a replaced driver's telemetry need a mirror surrogate |
| File system | Host paths | Target-specific paths need to exist or be changed until path remapping is built |
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
recorded at. Within a tick, the order in which flight threads run is decided by the host
scheduler. Tests should therefore assert on outcomes (an event occurred, a channel reached
a value) rather than on exact interleavings. Deterministic stepped time is on the roadmap.

Pausing the simulation stops the flight rate groups, which also stops downlink: the
response to `SIM_PAUSE` arrives after the resume. Uplink does not depend on the rate
groups, so `SIM_RESUME` gets through.
