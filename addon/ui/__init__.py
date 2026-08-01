"""aqt UI layer for the novel-item gate add-on.

Everything under `addon.ui` may import `aqt`/Qt at module top level — these
modules are only ever imported once the package `__init__` has confirmed `aqt`
is present. Nothing here is imported during a headless `import gap.*`.

Public surface:
  * `hooks.register()`        — wire the gui_hooks (profile open, reviewer timing)
  * `menu.build_menu(mw)`     — install the "Novel-item Gate" top menu
  * `dashboard.open_dashboard(mw)`
  * `novel_dialog.open_novel_dialog(mw)`
"""
