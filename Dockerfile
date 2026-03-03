FROM python:3.11-slim

ENV TZ=America/Santiago
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Instalar Chromium + dependencias del sistema
RUN playwright install --with-deps chromium

COPY . .

CMD ["python", "bot_auto.py"]
