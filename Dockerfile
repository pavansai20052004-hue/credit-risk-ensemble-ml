FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=10000 \
    DATABASE_PATH=/data/app.db

WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app app

COPY requirements.txt .
COPY requirements-deploy.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements-deploy.txt

COPY . .

RUN mkdir -p /data \
    && chown -R app:app /app /data

USER app

EXPOSE 10000

CMD ["gunicorn", "-c", "gunicorn.conf.py", "app:app"]
