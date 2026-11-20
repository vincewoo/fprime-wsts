# 2. Create a Simulation Project

Everything you write for the simulation (manifests, device models, surrogates, scenarios,
tests) is source code. Keep it in a **simulation project** next to the flight project, so
the flight project stays exactly as it is.

## Layout

```
work/
  fprime-wsts/                # the toolkit
  my-mission/                 # the flight project: never written by fprime-wsts
    settings.ini
    Components/ ...
    MyDeployment/
      Top/
  my-mission-sim/             # the simulation project: its own git repository
    lib/fprime-wsts/          # the toolkit: a git submodule, written by `init`
    wsts.yaml                 # project: ../my-mission
    wsts-catalog.yaml         # your surrogates (when you have some)
    library.cmake             # builds the modules below (when you have some)
    Models/                   # device models (plain C++)
    Surrogates/               # project surrogates (F´ components)
    scenarios/                # scenario files
    tests/                    # pytest tests against the running simulation
    .gitignore
```

Let the toolkit start it:

```bash
fprime-wsts init my-mission
```

`init` surveys the flight project, prints what it found, and writes `../my-mission-sim`
(or `--output DIR`): a starter `wsts.yaml` for one deployment, or a manifest per
deployment and a `wsts-system.yaml` for several (`--deployment NAME` picks some), and a
`.gitignore`, and adds the toolkit as a git submodule at `lib/fprime-wsts`
([below](#the-toolkit-submodule)). What it could work out is filled in: the ground link under `keep`, a register
interface under `substitute`, the hub link of a system, the bus endpoints that will want a
model. What only you know is marked `TODO`. It never writes over a file that exists, and
never writes into the flight project.

Add `--models` and every bus endpoint also gets a device model to start from (`Models/`, as
[`new-model`](device-models.md#start-one-with-new-model) writes one), named in the manifest:
the first build then runs with every endpoint answering, and you give each model its device's
behaviour as you go.

Or start by hand, with just the manifest:

```bash
mkdir my-mission-sim
```

```yaml title="my-mission-sim/wsts.yaml"
project: ../my-mission        # the flight project root, relative to this file
deployment: MyDeployment      # the directory containing Top/, relative to the flight project
```

That is enough for a first `fprime-wsts generate`. The next step adds what your survey
found.

## How the simulation project is built

`project:` makes the manifest's directory a standalone simulation project. It is an F´
**library** of its own:

- The generated deployment lists it under `library_locations`, and F´ includes its
  `library.cmake`, which adds each of its modules.
- Modules are named, and headers included, relative to its root: `Surrogates/SimFpga` is
  the module `Surrogates_SimFpga`, with header `Surrogates/SimFpga/SimFpga.hpp`.

You need `library.cmake` once the project has modules of its own:

```cmake title="my-mission-sim/library.cmake"
add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/Models")
# add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/Surrogates/SimFpga")

# Configuration overrides for the simulated build go in a directory of their own
# add_fprime_subdirectory("${CMAKE_CURRENT_LIST_DIR}/config")
```

!!! info "Paths in the manifest"

    Paths to **flight** things (`deployment`, `sources`, `main.sources`,
    `cmake.*_subdirectories`, `libraries`) are relative to the flight project root.
    Paths to **simulation** things (`catalogs`, `output`, `cmake.include`, `run.env`
    files) are relative to the simulation project.

## The toolkit submodule

A simulation project pins the toolkit it is built with: a git submodule at
`lib/fprime-wsts`. `init` adds it (pass `--no-submodule` not to) at the commit of the
`fprime-wsts` checkout it runs from, cloned from that checkout, with that checkout's
`origin` as its URL. The simulation project becomes a git repository first if it is not
in one. Commit the submodule with the rest of the project:

```bash
git -C my-mission-sim commit -m "Simulation project for my-mission"
```

`init` warns when the pinned commit is not on a branch of the remote, or when the checkout
has uncommitted changes the submodule does not have: push the commit first, or others
cannot check the submodule out. The toolkit's own submodules (`lib/fprime`,
`lib/fprime-vxworks`) are not checked out in it: the simulated build uses the flight
project's framework.

What the submodule changes:

- **Generated files point into the project.** `library_locations`, the toolkit's modules
  and the shims' include directories are written relative to the generated directory, as
  `../../lib/fprime-wsts`, wherever the toolkit is checked out on a machine. Generated
  output that is checked in is the same everywhere.
- **The pinned toolkit runs the commands on the project.** `generate`, `build`,
  `check-host`, `report`, `run`, `status`, `ctl` and `scaffold`, given a path in a
  directory with a `lib/fprime-wsts`, are run by that toolkit's Python package, in the
  same Python environment (a line on stderr says so). The Python half of the toolkit then
  always matches the C++ half it generates for, whichever `fprime-wsts` is on your path.
  The pytest plugin runs in pytest's own process and cannot do this: it warns when the
  installed package is not the pinned one. For tests, install the pinned one:

    ```bash
    pip install -e my-mission-sim/lib/fprime-wsts/python
    ```

- **Updating the toolkit is a commit.** Move the submodule and regenerate:

    ```bash
    git -C my-mission-sim/lib/fprime-wsts fetch && git -C my-mission-sim/lib/fprime-wsts checkout origin/main
    ```

    ```bash
    fprime-wsts generate my-mission-sim/wsts.yaml
    ```

A fresh clone of the simulation project needs the submodule checked out:

```bash
git clone --recurse-submodules <url of my-mission-sim>
```

or, in a clone made without it, `git submodule update --init lib/fprime-wsts`. Until then
every command on the project stops and says so.

A project without the submodule (made with `--no-submodule`, or before `init` added it)
is generated by whichever `fprime-wsts` runs, with paths to that checkout. Add the
submodule by hand to pin it:

```bash
git -C my-mission-sim submodule add https://github.com/vincewoo/fprime-wsts.git lib/fprime-wsts
```

## Generated output: ignore it or check it in

`fprime-wsts generate` writes the overlay project to `wsts/<Deployment>/` inside the
simulation project. It is a build artifact, and there are two reasonable ways to treat it:

=== "Ignore it (simplest)"

    ```gitignore title="my-mission-sim/.gitignore"
    # Generated simulated deployments
    /wsts/
    # Run output
    wsts-logs/
    *.wsts-rec.jsonl
    ```

    Everyone regenerates before building.

=== "Check it in"

    Reviewers see exactly what changes in the simulated deployment when the flight
    topology or the manifest changes. CI keeps it honest:

    ```bash
    fprime-wsts generate my-mission-sim/wsts.yaml --check
    ```

    `--check` writes nothing and exits 1 when the checked-in output is out of date. The
    repository's own example works this way. Machine-specific files (`wsts-host.json`,
    `wsts-host.md`) are never compared and should stay ignored.

## Alternative: inside the flight project

Simulation code can also live in the flight project, with the manifest next to the
deployment and no `project:` key. It is then built like any other project module, and the
overlay goes to `wsts/<Deployment>` in the flight project. Use this when the flight team
owns the simulation and wants it versioned with the flight code. A model module that
nothing in the flight build references then needs `cmake.extra_subdirectories: [Models]`.

## Next

[3. Write the manifest](manifest.md).
