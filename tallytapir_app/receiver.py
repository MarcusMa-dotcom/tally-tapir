"""Receiver engine: listens for TSL packets, decodes and checks them.

It keeps three views of what arrived:

* a log of the last packets with hex bytes, decoded content and problems,
* a table of every display seen (one row per screen and UMD ID),
* the "watched" display: the latest message that matches the filter
  ``watch_screen`` / ``watch_id``. An empty filter field matches everything,
  and the V5.0 broadcast address 65535 always matches.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from tallytapir.receiver import Receiver

from . import protocol, transport
from .engine import _to_int, check_host, coerce as coerce_common

LISTEN_FIELDS = frozenset(
    {"transport", "bind", "port", "serial_port", "baudrate", "bytesize", "parity", "stopbits", "protocol"}
)
WATCH_FIELDS = frozenset({"watch_screen", "watch_id"})
COMMON_FIELDS = frozenset(
    {"transport", "port", "serial_port", "baudrate", "bytesize", "parity", "stopbits", "hex_braces"}
)

LOG_SIZE = 200
LOG_SNAPSHOT_ENTRIES = 40
LOG_SNAPSHOT_BYTES = 64
MAX_DISPLAYS = 500
SNAPSHOT_DISPLAYS = 200


@dataclass
class ReceiverSettings:
    """Everything the receiver remembers. Saved to its settings file as it is.

    Attributes:
        transport: ``"udp"``, ``"tcp"`` or ``"serial"``.
        bind: Address to listen on.
        port: Port to listen on.
        serial_port: Device name of the serial port.
        baudrate: Serial speed.
        bytesize: Serial data bits.
        parity: Serial parity letter.
        stopbits: Serial stop bits.
        protocol: ``"auto"`` or one protocol name.
        watch_screen: Screen the watched display follows, None for any.
        watch_id: UMD ID the watched display follows, None for any.
        hex_braces: Show hex bytes as ``{AA}`` instead of ``AA``.
    """
    transport: str = "udp"
    bind: str = "0.0.0.0"
    port: int = 9800
    serial_port: str = ""
    baudrate: int = 38400
    bytesize: int = 8
    parity: str = "E"
    stopbits: float = 1
    protocol: str = "auto"
    watch_screen: int | None = None
    watch_id: int | None = None
    hex_braces: bool = False


def coerce(name: str, value: Any) -> Any:
    """Validate one receiver setting and return it in its stored type."""
    if name in COMMON_FIELDS:
        return coerce_common(name, value)
    if name == "bind":
        return check_host("Listen address", value)
    if name == "protocol":
        if value not in protocol.RX_PROTOCOLS:
            raise ValueError(f"Protocol must be one of {', '.join(protocol.RX_PROTOCOLS)}")
        return value
    if name in WATCH_FIELDS:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None  # an empty filter matches everything
        label = "Screen filter" if name == "watch_screen" else "UMD ID filter"
        return _to_int(label, value, 0, 0xFFFF)
    raise ValueError(f"Unknown setting {name!r}")


def load_settings(path: Path) -> ReceiverSettings:
    """Read receiver settings from JSON. Missing or invalid values keep their default."""
    settings = ReceiverSettings()
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return settings
    if not isinstance(data, dict):
        return settings
    for item in fields(ReceiverSettings):
        if item.name in data:
            try:
                setattr(settings, item.name, coerce(item.name, data[item.name]))
            except ValueError:
                pass
    return settings


def save_settings(path: Path, settings: ReceiverSettings) -> None:
    """Write the receiver settings as JSON, through a temporary file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")
    os.replace(temp, path)


def matches(message: protocol.Message, watch_screen: int | None, watch_id: int | None) -> bool:
    """True if a message is meant for the watched screen and UMD ID."""
    is_v5 = message.screen is not None
    if watch_id is not None and message.index != watch_id:
        if not (is_v5 and message.index == protocol.BROADCAST):
            return False
    if watch_screen is not None and is_v5:
        if message.screen not in (watch_screen, protocol.BROADCAST):
            return False
    return True


class TslReceiver:
    """The receiver behind window, web interface and REST API.

    It lets the library listen and keeps the books: the log, the table of
    displays and the watched display. All methods may be called from any thread.

    Args:
        settings: Settings to start with. By default they are loaded from
            ``config_path``, or the defaults are used.
        config_path: File to load from and save to. Without it nothing is stored.
    """
    def __init__(self, settings: ReceiverSettings | None = None, config_path: Path | str | None = None):
        """See the class description for the arguments."""
        self._lock = threading.RLock()
        self._config_path = Path(config_path) if config_path else None
        if settings is None:
            settings = load_settings(self._config_path) if self._config_path else ReceiverSettings()
        self.settings = settings
        self._instance = uuid.uuid4().hex[:8]
        self._revision = 0
        self._receiver: Receiver | None = None
        self._description = ""
        self._error = ""
        self._clients = 0
        self._packet_count = 0
        self._problem_count = 0
        self._log: deque[dict] = deque(maxlen=LOG_SIZE)
        self._displays: dict[tuple[int, int], dict] = {}
        self._watched: dict | None = None

    # -- state -----------------------------------------------------------

    @property
    def revision(self) -> int:
        """A counter that rises with every change, so front ends know when to redraw."""
        return self._revision

    @property
    def listening(self) -> bool:
        """True while the receiver listens."""
        return self._receiver is not None

    def _touch(self) -> None:
        """Mark that something changed, so front ends redraw."""
        self._revision += 1

    def snapshot(self) -> dict:
        """Settings and status as plain data for the front ends.

        The log holds the last 40 packets with at most 64 bytes of hex each, the
        table at most 200 displays. Whether a log entry counts as watched is judged
        against the filter as it is now.
        """
        with self._lock:
            s = self.settings
            log = []
            for entry in list(self._log)[-LOG_SNAPSHOT_ENTRIES:]:
                raw = entry["raw"]
                log.append({
                    "time": entry["time"],
                    "source": entry["source"],
                    "protocol": entry["protocol"],
                    "length": len(raw),
                    "hex": protocol.format_hex(raw[:LOG_SNAPSHOT_BYTES], s.hex_braces)
                    + (" ..." if len(raw) > LOG_SNAPSHOT_BYTES else ""),
                    "summary": entry["summary"],
                    "problems": entry["problems"],
                    # Judged against the filter as it is now, not as it was on arrival.
                    "matched": any(matches(m, s.watch_screen, s.watch_id) for m in entry["messages"]),
                })
            displays = [self._public(self._displays[key]) for key in sorted(self._displays)]
            return {
                "instance": self._instance,
                "revision": self._revision,
                "settings": asdict(s),
                "status": {
                    "listening": self.listening,
                    "description": self._description,
                    "error": self._error,
                    "clients": self._clients,
                    "packet_count": self._packet_count,
                    "problem_count": self._problem_count,
                    "watched": self._watched,
                    "display_count": len(displays),
                    "displays": displays[:SNAPSHOT_DISPLAYS],
                    "log": log,
                },
            }

    # -- changes ---------------------------------------------------------

    def update(self, **changes: Any) -> dict:
        """Validate and apply setting changes. Raises ValueError on bad input."""
        clean = {name: coerce(name, value) for name, value in changes.items()}
        stop = False
        with self._lock:
            s = self.settings
            changed = {name for name, value in clean.items() if getattr(s, name) != value}
            if changed:
                for name in changed:
                    setattr(s, name, clean[name])
                stop = bool(changed & LISTEN_FIELDS) and self.listening
                if changed & WATCH_FIELDS:
                    self._rewatch()
                self._touch()
                self._save()
        if stop:
            self.stop_listening()
        return self.snapshot()

    def _save(self) -> None:
        """Write the settings file, if there is one."""
        if self._config_path is not None:
            try:
                save_settings(self._config_path, self.settings)
            except OSError:
                pass

    def _rewatch(self) -> None:
        """After a filter change: watch the most recently seen display that matches."""
        s = self.settings
        candidates = [d for d in self._displays.values()
                      if matches(d["message"], s.watch_screen, s.watch_id)]
        latest = max(candidates, key=lambda d: d["time"], default=None)
        self._watched = self._public(latest) if latest else None

    @staticmethod
    def _public(entry: dict) -> dict:
        """A display entry without the internal message object."""
        return {key: value for key, value in entry.items() if key != "message"}

    def clear(self) -> None:
        """Forget everything received so far."""
        with self._lock:
            self._log.clear()
            self._displays.clear()
            self._watched = None
            self._packet_count = 0
            self._problem_count = 0
            self._touch()

    # -- listening -------------------------------------------------------

    def listen(self) -> bool:
        """Start listening. Returns False and sets the error on failure."""
        self.stop_listening()
        with self._lock:
            s = self.settings
            receiver = Receiver(
                s.transport, bind=s.bind, port=s.port, serial_port=s.serial_port,
                baudrate=s.baudrate, bytesize=s.bytesize, parity=s.parity, stopbits=s.stopbits,
                protocol=s.protocol, on_packet=self._handle, on_clients=self._set_clients,
            )
            receiver.on_error = lambda message: self._fail(receiver, message)
            try:
                receiver.start()
            except transport.TransportError as exc:
                self._error = str(exc)
                self._touch()
                return False
            self._receiver = receiver
            self._error = ""
            self._description = receiver.description
            self._clients = 0
            self._touch()
            return True

    def stop_listening(self) -> None:
        """Stop listening and close the port."""
        with self._lock:
            receiver = self._receiver
            if receiver is None:
                return
        receiver.stop()  # outside the lock: its thread may be waiting for it in a callback
        with self._lock:
            if self._receiver is receiver:
                self._receiver = None
                self._clients = 0
                self._touch()

    def close(self) -> None:
        """Stop listening at program end."""
        self.stop_listening()

    def _fail(self, receiver: Receiver, message: str) -> None:
        """The listening thread reports that it cannot go on."""
        with self._lock:
            self._error = message
            if self._receiver is receiver and receiver.failed:
                self._receiver = None
                self._clients = 0
            self._touch()

    def _set_clients(self, count: int) -> None:
        """Remember the number of TCP connections. Called from the listening thread."""
        with self._lock:
            self._clients = count
            self._touch()

    # -- bookkeeping -----------------------------------------------------

    def _handle(self, decoded: protocol.Decoded) -> None:
        """Keep the books for one received packet. Called from the listening thread.

        Counters, the table of displays, the watched display and the log are
        updated. The table keeps at most 500 displays and drops the one seen
        longest ago.
        """
        now = time.time()
        with self._lock:
            s = self.settings
            self._packet_count += 1
            if decoded.problems:
                self._problem_count += 1
            for message in decoded.messages:
                key = (-1 if message.screen is None else message.screen, message.index)
                previous = self._displays.get(key)
                if previous is None and len(self._displays) >= MAX_DISPLAYS:
                    oldest = min(self._displays, key=lambda k: self._displays[k]["time"])
                    del self._displays[oldest]
                entry = {
                    "message": message,
                    **asdict(message),
                    "text": message.text.rstrip() if message.screen is None else message.text,
                    "time": now,
                    "count": (previous["count"] if previous else 0) + 1,
                }
                self._displays[key] = entry
                if matches(message, s.watch_screen, s.watch_id):
                    self._watched = self._public(entry)
            self._log.append({
                "time": now,
                "source": decoded.source,
                "protocol": decoded.protocol,
                "raw": decoded.raw,
                "summary": " | ".join(protocol.describe(m) for m in decoded.messages),
                "problems": list(decoded.problems),
                "messages": list(decoded.messages),
            })
            self._touch()
