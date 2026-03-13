FROM python:3.13-slim

# Dependências de sistema para o Chromium (Debian Bookworm)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 \
    libxfixes3 libxrandr2 libgbm1 libasound2 libpango-1.0-0 \
    libcairo2 libxshmfence1 libx11-6 libxext6 libxcb1 \
    libx11-xcb1 libxcursor1 libxi6 libxtst6 \
    wget ca-certificates fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Mesmo path que o Railway seta em runtime via PLAYWRIGHT_BROWSERS_PATH
ENV PLAYWRIGHT_BROWSERS_PATH=/app/pw-browsers
RUN playwright install chromium

COPY . .

ENV PORT=8000
EXPOSE 8000

CMD uvicorn main:app --host 0.0.0.0 --port $PORT
