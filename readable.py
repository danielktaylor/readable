#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "patchright",
#     "flask",
#     "boto3",
# ]
# ///
# First-time setup: uv run --with patchright python -m patchright install chromium
# Run server:       uv run readable.py
# Fetch article:    http://localhost:8080/fetch?url=https://...

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import urllib.request
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from flask import Flask, abort, redirect, request, send_file
from patchright.async_api import async_playwright

BASE_DIR = Path(__file__).parent
EXTENSION_DIR = BASE_DIR / "extensions" / "ublock-origin-lite"
BPC_DIR = BASE_DIR / "extensions" / "bypass-paywalls-clean"
READABILITY_JS = BASE_DIR / "extensions" / "Readability.js"
DEFUDDLE_JS = BASE_DIR / "extensions" / "defuddle.js"
ARTICLES_DIR = BASE_DIR / "articles"

READABILITY_URL = (
    "https://raw.githubusercontent.com/mozilla/readability/main/Readability.js"
)
DEFUDDLE_URL = "https://unpkg.com/defuddle@latest/dist/index.js"
BPC_URL = "https://gitflic.ru/project/magnolia1234/bpc_uploads/blob/raw?file=bypass-paywalls-chrome-clean-master.zip"

READER_TEMPLATE = (BASE_DIR / "article.html").read_text(encoding="utf-8")

R2_ACCOUNT_ID = os.environ.get("R2_ACCOUNT_ID")
R2_ACCESS_KEY_ID = os.environ.get("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY")
R2_BUCKET = os.environ.get("R2_BUCKET")
R2_PUBLIC_URL = os.environ.get("R2_PUBLIC_URL", "").rstrip("/")


def r2_client():
    if not all([R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET]):
        return None
    return boto3.client(
        "s3",
        endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        region_name="auto",
    )


def upload_to_r2(aid: str, path: Path) -> str | None:
    client = r2_client()
    if not client:
        return None
    key = f"articles/{aid}.html"
    try:
        client.upload_file(
            str(path), R2_BUCKET, key,
            ExtraArgs={"ContentType": "text/html; charset=utf-8"},
        )
        if R2_PUBLIC_URL:
            return f"{R2_PUBLIC_URL}/{key}"
        return f"https://{R2_BUCKET}.r2.dev/{key}"
    except (BotoCoreError, ClientError) as e:
        print(f"R2 upload failed: {e}")
        return None


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
    api = urllib.request.urlopen(
        "https://api.github.com/repos/uBlockOrigin/uBOL-home/releases/latest"
    )
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


def ensure_defuddle() -> Path:
    if not is_stale(DEFUDDLE_JS):
        return DEFUDDLE_JS
    print("Downloading defuddle...")
    DEFUDDLE_JS.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(DEFUDDLE_URL, DEFUDDLE_JS)
    print(f"Installed to {DEFUDDLE_JS}")
    return DEFUDDLE_JS


def article_id(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:7]


async def fetch_article(url: str, output: Path, debug: bool = False) -> bool:
    ublock = str(ensure_ublock())
    bpc = str(ensure_bpc())
    readability_src = ensure_readability().read_text(encoding="utf-8")
    defuddle_src = ensure_defuddle().read_text(encoding="utf-8")
    extensions = f"{ublock},{bpc}"
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir="",
            headless=not debug,
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

        if debug:
            input("Press Enter to inject parsers and extract article...")

        results = await page.evaluate(f"""() => {{
            {readability_src}
            {defuddle_src}
            const r = new Readability(document.cloneNode(true)).parse();
            const d = new Defuddle(document).parse();
            return {{
                readability: r ? {{ title: r.title, content: r.content }} : null,
                defuddle: d ? {{ title: d.title, content: d.content }} : null,
            }};
        }}""")
        await context.close()

    r = results.get("readability")
    d = results.get("defuddle")
    if r and d:
        article = r if len(r["content"]) >= len(d["content"]) else d
    else:
        article = r or d

    if article:
        output.write_text(
            READER_TEMPLATE.format(
                title=article["title"], content=article["content"], url=url
            ),
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

    r2_url = None
    if not output.exists():
        ARTICLES_DIR.mkdir(exist_ok=True)
        print(f"Fetching {url}")
        found = asyncio.run(
            fetch_article(
                url,
                output,
                debug=app.config.get("DEBUG_FETCH", False),
            )
        )
        if not found:
            abort(422, "Could not parse an article from this page.")
        r2_url = upload_to_r2(aid, output)
        if r2_url:
            print(f"Uploaded to {r2_url}")
            output.unlink()

    return redirect(r2_url or f"/article/{aid}")


@app.get("/article/<aid>")
def article(aid: str):
    path = ARTICLES_DIR / f"{aid}.html"
    if not path.exists():
        abort(404)
    return send_file(path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Run Chrome in non-headless mode and pause before injecting the parser",
    )
    args = parser.parse_args()

    app.config["DEBUG_FETCH"] = args.debug

    # Pre-download extensions before starting the server
    ensure_ublock()
    ensure_bpc()
    ensure_readability()
    ensure_defuddle()
    app.run(port=8080)
