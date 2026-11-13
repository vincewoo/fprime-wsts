# SS-4 Fine Sun Sensor — Interface Control Document, Issue 2

## 3. Physical layer
RS-422, 115200 baud, 8N1. The sensor only transmits in response to a request.

## 4. Frame format
| Field | Size | Value |
|---|---|---|
| Sync | 2 | 0xEB 0x90 |
| Length | 1 | number of bytes in Command + Payload |
| Command | 1 | |
| Payload | Length − 1 | |
| CRC | 2 | CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF) over Length, Command and Payload, most significant byte first |

Multi-byte payload fields are little-endian.

## 5. Commands

| Request | Cmd | Payload | Response cmd | Response payload |
|---|---|---|---|---|
| Get version | 0x01 | – | 0x81 | major (u8), minor (u8) |
| Get angles | 0x10 | – | 0x90 | alpha (i16, 0.01 deg), beta (i16, 0.01 deg), flags (u8: bit 0 sun valid, bit 1 albedo warning) |
| Get temperature | 0x12 | – | 0x92 | temperature (i16, 0.1 °C) |
| Set integration time | 0x20 | time (u16, µs) | 0xA0 | – |

## 6. Errors
A frame with a bad CRC is answered with command 0x7F, payload 0x01.
An unknown command is answered with command 0x7F, payload 0x02.
Bytes before a sync word are discarded.
