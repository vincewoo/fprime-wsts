---
name: wsts-configure
description: Guide a developer through configuring fprime-wsts for their own F´ flight project(s), checked out as sibling folders of the fprime-wsts checkout. Surveys the projects (deployments, hardware drivers, ground and hub links, entry points, tick sources, what will not build on a workstation), interviews the user about what only they know, writes the simulation project (a deployment manifest, or one manifest per computer plus wsts-system.yaml to stitch several deployments into a system), then generates, checks the host, builds and runs it, and works through each error until the simulated deployment runs. Use this whenever someone wants to simulate, convert, "WSTS-ify" or run on a workstation their F´ deployment or multi-computer system; set up wsts.yaml or wsts-system.yaml; bring up fprime-wsts on a new project; or is stuck getting a fprime-wsts generate/build/run to work for their project, even if they do not say "skill" or "manifest".
---

# Configuring fprime-wsts for a project beside the checkout

The user has fprime-wsts and one or more F´ flight projects side by side:

```
workspace/
  fprime-wsts/          # this checkout (the toolkit, and this skill)
  my-mission/           # flight project(s): read, never written
  my-mission-sim/       # the simulation project this skill creates
```

The goal is a simulated deployment, or a system of them, that **generates, builds and
runs** on this workstation. Behind that are a simulation project the user owns and
understands, and a short account of what is simulated and what is not. Your job is the
legwork: survey, draft, build and diagnose. The user's job is the decisions that need
knowledge of their hardware and mission. Ask them only those, and give a recommendation
with each.

Read `README.md` and `AGENTS.md` of the checkout if you have not this session. Read the
manifest reference (`docs/reference.md`, "Manifest") before writing a manifest, and
`docs/systems.md` before writing a system.

## Ground rules

- **The flight project is read-only.** Never edit, build in, or commit to it. When a fix
  needs a flight change (a `#ifndef WSTS_SIM` guard, a missing `#include`), propose it to
  the user with the reason, and look for a manifest key that avoids it first.
- **Never edit generated files** (`<sim project>/wsts/…`). Change the manifest or the
  simulation project's sources and regenerate.
- **Do not guess hardware facts.** I²C addresses, ID register values, entry point
  arguments and which component talks to a device come from flight code (read it) or from
  the user. Each guess becomes a silent fidelity error.
- **One fix at a time**, then rerun from the failed stage. Comment every non-obvious
  manifest line with its reason, as `examples/mission-sim/*.yaml` do.
- **Report what actually ran.** Do not say a deployment works until the run stage passed.
  If a stage could not run here (no toolchain, no time), say so and give the command.

## Scripts

Run them with the Python environment where fprime-wsts is installed. `<skill>` is this
skill's directory (`fprime-wsts/.claude/skills/wsts-configure`).

| Script | What it does |
|---|---|
| `scripts/survey.py [DIR…] [--deployment NAME] [--starter-dir DIR] [--json] [--no-trial]` | Finds F´ projects (default: the checkout's siblings), their deployments and existing WSTS manifests. For each deployment: hardware instances and what fprime-wsts will do with each, byte stream drivers by role (ground, hub, device), register interfaces, project components whose sources open devices, tick and time sources, `main()` or entry points, ID range, command splitters, endpoint collisions, and a trial generation with a minimal manifest. `--starter-dir` writes starter manifests with every decision marked `TODO`, without overwriting anything. About 10 s per deployment |
| `scripts/build_loop.py MANIFEST [--stages generate,host,build,run] [--run-seconds 20] [--fresh] [--gds] [--keep-going] [--json]` | One pass through generate, `check-host`, `fprime-util generate`/`build` per element, and a short `fprime-wsts run`. Stops at the first failing stage and prints the lines that matter: the generator error, compiler, linker or CMake errors, an assert, an element that exited, links down, bus errors, `WARNING_HI` events. Full logs in `<generated dir>/build-wsts-loop/`. A first build takes several minutes per element. Later ones are incremental |

## Workflow

### 0. Preflight

Check, and fix what you can before involving the user:

1. `fprime-wsts --version` works in the active environment. If not, install it:
   `pip install -r lib/fprime/requirements.txt -e ./python`, in a virtual environment.
2. The checkout's submodules are present: `lib/fprime`, `lib/fprime-vxworks`, and
   `lib/fprime`'s `googletest`
   (`git -C lib/fprime submodule update --init --depth 1 googletest`). Without
   googletest, host checks differ from CI.
3. CMake, a C++ compiler and Java (for the FPP tools) are installed. If the FPP tools
   complain about Java, `unset JAVA_HOME`.

### 1. Survey

```bash
python3 <skill>/scripts/survey.py
```

Show the user a short summary, not the raw output: the projects and deployments found,
each deployment's toolchain and whether it has a `main()`, what the catalog replaces
automatically, the items marked **decide**, warnings from the trial generation, and any
existing WSTS manifests. If manifests exist already, offer to continue from them, and
start from step 5 with `build_loop.py`.

Survey problems to settle first: a flight project whose submodules are not checked out,
and a framework version different from the toolkit's (`references/troubleshooting.md`,
"Before generating").

### 2. Scope and location

Decide with the user ([decisions §1–2](references/decisions.md#1-scope)):

- **Which deployments**, and whether they form **a system**. The survey's hubs, hub link
  ends and command splitters are the evidence. Deployments joined by a hub, UART or socket
  in flight are a system.
- **Where the simulation project goes.** Recommend `../<flight project>-sim/`, a sibling,
  in its own repository or directory.

For a system that is new to the user, offer to bring up the elements one at a time.
Generate the whole system, then build and run one element alone
(`fprime-wsts run <system dir>/<element>`). Its link end is simulated and stays down, as
if the other computer were silent. A deployment simulated on its own, outside a system,
needs its hub driver substituted the same way
([decisions §5](references/decisions.md#5-hardware-instances)).

### 3. Draft the manifests

```bash
python3 <skill>/scripts/survey.py --starter-dir ../my-mission-sim [--deployment A --deployment B]
```

This writes `wsts.yaml` for one deployment, or `<element>.yaml` per deployment plus
`wsts-system.yaml`. Paths are relative to the simulation project. Then settle each
`TODO`, using `references/decisions.md`:

| Starter TODO / survey item | Decision |
|---|---|
| `keep:` ground link | [§3](references/decisions.md#3-the-ground-link) |
| `main:` entry points | [§4](references/decisions.md#4-how-the-deployment-starts) |
| `decide:` instances | [§5](references/decisions.md#5-hardware-instances), [§7](references/decisions.md#7-register-interfaces) |
| tick or time source not handled | [§6](references/decisions.md#6-ticks-and-time) |
| `models:` per endpoint | [§8](references/decisions.md#8-devices-behind-the-buses) |
| trial generation warnings (SDK, headers, root CMake) | [§9](references/decisions.md#9-what-does-not-build-on-the-host) |
| `wsts-system.yaml`: links, ground, `ids` | [§10](references/decisions.md#10-systems) |

**How to ask.** Read the flight code first (driver set-up lines are in the survey output;
device managers say what they check at start-up). Then ask the questions that remain in
one batch, each with your recommended answer, so the user can confirm most of them at
once. Typical ones: which components talk to hardware that the survey did not flag, the
arguments of an entry point, which devices matter for a first run, which byte stream
drivers pair up between computers.

Add the rest of the simulation project: `.gitignore` (`assets/sim-project/gitignore`),
and a `host.cmake` and `library.cmake` only when needed
(`assets/sim-project/`). Write `README.md` last (step 7).

### 4. First bring-up scope

Aim for the smallest configuration that runs. Leave models out where the user does not
need them yet, and attach stock models where the flight code makes the values obvious (an
ID register, a `WHO_AM_I`). Tell the user what flight software will see on each endpoint
left without a model ([decisions §8](references/decisions.md#8-devices-behind-the-buses)).
Devices with real protocols go to the **`wsts-device-model`** skill later. Do not write
them in this pass unless the user asks.

### 5. Generate, build, run, fix

```bash
python3 <skill>/scripts/build_loop.py ../my-mission-sim/wsts-system.yaml     # or wsts.yaml
```

Run it in the background for a first build (several minutes per element), and work on
other things meanwhile. For each failure:

1. Read the excerpt, and the log if the excerpt is not enough. Find the **first** error.
2. Look it up in `references/troubleshooting.md` under its stage. Generator errors usually
   name the key that fixes them.
3. Decide where the fix goes: the manifest, a simulation project file, the flight project
   (the user's), or fprime-wsts (report it). If the fix needs a fact only the user has
   (a header's values, whether a stub is good enough), ask.
4. Apply one fix, and rerun from that stage
   (`--stages build,run` after a build fix, the default after a manifest change: the build
   is incremental). `--fresh` only when `cmake.cache` or configure options changed.

The host stage does not stop the loop: `check-host` reads text and can be wrong, so the
build confirms or refutes each item. Settle the items it reports anyway.

Stop and talk to the user when the same stage fails three times for different reasons, or
when the remaining fix is a flight change or a toolkit limitation.

### 6. Smoke check

When the run stage passes, check its findings and the logs (`wsts-logs/[<element>/]sim.log`):

- no assert, no element exited, `tick` advanced;
- for a system, `links_up` equals `links` on every element;
- bus `errors` is 0, or every error is explained by an endpoint deliberately left without a
  model;
- the `WARNING_HI` events are understood (often a device manager that found no device).

Then offer a run with the GDS (`--gds`, or `fprime-wsts run <dir> --gds-gui` for the
browser UI), and point to `tests/e2e/` for writing the project's first closed-loop test
with the `wsts`/`wsts_system` fixtures.

### 7. Hand off

Write the simulation project's `README.md` from `assets/sim-project/README.md`: the files,
the generate/build/run commands (exact, for this layout), what is simulated (substitutions
and models), and what is not. Then tell the user, briefly:

- what was configured, and the stage the configuration reached (ran, built, generated);
- every decision you made with a default instead of their answer, so they can revisit it;
- what flight software sees on each endpoint without a model, and the warnings that remain;
- any flight-project change you proposed and did not make;
- next steps: device models (`wsts-device-model`), scenarios, tests, CI with
  `fprime-wsts generate --check` if they check the generated tree in;
- the commands to rerun: `build_loop.py <manifest>`, or the plain `fprime-wsts generate` /
  `fprime-util build` / `fprime-wsts run` lines.

Commit only if the user asks, and only in the simulation project's repository.

## Reference files

- `references/decisions.md`: every decision, what the survey shows, what to ask, and which key it becomes
- `references/troubleshooting.md`: symptom, cause and fix, by stage
- `assets/sim-project/`: `library.cmake`, `host.cmake`, `gitignore`, `README.md` templates
- The worked example: `examples/mission` (flight) and `examples/mission-sim` (simulation) in
  the checkout. Its manifests show most keys with comments that explain them
