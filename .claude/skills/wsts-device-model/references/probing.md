# Probing a device

## Contents

- Plan format
- Per interface: UART, I2C, SPI, GPIO
- Finding settings you do not know
- Things that can go wrong on the bench
- Capture format
- Choosing compare modes

## Plan format

```yaml
device: Acme TS-200 temperature sensor, s/n 0042, fw 2.1     # what was probed: say which unit
interface: uart                    # uart | i2c | spi | gpio (one per plan)
endpoint: uart                     # the WSTS endpoint name the model will sit on
tick_hz: 10                        # rate of the element's tick (manifest tick_hz, or the flight tick source)
approved:                          # who allowed which non-read steps, and when
  - {by: "user in chat", date: 2026-11-13, steps: [set_rate_1hz]}

uart: {port: /dev/ttyUSB0, baud: 9600, bytesize: 8, parity: N, stopbits: 1, rtscts: false, idle_listen_ms: 500}
# i2c:  {bus: 1}                                   # or device: /dev/i2c-1
# spi:  {bus: 0, cs: 0, mode: 0, speed_hz: 500000, bits: 8}   # or device: /dev/spidev0.0
# gpio: {chip: gpiochip0, endpoints: {17: tsReady, 18: tsReset}}   # line -> WSTS endpoint

defaults: {timeout_ms: 1000, quiet_ms: 100}

steps:
  - name: identify                 # unique; becomes "step" in the capture
    class: read                    # read | write | hazardous (missing = hazardous)
    source: "DS §6.1 tbl 9; FSW TsDriver.cpp:140"
    tx: {text: "*IDN?\r\n"}        # or {hex: "a5 5a 01 00"}
    until: {terminator: "\r\n"}    # or {length: 12}, or {quiet_ms: 50}; else timeout
    timeout_ms: 500
    compare: exact                 # how conform.py compares the model's reply (below)
    model_params: {}               # model parameters that reproduce the device's state for this step

  - name: temperature
    class: read
    tx: {text: "T?\r\n"}
    until: {terminator: "\r\n"}
    model_params: {temp_c: "22.4"} # read the bench thermometer, or take it from this reply
    compare: "regex:T=-?\\d+\\.\\d\\r\\n"

  - name: stream                   # unsolicited output
    class: read
    listen_ms: 3000
    compare: "regex:(T=-?\\d+\\.\\d\\r\\n){2,4}"

  - name: set_rate_1hz
    class: write
    tx: {text: "RATE 1\r\n"}
    until: {terminator: "\r\n"}
    delay_ms: 100                  # wait before this step
```

I2C, SPI and GPIO steps:

```yaml
  - {name: who_am_i, class: read, addr: 0x68, write: "75", read: 1}           # write then read, repeated start
  - {name: accel, class: read, addr: 0x68, write: "3b", read: 6, compare: "mask:ffff0000ffff"}
  - {name: set_ptr, class: read, addr: 0x50, write: "00 10", pointer_only: true}   # EEPROM address, no data
  - {name: wake, class: write, addr: 0x68, write: "6b 00"}                     # a register write
  - {name: adc_ch0, class: read, write: "06 00 00"}                            # SPI: read length = write length
  - {name: adc_ch0_padded, class: read, write: "06", read: 3, pad: 0x00}       # clock 3 bytes, pad with 0x00
  - {name: ready_level, class: read, line: 17}
  - {name: ready_edges, class: read, line: 17, watch_ms: 2000}
  - {name: release_reset, class: hazardous, line: 18, set: 1, hold_ms: 200}
```

`probe.py` refuses a plan where a step cannot be what its class says:
- driving a pin classed `read`;
- an I2C write with nothing read classed `read` (set `pointer_only` if it only moves the
  register pointer);
- a `read` step that sends more than `max_command_len` bytes (a top-level plan key, default
  64).

It cannot detect any other mislabel, so classify carefully. Short malformed commands (an
unknown word, wrong case, a missing terminator) are reads. Oversized lines, floods,
bursts and random bytes are stress tests: `hazardous`, and only with the user's go-ahead.

## Per interface

### UART

- `idle_listen_ms` (default 500) listens before the first step: it tells you at once
  whether the device talks on its own. Bytes that arrive between steps are kept as
  `_unsolicited` records rather than flushed.
- Run each request/response step with a terminator or length when the protocol has one;
  `quiet_ms` ends a reply that has neither. Each `bs_rx` record carries `latency_ms`
  (time to the first byte): put it in the spec's timing section when flight has a timeout
  near it.
- Binary protocols: get the checksum algorithm right from one captured frame before
  writing the model (try CRC-16/CCITT-FALSE, CRC-16/MODBUS, XOR, 8-bit sum against the
  capture in a few lines of Python).
- Some devices echo commands; some need a wake byte or a break; some answer only after
  a delay that a short `timeout_ms` will miss. Look at the raw capture before concluding
  a command is unsupported.

### I2C

- `probe.py` uses `I2C_RDWR`, so it works even when a kernel driver has claimed the
  address. **Check that first**: `ls /sys/bus/i2c/devices/` and `cat .../name`. A kernel
  driver polling the same device interleaves with your probe, and the user may not want
  it disturbed. Ask before probing a claimed device.
- Reads with side effects exist: FIFO data registers, interrupt status that clears on
  read, and read-to-trigger conversions. They are still `read` (nothing persists), but say so in
  the spec, and model the side effect.
- Do not scan the bus unless the user asks: a quick-write probe can change state on some
  parts (`i2cdetect` warns of this). Ask for the address instead.
- Status in the capture: 0 success, 1 no acknowledge (wrong address, device asleep or
  busy), 5 other.

### SPI

- SPI is full duplex: what comes back during the command bytes is usually junk or status,
  and the data follows. Many devices answer a command in the *next* transfer. Capture
  two transfers in a row to see which.
- Mode (CPOL/CPHA) and speed are host settings. A wrong mode gives shifted or garbage
  bits rather than an error. If replies look bit-shifted, try the other modes, which are
  all harmless.
- `spidev` drives chip select per transfer. A device that needs CS held across several
  transfers needs them in one step (`write` with all bytes).

### GPIO

- Reading a pin or watching its edges is `read`. Driving one is never `read`: reset,
  enable and power pins are usually `hazardous`.
- Requesting a line that a kernel driver or another process holds fails (busy). Do not
  force it.
- `endpoints` maps line numbers to WSTS endpoint names, so the captures use the names
  the model and manifest use.

## Finding settings you do not know

- **Baud rate**: listen (`listen_ms`, `class: read`) at each candidate rate with
  `--port` and a plan per rate. Readable text or a repeating frame shows the right one.
  Do not send at guessed rates without asking: garbage can be a valid command.
- **Line terminator / framing**: capture with `quiet_ms` and read the hex.
- **I2C address**: the datasheet, a strap pin, or the flight code. See the caution
  about scanning above.

## Things that can go wrong on the bench

- **The port is in use.** Flight software, a terminal or ModemManager may have it open.
  `probe.py` opens UARTs exclusively. If it fails with "busy", find out who holds it (`fuser
  /dev/ttyUSB0`) and ask the user. Do not kill the process yourself.
- **Permissions.** `/dev/tty*`, `/dev/i2c-*` and `/dev/spidev*` need the right group
  (`dialout`, `i2c`, `spi`, `gpio`). Tell the user. Do not run the probe with `sudo` unless
  they ask.
- **The device is in an unexpected state** (a previous `write` step, a mode left by
  someone else). Record the state you found in the spec. Ask before restoring it, unless
  the restoring step was approved.

## Capture format

One JSON object per line. The first line is a `# wsts-probe {…}` header with the device,
interface settings, plan and time. Each record is a WSTS recording line (see
`docs/reference.md`, "Scenario and recording files") plus:

| Field | Meaning |
|---|---|
| `step` | the plan step |
| `t` | seconds since the probe started |
| `compare`, `model_params` | copied from the step, for conform.py |
| `latency_ms` | UART: time to the first reply byte |
| `listen_ms`, `t0` | UART listen: duration and start |

`tick` is `t × tick_hz`, so device-paced output lines up with the model's `onTick` when
conform.py replays the file. Because these are recording files, I2C and SPI captures can
also be loaded into a running simulation with `scenario_load <file> responses` as a
stand-in device before the model exists.

## Choosing compare modes

| Mode | Use for |
|---|---|
| `exact` (default) | identification, fixed replies, error replies, and anything the model is given the state for with `model_params` |
| `mask:<hex>` | binary frames with a counter, timestamp or noisy field: zero only those bytes (or bits), and keep the header, length and checksum in the comparison |
| `regex:<pattern>` | ASCII replies whose numbers vary. Constrain the format (digits, decimal places, sign, terminator), not just `.*` |
| `length` | rarely: the content cannot be reproduced at all, such as encrypted or random data |
| `ignore` | idle listens and `_unsolicited` bytes only |

conform.py warns when a regex does not even match the device's own reply.
