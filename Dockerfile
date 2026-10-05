FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY pyproject.toml README.md ./
COPY quantpilot ./quantpilot
RUN pip install --no-cache-dir ".[platform]"
COPY alembic.ini ./
COPY migrations ./migrations
RUN useradd --create-home --uid 10001 quantpilot && mkdir -p /app/storage && chown -R quantpilot:quantpilot /app
USER quantpilot
CMD ["uvicorn", "quantpilot.platform.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
