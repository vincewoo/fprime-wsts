# VxWorks and Other Targets

A flight deployment does not have to target Linux. The generator reads the model with the
FPP tools and never builds the flight project, so a deployment whose build cannot even be
configured on a workstation converts like any other. The overlay is built with the host
toolchain, and F´ selects its Linux/POSIX OS layer. **No target toolchain is involved at
any point.**

The example's `Cdh` computer is the worked example: a VxWorks downloadable kernel module
with no `main()`, a watchdog timer, and a UART inside a library subtopology. Its manifest
names no drivers.

## What is handled for you

| Problem | Handling |
|---|---|
| The flight build cannot be configured on Linux | The model is read with the FPP tools directly |
| OS layer | The overlay builds with the host toolchain: Linux/POSIX OSAL |
| OS-specific components (`restrict_platforms`) | Replaced from the catalog, or by a surrogate whose ports match, or an error naming the instance |
| Platform libraries that do not configure on Linux | Dropped when nothing uses them, or kept only for the configuration the project overrides (reported) |
| Timer that fires from interrupt context | `Wsts.SimTick` in thread mode |
| Topology code naming driver classes and enumerators | Compiles unchanged through generated aliases |
| A driver instance inside a library subtopology | Found by its qualified name and retyped in a copy of the subtopology |
| Flight code including a replaced driver's header | A stand-in header at the same include path |
| Toolchain build options (`FPRIME_PLATFORM=VxWorks`, …) | Not carried over (reported) |
| Simple values in the flight root `CMakeLists.txt` | Carried over; the rest reported with the key to use |

## What you write

### Start-up without `main()`

```yaml title="cdh.yaml"
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

That is all the Cdh conversion needs; the rest of `cdh.yaml` adds its simulators.

### Host bridges

The target's toolchain and sysroot supply things that a workstation lacks. Each has a
manifest key, and `fprime-wsts check-host` lists them before you build
([step 5](build.md)):

| On the target | In the simulation |
|---|---|
| An SDK library linked by a module with a unit-test stub | `cmake.stub_variants: [<module>]` |
| Sysroot headers | `cmake.include_early: [host.cmake]` with a stand-in `host/include/` |
| A standard library that includes more than the host's | `cmake.cxx_flags: {<module>: ["-include cstring"]}` |
| Variables a library expects | `cmake.cache` |
| A platform library's modules that do not configure | `cmake.exclude_subdirectories` |

## What you still deal with

- **Code that includes the target OS's headers.** Guard it with
  `#ifdef TGT_OS_TYPE_VXWORKS` or `#ifndef WSTS_SIM` (defined in every simulated build),
  or replace the component with a surrogate. A POSIX-backed VxWorks API shim is planned,
  not built.
- **A project component that drives the rate groups or supplies time from hardware.** It
  builds on Linux and is kept, but its interrupt never arrives. `generate` warns; write a
  [project timer surrogate](surrogates.md#a-project-timer), or model its registers.
- **A kept driver whose set-up touches hardware** (maps a DMA window, opens a device).
  `fprime-wsts run` names the file, line and kept instance of the assert; substitute it.
- **Memory-mapped registers and interrupts**: replace the register interface component
  ([Register blocks](registers.md)).
- **DMA**: the flight libraries' virtual-to-physical translation has to mean something
  off the target ([details](../vxworks-conversion.md#what-you-still-have-to-deal-with)).
- **Target file-system paths** are used as written; path remapping is on the roadmap.
- **Word size, byte order and scheduling** are the host's ([Fidelity](../fidelity.md)).

The complete list, with remedies, is in
[Converting other targets](../vxworks-conversion.md).
