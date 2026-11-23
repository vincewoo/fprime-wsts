# Configuring with an Agent

The repository has [agent skills](https://github.com/vincewoo/fprime-wsts/tree/main/.claude/skills)
for Claude Code that do the legwork of this guide for you: the survey, the manifests, and
the generate, build and run loop. They ask you only what needs knowledge of your hardware
and mission, each time with a recommendation.

## Set up

Check out your flight project beside the toolkit, and start the agent in the `fprime-wsts`
checkout, where it finds the skills:

```
work/
  fprime-wsts/        # start the agent here
  my-mission/         # your flight project: read, never written
```

Then ask, in your own words:

> Set up a WSTS simulation of ../my-mission

## `wsts-configure`

Configures, builds and runs a simulation of a flight project checked out beside this one:
one deployment, or a system of several.

1. **Survey.** A script finds your deployments, their hardware instances and what the
   toolkit will do with each, byte stream drivers by role (ground, hub, device), register
   interfaces, tick and time sources, entry points, ID ranges and command splitters, and
   runs a trial generation.
2. **Interview.** It asks what only you know: which components reach hardware, how the
   deployment starts, which computers are linked, which devices matter most.
3. **Write** a simulation project beside yours (`../my-mission-sim`) with a manifest per
   deployment, or a whole system, each non-obvious line commented with its reason. The
   toolkit is pinned in it as a git submodule, `lib/fprime-wsts`.
4. **Build loop.** Generate, check the host, build and run; stop at the first failure,
   fix one thing, and go again, until the simulated deployment runs.

Its ground rules match this guide: the flight project is read-only (a fix that needs a
flight change is proposed to you, with the reason), generated files are never edited, and
hardware facts such as I2C addresses come from flight code or from you, never guessed.

## `wsts-device-model`

Writes a device model from a datasheet, ICD or protocol spec, and checks it against the
real device:

1. Reads the flight code that talks to the device, to learn what matters.
2. Writes a device spec with a source for every fact, and a probe plan that classifies each
   exchange as read, write or hazardous.
3. Probes a bench unit over its raw interface (serial, I2C, SPI, GPIO) and captures the
   answers in the WSTS recording format.
4. Writes the model (C++, or an F´ simulator component) and its manifest entry.
5. Replays the captures against the model as a conformance check, and reports what is
   modelled, what was verified on hardware and where the two disagree.

Ask for it in words like "add a model for the star tracker on /dev/ttyUSB0" or "write a
Wsts::I2cDevice for this part".

## The MCP server

`fprime-wsts mcp` gives an agent the simulation as tools, over the
[Model Context Protocol](https://modelcontextprotocol.io). It is a local process on stdio:
the agent's client starts it and it ends with the client. It opens no port of its own, and
everything it reaches is on the same machine: the runs it starts, the bridges of their
elements, and their GDS, which it joins as a client the way fprime-gds's test API does.

Install it once, in the environment that has the toolkit (Python 3.10 or later):

```bash
pip install -e './python[mcp]'
```

This installs `mcp` 1.x (`mcp>=1.2,<2`). The server does not run on `mcp` 2.x yet, for two
reasons:

- It is written against the 1.x API. `mcp` 2.0 renamed `FastMCP`, which the server is built
  on, to `MCPServer`, and changed other APIs
  ([migration guide](https://py.sdk.modelcontextprotocol.io/v2/migration/)). With 2.x
  installed, `fprime-wsts mcp` stops with an error that says so.
- `mcp` 2.x needs `httpx2`, which requires `idna>=3.18`, and F´'s
  `lib/fprime/requirements.txt` pins `idna==3.15`: the two cannot be installed together
  at the versions F´ pins, so pip would settle on a 1.x anyway.

Moving to 2.x means porting the server to `MCPServer` and waiting for an F´ release whose
pins allow `idna>=3.18`.

The repository's `.mcp.json` registers it for Claude Code started in the checkout, which
asks once whether to use it. Elsewhere, for example in a simulation project:

```bash
claude mcp add wsts -- fprime-wsts mcp
```

Start the agent in the environment where `fprime-wsts` is on the `PATH` (the virtual
environment activated). Relative paths in tool calls are taken from the directory the
client starts the server in (`--cwd` to choose another).

| Tools | What they do |
|---|---|
| `build`, `run_start`, `run_stop`, `runs_list` | Build a generated deployment or system; start it on free ports with a headless GDS (`fprime-wsts run --auto-ports`), stop it in order and return its summary (exit codes, final ticks, an F´ assert); list the runs the server knows |
| `snapshot` (and `run_start` with `journal`, `from_snapshot`) | Snapshot a run that keeps a journal, and start runs from the snapshot: each replays it, is checked, and is held at its tick ([Snapshots](snapshots.md)) |
| `status`, `pause`, `resume`, `set_rate`, `step`, `wait_quiescent` | Where a run is (with `bus=true`, the traffic of each endpoint and what nothing answered), and its time |
| `fault_set`, `fault_clear`, `inject`, `gpio`, `register_set`, `raise_irq`, `model_set`, `scenario_run`, `action` | [Actions](../reference.md#actions) through the bridge; `action` sends any of them with its fields |
| `bus_records` | What flight software sent and received on each endpoint, since the first call |
| `dictionary`, `send_command`, `wait_event`, `recent_events`, `read_telemetry` | The flight dictionary, and commands, events and telemetry through the GDS |
| `tail_log`, `last_assert` | The logs of a run (`sim`, `gds`, `models`, `build`), and the F´ assert a deployment stopped on, with the instance to substitute |

A run is named by its generated directory. Once the server knows one run, `run` can be
left out. A run started elsewhere (`fprime-wsts run`, a test session) is found through the
`wsts-run.json` in its log directory, so the server can attach to one started with
`--auto-ports`. The server stops the runs it started when it exits, and leaves the others.

The tools deal with what otherwise surprises an agent:

- **Names.** Flight names are qualified by subtopology (`CdhCore.cmdDisp.CMD_NO_OP`). A tool
  takes the full name or a unique end of it (`heater.HEATER_SET`), and refuses an ambiguous
  one with the candidates: in a system, both elements have a `cmdDisp.CMD_NO_OP`.
- **Commands.** `send_command` returns once the dispatcher reports the command completed or
  failed, with the events seen meanwhile.
- **Telemetry** is downlinked about once a second, with the latest value only:
  `read_telemetry` waits for a sample sent after the call unless `fresh=false`.
- **Late events.** Events of an element behind a hub arrive late. `wait_event` looks back
  to the last command or action the server sent, so an event that arrived before the call
  is still found.
- **Systems.** Faults take a link name or `<element>.<endpoint>`. `step` takes an element,
  because elements keep their own ticks. A system in lockstep is conducted by its own
  `fprime-wsts run`: it can be paused and resumed, but not stepped through the server.

Ask the agent in your own words, for example "start the Mission system, switch the heater
on and check that the pin goes high", or "drop the hub link for five seconds and tell me
which Payload events Cdh misses".

## Agents in the development loop

Any agent can use a simulation the way the skills do:

```bash
fprime-wsts run <generated dir> --gds --duration 30 --json
```

starts the deployment and a headless GDS, runs for 30 seconds and prints a JSON summary:
exit code, final tick, bus counters, log directory, and for an F´ assert the file, line
and instance to substitute. With `--gds-gui` a browser-automation agent can operate the
GDS's web UI. For closed-loop checks, the [pytest plugin](testing.md) gives an agent
the same faults, injection and assertions as a test.

The repository's
[AGENTS.md](https://github.com/vincewoo/fprime-wsts/blob/main/AGENTS.md) is written for
agents working on or with the toolkit.
