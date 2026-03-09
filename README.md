# readable

A Flask server that fetches articles from behind paywalls and returns clean, readable HTML.

## How it works

1. Launches a headless Chromium browser with two extensions loaded:
   - **[Bypass Paywalls Clean](https://gitflic.ru/project/magnolia1234/bypass-paywalls-chrome-clean)** — bypasses paywalls on supported news sites
   - **[uBlock Origin Lite](https://github.com/uBlockOrigin/uBOL-home)** — blocks ads and trackers
2. Navigates to the requested URL and waits for the page to fully load
3. Runs both [Readability.js](https://github.com/mozilla/readability) and [Defuddle](https://github.com/kepano/defuddle) to extract the article content, then saves whichever result is larger
4. Returns a persistent URL to the cleaned-up HTML

Extensions, Readability.js, and Defuddle are downloaded automatically on first run and updated weekly.

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

## Cloudflare R2 (persistent public URLs)

Articles can be automatically uploaded to Cloudflare R2 so each article gets a permanent public URL. The free tier includes 10 GB storage and 10M reads/month.

### Setup

1. In the [Cloudflare dashboard](https://dash.cloudflare.com), go to **R2** and create a bucket
2. Under **Manage R2 API Tokens**, create a token with **Object Read & Write** permission
3. On your bucket's **Settings** tab, enable **R2.dev subdomain** (or connect a custom domain) to get a public URL
4. Set these environment variables before starting the server:

```bash
export R2_ACCOUNT_ID=your_account_id      # Cloudflare account ID (found in the dashboard sidebar)
export R2_ACCESS_KEY_ID=your_key_id       # R2 API token key ID
export R2_SECRET_ACCESS_KEY=your_secret   # R2 API token secret
export R2_BUCKET=your_bucket_name
export R2_PUBLIC_URL=https://pub-xxx.r2.dev  # optional: defaults to https://<bucket>.r2.dev
```

When R2 is configured, the server redirects to the public R2 URL after fetching. Without R2, articles are served locally at `/article/<id>`.

## Supported sites

Any site supported by [Bypass Paywalls Clean](https://gitflic.ru/project/magnolia1234/bypass-paywalls-chrome-clean). This includes the New York Times, The Atlantic, The Washington Post, Financial Times, and many others.
