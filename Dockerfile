# DOCKERFILE QUE FUNCIONA EN RAILWAY MARZO 2026 (100% comprobado)
FROM python:3.11-slim-bookworm

# Variables esenciales
ENV DEBIAN_FRONTEND=noninteractive \
    TZ=America/Santiago \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PYTHONUNBUFFERED=1

# Instalar TODAS las dependencias que Chromium necesita en 2026
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libatspi2.0-0 \
    libc6 \
    libcairo2 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libexpat1 \
    libfontconfig1 \
    libgbm1 \
    libgcc1 \
    libglib2.0-0 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libpango-1.0-0 \
    libstdc++6 \
    libx11-6 \
    libx11-xcb1 \
    libxcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libxshmfence1 \
    wget \
    xdg-utils \
    && rm -rf /var/lib/apt/lists/*

# Crear directorio para Playwright
RUN mkdir -p /ms-playwright

# Instalar dependencias Python
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Instalar SOLO Chromium (sin --with-deps que está roto)
RUN playwright install chromium

# Copiar código
COPY . .

# Ejecutar como root (en Railway funciona mejor así para Playwright)
# USER nobody → QUITAR ESTA LÍNEA (es la que mata a muchos)

CMD ["python", "bot_auto.py"]
