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
  my-mission-sim/             # the simulation project: its own directory or repository
    wsts.yaml                 # project: ../my-mission
    wsts-catalog.yaml         # your surrogates (when you have some)
    library.cmake             # builds the modules below (when you have some)
    Models/                   # device models (plain C++)
    Surrogates/               # project surrogates (F´ components)
    scenarios/                # scenario files
    tests/                    # pytest tests against the running simulation
    .gitignore
```

Start with just the manifest:

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
