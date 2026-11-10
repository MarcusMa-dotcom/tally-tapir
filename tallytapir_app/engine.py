"""Sender engine: holds the settings, builds packets and sends them.

The desktop window and the web interface both talk to one ``TslSender``.
Every change raises ``revision``, so each front end can poll cheaply and
redraw only when something changed.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from . import protocol, transport

PACKET_FIELDS = frozenset({"protocol", "umd_id", "screen", "text", "utf16", "brightness", "tally_bits"})
CONNECTION_FIELDS = frozenset(
    {"transport", "host", "port", "serial_port", "baudrate", "bytesize", "parity", "stopbits"}
)

MIN_INTERVAL_MS = 10
MAX_INTERVAL_MS = 3_600_000
TICK_SECONDS = 0.02
SAVE_DELAY_SECONDS = 1.0
LOG_SIZE = 50
LOG_SNAPSHOT_ENTRIES = 20
LOG_SNAPSHOT_BYTES = 64


@dataclass
class Settings:
    """Everything the sender remembers. Saved to the settings file as it is.

    Attributes:
        protocol: ``"v3.1"``, ``"v4.0"``, ``"v5.0"`` or ``"v5.0w"``.
        umd_id: Display address.
        screen: V5.0 screen number.
        text: The UMD text. Replaced by the time of day while ``clock`` is on.
        utf16: Send V5.0 text as UTF-16LE.
        brightness: 0 to 3.
        tally_bits: The six tally boxes, bit 0 is box 1.
        clock: Send the time of day as text.
        clock_tenths: Show tenths of a second in the clock.
        interval_ms: Time between repeated sends.
        interval_enabled: Repeat the packet every ``interval_ms``.
        tally_toggle: Switch the tallies off and on with every interval.
        send_on_change: Send as soon as a value changes, and once on connect.
        hex_braces: Show hex bytes as ``{AA}`` instead of ``AA``.
        transport: ``"udp"``, ``"tcp"`` or ``"serial"``.
        host: Where UDP and TCP packets go.
        port: Port of that host.
        serial_port: Device name of the serial port.
        baudrate: Serial speed.
        bytesize: Serial data bits.
        parity: Serial parity letter.
        stopbits: Serial stop bits.
    """
    protocol: str = "v5.0"
    umd_id: int = 1
    screen: int = 0
    text: str = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    utf16: bool = False
    brightness: int = 3
    tally_bits: int = 0
    clock: bool = False
    clock_tenths: bool = False
    interval_ms: int = 1000
    interval_enabled: bool = False
    tally_toggle: bool = False
    send_on_change: bool = True
    hex_braces: bool = False
    transport: str = "udp"
    host: str = "127.0.0.1"
    port: int = 9800
    serial_port: str = ""
    baudrate: int = 38400
    bytesize: int = 8
    parity: str = "E"
    stopbits: float = 1


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _to_int(name: str, value: Any, low: int, high: int) -> int:
    """Turn a number, or text that holds one, into an int within ``low`` and ``high``.

    Raises:
        ValueError: Not a whole number, or out of range. The message names the field.
    """
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number")
    if isinstance(value, str):
        try:
            value = int(value.strip())
        except ValueError:
            raise ValueError(f"{name} must be a whole number") from None
    elif isinstance(value, float):
        if not value.is_integer():
            raise ValueError(f"{name} must be a whole number")
        value = int(value)
    elif not isinstance(value, int):
        raise ValueError(f"{name} must be a number")
    if not low <= value <= high:
        raise ValueError(f"{name} must be {low} to {high}")
    return value


def _to_bool(name: str, value: Any) -> bool:
    """Accept only real true/false values.

    Raises:
        ValueError: The value is something else, for example 1 or ``"yes"``.
    """
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be true or false")
    return value


def _to_choice(name: str, value: Any, choices) -> Any:
    """Return ``value`` if it is one of ``choices``.

    Raises:
        ValueError: It is not. The message lists the choices.
    """
    if value not in choices:
        raise ValueError(f"{name} must be one of {', '.join(str(c) for c in choices)}")
    return value


def check_host(label: str, value: Any) -> str:
    """A host name or address: not empty, no spaces, no control characters."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must not be empty")
    value = value.strip()
    if len(value) > 255:
        raise ValueError(f"{label} is too long")
    if any(ch.isspace() or ord(ch) < 33 or ord(ch) == 127 for ch in value):
        raise ValueError(f"{label} must not contain spaces or control characters")
    return value


def check_serial_port(value: Any) -> str:
    """A serial device name, or empty. Other paths are refused, so the program
    cannot be used to probe files."""
    if not isinstance(value, str) or len(value) > 255:
        raise ValueError("Serial port must be text")
    value = value.strip()
    if not value:
        return ""
    posix = re.fullmatch(r"/dev/[A-Za-z0-9._:+/\-]+", value) and ".." not in value
    windows = re.fullmatch(r"(\\\\\.\\)?COM[0-9]{1,3}", value, re.IGNORECASE)
    if not (posix or windows):
        raise ValueError("Serial port must be a device such as /dev/ttyUSB0 or COM3")
    return value


def coerce(name: str, value: Any) -> Any:
    """Validate one setting and return it in its stored type."""
    if name == "protocol":
        return _to_choice("Protocol", value, protocol.PROTOCOLS)
    if name == "umd_id":
        return _to_int("UMD ID", value, 0, protocol.V5_MAX_INDEX)
    if name == "screen":
        return _to_int("Screen", value, 0, 0xFFFF)
    if name == "text":
        if not isinstance(value, str):
            raise ValueError("UMD text must be text")
        return value[: protocol.MAX_TEXT_CHARS]
    if name in ("utf16", "clock", "clock_tenths", "interval_enabled", "tally_toggle",
                "send_on_change", "hex_braces"):
        return _to_bool(name, value)
    if name == "brightness":
        return _to_int("Brightness", value, 0, 3)
    if name == "tally_bits":
        return _to_int("Tally bits", value, 0, 0x3F)
    if name == "interval_ms":
        return _to_int("Tx interval", value, MIN_INTERVAL_MS, MAX_INTERVAL_MS)
    if name == "transport":
        return _to_choice("Transport", value, transport.TRANSPORTS)
    if name == "host":
        return check_host("Host", value)
    if name == "port":
        return _to_int("Port", value, 1, 65535)
    if name == "serial_port":
        return check_serial_port(value)
    if name == "baudrate":
        return _to_int("Baud rate", value, 50, 4_000_000)
    if name == "bytesize":
        return _to_choice("Data bits", _to_int("Data bits", value, 5, 8), transport.BYTESIZES)
    if name == "parity":
        return _to_choice("Parity", value, tuple(transport.PARITIES))
    if name == "stopbits":
        if isinstance(value, str):
            try:
                value = float(value)
            except ValueError:
                raise ValueError("Stop bits must be 1, 1.5 or 2") from None
        if isinstance(value, bool) or value not in transport.STOPBITS:
            raise ValueError("Stop bits must be 1, 1.5 or 2")
        return int(value) if float(value).is_integer() else float(value)
    raise ValueError(f"Unknown setting {name!r}")


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def default_config_path() -> Path:
    """The settings file in the place the operating system intends for it."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "tally-tapir" / "settings.json"


def load_settings(path: Path) -> Settings:
    """Read settings from JSON. Missing or invalid values keep their default."""
    settings = Settings()
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return settings
    if not isinstance(data, dict):
        return settings
    for item in fields(Settings):
        if item.name in data:
            try:
                setattr(settings, item.name, coerce(item.name, data[item.name]))
            except ValueError:
                pass
    return settings


def save_settings(path: Path, settings: Settings) -> None:
    """Write the settings as JSON.

    The file is written under another name first and then moved into place, so
    a crash cannot leave half a file behind.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")
    os.replace(temp, path)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class TslSender:
    """The sender behind window, web interface and REST API.

    It holds the settings, builds the packet, keeps the connection and sends:
    on request, on every change, and on a timer. All methods may be called
    from any thread.

    Args:
        settings: Settings to start with. By default they are loaded from
            ``config_path``, or the defaults are used.
        config_path: File to load from and save to. Without it nothing is stored.
        now: Returns the current time for the clock. Tests replace it.
    """
    def __init__(
        self,
        settings: Settings | None = None,
        config_path: Path | str | None = None,
        now: Callable[[], datetime] = datetime.now,
    ):
        """See the class description for the arguments."""
        self._lock = threading.RLock()
        self._config_path = Path(config_path) if config_path else None
        if settings is None:
            settings = load_settings(self._config_path) if self._config_path else Settings()
        self.settings = settings
        self._now = now
        self._transport = None
        self._connecting = False
        self._instance = uuid.uuid4().hex[:8]  # lets a front end notice a restart
        self._revision = 0
        self._sent_count = 0
        self._last_sent: float | None = None
        self._error = ""
        self._log: deque[tuple[float, bytes]] = deque(maxlen=LOG_SIZE)
        self._toggle_off = False
        self._next_due: float | None = None
        self._dirty = False
        self._dirty_since = 0.0
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # -- state -----------------------------------------------------------

    @property
    def revision(self) -> int:
        """A counter that rises with every change.

        Front ends compare it with the value they drew last to know when to redraw.
        """
        return self._revision

    @property
    def connected(self) -> bool:
        """True while a connection is open."""
        return self._transport is not None

    def _touch(self) -> None:
        """Mark that something changed, so front ends redraw."""
        self._revision += 1

    def _effective_tally_bits(self) -> int:
        """The tally bits to send right now: 0 in the dark phase of the tally toggle."""
        s = self.settings
        if s.tally_toggle and s.interval_enabled and self._toggle_off:
            return 0
        return s.tally_bits

    def build(self) -> bytes:
        """Build the packet for the current settings."""
        s = self.settings
        with self._lock:
            return protocol.build_packet(
                s.protocol,
                umd_id=s.umd_id,
                tally_bits=self._effective_tally_bits(),
                brightness=s.brightness,
                text=s.text,
                utf16=s.utf16,
                screen=s.screen,
            )

    def snapshot(self) -> dict:
        """Return settings and status as plain data for the front ends."""
        with self._lock:
            s = self.settings
            try:
                packet = self.build()
                packet_error = ""
            except protocol.ProtocolError as exc:
                packet = b""
                packet_error = str(exc)
            tally = self._effective_tally_bits()
            log = [
                {
                    "time": stamp,
                    "length": len(data),
                    "hex": protocol.format_hex(data[:LOG_SNAPSHOT_BYTES], s.hex_braces)
                    + (" ..." if len(data) > LOG_SNAPSHOT_BYTES else ""),
                }
                for stamp, data in list(self._log)[-LOG_SNAPSHOT_ENTRIES:]
            ]
            return {
                "instance": self._instance,
                "revision": self._revision,
                "settings": asdict(s),
                "status": {
                    "connected": self.connected,
                    "connecting": self._connecting,
                    "connection": self._transport.description if self._transport else "",
                    "error": self._error,
                    "packet_error": packet_error,
                    "packet_hex": protocol.format_hex(packet, s.hex_braces),
                    "packet_length": len(packet),
                    "sent_count": self._sent_count,
                    "last_sent": self._last_sent,
                    "tally_bits_sent": tally,
                    "lamps": protocol.lamps(tally),
                    "v3_tallies": protocol.v3_tallies(tally),
                    "log": log,
                },
            }

    # -- changes ---------------------------------------------------------

    def update(self, **changes: Any) -> dict:
        """Validate and apply setting changes. Raises ValueError on bad input."""
        clean = {name: coerce(name, value) for name, value in changes.items()}
        with self._lock:
            s = self.settings
            changed = {name for name, value in clean.items() if getattr(s, name) != value}
            if not changed:
                return self.snapshot()
            for name in changed:
                setattr(s, name, clean[name])

            if changed & CONNECTION_FIELDS and self.connected:
                self._close_transport()
            if changed & {"interval_enabled", "interval_ms"}:
                self._next_due = None
            if not (s.tally_toggle and s.interval_enabled):
                self._toggle_off = False
            if s.clock and changed & {"clock", "clock_tenths"}:
                s.text = self._clock_text()
                changed.add("text")

            self._touch()
            self._mark_dirty()
            if changed & PACKET_FIELDS and s.send_on_change and self.connected:
                self._send_locked()
            return self.snapshot()

    def _mark_dirty(self) -> None:
        """Note that the settings need saving. The timer thread saves a moment later."""
        if not self._dirty:
            self._dirty = True
            self._dirty_since = time.monotonic()

    def _clock_text(self) -> str:
        """The time of day as UMD text, with tenths of a second if wanted."""
        stamp = self._now()
        text = stamp.strftime("%H:%M:%S")
        if self.settings.clock_tenths:
            text += f".{stamp.microsecond // 100_000}"
        return text

    # -- connection ------------------------------------------------------

    def _make_transport(self):
        """A transport object for the current connection settings, not opened yet."""
        s = self.settings
        if s.transport == "udp":
            return transport.UdpTransport(s.host, s.port)
        if s.transport == "tcp":
            return transport.TcpTransport(s.host, s.port)
        return transport.SerialTransport(s.serial_port, s.baudrate, s.bytesize, s.parity, s.stopbits)

    def connect(self) -> bool:
        """Open the selected transport. Returns False and sets the error on failure."""
        with self._lock:
            if self._connecting:
                return False
            self._close_transport()
            candidate = self._make_transport()
            self._connecting = True
            self._error = ""
            self._touch()
        error = ""
        try:
            candidate.open()  # outside the lock: a TCP connect may take seconds
        except transport.TransportError as exc:
            error = str(exc)
        except Exception as exc:  # never leave the sender stuck in "connecting"
            error = f"Cannot connect: {type(exc).__name__}"
        with self._lock:
            self._connecting = False
            if error:
                self._error = error
                self._touch()
                return False
            self._transport = candidate
            self._touch()
            if self.settings.send_on_change:
                self._send_locked()
            return True

    def disconnect(self) -> None:
        """Close the connection."""
        with self._lock:
            if self.connected:
                self._close_transport()
                self._error = ""
                self._touch()

    def _close_transport(self) -> None:
        """Close and forget the transport. Called with the lock held."""
        if self._transport is not None:
            try:
                self._transport.close()
            finally:
                self._transport = None

    # -- sending ---------------------------------------------------------

    def send(self) -> bool:
        """Send the current packet once. Returns False and sets the error on failure."""
        with self._lock:
            if not self.connected:
                self._error = "Not connected"
                self._touch()
                return False
            return self._send_locked()

    def _send_locked(self) -> bool:
        """Build the packet and send it. Called with the lock held.

        After a failed send, TCP and serial connections are closed. UDP stays
        open, because the next datagram may get through.

        Returns:
            True if the packet went out.
        """
        try:
            packet = self.build()
        except protocol.ProtocolError as exc:
            self._error = str(exc)
            self._touch()
            return False
        try:
            self._transport.send(packet)
        except transport.TransportError as exc:
            self._error = str(exc)
            if self.settings.transport != "udp":
                self._close_transport()
            self._touch()
            return False
        self._error = ""
        self._sent_count += 1
        self._last_sent = time.time()
        self._log.append((self._last_sent, packet))
        self._touch()
        return True

    # -- background timer ------------------------------------------------

    def start(self) -> None:
        """Start the timer thread for the clock and the Tx interval."""
        if self._thread is None:
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="tsl-timer", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        """Stop the timer thread, save the settings and close the connection."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        with self._lock:
            self._save_if_dirty(force=True)
            self._close_transport()

    def _run(self) -> None:
        """Timer thread: one step every 20 ms."""
        while not self._stop.wait(TICK_SECONDS):
            with self._lock:
                self.tick(time.monotonic())

    def tick(self, now: float) -> None:
        """One timer step. Public so tests can drive time themselves."""
        with self._lock:
            s = self.settings
            if s.clock:
                text = self._clock_text()
                if text != s.text:
                    s.text = text
                    self._touch()
                    if s.send_on_change and self.connected:
                        self._send_locked()
            if s.interval_enabled:
                interval = s.interval_ms / 1000.0
                if self._next_due is None:
                    self._next_due = now + interval
                elif now >= self._next_due:
                    if s.tally_toggle:
                        self._toggle_off = not self._toggle_off
                        self._touch()
                    if self.connected:
                        self._send_locked()
                    self._next_due += interval
                    if self._next_due <= now:
                        self._next_due = now + interval
            else:
                self._next_due = None
            self._save_if_dirty(now=now)

    def _save_if_dirty(self, now: float | None = None, force: bool = False) -> None:
        """Save changed settings.

        Args:
            now: The current time. Saving waits one second after a change, so a
                burst of changes writes the file once.
            force: Save now, whatever the time. Used at program end.
        """
        if not self._dirty or self._config_path is None:
            return
        if not force and (now is None or now - self._dirty_since < SAVE_DELAY_SECONDS):
            return
        self._dirty = False
        try:
            save_settings(self._config_path, self.settings)
        except OSError:
            pass
