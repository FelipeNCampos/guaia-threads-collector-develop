FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

LABEL maintainer="threads-scraper" \
      description="Threads post collector using Playwright + PostgreSQL"

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data && chmod 777 /app/data

ENV HEADLESS=true

CMD ["python", "threads_api_feeder.py"]
