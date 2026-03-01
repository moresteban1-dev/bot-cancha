FROM python:3.11-slim
RUN pip install playwright && playwright install chromium && playwright install-deps
COPY . .
RUN pip install -r requirements.txt
CMD ["python", "bot_auto.py"]
