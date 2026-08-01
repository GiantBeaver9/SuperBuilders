"""Novel-item Gate — Anki add-on entry point.

Loaded by Anki (aqt) from `addons21/<package>/__init__.py`. Two jobs:

1. **Make the bundled engine importable.** The pure engine (`gap/`) is packaged
   *inside* this add-on directory by `scripts/bundle_addon.py`. Anki imports the
   add-on as a package, so `gap` would otherwise only be reachable as
   `<package>.gap`; the engine imports itself as top-level `gap` (`from gap.db
   import ...`). We put this add-on's own directory on `sys.path` so `gap`
   resolves as a top-level package.

2. **Register the aqt integration** — but only when `aqt` is actually present.
   The `aqt` import is guarded: importing this package headlessly (no GUI, e.g.
   a test or a bundling run) must not raise. When `aqt` is missing we simply do
   nothing and return.
"""
from __future__ import annotations

import os
import sys

# --- 1. bundled-engine path (do this before importing gap or the ui layer) --- #
_ADDON_DIR = os.path.dirname(os.path.abspath(__file__))
if _ADDON_DIR not in sys.path:
    sys.path.insert(0, _ADDON_DIR)


# --- 2. guarded aqt integration --------------------------------------------- #
def _setup() -> None:
    """Register hooks and the menu. No-op (returns) when aqt is unavailable."""
    try:
        from aqt import gui_hooks, mw  # noqa: F401
    except ImportError:
        # Headless / no GUI (tests, bundling). Nothing to wire.
        return

    from .ui import hooks, menu

    # Reviewer timing + profile-open sync + retirement reaction.
    hooks.register()

    # The top menu is built once the main window's menu bar exists. If aqt is far
    # enough along that `mw.form` is ready we build immediately; otherwise defer
    # to the first profile open.
    def _build_menu_once() -> None:
        if getattr(mw, "form", None) is not None:
            menu.build_menu(mw)

    gui_hooks.main_window_did_init.append(_build_menu_once)


_setup()
