# Decisions when configuring a simulation

Each section is one decision: what the survey tells you, what to ask the user when it
does not settle the question, and which manifest key carries the answer. The full key
reference is `docs/reference.md` in the fprime-wsts checkout. Ask only what the survey
and the flight code cannot answer, and offer a recommendation with each question.

Record the reason for every non-obvious entry as a comment in the manifest. The
manifests in `examples/mission-sim/` show the style: whoever reads it next has to know
why a line is there.

## Contents

1. [Scope: which deployments, one or a system](#1-scope)
2. [Where the simulation project lives](#2-where-the-simulation-project-lives)
3. [The ground link](#3-the-ground-link)
4. [How the deployment starts (`main`)](#4-how-the-deployment-starts)
5. [Hardware instances](#5-hardware-instances)
6. [Ticks and time](#6-ticks-and-time)
7. [Register interfaces](#7-register-interfaces)
8. [Devices behind the buses (`models`)](#8-devices-behind-the-buses)
9. [What does not build on the host](#9-what-does-not-build-on-the-host)
10. [Systems: elements, links, ground, ID windows, bridges](#10-systems)
11. [Simulators the project already has](#11-simulators-the-project-already-has)

---

## 1. Scope

**Survey:** every deployment of every project beside the checkout, with hubs and command
splitters.

**Ask:** which deployments to simulate, and whether they are one system (computers that
talk to each other in flight) or separate deployments.

| Situation | Recommend |
|---|---|
| One deployment, or several that never talk to each other | One deployment manifest each (`wsts.yaml`, or `<name>.yaml`) |
| Deployments joined by a hub (`Svc.GenericHub`), a UART, or sockets between them | A system: one manifest per element plus `wsts-system.yaml` |
| A system where the user wants to start with one computer | Write the system manifest with both elements, and run one element alone (`fprime-wsts run <system dir>/<element>`): its link end is simulated and stays down, as if the other computer were silent. Or write a single manifest for the one deployment, with its hub driver substituted (§5) |

Deployments of a system may come from different flight projects. Each element manifest
names its own `project`.

## 2. Where the simulation project lives

**Recommend** a sibling directory named after the flight project with `-sim`
(`../my-mission-sim/`), in its own repository or directory. The flight project is never
written. Each manifest names the flight project with `project: ../my-mission`, relative
to the manifest, and the directory becomes a standalone simulation project.

**Ask** only if the user has a convention (a `sim/` repo, a monorepo). Simulation code
inside the flight project also works (no `project:` key, manifest next to the
deployment), but it mixes simulation sources into flight, so suggest it only if they ask.

The project needs a `library.cmake` only once it has modules of its own: surrogates,
models, simulator components, configuration overrides. Until then leave it out. Templates
are in `assets/sim-project/`. The generated directory (`wsts/`) is a build artifact: put
it in `.gitignore` unless the user wants it checked in, as this repository does for its
example, where CI runs `generate --check`.

## 3. The ground link

**Survey:** byte stream drivers with role `ground`, wired to the com stack (`ComStub`, a
`Com*` subtopology, framer and deframer).

**Default:** `keep: [<instance>]`. The GDS then connects to the simulated deployment as it
does in flight, usually `Drv.TcpClient` to `fprime-gds` on port 50000, which
`fprime-wsts run --gds` provides. Ask only when the ground link is not a socket (a radio
UART, for example). The catalog substitutes such a driver, and the toolkit has nothing
that carries a simulated UART to a GDS. Say so, and agree on a way to command the
deployment: through the bridge (`WstsBridge`, scenario files), or by keeping a socket
driver that a ground or debug build of the deployment already has.

A deployment with no ground link of its own (Payload in the example, reached through the
hub) needs no `keep`: in a system, the GDS connects to the `ground` element.

## 4. How the deployment starts

**Survey:** `main(): yes/no`, and `extern "C"` entry points when there is no `main()`. The
trial generation stops with "has no main()" for such a deployment.

**Ask** the user how the target starts the deployment, if the entry points do not make it
obvious: which function, with which arguments (hub ports, a configuration path). The
generated `main()` provides `hostname` and `port` from `-a` and `-p`:

```yaml
main:
  generate: true
  declarations: |
    extern "C" int cdhStart(const char* hostname, int port, int hubPort);
    extern "C" void cdhStop();
  start: cdhStart(hostname, port, 50050)   # any C++ expression
  stop: cdhStop()
```

Code that includes the target OS's headers must be guarded in the flight source
(`#ifndef WSTS_SIM` or `#ifdef TGT_OS_TYPE_VXWORKS`). That is a change to the flight project,
so propose it to the user and do not make it yourself. See `docs/vxworks-conversion.md`.

## 5. Hardware instances

**Survey table, column "What happens":**

| Entry | Meaning | Usual decision |
|---|---|---|
| `catalog → Wsts.X` | Replaced from the built-in catalog, with an endpoint named after the instance | Nothing to decide; tell the user |
| `keep (ground link)` | See §3 | `keep` |
| `link end (system); alone: substitute…` | A byte stream driver wired to a hub | In a system, a link (§10). For a deployment simulated alone, `substitute` it with `Wsts.SimByteStream`: the other computer is then silent. Keeping it a real socket makes flight software see the link down, and run its handling of that. In the example, Payload asserts: events about the dead link go into the hub, which runs out of buffers and re-enters its buffer manager. Keep it only to test exactly that, and tell the user what it found |
| `decide: register interface` | A project component with register read/write ports | §7 |
| `decide: cannot build on Linux` | Platform-restricted, not in the catalog. Generation stops on it | `substitute` with a stock surrogate whose ports fit (the error lists them), or a project surrogate |
| `decide: project component that may reach hardware` | Its sources open `/dev`, call `ioctl`/`mmap`, include device or OS headers | Read the code with the user. If it reaches hardware, substitute it: a mirror surrogate (`fprime-wsts scaffold <Module.Component> --project <flight project> --output <sim>/Surrogates/Sim<Component>`, then a project catalog entry or a `substitute` entry) or a stock one whose ports fit. If the access is guarded or harmless on Linux, keep it and note why |
| `decide: device on a socket/driver the catalog does not know` | A byte stream driver (often a socket) to a device | `substitute: [{instance: X, with: Wsts.SimByteStream}]`, then a model on its endpoint |

The survey's heuristics can miss a driver that builds on Linux and reaches hardware
through a library. **Ask** the user: "Which of these components talk to hardware?" Show
the project components of the topology that the survey did not list, and ask whether any
of them open devices. A kept driver whose set-up touches hardware usually shows up at run
time as an F´ assert: `fprime-wsts run --json` names the file, line and kept instance
(see troubleshooting, "run").

Endpoint names default to the instance's base name. Two substituted instances with the
same base name (a `uart` in two subtopologies) need `endpoint:` on one of them. The
survey lists such collisions.

## 6. Ticks and time

**Survey:** rate group tick sources and the time source, with `handled: false` when a
project component that the catalog does not replace drives them.

- `Svc.LinuxTimer` and `VxWorksSvc.VxWatchDogTimer` are replaced by `Wsts.SimTick`. Nothing
  to decide.
- A project timer (an FPGA interrupt, a hardware clock) is kept and probably never fires:
  no rate group runs and no telemetry is downlinked. It needs a project surrogate
  (`docs/surrogate-guide.md`, "A project timer"). If its interrupt arrives through a
  register interface component, a register model can be the tick source instead
  ("A time unit"). This is real work: agree with the user on the approach before writing
  code.
- A project time source that reads a hardware clock keeps time stamps from advancing.
  Substitute it, as `cdh.yaml` does with `SensorSim.SimTime` (`endpoint: none`).

## 7. Register interfaces

**Survey:** `decide: register interface` with the ports and their roles.

**Recommend** `substitute: [{instance: <name>, with: Wsts.SimRegister}]` (or
`component:` for every instance). The generator writes the surrogate again with the
flight component's own port types, so the flight drivers run unchanged. Then a register
block on its endpoint: `Wsts::RegisterFile` with `params` for the registers flight reads
(`reg.<offset>`, `mask.`, `w1c.`, `irq.*`), or a project model derived from it.

**Ask:** which registers flight software reads at start-up and what values make it happy
(identity, status). Usually the driver code says (a check on an ID register). Without a
block, every read returns all ones, and a driver that checks an ID asserts or reports
an error.

A connected port of another shape (a command port the driver calls) is an error that
names it: drop it with `ports: {<port>: }` if the simulation can do without it, or write a
mirror surrogate (`docs/surrogate-guide.md`, "A register block").

## 8. Devices behind the buses

**Survey:** the endpoints that will exist and their kind. Without a model:

| Kind | Flight software sees |
|---|---|
| `i2c` | address error on every transaction (counted in `BusErrors`) |
| `spi` | reads of zeros |
| `bytestream` | sends accepted, nothing ever arrives |
| `gpio` | the level it wrote; inputs stay low unless forced (`gpio_force`) |
| `register` | all ones (counted in `BusErrors`) |

**Recommend** a staged approach. For the first build, attach stock models where they are
enough. Leave the rest without a model, and tell the user what flight software will see on
each.

| Device | Stock model | `params` |
|---|---|---|
| Register-addressed I2C part (sensor, power monitor) | `Wsts::RegisterMapI2c`, at its `address` | `"0x<reg>": "0x<byte> 0x<byte>"` |
| Serial device that reports periodically | `Wsts::ScriptedByteStream` | `text`, `period` (ticks) |
| SPI part where zeros or an echo is acceptable | `Wsts::LoopbackSpi` | |
| Memory-mapped block | `Wsts::RegisterFile` | §7 |

The stock models and their parameters are in `Wsts/Core/Models.hpp`. For a device with a
real protocol (commands and replies, checksums, state), hand off to the
**`wsts-device-model`** skill, which writes the model from a datasheet and checks it
against the hardware. Note the endpoint and kind it needs.

**Ask:** for each endpoint, which device is behind it (part number), and whether it matters
for the first run. Read the flight device manager to see what it checks at start-up. An
ID register that does not match usually means an error event or an assert.

## 9. What does not build on the host

These come from the trial generation's warnings and from `check-host`. Each has a
manifest key, so nothing needs the environment or a change to flight.

| Finding | Ask | Key |
|---|---|---|
| A module links a file of the target's SDK and has a stub under `if (BUILD_TESTING)` | Is the stub enough for a simulation? It usually is | `cmake.stub_variants: [<module dir>]` |
| A module links the SDK and has no stub | Is there a host build of the SDK? Otherwise substitute the instances that need it, or exclude the module and alias its target to a stand-in | `cmake.cache: {SDK_ROOT: …}`, `substitute`, `cmake.exclude_subdirectories` + `cmake.include` |
| A system header or library from the target's sysroot is missing | Is it on a package the user can install, or a board header the simulation should provide? | Install it, or a stand-in under `host/include` added by a `cmake.include_early` file (`include_directories`) |
| Code compiles for flight only because of an indirect include | — (no question: it is the host's standard library) | `cmake.cxx_flags: ["-include cstring"]`, for the whole build or `{<module dir>: [...]}` |
| The flight root `CMakeLists.txt` does something other than plain `set()` | What it is for | The key the warning names |
| A platform library adds modules that do not configure on Linux | — | `cmake.exclude_subdirectories` |
| A header check that is wrong (the header is found another way) | — | `host.ignore: [<name>]` |

Never set `BUILD_TESTING` for the whole build. It turns on sanitizers and unit-test
targets, and it swaps every module to its test variant (`docs/vxworks-conversion.md`, "Why
not BUILD_TESTING for the whole build").

## 10. Systems

**Survey:** byte stream drivers wired to hubs (`hub link end`), ground candidates, command
splitters and their opcode base, each deployment's ID range.

- **Element names.** Short and lower case (`cdh`, `payload`). They appear in commands
  (`fprime-wsts run`, `--env payload.X=1`) and in `WstsSystem["payload"]`.
- **Links.** Pair the hub drivers: the server end in one element and the client end in
  the other. The survey shows their set-up lines (ports, hosts) so the pairing can be
  checked. `links: [{name: hub, a: cdh.hubDriver, b: payload.hubDriver}]`. A link
  substitutes both ends, so do not also `keep` them. **Ask** when there are more than two
  ends, or when it is not clear which end pairs with which.
- **Ground.** The element whose ground link is kept (`ground: cdh`). The GDS connects
  there with the merged dictionary.
- **ID windows.** When a command splitter routes by opcode, `ids` of each element must
  match the routing: below the splitter's remote base for the local element, and from the
  base for the remote one (`[0x00000000, 0x1FFFFFFF]` and `[0x20000000, 0x2FFFFFFF]` for a
  base of `0x20000000`). Each element's `wstsControl` is placed inside its window, so
  its commands are routed to it. Without a splitter, `ids` can be left out as long as the
  elements' flight IDs do not overlap. The generator reports any overlap.
- **Bridges.** Leave them out: elements get `tcp:50100`, `tcp:50101`, … in order. Set
  them only if those ports are taken on the user's machine.
- **Links not built yet:** GPIO lines between computers, and datagram semantics. Tell the
  user if the flight system needs them (`docs/systems.md`, "Things to know").

## 11. Simulators the project already has

**Ask** whether the project has simulators written as F´ components, or a hardware-in-the-
loop simulator. If it does, they can be added to the simulated topology
(`simulators`, `devices`, `clocks`, `libraries.sim`). `cdh.yaml` in the example is the
pattern, and `docs/surrogate-guide.md` ("Device simulators as F´ components") explains
it. This is not part of a first bring-up: get the deployment building and running first.
