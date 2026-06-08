# CrediSense AI Deployment Runbook

## Production Readiness Checklist

- Use Python 3.11 and install dependencies with `pip install -r requirements.txt`.
- Set `FLASK_SECRET_KEY` to a long random value.
- Set `SESSION_COOKIE_SECURE=1` on HTTPS hosts.
- Use PostgreSQL through `DATABASE_URL` for a durable public deployment.
- Keep email and Twilio credentials in platform secrets, not in Git.
- Run `python -m unittest discover -s tests -v` before each deploy.
- Confirm `/api/health` returns `{"status": "ok"}` after deploy.
- Paste the final public URLs into the README `Live Demo Links` table.

## Render

Use the included `render.yaml` blueprint or create a Web Service manually:

```text
Build Command: pip install -r requirements-deploy.txt
Start Command: gunicorn -c gunicorn.conf.py app:app
Health Check Path: /api/health
```

Required environment variables:

```text
FLASK_SECRET_KEY=<long-random-secret>
DATABASE_URL=<postgres-connection-url>
SESSION_COOKIE_SECURE=1
```

Optional delivery integrations:

```text
EMAIL_SENDER=
EMAIL_APP_PASSWORD=
EMAIL_SMTP_HOST=smtp.gmail.com
EMAIL_SMTP_PORT=587
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_FROM_PHONE=
```

## Railway

Railway can deploy this app with the included `railway.toml` and `Dockerfile`.

```text
Builder: Dockerfile
Pre-deploy Command: python init_db.py
Start Command: gunicorn -c gunicorn.conf.py app:app
Health Check Path: /api/health
```

Required environment variables:

```text
FLASK_SECRET_KEY=<long-random-secret>
SESSION_COOKIE_SECURE=1
DATABASE_URL=<postgres-connection-url-optional-but-recommended>
```

## Vercel

Vercel can run the Flask app as a Python function using `vercel.json`. It is useful as an optional backup demo, but Render or Railway is preferred for the full server workflow.

```text
Install Command: pip install -r requirements-deploy.txt
Detected app: app.py
Health Check URL after deploy: /api/health
```

If `DATABASE_URL` is not provided, Vercel uses temporary SQLite storage at `/tmp/credisense-demo.db`, so demo data can reset.

## Docker

```bash
docker build -t credisense-ai .
docker run --rm -p 10000:10000 \
  -e FLASK_SECRET_KEY=replace-with-a-long-random-secret \
  -e DATABASE_PATH=/data/app.db \
  -v credisense-data:/data \
  credisense-ai
```

For cloud Docker deployment, prefer `DATABASE_URL` over the local SQLite volume.
