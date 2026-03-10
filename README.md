# Readable

A simple web service to fetch web articles and reformat them to just the article text for easier reading. Optionally uploads the result to Cloudflare R2.

<img width="1414" height="282" alt="screenshot" src="https://github.com/user-attachments/assets/f32265df-8973-4ad4-ba28-8977e27dcdaa" />


Under the hood it uses:

* Headless Chromium
* [Readability.js](https://github.com/mozilla/readability)
* [Defuddle](https://github.com/kepano/defuddle)
* [uBlock Origin Lite](https://github.com/uBlockOrigin/uBOL-home)
* [Bypass Paywalls Clean](https://gitflic.ru/project/magnolia1234/bypass-paywalls-chrome-clean)

The Chrome extensions, readability.js, and defuddle are downloaded automatically on first run and updated weekly.

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
cp .env.sample .env
# edit .env and fill in your Cloudflare R2 credentials
docker compose up --build
```

## Cloudflare R2 (persistent public URLs)

Articles can be automatically uploaded to Cloudflare R2 so each article gets a permanent public URL -- useful for passing into a read-it-later app.

### Setup

```bash
cp .env.sample .env
```

```ini
R2_ACCOUNT_ID=your_account_id
R2_ACCESS_KEY_ID=your_key_id
R2_SECRET_ACCESS_KEY=your_secret
R2_BUCKET=your_bucket_name
R2_PUBLIC_URL=https://example.com  # optional: defaults to https://<bucket>.r2.dev
```
