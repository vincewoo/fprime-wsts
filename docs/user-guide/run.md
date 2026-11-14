# 6. Run the Simulation

```bash
fprime-wsts run my-mission-sim/wsts/MyDeployment --gds-gui
```

`run` starts the built deployment with its bridge open, starts the GDS, and prints the
GUI's address (`http://127.0.0.1:5000/` by default). Stop it with ++ctrl+c++.

## Ways to run

=== "With the GDS in a browser"

    ```bash
    fprime-wsts run <dir> --gds-gui
    ```

    For a person, or a browser-automation agent. `--gds-gui-port` (default 5000) and
    `--gds-gui-addr` (default `127.0.0.1`) choose where the GUI is served.

=== "Headless GDS"

    ```bash
    fprime-wsts run <dir> --gds
    ```

    The same GDS without its web UI, for tests that use fprime-gds's API.

=== "For a fixed time, with a summary"

    ```bash
    fprime-wsts run <dir> --gds --duration 30 --json
    ```

    Runs for 30 seconds and prints one JSON summary. This is the development loop for
    scripts and agents: change something, run, read the summary, repeat.

=== "Without a GDS"

    ```bash
    fprime-wsts run <dir>
    ```

    Only the deployment and its bridge, for Python-only control.

Other options:

| Option | Use |
|---|---|
| `--scenario F`, `--scenario-mode stimulus\|responses` | Play a scenario or a recording from start-up ([Scenarios](scenarios.md)) |
| `--record F` | Record bus traffic and actions from start-up |
| `--bridge ADDR` | Override the manifest's bridge address |
| `--env NAME=VALUE` | An environment variable for the deployment, over the manifest's `run.env` |
| `--files-dir DIR` | Where relative scenario and recording paths resolve (default: the log directory) |
| `--gds-port P` | The port the deployment connects to (default 50000) |

## The JSON summary

```json
{
  "deployment": "MyDeployment",
  "exit_code": 0,
  "exited_early": false,
  "interrupted": false,
  "seconds": 30.04,
  "logs": "my-mission-sim/wsts/MyDeployment/wsts-logs",
  "gds_gui": null,
  "final_status": {
    "tick": 300, "paused": false, "rate": 1.0, "active_faults": 0,
    "scenario_state": "idle", "recording": false,
    "transactions": 4512, "bytes": 30881, "errors": 0, "fault_hits": 0,
    "endpoints": ["i2cBus", "sensorUart", "heaterPin"]
  }
}
```

What to look at first:

- **`exited_early`**: the deployment stopped on its own. The exit status of `run` is then
  non-zero.
- **`final_status.tick`**: how far simulated time got. A tick of 0 means nothing cycled the
  rate groups (a project timer that was kept, or the run is paused).
- **`final_status.errors`**: failed bus transactions. A steady count usually means an
  endpoint without a model, or a model that rejects what flight software sends.

Logs are in `<dir>/wsts-logs/`: `sim.log` for the deployment, `gds.log` for the GDS.

## When flight software asserts

The most common first-run failure is a component the simulation kept that touches
hardware in its set-up: it maps a device, opens a file under `/dev`, or checks an ID
register. `run` reads the F´ assert from the log and names the culprit:

```json
"assert": {
  "file": "Components/CameraDriver/CameraDriver.cpp",
  "line": 88,
  "instances": [{"instance": "MyDeployment.camera", "type": "Components.CameraDriver", "kept": true}]
}
```

and prints, on standard error, the `substitute` entry to add to the manifest. An instance
with `kept: true` is one the simulation did not replace: give it a surrogate, or a model
on the endpoint it reads. An assert in a flight device manager usually means it checks
something the device model does not provide yet.

## Operating it

While it runs, everything in [Control and fault injection](control.md) is available:

- from the GDS, as commands on `<Deployment>.wstsControl` (`SIM_PAUSE`, `FAULT_SET`,
  `INJECT_TEXT`, …);
- from Python, through the bridge at the manifest's `bridge` address;
- from a scenario file.

## You are done with the walkthrough

Your deployment runs on a workstation. From here:

- make the hardware behave: [device models](device-models.md),
  [register blocks](registers.md), [simulators](simulators.md);
- test it: [pytest](testing.md), [scenarios and replay](scenarios.md);
- grow it: [several computers](systems.md).
