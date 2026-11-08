FROM python:3.13-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/camoufox \
    CAMOUFOX_VIRTUAL_DISPLAY_SIZE=1920x1080x24 \
    CAMOUFOX_RESTART_AFTER_UPDATE=1

# Firefox/Camoufox runtime + Xvfb.
# Mesa's software GL is installed because there is no GPU in the
# container (without it WebGL is null, which is a headless tell)
# tzdata is for the weekly browser-update schedule (TZ).
RUN apt-get update && apt-get install -y --no-install-recommends \
        tzdata \
        xvfb \
        util-linux \
        libgtk-3-0 \
        libasound2 \
        libx11-xcb1 \
        libxcomposite1 \
        libxdamage1 \
        libxrandr2 \
        libgbm1 \
        libpango-1.0-0 \
        libcairo2 \
        libatk1.0-0 \
        libatk-bridge2.0-0 \
        libxshmfence1 \
        libdbus-glib-1-2 \
        libxt6 \
        libxext6 \
        libxfixes3 \
        libxrender1 \
        libfontconfig1 \
        fonts-liberation \
        libnss3 \
        libnspr4 \
        libgl1 \
        libglx-mesa0 \
        libgl1-mesa-dri \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -d /home/camoufox -s /bin/bash camoufox
USER camoufox
WORKDIR /app

RUN pip install --user "camoufox[geoip]"

RUN pip install --user flask boto3 stealth-requests

RUN mkdir -p /home/camoufox/.cache/camoufox

USER root
COPY --chown=camoufox:camoufox readable.py article.html index.html ./
RUN mkdir -p /app/articles /app/extensions \
    && chown -R camoufox:camoufox /app
USER camoufox

EXPOSE 8080
CMD ["python", "readable.py"]
