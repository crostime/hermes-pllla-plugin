"""PLLLA bridge plugin for Hermes Agent — entry point.

Hermes imports this directory as a package from ``$HERMES_HOME/plugins/pllla/``
and calls ``register(ctx)`` once at gateway start (docs/agent/EXTERNAL_RUNTIME.md §7).
"""

try:
    from .adapter import register
except ImportError:  # imported as a bare module (pytest collecting this dir) — not a plugin load
    register = None  # type: ignore[assignment]

__all__ = ["register"]
