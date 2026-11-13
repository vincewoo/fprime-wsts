# <Device name>: device spec for the WSTS model

<!-- Every fact carries a source tag:
     [DS p.N] / [DS §N]   datasheet, with revision below
     [ICD §N]             interface control document
     [FSW file:line]      flight software that uses it
     [HW capture:line]    observed on the device by probe.py
     A fact with only [DS]/[ICD]/[FSW] tags has not been seen on hardware. -->

| | |
|---|---|
| Part, unit probed | <part number>, s/n <…>, firmware <…> |
| Documents | <title, revision, date> |
| Flight software | <component(s) that talk to it, files> |
| WSTS seam | surrogate <…> on endpoint `<…>`, kind `<bytestream/i2c/spi/gpio/register>` |
| Model | `<class>` in `<header>`, manifest `<element>.yaml` model `<name>` |

## Interface

<Line settings or bus parameters: baud/bits/parity/stop, flow control; I2C address and
speed; SPI mode, speed, bit order, CS behaviour; GPIO lines and polarity.>

## Framing

<ASCII or binary; terminators; sync bytes; length field; checksum algorithm, its span and
byte order; byte order of fields; echo; escaping.>

## Commands / registers used by flight software

| Command or register | Request | Response | Notes | Sources |
|---|---|---|---|---|
| | | | | |

## Other commands / registers (not used by flight software)

<Listed so that a reviewer knows they exist. Model them only if asked.>

## Unsolicited output

<Periodic messages, rate, what starts or stops them; interrupt or data-ready pins.>

## Timing

<Reply latency (from `latency_ms` in captures), conversion times, minimum command
spacing, power-up time, and the flight timeouts that matter.>

## Errors and off-nominal behaviour

<Bad checksum, unknown command, out-of-range argument, busy, NACK, what a reset does.
Include what flight software does on each.>

## Units and scaling

| Quantity | Raw encoding | Engineering units | Range, saturation | Model parameter |
|---|---|---|---|---|
| | | | | |

## Power-up / reset state

## Discrepancies

| Item | Document says | Device does | Capture | Model follows | Decision |
|---|---|---|---|---|---|
| | | | | device | <who decided, or "to confirm"> |

## Open questions

- 

## Verification

| Capture | Steps | conform.py result | Relaxed comparisons (and why) |
|---|---|---|---|
| | | | |

Not modelled: <list, with reasons>.
