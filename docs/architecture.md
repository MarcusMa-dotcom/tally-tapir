# How Tally Tapir is built

For people who want to read or change the code.

## The parts

```
                 window (Tkinter)        web page          other programs
                        |                    |                    |
                        |              /ui/api/...          /api/v1/...
                        |                    |                    |
                        |                 web.py  (Flask, login, switches)
                        |                    |
                        +---------+----------+
                                  |
              engine.py (sender)     receiver.py (receiver)     access.py
                                  |
                       tallytapir library (other repository)
                                  |
                          UDP, TCP, serial line
```

| Module | Task |
|---|---|
| `tallytapir_app/engine.py` | `TslSender`: settings, packet, connection, sending on request, on change and on a timer (interval, clock, tally toggle) |
| `tallytapir_app/receiver.py` | `TslReceiver`: lets the library listen and keeps log, table of displays and watched display |
| `tallytapir_app/access.py` | `Access`: switches and logins of web interface and REST API, password hashing, login throttle |
| `tallytapir_app/web.py` | The Flask app and `WebServer`, which opens the port only while a door is switched on |
| `tallytapir_app/gui.py`, `gui_receive.py`, `gui_settings.py`, `gui_colors.py` | The desktop window |
| `tallytapir_app/__main__.py` | Command line, wiring of all parts |
| `tallytapir_app/templates/`, `static/` | The web page: one HTML file, one script per view |
| `packaging/` | Recipe for the packaged Mac app |

Packets, checks, transports and the listening loops are not in this
repository. They come from the library
[tally-tapir-lib](https://github.com/MarcusMa-dotcom/tally-tapir-lib), which
is installed at a fixed tag. `tallytapir_app/protocol.py` and `transport.py`
only pass its names on.

## One state, many front ends

The window, every browser and every API client work on the same `TslSender`
and `TslReceiver`. Neither front end keeps state of its own.

- A change goes in through `update(**changes)`. It validates all values first
  and applies either all or none.
- Every change raises a counter, `revision`.
- `snapshot()` returns settings and status as plain data.
- The window asks every 100 ms, the web page every 400 ms. Both redraw only
  when `revision` changed.

That is why a click in the window shows up in the browser and the other way
round, without the two knowing each other.

## Threads

| Thread | Does |
|---|---|
| Main thread | The Tk window, or waiting for Ctrl+C without window |
| `tsl-timer` | One step every 20 ms: clock text, interval sends, tally toggle, delayed saving |
| `tsl-receiver` | The listening loop of the library, while the receiver listens |
| `tsl-web` and its request threads | The web server |

`TslSender`, `TslReceiver` and `Access` each guard their state with a lock, so
all methods may be called from any thread. Slow work stays outside the locks:
a TCP connect and the password check run without holding one.

## The two doors of the web server

The same routes for sender and receiver are mounted twice:

| Prefix | For | Protected by |
|---|---|---|
| `/ui/api/` | The web page | Switch "web interface", session cookie after the login form |
| `/api/v1/` | Other programs | Switch "REST API", HTTP Basic |

`web.py` checks every request before it reaches a route: the host name, the
content type and origin of changing requests, then the switch and the login of
the door. Login and access settings exist only under `/ui/api/`.

## Tests

`tests/` runs the real parts against each other on the local machine: the
sender sends over UDP and TCP to a receiver, the web routes are called through
Flask's test client, the serial tests use a pseudo terminal. `test_security.py`
holds the probes from the security audit.

```bash
pip install -r requirements.txt pytest
```

```bash
python -m pytest
```
