# Reference

The reference pages are the repository's own design and reference documents, published as
they are. Where the [user guide](../user-guide/index.md) explains how to do something,
these say exactly what each key, option and format does.

<div class="grid cards" markdown>

-   :material-file-cog-outline:{ .lg .middle } **[Manifest, CLI and formats](../reference.md)**

    ---

    Every manifest and system manifest key, the surrogate catalog, the command line,
    actions, fault kinds, scenario and recording lines, the bridge protocol, and
    `Wsts.SimControl`.

-   :material-sitemap:{ .lg .middle } **[Design](../design.md)**

    ---

    Goals, where the simulation is cut in, the runtime, how the transform works, the
    overlay project, and the roadmap.

-   :material-scale-balance:{ .lg .middle } **[Fidelity](../fidelity.md)**

    ---

    What a simulated deployment reproduces, what it does not, and how to choose a seam.

-   :material-lan:{ .lg .middle } **[Systems](../systems.md)**

    ---

    Several compute elements: links, ID windows, running and controlling a system.

-   :material-puzzle-outline:{ .lg .middle } **[Surrogates and device models](../surrogate-guide.md)**

    ---

    Writing models and surrogates in depth: simulators as components, clocks, project
    timers, register blocks, time units, DMA, mirrors.

-   :material-swap-horizontal:{ .lg .middle } **[Converting other targets](../vxworks-conversion.md)**

    ---

    Converting a VxWorks deployment, and building on a host that lacks the target's
    toolchain.

</div>

## Quick reference

### Commands

| Command | Purpose |
|---|---|
| `fprime-wsts generate <manifest> [--check] [--json]` | Write (or check) the overlay project for a deployment or a system |
| `fprime-wsts check-host <dir or manifest>` | List what the build needs from this machine, before building |
| `fprime-wsts run [dir] [--gds \| --gds-gui] [--duration S] [--json]` | Run a built deployment or system |
| `fprime-wsts report [dir]` | Print a generated deployment's fidelity report |
| `fprime-wsts scaffold <Component> --output DIR --name NAME` | Start a mirror surrogate for a flight component |

### Environment of a simulated deployment

| Variable | Meaning |
|---|---|
| `WSTS_BRIDGE` | Bridge address, overriding the manifest |
| `WSTS_SCENARIO`, `WSTS_SCENARIO_MODE` | Scenario to load and start at start-up |
| `WSTS_RECORD` | File to record to from start-up |
| `WSTS_DIR` | Base directory for relative scenario and recording paths |
| `WSTS_LINK_DIR` | Directory of a system's link sockets (set by `fprime-wsts run`) |
