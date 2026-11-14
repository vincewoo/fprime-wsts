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
   deployment, or a whole system, each non-obvious line commented with its reason.
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
