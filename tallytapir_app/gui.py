"""Tkinter desktop window for the TSL sender."""

from __future__ import annotations

import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from . import __version__, protocol, transport
from .access import Access
from .engine import TslSender
from .gui_colors import error_color
from .gui_receive import ReceiveTab
from .gui_settings import AboutTab, SettingsTab
from .receiver import TslReceiver

POLL_MS = 100
LAMP_COLORS = ("#c4c8cc", "#d0202a", "#1f9d45", "#e59500")
BOOL_FIELDS = ("utf16", "clock", "clock_tenths", "interval_enabled", "tally_toggle",
               "send_on_change", "hex_braces")
ENTRY_FIELDS = ("host", "port", "umd_id", "screen", "interval_ms")


class MainWindow:
    """The desktop window. The Send tab is built here, the other tabs in their modules.

    The window never talks to the network itself. Every input goes to the
    sender through ``engine.update``, and a poll every 100 ms redraws the
    widgets when the sender changed. That keeps window and web interface in
    step, whichever of them made the change.

    Args:
        root: The Tk root window.
        engine: The sender.
        receiver: The receiver, for the Receive tab.
        access: The access settings. Without them there is no Settings tab.
        server: The web server, for its address in status bar and Settings.
    """
    def __init__(self, root: tk.Tk, engine: TslSender, receiver: TslReceiver,
                 access: Access | None = None, server=None):
        """Build all tabs and start the poll. See the class description for the arguments."""
        self.root = root
        self.engine = engine
        self.server = server
        self._loading = False
        self._seen_revision = -1
        self._input_error = ""

        root.title(f"Tally Tapir {__version__}")
        try:
            self.logo = tk.PhotoImage(file=str(Path(__file__).with_name("static") / "logo.png"))
            root.iconphoto(True, self.logo)
        except tk.TclError:
            self.logo = None  # the window works without its picture
        root.resizable(False, False)

        self.vars: dict[str, tk.Variable] = {
            "transport": tk.StringVar(),
            "protocol": tk.StringVar(),
            "text": tk.StringVar(),
            "serial_port": tk.StringVar(),
            "baudrate": tk.StringVar(),
            "bytesize": tk.StringVar(),
            "parity": tk.StringVar(),
            "stopbits": tk.StringVar(),
            "brightness": tk.IntVar(),
        }
        for name in ENTRY_FIELDS:
            self.vars[name] = tk.StringVar()
        for name in BOOL_FIELDS:
            self.vars[name] = tk.BooleanVar()
        self.tally_vars = [tk.BooleanVar() for _ in range(6)]
        self.entries: dict[str, tk.Widget] = {}

        notebook = ttk.Notebook(root)
        notebook.grid(sticky="nsew")
        outer = ttk.Frame(notebook, padding=10)
        notebook.add(outer, text="Send")
        self.receive_tab = ReceiveTab(notebook, receiver)
        notebook.add(self.receive_tab, text="Receive")
        self.settings_tab = None
        if access is not None:
            self.settings_tab = SettingsTab(notebook, access, server)
            notebook.add(self.settings_tab, text="Settings")
        notebook.add(AboutTab(notebook, self.logo), text="About")
        left = ttk.Frame(outer)
        right = ttk.Frame(outer)
        left.grid(row=0, column=0, sticky="n", padx=(0, 10))
        right.grid(row=0, column=1, sticky="new")
        outer.columnconfigure(1, weight=1)  # the right column takes the spare width

        self._build_connection(left)
        self._build_protocol(left)
        self._build_transmit(left)
        self._build_text(right)
        self._build_tally(right)
        self._build_packet(right)
        self._build_status(outer)

        self.vars["text"].trace_add("write", lambda *_: self._push("text", self.vars["text"].get()))
        root.protocol("WM_DELETE_WINDOW", root.destroy)
        self._poll()

    # -- layout ----------------------------------------------------------

    def _group(self, parent: tk.Widget, title: str) -> ttk.LabelFrame:
        """A titled frame, stacked below the previous one."""
        frame = ttk.LabelFrame(parent, text=title, padding=8)
        frame.pack(fill="x", pady=(0, 8))
        return frame

    def _entry(self, parent: tk.Widget, name: str, width: int) -> ttk.Entry:
        """An entry field that hands its value over on Return and when the focus leaves."""
        entry = ttk.Entry(parent, textvariable=self.vars[name], width=width)
        entry.bind("<FocusOut>", lambda _event: self._push_entry(name))
        entry.bind("<Return>", lambda _event: self._push_entry(name))
        self.entries[name] = entry
        return entry

    def _check(self, parent: tk.Widget, name: str, label: str) -> ttk.Checkbutton:
        """A check box that hands its state over when clicked."""
        return ttk.Checkbutton(
            parent, text=label, variable=self.vars[name],
            command=lambda: self._push(name, bool(self.vars[name].get())),
        )

    def _combo(self, parent: tk.Widget, name: str, values, width: int) -> ttk.Combobox:
        """A drop-down list that hands its choice over when one is picked."""
        combo = ttk.Combobox(parent, textvariable=self.vars[name], values=values, width=width,
                             state="readonly")
        combo.bind("<<ComboboxSelected>>", lambda _event: self._push_combo(name))
        return combo

    def _build_connection(self, parent: tk.Widget) -> None:
        """Connection group: transport, address or serial settings, Connect button."""
        frame = self._group(parent, "Connection")
        row = ttk.Frame(frame)
        row.pack(fill="x")
        for key, label in transport.TRANSPORT_LABELS.items():
            ttk.Radiobutton(
                row, text=label, value=key, variable=self.vars["transport"],
                command=lambda: self._push("transport", self.vars["transport"].get()),
            ).pack(side="left", padx=(0, 10))

        self.ip_frame = ttk.Frame(frame)
        ttk.Label(self.ip_frame, text="Host").grid(row=0, column=0, sticky="w")
        ttk.Label(self.ip_frame, text="Port").grid(row=0, column=1, sticky="w", padx=(8, 0))
        self._entry(self.ip_frame, "host", 18).grid(row=1, column=0)
        self._entry(self.ip_frame, "port", 7).grid(row=1, column=1, padx=(8, 0))

        self.serial_frame = ttk.Frame(frame)
        ttk.Label(self.serial_frame, text="Serial port").grid(row=0, column=0, columnspan=3, sticky="w")
        self.port_combo = ttk.Combobox(self.serial_frame, textvariable=self.vars["serial_port"], width=22)
        self.port_combo.grid(row=1, column=0, columnspan=3, sticky="w")
        self.port_combo.bind("<<ComboboxSelected>>", lambda _event: self._push_combo("serial_port"))
        self.port_combo.bind("<FocusOut>", lambda _event: self._push_combo("serial_port"))
        self.port_combo.bind("<Return>", lambda _event: self._push_combo("serial_port"))
        ttk.Button(self.serial_frame, text="Refresh", command=self._refresh_ports).grid(
            row=1, column=3, padx=(8, 0))
        for column, (label, name, values, width) in enumerate((
            ("Baud rate", "baudrate", transport.BAUDRATES, 7),
            ("Data bits", "bytesize", transport.BYTESIZES, 3),
            ("Parity", "parity", tuple(transport.PARITIES), 3),
            ("Stop bits", "stopbits", ("1", "1.5", "2"), 3),
        )):
            ttk.Label(self.serial_frame, text=label).grid(row=2, column=column, sticky="w", pady=(6, 0))
            self._combo(self.serial_frame, name, values, width).grid(
                row=3, column=column, sticky="w", padx=(0, 6))

        self.connect_button = ttk.Button(frame, text="Connect", command=self._toggle_connection)
        self.connect_button.pack(anchor="w", pady=(8, 0), side="bottom")

    def _build_protocol(self, parent: tk.Widget) -> None:
        """Protocol group: version, UMD ID and screen."""
        frame = self._group(parent, "Protocol")
        row = ttk.Frame(frame)
        row.pack(fill="x")
        for key, label in protocol.PROTOCOL_LABELS.items():
            ttk.Radiobutton(
                row, text=label, value=key, variable=self.vars["protocol"],
                command=lambda: self._push("protocol", self.vars["protocol"].get()),
            ).pack(side="left", padx=(0, 10))
        fields = ttk.Frame(frame)
        fields.pack(fill="x", pady=(6, 0))
        ttk.Label(fields, text="UMD ID").grid(row=0, column=0, sticky="w")
        ttk.Label(fields, text="Screen").grid(row=0, column=1, sticky="w", padx=(8, 0))
        self._entry(fields, "umd_id", 7).grid(row=1, column=0)
        self._entry(fields, "screen", 7).grid(row=1, column=1, padx=(8, 0))

    def _build_transmit(self, parent: tk.Widget) -> None:
        """Transmit group: Send button, interval, tally toggle and send on change."""
        frame = self._group(parent, "Transmit")
        self.send_button = ttk.Button(frame, text="Send", command=self._send)
        self.send_button.grid(row=0, column=0, sticky="w")
        ttk.Label(frame, text="Tx interval (ms)").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self._entry(frame, "interval_ms", 8).grid(row=2, column=0, sticky="w")
        self._check(frame, "interval_enabled", "Enable interval").grid(row=2, column=1, sticky="w", padx=(8, 0))
        self.toggle_check = self._check(frame, "tally_toggle", "Tally toggle")
        self.toggle_check.grid(row=3, column=0, sticky="w", pady=(4, 0))
        self._check(frame, "send_on_change", "Send on change").grid(
            row=3, column=1, sticky="w", padx=(8, 0), pady=(4, 0))
        self.sent_label = ttk.Label(frame, text="Sent: 0 packets")
        self.sent_label.grid(row=4, column=0, columnspan=2, sticky="w", pady=(6, 0))

    def _build_text(self, parent: tk.Widget) -> None:
        """UMD text group: text, UTF-16LE, clock and brightness."""
        frame = self._group(parent, "UMD text")
        self.text_entry = ttk.Entry(frame, textvariable=self.vars["text"], width=44, font="TkFixedFont")
        self.text_entry.grid(row=0, column=0, columnspan=3, sticky="we")
        self.utf16_check = self._check(frame, "utf16", "UTF-16LE")
        self.utf16_check.grid(row=1, column=0, sticky="w", pady=(6, 0), padx=(0, 18))
        self._check(frame, "clock", "Clock").grid(row=1, column=1, sticky="w", pady=(6, 0), padx=(0, 18))
        self.tenths_check = self._check(frame, "clock_tenths", "1/10 s")
        self.tenths_check.grid(row=1, column=2, sticky="w", pady=(6, 0))
        frame.columnconfigure(2, weight=1)
        self.brightness_label = ttk.Label(frame, text="Brightness 3")
        self.brightness_label.grid(row=2, column=0, sticky="w", pady=(6, 0), padx=(0, 18))
        self.brightness_scale = ttk.Scale(frame, from_=0, to=3, orient="horizontal", length=140,
                                          command=self._brightness_moved)
        self.brightness_scale.grid(row=2, column=1, columnspan=2, sticky="w", pady=(6, 0))
        # Let the knob snap to the chosen step when the mouse button goes up.
        self.brightness_scale.bind(
            "<ButtonRelease-1>",
            lambda _event: self.brightness_scale.set(self.vars["brightness"].get()), add="+")

    def _build_tally(self, parent: tk.Widget) -> None:
        """Tally group: the six boxes, a preview of the lamps and a hint for the version."""
        frame = self._group(parent, "Tally")
        self.tally_checks: list[ttk.Checkbutton] = []
        for column, (title, first) in enumerate((("Right lamp", 0), ("Text", 2), ("Left lamp", 4))):
            ttk.Label(frame, text=title).grid(row=0, column=column * 2, columnspan=2, sticky="w")
            for offset in (0, 1):
                check = ttk.Checkbutton(
                    frame, text=str(first + offset + 1), variable=self.tally_vars[first + offset],
                    command=self._push_tally,
                )
                check.grid(row=1, column=column * 2 + offset, sticky="w", padx=(0, 10))
                self.tally_checks.append(check)
        lamps = ttk.Frame(frame)
        lamps.grid(row=2, column=0, columnspan=6, sticky="w", pady=(8, 0))
        self.lamp_widgets: dict[str, tuple[tk.Canvas, ttk.Label]] = {}
        for key, title in (("lh", "Left"), ("txt", "Text"), ("rh", "Right")):
            canvas = tk.Canvas(lamps, width=16, height=16, highlightthickness=1,
                               highlightbackground="#808080", background=LAMP_COLORS[0])
            canvas.pack(side="left")
            label = ttk.Label(lamps, text=f"{title}: off", width=13)
            label.pack(side="left", padx=(4, 8))
            self.lamp_widgets[key] = (canvas, label)
        self.tally_hint = ttk.Label(frame, text="", wraplength=360, justify="left")
        self.tally_hint.grid(row=3, column=0, columnspan=6, sticky="w", pady=(6, 0))

    def _build_packet(self, parent: tk.Widget) -> None:
        """Packet group: the current packet as hex bytes and its length."""
        frame = self._group(parent, "Packet")
        self.hex_text = tk.Text(frame, width=48, height=6, wrap="char", font="TkFixedFont",
                                state="disabled", relief="solid", borderwidth=1)
        self.hex_text.grid(row=0, column=0, columnspan=2, sticky="we")
        frame.columnconfigure(0, weight=1)
        self._hex_foreground = self.hex_text.cget("foreground")
        self.length_label = ttk.Label(frame, text="0 bytes")
        self.length_label.grid(row=1, column=0, sticky="w", pady=(4, 0))
        self._check(frame, "hex_braces", "{} format").grid(row=1, column=1, sticky="e", pady=(4, 0))

    def _build_status(self, parent: tk.Widget) -> None:
        """Status bar: connection state, web address and a line for errors."""
        bar = ttk.Frame(parent)
        bar.grid(row=1, column=0, columnspan=2, sticky="we")
        self.state_label = ttk.Label(bar, text="Disconnected")
        self.state_label.pack(side="left")
        self.web_label = ttk.Label(bar, text="")
        self.web_label.pack(side="right")
        self.error_label = ttk.Label(parent, text="", wraplength=640, justify="left")
        self.error_label.grid(row=2, column=0, columnspan=2, sticky="w")

    # -- user input -> engine -------------------------------------------

    def _push(self, name: str, value) -> None:
        """Hand one changed value to the sender.

        A refused value is kept as error text and shows below the status bar.
        """
        if self._loading:
            return
        try:
            self.engine.update(**{name: value})
            self._input_error = ""
        except ValueError as exc:
            self._input_error = str(exc)
        self._seen_revision = -1  # redraw on the next poll, also after an error

    def _push_entry(self, name: str) -> None:
        """Hand over what stands in the entry field ``name``."""
        self._push(name, self.vars[name].get())

    def _push_combo(self, name: str) -> None:
        """Hand over what was picked in the drop-down list ``name``."""
        self._push(name, self.vars[name].get())

    def _brightness_moved(self, value: str) -> None:
        """The slider moved: hand over the nearest of the four steps."""
        # The slider moves smoothly, the setting has the four steps 0 to 3.
        step = round(float(value))
        if step != self.vars["brightness"].get():
            self.vars["brightness"].set(step)
            self._push("brightness", step)

    def _push_tally(self) -> None:
        """Combine the six boxes into ``tally_bits`` and hand them over."""
        bits = sum(1 << bit for bit, var in enumerate(self.tally_vars) if var.get())
        self._push("tally_bits", bits)

    def _toggle_connection(self) -> None:
        """Connect or disconnect, depending on the current state."""
        if self.engine.connected:
            self.engine.disconnect()
        else:
            # A TCP connect can take seconds, keep the window responsive.
            threading.Thread(target=self.engine.connect, daemon=True).start()

    def _send(self) -> None:
        """Send the current packet once."""
        self.engine.send()

    def _refresh_ports(self) -> None:
        """Fill the list of serial ports from what the machine has now."""
        ports = [port["device"] for port in transport.list_serial_ports()]
        self.port_combo.configure(values=ports)

    # -- engine -> widgets ----------------------------------------------

    def _poll(self) -> None:
        """Runs every 100 ms: redraw what changed, in all tabs."""
        if self.engine.revision != self._seen_revision:
            self._load(self.engine.snapshot())
        self.receive_tab.poll()
        if self.settings_tab is not None:
            self.settings_tab.poll()
        self.web_label.configure(text=self._web_state())
        self.root.after(POLL_MS, self._poll)

    def _web_state(self) -> str:
        """Text for the status bar about the web server."""
        server = self.server
        if server is None:
            return ""
        if server.error:
            return "Web server: error, see Settings"
        if not server.running:
            return "Web interface and REST API: off"
        return f"Web: {server.url}"

    def _set(self, name: str, value) -> None:
        """Set a Tk variable only if the value differs, to avoid needless events."""
        var = self.vars[name]
        if var.get() != value:
            var.set(value)

    def _load(self, data: dict) -> None:
        """Draw the whole Send tab from a snapshot of the sender."""
        self._seen_revision = data["revision"]
        s = data["settings"]
        status = data["status"]
        is_v5 = s["protocol"] in ("v5.0", "v5.0w")
        is_v3 = s["protocol"] == "v3.1"
        focused = self.root.focus_get()

        self._loading = True
        try:
            for name in ("transport", "protocol", "serial_port", "parity", "brightness"):
                self._set(name, s[name])
            for name in ("baudrate", "bytesize"):
                self._set(name, str(s[name]))
            self._set("stopbits", f"{s['stopbits']:g}")
            for name in BOOL_FIELDS:
                self._set(name, s[name])
            for name in ENTRY_FIELDS:
                if self.entries[name] is not focused:
                    self._set(name, str(s[name]))
            if self.vars["text"].get() != s["text"]:
                self.vars["text"].set(s["text"])
            for bit, var in enumerate(self.tally_vars):
                value = bool(s["tally_bits"] & (1 << bit))
                if var.get() != value:
                    var.set(value)
        finally:
            self._loading = False

        if s["transport"] == "serial":
            self.ip_frame.pack_forget()
            self.serial_frame.pack(fill="x", pady=(6, 0))
            if not self.port_combo.cget("values"):
                self._refresh_ports()
        else:
            self.serial_frame.pack_forget()
            self.ip_frame.pack(fill="x", pady=(6, 0))

        self.brightness_label.configure(text=f"Brightness {s['brightness']}")
        if round(float(self.brightness_scale.get())) != s["brightness"]:
            self.brightness_scale.set(s["brightness"])
        self.entries["screen"].configure(state="normal" if is_v5 else "disabled")
        self.utf16_check.configure(state="normal" if is_v5 else "disabled")
        self.text_entry.configure(state="readonly" if s["clock"] else "normal")
        self.tenths_check.configure(state="normal" if s["clock"] else "disabled")
        self.toggle_check.configure(state="normal" if s["interval_enabled"] else "disabled")
        for index in (2, 3):
            self.tally_checks[index].configure(state="disabled" if is_v3 else "normal")

        for key, (canvas, label) in self.lamp_widgets.items():
            lamp = status["lamps"][key]
            canvas.configure(background=LAMP_COLORS[lamp])
            title = {"lh": "Left", "txt": "Text", "rh": "Right"}[key]
            label.configure(text=f"{title}: {protocol.LAMP_NAMES[lamp]}")
        if is_v3:
            on = [str(n) for n in range(1, 5) if status["v3_tallies"] & (1 << (n - 1))]
            hint = f"V3.1: boxes 1, 2, 5 and 6 set tally 1 to 4. On now: {', '.join(on) or 'none'}."
        elif s["protocol"] == "v4.0":
            hint = "V4.0: the lamps go into XDATA, boxes 1, 2, 5 and 6 also set tally 1 to 4."
        else:
            hint = "Two bits per lamp: first box red, second box green, both amber."
        self.tally_hint.configure(text=hint)

        if status["connecting"]:
            self.connect_button.configure(text="Connecting...", state="disabled")
        else:
            self.connect_button.configure(
                text="Disconnect" if status["connected"] else "Connect", state="normal")
        self.send_button.configure(state="normal" if status["connected"] else "disabled")
        self.state_label.configure(
            text=f"Connected: {status['connection']}" if status["connected"] else "Disconnected")
        self.error_label.configure(text=self._input_error or status["error"],
                                   foreground=error_color(self.root))

        self.hex_text.configure(state="normal")
        self.hex_text.delete("1.0", "end")
        self.hex_text.insert("1.0", status["packet_error"] or status["packet_hex"])
        self.hex_text.configure(
            state="disabled",
            foreground=error_color(self.root) if status["packet_error"] else self._hex_foreground)
        self.length_label.configure(text=f"{status['packet_length']} bytes")
        last = status["last_sent"]
        when = time.strftime("%H:%M:%S", time.localtime(last)) if last else "never"
        count = status["sent_count"]
        self.sent_label.configure(text=f"Sent: {count} packet{'' if count == 1 else 's'}. Last: {when}")


def run_gui(engine: TslSender, receiver: TslReceiver, access: Access | None = None,
            server=None) -> None:
    """Open the window and run until it is closed.

    Args:
        engine: The sender.
        receiver: The receiver.
        access: The access settings, for the Settings tab.
        server: The web server, for its address and state.
    """
    root = tk.Tk()
    MainWindow(root, engine, receiver, access, server)
    root.mainloop()
