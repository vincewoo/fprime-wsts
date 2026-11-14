# Writing Surrogates

The stock surrogates cover the standard F´ driver interfaces. Write your own when:

- a **project driver** reaches hardware through ports of its own;
- you want to replace a **whole device manager**, because its protocol is not what you are
  testing;
- you need a **flight-identical dictionary** for a replaced driver (a *mirror*);
- a **project timer** drives the rate groups from a hardware interrupt.

A surrogate is an ordinary F´ component in your simulation project, with three
conventions.

## The three conventions

**1. The same ports as what it replaces.** For every port the topology connects on the
flight instance, the surrogate has a port with the same name, port type and direction.
Implementing the same FPP interface (`import Drv.I2c`) gives you that. The generator
checks it and names the ports that do not match.

**2. A binding hook**, which generated code calls during `configComponents`:

```cpp
void wstsBind(const char* endpoint, const char* options = "");
```

**3. The methods flight topology code calls** on the flight class. The generator aliases
the flight class name to the surrogate, so
`uart.open("/dev/ttyS1", Drv::PosixUartDriver::BAUD_115K, …)` resolves against your
class: provide `open` and the enumerators it names. When the arguments do not matter, a
variadic template is enough:

```cpp
template <typename... Args> bool open(Args&&...) { return true; }
```

A stand-in may only be empty when the real method has nothing to do in simulation: if
flight teardown calls `camera.stopCapture()` to end a thread, and your replacement runs
one, its `stopCapture()` must stop it.

## Forward to the bus

Port handlers hand each transaction to the bus, which is what gives a surrogate fault
injection, recording and bridge access for free:

```cpp
Drv::I2cStatus SimI2c::writeRead_handler(FwIndexType, U32 addr, Fw::Buffer& w, Fw::Buffer& r) {
    Wsts::Bytes write(w.getData(), w.getData() + w.getSize());
    Wsts::Bytes read(r.getSize(), 0);
    const int status = Wsts::Bus::get().i2cTransact(m_endpoint, addr, write, read);
    std::memcpy(r.getData(), read.data(), read.size());
    return static_cast<Drv::I2cStatus::T>(status);
}
```

`Wsts/Drv/SimByteStream` is the fullest example: receive path, buffer ownership and the
ready signal.

## Register it in a catalog

```yaml title="my-mission-sim/wsts-catalog.yaml"
surrogates:
  - surrogate: Proj.SimFpga
    replaces: [Proj.FpgaDriver]
    options: "mode=fast"            # default wstsBind() options
```

```yaml title="wsts.yaml"
catalogs: [wsts-catalog.yaml]       # searched before the built-in catalog
```

Or choose it for specific instances with `substitute`. A surrogate's own commands, events
and telemetry appear in the simulated dictionary under the replaced instance's base ID,
and the report lists the difference from flight.

## Mirror surrogates

A general-purpose surrogate has no events or telemetry, so replacing a driver removes the
driver's entries from the dictionary. Ground procedures that use them need a **mirror**:
a surrogate with the flight component's exact model.

```bash
fprime-wsts scaffold Drv.LinuxGpioDriver --project ../my-mission --output Surrogates/SimLinuxGpio --name Sim.SimLinuxGpio
```

`scaffold` copies the component's model, with its event and telemetry definitions, under
the new name, and writes the build file, a catalog entry and a README with the remaining
steps: `fprime-util impl` for the implementation templates, then the binding hook and
the bus calls. Names relative to the old module must be qualified in the copy
(`import Gpio` becomes `import Drv.Gpio`); `fpp-check` reports them.
`examples/mission-sim/Surrogates/SimLinuxGpio` is the finished result.

## A surrogate without a bus endpoint

A replacement that talks to flight components over F´ ports only (a time source, a
simulator in a driver's place) needs no `wstsBind`:

```yaml
substitute:
  - {instance: chronoTime, with: SensorSim.SimTime, endpoint: none}
```

The report marks it "no bus endpoint": faults, recording, replay and the bridge do not
reach it. If you want those, keep the stock surrogate in the driver's place and put the
simulator behind the bus instead.

## A surrogate not shaped like the driver

```yaml
substitute:
  - instance: Payload.fpgaDriver
    with: PayloadSim.FpgaSim
    queue_size: 20                 # the flight definition has none: the driver is passive
    ports:
      runIn: schedIn               # same port type, another name
      regReadOut:                  # no counterpart: these flight connections are dropped
      regWriteOut:
```

Renamed ports are renamed wherever flight connects them, inside subtopologies too.
Dropped connections are commented out and listed in the report. When the mapping is not
one port to one port, write a thin shim component with the flight names instead.

## A project timer

When one project component is both clock and tick (a "time unit" whose interrupt cycles
the rate group driver), it often builds on Linux and is kept, and then nothing ever runs.
`generate` warns. Give it a surrogate with the flight port names that ticks from the
simulation:

```cpp
SimTimeUnit::SimTimeUnit(const char* name) : SimTimeUnitComponentBase(name) {
    Wsts::Control::get().registerTickSource();      // no internal ticker is needed
}

void SimTimeUnit::enable() {                          // what flight code calls
    Wsts::Control::get().setTickPeriod(periodNanoseconds);
    m_thread = std::thread([this]() {
        Wsts::Control& control = Wsts::Control::get();
        while (!m_quit) {
            std::this_thread::sleep_for(period / control.rate());
            if (control.paused()) { control.onIdle(); continue; }
            control.onTick();                         // models, scenarios, faults, clocks
            Os::RawTime now;
            (void)now.now();
            this->tickOut_out(0, now);                // then one flight cycle
        }
    });
}
```

Keep the flight component's `Svc.Ping` ports if the health component pings it, and
answer them at once: ping ports are numbered across instances, and a missing one would
shift the rest. `Wsts/Svc/SimTick/SimTick.cpp` is the complete loop. If the timer reaches
its hardware through a register interface, a narrower cut keeps the flight driver: model
its registers instead ([A time unit](../surrogate-guide.md#a-time-unit)).

## More

[Surrogates and device models](../surrogate-guide.md) in the reference has the details:
phase code in flight subtopologies, includes of a replaced driver's header, buffer
ownership and threading.
