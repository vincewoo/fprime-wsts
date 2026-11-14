# Testing with pytest

A simulated deployment is most useful as the target of automated tests: the real flight
code, commanded through the real ground interface, against hardware a test controls.
`fprime-wsts` ships a pytest plugin that starts the simulation for the test session and
hands each test a bridge client. fprime-gds's own `fprime_test_api` fixture sends
commands and asserts on events and telemetry.

## Run tests against a deployment

```bash
pytest tests -p fprime_wsts.pytest_plugin --wsts-deployment my-mission-sim/wsts/MyDeployment --wsts-gds --dictionary <path to the deployment's dictionary JSON>
```

| Option | Meaning |
|---|---|
| `--wsts-deployment DIR` | Start this generated deployment for the session. Without it, tests attach to a simulation already running |
| `--wsts-gds` | Also start a headless GDS, for `fprime_test_api` |
| `--wsts-bridge ADDR` | The bridge address (default: the deployment's, else `tcp:50100`) |
| `--wsts-system DIR` | Start a whole [system](systems.md) instead |
| `--wsts-env NAME=VALUE` | An environment variable for the deployment (repeatable) |
| `--dictionary FILE` | fprime-gds's option: the dictionary its test API uses |

The dictionary is the one the overlay build writes, under `build-artifacts/` of the
generated deployment (for a system, the merged one is written to
`wsts-logs/<System>Dictionary.json` when the system starts).

## Fixtures

| Fixture | Scope | What it is |
|---|---|---|
| `wsts` | test | A `WstsBridge`, subscribed. After each test, faults are cleared and the simulation resumed |
| `wsts_simulation` | session | The running simulation, or `None` when attaching to one started elsewhere |
| `wsts_system` | test | A `WstsSystem` for a system; `wsts_system["payload"]` is one element's bridge |
| `fprime_test_api` | session | fprime-gds's test API: `send_and_assert_command`, `assert_event`, `assert_telemetry`, … |

## A closed-loop test

The pattern: make the hardware misbehave through `wsts`, and assert on what flight
software reports through `fprime_test_api`.

```python title="tests/test_imu.py"
T = 10  # seconds allowed for an event or channel to arrive


def test_imu_recovers_from_a_bus_error(fprime_test_api, wsts):
    fprime_test_api.clear_histories()
    # I2C read error for the next five transactions, whichever device they are for
    wsts.fault_set("i2cBus", "error", arg=3, count=5)
    fprime_test_api.assert_event("MyDeployment.imu.ImuReadError", timeout=T)
    fprime_test_api.assert_event("MyDeployment.wstsControl.FaultExpired", timeout=T)
    fprime_test_api.assert_event("MyDeployment.imu.ImuRecovered", timeout=T)
    assert wsts.status()["active_faults"] == 0


def test_heater_cuts_out_on_overtemperature(fprime_test_api, wsts):
    fprime_test_api.send_and_assert_command("MyDeployment.heater.HEATER_SET", ["ON"], timeout=T)
    wsts.gpio_force("switchPin", True)                 # the switch trips
    fprime_test_api.assert_event("MyDeployment.heater.OverTemperature", timeout=T)
    assert wsts.gpio_get("heaterPin") is False


def test_sensor_query_goes_out_on_the_uart(fprime_test_api, wsts):
    fprime_test_api.send_and_assert_command("MyDeployment.sensor.SEND_QUERY", ["STATUS?"], timeout=T)
    wsts.wait_for(lambda m: m.get("rec") == "bs_tx" and m.get("endpoint") == "sensorUart")
    assert b"STATUS?\n" in wsts.bytes_sent("sensorUart")
```

Patterns that keep tests reliable:

- **Assert on outcomes, with timeouts.** Ticks pace the run, not the test; wait for the
  event or the channel value, not for a number of seconds.
- **Use `wsts.advance(n)`** to wait for `n` simulation ticks when a test needs time to pass.
- **Silence free-running models** before injecting a value they would overwrite
  (`model_set(..., "period", 0)`), and restore them after.
- **Clear histories** at the start of a test: an event from the previous test can arrive
  late, especially through a hub.

The repository's [end-to-end tests](https://github.com/vincewoo/fprime-wsts/tree/main/tests/e2e)
cover every feature against the example and are the best source of patterns, including
recording and replaying a run inside a test.

## In CI

The whole loop needs only Linux, CMake, a compiler and Python. A GitHub Actions job:

```yaml title=".github/workflows/sim.yml"
jobs:
  simulation:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v4
        with: {path: my-mission, submodules: true}
      - uses: actions/checkout@v4
        with: {repository: vincewoo/fprime-wsts, path: fprime-wsts, submodules: true}
      - uses: actions/setup-python@v5
        with: {python-version: "3.12"}
      - run: pip install -r fprime-wsts/lib/fprime/requirements.txt -e ./fprime-wsts/python pytest
      - run: fprime-wsts generate my-mission/sim/wsts.yaml
      - run: fprime-wsts check-host my-mission/sim/wsts/MyDeployment
      - run: cd my-mission/sim/wsts/MyDeployment && fprime-util generate && fprime-util build -j"$(nproc)"
      - run: |
          D=my-mission/sim/wsts/MyDeployment
          pytest my-mission/sim/tests -p fprime_wsts.pytest_plugin --wsts-deployment $D --wsts-gds \
            --dictionary $D/build-artifacts/Linux/MyDeployment/dict/MyDeploymentTopologyDictionary.json
      - if: failure()
        uses: actions/upload-artifact@v4
        with: {name: wsts-logs, path: my-mission/sim/wsts/MyDeployment/wsts-logs}
```

Adjust the paths to your layout and the dictionary path to what your build writes. Keep
`wsts-logs/` on failure: `sim.log` and `gds.log` usually explain it.

For a quick check without pytest, `fprime-wsts run <dir> --duration 30 --json` exits
non-zero if the deployment stops on its own, and its summary names the cause of an F´
assert.
