"""Start script of the packaged app."""

import os
import sys

# A program started from the Finder has no terminal to write to.
for name in ("stdout", "stderr"):
    if getattr(sys, name) is None:
        setattr(sys, name, open(os.devnull, "w"))

from tallytapir_app.__main__ import main  # noqa: E402

if __name__ == "__main__":
    # Older systems pass a process serial number as an argument, drop it.
    sys.exit(main([arg for arg in sys.argv[1:] if not arg.startswith("-psn_")]))
