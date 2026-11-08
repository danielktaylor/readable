#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "camoufox[geoip]",
#     "flask",
#     "boto3",
#     "stealth-requests",
# ]
# ///
# Run server:       uv run readable.py
# Fetch article:    http://localhost:8080/fetch?url=https://...

import argparse
import asyncio
import hashlib
import importlib.metadata
import io
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import boto3
import stealth_requests as requests
from botocore.exceptions import BotoCoreError, ClientError
from camoufox import DefaultAddons
from camoufox.async_api import AsyncCamoufox
from camoufox.pkgman import INSTALL_DIR as CAMOUFOX_CACHE
from flask import Flask, abort, redirect, request, send_file

BASE_DIR = Path(__file__).parent
EXTENSIONS_DIR = BASE_DIR / "extensions"
UBO_DIR = EXTENSIONS_DIR / "ublock-origin"
BPC_DIR = EXTENSIONS_DIR / "bypass-paywalls-clean"
READABILITY_JS = EXTENSIONS_DIR / "Readability.js"
DEFUDDLE_JS = EXTENSIONS_DIR / "defuddle.js"
ARTICLES_DIR = BASE_DIR / "articles"

CAMOUFOX_CONFIG = CAMOUFOX_CACHE / "config.json"
CAMOUFOX_UPDATE_MARKER = CAMOUFOX_CACHE / ".last_browser_update"
CAMOUFOX_PACKAGE_MARKER = CAMOUFOX_CACHE / ".last_package_version"
BROWSER_UPDATE_INTERVAL = timedelta(days=7)


def _browser_update_hour() -> int:
    try:
        return int(os.environ.get("CAMOUFOX_UPDATE_HOUR", "3")) % 24
    except ValueError:
        return 3


BROWSER_UPDATE_HOUR = _browser_update_hour()

UBO_URL = (
    "https://addons.mozilla.org/firefox/downloads/latest/ublock-origin/latest.xpi"
)
UBO_GITHUB_API = "https://api.github.com/repos/gorhill/uBlock/releases/latest"
BPC_URL = os.environ.get("BPC_URL") or (
    "https://gitflic.ru/project/magnolia1234/bpc_uploads/blob/raw"
    "?file=bypass-paywalls-firefox-clean-master.zip"
)
READABILITY_URL = (
    "https://raw.githubusercontent.com/mozilla/readability/main/Readability.js"
)
DEFUDDLE_URL = "https://unpkg.com/defuddle@latest/dist/index.js"

READER_TEMPLATE = (BASE_DIR / "article.html").read_text(encoding="utf-8")

R2_ACCOUNT_ID = os.environ.get("R2_ACCOUNT_ID")
R2_ACCESS_KEY_ID = os.environ.get("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY")
R2_BUCKET = os.environ.get("R2_BUCKET")
R2_PUBLIC_URL = os.environ.get("R2_PUBLIC_URL", "").rstrip("/")

UPDATE_INTERVAL = timedelta(weeks=1)
UPDATE_LOCK = threading.Lock()

# Markers that indicate a bot challenge / hard block rather than a paywall.
BLOCK_MARKERS = (
    "you have been blocked",
    "your access has been blocked",
    "access has been blocked",
    "access denied",
    "just a moment",
    "attention required",
    "verify you are human",
    "suspect that you're a robot",
    "are you a robot",
    "captcha-delivery.com",
    "unusual traffic",
    "enable javascript and cookies to continue",
)

# A block page can be long enough for Readability to "extract" it as an
# article. These titles are specific to challenge/block pages.
BLOCK_TITLES = (
    "just a moment",
    "access denied",
    "access has been blocked",
    "you have been blocked",
)

# Calibre's nytimes recipe waits out the anti-bot interstitial: the first
# request from an unscored browser gets a 403 while the page's scripts score
# the browser, then a reload normally gets the real page. The same browser
# context (cookies included) must be used for every attempt.
BOT_CHECK_RETRIES = 4
BOT_CHECK_DELAY_MS = 5_000
CHALLENGE_STATUSES = {403, 429}
CHALLENGE_MARKERS = (
    "just a moment",
    "attention required",
    "captcha-delivery.com",
    "datadome",
    "verify you are human",
    "px-captcha",
    "perimeterx",
    "unusual traffic",
    "enable javascript and cookies to continue",
)


class FetchBlocked(RuntimeError):
    """The page was a bot challenge / block page instead of an article."""


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
            str(path),
            R2_BUCKET,
            key,
            ExtraArgs={"ContentType": "text/html; charset=utf-8"},
        )
        if R2_PUBLIC_URL:
            return f"{R2_PUBLIC_URL}/{key}"
        return f"https://{R2_BUCKET}.r2.dev/{key}"
    except (BotoCoreError, ClientError) as e:
        print(f"R2 upload failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Extension / parser updates
#
# uBlock Origin and Bypass Paywalls Clean are Firefox addons. They are fetched
# from their upstream release channels and re-downloaded when older than a
# week. Updates are atomic: a failed download keeps the previous copy.
# ---------------------------------------------------------------------------


def _marker(path: Path) -> Path:
    """Timestamp file for an addon directory (or the file itself)."""
    return path if path.is_file() else path.parent / f".{path.name}.updated"


def is_stale(path: Path) -> bool:
    if not path.exists():
        return True
    marker = _marker(path)
    if not marker.exists():
        return True
    age = datetime.now() - datetime.fromtimestamp(marker.stat().st_mtime)
    return age > UPDATE_INTERVAL


def _mark_updated(path: Path) -> None:
    marker = _marker(path)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()


def _download(url: str, *, timeout: int = 120) -> bytes:
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return response.content


def _addon_root(path: Path) -> Path:
    """Find the directory containing manifest.json inside an extracted zip."""
    if (path / "manifest.json").is_file():
        return path
    for child in path.iterdir():
        if child.is_dir() and (child / "manifest.json").is_file():
            return child
    raise RuntimeError(f"No manifest.json found in {path}")


def _addon_manifest(path: Path) -> dict:
    return json.loads((path / "manifest.json").read_text(encoding="utf-8"))


def _addon_version(path: Path) -> str:
    return str(_addon_manifest(path).get("version", "unknown"))


def _install_dir(new_root: Path, target: Path) -> None:
    """Atomically replace target with new_root, restoring the old copy on error."""
    old = target.with_name(target.name + ".old")
    if old.exists():
        shutil.rmtree(old)
    if target.exists():
        target.rename(old)
    try:
        shutil.move(str(new_root), str(target))
    except Exception:
        if not target.exists() and old.exists():
            old.rename(target)
        raise
    if old.exists():
        shutil.rmtree(old)


def _extract_zip(data: bytes, dest: Path) -> None:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        zf.extractall(dest)


def ensure_ublock(force: bool = False) -> Path:
    if not force and not is_stale(UBO_DIR):
        return UBO_DIR

    print("Updating uBlock Origin...")
    tmp = EXTENSIONS_DIR / ".ublock-origin.tmp"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    try:
        try:
            data = _download(UBO_URL)
        except Exception as e:
            print(f"  AMO download failed ({e}); trying GitHub releases")
            release = requests.get(UBO_GITHUB_API, timeout=30).json()
            asset = next(
                a
                for a in release.get("assets", [])
                if a["name"].endswith(".firefox.xpi")
            )
            data = _download(asset["browser_download_url"])

        _extract_zip(data, tmp)
        root = _addon_root(tmp)
        manifest = _addon_manifest(root)
        if "uBlock Origin" not in manifest.get("name", ""):
            raise RuntimeError("Downloaded uBlock Origin addon has an unexpected manifest")

        _install_dir(root, UBO_DIR)
        _mark_updated(UBO_DIR)
        print(f"uBlock Origin {manifest.get('version', 'unknown')} ready")
        return UBO_DIR
    except Exception as e:
        if UBO_DIR.exists():
            print(f"uBlock Origin update failed ({e}); keeping existing copy")
            return UBO_DIR
        raise
    finally:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)


def ensure_bpc(force: bool = False) -> Path:
    if not force and not is_stale(BPC_DIR):
        return BPC_DIR

    print("Updating Bypass Paywalls Clean...")
    tmp = EXTENSIONS_DIR / ".bypass-paywalls.tmp"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    try:
        data = _download(BPC_URL)
        _extract_zip(data, tmp)
        root = _addon_root(tmp)
        manifest = _addon_manifest(root)
        if "Bypass Paywalls" not in manifest.get("name", ""):
            raise RuntimeError("Downloaded BPC addon has an unexpected manifest")

        _install_dir(root, BPC_DIR)
        _mark_updated(BPC_DIR)
        print(f"Bypass Paywalls Clean {manifest.get('version', 'unknown')} ready")
        return BPC_DIR
    except Exception as e:
        if BPC_DIR.exists():
            print(f"Bypass Paywalls Clean update failed ({e}); keeping existing copy")
            return BPC_DIR
        raise
    finally:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)


def _ensure_js_file(
    path: Path, url: str, label: str, min_bytes: int, force: bool = False
) -> Path:
    if not force and not is_stale(path):
        return path

    print(f"Updating {label}...")
    tmp = path.with_name(path.name + ".tmp")
    try:
        data = _download(url)
        if len(data) < min_bytes:
            raise RuntimeError(f"{label} download was only {len(data)} bytes")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(data)
        os.replace(tmp, path)
        _mark_updated(path)
        print(f"{label} ready")
        return path
    except Exception as e:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        if path.exists():
            print(f"{label} update failed ({e}); keeping existing copy")
            return path
        raise


def ensure_readability(force: bool = False) -> Path:
    return _ensure_js_file(READABILITY_JS, READABILITY_URL, "Readability.js", 10_000, force)


def ensure_defuddle(force: bool = False) -> Path:
    return _ensure_js_file(DEFUDDLE_JS, DEFUDDLE_URL, "defuddle", 10_000, force)


def ensure_all(force: bool = False) -> None:
    for label, func in (
        ("uBlock Origin", lambda: ensure_ublock(force)),
        ("Bypass Paywalls Clean", lambda: ensure_bpc(force)),
        ("Readability.js", lambda: ensure_readability(force)),
        ("defuddle", lambda: ensure_defuddle(force)),
    ):
        try:
            func()
        except Exception as e:
            print(f"{label} update failed: {e}")


def _log_versions() -> None:
    for path, label in (
        (UBO_DIR, "uBlock Origin"),
        (BPC_DIR, "Bypass Paywalls Clean"),
    ):
        try:
            if (path / "manifest.json").is_file():
                print(f"{label}: {_addon_version(path)}")
        except Exception as e:
            print(f"{label}: version check failed: {e}")
    print(f"Camoufox package: {_camoufox_package_version()}")
    print(f"Camoufox browser: {_browser_version()}")


def _start_extension_updater() -> None:
    if os.environ.get("DISABLE_EXTENSION_UPDATES") == "1":
        return

    def loop() -> None:
        while True:
            time.sleep(6 * 3600)
            try:
                with UPDATE_LOCK:
                    ensure_all()
            except Exception as e:
                print(f"Extension updater error: {e}")

    threading.Thread(target=loop, name="extension-updater", daemon=True).start()


# ---------------------------------------------------------------------------
# Camoufox browser updates
#
# The browser is not baked into the image. It lives in a Docker volume so it
# can be updated without rebuilding, and old versions are pruned so the cache
# does not grow forever.
# ---------------------------------------------------------------------------


def _run_camoufox(*args: str, timeout: int = 3600) -> None:
    print(f"[camoufox] {' '.join(args)}", flush=True)
    subprocess.run(
        [sys.executable, "-m", "camoufox", *args],
        check=True,
        timeout=timeout,
    )


def _camoufox_package_version() -> str:
    try:
        return importlib.metadata.version("camoufox")
    except Exception:
        return "unknown"


def _browser_ready() -> bool:
    try:
        config = json.loads(CAMOUFOX_CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return False
    active = config.get("active_version")
    if not isinstance(active, str) or not active:
        return False
    path = Path(active)
    if not path.is_absolute():
        path = CAMOUFOX_CACHE / path
    return path.is_dir()


def _browser_stale() -> bool:
    if not CAMOUFOX_UPDATE_MARKER.exists():
        return True
    age = datetime.now() - datetime.fromtimestamp(CAMOUFOX_UPDATE_MARKER.stat().st_mtime)
    return age > BROWSER_UPDATE_INTERVAL


def _browser_version() -> str:
    try:
        config = json.loads(CAMOUFOX_CONFIG.read_text(encoding="utf-8"))
        active = config.get("active_version")
        if isinstance(active, str) and active:
            return Path(active).name
    except Exception:
        pass
    return "unknown"


def _prune_browsers() -> None:
    """Keep only the active browser version; delete the rest."""
    try:
        config = json.loads(CAMOUFOX_CONFIG.read_text(encoding="utf-8"))
        active = config.get("active_version")
    except Exception:
        return
    if not isinstance(active, str) or not active:
        return

    active_path = Path(active)
    if not active_path.is_absolute():
        active_path = CAMOUFOX_CACHE / active_path
    try:
        active_path = active_path.resolve()
    except OSError:
        return

    browsers = CAMOUFOX_CACHE / "browsers"
    if not browsers.is_dir():
        return

    removed = 0
    for repo in browsers.iterdir():
        if not repo.is_dir():
            continue
        for version in repo.iterdir():
            if not version.is_dir():
                continue
            try:
                if version.resolve() == active_path:
                    continue
            except OSError:
                continue
            print(f"[camoufox] pruning old browser {version.name}", flush=True)
            shutil.rmtree(version, ignore_errors=True)
            removed += 1

    for repo in browsers.iterdir():
        if repo.is_dir() and repo.name != "official":
            try:
                if not any(repo.iterdir()):
                    repo.rmdir()
            except OSError:
                pass

    if removed:
        print(f"[camoufox] pruned {removed} old browser version(s)", flush=True)


def _update_camoufox(restart: bool = False) -> bool:
    """Follow the stable channel, install the latest browser, prune old ones.

    The caller must hold UPDATE_LOCK. The previous config is restored if the
    fetch fails. Camoufox may clear an incompatible cache first, so a failed
    update can still leave no browser; the next startup or scheduled check
    retries.
    """
    backup = CAMOUFOX_CONFIG.read_bytes() if CAMOUFOX_CONFIG.exists() else None
    try:
        _run_camoufox("sync", timeout=300)
        _run_camoufox("set", "official/stable", timeout=300)
        _run_camoufox("fetch", timeout=3600)
    except Exception as e:
        print(f"[camoufox] update failed: {e}", flush=True)
        if backup is not None:
            CAMOUFOX_CONFIG.write_bytes(backup)
            print("[camoufox] restored previous browser selection", flush=True)
        return _browser_ready()

    if not _browser_ready():
        print("[camoufox] update finished but no browser is active", flush=True)
        if backup is not None:
            CAMOUFOX_CONFIG.write_bytes(backup)
        return False

    _prune_browsers()
    CAMOUFOX_UPDATE_MARKER.parent.mkdir(parents=True, exist_ok=True)
    CAMOUFOX_UPDATE_MARKER.touch()
    CAMOUFOX_PACKAGE_MARKER.write_text(
        _camoufox_package_version(), encoding="utf-8"
    )
    print(f"[camoufox] browser ready: {_browser_version()}", flush=True)

    if restart and os.environ.get("CAMOUFOX_RESTART_AFTER_UPDATE") == "1":
        print("[camoufox] restarting to use the updated browser", flush=True)
        os._exit(0)
    return True


def ensure_camoufox() -> bool:
    """Ensure a usable browser exists, updating it if it is stale.

    A package change (image rebuild) also triggers a fetch, so the browser is
    re-checked for compatibility with the new launcher.
    """
    package_version = _camoufox_package_version()
    try:
        last_package_version = CAMOUFOX_PACKAGE_MARKER.read_text(encoding="utf-8").strip()
    except Exception:
        last_package_version = ""

    if (
        _browser_ready()
        and CAMOUFOX_UPDATE_MARKER.exists()
        and not _browser_stale()
        and last_package_version == package_version
    ):
        return True

    if _browser_ready():
        print(
            "[camoufox] browser is stale, the package changed, or the update "
            "timestamp is missing",
            flush=True,
        )
    return _update_camoufox(restart=False)


def _start_browser_updater() -> None:
    if os.environ.get("DISABLE_BROWSER_UPDATES") == "1":
        return

    def loop() -> None:
        while True:
            now = datetime.now()
            target = now.replace(
                hour=BROWSER_UPDATE_HOUR, minute=0, second=0, microsecond=0
            )
            if target <= now:
                target += timedelta(days=1)
            time.sleep(max(1.0, (target - now).total_seconds()))

            if _browser_ready() and not _browser_stale():
                continue
            print("[camoufox] starting scheduled browser update", flush=True)
            with UPDATE_LOCK:
                _update_camoufox(restart=True)

    threading.Thread(target=loop, name="camoufox-updater", daemon=True).start()


# ---------------------------------------------------------------------------
# Article fetching
# ---------------------------------------------------------------------------


def article_id(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:7]


def _launch_kwargs(ublock: Path, bpc: Path, debug: bool) -> dict:
    if debug:
        # A headed window only makes sense on a real display. On a headless
        # Linux server, use the virtual framebuffer and save a screenshot.
        headless = "virtual" if sys.platform.startswith("linux") and not os.environ.get("DISPLAY") else False
    elif sys.platform.startswith("linux"):
        headless = "virtual"
    else:
        headless = True

    if sys.platform.startswith("linux"):
        target_os = "linux"
    elif sys.platform == "darwin":
        target_os = "macos"
    else:
        target_os = "windows"

    options = {
        "headless": headless,
        "os": target_os,
        "locale": os.environ.get("CAMOUFOX_LOCALE", "en-US"),
        "geoip": True,
        "humanize": True,
        "addons": [str(ublock), str(bpc)],
        "exclude_addons": [DefaultAddons.UBO],
    }

    proxy_server = os.environ.get("PROXY_SERVER")
    if proxy_server:
        proxy = {"server": proxy_server}
        if os.environ.get("PROXY_USERNAME"):
            proxy["username"] = os.environ["PROXY_USERNAME"]
        if os.environ.get("PROXY_PASSWORD"):
            proxy["password"] = os.environ["PROXY_PASSWORD"]
        options["proxy"] = proxy

    return options


async def _settle(page) -> None:
    try:
        await page.wait_for_load_state("load", timeout=30_000)
    except Exception:
        pass
    await page.wait_for_timeout(1500)


def _is_challenge(response, html: str) -> bool:
    if response is not None and response.status in CHALLENGE_STATUSES:
        return True
    low = html.lower()
    return any(marker in low for marker in CHALLENGE_MARKERS)


async def _load_with_bot_check(page, url: str):
    """Load a URL, retrying the anti-bot interstitial in the same context.

    Mirrors calibre.web.automate.bot_check: 4 total attempts, 5 seconds apart.
    """
    response = await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    for attempt in range(1, BOT_CHECK_RETRIES + 1):
        await _settle(page)
        html = await page.content()
        if not _is_challenge(response, html):
            return response
        if attempt == BOT_CHECK_RETRIES:
            print(
                f"Bot check still present after {BOT_CHECK_RETRIES} attempts",
                flush=True,
            )
            return response
        status = response.status if response is not None else "?"
        print(
            f"Bot check (status {status}); waiting 5s before retry "
            f"{attempt}/{BOT_CHECK_RETRIES - 1}",
            flush=True,
        )
        await page.wait_for_timeout(BOT_CHECK_DELAY_MS)
        response = await page.reload(wait_until="domcontentloaded", timeout=60_000)
    return response


async def _extract(page, readability_src: str, defuddle_src: str) -> dict:
    return await page.evaluate(
        f"""() => {{
            {readability_src}
            {defuddle_src}
            const r = new Readability(document.cloneNode(true)).parse();
            const d = new Defuddle(document).parse();
            return {{
                readability: r ? {{ title: r.title, content: r.content }} : null,
                defuddle: d ? {{ title: d.title, content: d.content }} : null,
            }};
        }}"""
    )


def _best_article(results: dict) -> dict | None:
    candidates = []
    for key in ("readability", "defuddle"):
        item = results.get(key)
        if item and item.get("content"):
            candidates.append(item)
    if not candidates:
        return None
    article = max(candidates, key=lambda item: len(item["content"]))
    return article if len(article["content"]) >= 200 else None


def _looks_blocked(text: str) -> bool:
    low = text.lower()
    return any(marker in low for marker in BLOCK_MARKERS)


def _article_is_blocked(article: dict) -> bool:
    title = (article.get("title") or "").strip().lower()
    if title in {"nytimes.com", "the atlantic"}:
        return True
    if any(marker in title for marker in BLOCK_TITLES):
        return True
    content = (article.get("content") or "")[:5000].lower()
    return "captcha-delivery.com" in content or "suspect that you're a robot" in content


async def fetch_article(url: str, output: Path, debug: bool = False) -> bool:
    # Hold the update lock through the launch: replacing an addon directory
    # while Firefox is loading it would point the browser at a missing path.
    with UPDATE_LOCK:
        ublock = ensure_ublock()
        bpc = ensure_bpc()
        readability_src = ensure_readability().read_text(encoding="utf-8")
        defuddle_src = ensure_defuddle().read_text(encoding="utf-8")

        if not (ublock / "manifest.json").is_file() or not (bpc / "manifest.json").is_file():
            raise RuntimeError("Browser extensions are missing; check the server logs")
        if not _browser_ready():
            raise RuntimeError(
                "Camoufox browser is not installed; check the server logs and retry"
            )

        async with AsyncCamoufox(**_launch_kwargs(ublock, bpc, debug)) as browser:
            page = await browser.new_page()
            try:
                response = await _load_with_bot_check(page, url)

                if debug:
                    screenshot = BASE_DIR / "debug.png"
                    await page.screenshot(path=str(screenshot), full_page=False)
                    print(f"Debug screenshot: {screenshot}")
                    input("Press Enter to inject parsers and extract the article...")

                article = _best_article(
                    await _extract(page, readability_src, defuddle_src)
                )
                if article is not None and _article_is_blocked(article):
                    raise FetchBlocked("Blocked by bot detection")

                if article is None:
                    html = await page.content()
                    if _is_challenge(response, html):
                        # The bot-check loop already made all its attempts.
                        raise FetchBlocked("Blocked by bot detection")
                    # A normal 200 page that did not yield an article yet:
                    # give paywall/extension timing one more load.
                    print("No article on first load; reloading once...", flush=True)
                    response = await page.reload(
                        wait_until="domcontentloaded", timeout=60_000
                    )
                    await _settle(page)
                    article = _best_article(
                        await _extract(page, readability_src, defuddle_src)
                    )
                    if article is not None and _article_is_blocked(article):
                        raise FetchBlocked("Blocked by bot detection")

                if article is None:
                    html = await page.content()
                    if _looks_blocked(html):
                        raise FetchBlocked("Blocked by bot detection")
                    return False

                output.write_text(
                    READER_TEMPLATE.format(
                        title=article["title"], content=article["content"], url=url
                    ),
                    encoding="utf-8",
                )
                return True
            finally:
                await page.close()


app = Flask(__name__)


@app.get("/")
def index():
    return send_file(BASE_DIR / "index.html")


@app.get("/fetch")
def fetch():
    url = request.args.get("url")
    if not url:
        abort(400, "Missing ?url= parameter")
    if not url.startswith(("http://", "https://")):
        abort(400, "URL must use http or https")

    aid = article_id(url)
    output = ARTICLES_DIR / f"{aid}.html"
    r2_link = ARTICLES_DIR / f"{aid}.url"

    r2_url = None
    if not output.exists():
        ARTICLES_DIR.mkdir(exist_ok=True)
        # With R2 configured the local HTML is deleted after upload. Remember
        # the public URL so a repeat request does not re-fetch the article.
        if r2_link.exists():
            r2_url = r2_link.read_text(encoding="utf-8").strip() or None
        if r2_url is None:
            print(f"Fetching {url}")
            try:
                found = asyncio.run(
                    fetch_article(
                        url,
                        output,
                        debug=app.config.get("DEBUG_FETCH", False),
                    )
                )
            except FetchBlocked as e:
                abort(502, str(e))
            except Exception as e:
                print(f"Fetch failed for {url}: {e}")
                abort(502, "Article fetch failed; check the server logs.")
            if not found:
                abort(422, "Could not parse an article from this page.")
            r2_url = upload_to_r2(aid, output)
            if r2_url:
                print(f"Uploaded to {r2_url}")
                r2_link.write_text(r2_url, encoding="utf-8")
                output.unlink()

    return redirect(r2_url or f"/article/{aid}")


@app.get("/article/<aid>")
def article(aid: str):
    if not aid.isalnum():
        abort(400)
    path = ARTICLES_DIR / f"{aid}.html"
    if not path.exists():
        abort(404)
    return send_file(path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Open a visible browser (or save a debug screenshot on a headless server) and pause before extraction",
    )
    args = parser.parse_args()

    app.config["DEBUG_FETCH"] = args.debug

    print("Checking Camoufox browser...")
    with UPDATE_LOCK:
        browser_ok = ensure_camoufox()
    if not browser_ok:
        print(
            "Camoufox browser is not installed; /fetch will fail until it is "
            "available. Check the logs and restart the container.",
            flush=True,
        )

    print("Checking browser extensions and parsers...")
    with UPDATE_LOCK:
        ensure_all()
    _log_versions()
    _start_extension_updater()
    _start_browser_updater()

    app.run(host="0.0.0.0", port=8080)
