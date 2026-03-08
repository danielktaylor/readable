FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY readable.py .

RUN uv pip install --system patchright flask && \
    python -m patchright install --with-deps chromium

EXPOSE 8080
CMD ["python", "readable.py"]
