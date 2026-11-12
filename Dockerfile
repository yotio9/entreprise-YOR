FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

RUN python -m playwright install --with-deps chromium

COPY . .
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/staticfiles /app/media /app/.private_backups \
    && chown -R appuser:appuser /app /ms-playwright
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD ["python", "manage.py", "check_database"]
CMD ["gunicorn", "yorwani.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "60"]
