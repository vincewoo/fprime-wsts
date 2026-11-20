# Installation

## Requirements

| | |
|---|---|
| Operating system | Linux (the simulated deployment uses the F´ Linux/POSIX OS layer) |
| Build tools | CMake and a C++ compiler (GCC or Clang) |
| Python | 3.9 or later |
| F´ | v4.4.2, pinned as a submodule of this repository; nothing to install separately |

No target toolchain is needed: not for a Linux flight deployment, and not for a VxWorks one.

## Get the toolkit

Clone the repository with its submodules (`lib/fprime` and `lib/fprime-vxworks`):

```bash
git clone --recurse-submodules https://github.com/vincewoo/fprime-wsts.git
```

```bash
cd fprime-wsts
```

If you cloned without `--recurse-submodules`, fetch them now:

```bash
git submodule update --init
```

## Install into a virtual environment

```bash
python3 -m venv venv && . venv/bin/activate
```

```bash
pip install -r lib/fprime/requirements.txt -e ./python
```

This installs the F´ tools (`fprime-util`, `fprime-gds`, the FPP tools) at the versions the
framework pins, and `fprime-wsts` itself, in editable mode.

Check that it worked:

```bash
fprime-wsts --help
```

!!! warning "FPP tools and Java"

    The FPP tools need a Java runtime. If an `fpp-*` tool complains about the Java version,
    unset `JAVA_HOME` so that the runtime bundled with the tools is used:

    ```bash
    unset JAVA_HOME
    ```

## Where your projects go

`fprime-wsts` reads a flight project and writes a simulation next to it. The layout that
the rest of this guide (and the [agent skills](../user-guide/agents.md)) assumes:

```
work/
  fprime-wsts/        # this repository: where you run fprime-wsts from at first
  my-mission/         # your flight project, never written to
  my-mission-sim/     # your simulation project (created in the walkthrough)
    lib/fprime-wsts/  # the toolkit, pinned as a git submodule of the simulation project
```

The simulation project builds against its own submodule of the toolkit, which
`fprime-wsts init` adds at the commit of the checkout it runs from. Generated files point
into that submodule, not at `../fprime-wsts`, so they are the same on every machine, and
the project keeps building against the toolkit version it pins when this checkout moves
on. Any `fprime-wsts` on your path hands a command on such a project to the pinned toolkit
([Simulation project](../user-guide/simulation-project.md#the-toolkit-submodule)).

The simulated build uses the F´ framework that your flight project's `settings.ini`
names. The toolkit is built and tested against F´ v4.4.2; `fprime-wsts generate` reports
FPP tools that are older or newer than the framework pins before it analyses anything.

## Next

[Run the example](first-simulation.md) to see a complete simulation before you build your own.
