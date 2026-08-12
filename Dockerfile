FROM python:3.11-slim

WORKDIR /app

# Deno is required for yt-dlp YouTube JS challenges (EJS).
ARG DENO_VERSION=2.4.3

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev ffmpeg curl unzip ca-certificates \
    && curl -fsSL "https://github.com/denoland/deno/releases/download/v${DENO_VERSION}/deno-x86_64-unknown-linux-gnu.zip" \
      -o /tmp/deno.zip \
    && unzip -o /tmp/deno.zip -d /usr/local/bin \
    && chmod +x /usr/local/bin/deno \
    && rm /tmp/deno.zip \
    && apt-get purge -y curl unzip \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1
CMD ["python", "bot.py"]
