# REST API

Other programs control Tally Tapir through its REST API: a vision mixer
script, a show control system, a test rig, a shell script. Everything the Send
and Receive views do is available here.

- [Basics](#basics)
- [Login](#login)
- [Endpoints](#endpoints)
- [Sender](#sender)
- [Receiver](#receiver)
- [Tally bits](#tally-bits)
- [Errors](#errors)
- [Recipes](#recipes)

## Basics

| | |
|---|---|
| Base address | `http://localhost:8088/api/v1/` |
| Format | JSON in both directions |
| Reading | `GET` |
| Changing settings | `PATCH` with a JSON object that names only the fields to change |
| Actions | `POST` with `{}` as body |

Every request that changes something (`PATCH`, `POST`) must carry the header
`Content-Type: application/json`. A request without it is refused with status
415. This keeps web pages in a browser from triggering actions.

The API can be switched off in the Settings tab or with `--api off`. It then
answers 403. It is independent of the web interface: either can run without
the other.

By default the web server answers on the local machine only. Start with
`--host 0.0.0.0` to reach the API from other machines, and then give it a
login and HTTPS, see [security-audit.md](security-audit.md).

## Login

Without a login configured, no credentials are needed.

With an API login (Settings tab, or `--api-login USER`), every request needs
HTTP Basic credentials:

```bash
curl -u robot http://localhost:8088/api/v1/sender
```

curl asks for the password. After five failed logins from one address, that
address has to wait: 30 seconds at first, twice as long with every further
failure, up to 15 minutes. During that time the API answers 429 with a
`Retry-After` header.

The API login is separate from the login of the web interface.

## Endpoints

| Method | Path | Does |
|---|---|---|
| GET | `/api/v1/` | Name, version and this list |
| GET | `/api/v1/sender` | Sender settings and status |
| PATCH | `/api/v1/sender` | Change sender settings |
| POST | `/api/v1/sender/connect` | Open the connection |
| POST | `/api/v1/sender/disconnect` | Close the connection |
| POST | `/api/v1/sender/send` | Send the current packet once |
| GET | `/api/v1/receiver` | Receiver settings, displays seen and log |
| PATCH | `/api/v1/receiver` | Change receiver settings |
| POST | `/api/v1/receiver/listen` | Start listening |
| POST | `/api/v1/receiver/stop` | Stop listening |
| POST | `/api/v1/receiver/clear` | Forget received packets |
| GET | `/api/v1/serial-ports` | Serial ports of the machine |

Every sender and receiver endpoint answers with the full state, so one request
changes something and shows the result. Actions add `"ok": true` or
`"ok": false`. When an action fails, `status.error` says why.

## Sender

### Reading the state

```bash
curl http://localhost:8088/api/v1/sender
```

```json
{
  "instance": "d1c3fa46",
  "revision": 4,
  "settings": {
    "protocol": "v5.0",
    "umd_id": 3,
    "screen": 0,
    "text": "CAM 3",
    "utf16": false,
    "brightness": 3,
    "tally_bits": 1,
    "clock": false,
    "clock_tenths": false,
    "interval_ms": 1000,
    "interval_enabled": false,
    "tally_toggle": false,
    "send_on_change": true,
    "hex_braces": false,
    "transport": "udp",
    "host": "127.0.0.1",
    "port": 9988,
    "serial_port": "",
    "baudrate": 38400,
    "bytesize": 8,
    "parity": "E",
    "stopbits": 1
  },
  "status": {
    "connected": true,
    "connecting": false,
    "connection": "UDP 127.0.0.1:9988",
    "error": "",
    "packet_error": "",
    "packet_hex": "0F 00 00 00 00 00 03 00 C1 00 05 00 43 41 4D 20 33",
    "packet_length": 17,
    "sent_count": 1,
    "last_sent": 1794341034.6,
    "tally_bits_sent": 1,
    "lamps": { "rh": 1, "txt": 0, "lh": 0 },
    "v3_tallies": 1,
    "log": [
      {
        "time": 1794341034.6,
        "length": 17,
        "hex": "0F 00 00 00 00 00 03 00 C1 00 05 00 43 41 4D 20 33"
      }
    ]
  }
}
```

`revision` rises with every change, which lets a client notice that something
changed. `instance` changes when the program was restarted.

### Settings

| Field | Values | Meaning |
|---|---|---|
| `protocol` | `"v3.1"`, `"v4.0"`, `"v5.0"`, `"v5.0w"` | TSL version. `v5.0w` is V5.0 with the DLE/STX wrapper for TCP |
| `umd_id` | 0 to 65535 | Display address. V3.1 and V4.0 allow 0 to 126 |
| `screen` | 0 to 65535 | V5.0 screen |
| `text` | up to 256 characters | UMD text. V3.1 and V4.0 send the first 16 |
| `utf16` | true, false | V5.0 text as UTF-16LE |
| `brightness` | 0 to 3 | Brightness |
| `tally_bits` | 0 to 63 | The lamps, see [Tally bits](#tally-bits) |
| `clock` | true, false | Send the time of day as text |
| `clock_tenths` | true, false | Clock with tenths of a second |
| `interval_ms` | 10 to 3600000 | Time between repeated sends |
| `interval_enabled` | true, false | Repeat the packet every `interval_ms` |
| `tally_toggle` | true, false | Switch the tallies off and on with every interval |
| `send_on_change` | true, false | Send as soon as a value changes, and once on connect |
| `hex_braces` | true, false | Hex bytes as `{AA}{BB}` instead of `AA BB` |
| `transport` | `"udp"`, `"tcp"`, `"serial"` | How packets leave |
| `host` | name or address | Where UDP and TCP packets go |
| `port` | 1 to 65535 | Port of that host |
| `serial_port` | device name | For example `/dev/ttyUSB0` or `COM3` |
| `baudrate` | 50 to 4000000 | Serial speed |
| `bytesize` | 5, 6, 7, 8 | Serial data bits |
| `parity` | `"N"`, `"E"`, `"O"`, `"M"`, `"S"` | Serial parity |
| `stopbits` | 1, 1.5, 2 | Serial stop bits |

Changing a connection setting (`transport`, `host`, `port` or a serial
setting) closes an open connection.

### Status

| Field | Meaning |
|---|---|
| `connected`, `connecting` | State of the connection |
| `connection` | For example `UDP 192.168.1.50:9800` |
| `error` | Last error of a connection or send, empty if none |
| `packet_error` | Why the current settings give no valid packet, for example a UMD ID above 126 in V3.1 |
| `packet_hex`, `packet_length` | The packet the current settings produce |
| `sent_count`, `last_sent` | Packets sent, and when the last one went (seconds since 1970) |
| `tally_bits_sent`, `lamps`, `v3_tallies` | The tallies as sent right now, which differs from the setting during the dark phase of the tally toggle |
| `log` | The last 20 packets sent |

### Changing and sending

```bash
curl -X PATCH http://localhost:8088/api/v1/sender -H 'Content-Type: application/json' -d '{"text": "CAM 3", "umd_id": 3, "tally_bits": 1}'
```

```bash
curl -X POST http://localhost:8088/api/v1/sender/connect -H 'Content-Type: application/json' -d '{}'
```

With `send_on_change` (the default) a connected sender sends at once when
`protocol`, `umd_id`, `screen`, `text`, `utf16`, `brightness` or `tally_bits`
change, and once when the connection opens. Without it, send with:

```bash
curl -X POST http://localhost:8088/api/v1/sender/send -H 'Content-Type: application/json' -d '{}'
```

## Receiver

### Reading the state

```bash
curl http://localhost:8088/api/v1/receiver
```

The answer has `settings` and `status`. The status of a receiver that got one
packet:

```json
{
  "listening": true,
  "description": "UDP 127.0.0.1:9988",
  "error": "",
  "clients": 0,
  "packet_count": 1,
  "problem_count": 0,
  "watched": {
    "protocol": "v5.0",
    "index": 3,
    "text": "CAM 3",
    "brightness": 3,
    "screen": 0,
    "tallies": null,
    "lh": 0,
    "txt": 0,
    "rh": 1,
    "time": 1794341034.6,
    "count": 1
  },
  "display_count": 1,
  "displays": [ { "protocol": "v5.0", "index": 3, "text": "CAM 3", "...": "..." } ],
  "log": [
    {
      "time": 1794341034.6,
      "source": "127.0.0.1:57114",
      "protocol": "v5.0",
      "length": 17,
      "hex": "0F 00 00 00 00 00 03 00 C1 00 05 00 43 41 4D 20 33",
      "summary": "screen 0  ID 3  'CAM 3'  L:off T:off R:red  brightness 3",
      "problems": [],
      "matched": true
    }
  ]
}
```

| Field | Meaning |
|---|---|
| `listening`, `description` | Whether and where the receiver listens |
| `error` | Why listening failed or stopped, empty if it did not |
| `clients` | Number of TCP senders connected |
| `packet_count`, `problem_count` | Packets received, and how many of them had problems |
| `watched` | The latest display message that matches the filter, or `null` |
| `displays`, `display_count` | One entry per screen and UMD ID with its latest state. At most 200 are listed |
| `log` | The last 40 packets, oldest first. `problems` lists what was wrong, `matched` tells whether the packet matches the filter |

In a display entry, `lh`, `txt` and `rh` are the left lamp, the text tally and
the right lamp (0 off, 1 red, 2 green, 3 amber). `tallies` holds the four
tallies of V3.1 and V4.0 as a number, bit 0 is tally 1. A version that lacks
one of them reports `null`. `screen` is `null` for V3.1 and V4.0.

### Settings

| Field | Values | Meaning |
|---|---|---|
| `transport` | `"udp"`, `"tcp"`, `"serial"` | Where to listen. TCP makes the receiver a server for up to 32 senders |
| `bind` | address | `"0.0.0.0"` for all network interfaces, `"127.0.0.1"` for this machine only |
| `port` | 1 to 65535 | Port to listen on |
| `serial_port`, `baudrate`, `bytesize`, `parity`, `stopbits` | as for the sender | Serial line |
| `protocol` | `"auto"`, `"v3.1"`, `"v4.0"`, `"v5.0"`, `"v5.0w"` | `auto` recognises the version of each packet |
| `watch_screen` | 0 to 65535, or `null` | Screen the watched display follows, `null` for any. Applies to V5.0 |
| `watch_id` | 0 to 65535, or `null` | UMD ID the watched display follows, `null` for any |
| `hex_braces` | true, false | Hex bytes as `{AA}{BB}` |

Changing a listen setting stops the receiver. The V5.0 broadcast address 65535
matches every filter.

### Listening

```bash
curl -X PATCH http://localhost:8088/api/v1/receiver -H 'Content-Type: application/json' -d '{"transport": "udp", "port": 9800, "watch_id": 3}'
```

```bash
curl -X POST http://localhost:8088/api/v1/receiver/listen -H 'Content-Type: application/json' -d '{}'
```

## Tally bits

`tally_bits` is one number for all lamps, two bits per lamp:

| Lamp | Red | Green | Amber |
|---|---|---|---|
| Right lamp | 1 | 2 | 3 |
| Text | 4 | 8 | 12 |
| Left lamp | 16 | 32 | 48 |

Add the values of the lamps that should be lit. Left and right red is
16 + 1 = 17. Everything off is 0.

V3.1 has four tallies and no colours. There the values 1, 2, 16 and 32 set
tally 1, 2, 3 and 4, and the values for the text lamp have no effect. V4.0
sends both: the four tallies and the three lamps.

## Errors

| Status | When | Body |
|---|---|---|
| 400 | A value was refused, or the body is not a JSON object | `{"error": "Brightness must be 0 to 3", ...}` together with the unchanged state |
| 401 | The API requires a login and none or a wrong one was sent | `{"error": "Login required"}` |
| 403 | The API is switched off, or the request came from another web page | `{"error": "The REST API is switched off"}` |
| 404 | Unknown path | `{"error": "..."}` |
| 405 | Wrong method for the path | `{"error": "..."}` |
| 413 | Body larger than 64 kB | `{"error": "..."}` |
| 415 | A changing request without `Content-Type: application/json` | `{"error": "..."}` |
| 429 | Too many failed logins from this address | `{"error": "Too many failed logins. Try again in 30 seconds."}` |

A `PATCH` is all or nothing: if one value is refused, none is applied.

A failed action is not an HTTP error. `POST /sender/connect` to a host that
does not answer returns 200 with `"ok": false` and the reason in
`status.error`.

## Recipes

Ready-to-run versions of these are in [examples/](../examples/).

### A red tally for camera 3

```bash
curl -X PATCH http://localhost:8088/api/v1/sender -H 'Content-Type: application/json' -d '{"host": "192.168.1.50", "port": 9800, "umd_id": 3, "text": "CAM 3", "tally_bits": 17}'
```

```bash
curl -X POST http://localhost:8088/api/v1/sender/connect -H 'Content-Type: application/json' -d '{}'
```

### Tally off again

```bash
curl -X PATCH http://localhost:8088/api/v1/sender -H 'Content-Type: application/json' -d '{"tally_bits": 0}'
```

### Blink a tally once a second

```bash
curl -X PATCH http://localhost:8088/api/v1/sender -H 'Content-Type: application/json' -d '{"tally_bits": 1, "interval_ms": 1000, "interval_enabled": true, "tally_toggle": true}'
```

### Send the time of day as UMD text

```bash
curl -X PATCH http://localhost:8088/api/v1/sender -H 'Content-Type: application/json' -d '{"clock": true, "clock_tenths": false}'
```

### Check what a device sends

Let the receiver listen, then read what it saw and whether packets had
problems:

```bash
curl -X POST http://localhost:8088/api/v1/receiver/listen -H 'Content-Type: application/json' -d '{}'
```

```bash
curl -s http://localhost:8088/api/v1/receiver | python3 -c "import json, sys; s = json.load(sys.stdin)['status']; print(s['packet_count'], 'packets,', s['problem_count'], 'with problems'); [print(d['index'], repr(d['text'])) for d in s['displays']]"
```

### From Python

[examples/api_client.py](../examples/api_client.py) holds a small client class
that needs only the standard library:

```python
from api_client import TallyTapirApi, lamp_bits

api = TallyTapirApi("http://localhost:8088")
api.set_sender(host="192.168.1.50", port=9800, umd_id=3, text="CAM 3",
               tally_bits=lamp_bits(left="red", right="red"))
api.connect()
```

To build TSL into a Python program without running Tally Tapir at all, use the
library: [tally-tapir-lib](https://github.com/MarcusMa-dotcom/tally-tapir-lib).
