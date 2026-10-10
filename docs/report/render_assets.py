"""Render editable local SVGs and refuse clipped diagram labels."""

import asyncio
from pathlib import Path
from xml.etree import ElementTree as ET

from diagrams import build
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[2]
FOLDER = ROOT / "output/pdf/hypervaults-report/assets"
CHECK = """Array.from(document.querySelectorAll('[data-card]')).flatMap(g =>
  Array.from(g.querySelectorAll('text')).filter(t => {
    const b=t.getBBox();
    return b.x < Number(g.dataset.x)+8 || b.y < Number(g.dataset.y)+3 ||
           b.x+b.width > Number(g.dataset.x)+Number(g.dataset.width)-8 ||
           b.y+b.height > Number(g.dataset.y)+Number(g.dataset.height)-3;
  }).map(t=>t.textContent))"""


async def main():
    build(FOLDER)
    for logo in Path(__file__).with_name("logos").glob("*.svg"):
        root = ET.parse(logo).getroot()
        root.set("width", "96")
        root.set("height", "96")
        if logo.stem != "hypervaults":
            root.set("fill", "#BDA5EF")
        ET.ElementTree(root).write(FOLDER / logo.name, encoding="unicode")
    (FOLDER / "vault-art.svg").write_bytes(
        (ROOT / "webapp/static/vault.svg").read_bytes()
    )
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            page = await browser.new_page(
                viewport={"width": 1200, "height": 800}, device_scale_factor=2
            )
            for path in FOLDER.glob("*.svg"):
                await page.goto(path.resolve().as_uri())
                issues = await page.evaluate(CHECK)
                assert not issues, (path.name, issues)
                await page.locator("svg").screenshot(
                    path=str(path.with_suffix(".png")), omit_background=True
                )
        finally:
            await browser.close()
    print("PASS: all diagram labels fit; local vector assets rendered.")


if __name__ == "__main__":
    asyncio.run(main())
