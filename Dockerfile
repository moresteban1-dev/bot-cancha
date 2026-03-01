# Usar imagen oficial de Playwright (ya trae Chromium y Python)
FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

# Configurar directorio de trabajo
WORKDIR /app

# Copiar archivos de requerimientos
COPY requirements.txt .

# Instalar dependencias de Python
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el resto del código
COPY . .

# Comando para iniciar el bot
CMD ["python", "bot_auto.py"]
