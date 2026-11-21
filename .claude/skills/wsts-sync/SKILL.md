---
name: wsts-sync
description: Bring an existing fprime-wsts simulation project back in step with what moved under it. The flight project may have new commits (drivers added, instances renamed or removed, new modules, dictionary changes), the pinned toolkit (lib/fprime-wsts) may be due for a bump, or the generated tree may be stale. Finds what would change before anything is written, settles each change in the manifests (never in flight code or the generated tree), regenerates, rebuilds, runs and retests, and reports what changed for the simulation. Can also set up CI for the simulation project. Use this whenever someone says the flight software moved on, they pulled or merged flight changes, `fprime-wsts generate --check` fails, a simulation that used to generate or build no longer does after a flight update, they want to update or bump fprime-wsts in their simulation project, or they ask whether their simulation is up to date, even if they do not say "sync".
---

# Keeping a simulation project in step

A simulation project is generated from three things that move on their own:

```
workspace/
  my-mission/            # flight project: new commits arrive here (read, never written)
  my-mission-sim/        # the simulation project: manifests, models, generated wsts/
    lib/fprime-wsts/     # the toolkit, pinned as a submodule: bumped on purpose
```

The goal is a simulation project that generates, builds and runs against the flight
project as it is now, with every change in what is simulated explained to the user. Your
job is to find what moved, settle the mechanical changes, and bring the user the
decisions that need knowledge of their hardware: a new device, a removed one, a changed
interface. Give a recommendation with each.

If the user has no simulation project yet, this is the wrong skill: use `wsts-configure`.

Read `AGENTS.md` of the checkout if you have not this session, and the `wsts-configure`
skill's `references/decisions.md` for the decisions this work reopens.

## Ground rules

- **The flight project is read-only.** A change that breaks the simulation is settled in
  the manifests or the simulation project's sources. A fix that needs flight code is
  proposed to the user, with the reason.
- **Never edit the generated tree** (`<sim project>/wsts/…`). Change a manifest and
  regenerate.
- **Look before writing.** Run `scripts/drift.py` first: it generates into a scratch
  directory and writes nothing. Regenerate only once you know what will change.
- **A rename is not a removal.** When the manifest names an instance flight no longer has,
  read the flight commits before dropping anything: the drift report suggests the new name
  when the component is the same.
- **Bump the toolkit on its own.** Settle flight drift with the toolkit as pinned, then
  bump it in a separate step, so each change in the generated tree has one cause.
- **Report what actually ran.** Do not say the simulation is back in step until it has
  built and run; if a stage could not run here, say so and give the command.

## Scripts

Run them with the Python environment where fprime-wsts is installed. `<skill>` is this
skill's directory (`fprime-wsts/.claude/skills/wsts-sync`).

| Script | What it does |
|---|---|
| `scripts/drift.py [MANIFEST or SIM PROJECT] [--json]` | Writes nothing. Runs `fprime-wsts generate --check --json` (with the toolkit the project pins) and says whether generating again would change the tree, and if generation fails now, why, with the likely new name of an instance the manifest names and flight no longer has. Compares the new reports with those on disk by area: substitutions (and renames), hardware drivers kept, endpoints with nothing answering, models, links, register blocks, simulator components, dictionary items missing, time and what waits on the host, capacity, warnings, modules and their BUILD_TESTING variants, host requirements. Each change that needs a decision says which. Also lists the flight commits since the tree was generated, and where the pinned toolkit stands against this checkout. Exit status 0 when up to date, 1 when not, 2 on bad usage. About 15 s per element |
| `../wsts-configure/scripts/build_loop.py MANIFEST [--stages …]` | The `wsts-configure` skill's loop: generate, check the host, build, run, stopping at the first failure with the lines that matter |

## Workflow

### 0. Preflight

1. `fprime-wsts --version` works in the active environment (else
   `pip install -r lib/fprime/requirements.txt -e ./python` in a virtual environment).
2. Find the simulation project and its manifest: `wsts-system.yaml`, `wsts.yaml`, or the
   one the user names. Its `project:` keys name the flight project.
3. `git -C <sim project> status`: if it has uncommitted work, tell the user, and do not
   mix it into this change. Do the same for the flight project: uncommitted flight
   changes are part of what you are syncing to, and the user should know that.
4. The pinned toolkit is checked out (`git -C <sim project> submodule status`). If it is
   not: `git -C <sim project> submodule update --init --recursive lib/fprime-wsts`.

### 1. Find what moved

```bash
python3 <skill>/scripts/drift.py <sim project>
```

Show the user a short summary, not the raw table:

- **Up to date** (exit status 0): say so, with the flight commit and toolkit commit it is
  in step with, and stop unless they asked for a toolkit bump or CI.
- **Generation fails**: the generator's message and the hints. Go to step 2.
- **Changes**: group them as *mechanical* (renames, files that follow flight sources,
  telemetry added by WSTS, fidelity notes) and *decisions* (the `Decide` column). Lead
  with the decisions.

Read the flight commits it lists (`git -C <flight> show --stat <commit>`, then the
diffs of topology and component files) so that you can say *why* each change happened.

### 2. Settle each change

Work through generation failures first, one at a time, then the decisions.
`references/changes.md` has each kind of change, what it means and where it is settled.
The common ones:

| Drift | Where it is settled |
|---|---|
| The manifest names an instance flight no longer has | A rename: the new name in every manifest key that names it (`substitute`, `keep`, `models[].endpoint`, `devices[].endpoint`, `links` in `wsts-system.yaml`, comments). A removal: drop those keys, and the model sources only if nothing else uses them |
| New hardware instance replaced from the catalog | Whether its endpoint needs a model ([decisions §8](../wsts-configure/references/decisions.md#8-devices-behind-the-buses)); leaving it unanswered is a fine first answer, said out loud |
| New hardware driver kept | Substitute it, or keep it with a reason ([decisions §5](../wsts-configure/references/decisions.md#5-hardware-instances)) |
| New project module | Whether it reaches hardware: read it, or `fprime-wsts survey <flight> --deployment <name>` lists components whose sources open devices |
| New dictionary items missing | Whether ground procedures or tests use them: a mirror surrogate if so |
| Capacity nearly full | The simulation's configuration override of that table |
| Waits on the host, new | Matters only in stepped time (`docs/stepped-time.md`) |

Ask the user only what the flight code cannot tell you, in one batch, each with your
recommendation. A rename that the flight commit states, or that the drift hint names with
a single candidate, is mechanical: do it and report it.

Re-run `drift.py` after each manifest change until generation succeeds and every change
left is one you have explained.

### 3. Regenerate, build, run

```bash
fprime-wsts generate <manifest>
```

```bash
python3 <skill>/../wsts-configure/scripts/build_loop.py <manifest> --stages host,build,run
```

Build failures after a flight update are usually new modules that need what they needed
on the flight build (a header, an SDK): use the `wsts-configure` skill's
`references/troubleshooting.md`, stage by stage. A build directory configured before a
change of `cmake.cache` needs `--fresh`.

### 4. Retest

If the simulation project has tests (pytest with `-p fprime_wsts.pytest_plugin`, as in the
toolkit's `tests/e2e/`), run them, and the project's scenarios. A test that fails because
of a rename (an event or channel name) is updated; one that fails because behaviour
changed is reported to the user with the flight commit that changed it, not edited to
pass.

If the project has a fidelity review (`FIDELITY.md`, from the `wsts-fidelity` skill),
offer to refresh it: the endpoints and gaps it lists may have moved.

### 5. The toolkit pin (when asked, or when the drift report shows it far behind)

The drift report says how far this checkout is ahead of the pin. A bump is the user's
decision: show the commits it brings and recommend for or against. Then, in its own step:

```bash
git -C <sim project>/lib/fprime-wsts fetch
```

```bash
git -C <sim project>/lib/fprime-wsts checkout <commit or tag>
```

```bash
git -C <sim project>/lib/fprime-wsts submodule update --init --depth 1 lib/fprime lib/fprime-vxworks
```

If the toolkit's Python requirements changed, install them
(`pip install -r <sim project>/lib/fprime-wsts/lib/fprime/requirements.txt`). Then run
`drift.py` again: what changes now comes from the toolkit. Read the toolkit's commits for
anything that renames manifest keys or changes defaults, settle the manifests, and go
through steps 3 and 4. A build directory made by the old toolkit may need `--fresh`.

### 6. CI (when asked, or offered once at the end)

`assets/simulation-ci.yml` is a GitHub Actions workflow for the simulation project: it
checks out the simulation project (with its toolkit submodule) and the flight project
side by side, checks the generated tree is up to date (`generate --check`, when the tree is
checked in) or generates it, checks the host, builds, and runs the tests. Fill in its
`TODO`s: the flight repository and the directory name `project:` expects. Write it to
`<sim project>/.github/workflows/simulation.yml` only when the user wants it.

### 7. Hand off

Tell the user, briefly:

- what moved: the flight commits (and toolkit commits) the simulation is now in step with;
- what changed in the simulation, in their terms: renamed instances followed, new devices
  and what flight software sees on them, items now missing from the dictionary, warnings;
- every decision you made with a default, so they can revisit it;
- what ran: generated, built, ran, tests passed (or which stage stopped, with the command);
- what is left: models a new device needs, a mirror surrogate, a flight change you
  proposed and did not make.

Commit only if the user asks, and only in the simulation project. A toolkit bump is a
commit of its own.

## Reference files

- `references/changes.md`: each kind of drift, what it means, and where it is settled
- `assets/simulation-ci.yml`: CI for a simulation project
- The `wsts-configure` skill's `references/decisions.md` and `references/troubleshooting.md`
