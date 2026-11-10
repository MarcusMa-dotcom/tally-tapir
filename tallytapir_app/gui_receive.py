"""Receive tab of the Tkinter window."""

from __future__ import annotations

import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

from . import protocol, transport
from .gui_colors import error_color, muted_color
from .receiver import TslReceiver

LAMP_COLORS = ("#2a2f34", "#d0202a", "#1f9d45", "#e59500")
TEXT_COLORS = ("#f2f4f5", "#ff5a5f", "#4fd675", "#ffb52e")
DISPLAY_BACKGROUND = "#0b0d0f"
LABELS = {**protocol.RX_PROTOCOL_LABELS, "junk": "?"}
ENTRY_FIELDS = ("bind", "port", "watch_screen", "watch_id")
COLUMNS = (
    ("screen", "Screen", 50), ("index", "ID", 45), ("text", "Text", 140), ("lh", "Left", 50),
    ("txt", "Text tally", 65), ("rh", "Right", 50), ("tallies", "Tally 1-4", 65),
    ("brightness", "Bright.", 50), ("protocol", "Protocol", 60), ("count", "Packets", 55),
    ("time", "Last seen", 65),
)


def _clock(stamp: float | None) -> str:
    """Time of day of a timestamp, ``never`` for none."""
    return time.strftime("%H:%M:%S", time.localtime(stamp)) if stamp else "never"


def _lamp(value: int | None) -> str:
    """Name of a lamp state, a dash where the version has no lamps."""
    return "-" if value is None else protocol.LAMP_NAMES[value]


def _tallies(bits: int | None) -> str:
    """The tallies that are on, as text. A dash where the version has none."""
    if bits is None:
        return "-"
    on = [str(n) for n in range(1, 5) if bits & (1 << (n - 1))]
    return ", ".join(on) if on else "none"


class ReceiveTab(ttk.Frame):
    """The Receive tab: listen settings, filter, watched display, table and log.

    Like the Send tab it only hands inputs to the receiver and redraws from
    the receiver's snapshot when asked by :meth:`poll`.

    Args:
        parent: The notebook the tab belongs to.
        receiver: The receiver.
    """
    def __init__(self, parent: tk.Widget, receiver: TslReceiver):
        """Build the tab. See the class description for the arguments."""
        super().__init__(parent, padding=10)
        self.receiver = receiver
        self._loading = False
        self._seen_revision = -1
        self._input_error = ""

        self.vars: dict[str, tk.Variable] = {
            name: tk.StringVar() for name in
            ("transport", "serial_port", "baudrate", "bytesize", "parity", "stopbits", "protocol")
        }
        for name in ENTRY_FIELDS:
            self.vars[name] = tk.StringVar()
        self.vars["hex_braces"] = tk.BooleanVar()
        self.entries: dict[str, ttk.Entry] = {}

        left = ttk.Frame(self)
        right = ttk.Frame(self)
        left.grid(row=0, column=0, sticky="n", padx=(0, 10))
        right.grid(row=0, column=1, sticky="n")
        self._build_listen(left)
        self._build_watch(left)
        self._build_displays(right)
        self._build_log(right)

    # -- layout ----------------------------------------------------------

    def _group(self, parent: tk.Widget, title: str) -> ttk.LabelFrame:
        """A titled frame, stacked below the previous one."""
        frame = ttk.LabelFrame(parent, text=title, padding=8)
        frame.pack(fill="x", pady=(0, 8))
        return frame

    def _entry(self, parent: tk.Widget, name: str, width: int, live: bool = False) -> ttk.Entry:
        """An entry field.

        Args:
            parent: Frame to put it in.
            name: Receiver setting it belongs to.
            width: Width in characters.
            live: Hand over every change at once. Other fields wait for Return
                or for the focus to leave.
        """
        entry = ttk.Entry(parent, textvariable=self.vars[name], width=width)
        if live:
            # Filters follow every change of the field: typing, pasting, dictation.
            self.vars[name].trace_add("write", lambda *_: self._push(name, self.vars[name].get()))
        entry.bind("<FocusOut>", lambda _event: self._push(name, self.vars[name].get()))
        entry.bind("<Return>", lambda _event: self._push(name, self.vars[name].get()))
        self.entries[name] = entry
        return entry

    def _combo(self, parent: tk.Widget, name: str, values, width: int) -> ttk.Combobox:
        """A drop-down list that hands its choice over when one is picked."""
        combo = ttk.Combobox(parent, textvariable=self.vars[name], values=values, width=width,
                             state="readonly")
        combo.bind("<<ComboboxSelected>>", lambda _event: self._push_combo(name))
        return combo

    def _build_listen(self, parent: tk.Widget) -> None:
        """Listen group: transport, address or serial settings, protocol, Listen button."""
        frame = self._group(parent, "Listen")
        row = ttk.Frame(frame)
        row.pack(fill="x")
        for key, label in transport.TRANSPORT_LABELS.items():
            ttk.Radiobutton(
                row, text=label, value=key, variable=self.vars["transport"],
                command=lambda: self._push("transport", self.vars["transport"].get()),
            ).pack(side="left", padx=(0, 10))

        bottom = ttk.Frame(frame)
        bottom.pack(fill="x", side="bottom", pady=(8, 0))
        ttk.Label(bottom, text="Protocol").pack(anchor="w")
        controls = ttk.Frame(bottom)  # own row, so a long message below cannot move the button
        controls.pack(anchor="w")
        self._combo(controls, "protocol", tuple(protocol.RX_PROTOCOL_LABELS.values()), 7).pack(side="left")
        self.listen_button = ttk.Button(controls, text="Listen", command=self._toggle_listening)
        self.listen_button.pack(side="left", padx=(10, 0))
        self.state_label = ttk.Label(bottom, text="Not listening")
        self.state_label.pack(anchor="w", pady=(6, 0))
        self.error_label = ttk.Label(bottom, text="", wraplength=300, justify="left")
        self.error_label.pack(anchor="w")

        self.ip_frame = ttk.Frame(frame)
        ttk.Label(self.ip_frame, text="Listen address").grid(row=0, column=0, sticky="w")
        ttk.Label(self.ip_frame, text="Port").grid(row=0, column=1, sticky="w", padx=(8, 0))
        self._entry(self.ip_frame, "bind", 18).grid(row=1, column=0)
        self._entry(self.ip_frame, "port", 7).grid(row=1, column=1, padx=(8, 0))

        self.serial_frame = ttk.Frame(frame)
        ttk.Label(self.serial_frame, text="Serial port").grid(row=0, column=0, columnspan=3, sticky="w")
        self.port_combo = ttk.Combobox(self.serial_frame, textvariable=self.vars["serial_port"], width=22)
        self.port_combo.grid(row=1, column=0, columnspan=3, sticky="w")
        for event in ("<<ComboboxSelected>>", "<FocusOut>", "<Return>"):
            self.port_combo.bind(event, lambda _event: self._push("serial_port", self.vars["serial_port"].get()))
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

    def _build_watch(self, parent: tk.Widget) -> None:
        """Watched display group: the filter fields and the picture of a UMD."""
        frame = self._group(parent, "Watched display")
        ttk.Label(frame, text="Screen").grid(row=0, column=0, sticky="w")
        ttk.Label(frame, text="UMD ID").grid(row=0, column=1, sticky="w", padx=(8, 0))
        self._entry(frame, "watch_screen", 7, live=True).grid(row=1, column=0, sticky="w")
        self._entry(frame, "watch_id", 7, live=True).grid(row=1, column=1, sticky="w", padx=(8, 0))
        ttk.Label(
            frame, wraplength=300, justify="left",
            text="Empty means any. Screen applies to V5.0 only. The V5.0 broadcast address 65535 always matches.",
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))

        display = tk.Frame(frame, background=DISPLAY_BACKGROUND, padx=6, pady=6)
        display.grid(row=3, column=0, columnspan=3, sticky="we", pady=(8, 0))
        self.lamp_left = tk.Frame(display, width=30, height=40, background=LAMP_COLORS[0])
        self.lamp_left.pack(side="left")
        self.lamp_right = tk.Frame(display, width=30, height=40, background=LAMP_COLORS[0])
        self.lamp_right.pack(side="right")
        self.display_text = tk.Label(display, text="", background=DISPLAY_BACKGROUND,
                                     foreground=TEXT_COLORS[0], font=("TkFixedFont", 16, "bold"),
                                     width=18, wraplength=230)
        self.display_text.pack(side="left", fill="both", expand=True, padx=6)
        self.display_info = ttk.Label(frame, text="Nothing received for this filter yet.",
                                      wraplength=300, justify="left")
        self.display_info.grid(row=4, column=0, columnspan=3, sticky="w", pady=(6, 0))

    def _build_displays(self, parent: tk.Widget) -> None:
        """Displays seen group: the table with one row per screen and UMD ID."""
        frame = self._group(parent, "Displays seen")
        self.table = ttk.Treeview(frame, columns=[c[0] for c in COLUMNS], show="headings", height=6)
        for key, title, width in COLUMNS:
            self.table.heading(key, text=title, anchor="w")
            self.table.column(key, width=width, anchor="w", stretch=False)
        bold = tkfont.nametofont("TkDefaultFont").copy()
        bold.configure(weight="bold")
        self.table.tag_configure("watched", font=bold)  # the watched display stands out
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        self.table.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")

    def _build_log(self, parent: tk.Widget) -> None:
        """Received packets group: counters, the log and the Clear button."""
        frame = self._group(parent, "Received packets")
        top = ttk.Frame(frame)
        top.grid(row=0, column=0, columnspan=2, sticky="we")
        self.count_label = ttk.Label(top, text="Packets: 0, with problems: 0")
        self.count_label.pack(side="left")
        ttk.Button(top, text="Clear", command=self.receiver.clear).pack(side="right")
        ttk.Checkbutton(
            top, text="{} format", variable=self.vars["hex_braces"],
            command=lambda: self._push("hex_braces", bool(self.vars["hex_braces"].get())),
        ).pack(side="right", padx=(0, 10))
        self.log_text = tk.Text(frame, width=96, height=14, wrap="word", font="TkFixedFont",
                                state="disabled", relief="solid", borderwidth=1)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        scroll.grid(row=1, column=1, sticky="ns", pady=(6, 0))

    # -- user input -> receiver -----------------------------------------

    def _push(self, name: str, value) -> None:
        """Hand one changed value to the receiver. A refused value shows as error text."""
        if self._loading:
            return
        try:
            self.receiver.update(**{name: value})
            self._input_error = ""
        except ValueError as exc:
            self._input_error = str(exc)
        self._seen_revision = -1

    def _push_combo(self, name: str) -> None:
        """Hand over a drop-down choice. The protocol label is turned back into its name."""
        value = self.vars[name].get()
        if name == "protocol":
            value = next(key for key, label in protocol.RX_PROTOCOL_LABELS.items() if label == value)
        self._push(name, value)

    def _toggle_listening(self) -> None:
        """Start or stop listening, depending on the current state."""
        if self.receiver.listening:
            self.receiver.stop_listening()
        else:
            self.receiver.listen()
        self._seen_revision = -1

    def _refresh_ports(self) -> None:
        """Fill the list of serial ports from what the machine has now."""
        self.port_combo.configure(values=[port["device"] for port in transport.list_serial_ports()])

    # -- receiver -> widgets --------------------------------------------

    def poll(self) -> None:
        """Redraw if the receiver changed. The window calls this every 100 ms."""
        if self.receiver.revision != self._seen_revision:
            self._load(self.receiver.snapshot())

    def _set(self, name: str, value) -> None:
        """Set a Tk variable only if the value differs, to avoid needless events."""
        var = self.vars[name]
        if var.get() != value:
            var.set(value)

    def _load(self, data: dict) -> None:
        """Draw the whole tab from a snapshot of the receiver."""
        self._seen_revision = data["revision"]
        s = data["settings"]
        status = data["status"]
        focused = self.focus_get()

        self._loading = True
        try:
            for name in ("transport", "serial_port", "parity"):
                self._set(name, s[name])
            for name in ("baudrate", "bytesize"):
                self._set(name, str(s[name]))
            self._set("stopbits", f"{s['stopbits']:g}")
            self._set("protocol", protocol.RX_PROTOCOL_LABELS[s["protocol"]])
            self._set("hex_braces", s["hex_braces"])
            for name in ENTRY_FIELDS:
                if self.entries[name] is not focused:
                    self._set(name, "" if s[name] is None else str(s[name]))
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

        self.listen_button.configure(text="Stop listening" if status["listening"] else "Listen")
        state = f"Listening: {status['description']}" if status["listening"] else "Not listening"
        if status["listening"] and s["transport"] == "tcp":
            state += f", {status['clients']} client{'' if status['clients'] == 1 else 's'}"
        self.state_label.configure(text=state)
        self.error_label.configure(text=self._input_error or status["error"],
                                   foreground=error_color(self))
        self.log_text.tag_configure("problem", foreground=error_color(self))
        self.log_text.tag_configure("other", foreground=muted_color(self))

        watched = status["watched"]
        if watched:
            self.lamp_left.configure(background=LAMP_COLORS[watched["lh"] or 0])
            self.lamp_right.configure(background=LAMP_COLORS[watched["rh"] or 0])
            self.display_text.configure(text=watched["text"], foreground=TEXT_COLORS[watched["txt"] or 0])
            parts = [LABELS[watched["protocol"]]]
            if watched["screen"] is not None:
                parts.append(f"screen {watched['screen']}")
            parts.append(f"ID {watched['index']}")
            if watched["lh"] is not None:
                parts.append(f"left {_lamp(watched['lh'])}, text {_lamp(watched['txt'])}, "
                             f"right {_lamp(watched['rh'])}")
            if watched["tallies"] is not None:
                parts.append(f"tally {_tallies(watched['tallies'])}")
            parts.append(f"brightness {watched['brightness']}")
            parts.append(f"{watched['count']} packet{'' if watched['count'] == 1 else 's'}, "
                         f"last {_clock(watched['time'])}")
            self.display_info.configure(text=" · ".join(parts))
        else:
            self.lamp_left.configure(background=LAMP_COLORS[0])
            self.lamp_right.configure(background=LAMP_COLORS[0])
            self.display_text.configure(text="", foreground=TEXT_COLORS[0])
            self.display_info.configure(text="Nothing received for this filter yet.")

        self.table.delete(*self.table.get_children())
        for d in status["displays"]:
            is_watched = bool(watched) and all(d[key] == watched[key] for key in ("screen", "index", "protocol"))
            self.table.insert("", "end", tags=("watched",) if is_watched else (), values=(
                "-" if d["screen"] is None else d["screen"], d["index"], d["text"],
                _lamp(d["lh"]), _lamp(d["txt"]), _lamp(d["rh"]), _tallies(d["tallies"]),
                d["brightness"], LABELS[d["protocol"]], d["count"], _clock(d["time"]),
            ))

        self.count_label.configure(
            text=f"Packets: {status['packet_count']}, with problems: {status['problem_count']}")
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        for entry in reversed(status["log"]):
            tag = () if entry["matched"] else ("other",)
            head = (f"{_clock(entry['time'])}  {entry['source']}  "
                    f"{LABELS.get(entry['protocol'], entry['protocol'])}  {entry['length']} B\n")
            self.log_text.insert("end", head, tag)
            self.log_text.insert("end", f"  {entry['hex']}\n", tag)
            if entry["summary"]:
                suffix = "" if entry["matched"] else "  (not watched)"
                self.log_text.insert("end", f"  {entry['summary']}{suffix}\n", tag)
            for problem in entry["problems"]:
                self.log_text.insert("end", f"  Problem: {problem}\n", ("problem",))
        if not status["log"]:
            self.log_text.insert("end", "Nothing received yet.", ("other",))
        self.log_text.configure(state="disabled")
