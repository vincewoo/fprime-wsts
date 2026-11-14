# Getting Started

This section gets the toolkit installed and a simulated spacecraft running on your
machine in a few commands, then explains the handful of ideas the rest of the guide
builds on.

<div class="grid cards" markdown>

-   :material-numeric-1-circle:{ .lg .middle } **[Installation](installation.md)**

    ---

    What the host needs, cloning with submodules, and installing the `fprime-wsts`
    command into a virtual environment.

-   :material-numeric-2-circle:{ .lg .middle } **[Your First Simulation](first-simulation.md)**

    ---

    Generate, build and run the example *Mission*: a VxWorks computer and a Linux
    computer joined by a hub. Then command it, fault it and watch what flight software
    does.

-   :material-numeric-3-circle:{ .lg .middle } **[Core Concepts](concepts.md)**

    ---

    Surrogates, the bus, endpoints, device models, manifests, the overlay project and the
    fidelity report, in one page.

</div>

When you are ready to simulate your own flight software, continue with
[Simulating Your Deployment](../user-guide/survey.md).

!!! tip "Already know F´?"

    Nothing in `fprime-wsts` replaces what you know: the simulated deployment is an
    ordinary F´ project, built with `fprime-util` and operated with `fprime-gds`. The
    toolkit only writes it for you, adds one component (`wstsControl`) and swaps the
    drivers.
