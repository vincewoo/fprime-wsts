# NOVA PX-310 Absolute Pressure Sensor — Datasheet v1.4

Range 260–1260 hPa, 24-bit pressure, 16-bit temperature. I2C up to 400 kHz.
7-bit address 0x5C (SA0 low) or 0x5D (SA0 high).

## 6. I2C protocol
A write sets the register pointer (first byte), followed by data bytes to write.
A read returns bytes starting at the pointer. When IF_ADD_INC (CTRL_REG2 bit 4) is 1, the
pointer increments after each byte read or written. A read of a reserved address returns 0x00.

## 7. Register map

| Addr | Name | Access | Reset | Description |
|---|---|---|---|---|
| 0x0F | WHO_AM_I | R | 0xB3 | Device identification |
| 0x10 | CTRL_REG1 | R/W | 0x00 | bits 6:4 ODR (000 one-shot/power-down, 001 1 Hz, 010 10 Hz, 011 25 Hz, 100 50 Hz, 101 75 Hz); bit 1 BDU |
| 0x11 | CTRL_REG2 | R/W | 0x10 | bit 7 BOOT; bit 4 IF_ADD_INC; bit 2 SWRESET (self-clearing); bit 0 ONE_SHOT (self-clearing) |
| 0x27 | STATUS | R | 0x00 | bit 0 P_DA new pressure available; bit 1 T_DA new temperature available. Cleared when the corresponding OUT registers are read |
| 0x28 | PRESS_OUT_XL | R | 0x00 | Pressure, bits 7:0 |
| 0x29 | PRESS_OUT_L | R | 0x00 | Pressure, bits 15:8 |
| 0x2A | PRESS_OUT_H | R | 0x00 | Pressure, bits 23:16 |
| 0x2B | TEMP_OUT_L | R | 0x00 | Temperature, bits 7:0 |
| 0x2C | TEMP_OUT_H | R | 0x00 | Temperature, bits 15:8 |

## 8. Output data
Pressure: 24-bit two's complement, P[hPa] = PRESS_OUT / 4096.
Temperature: 16-bit two's complement, T[°C] = TEMP_OUT / 100.

## 9. Operation
Power-down after reset. Setting ODR ≠ 0 starts continuous conversion at that rate. In
power-down, writing ONE_SHOT = 1 performs a single conversion; the result is available
(P_DA, T_DA set) after at most 30 ms, and ONE_SHOT clears itself.
SWRESET = 1 resets the registers to their reset values.
