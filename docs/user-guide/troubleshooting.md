# Troubleshooting

Fix one problem at a time and rerun from the stage that failed: messages lower in a log
are often consequences of the first one. Most generator errors say what to change, and
many print the YAML to add. Read the whole message first.

Each fix belongs in one of three places:

| Place | Examples |
|---|---|
| The manifest | `substitute`, `keep`, `models`, `cmake.*`, `host.ignore` |
| Your simulation project | `host.cmake` and stand-in headers, a surrogate, a model |
| The flight project | Only when nothing else works: a `#ifndef WSTS_SIM` guard, a missing `#include`. Look for a manifest key first |

## Before generating

| Symptom | Fix |
|---|---|
| `fprime-wsts: command not found`, `cannot import fprime_wsts` | Activate the virtual environment, or `pip install -r lib/fprime/requirements.txt -e ./python` in the toolkit checkout |
| `fpp-*` tools complain about the Java version | `unset JAVA_HOME`, so the bundled runtime is used |
| FPP tool version differs from the framework's pin | Install the `lib/fprime/requirements.txt` of the checkout in use |
| Framework or library location missing | The flight project's submodules are not checked out: `git submodule update --init --recursive` in it |
| Your project is on another F´ release | The toolkit is built against F´ v4.4.2. Matching minor releases usually work; older major versions do not |

## Generate { #generate }

| Message (start) | Cause | Fix |
|---|---|---|
| `deployment X has no main()` | The target OS starts it | Add [`main:`](manifest.md#start-a-deployment-that-has-no-main) |
| `expected exactly one topology in …` | `Top/` defines several | `topology: <Module.Name>` |
| `… has no Top directory` | `deployment` is not the deployment's directory | Fix `deployment:` (relative to the flight project) and `project:` (relative to the manifest) |
| `manifest names instance 'x', which is not in topology` | Typo, or a name from another deployment | Qualified names (`SensorLink.uart`) always work |
| `instance name 'x' is ambiguous` | Two subtopologies have one | Use the qualified name |
| `instance X (C) cannot build on Linux […] and no surrogate is mapped` | A platform-restricted component outside the catalog | `substitute` with one of the surrogates the message lists, or a project surrogate |
| `S cannot replace X (C): connected ports it does not serve` | The surrogate's ports differ | `ports: {flightName: surrogateName}`, an empty value to drop a connection, or a mirror surrogate |
| `Wsts.SimRegister cannot stand in for X: …` | A command or other port on the register interface | `ports: {<port>: }` to drop it, or a mirror surrogate |
| `surrogate component … was not found` | A project surrogate the analysis cannot see | Is it in the simulation project, named in `catalogs:` or `substitute`, and added by `library.cmake`? FPP names are `Module.Component` |
| `endpoint 'e' is used by both A and B` | Two instances share a base name | `endpoint:` on one |
| `model 'm' attaches to endpoint 'e', which no surrogate is bound to` | Wrong endpoint name | The message lists the endpoints |
| `… registers CONFIGURATION_OVERRIDES directly` | The override call is in `library.cmake` | Move it to `config/CMakeLists.txt` and `add_fprime_subdirectory()` it |
| `uses FPP files from the simulation project … but it has no library.cmake` | Simulation modules with no build | Add a `library.cmake` |
| `base ID … for instance …` from FPP | A simulator instance's IDs collide | Move the simulator's base ID; flight IDs never move |
| A Python traceback instead of `fprime-wsts: error:` | A toolkit bug | Report it with the traceback and the manifest |

## Host check

| Missing | Fix |
|---|---|
| A header of a common package (`libusb-1.0`, `gpiod`, `openssl`) | Install the development package the check hints at |
| A board or SDK header | A stand-in under `host/include/`, added by `cmake.include_early: [host.cmake]` |
| A library (`-lfoo`) | Install it, add a host build under `host/lib` (`link_directories` in `host.cmake`), or stand in for the module |
| A `REQUIRED` package | Install it, or `cmake.cache: {<Pkg>_DIR: …}` |
| A false positive | `host.ignore: [<name>]` |

## Configure (`fprime-util generate`)

| Symptom | Fix |
|---|---|
| `build directory … already exists` | Run only `fprime-util build`, or `fprime-util generate -f` |
| `Cannot find source file` on an SDK path | The SDK warning from `generate`: `cmake.stub_variants`, or one of its other options |
| A library's `library.cmake` stops: a variable is not set | `cmake.cache: {VAR: value}`; if it needs a function first, `cmake.include_early` |
| A target defined twice | `cmake.exclude_subdirectories` on the duplicate |
| A changed `cmake.cache` value has no effect | The build directory cached the old one: `fprime-util generate -f` |
| `restrict_platforms` stops configuration | A platform library's module is added on the host: `cmake.exclude_subdirectories` |

## Compile and link

| Symptom | Fix |
|---|---|
| `'memcpy'` (or `strlen`, `std::…`) `was not declared` in flight or library code | The flight compiler's headers included it indirectly: `cmake.cxx_flags: {<module>: ["-include cstring"]}` |
| `vxWorks.h` or a board header: `No such file` from a project component | The component is OS-specific: substitute its instances, stand in for the header, or guard the code with `#ifndef WSTS_SIM` |
| Errors in topology code naming a driver type or enumerator | A catalog or manifest entry's `compat:` can declare it (see the SPI entry of `catalog/wsts-catalog.yaml`) |
| `undefined reference` into a module built from its stub variant | The stub is not enough (the report says what it lacks): a stand-in module, or substitute the instance |
| `undefined reference` to a plain C++ library of the project | `cmake.extra_subdirectories: [<dir>]`, and `cmake.depends` if the topology needs it |
| Sanitizer symbols (`__asan_…`) undefined | `BUILD_TESTING` or `ENABLE_SANITIZER_*` is set for the build: remove it |
| `-Werror` on warnings the flight compiler does not give | `cmake.cxx_flags: {<module>: ["-Wno-error=<warning>"]}` |

## Run

| Symptom | Fix |
|---|---|
| `assert: {file, line, instances}` with a `kept: true` instance | A kept component reached hardware. Substitute it: standard error prints the entry to add |
| An assert in a flight device manager | It checks something the model does not provide (an ID register, a reply): add it to the model |
| An assert in `Os/MutexInterface.hpp` under `BufferManager` or `GenericHub` | A deployment behind a hub, run alone with its hub driver kept as a real socket: the link is down and buffers run out. Substitute the hub driver or simulate the system. Flight software would do the same with the other computer off |
| `Address already in use` before the bridge opens | Another run holds the port: change `bridge:` or stop the other run |
| `tick` stays at 0 | The tick source is not simulated (`generate` warned), or the run is paused |
| `errors` > 0 in the final status | An endpoint without a model, or a model that rejects what flight sends. `--record run.jsonl` shows each transaction |
| Links down in a system | One element did not start or did not open its link driver: check its `sim.log` |
| GDS: `cannot merge dictionaries … versions differ` | Rebuild every element from the same checkout state |
| Injected telemetry values are overwritten | A free-running model sends again before the once-a-second downlink: silence it first |
| Events arrive late, even after `clear_histories()` | Expected for elements behind a hub |

## Getting more detail

- **Logs:** `<dir>/wsts-logs/sim.log` and `gds.log` (per element for a system).
- **The report:** `wsts-report.md` says what was replaced and what was kept.
- **A recording:** `--record run.jsonl` logs every bus transaction with its status.
- **A backtrace:** run the binary under `gdb` from the generated directory, with
  `WSTS_BRIDGE` set to the manifest's bridge address:

    ```bash
    gdb -q --batch -ex run -ex bt --args build-artifacts/Linux/MyDeployment/bin/MyDeployment -a 127.0.0.1 -p 50000
    ```

Still stuck? [Open an issue](https://github.com/vincewoo/fprime-wsts/issues) with the
manifest, the error and the report.
