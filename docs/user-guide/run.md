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

The GDS is started with its default framing. A deployment that frames its ground link
otherwise (the F´ protocol rather than CCSDS space packets, say) says what to add to the
`fprime-gds` command line in its manifest:

```yaml
run:
  gds_args: [--framing-selection, fprime]
```

In a [system](systems.md) the GDS is the ground element's, so it is that element's
manifest that says so.

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
    "endpoints": ["i2cBus", "sensorUart", "heaterPin"],
    "bus_traffic": {
      "i2cBus": {"transactions": 3300, "to_device": 3300, "to_flight": 0, "bytes_to_device": 3300,
                 "bytes_to_flight": 19800, "errors": 300, "unanswered": 300, "fault_hits": 0, "last_tick": 300,
                 "addresses": {"0x68": {"transactions": 3000, "errors": 0, "unanswered": 0},
                               "0x48": {"transactions": 300, "errors": 300, "unanswered": 300}}},
      "...": {}
    }
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
- **`final_status.bus_traffic`**: the same, per endpoint and per I2C address or register
  offset. `unanswered` is what nothing answered: above, flight software polls a device at
  0x48 that has no model. Each first one is also the `UnansweredTraffic` event in the GDS.

Logs are in `<dir>/wsts-logs/`: `sim.log` for the deployment, `gds.log` for the GDS.

## Several runs on one machine

The bridge, the GDS link and the GDS GUI have fixed ports by default (the manifest's
`bridge`, 50000, 5000), so a second run on the same machine, or two CI jobs on one runner,
would collide. `--auto-ports` takes free ports instead and gives the GDS sockets of its own:

```bash
fprime-wsts run my-mission-sim/wsts/MyDeployment --gds --auto-ports
```

The addresses are printed at start-up, are in the `--json` summary (`bridge`, `gds_port`,
`gds_gui`), and are in `wsts-logs/wsts-run.json` while the run is up. Use a `--log-dir` per
run when two runs share one generated directory.

## Looking in from another terminal

```bash
fprime-wsts status my-mission-sim/wsts/MyDeployment
```

```
MyDeployment: running at tick 412, rate 1
  bus: 6180 transaction(s), 42310 byte(s), 0 error(s); faults: 0 active, 0 hit(s)
  scenario: idle; recording: no
  endpoints: i2cBus, sensorUart, heaterPin
```

`--bus` adds a line per endpoint, and where nothing answered:

```
  endpoint    from flight  to flight  bytes out  bytes in  errors  unanswered  fault hits  last tick
  heaterPin             2          0          0         0       0           0           0        380
  i2cBus             4532          0       4532     27192     412         412           0        412
  sensorUart            0         41          0      1066       0           0           0        400
  nothing answered on i2cBus at: 0x48 (412)
```

`fprime-wsts ctl` sends any [action](control.md) the same way, with no Python and no GDS:

```bash
fprime-wsts ctl my-mission-sim/wsts/MyDeployment fault_set endpoint=i2cBus kind=error arg=3 count=5
```

```bash
fprime-wsts ctl my-mission-sim/wsts/MyDeployment inject_bytes endpoint=sensorUart 'text=$TEMP,48.0\n'
```

Both find the run through its directory, whichever ports it uses. For a system, give the
system directory; `--element payload` picks one element.

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
