# Troubleshooting a new configuration

Organised by the stage `scripts/build_loop.py` reports. Fix one problem at a time and
rerun from the stage that failed: messages lower in a log are often consequences of the
first one.

Most generator errors say what to change, and many give the YAML to add. Read the whole
message before reaching for this file. Each fix belongs in one of four places. Say which
one when you propose it:

| Place | Who changes it |
|---|---|
| The manifest (`*.yaml`) | You, with the user's agreement on the decision |
| A file of the simulation project (`host.cmake`, a stand-in header, a surrogate, a model) | You |
| The flight project | **The user.** Never edit it. Propose the change (a `#ifndef WSTS_SIM` guard, a missing `#include`) and say why the manifest cannot do it |
| fprime-wsts itself (a limitation or a bug) | Report it to the user with the error text. Do not patch the toolkit from inside this skill |

## Contents

- [Before generating](#before-generating)
- [generate: errors](#generate-errors)
- [generate: warnings worth acting on](#generate-warnings-worth-acting-on)
- [host](#host)
- [build: configure (`fprime-util generate`)](#build-configure)
- [build: compile and link](#build-compile-and-link)
- [run](#run)

---

## Before generating

| Symptom | Fix |
|---|---|
| `cannot import fprime_wsts`, `fprime-wsts: command not found` | Activate the virtual environment, or `pip install -r lib/fprime/requirements.txt -e ./python` in the fprime-wsts checkout |
| `fpp-*` tools complain about the Java version | `unset JAVA_HOME`, so the bundled JRE is used |
| FPP tool version differs from the framework's pin | Install the `lib/fprime/requirements.txt` of the checkout in use. The generator reports the difference before the analysis |
| Survey: framework or library location missing | The flight project's submodules are not checked out: `git -C <project> submodule update --init --recursive` (ask the user first: it changes their checkout) |
| Survey: framework version differs from the toolkit's | fprime-wsts is built against the F´ in its `lib/fprime` (v4.4.2). A project on another F´ release may not generate or build. Tell the user before going further. Matching minor releases usually work. Older major versions do not |
| `gtest/gtest.h` reported missing, or `generate --check` differs only in the host section | `git -C lib/fprime submodule update --init --depth 1 googletest` in the fprime-wsts checkout, as CI does |

## generate: errors

| Message (start) | Cause | Fix |
|---|---|---|
| `deployment X has no main()` | Started by the target OS | `main:` ([decisions §4](decisions.md#4-how-the-deployment-starts)) |
| `expected exactly one topology in …` | `Top/` defines several | `topology: <Module.Name>` |
| `… has no Top directory` | `deployment` is not the deployment's directory, relative to the flight project | Fix `deployment:` (and `project:`, which is relative to the manifest) |
| `manifest names instance 'x', which is not in topology` | Typo, or a name from another deployment | Names from the survey table. Qualified names (`SensorLink.uart`) always work |
| `instance name 'x' is ambiguous` | Two subtopologies have one | The qualified name |
| `instance X (C) cannot build on Linux [...] and no surrogate is mapped` | A platform-restricted component outside the catalog | `substitute` with one of the surrogates the message lists, or a project surrogate |
| `S cannot replace X (C): connected ports it does not serve` | The surrogate's ports differ | `ports: {flightName: surrogateName}`, an empty value to drop a connection the simulation can do without, or a mirror surrogate (`fprime-wsts scaffold`) |
| `Wsts.SimRegister cannot stand in for X: … neither a register access nor an interrupt` | A command or other port on the register interface | `ports: {<port>: }` to drop it, or a mirror surrogate ([decisions §7](decisions.md#7-register-interfaces)) |
| `manifest substitutes X with unknown component` / `surrogate component … was not found` | A project surrogate the analysis cannot see | Is it in the simulation project, with `catalogs:` or a `substitute`, and is the module in `library.cmake`? FPP names are `Module.Component` |
| `endpoint 'e' is used by both A and B` | Two instances share a base name | `endpoint:` on one, as the message shows |
| `model 'm' attaches to endpoint 'e', which no surrogate is bound to` | Wrong endpoint name, or the device has no driver instance in the flight topology | Endpoints are listed in the message. For a device with no driver yet, leave the entry commented out with a note |
| `endpoint 'e' has both the model … and the link …` | A link end also has a model | Remove one |
| `link '…' ends at X, which the manifest lists under 'keep'` | A link end is kept | Remove it from `keep` |
| `link … ends at X, which is substituted with 'endpoint: none'` / `is not a byte stream driver` | Wrong instance for a link | A link ends at the byte stream driver (the survey's `hub link end`) |
| `the WSTS instances need IDs … outside the element's 'ids' window` / `no room … at the top of the 'ids' window` | The window does not fit the element's IDs | Check the window against the splitter's opcode base and the survey's ID range, or set `control.base_id` inside the window |
| `control.base_id … overlaps` | Chosen base collides with flight | Leave `control.base_id` out, or pick a free block |
| Two elements' `wstsControl` would share IDs | Flight IDs of the elements overlap | `ids` windows, or `control.base_id` per element |
| `… is listed under CONFIGURATION_OVERRIDES, but no … supplies a file named` | An override of nothing | The file name must match the replaced file exactly; new configuration goes under `AUTOCODER_INPUTS`/`HEADERS` |
| `… registers CONFIGURATION_OVERRIDES directly` | Override call in `library.cmake` | Move it to `config/CMakeLists.txt` and `add_fprime_subdirectory()` it |
| `uses FPP files from the simulation project … but it has no library.cmake` | Simulation modules with no build | Add `library.cmake` (`assets/sim-project/library.cmake`) |
| `cmake.include… names …, which does not exist` / `libraries.sim names …` | Paths are relative to the simulation project (the manifest's directory) | Fix the path |
| `… stub_variants … is not a module directory` / `… is not part of the simulated build` | The module path is relative to the flight project, or the module is not built | Use the path from the warning that suggested it |
| `… exists and was not generated by fprime-wsts; refusing to overwrite it` | `output` points at a real directory | Choose another `output` |
| `base ID … for instance …` from FPP | A simulator instance's IDs collide | Move the simulator's base ID; flight IDs never move |
| `the generated topology does not pass fpp-check` | A toolkit bug, or flight FPP the patcher does not understand | Report to the user with the text. If one instance causes it, try `keep` on it to confirm |
| Python traceback instead of `fprime-wsts: error:` | A toolkit bug | Report with the traceback and the manifest |

## generate: warnings worth acting on

Warnings do not stop generation, but most of these stop the build or the run later.

| Warning (gist) | Fix |
|---|---|
| `module M links '${SDK}/…': … not set in the simulated build` | `cmake.stub_variants: [M]` when the warning says a stub variant exists; otherwise the options it lists ([decisions §9](decisions.md#9-what-does-not-build-on-the-host)) |
| `X cycles the flight rate groups … and is kept` | A project tick surrogate ([decisions §6](decisions.md#6-ticks-and-time)). Without one, nothing scheduled runs |
| `time comes from X, a project component that is kept` | Substitute it if it reads a hardware clock |
| `X: S is a queued/active component and the flight instance has no queue` | Fine for a start. Set `queue_size` if the surrogate needs a deeper queue |
| `flight root CMakeLists.txt: … not carried over … (key: …)` | Put the statement under the key named, if the build needs it |
| `cmake.cache sets CMAKE_…` | Use `cmake.cxx_flags`, or pass it as a configure option (the "Build it with" line has it) |
| `BUILD_TESTING …` | Remove it. Use `cmake.stub_variants` for the modules that need their stub |
| A platform library adds modules that may not configure on Linux | `cmake.exclude_subdirectories` for those that fail |
| `X (C) is kept by the manifest although it is restricted` | Intentional only if the user says so: it will not build |

## host

`fprime-wsts check-host` reads the requirements from text, so it can be wrong in both
directions. The build has the last word. For each missing item it gives remedies:

| Missing | Fix |
|---|---|
| A header of a common package (`libusb-1.0`, `gpiod`, `openssl`) | Ask the user to install the dev package it hints at. Do not install system packages yourself unless they agree |
| A board or SDK header (`board/revision.h`, `vendor/…`) | A stand-in in the simulation project: `host/include/<path>` with the definitions flight code uses (ask the user for values), and `cmake.include_early: [host.cmake]` with `include_directories("${CMAKE_CURRENT_LIST_DIR}/host/include")` |
| A library (`-lfoo`) | Install it, a host build under `host/lib` (`link_directories` in `host.cmake`), or stand in for the module |
| A `REQUIRED` package | Install it, or `cmake.cache: {<Pkg>_DIR: …}` |
| A false positive (found another way) | `host.ignore: [<name>]` |

## build: configure

`fprime-util generate` in each element's directory. `build_loop.py` runs it only when no
build directory exists (`--fresh` regenerates it).

| Symptom | Fix |
|---|---|
| `build directory … already exists` | Run only `fprime-util build`, or `fprime-util generate -f` |
| `target … not available to deployment`, `Cannot find source file` on an SDK path | The SDK link the generate warning named: `cmake.stub_variants`, or the other options |
| A library's `library.cmake` stops: a variable is not set | `cmake.cache: {VAR: value}`. If it needs a function or variable before libraries are read, `cmake.include_early` |
| `add_subdirectory … already used` / a target defined twice | `cmake.exclude_subdirectories` on the duplicate |
| `differs from CMake cache` | The directory was reached through a symlink. Run from the resolved path printed by `generate`, or `fprime-util generate -f` |
| A changed `cmake.cache` value has no effect | The build directory cached the old one: `fprime-util generate -f` (or `cmake -DNAME=VALUE` on the build directory) |
| `restrict_platforms` stops configuration in a module | A module of a platform library is added on the host: `cmake.exclude_subdirectories` |

## build: compile and link

| Symptom | Fix |
|---|---|
| `'memcpy'/'strlen'/'std::…' was not declared`, `incomplete type std::…` in flight or library code | The flight compiler's headers included it indirectly: `cmake.cxx_flags: {<module dir>: ["-include cstring"]}` (or `<cstdint>`, `<functional>`…), for the module named in the path, or `["…"]` for all |
| `vxWorks.h`, `sysLib.h`, a board header: `No such file` from a project component | The component is OS- or board-specific. Substitute its instances (a project surrogate), stand in for the header (`host/include`), or ask the user to guard the code with `#ifndef WSTS_SIM` in flight |
| Errors in a `…Topology.cpp` naming a driver type or enumerator | The generated aliases (`WstsCompat.hpp`, `wsts-include/`) should cover replaced drivers. If one is missing, a catalog or manifest entry's `compat:` can declare it (see `catalog/wsts-catalog.yaml`, SPI). Report it to the user as a possible toolkit gap |
| `undefined reference` to functions of a module built from its stub variant | The stub is not enough (the generate report, "Modules that change under BUILD_TESTING", says what it lacks). Use a stand-in module of the simulation, or substitute the instance |
| `undefined reference` to a plain C++ library of the project | Its directory has no FPP model: `cmake.extra_subdirectories: [<dir>]`, and `cmake.depends` if the topology needs it |
| Sanitizer runtime (`__asan_…`, `__ubsan_…`) undefined | `BUILD_TESTING` or `ENABLE_SANITIZER_*` set for the build: remove it |
| `-Werror` failures from warnings the flight compiler does not give | `cmake.cxx_flags: {<module>: ["-Wno-error=<warning>"]}` for that module, and tell the user |
| Errors in `Wsts/` or the generated `WstsSetup.cpp` | A toolkit problem or a manifest value (a model `class`/`header` that does not exist, wrong `params` type). Check the `models` entries first |

## run

`fprime-wsts run <generated dir> --duration 20 --json`. Logs are in `wsts-logs/` (per
element for a system). For an assert whose cause is not obvious from the file and line,
get a backtrace: `gdb -q --batch -ex run -ex bt --args <dir>/build-artifacts/*/*/bin/<Deployment> -a 127.0.0.1 -p 50000`,
run from `<dir>` with `WSTS_BRIDGE` set as the manifest's `bridge`.

| Symptom | Fix |
|---|---|
| `assert: {file, line, instances}` with a `kept: true` instance | A kept component reached hardware (often in its set-up). Substitute it; stderr prints the `substitute` entry to add |
| Assert in `Os/MutexInterface.hpp` (`lock()`, status 2) under `BufferManager`, `GenericHub`, `ByteStreamBufferAdapter` in the backtrace | A deployment reached through a hub, simulated alone with its hub driver kept as a real socket: the link is down, the adapter's `DriverNotReady` event goes into the hub, buffers run out, and the buffer manager's own event re-enters it. Flight software would do the same with the other computer off. Substitute the hub driver with `Wsts.SimByteStream`, or simulate the system. Tell the user, because it is a finding about flight |
| Assert in a flight device manager | It checks something the device model does not provide (an ID register, a reply). Add or fix the model on that endpoint |
| Exits before the bridge opens, `Address already in use` | The bridge or GDS port is taken: `bridge:` in the manifest (`tcp:<free port>`), or stop the other run |
| `tick` stays at 0, no telemetry | The tick source is not simulated (generate warned), or the run is paused |
| `errors` > 0 in the final status | An I2C or register endpoint without a model, or a model that rejects what flight sends. `--record run.jsonl` shows each transaction with its status and endpoint |
| `links_up` < `links` | One element did not start or did not open its hub driver (the surrogate reports ready on `start`/`open`). Check that element's `sim.log`. A deployment that only connects after a command will not link up by itself |
| GDS: `cannot merge dictionaries … versions differ` | The elements were built from different checkout states (`-dirty`). Rebuild both from the same state |
| GDS does not start over an existing log directory | `fprime-wsts run` clears its own. Remove a `--log-dir` you chose |
| Events from one element arrive late, or after `clear_histories()` | Expected for elements behind a hub (AGENTS.md, "Things that surprise") |
| Values injected into telemetry are overwritten | Telemetry is downlinked about once a second with the latest value. A free-running model can overwrite an injected value before it is seen |
