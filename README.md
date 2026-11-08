# Readable

A simple web service to fetch web articles and reformat them to just the article text for easier reading. Optionally uploads the result to Cloudflare R2 (there's a generous free tier!).

<img width="1414" height="282" alt="screenshot" src="https://github.com/user-attachments/assets/f32265df-8973-4ad4-ba28-8977e27dcdaa" />

Under the hood it uses:

* [Camoufox](https://camoufox.com/)
* [Readability.js](https://github.com/mozilla/readability)
* [Defuddle](https://github.com/kepano/defuddle)
* [uBlock Origin](https://github.com/gorhill/uBlock)
* [Bypass Paywalls Clean](https://gitflic.ru/project/magnolia1234/bypass-paywalls-firefox-clean)

All of them are downloaded automatically inside the container and refreshed weekly.

## Usage

### Local

```bash
# Start the server. The first run downloads the Camoufox browser.
uv run readable.py

# Debug mode: opens a visible browser (or saves debug.png on a headless install)
uv run readable.py --debug
```

On Linux, Camoufox's virtual display also needs Xvfb and util-linux (`setpriv`).

```bash
sudo apt-get install -y xvfb util-linux
```

### Docker

```bash
cp .env.sample .env
# edit .env and fill in your Cloudflare R2 credentials (optional)
docker compose up -d --build
```

## Version updates

Camoufox and the browser extensions are installed on first run and updated weekly.

**Python package / base image** updates still come from rebuilding the image:

  ```bash
  docker compose build --pull
  docker compose up -d
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
