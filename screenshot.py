#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "patchright",
# ]
# ///
# First-time setup: uv run --with patchright python -m patchright install chromium

import sys
import json
import asyncio
import zipfile
import urllib.request
from pathlib import Path
from patchright.async_api import async_playwright

EXTENSION_DIR = Path(__file__).parent / "extensions" / "ublock-origin-lite"
BPC_DIR = Path(__file__).parent / "extensions" / "bypass-paywalls-clean"
READABILITY_JS = Path(__file__).parent / "extensions" / "Readability.js"

READABILITY_URL = "https://raw.githubusercontent.com/mozilla/readability/main/Readability.js"
BPC_URL = "https://gitflic.ru/project/magnolia1234/bpc_uploads/blob/raw?file=bypass-paywalls-chrome-clean-master.zip"

READER_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>{title}</title>
  <style>
    body {{ max-width: 700px; margin: 40px auto; font-family: Georgia, serif;
           font-size: 1.1em; line-height: 1.7; color: #222; padding: 0 1em; }}
    h1 {{ font-size: 1.8em; line-height: 1.3; }}
    img {{ max-width: 100%; }}
  </style>
</head>
<body>
  <h1>{title}</h1>
  {content}
</body>
</html>"""


def ensure_ublock() -> Path:
    if EXTENSION_DIR.exists():
        return EXTENSION_DIR

    print("Downloading uBlock Origin Lite...")
    api = urllib.request.urlopen("https://api.github.com/repos/uBlockOrigin/uBOL-home/releases/latest")
    release = json.load(api)
    asset = next(a for a in release["assets"] if "chromium" in a["name"])
    zip_path = EXTENSION_DIR.parent / asset["name"]
    EXTENSION_DIR.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(asset["browser_download_url"], zip_path)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(EXTENSION_DIR)
    zip_path.unlink()
    print(f"Installed to {EXTENSION_DIR}")
    return EXTENSION_DIR


def ensure_bpc() -> Path:
    if BPC_DIR.exists():
        return BPC_DIR

    print("Downloading Bypass Paywalls Clean...")
    BPC_DIR.parent.mkdir(parents=True, exist_ok=True)
    zip_path = BPC_DIR.parent / "bypass-paywalls-clean.zip"
    urllib.request.urlretrieve(BPC_URL, zip_path)
    with zipfile.ZipFile(zip_path) as zf:
        top = zf.namelist()[0].split("/")[0]
        zf.extractall(BPC_DIR.parent)
        (BPC_DIR.parent / top).rename(BPC_DIR)
    zip_path.unlink()
    print(f"Installed to {BPC_DIR}")
    return BPC_DIR


def ensure_readability() -> Path:
    if not READABILITY_JS.exists():
        print("Downloading Readability.js...")
        READABILITY_JS.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(READABILITY_URL, READABILITY_JS)
        print(f"Installed to {READABILITY_JS}")
    return READABILITY_JS


async def fetch_article(url: str, output: str = "article.html") -> None:
    ublock = str(ensure_ublock())
    bpc = str(ensure_bpc())
    readability = ensure_readability()
    extensions = f"{ublock},{bpc}"
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir="",
            # headless=False,
            no_viewport=True,
            args=[
                f"--disable-extensions-except={extensions}",
                f"--load-extension={extensions}",
            ],
        )
        page = await context.new_page()
        await page.goto(url, wait_until="load", timeout=60000)

        readability_src = readability.read_text(encoding="utf-8")
        article = await page.evaluate(f"""() => {{
            {readability_src}
            const article = new Readability(document.cloneNode(true)).parse();
            return article ? {{ title: article.title, content: article.content }} : null;
        }}""")
        await context.close()

    if article:
        Path(output).write_text(
            READER_TEMPLATE.format(title=article["title"], content=article["content"]),
            encoding="utf-8",
        )
        print(f"Reader view saved to {output}")
    else:
        print("Readability could not parse an article from this page.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: uv run screenshot.py <url> [output.html]")
        sys.exit(1)

    url = sys.argv[1]
    output = sys.argv[2] if len(sys.argv) > 2 else "article.html"
    asyncio.run(fetch_article(url, output))
