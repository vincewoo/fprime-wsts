# ACME TS-200 Digital Temperature Sensor — Datasheet Rev C (2024-03)

## 1. Overview
The TS-200 is a precision RTD temperature sensor with an RS-232 serial interface.
Range -40 °C to +125 °C, resolution 0.01 °C, accuracy ±0.1 °C.

## 4. Electrical interface
RS-232, 9600 baud, 8 data bits, no parity, 1 stop bit, no flow control.

## 5. Protocol
ASCII. Each command is terminated by CR LF (0x0D 0x0A). Each response is terminated by
CR LF. Commands are case sensitive. The sensor answers within 20 ms.

### 5.1 Command summary

| Command | Description | Response |
|---|---|---|
| `*IDN?` | Identification | `ACME,TS-200,<serial>,<firmware>` |
| `T?` | Read temperature | `T=<sign><dd>.<dd>` in the selected units, e.g. `T=+21.50` |
| `STAT?` | Status flags | `S=0x<hh>`; bit 0 sensor open, bit 1 over-temperature (>110 °C), bit 2 calibration invalid |
| `RATE <n>` | Stream `T=` messages every n seconds (n = 0..10); 0 stops streaming | `OK` |
| `UNITS C` / `UNITS F` | Select units for `T?` and the stream | `OK` |
| `CAL <offset>` | Store a calibration offset in °C in EEPROM (-5.00..+5.00) | `OK` |
| `FACTORY` | Restore factory defaults, erasing the stored calibration | `OK` (sensor restarts, 2 s) |

### 5.2 Errors
An unknown or malformed command is answered with `ERR`.
An argument out of range is answered with `ERR`.

### 5.3 Streaming
When streaming, the sensor sends `T=` messages on its own at the selected interval, in
the same format as the `T?` response.

## 6. Power-up state
Units °C, streaming off (RATE 0), calibration offset as stored in EEPROM.
