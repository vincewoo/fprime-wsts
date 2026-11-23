# SpaceWire

A SpaceWire link is simulated as a **packet endpoint**: flight software sends whole packets,
the bus routes each one by its address bytes to the node at its logical address, and a node
answers with packets of its own. The node is a model (the toolkit's RMAP target, or one of
yours), a Python process, or nothing, which the bus reports.

## The driver this assumes

F´ has no SpaceWire driver of its own. A SpaceWire driver written for F´ v4 fits the
framework's **passive buffer driver** interface (`Drv.PassiveBufferDriver`), the one its
buffer-based drivers and their clients use:

| Port | Direction | What travels |
|---|---|---|
| `bufferIn` | into the driver | One packet to transmit, its address bytes first |
| `bufferInReturn` | out of the driver | The same buffer, once the packet is sent |
| `bufferOut` | out of the driver | One packet received, in a buffer of its own |
| `bufferOutReturn` | into the driver | That buffer, once the client is done with it |
| `allocate`, `deallocate` | out of the driver | Buffers for received packets, from a buffer manager |

The client (an instrument manager, an RMAP initiator, a CCSDS packet router) is a
`Drv.PassiveBufferDriverClient`. Nothing else is assumed: what the packets mean (RMAP,
CCSDS over SpaceWire, a protocol of your own) is the client's and the node's.

The only F´ SpaceWire code public when this was written fits: fprime-community's
[CommLib](https://github.com/codeflight1/fprime-CommLib) passes whole packets as `Fw.Buffer`
between its controller and the driver. JPL's
[Sphinx drivers](https://github.com/fprime-community/fprime-sphinx-drivers) are RMAP calls
with pointer arguments, in the XML component format of earlier F´ releases.

## Replace the driver

`Wsts.SimSpaceWire` has the driver's interface and port names. Name it for the project's
driver in the manifest (no catalog can: the driver is the project's):

```yaml title="payload.yaml"
substitute:
  - instance: spw
    with: Wsts.SimSpaceWire
```

Its endpoint is named after the instance (`spw`). A driver with ports of other names maps
them with `ports:` ([Surrogates of another kind](../reference.md#surrogates-of-another-kind-or-with-other-port-names)),
and a call the topology code makes on the driver to set it up (`open`, `configure`, `start`,
`stop`) is accepted and does nothing.

## Routing

The bus routes as SpaceWire routers do. The leading **path address** bytes (0 to 31) are
consumed on the way, and the first byte left, the **logical address** (32 to 255), names the
node, which receives the packet from that byte on. A node at `address: any` receives the
logical addresses that have no node of their own. A packet that reaches no node (no logical
address, or no node there) is lost, as on a network: the send succeeds, and the bus counts
the packet as unanswered and posts `UnansweredTraffic` once for that address.

A node's reply reaches flight software **during the send**, before `bufferIn` returns, as a
model's reply does on a byte stream. On the board it comes later, from the receive
interrupt. A client that takes a lock in its send path that its receive port takes too would
deadlock here; one with an asynchronous receive port, or no lock, does not notice.

## The RMAP target

`Wsts::RmapTarget` is a block of memory at a logical address that flight software reaches
with RMAP (ECSS-E-ST-50-52C), as it reaches an instrument's or a controller's registers and
buffers over SpaceWire:

```yaml title="payload.yaml"
models:
  - name: sunSensor
    class: Wsts::RmapTarget
    endpoint: spw
    kind: packet
    address: 0xFE                # the node's logical address
    params:
      key: "0x20"                # the key commands carry
      size: "0x100"              # bytes of memory (default 4096), from base (default 0)
      "mem.0x04": "0x00 0x64"    # bytes from that address on
      "mem.0x10": "sine(offset=30000, amplitude=15000, period=60) as s32be"
```

| | |
|---|---|
| Commands | Write (verified or not, acknowledged or not), read, read-modify-write; the address incremented or not |
| Replies | By the reply address the command gave (its leading zero bytes are padding), to the initiator's logical address |
| Discarded, no reply | A packet that is not an RMAP command (another protocol, a reply), or whose header CRC is wrong |
| Statuses in a reply | 2 command code not used, 3 wrong key, 4 data CRC wrong, 5 the packet ended early, 6 too much data, 10 memory outside the target, 11 read-modify-write data length |
| Unverified writes | Made before the data CRC is checked, as targets make them: data with a bad CRC lands, and the reply says 4 |
| Memory | Big-endian, as SpaceWire hardware is. `mem.<address>` takes bytes, or a [signal](device-models.md#values-that-change-over-time) stored there on every tick, until bytes are given for it; a signal wider than a byte needs its order (`as u16be`) |

Change the memory or the key while it runs with `model_set sunSensor mem.0x10 "0x00 0x00 0x75 0x30"`.

A model of an instrument with behaviour of its own starts from `new-model`:

```bash
fprime-wsts new-model SunSensorModel --kind packet --endpoint spw --address 0xFE
```

writes a C++ model on `Wsts::RmapTarget` whose measurement follows a signal; `--python`
writes a Python node that answers RMAP reads instead.

## A node in Python

From a test, or `run.models`:

```python
def sensor(packet: bytes):
    """A packet for the node, from its logical address on: return the reply packet, or None"""
    ...

sim.serve_packet("spw", 0xFE, sensor)          # or "any"
sim.inject_packet("spw", bytes.fromhex("67010c00fe00010000000476..."))  # unasked
sim.packets_sent("spw")                        # what flight software sent, since subscribe()
```

A node served this way stands in for the model at the same address, which answers again
when the client disconnects. `tests/e2e/test_spacewire.py` has one that answers the example's
sun sensor with an angle of its own.

## Faults, recording and replay

The endpoint takes the usual [faults](../reference.md#faults), on whole packets: `drop` loses
one, `corrupt` XORs every byte (its address too, so it may go astray, and its CRCs fail),
`delay` holds it, `duplicate` delivers it twice, `error` fails the send. `to_device` faults
act on what flight software sends, `to_flight` on what it receives. `INJECT_PACKET` from the
GDS, `inject_packet` from the bridge or a scenario, delivers one packet.

A recording has `pkt_tx` (with the logical address it was routed to) and `pkt_rx` lines, and
`status --bus` the traffic per logical address. In `responses` mode the recorded packets to
flight software are delivered again with the nodes bypassed, and in stepped time two runs
of the same inputs record the same packets.

## What is not simulated

The link itself: its state (started, connecting, running, errors), its rate and time-codes
are not modelled, nor the time a packet takes. An error end of packet (EEP) has no fault of
its own (`corrupt` and `drop` stand in for one). Routers are not modelled beyond consuming
path addresses: every logical address is one hop away.

## The example

The example's Payload has a sun sensor on SpaceWire: `Components.SpwDriver`, a v4 driver on
`Drv.PassiveBufferDriver`, and `Components.SunSensor`, which reads the sun angle over RMAP
every 10 Hz tick, reports a read with no reply by the next tick (`NoReply`), and sets its
integration time with a verified write (`SET_INTEGRATION`). `payload.yaml` replaces the
driver and puts the RMAP target at the sensor's address.
