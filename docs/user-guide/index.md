# User Guide

This guide takes you from an F´ flight project that has never seen `fprime-wsts` to a
simulated deployment you can run in CI, operate from the GDS and drive from tests. It
assumes you have [installed the toolkit](../getting-started/installation.md) and
[run the example](../getting-started/first-simulation.md).

## The workflow

```mermaid
flowchart LR
  s1["1 Survey<br/>the deployment"]:::user
  s2["2 Create a<br/>simulation project"]:::user
  s3["3 Write<br/>the manifest"]:::user
  s4["4 Generate,<br/>read the report"]:::toolkit
  s5["5 Check the host,<br/>build"]:::toolkit
  s6["6 Run"]:::toolkit
  s7["Add models,<br/>scenarios, tests"]:::user

  s1 --> s2 --> s3 --> s4 --> s5 --> s6 --> s7
  s4 -.->|"errors name<br/>the manifest key"| s3
  s5 -.->|"host bridges"| s3
  s6 -.->|"asserts name the<br/>instance to substitute"| s3
  s7 -.-> s4

  classDef toolkit fill:#dcfce7,stroke:#15803d,color:#052e16
  classDef user fill:#f3e8ff,stroke:#7e22ce,color:#3b0764
```

Getting a deployment to run is a loop. Each tool says what it found and, where it stops,
which manifest key settles it: `generate` names ambiguous instances and unmatched ports,
`check-host` names missing headers and libraries with the module that needs them, and
`run` names the kept instance behind an F´ assert.

## Sections

<div class="grid cards wsts-2col" markdown>

-   :material-map-marker-path:{ .lg .middle } **Simulating your deployment**

    ---

    The six steps above, in order.

    [:octicons-arrow-right-24: 1. Survey the deployment](survey.md)

-   :material-chip:{ .lg .middle } **Simulating hardware**

    ---

    What answers behind the surrogates: [device models](device-models.md),
    [register blocks and interrupts](registers.md),
    [simulators as F´ components](simulators.md), and
    [your own surrogates](surrogates.md).

-   :material-gamepad-variant-outline:{ .lg .middle } **Operating a simulation**

    ---

    [Control and fault injection](control.md),
    [scenarios, recording and replay](scenarios.md), and
    [testing with pytest](testing.md).

-   :material-sitemap-outline:{ .lg .middle } **Larger projects**

    ---

    [Multi-computer systems](systems.md),
    [VxWorks and other targets](other-targets.md), and
    [configuring with an agent](agents.md).

</div>

When something goes wrong, see [Troubleshooting](troubleshooting.md).
