#!/usr/bin/env python3
"""Zeichnet die Ausgabe von ``zv demo --offline`` als SVG auf (Terminal-Screenshot für das README).

python tools/render_demo_output.py   →  docs/img/demo-offline.svg
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from rich.console import Console

WURZEL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WURZEL / "src"))


def main() -> None:
    from zv_klaerfall import cli

    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)
        os.environ["ZV_DATA_DIR"] = ".data"
        console = Console(record=True, width=138, force_terminal=True, color_system="truecolor")
        cli.console = console
        console.print("[bold]$ zv demo --offline[/bold]")
        cli.demo(dateien=None, offline=True, interaktiv=False, sla=False, reset=True, fachdatum=None)
        out = WURZEL / "docs" / "img" / "demo-offline.svg"
        console.save_svg(str(out), title="zv-klaerfall · Offline-Demo")
        print("geschrieben:", out, out.stat().st_size, "Bytes")


if __name__ == "__main__":
    main()
