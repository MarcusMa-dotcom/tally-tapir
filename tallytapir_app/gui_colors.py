"""Text colours for the Tkinter window that stay readable in light and dark mode.

The functions look at the window background each time they are called, so
they follow a system that switches its appearance while the program runs.
"""

from __future__ import annotations

import tkinter as tk


def is_dark(widget: tk.Misc) -> bool:
    """True if the window currently has a dark background."""
    try:
        red, green, blue = widget.winfo_rgb(widget.winfo_toplevel().cget("background"))
    except tk.TclError:
        return False
    return (red + green + blue) / 3 < 32768


def error_color(widget: tk.Misc) -> str:
    """A red that reads well on the current background."""
    return "#ff7b72" if is_dark(widget) else "#a4161a"


def muted_color(widget: tk.Misc) -> str:
    """A grey for less important text that reads well on the current background."""
    return "#9aa1a8" if is_dark(widget) else "#6b7278"
