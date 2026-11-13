"""Locate the libmpv shared library, then import the python-mpv bindings.

Import `mpv` from here rather than directly, so the search paths are set up
first:

    from stackedviewer.libmpv import mpv

Search order:
  1. the directory named by the STACKEDVIEWER_LIBMPV environment variable
  2. a `libmpv/` folder at the project root (drop mpv-2.dll / libmpv.dylib there)
  3. the usual system locations (PATH on Windows, Homebrew on macOS)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _candidate_dirs() -> list[str]:
    dirs = []
    env = os.environ.get("STACKEDVIEWER_LIBMPV")
    if env:
        dirs.append(env)
    dirs.append(str(_PROJECT_ROOT / "libmpv"))
    if sys.platform == "darwin":
        dirs += ["/opt/homebrew/lib", "/usr/local/lib"]
    return [d for d in dirs if os.path.isdir(d)]


def _prepare_search_path() -> None:
    dirs = _candidate_dirs()
    if not dirs:
        return
    if sys.platform == "win32":
        os.environ["PATH"] = os.pathsep.join(dirs + [os.environ.get("PATH", "")])
        for d in dirs:
            os.add_dll_directory(d)
    elif sys.platform == "darwin":
        existing = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
        os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = os.pathsep.join(dirs + ([existing] if existing else []))
    else:
        existing = os.environ.get("LD_LIBRARY_PATH", "")
        os.environ["LD_LIBRARY_PATH"] = os.pathsep.join(dirs + ([existing] if existing else []))


_prepare_search_path()

try:
    import mpv  # noqa: E402
except OSError as exc:  # python-mpv raises OSError when the library is missing
    raise SystemExit(
        "Could not load libmpv.\n"
        "  Windows: download a libmpv build and put mpv-2.dll (or libmpv-2.dll) in the\n"
        f"           folder {_PROJECT_ROOT / 'libmpv'}\n"
        "  macOS:   brew install mpv\n"
        "  Linux:   install libmpv2 (e.g. apt install libmpv2)\n"
        f"Original error: {exc}"
    ) from exc

__all__ = ["mpv"]
