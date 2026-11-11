# Converting a deployment that targets another operating system

`examples/vx-demo` is the worked example: a VxWorks downloadable kernel module that uses
`VxWorksDrv.VxWorksUartDriver` and `VxWorksSvc.VxWatchDogTimer` from `fprime-vxworks`.

```bash
fprime-wsts generate examples/vx-demo/VxDemo/wsts.yaml
```

```bash
cd examples/vx-demo/wsts/VxDemo && fprime-util generate && fprime-util build
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

The manifest for VxDemo:

```yaml
deployment: VxDemo
keep: [comDriver]
main:
  generate: true
  declarations: |
    extern "C" int vxDemoStart(const char* hostname, int port);
    extern "C" void vxDemoStop();
  start: vxDemoStart(hostname, port)
  stop: vxDemoStop()
models:
  - {name: tempSensor, class: "Wsts::ScriptedByteStream", endpoint: sensorUart, kind: bytestream,
     params: {text: "$TEMP,-12.25\n", period: "10"}}
```

In the generated `main()`, `hostname` and `port` come from `-a` and `-p`.

## What you still have to deal with

- **Code that includes the target OS's headers.** Guard it, as `VxDemo/Entry.cpp` does with
  `#ifdef TGT_OS_TYPE_VXWORKS`, or with `#ifndef WSTS_SIM` (defined for every simulated
  build). A component that is built around OS calls should be replaced by a surrogate. A
  POSIX-backed VxWorks API shim is planned, not built.
- **Project code a component depends on that has no FPP model** (plain C++ libraries). The
  generator finds modules through the FPP dependency closure, so list such directories
  under `cmake.extra_subdirectories` in the manifest.
- **OSAL implementations chosen per deployment** (`CHOOSES_IMPLEMENTATIONS`) are not
  copied; the host platform defaults apply.
- **Target file-system paths.** They are used as written.
- **Behavioural differences** from scheduling, word size and byte order:
  see [fidelity.md](fidelity.md).
