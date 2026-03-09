# readable

A Flask server that fetches articles from behind paywalls and returns clean, readable HTML using [Readability.js](https://github.com/mozilla/readability).

## How it works

1. Launches a headless Chromium browser with two extensions loaded:
   - **[Bypass Paywalls Clean](https://gitflic.ru/project/magnolia1234/bypass-paywalls-chrome-clean)** — bypasses paywalls on supported news sites
   - **[uBlock Origin Lite](https://github.com/uBlockOrigin/uBOL-home)** — blocks ads and trackers
2. Navigates to the requested URL and waits for the page to fully load
3. Runs Readability.js to extract the article content
4. Returns a persistent URL to the cleaned-up HTML

Extensions and Readability.js are downloaded automatically on first run and updated weekly.

## Usage

### Local

```bash
# First-time setup
uv run --with patchright python -m patchright install chromium

# Start the server
uv run readable.py

# Debug mode: opens a visible Chrome window and pauses before extracting
uv run readable.py --debug
```

### Docker

```bash
docker compose up --build
```

## API

### `GET /fetch?url=<article-url>`

Fetches and parses the article at the given URL. On success, redirects to a persistent article URL. Cached articles are served immediately without re-fetching.

```
http://localhost:8080/fetch?url=https://www.theatlantic.com/ideas/2026/03/...
```

### `GET /article/<id>`

Returns the cached reader-view HTML for a previously fetched article.

```
http://localhost:8080/article/a73b30c
```

## Supported sites

Any site supported by [Bypass Paywalls Clean](https://gitflic.ru/project/magnolia1234/bypass-paywalls-chrome-clean). This includes the New York Times, The Atlantic, The Washington Post, Financial Times, and many others.
