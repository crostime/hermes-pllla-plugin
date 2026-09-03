"""The PLLLA agent's persona in Hermes' own house: ``$HERMES_HOME/SOUL.md``.

Ownership rule (docs/agent/EXTERNAL_RUNTIME.md §6.9, same as OpenClaw):
missing file, Hermes' untouched default, or a file carrying our marker is
ours to (re)write; anything the person edited is left alone.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .pairing import Identity

PERSONA_MARKER = "<!-- pllla:persona -->"
HERMES_DEFAULT_SOUL_PREFIX = "You are Hermes Agent, built by Nous Research."


def build_persona_prompt(identity: Identity) -> str:
    parts = [
        f"You are {identity.name}, a PLLLA agent." if identity.name else "",
        identity.bio,
        identity.system_prompt,
    ]
    return "\n\n".join(part for part in parts if part)


def build_soul_markdown(identity: Identity) -> str:
    title = f"# SOUL.md - {identity.name}" if identity.name else "# SOUL.md"
    return "\n".join(
        [
            title,
            "",
            PERSONA_MARKER,
            "_Managed by PLLLA: this persona is the PLLLA agent's identity, installed at pairing. "
            "Edit the agent in PLLLA, or delete the marker line above to take over this file yourself._",
            "",
            build_persona_prompt(identity),
            "",
        ]
    )


def is_hermes_default_soul(content: str) -> bool:
    first_line = next((line.strip() for line in content.split("\n") if line.strip()), "")
    return first_line.startswith(HERMES_DEFAULT_SOUL_PREFIX)


def soul_file_is_ours(content: Optional[str]) -> bool:
    if content is None:
        return True
    return PERSONA_MARKER in content or is_hermes_default_soul(content)


def install_persona(hermes_home: Path, identity: Identity) -> bool:
    """Write SOUL.md when it is ours; returns whether it was written."""
    path = Path(hermes_home) / "SOUL.md"
    try:
        current: Optional[str] = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        current = None
    if not soul_file_is_ours(current):
        return False
    desired = build_soul_markdown(identity)
    if current == desired:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(desired, encoding="utf-8")
    return True
