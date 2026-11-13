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
| A platform library that supplies configuration the project overrides (`VxWorksCfg.hpp` of `fprime-vxworks`) | Kept, with the reason in the report: F´ rejects an override of a file nothing supplies. Its `library.cmake` registers the configuration and then stops on the host (`restrict_platforms`) |
| Flight code that includes the header of a replaced driver (a `SubtopologyTopologyDefs.hpp` with a field of the driver's baud rate type) | A stand-in header at the same include path gives the surrogate under the driver's name, for every module |
| A configuration module that adds another module of the project | The child is added once, by its parent |
| A root `CMakeLists.txt` that sets variables a library requires, or adds compile definitions | Plain values are carried over; the rest is reported with the manifest key to use |
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
| A module that links the target's SDK and has a stub for unit tests under `if (BUILD_TESTING)` | `cmake.stub_variants` reads that one module with `BUILD_TESTING` set; the SDK warning names the key first when such a variant exists |
| Headers, libraries and packages of the target's sysroot that the host lacks | `fprime-wsts check-host` lists them before the build, with the module and file that needs each (`generate` prints a summary) |
| Code that compiles with the flight compiler only because of an indirect include | `cmake.cxx_flags` (for example `-include cstring`), for the whole build or for one module |

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

- **A platform library without a platform guard.** A library that is kept only for the
  configuration it supplies is still read whole. `fprime-vxworks` calls
  `restrict_platforms(vxworks)` in its `library.cmake` after registering its configuration,
  so nothing else of it is added on the host. For a library that goes on to add modules
  without such a call, the generator warns and names them. If they do not configure or
  build on Linux, list them under `cmake.exclude_subdirectories` (paths relative to the
  flight project, `../` allowed): the overlay then skips them where the library adds them.
- **Modules that link files of the target's SDK** (`DEPENDS … ${SDK_ROOT}/3rdparty/lib/libx.a`).
  The generator warns before the build does. Take the module's stub variant if it has one
  (next item), substitute the instances whose component is in the module, drop the
  library, exclude the module and give its target name to a stand-in
  (`add_library(<name> ALIAS <stand-in>)` in a `cmake.include` file), or set the variable
  to a host build of the SDK with `cmake.cache`.
- **Modules with a stub variant for unit tests.** A module that picks a stub source under
  `if (BUILD_TESTING)` builds its real source in the simulated deployment, which is not a
  unit-test build. List the module under `cmake.stub_variants` and the overlay reads that
  one `CMakeLists.txt` with `BUILD_TESTING` set, and nothing else
  ([Stub variants](#the-stub-variant-of-one-module)). Do not set `BUILD_TESTING` for the
  whole build to get there: the generator warns when a manifest or the flight project does.
  A stub selected by another variable is chosen with `cmake.cache`.
- **A stub that is not enough.** A stub written for unit tests provides what those tests
  call. Flight code that calls something else of the real module does not link, and a
  stub target may not export the include directories the real one does. The report lists
  what can be read of this from the text ("Modules that change under BUILD_TESTING"); the
  remedy is a stand-in module of the simulation, or a surrogate.
- **A kept driver whose set-up touches hardware.** A driver the simulation does not
  replace runs its flight code, including the phase code of its subtopology: a `setup()`
  that maps a DMA window asserts on a workstation. There is no general way to detect this
  from the model. `fprime-wsts run` names the file and line of an F´ assert the deployment
  stops on, and the kept instance whose component the file belongs to; the remedy is a
  surrogate for that instance.
- **Statements of the flight root `CMakeLists.txt` that are not plain values**: compiler
  options, include directories, target properties, anything inside `if()`. They are listed
  as warnings with the manifest key that reproduces each.
- **A module added twice through a path the generator cannot read** (built from a
  variable). An overlay that defines its own `add_fprime_subdirectory()` (it does when a
  module is patched, deferred or excluded) skips the repeat; otherwise CMake reports the
  duplicate, and `cmake.exclude_subdirectories` on the child settles it.
- **Memory-mapped registers and interrupts.** There is no stock surrogate for these
  interfaces: a driver that uses them is substituted with a project surrogate.
- **Third-party build systems inside libraries** behave on the host as they do there, not
  as on the target's build machine (an autocoder script that depends on the shell, for
  example). The toolkit builds them as they are.
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

## Building on a host that lacks the target's toolchain

A flight project relies on what its toolchain and sysroot provide without saying so: SDK
libraries and headers, a compiler whose standard headers include more than the host's, and
build options meant for unit tests. On a workstation each of these shows up as a failure
minutes into the build, one per rebuild. The pieces below make them visible first and give
each a place in the manifest, so that nothing has to be passed in the environment.

### Check the host before building

```bash
fprime-wsts check-host examples/mission-sim/wsts-system.yaml
```

It takes a manifest (nothing is written) or a generated directory, compiles nothing of the
project, and exits with status 1 when something is missing. `fprime-wsts generate` runs the
same check and prints one line of it. For the modules of the simulated build (the kept
flight modules, the kept libraries and the simulation's own) it lists:

| Checked | How it is found | How the host is asked |
|---|---|---|
| System headers | `#include` lines of the sources each module's `CMakeLists.txt` compiles, and of the project headers those include, that name no file in the project, a library, the framework or the simulation, and are not C or C++ standard headers or autocoded (`…Ac.hpp`) | One preprocessor run of the host compiler (`$CXX`, else `c++`, `g++`, `clang++`) with `__has_include`: its default search path, `CPATH`, and the `include_directories()` of the simulation's CMake files |
| Libraries | `-l<name>` in `DEPENDS`, `target_link_libraries()` and the like | A link of an empty program with `-l<name>`, with the `link_directories()` of the simulation's CMake files |
| Packages | `find_package(<Name> …)`. Reported as missing only when `REQUIRED` and outside an `if()` | One CMake run in a project of its own, with the manifest's `cmake.cache` |
| SDK files | `DEPENDS` entries that are files (the existing warning) | Whether the file exists |
| Compiler | The flight build directory's `CMakeCXXCompiler.cmake` if there is one, else the toolchain file | Compared with the host compiler; a difference is a note |
| Sanitizer runtime | Only when a sanitizer is enabled (`BUILD_TESTING` or `ENABLE_SANITIZER_*` in `cmake.cache`) | A link with `-fsanitize=…` |

For each missing item it names the module and file that needs it, and what can be done:
install a package (for well-known headers and libraries a usual Debian package name is
given, as a hint only), point the build at a directory, or stand in for the module
(`substitute`, `cmake.exclude_subdirectories`, `cmake.stub_variants`).

What it cannot check:

- An `#include` under a condition it cannot decide (`#ifdef BOARD_REV_B`) is a note, not a
  failure. It knows the macros of a Linux host build (`__linux__`, `TGT_OS_TYPE_LINUX`,
  `WSTS_SIM`, the manifest's `defines`) and that those of other systems are not defined.
- Headers that framework headers include are not followed: the framework is taken to build
  on Linux.
- A header that exists anywhere below a build root counts as provided, whether or not an
  include path leads to it.
- Sources named through a CMake variable are not read; such a module is taken to compile
  every source file in its directory. Directories added through a variable that is not in
  `cmake.cache` are not followed either: every module directory below that file is then
  checked, and the report says so.
- Whether a library that is found is built for the host's architecture and has the symbols
  the code needs. That is the link step's to say.
- A check that cannot be made (no compiler, no CMake) is a warning, and not a failure.

A false positive is silenced by name:

```yaml
host:
  ignore: [vendor/gen.h, -lcodec, Codec]     # a header, a library, a package
```

### The stub variant of one module

```yaml
cmake:
  stub_variants: [Components/Imager]     # module directories, relative to the flight project
```

The overlay's `add_fprime_subdirectory()` sets `BUILD_TESTING` to `ON` as a variable of
the call that adds a listed directory. That module's `if (BUILD_TESTING)` sees it. A
module which the listed one adds gets the build's own value back (list it too if its
variant is wanted), and when the call returns the variable is gone. What F´ itself does
with `BUILD_TESTING` is kept out of the listed module:

| F´, when `BUILD_TESTING` is set | For a listed module |
|---|---|
| `register_fprime_ut()` adds a unit-test executable | Not added: `__FPRIME_NO_UT_GEN__` is set for the call, which makes that function return at once |
| A module's own target gets the coverage flags (`FPRIME_TESTING_REQUIRED_COMPILE_FLAGS`) | Cleared for the call |
| Sanitizers default to on, `-DBUILD_UT` is defined, unit-test targets and GoogleTest are set up | These are decided once for the whole build, where `BUILD_TESTING` is off |

`BUILD_UT` is therefore **not** defined for a stub's sources. It cannot be: it changes the
layout of framework classes (`Fw::StringBase` gains a virtual function), so a module built
with it does not fit a deployment built without it. A stub source that needs `BUILD_UT`
gets it from a stand-in module of the simulation instead.

A listed directory that is no module of the build is an error when generating; one that
nothing adds stops the configure step; one whose `CMakeLists.txt` has nothing under
`if (BUILD_TESTING)` is a warning. In the example, `Components/Imager` links the camera
vendor's SDK and is listed; `lib/sensor-lib/SensorLib/Units` also has a variant for unit
tests and is not listed, and Payload's topology code needs the include directory that only
its real variant exports.

### Why not `BUILD_TESTING` for the whole build

It is the switch for a unit-test build, and it does much more than choose stubs:

1. The address, leak and undefined-behaviour sanitizers are turned on, so every link needs
   the sanitizer runtime that matches the host compiler.
2. Every module's unit-test executable is built. Tests that are stale or need the target
   do not compile, and they bury the log.
3. Every module with a variant for unit tests takes it. A library that picks stubs then
   leaves the deployment without the symbols of its real targets and without the include
   directories they export.

The generator warns when `cmake.cache` sets it, when the flight root `CMakeLists.txt` sets
it (plain values of that file are carried over; `cmake: {cache: {BUILD_TESTING: OFF}}`
keeps it off), and when the flight `default_cmake_options` do (those are not carried over).
The report section "Modules that change under BUILD_TESTING" lists the modules of the
build that have such a branch, the instances that use them, what each side does, and what
the unit-test side lacks.

### Host include and library directories

```yaml
cmake:
  include_early: [host.cmake]
```

```cmake
# host.cmake, in the simulation project
include_directories("${CMAKE_CURRENT_LIST_DIR}/host/include")
link_directories("${CMAKE_CURRENT_LIST_DIR}/host/lib")
```

The file is included before the framework and the libraries are read, so every module
gets the directories. This is preferable to `CPATH` and `LIBRARY_PATH`: it is checked in,
anyone who builds gets the same result, and `check-host` reads it. The example gives
Payload a `<board/revision.h>` this way.

### Compile flags for the host compiler

```yaml
cmake:
  cxx_flags: ["-include cstring"]                    # every C++ source of the build
```

```yaml
cmake:
  cxx_flags:
    lib/sensor-lib/SensorLib/Units: ["-include cstring"]   # one module
```

One list entry is one flag with its argument. The first form is written into the generated
project as `string(APPEND CMAKE_CXX_FLAGS …)` after `project()`; the second as
`target_compile_options()` on the module's target, for C++ sources only (`<cstring>` is no
C header, and F´ compiles some C). A library that uses `memcpy`, `memset` or `strlen`
without `#include <cstring>` builds with this, as the example's `Units` does.

`cmake.cache: {CMAKE_CXX_FLAGS: …}` does **not** work: `project()` has put that variable in
the cache before the manifest's entries are read, and a `set(… CACHE …)` leaves a value
that is there alone. The generator warns and names `cmake.cxx_flags`. `add_compile_options()`
in a `cmake.include_early` file works too, but applies to C sources as well.

### Passing configure options to `fprime-util`

```bash
fprime-util generate -DCMAKE_BUILD_TYPE=Debug
```

`fprime-util generate` (fprime-tools 4.4) takes `-DNAME=VALUE` directly. After `--` it
takes the same text for the name of a toolchain, and it does that too for a value with a
space in it, so compiler flags cannot be passed this way. Options of the simulated build
belong in the manifest (`cmake.cache`, `cmake.cxx_flags`), where they are checked in. For
the few variables `cmake.cache` cannot set because `project()` sets them first
(`CMAKE_BUILD_TYPE`, `CMAKE_CXX_COMPILER`), the generator warns and puts the option into
the "Build it with" line it prints.

### What still has no bridge

- **An SDK library for another architecture that a kept module needs at run time.** A stub,
  a host build of the library or a substitute for the module are the only ways.
- **Code that depends on the target's word size, byte order or kernel interfaces.**
- **Libraries whose tests or code generators only run with the target's toolchain.**
