# 3. Write the Manifest

The manifest says what the topology cannot: what to keep real, what answers behind each
bus, and how to start a deployment that has no `main()`. This page builds one up in the
order you usually need the keys. Every key is in the
[manifest reference](../reference.md#manifest-wstsyaml).

## Start minimal

```yaml title="wsts.yaml"
project: ../my-mission
deployment: MyDeployment
topology: MyDeployment.MyDeployment   # only if Top/ has several
```

With only this, `generate` replaces every instance whose component is in the
[built-in catalog](../reference.md#surrogate-catalog) and keeps everything else.

## Keep the ground link real

The GDS should connect to the simulated deployment exactly as it does to flight: through
the deployment's own communication stack. Keep the ground link driver:

```yaml
keep: [comDriver]
```

`keep` exempts an instance from substitution. A TCP ground link is not in the catalog
anyway, but listing it documents the decision and protects it if a catalog later learns
to replace it.

## Name instances

Wherever a manifest names an instance (`keep`, `substitute`, the ends of a system link) it
may give:

- the **qualified** FPP name, which always works: `MyDeployment.radioUart`,
  `SensorLink.uart`;
- the **base name** or any trailing part, when exactly one instance ends with it: `uart`.

Instances of imported subtopologies are named by their own module (`SensorLink.uart`), not
the deployment's. An ambiguous name is an error that lists the candidates.

## Substitute what the catalog does not know

`substitute` makes explicit choices, and wins over the catalog:

```yaml
substitute:
  - instance: radioUart            # one instance...
    with: Wsts.SimByteStream
    endpoint: radio                # bus endpoint name; default: the instance's base name
    options: "chunk=256"           # passed to the surrogate's wstsBind()
  - component: Board.RegisterInterface   # ...or every instance of a component
    with: Wsts.SimRegister
```

Common cases:

| You have | Write |
|---|---|
| A project register interface component | `with: Wsts.SimRegister`, and a `kind: register` model ([Register blocks](registers.md)) |
| A project driver or manager with its own ports | `with:` your project surrogate ([Writing surrogates](surrogates.md)), or list it in your catalog |
| A replacement that talks over F´ ports only | `endpoint: none` |
| A surrogate whose port names differ | `ports: {flightName: surrogateName}`; an empty value drops the connection |
| Two substituted instances with the same base name | `endpoint:` on one of them |

## Put models behind the buses

Each substituted driver is a bus endpoint. Without a model behind it, an I2C transaction
gets an address error and a register read returns all ones. Attach models:

```yaml
models:
  - name: imu                      # used by model_set / MODEL_SET
    class: Wsts::RegisterMapI2c    # a stock model, or your own class
    endpoint: i2cBus
    kind: i2c                      # bytestream | i2c | spi | gpio | register | none
    address: 0x68                  # i2c only
    params: {"0x75": "0x68"}       # each becomes model.set(key, value) at start-up
  - name: gps
    class: Wsts::ScriptedByteStream
    endpoint: gpsUart
    kind: bytestream
    params: {text: "$GPGGA,...\n", period: "10"}
```

[Device models](device-models.md) lists the stock models and shows how to write your own.

## Start a deployment that has no `main()`

A Linux deployment's `Main.cpp` is compiled unchanged. A deployment started by the target
OS (a VxWorks downloadable kernel module, for example) has entry points instead. Generate
a Linux `main()` that calls them:

```yaml
main:
  generate: true
  declarations: |
    extern "C" int cdhStart(const char* hostname, int port, int hubPort);
    extern "C" void cdhStop();
  start: cdhStart(hostname, port, 50050)   # hostname and port come from -a and -p
  stop: cdhStop()
```

`start` is any C++ expression, so other arguments of the entry point are written into it.

## Ticks, the bridge and the control component

```yaml
bridge: tcp:50100     # tcp:<port> | tcp:<host>:<port> | unix:<path> | none
tick_hz: 10           # only used when no tick source was substituted
control:              # or `control: none` to leave wstsControl out
  base_id: 0x10030000 # default: the first 0x1000 block above all flight IDs
```

The `wstsControl` instance is added above every flight ID, so flight IDs never move. Give
each deployment you run at the same time its own bridge port.

## A complete example

The example's Payload computer, slightly shortened:

```yaml title="examples/mission-sim/payload.yaml"
project: ../mission
deployment: Payload

catalogs: [wsts-catalog.yaml]        # (1)!

substitute:
  - instance: fpgaRegs               # (2)!
    with: Wsts.SimRegister

models:
  - {name: imu, class: Sim::ImuModel, header: Models/ImuModel.hpp,
     endpoint: i2cBus, kind: i2c, address: 0x68}                     # (3)!
  - {name: power, class: Wsts::RegisterMapI2c, endpoint: i2cBus, kind: i2c,
     address: 0x40, params: {"0x02": "0x6D 0x60", "0x04": "0x01 0xF4"}}
  - {name: adc, class: Wsts::LoopbackSpi, endpoint: spiBus, kind: spi}
  - {name: fpga, class: Wsts::RegisterFile, endpoint: fpgaRegs, kind: register,
     params: {"reg.0x00": "0xF00D0001", "mask.0x00": "0",
              "irq.number": "1", "irq.period": "20", "irq.status": "0x04"}}
  - {name: tempSensor, class: Wsts::ScriptedByteStream, endpoint: sensorUart,
     kind: bytestream, params: {text: "$TEMP,21.5\n", period: "20"}}

cmake:
  stub_variants: [Components/Imager] # (4)!
  cxx_flags:
    lib/sensor-lib/SensorLib/Units: ["-include cstring"]
  include_early: [host.cmake]
  depends: [Models]                  # (5)!

bridge: tcp:50101
```

1. The project's own catalog: a mirror surrogate for the GPIO driver, searched before the
   built-in catalog.
2. A register interface component of a library. No catalog knows it, so it is named here.
3. Two devices on one I2C bus, at different addresses: a project model and a stock one.
4. Host bridges, explained in [step 5](build.md).
5. The module that holds the project model, for the generated set-up that creates it.

Nothing here names the timer, I2C, SPI, UART or GPIO drivers: the catalogs replace them.

## Next

[4. Generate and read the report](generate.md).
