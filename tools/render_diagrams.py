#!/usr/bin/env python3
"""Rendert die BPMN-/DMN-Modelle als SVG und PNG nach ``docs/img/`` (für README und Fachkonzept).

Nutzt bpmn-js / dmn-js (die Bibliotheken hinter dem Camunda Modeler) in einem Headless-Chromium
via Playwright, damit die Bilder exakt so aussehen wie im Modeler.

Voraussetzungen (nur für die Bilderzeugung, nicht für das Projekt selbst):
    pip install playwright && playwright install chromium
    npm install            # bpmn-js, dmn-js (siehe package.json)

Aufruf:  python tools/render_diagrams.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

WURZEL = Path(__file__).resolve().parents[1]
BPMN_DIR = WURZEL / "bpmn"
IMG_DIR = WURZEL / "docs" / "img"
NODE_MODULES = Path(__import__("os").environ.get("NODE_MODULES", WURZEL / "node_modules"))


def _lese(rel: str) -> str:
    return (NODE_MODULES / rel).read_text(encoding="utf-8")


def render_bpmn(page, bpmn_pfad: Path) -> tuple[str, bytes]:
    html = f"""<!doctype html><html><head><meta charset="utf-8">
    <style>{_lese("bpmn-js/dist/assets/diagram-js.css")}</style>
    <style>{_lese("bpmn-js/dist/assets/bpmn-js.css")}</style>
    <style>{_lese("bpmn-js/dist/assets/bpmn-font/css/bpmn-embedded.css")}</style>
    <style>html,body{{margin:0;background:#fff}} #c{{width:1400px;height:700px}}</style>
    </head><body><div id="c"></div>
    <script>{_lese("bpmn-js/dist/bpmn-viewer.development.js")}</script></body></html>"""
    page.set_content(html)
    xml = bpmn_pfad.read_text(encoding="utf-8")
    svg = page.evaluate(
        """async (xml) => {
            const viewer = new BpmnJS({ container: '#c' });
            await viewer.importXML(xml);
            viewer.get('canvas').zoom('fit-viewport', 'auto');
            const { svg } = await viewer.saveSVG();
            return svg;
        }""",
        xml,
    )
    # PNG: SVG in eine Seite mit exakter Größe laden und screenshotten
    page.set_content(f"<!doctype html><html><body style='margin:0;background:#fff'>{svg}</body></html>")
    el = page.query_selector("svg")
    png = el.screenshot(type="png", scale="device")
    return svg, png


def render_dmn(page, dmn_pfad: Path) -> tuple[str, bytes]:
    css = "".join(
        f"<style>{_lese('dmn-js/dist/assets/' + f)}</style>"
        for f in (
            "diagram-js.css",
            "dmn-js-shared.css",
            "dmn-js-drd.css",
            "dmn-js-decision-table.css",
            "dmn-js-literal-expression.css",
            "dmn-font/css/dmn-embedded.css",
        )
    )
    html = f"""<!doctype html><html><head><meta charset="utf-8">{css}
    <style>html,body{{margin:0;background:#fff;font-family:Arial,sans-serif}} #c{{width:2400px}}
    .dmn-decision-table-container{{padding:12px;width:fit-content}}
    .view-drd, .powered-by, .bjs-powered-by, .dmn-definitions{{display:none !important}}</style>
    </head><body><div id="c"></div>
    <script>{_lese("dmn-js/dist/dmn-viewer.development.js")}</script></body></html>"""
    page.set_content(html)
    xml = dmn_pfad.read_text(encoding="utf-8")
    page.evaluate(
        """async (xml) => {
            const viewer = new DmnJS({ container: '#c' });
            await viewer.importXML(xml);
            const view = viewer.getViews().find(v => v.type === 'decisionTable');
            await viewer.open(view);
        }""",
        xml,
    )
    page.wait_for_timeout(300)
    el = (
        page.query_selector(".tjs-table-container")
        or page.query_selector(".dmn-decision-table-container")
        or page.query_selector("#c")
    )
    png = el.screenshot(type="png", scale="device")
    html_out = page.inner_html("#c")
    return html_out, png


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright fehlt: pip install playwright && playwright install chromium", file=sys.stderr)
        return 1
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 2400, "height": 1000}, device_scale_factor=2)
        for bpmn in sorted(BPMN_DIR.glob("*.bpmn")):
            svg, png = render_bpmn(page, bpmn)
            (IMG_DIR / f"{bpmn.stem}.svg").write_text(svg, encoding="utf-8")
            (IMG_DIR / f"{bpmn.stem}.png").write_bytes(png)
            print("gerendert:", bpmn.stem)
        for dmn in sorted(BPMN_DIR.glob("*.dmn")):
            _, png = render_dmn(page, dmn)
            (IMG_DIR / f"{dmn.stem}.png").write_bytes(png)
            print("gerendert:", dmn.stem)
        browser.close()
    manifest = {p.name: p.stat().st_size for p in sorted(IMG_DIR.iterdir())}
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
