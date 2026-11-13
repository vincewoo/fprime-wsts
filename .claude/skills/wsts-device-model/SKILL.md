---
name: wsts-device-model
description: Author a hardware device model for an fprime-wsts simulated deployment from a datasheet, ICD or protocol spec, and check it against the real device by probing it over its raw interface (UART/serial, I2C, SPI, GPIO). Writes the Wsts C++ model (or an F´ simulator component), its manifest entry, the device spec with sources, a probe plan, hardware captures and a conformance check that replays the captures against the model. Use this whenever someone wants to simulate, emulate, model or stub a sensor, instrument, radio, FPGA block or other peripheral for fprime-wsts or an F´ simulation; turn a datasheet or ICD into a model; reverse-engineer or verify a device's serial/I2C/SPI protocol on a bench unit; or asks why a model disagrees with the real hardware. Also use it for "add a model for the X on /dev/ttyUSB0", "our sim needs the star tracker", or "write a Wsts::I2cDevice for this part", even if the word "skill" or "model" is not used.
---

# Authoring a WSTS device model from a datasheet and the real device

A WSTS device model is plain C++ that sits on a simulation bus endpoint behind a surrogate
and answers flight software as the hardware would. This skill turns three sources of truth
into one: the **document** (datasheet, ICD) says what the device should do, the **flight
code** says which parts of it matter and how they are used, and the **device itself** says
what it actually does. The model is right when it reproduces the device's captured
behaviour on everything flight software uses.

Read `docs/surrogate-guide.md` ("Device models", "Device simulators as F´ components", "A
register block") in the fprime-wsts checkout if you have not this session. The bus interfaces
are in `Wsts/Core/Bus.hpp`, and the stock models are in `Wsts/Core/Models.hpp`.

## What you deliver

In the simulation project (the directory with `wsts.yaml` / `<element>.yaml`, often `*-sim/`):

| File | Purpose |
|---|---|
| `devices/<device>/spec.md` | What the device does, every fact tagged with its source. Template: `assets/device-spec-template.md` |
| `devices/<device>/probe-plan.yaml` | The exchanges to run on hardware, each classified read / write / hazardous |
| `devices/<device>/captures/*.jsonl` | What the device did (WSTS recording format, written by `scripts/probe.py`) |
| `Models/<Device>Model.hpp/.cpp` (or a simulator component) | The model |
| manifest `models:` entry, `cmake` changes | So the generated deployment builds and attaches it |
| a short report to the user | What is modelled, what was verified on hardware, discrepancies, what is not modelled |

Follow the project's existing layout when it has one (for example, a `Models/` module
already added by `library.cmake` and listed in `cmake.cache`).

## Workflow

### 1. Orient

Find out, before reading the datasheet:

- **The simulation project and element manifest.** Is there a `models:` entry or a
  substitution for this device already?
- **The flight code that talks to the device.** That is the driver or device manager
  component and any library under it. Grep for the part number, the port or bus name, or
  the command strings. This code is the most useful spec you have: it tells you which
  commands, registers and timings matter, how flight frames them, what it checks
  (checksums, IDs, ranges) and what makes it assert or raise an error event. Model what
  flight uses first and completely, and the rest only if asked.
- **The seam and endpoint.** Which surrogate replaces the driver decides the interface
  kind (`bytestream`, `i2c`, `spi`, `gpio`, `register`) and the endpoint name, which is the
  instance's base name unless the manifest says otherwise. `wsts-report.md` in a generated
  deployment lists the substitutions. **Check that the endpoint exists.** A model can only
  be attached where a flight driver instance is replaced by a surrogate. A device that
  is new to the mission often has no driver in the flight topology yet: its model can still
  be written and checked, but not attached (step 8).
- **The hardware.** Where is the device connected (`/dev/ttyUSB0`, `/dev/i2c-1`,
  `/dev/spidev0.0`, `gpiochip0` line 17), with what line settings, and is it on this
  machine? A cloud session has no hardware. Then write the plan, give the user the
  `probe.py` command to run where the device is, and continue from the captures they
  send back.

### 2. Extract the spec

Read the document (for a PDF, use the pdf skill or `Read` with `pages`) and the flight
code, and fill in `spec.md` from the template. The template asks for line settings or bus
parameters, framing, every command or register flight uses, responses, unsolicited
output, timing, error behaviour, units and scaling, and power-up state.

Tag every fact with its source, for example `[DS p.14 tbl 6]`, `[ICD §4.3.2]`,
`[FSW SerialSensor.cpp:88]` or `[HW id.jsonl:3]`. When the sources disagree, record both.
The tags are what let a reviewer trust the model, and they show which parts no one has
checked. List open questions explicitly; probing should answer them.

### 3. Plan the probe, and get the user's consent for anything that is not a read

Write `probe-plan.yaml` (format and per-interface advice: `references/probing.md`). Give
each behaviour that flight uses, or that the spec is unsure of, one step: identification,
every query flight sends, a malformed or unknown command (devices differ most in error
handling), a listen period for unsolicited output, and timing-sensitive exchanges.

Classify every step by what it does to the **device**:

- `read`: no lasting effect. Queries, register reads, reading a pin, listening.
- `write`: changes state the device keeps but which is harmless and reversible, such as a
  mode, a rate, units or a setpoint.
- `hazardous`: resets, erases, writes flash or EEPROM, moves or heats anything,
  calibrates, changes the baud rate or address (you would lose the device), or anything
  whose effect you do not know.

A few short malformed or unknown commands are reads: they show the device's error
handling, which the model needs. **Stress is not a read.** Lines far longer than any
documented command, floods, rapid bursts, random bytes and out-of-spec framing test the
firmware's robustness, not its protocol. They can hang or crash a unit, and nobody asked
for a robustness test. Class them `hazardous`, or leave them out. `probe.py` refuses a
`read` step that sends more than `max_command_len` bytes (default 64), which is longer
than any documented command.

When unsure, choose the more dangerous class. The device may be flight hardware, a shared
engineering unit or something that can hurt someone. A wrong `read` label is the only way
this process can do real damage.

`probe.py` runs `read` steps and refuses the others unless told otherwise. Before you
pass `--allow`, **show the user the non-read steps**: name, bytes and what the datasheet
says they do. Ask which they approve. Then pass exactly those (`--allow <step>`), or
`--allow-class write` if they approve all writes. Hazardous steps are only ever allowed by
name. Record the approval in the plan (`approved:` with who and when). Approval covers
the steps the user saw. If you add or change a step, ask again. Without approval, model
those commands from the document and mark them unverified.

Always dry-run first (`probe.py plan.yaml --dry-run`) and check the bytes are what you
meant.

### 4. Probe and reconcile

```bash
python3 <skill>/scripts/probe.py devices/<device>/probe-plan.yaml --out devices/<device>/captures/<date>-<what>.jsonl
```

Read every response. Then update `spec.md`:

- Confirmed facts get a `[HW …]` tag.
- **Discrepancies** go into their own table: document vs observed, with the capture line.
  The model follows the hardware, because flight will meet the hardware. Still report each
  one: it may be document errata, a firmware revision or a damaged unit, and the user
  should decide.
- New questions may need another probe round (repeat steps 3–4). Use `--repeat N` on
  steps whose output varies (sensor noise, counters, timing) to see the spread.

### 5. Choose the model's form

| Situation | Form |
|---|---|
| Default: tests and flight software are the only things that talk to the device | **C++ model** implementing a `Wsts::*Device` interface (and `Wsts::Model` for parameters and ticks) in `Models/`. No IDs, no topology change |
| An FPGA block or SoC peripheral behind a register interface component | **C++ model** derived from `Wsts::RegisterFile` (`kind: register`) |
| A simple register-addressed I2C part, or a fixed periodic message | A **stock model** with `params` (`Wsts::RegisterMapI2c`, `Wsts::ScriptedByteStream`). No code |
| Operators must command or watch the simulator from the GDS (its own commands, events, telemetry, parameters), a simulator component exists already, or the device is several cooperating parts | **F´ simulator component** behind `Wsts.BusByteStreamDevice`. Keep the protocol in a plain C++ class the component owns, so `conform.py` can still test it |
| Exploring a protocol quickly, before committing to C++ | A **Python model** over the bridge (`WstsBridge.serve_i2c`, `serve_spi`, `serve_register`, or `subscribe` + `inject_bytes`). A prototype; port it to C++ |

Say which form you chose and why.

### 6. Write the model

Patterns and skeletons for each interface are in `references/model-patterns.md`: ASCII
and binary framed byte streams, streaming output, I2C register maps, SPI, GPIO and
register blocks. The points that most often go wrong:

- **Flight sends in pieces.** A byte stream model gets whatever each `send` carried.
  Buffer, and parse complete frames only.
- **Every method can be called from a different thread**: flight threads, the tick thread
  and bridge threads. Guard state with a mutex. Never call `reply.send`,
  `Bus::byteStreamInject`, `gpioSetInput` or `raise` while holding it.
- **Do not block.** The flight thread is inside its port call. Model a response delay by
  queuing the reply and sending it from `onTick`, not by sleeping.
- **Parameters are in engineering units** (`temp_c`, `accel_x` in g, `voltage_v`), so tests
  and `model_set` speak physics while the model handles the encoding (scale, offset,
  saturation, two's complement, byte order). Reject bad values by returning `false`.
  `set("endpoint", …)` arrives first at start-up for every model. Keep it if the model
  sends on its own.
- **Default-constructible, deterministic.** The generated set-up declares the model as a
  global. Seed any noise from a parameter.
- **Plain C++ only** (std library and `Wsts/Core`), with no F´ headers. `conform.py` builds
  the model outside F´.
- Model what flight uses faithfully, including **error responses** (bad checksum, unknown
  command, busy). Fault injection and off-nominal tests depend on them.

### 7. Check the model against the captures

```bash
python3 <skill>/scripts/conform.py --manifest <element>.yaml --model <name> devices/<device>/captures/*.jsonl
```

(or `--class Sim::X --header Models/X.hpp --kind bytestream|i2c|spi|gpio|register --endpoint <ep> --root <sim project>`
before the manifest entry exists). It compiles the model with `Wsts/Core` only (seconds,
with no F´ build), replays every flight-side record of the captures through the bus, and
compares what the model returns with what the device returned. Exit 0 means every
comparison passed. Each capture file is replayed on a fresh model, as its own session
with the device, so keep one session per file.

The device's physical state (temperature, attitude, counters) differs from the model's
defaults. Put it in the plan step's `model_params`, so the model is set to the same
condition before that step. For fields that cannot be reproduced (timestamps, sequence
counters, noise), use the narrowest `compare` that still tests the format, such as
`mask:` over just those bytes or a `regex:` for ASCII. Do not use `ignore` to make a
failure go away. Each relaxed comparison is a statement about the model's fidelity, so
list it in the report.

Iterate until it passes. A failure means the model is wrong, the capture is unusual
(re-probe) or the compare mode is wrong. Fix the one that is actually at fault.

### 8. Integrate and run with flight software

1. **Does the endpoint exist?** `fprime-wsts generate` rejects a model on an endpoint
   that no surrogate is bound to. If the flight topology has no driver instance for this
   device yet, do not add a live entry: it would break generation of the whole element.
   Put the entry in the manifest commented out, with a line saying what it waits for,
   and tell the user. Then skip to step 4 of this list.
2. Add the `models:` entry to the element manifest (kind, endpoint, `address` for I2C,
   `params`). Make sure the module is built (`library.cmake` / `cmake.cache`) and linked
   (`cmake.depends`).
3. **Regenerate.** `fprime-wsts generate <manifest or system manifest>`. A generated tree
   that is checked in, as `examples/mission-sim/wsts/` is, has to be committed along with
   the manifest change: CI runs `fprime-wsts generate --check` on it. Never edit the
   generated files by hand. If the generator cannot run here (FPP tools or `lib/fprime`
   missing), say plainly that the change is incomplete until someone regenerates, and give
   the command. Then build: after a regeneration, run only `fprime-util build` if the build
   directory exists.
4. `fprime-wsts run <generated dir> --gds --duration 30 --json`. Check that the run shows
   no assert, that the bus `errors` counter stayed at 0 for this endpoint, and that
   `wsts-logs/sim.log` shows the flight device manager's events and telemetry with
   plausible values (initialised, no timeouts, no checksum errors).
5. If the project has e2e tests (`tests/e2e/`), add one that sets a model parameter and
   sees the value in flight telemetry, as `test_payload.py::test_project_model_takes_engineering_units`
   does.

If steps 3–4 cannot run here (no toolchain or time), say so. Do not claim the
integration works without having run it.
3. `fprime-wsts run <generated dir> --gds --duration 30 --json`. Check that the run shows
   no assert, that the bus `errors` counter stayed at 0 for this endpoint, and that
   `wsts-logs/sim.log` shows the flight device manager's events and telemetry with
   plausible values (initialised, no timeouts, no checksum errors).
4. If the project has e2e tests (`tests/e2e/`), add one that sets a model parameter and
   sees the value in flight telemetry, as `test_payload.py::test_project_model_takes_engineering_units`
   does.

If steps 2–3 cannot run here (no toolchain or time), say so. Do not claim the
integration works without having run it.

### 9. Report

Tell the user, briefly:

- the form chosen and the files written;
- what was verified on hardware (conformance N/N, which steps), what comes from the
  document only, and what is not modelled;
- the discrepancy table, and every relaxed compare mode with its reason;
- any step that was not run because it was not approved;
- whether the manifest entry is live or waiting for a flight driver, and whether a
  regeneration is still owed before CI passes;
- how to re-probe and re-check (the two commands).

## Without hardware

When there is no device, or the user wants a model from the document only, do steps 1, 2,
5, 6 and 8. Write the probe plan anyway, so it is ready when hardware is. Mark everything
`[DS]`/`[ICD]`/`[FSW]` as unverified in the spec and the report. If flight-software
recordings exist (`record_start` from an earlier run, or bench logs), they are captures
too: `conform.py` reads any WSTS recording.

## Scripts

- `scripts/probe.py PLAN [--dry-run] [--out FILE] [--allow STEP]… [--allow-class write] [--only STEP]… [--repeat N] [--port DEV]`.
  Uses pyserial if installed, termios otherwise (UART); Linux i2c-dev (I2C_RDWR) and spidev
  ioctls (I2C, SPI); the libgpiod Python bindings or tools (GPIO). Exit 2 on a refused
  plan or a device error.
- `scripts/conform.py CAPTURES… (--manifest M --model N | --class C --header H --kind K --endpoint E --root R) [--plan P] [--param K=V]… [--json]`.
  `--plan` overrides `compare` and `model_params` by step name without re-probing. Needs a
  C++17 compiler and the fprime-wsts checkout (found automatically, or `--wsts-root`).
