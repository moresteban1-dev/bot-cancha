FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --upgrade pip
RUN pip install python-telegram-bot==20.7 playwright aiohttp

COPY . .

RUN playwright install chromium
RUN playwright install-deps chromium

CMD ["python", "bot_auto.py"]
