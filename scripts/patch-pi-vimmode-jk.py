"""Patch pi-vimmode to support the jk chord (insert -> normal).

pi-vimmode rejects printable chords by design ("aliases are real key
sequences"), so we patch the installed bundle minimally:

- in insert mode, remember when `j` was typed
- if `k` follows within 500ms: delete the `j` (backspace) and feed an ESC
  through the editor's own pipeline (mode transition stays theirs)

Idempotent; keeps a .bak; re-run after upgrading pi-vimmode.

    .venv/bin/python scripts/patch-pi-vimmode-jk.py
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

BUNDLE = Path.home() / ".pi/agent/npm/node_modules/pi-vimmode/index.js"
ANCHOR = "handleInput(e){let t=D(this.options);"
# Inject at the top of the existing method body (NOT a new method — a second
# handleInput would override the first one in a class body).
PATCH = (
    "handleInput(e){"
    "if(this.modalState.mode===`insert`){"
    "if(e===`j`){this._jkTs=Date.now()}"
    "else if(e===`k`&&this._jkTs&&Date.now()-this._jkTs<500){"
    "this._jkTs=0;super.handleInput(`\\x7f`);this.handleInput(`\\x1b`);return}"
    "else{this._jkTs=0}}"
    "let t=D(this.options);"
)
MARKER = "_jkTs"


def main() -> None:
    if not BUNDLE.exists():
        sys.exit(f"pi-vimmode not installed at {BUNDLE}; run: pi install npm:pi-vimmode")
    src = BUNDLE.read_text(encoding="utf-8")

    if MARKER in src:
        print("already patched — nothing to do")
        return
    if src.count(ANCHOR) != 1:
        sys.exit(f"anchor not unique ({src.count(ANCHOR)} found); pi-vimmode "
                 f"version may have changed — re-check before patching")

    shutil.copy(BUNDLE, BUNDLE.with_suffix(".js.bak"))
    BUNDLE.write_text(src.replace(ANCHOR, PATCH), encoding="utf-8")
    print(f"patched: {BUNDLE}  (backup: {BUNDLE.with_suffix('.js.bak')})")


if __name__ == "__main__":
    main()
