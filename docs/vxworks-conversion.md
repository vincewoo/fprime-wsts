# Converting a deployment that targets another operating system

The `Cdh` computer of the repository's [example](../examples/README.md) is the worked
example: a VxWorks downloadable kernel module (`examples/mission/Cdh`) that uses
`VxWorksSvc.VxWatchDogTimer` and `VxWorksDrv.VxWorksUartDriver` from `fprime-vxworks`. It
is also assembled from subtopologies: its UART driver is inside a subtopology of a project
library (`lib/sensor-lib`), the driver is opened and started by phase code in that
subtopology, the project overrides configuration of the library and of the framework, and
the model has a `system.fpp`. Its manifest (`examples/mission-sim/cdh.yaml`) names no
drivers.

```bash
fprime-wsts generate examples/mission-sim/wsts-system.yaml
```

```bash
cd examples/mission-sim/wsts/Mission/cdh && fprime-util generate && fprime-util build
```

No VxWorks toolchain is involved at any point.

## What the generator does

| Problem | Handling |
|---|---|
| The flight build cannot be configured on Linux | The model is read with the FPP tools directly; the flight project is never built |
| OS layer | The overlay builds with the host toolchain, so F´ selects the Linux/POSIX OSAL implementations |
| OS-specific components (`restrict_platforms`) | Replaced from the catalog, or by port-structure match, or it is an error naming the instance |
| Platform libraries that do not configure on Linux | Dropped when the simulated topology no longer references them (reported) |
| Toolchain build options (`FPRIME_PLATFORM=VxWorks`, `VX_TARGET_TYPE=…`) | Not carried over (reported) |
| No `main()`: entry points are called from a start-up script | The manifest's `main` section generates a Linux `main()` that calls them |
| Timer that fires from interrupt context | `Wsts.SimTick` in `mode=thread`: `startTimer…()` returns at once and ticks from its own thread |
| Topology code naming driver classes and enumerators | Compiled unchanged through the generated type aliases |
| A driver instance inside a subtopology (of a library, the framework or the project) | Found under its qualified name (`SensorLink.uart`) and retyped in a copy of the subtopology's FPP; the overlay builds the copy in place of the original module, and the rest of the library builds as it is |
| Phase code of such an instance (`uart.open(…, VxWorksDrv::VxWorksUartDriver::BAUD_19200, …)`, `uart.start(…)`) | Emitted into the deployment's topology module, so it compiles against the surrogate through the same aliases |
| The flight driver's module in the subtopology module's `DEPENDS` | Removed in the copy's build file when no instance uses the driver any more |
| Configuration replaced with `CONFIGURATION_OVERRIDES` (framework defaults, subtopology configuration, library configuration) | Applied in the analysis as the build applies them: by file name, last registration wins. Listed in the report |
| A timer or time source that is a project component waiting for a hardware interrupt | Reported: a warning and a fidelity note say that the flight rate groups may never run, and what to do |
| Simulators the project already has as F´ components, in their own library | Added to the topology from the manifest (`simulators`, `devices`), with clocks of their own (`clocks`), their libraries (`libraries.sim`), CMake around the flight modules (`cmake.include`) and configuration overrides that win over flight's |
| FPP tools older or newer than the framework pins; a Java runtime that is too old | Reported before the analysis, and again with the tool's error |

What the manifest for Cdh needs for the conversion (the rest of `cdh.yaml` is about its
simulators):

```yaml
project: ../mission
deployment: Cdh
keep: [comDriver]
main:
  generate: true
  declarations: |
    extern "C" int cdhStart(const char* hostname, int port, int hubPort);
    extern "C" void cdhStop();
  start: cdhStart(hostname, port, 50050)
  stop: cdhStop()
```

In the generated `main()`, `hostname` and `port` come from `-a` and `-p`; `start` is any
C++ expression, so other arguments of the entry point are written into it.

## What you still have to deal with

- **Code that includes the target OS's headers.** Guard it, as `Cdh/Entry.cpp` does with
  `#ifdef TGT_OS_TYPE_VXWORKS`, or with `#ifndef WSTS_SIM` (defined for every simulated
  build). A component that is built around OS calls should be replaced by a surrogate. A
  POSIX-backed VxWorks API shim is planned, not built.
- **A project component that drives the rate groups or supplies time from hardware.** If
  it is not platform-restricted it builds on Linux and is kept, but its interrupt never
  arrives. The generator warns; the remedy is a project surrogate
  ([A project timer](surrogate-guide.md#a-project-timer)).
- **Two substituted instances with the same base name** (a `uart` in each of two
  subtopologies) need an `endpoint:` for one of them
  ([Instance names](reference.md#instance-names)).
- **Configuration overrides the generator cannot read**: a `CONFIGURATION_OVERRIDES` list
  held in a CMake variable, or two configuration modules of one project that override the
  same file for different builds. Settle these with `fpp_exclude`.
- **A simulator that must see a flight output port that is already connected.** An output
  port drives one input; there is no tap. Feed the simulator from another port, or
  substitute the receiving component.
- **I2C, SPI and GPIO simulators written as components.** Only the byte stream adapter
  exists; use a C++ model on those endpoints.
- **Simulator rates across the elements of a system.** Each element has its own tick and
  its own clocks; nothing aligns them between processes.
- **A project tick surrogate that does not declare its period** makes the simulation's
  clocks follow a measured tick interval. Call `Control::setTickPeriod()` in it.
- **Project code a component depends on that has no FPP model** (plain C++ libraries). The
  generator finds modules through the FPP dependency closure, so list such directories
  under `cmake.extra_subdirectories` in the manifest.
- **OSAL implementations chosen per deployment** (`CHOOSES_IMPLEMENTATIONS`) are not
  copied; the host platform defaults apply.
- **Target file-system paths.** They are used as written.
- **Behavioural differences** from scheduling, word size and byte order:
  see [fidelity.md](fidelity.md).
