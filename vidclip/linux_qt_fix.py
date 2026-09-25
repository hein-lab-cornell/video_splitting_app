"""Linux-only startup fixes for Qt's xcb (X11) platform plugin.

Qt >= 6.5 needs libxcb-cursor.so.0, which many Linux systems (especially
shared servers / HPC nodes) don't have installed, and users often can't sudo.
If the system copy is missing we look for one in a few user-writable places
and pre-load it, so Qt's xcb plugin can find it without LD_LIBRARY_PATH.

Places searched (first hit wins):
    $VIDCLIP_XCB_CURSOR_DIR
    <app folder>/lib/
    ~/xcb-cursor/lib/            (conda create -p ~/xcb-cursor -c conda-forge xcb-util-cursor)
    ~/.local/lib/xcb-cursor/  and  ~/.local/lib/
    $CONDA_PREFIX/lib, and the lib/ + envs/*/lib of ~/miniconda3, ~/anaconda3,
    ~/miniforge3, ~/mambaforge
"""
from __future__ import annotations

import ctypes
import glob
import os
import sys
from pathlib import Path

LIB = "libxcb-cursor.so.0"
APP_DIR = Path(__file__).resolve().parent.parent

HELP = f"""
------------------------------------------------------------------------
Behavior Clipper can't start its window: Qt needs {LIB}, which isn't
installed on this machine.

Fix without admin rights (needs conda or miniconda):
    conda create -y -p ~/xcb-cursor -c conda-forge xcb-util-cursor
then run the app again; it finds ~/xcb-cursor/lib automatically.

Or, with admin rights:
    sudo dnf install xcb-util-cursor      (Fedora / RHEL / Rocky)
    sudo apt install libxcb-cursor0       (Debian / Ubuntu)

Or put a copy of {LIB} in: {APP_DIR / 'lib'}
------------------------------------------------------------------------
"""


def _candidate_dirs() -> list[Path]:
    home = Path.home()
    dirs: list[str | Path | None] = [
        os.environ.get("VIDCLIP_XCB_CURSOR_DIR"),
        APP_DIR / "lib",
        home / "xcb-cursor" / "lib",
        home / ".local" / "lib" / "xcb-cursor",
        home / ".local" / "lib",
    ]
    if os.environ.get("CONDA_PREFIX"):
        dirs.append(Path(os.environ["CONDA_PREFIX"]) / "lib")
    for base in ("miniconda3", "anaconda3", "miniforge3", "mambaforge", "micromamba"):
        root = home / base
        dirs.append(root / "lib")
        dirs.extend(sorted(glob.glob(str(root / "envs" / "*" / "lib"))))
    seen, out = set(), []
    for d in dirs:
        if d and str(d) not in seen:
            seen.add(str(d))
            out.append(Path(d))
    return out


def ensure_xcb_cursor(verbose: bool = False) -> bool:
    """Make libxcb-cursor loadable. Returns True if Qt should be able to use xcb."""
    if not sys.platform.startswith("linux"):
        return True
    platform = os.environ.get("QT_QPA_PLATFORM", "")
    if platform and not platform.startswith("xcb"):
        return True                     # user chose wayland/offscreen/etc.
    try:
        ctypes.CDLL(LIB)                # system copy present
        return True
    except OSError:
        pass

    errors = []
    for d in _candidate_dirs():
        for f in sorted(d.glob(LIB + "*")) if d.is_dir() else []:
            try:
                ctypes.CDLL(str(f), mode=ctypes.RTLD_GLOBAL)
                if verbose:
                    print(f"[vidclip] using {f}", file=sys.stderr)
                return True
            except OSError as e:
                errors.append(f"  {f}: {e}")

    # Missing entirely. If a Wayland session is available, Qt can use that instead.
    if os.environ.get("WAYLAND_DISPLAY") and not platform:
        os.environ["QT_QPA_PLATFORM"] = "wayland"
        return True

    msg = HELP
    if errors:
        msg += "\nFound a copy but it couldn't be loaded (a dependency may be missing):\n"
        msg += "\n".join(errors) + "\n"
    print(msg, file=sys.stderr)
    return False
