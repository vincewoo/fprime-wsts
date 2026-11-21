# Purposes, and the gaps that matter for each

The inventory's gaps come with a severity hint that ignores purpose. Re-rank them with the
table for the user's purpose: **raises** means a gap in that area usually blocks or weakens
the purpose, **lowers** means it usually does not matter for it. The questions settle the
cases in between. A simulation used for several purposes gets the strictest ranking of
each gap.

## Rehearsing operations and ground procedures

Operators run procedures from the GDS: commands, expected events, telemetry limits.

| Raises | Lowers |
|---|---|
| Dictionary items missing (a procedure that sends a replaced driver's command, or waits for its event, fails here). Endpoints with nothing answering behind telemetry the procedure checks (a constant, or no update). Stock models where the procedure expects a value to move (`ScriptedByteStream` sends the same text; a `RegisterMapI2c` register is fixed unless it is a signal). Time: real time is what operators expect; stepped time is not | Driver internals, interrupt timing, OS scheduling. Repeatability |

Questions: which procedures, and which commands, events and channels do they name (check
each against the dictionary's missing items)? Which telemetry must change during the
procedure, and what drives it in the simulation? Do operators need to inject faults from
the GDS (the `SIM_*` commands of `wstsControl`) or does a test lead do it from the bridge?
Payload telemetry arriving through a hub is late by up to a downlink period: does a
procedure time a response?

## Testing fault detection and response (FDIR)

Flight software must notice a failure and respond: switch off, retry, safe mode.

| Raises | Lowers |
|---|---|
| Endpoints whose failure triggers the response: a stock model or nothing cannot fail the way the device does, and a fault can only be injected at the bus (`fault_set`: error, drop, corrupt, stuck) or by forcing a pin. Whether the trigger is emergent (a model of the physics: heater on, temperature rises, switch trips) or scripted (a test forces the switch). Models that never report the device's own error states (a status register's fault bit, an error reply). Events and telemetry the response is verified by (dictionary items missing) | Exact timing of the response, unless the requirement is a deadline (then: real time and host load, or stepped time) |

Questions: for each fault in the campaign, what triggers it on the target, and what
triggers it here (a bus fault, a forced pin, a model parameter, nothing)? Is a scripted
trigger acceptable, or must the condition arise from the simulated device? What tells the
test that the response happened (an event, a pin level, a channel), and is it in the
dictionary and observable from the bridge (`gpio_get`, `bus_records`)?

## Regression tests in CI

The same tests on every change: what matters is that they run, pass for the right reason,
and fail when behaviour changes.

| Raises | Lowers |
|---|---|
| Repeatability: in real time the order of threads within a tick is the host's, so tests must assert outcomes; stepped time makes runs identical, as long as nothing waits on the host (the inventory's "waits on the host"). Kept hardware drivers (fail or behave differently on a CI runner). Endpoints that flight software uses with nothing answering (error paths run instead of the nominal ones). Host requirements of the build (`check-host` on the runner) | Realism of device values, as long as tests assert on what the models give |

Questions: which code paths do the tests mean to cover, and does each device that path
reads have a model that gives it the nominal answer? Which tests are timing-sensitive? Is
the generated tree checked in and checked (`generate --check`) in CI?

## Developing a driver, an FPGA interface or a register map

The code under work talks to the hardware interface itself.

| Raises | Lowers |
|---|---|
| The seam: a driver replaced at its port interface is **not exercised** at all; only a register interface replacement (`register_blocks` in the report) runs the flight driver against a model. The register model: `Wsts::RegisterFile` holds values with no side effects (no clear-on-read, no write-one-to-clear, no busy bits, no interrupts unless raised by hand). DMA (`docs/fidelity.md`). Interrupt context and access widths | Dictionary, operator-facing telemetry |

Questions: which driver is under development, and is it running as flight code here (it
is when its device is behind a register block or a bus endpoint; it is not when the driver
itself is replaced)? Which register behaviours does the driver depend on (clear-on-read,
status polling, interrupt acknowledge), and does the model implement each? Is there a
bench unit to capture from (`wsts-device-model`)?

## Integrating several computers (a system)

The elements' traffic over their links, and how they behave together.

| Raises | Lowers |
|---|---|
| Links are ideal: each send arrives whole and at once, with no line rate, framing errors or partial reads unless injected as faults; a socket link's loss is not modelled. Elements keep their own time; in real time they are not synchronised, and a pause reaches them one after another; lockstep adds up to one tick of link latency. An element left out of the system (its link end silent) | Device fidelity of each element, except where a device's data crosses the link |

Questions: what does the integration test check (message flow, timing between elements,
recovery from a link drop)? Are link faults part of it (`fault_set` on a link by name)?
Does any requirement depend on latency or bandwidth on the link?

## Training and demonstrations

People learn the flight software's behaviour and the ground system.

| Raises | Lowers |
|---|---|
| What the trainees see: telemetry that does not move, missing dictionary items, events that never come. Being able to stage faults on demand | Nearly everything below the user interface |

Questions: which scenarios will be shown, and can each be staged (a scenario file, a
fault, a model parameter)? Is the GDS browser UI needed (`run --gds-gui`)?

## General limitations

Some differences hold for every simulated deployment and are listed in `docs/fidelity.md`:
the OS (Linux OSAL: priorities advisory, no preemption guarantees), word size and byte
order of the host, time (host clock, not cycle-exact), the file system sandbox. Mention
them once in the review, under general limitations, and raise one only when the purpose
depends on it (a deadline requirement depends on time; endian-sensitive code on byte
order).
