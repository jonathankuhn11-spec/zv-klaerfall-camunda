#!/usr/bin/env python3
"""Rendert das Klärfall-Formular mit form-js (die Bibliothek hinter Camunda Tasklist) als PNG.

Verifiziert damit zugleich, dass das Formular-Schema gültig ist und die Vorlagenausdrücke
(``{{klassifikation.kategorie}}`` …) mit echten Prozessvariablen funktionieren. Die Variablen stammen
aus einem Offline-Durchlauf von ``samples/pain.001/04_betrag_limit.xml``.

    python tools/render_form.py   →  docs/img/zv-klaerfall-form.png
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

WURZEL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WURZEL / "src"))
NODE_MODULES = Path(os.environ.get("NODE_MODULES", WURZEL / "node_modules"))


def beispielvariablen() -> dict:
    import tempfile

    from zv_klaerfall import handlers
    from zv_klaerfall.config import Dienste, Einstellungen
    from zv_klaerfall.handlers import JobKontext
    from zv_klaerfall.rules import dmn_lite

    with tempfile.TemporaryDirectory() as tmp:
        dienste = Dienste(Einstellungen(datenverzeichnis=Path(tmp)))
        ctx = JobKontext(heute=date(2026, 10, 6))
        xml = (WURZEL / "samples" / "pain.001" / "04_betrag_limit.xml").read_text(encoding="utf-8")
        v = handlers.nachricht_validieren(
            {"nachrichtXml": xml, "dateiname": "04_betrag_limit.xml"}, dienste, ctx
        )
        v = {"zahlung": v["zahlungen"][0], **v}
        v.update(handlers.pruefen(v, dienste, ctx))
        tabelle = dmn_lite.lade(WURZEL / "bpmn" / "klaerfall-klassifikation.dmn")
        v["klassifikation"] = dmn_lite.werte_aus(tabelle, v)
        return {k: v[k] for k in ("zahlungText", "befundeText", "klassifikation")}


def main() -> int:
    from playwright.sync_api import sync_playwright

    schema = json.loads((WURZEL / "forms" / "zv-klaerfall-form.form").read_text(encoding="utf-8"))
    daten = beispielvariablen()
    css = (NODE_MODULES / "@bpmn-io/form-js/dist/assets/form-js.css").read_text(encoding="utf-8")
    js = (NODE_MODULES / "@bpmn-io/form-js/dist/form-viewer.umd.js").read_text(encoding="utf-8")
    html = f"""<!doctype html><html><head><meta charset="utf-8"><style>{css}</style>
    <style>html,body{{margin:0;background:#fff;font-family:IBM Plex Sans,Arial,sans-serif}} #f{{width:760px;padding:16px}}</style>
    </head><body><div id="f"></div><script>{js}</script></body></html>"""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 800, "height": 1200}, device_scale_factor=2)
        page.set_content(html)
        fehler = page.evaluate(
            """async ([schema, data]) => {
                const form = new FormViewer.Form({ container: document.querySelector('#f') });
                const { warnings } = await form.importSchema(schema, data);
                return warnings.map(w => String(w));
            }""",
            [schema, daten],
        )
        page.wait_for_timeout(300)
        out = WURZEL / "docs" / "img" / "zv-klaerfall-form.png"
        page.query_selector("#f").screenshot(path=str(out), type="png")
        browser.close()
    print("Formular gerendert:", out, "Warnungen:", fehler or "keine")
    return 1 if fehler else 0


if __name__ == "__main__":
    raise SystemExit(main())
