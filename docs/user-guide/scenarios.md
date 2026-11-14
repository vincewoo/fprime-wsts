# Scenarios, Recording and Replay

A **scenario** is a file of actions, one per line, keyed on simulation ticks. A
**recording** is a log of every bus transaction and action in a run, in the same format,
so a recording loads as a scenario and replays the run.

## Write a scenario

One JSON object per line. `tick` counts from the scenario's start; blank lines and lines
starting with `#` are ignored.

```json title="scenarios/overtemp.jsonl"
# Sensor reports a rising temperature, then the over-temperature switch trips
{"tick": 4,  "op": "inject_bytes", "endpoint": "sensorUart", "text": "$TEMP,70.0\n"}
{"tick": 8,  "op": "inject_bytes", "endpoint": "sensorUart", "text": "$TEMP,95.5\n"}
{"tick": 10, "op": "gpio_force",   "endpoint": "switchPin",  "level": true}
{"tick": 16, "op": "gpio_force",   "endpoint": "switchPin",  "level": false}
{"tick": 20, "op": "fault_set",    "endpoint": "i2cBus", "kind": "error", "arg": 3, "ticks": 30}
{"tick": 60, "op": "log",          "text": "overtemp scenario done"}
```

Any [action](control.md#the-actions) can appear, with the same fields as on the bridge.

## Play it

=== "From start-up"

    ```bash
    fprime-wsts run <dir> --gds --scenario scenarios/overtemp.jsonl
    ```

=== "From Python"

    ```python
    sim.run_scenario("scenarios/overtemp.jsonl")      # load, start, wait until it completes
    ```

=== "From the GDS"

    Copy the file into the run's files directory (the log directory unless you passed
    `--files-dir`), then send `SCENARIO_LOAD` with `overtemp.jsonl` and `STIMULUS`, and
    `SCENARIO_START`. `wstsControl` reports `ScenarioStarted` and `ScenarioCompleted`.

Relative paths resolve against `WSTS_DIR`, which `fprime-wsts run` sets to the files
directory.

## Record a run

```bash
fprime-wsts run <dir> --gds --record run1.jsonl
```

or `sim.record_start("run1.jsonl")` … `sim.record_stop()` from Python. The recording has
action lines like a scenario, plus one line per bus transaction:

```json
{"rec": "i2c", "tick": 12, "endpoint": "i2cBus", "src": "flight", "addr": 104, "write": "3b", "read": "0000", "status": 3}
{"rec": "bs_rx", "tick": 14, "endpoint": "sensorUart", "src": "model", "data": "2454454d502c32312e350a"}
{"rec": "reg_rd", "tick": 4, "endpoint": "fpgaRegs", "src": "flight", "offset": "0xc", "width": 4, "read": "d7000000", "status": 0}
```

`rec` is one of `bs_tx`, `bs_rx`, `i2c`, `spi`, `gpio_out`, `gpio_in`, `reg_rd`, `reg_wr`,
`reg_irq`; `src` says where the traffic came from (`flight`, `model`, `link`, `inject`,
`replay`). A recording is also the quickest way to see what flight software actually
does on a bus.

## Replay it

A recording replays in one of two modes:

| Mode | What is replayed | Use it to |
|---|---|---|
| `stimulus` (default) | The recorded **actions**, against the live models | Re-run the same test inputs after changing flight code |
| `responses` | The actions **and** what the devices answered (I2C/SPI read data, bytes received, GPIO input changes, register reads and interrupts), with the models bypassed | Reproduce a run exactly, without the models; replay a capture from real hardware |

```bash
fprime-wsts run <dir> --gds --scenario run1.jsonl --scenario-mode responses
```

In `responses` mode recorded I2C and SPI responses are served in the order flight
software asks for them, separately for each endpoint and I2C address, and register reads
separately for each offset. When they run out, a read fails.

!!! info "Repeatability"

    Actions apply at fixed ticks, and replay is repeatable to tick granularity. Within a
    tick, the order flight threads run in is the host scheduler's, so tests should assert
    on outcomes (an event occurred, a channel reached a value) rather than exact
    interleavings. Deterministic stepped time is on the
    [roadmap](../design.md#roadmap).
