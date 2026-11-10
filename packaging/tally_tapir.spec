# PyInstaller recipe for the macOS app "Tally Tapir.app".
#
#   pyinstaller --noconfirm packaging/tally_tapir.spec
#
# Build with a Python that carries Tk and targets an old enough macOS, for
# example a python.org installer or a python-build-standalone build. The app
# runs on the processor type of the Python it was built with.

import os

from PyInstaller.utils.hooks import collect_data_files

HERE = SPECPATH  # noqa: F821  (set by PyInstaller)
VERSION = "0.1.0"
ICON = os.path.join(HERE, "Tally Tapir.icns")

analysis = Analysis(  # noqa: F821
    [os.path.join(HERE, "launcher.py")],
    datas=collect_data_files("tallytapir_app"),      # templates and static files
    hiddenimports=["tallytapir", "serial", "serial.tools.list_ports"],
    # Pure-Python fallback instead of the compiled speedup: one binary less to trust and sign.
    excludes=["markupsafe._speedups"],
)
archive = PYZ(analysis.pure)  # noqa: F821
executable = EXE(  # noqa: F821
    archive,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="Tally Tapir",
    console=False,
)
collected = COLLECT(executable, analysis.binaries, analysis.datas, name="Tally Tapir")  # noqa: F821
app = BUNDLE(  # noqa: F821
    collected,
    name="Tally Tapir.app",
    icon=ICON if os.path.exists(ICON) else None,
    bundle_identifier="de.marcusmathy.tallytapir",
    version=VERSION,
    info_plist={
        "CFBundleDisplayName": "Tally Tapir",
        "CFBundleShortVersionString": VERSION,
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "11.0",
        "NSHumanReadableCopyright": "Copyright (c) 2026 Marcus Mathy. MIT License. Use at your own risk.",
        "LSApplicationCategoryType": "public.app-category.utilities",
        "NSLocalNetworkUsageDescription":
            "Tally Tapir sends and receives TSL tally packets on your network.",
    },
)
