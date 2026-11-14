# 5. Check the Host and Build

A flight project relies on things its toolchain and sysroot provide without saying so:
SDK libraries and headers, a compiler whose standard headers include more than the host's,
build options meant for unit tests. On a workstation each shows up as a failure minutes
into the build. `fprime-wsts` makes them visible first and gives each a place in the
manifest.

## Check the host

```bash
fprime-wsts check-host my-mission-sim/wsts/MyDeployment
```

It takes a generated directory (or a manifest), compiles nothing of the project, and
exits 1 when something is missing. For the modules of the simulated build it lists, with
the module and file that needs each:

| Checked | Found from |
|---|---|
| System headers | `#include` lines of the sources each module compiles |
| Libraries | `-l<name>` in `DEPENDS`, `target_link_libraries()` |
| Packages | `REQUIRED` `find_package()` calls |
| SDK files | `DEPENDS` entries that are files |
| Compiler | The flight compiler, compared with the host's |
| Sanitizer runtime | Only when a sanitizer is enabled |

For each missing item it says what can be done: a package to install (as a hint), a
directory to point the build at, or a way to stand in for the module. A false positive is
silenced by name:

```yaml
host:
  ignore: [vendor/gen.h, -lcodec, Codec]     # a header, a library, a package
```

`generate` runs the same check and prints a one-line summary.

## Build

The overlay is an ordinary F´ project:

```bash
cd my-mission-sim/wsts/MyDeployment
```

```bash
fprime-util generate && fprime-util build -j"$(nproc)"
```

The executable is under `build-artifacts/`, as for any F´ deployment, but you will
normally start it with [`fprime-wsts run`](run.md).

!!! warning "After regenerating"

    `fprime-util generate` fails if the build directory already exists. After a
    `fprime-wsts generate`, run only `fprime-util build`. After changing `cmake.cache` in
    the manifest, regenerate the build directory with `fprime-util generate -f`: CMake
    keeps the value it cached.

## Host bridges

When the build fails on something the target's toolchain would have supplied, the
manifest has a key for it.

### A module that links the target's SDK

```yaml
cmake:
  stub_variants: [Components/Imager]
```

If the module has a stub under `if (BUILD_TESTING)` (one written for its unit tests),
`stub_variants` reads that one module's `CMakeLists.txt` with `BUILD_TESTING` set, and
nothing else. No unit-test target is added and no coverage flags are set for it. Other
ways out, which the generator's warning lists: substitute the instances that use the
module, drop its library, exclude the module and give its target name to a stand-in, or
point the SDK variable at a host build with `cmake.cache`.

!!! danger "Do not set `BUILD_TESTING` for the whole build"

    It turns on sanitizers, builds every module's unit tests, and switches *every* module
    with a test variant to its stub, which leaves the deployment without real targets and
    their include directories. The generator warns when anything sets it.

### Headers of the target's sysroot

Give the simulation a stand-in, checked in, rather than setting `CPATH`:

```yaml
cmake:
  include_early: [host.cmake]
```

```cmake title="my-mission-sim/host.cmake"
include_directories("${CMAKE_CURRENT_LIST_DIR}/host/include")
# link_directories("${CMAKE_CURRENT_LIST_DIR}/host/lib")
```

Then write `host/include/board/revision.h` (or whatever is missing) with the definitions
the flight code uses. `check-host` reads these directories too.

### Code that relies on an indirect include

The flight compiler's standard library may pull in `<cstring>` where the host's does not:

```yaml
cmake:
  cxx_flags:
    lib/sensor-lib/SensorLib/Units: ["-include cstring"]   # one module
# or, for every C++ source:  cxx_flags: ["-include cstring"]
```

One list entry is one flag with its argument.

### CMake variables a library expects

```yaml
cmake:
  cache:                         # set before F´ reads the libraries
    MY_LIB_BOARD: devkit           # a variable a library's library.cmake requires
    SIM_COMPONENTS: [SerialSensorSim, Top]   # a list becomes a CMake list
```

Plain `set()` and `option()` values of the flight root `CMakeLists.txt` are carried over
for you; the report lists the statements that are not. `cmake.cache` cannot change what
`project()` caches itself (`CMAKE_CXX_FLAGS`, `CMAKE_BUILD_TYPE`): use `cmake.cxx_flags`,
or pass the option when configuring:

```bash
fprime-util generate -DCMAKE_BUILD_TYPE=Debug
```

### More CMake keys

| Key | Use |
|---|---|
| `cmake.extra_subdirectories` | Plain C++ modules with no FPP model that components depend on |
| `cmake.exclude_subdirectories` | Modules to leave out wherever they are added |
| `cmake.depends` | Extra link dependencies of the topology module (your models' module) |
| `cmake.include` | CMake included after every module is added: aliases, link edges |
| `libraries.keep`, `libraries.drop` | Flight libraries to force in or out |
| `defines` | Compile definitions, in addition to `WSTS_SIM=1` |

Flight code can test `#ifdef WSTS_SIM`, which every simulated build defines, but the point
is that it rarely has to.

## Next

[6. Run the simulation](run.md).
