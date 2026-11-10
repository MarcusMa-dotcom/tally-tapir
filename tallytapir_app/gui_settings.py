"""Settings and About tabs of the Tkinter window."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from . import __version__, about
from .access import MIN_PASSWORD_LENGTH, Access
from .gui_colors import error_color

FLAGS = ("web_enabled", "web_login", "api_enabled", "api_login")
USERS = ("web_user", "api_user")
PASSWORDS = ("web_password", "api_password")


class SettingsTab(ttk.Frame):
    """Switches and logins for the web interface and the REST API."""

    def __init__(self, parent: tk.Widget, access: Access, server=None):
        """Build the tab.

        Args:
            parent: The notebook the tab belongs to.
            access: The access settings.
            server: The web server, or None if the program runs without one.
        """
        super().__init__(parent, padding=10)
        self.access = access
        self.server = server
        self._shown: dict = {}

        self.vars: dict[str, tk.Variable] = {name: tk.BooleanVar() for name in FLAGS}
        for name in USERS + PASSWORDS:
            self.vars[name] = tk.StringVar()
        self.password_states: dict[str, ttk.Label] = {}

        self._build_area("web", "Web interface", "Web interface on")
        self._build_area("api", "REST API", "REST API on")

        ttk.Label(
            self, wraplength=560, justify="left",
            text=(f"Passwords need at least {MIN_PASSWORD_LENGTH} characters. They are stored as a "
                  "salted hash, never as text. Leave a password field empty to keep the password "
                  "that is set."),
        ).pack(anchor="w", pady=(0, 8))
        row = ttk.Frame(self)
        row.pack(anchor="w")
        self.apply_button = ttk.Button(row, text="Apply", command=self.apply)
        self.apply_button.pack(side="left")
        self.message = ttk.Label(row, text="")
        self.message.pack(side="left", padx=(10, 0))
        self.server_label = ttk.Label(self, text="", wraplength=560, justify="left")
        self.server_label.pack(anchor="w", pady=(12, 0))
        self.warning_label = ttk.Label(self, text="", wraplength=560, justify="left")
        self.warning_label.pack(anchor="w", pady=(4, 0))
        self._load(self.access.public())

    def _build_area(self, area: str, title: str, switch_label: str) -> None:
        """One group for a door: switch, login box, user name and password."""
        frame = ttk.LabelFrame(self, text=title, padding=8)
        frame.pack(fill="x", pady=(0, 8), anchor="w")
        ttk.Checkbutton(frame, text=switch_label, variable=self.vars[f"{area}_enabled"]).grid(
            row=0, column=0, sticky="w", padx=(0, 18))
        ttk.Checkbutton(frame, text="Require login", variable=self.vars[f"{area}_login"]).grid(
            row=0, column=1, sticky="w")
        ttk.Label(frame, text="User name").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Label(frame, text="New password").grid(row=1, column=1, sticky="w", pady=(6, 0))
        ttk.Entry(frame, textvariable=self.vars[f"{area}_user"], width=24).grid(
            row=2, column=0, sticky="w", padx=(0, 18))
        ttk.Entry(frame, textvariable=self.vars[f"{area}_password"], width=24, show="•").grid(
            row=2, column=1, sticky="w")
        state = ttk.Label(frame, text="")
        state.grid(row=3, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self.password_states[area] = state

    # -- form <-> settings ----------------------------------------------

    def _load(self, data: dict) -> None:
        """Fill the form from the access settings and empty the password fields."""
        self._shown = data
        for name in FLAGS + USERS:
            self.vars[name].set(data[name])
        for name in PASSWORDS:
            self.vars[name].set("")
        for area, label in self.password_states.items():
            label.configure(text="A password is set." if data[f"{area}_password_set"]
                            else "No password set yet.")

    def _changes(self) -> dict:
        """What the form differs in from the stored settings."""
        changes = {}
        for name in FLAGS:
            if bool(self.vars[name].get()) != self._shown[name]:
                changes[name] = bool(self.vars[name].get())
        for name in USERS:
            if self.vars[name].get().strip() != self._shown[name]:
                changes[name] = self.vars[name].get()
        for name in PASSWORDS:
            if self.vars[name].get():
                changes[name] = self.vars[name].get()
        return changes

    def apply(self) -> None:
        """Save the changes. A refused value shows next to the button."""
        changes = self._changes()
        if not changes:
            self.message.configure(text="Nothing to change.", foreground="")
            return
        try:
            data = self.access.update(**changes)
        except ValueError as exc:
            self.message.configure(text=str(exc), foreground=error_color(self))
            return
        self._load(data)
        self.message.configure(text="Saved.", foreground="")

    def poll(self) -> None:
        """Follow changes made elsewhere, unless the form holds unsaved edits."""
        current = self.access.public()
        if current != self._shown and not self._changes():
            self._load(current)
        self._show_server(current)

    def _show_server(self, data: dict) -> None:
        """Show where the web server runs and warn about doors that stand open."""
        server = self.server
        if server is None:
            self.server_label.configure(text="The web server was not started (--no-web).")
            self.warning_label.configure(text="")
            return
        if server.error:
            self.server_label.configure(text=server.error, foreground=error_color(self))
        elif server.running:
            doors = []
            if data["web_enabled"]:
                doors.append(f"web interface {server.url}")
            if data["api_enabled"]:
                doors.append(f"REST API {server.url}api/v1/")
            self.server_label.configure(text="Running: " + ", ".join(doors), foreground="")
        else:
            self.server_label.configure(
                text="Web interface and REST API are switched off, the port is closed.", foreground="")
        warnings = []
        if server.exposed and server.running:
            if data["web_enabled"] and not data["web_login"]:
                warnings.append("The web interface can be reached from the network without a login.")
            if data["api_enabled"] and not data["api_login"]:
                warnings.append("The REST API can be reached from the network without a login.")
            if not server.tls and (data["web_login"] or data["api_login"]):
                warnings.append("Without TLS, passwords cross the network unencrypted. "
                                "Start with --tls-cert and --tls-key.")
        self.warning_label.configure(text="\n".join(warnings), foreground=error_color(self))


class AboutTab(ttk.Frame):
    """The About tab: logo, author, license and disclaimer."""
    def __init__(self, parent: tk.Widget, logo: tk.PhotoImage | None = None):
        """Build the tab.

        Args:
            parent: The notebook the tab belongs to.
            logo: The logo picture of the window, shown at half size.
        """
        super().__init__(parent, padding=16)
        if logo is not None:
            self.logo = logo.subsample(2)  # kept on the widget, or Tk drops the picture
            ttk.Label(self, image=self.logo).pack(anchor="w", pady=(0, 8))
        ttk.Label(self, text=f"Tally Tapir {__version__}", font=("TkDefaultFont", 16, "bold")).pack(anchor="w")
        ttk.Label(self, text=about.SUMMARY).pack(anchor="w", pady=(4, 12))
        ttk.Label(self, text=f"Author: {about.AUTHOR}").pack(anchor="w")
        ttk.Label(self, text=f"License: {about.LICENSE_NAME}. {about.COPYRIGHT}.").pack(anchor="w")
        ttk.Label(self, text=f"Source code: {about.HOMEPAGE}").pack(anchor="w")
        ttk.Label(self, text=about.DISCLAIMER, wraplength=640, justify="left").pack(anchor="w", pady=(8, 12))
        text = tk.Text(self, width=84, height=12, wrap="word", font="TkFixedFont",
                       relief="solid", borderwidth=1)
        text.insert("1.0", about.LICENSE_TEXT)
        text.configure(state="disabled")
        text.pack(anchor="w")
