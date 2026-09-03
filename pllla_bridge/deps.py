"""Lazy dependency for the socket lane: ``python-socketio`` (asyncio client).

The Hermes venv ships aiohttp but not python-socketio. The connector installs
it at bridge time (``uv pip install --python <venv>``); this module is the
in-process fallback the gateway calls through ``ensure_deps_fn`` when the
passive check fails — deps only, never credentials (Hermes contract).
"""

from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

SOCKETIO_REQUIREMENT = "python-socketio>=5.11,<6"


def socketio_available() -> bool:
    return importlib.util.find_spec("socketio") is not None


def _uv_candidates(hermes_home: Optional[Path]) -> List[str]:
    candidates: List[str] = []
    if hermes_home:
        candidates.append(str(Path(hermes_home) / "bin" / "uv"))
    on_path = shutil.which("uv")
    if on_path:
        candidates.append(on_path)
    candidates.append(str(Path.home() / ".local" / "bin" / "uv"))
    return [candidate for candidate in candidates if Path(candidate).exists()]


def install_socketio(hermes_home: Optional[Path] = None, *, runner=subprocess.run) -> bool:
    """Install into the interpreter running this code (the Hermes venv)."""
    if socketio_available():
        return True
    python = sys.executable
    attempts: List[List[str]] = [
        [uv, "pip", "install", "--python", python, SOCKETIO_REQUIREMENT]
        for uv in _uv_candidates(hermes_home)
    ]
    attempts.append([python, "-m", "pip", "install", SOCKETIO_REQUIREMENT])
    for command in attempts:
        try:
            result = runner(command, capture_output=True, text=True, timeout=300)
        except Exception:  # noqa: BLE001 — missing binary, timeout
            continue
        if result.returncode == 0:
            importlib.invalidate_caches()
            if socketio_available():
                return True
    return False


def ensure_socketio(hermes_home: Optional[Path] = None) -> bool:
    if socketio_available():
        return True
    home = hermes_home or (Path(os.environ["HERMES_HOME"]) if os.environ.get("HERMES_HOME") else None)
    return install_socketio(home)
