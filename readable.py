#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "patchright",
#     "flask",
# ]
# ///
# First-time setup: uv run --with patchright python -m patchright install chromium
# Run server:       uv run readable.py
# Fetch article:    http://localhost:8080/fetch?url=https://...

import os
import sys
import json
import asyncio
import hashlib
import shutil
import zipfile
import urllib.request
from pathlib import Path
from datetime import datetime, timedelta
from flask import Flask, redirect, request, send_file, abort
from patchright.async_api import async_playwright

BASE_DIR = Path(__file__).parent
EXTENSION_DIR = BASE_DIR / "extensions" / "ublock-origin-lite"
BPC_DIR       = BASE_DIR / "extensions" / "bypass-paywalls-clean"
READABILITY_JS = BASE_DIR / "extensions" / "Readability.js"
ARTICLES_DIR  = BASE_DIR / "articles"

READABILITY_URL = "https://raw.githubusercontent.com/mozilla/readability/main/Readability.js"
BPC_URL = "https://gitflic.ru/project/magnolia1234/bpc_uploads/blob/raw?file=bypass-paywalls-chrome-clean-master.zip"

READER_TEMPLATE = (BASE_DIR / "reader.html").read_text(encoding="utf-8")


UPDATE_INTERVAL = timedelta(weeks=1)


def is_stale(path: Path) -> bool:
    if not path.exists():
        return True
    age = datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
    return age > UPDATE_INTERVAL


def ensure_ublock() -> Path:
    if not is_stale(EXTENSION_DIR):
        return EXTENSION_DIR
    print("Downloading uBlock Origin Lite...")
    if EXTENSION_DIR.exists():
        shutil.rmtree(EXTENSION_DIR)
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
    if not is_stale(BPC_DIR):
        return BPC_DIR
    print("Downloading Bypass Paywalls Clean...")
    if BPC_DIR.exists():
        shutil.rmtree(BPC_DIR)
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
    if not is_stale(READABILITY_JS):
        return READABILITY_JS
    print("Downloading Readability.js...")
    READABILITY_JS.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(READABILITY_URL, READABILITY_JS)
    print(f"Installed to {READABILITY_JS}")
    return READABILITY_JS


def article_id(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:7]


async def fetch_article(url: str, output: Path) -> bool:
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
                *(["--no-sandbox"] if os.environ.get("NO_SANDBOX") else []),
            ],
        )
        page = await context.new_page()
        await page.goto(url, wait_until="load", timeout=60000)
        await page.reload(wait_until="load", timeout=60000)

        readability_src = readability.read_text(encoding="utf-8")
        article = await page.evaluate(f"""() => {{
            {readability_src}
            const article = new Readability(document.cloneNode(true)).parse();
            return article ? {{ title: article.title, content: article.content }} : null;
        }}""")
        await context.close()

    if article:
        output.write_text(
            READER_TEMPLATE.format(title=article["title"], content=article["content"], url=url),
            encoding="utf-8",
        )
        return True
    return False


app = Flask(__name__)


@app.get("/")
def index():
    return send_file(BASE_DIR / "index.html")


@app.get("/fetch")
def fetch():
    url = request.args.get("url")
    if not url:
        abort(400, "Missing ?url= parameter")

    aid = article_id(url)
    output = ARTICLES_DIR / f"{aid}.html"

    if not output.exists():
        ARTICLES_DIR.mkdir(exist_ok=True)
        print(f"Fetching {url}")
        found = asyncio.run(fetch_article(url, output))
        if not found:
            abort(422, "Readability could not parse an article from this page.")

    return redirect(f"/article/{aid}")


@app.get("/article/<aid>")
def article(aid: str):
    path = ARTICLES_DIR / f"{aid}.html"
    if not path.exists():
        abort(404)
    return send_file(path)


if __name__ == "__main__":
    # Pre-download extensions before starting the server
    ensure_ublock()
    ensure_bpc()
    ensure_readability()
    app.run(port=8080)
