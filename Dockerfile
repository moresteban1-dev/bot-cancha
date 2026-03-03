# ======== DOCKERFILE FUNCIONAL 2025-2026 ========
FROM python:3.11-slim-bookworm

# Variables de entorno
ENV TZ=America/Santiago \
    DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Actualizar e instalar dependencias mínimas + fix fonts
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        wget \
        gnupg \
        libglib2.0-0 \
        libnss3 \
        libnspr4 \
        libdbus-1-3 \
        libatk1.0-0 \
        libatk-bridge2.0-0 \
        libcups2 \
        libdrm2 \
        libxkbcommon0 \
        libatspi2.0-0 \
        libxcomposite1 \
        libxdamage1 \
        libxrandr2 \
        libgbm1 \
        libpango-1.0-0 \
        libcairo2 \
        libasound2 \
        fonts-liberation \
        fonts-noto-color-emoji \
        libu2f-udev \
        libvulkan1 && \
    # Fix fonts que antes rompían todo
    fc-cache -fv && \
    rm -rf /var/lib/apt/lists/*

# Crear usuario no-root (recomendado)
RUN useradd -m pwuser

# Workdir
WORKDIR /app

# Copiar requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Instalar Playwright + Chromium (SIN --with-deps que está roto en Trixie)
RUN playwright install chromium --with-deps || \
    (echo "Fallback: instalando solo chromium sin deps del sistema" && \
     mkdir -p /ms-playwright && \
     PLAYWRIGHT_BROWSERS_PATH=/ms-playwright playwright install chromium)

# Copiar código
COPY . .

# Cambiar a usuario no-root
USER pwuser

CMD ["python", "bot_auto.py"]
