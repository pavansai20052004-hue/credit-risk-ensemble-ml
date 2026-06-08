# CrediSense AI Live Demo Guide

This project is ready for public demo deployment. For recruiters, use Render or Railway as the primary live app because they run the full Flask server cleanly. Vercel is included as an optional serverless demo target.

## Best Recruiter Setup

Use one primary link in your resume and portfolio:

```text
Live Demo: https://your-credisense-demo.onrender.com
GitHub: https://github.com/your-username/credit-risk-project
```

Then keep backup links in the README:

| Platform | Purpose | Link |
| --- | --- | --- |
| Render | Primary full Flask demo | Paste Render URL |
| Railway | Backup full Flask demo | Paste Railway URL |
| Vercel | Live serverless demo | https://credit-risk-project.vercel.app |

## Required Environment Variables

Set these in each platform:

```text
FLASK_SECRET_KEY=<generate a long random value>
SESSION_COOKIE_SECURE=1
```

Recommended for durable public demos:

```text
DATABASE_URL=<PostgreSQL connection string>
```

If `DATABASE_URL` is not set, the app still runs with SQLite for demo purposes, but data can reset when the host restarts or a serverless function cold-starts.

Optional notification variables:

```text
EMAIL_SENDER=
EMAIL_APP_PASSWORD=
EMAIL_SMTP_HOST=smtp.gmail.com
EMAIL_SMTP_PORT=587
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_FROM_PHONE=
```

## Render Deployment

Render is the easiest primary live demo for this Flask app.

1. Push the project to GitHub.
2. Go to Render Dashboard.
3. Create a new Web Service or Blueprint from the repository.
4. Use the included `render.yaml`.
5. Confirm:
   - Build command: `pip install -r requirements-deploy.txt`
   - Start command: `gunicorn -c gunicorn.conf.py app:app`
   - Health check path: `/api/health`
6. Add `DATABASE_URL` if you have PostgreSQL.
7. Deploy and copy the `onrender.com` URL.

## Railway Deployment

Railway is a strong backup live demo because it can use the included Dockerfile.

1. Push the project to GitHub.
2. Go to Railway and create a new project from the repository.
3. Railway will read `railway.toml`.
4. Confirm:
   - Builder: Dockerfile
   - Start command: `gunicorn -c gunicorn.conf.py app:app`
   - Health check path: `/api/health`
5. Add variables:
   - `FLASK_SECRET_KEY`
   - `SESSION_COOKIE_SECURE=1`
   - optional `DATABASE_URL`
6. Deploy and copy the Railway public domain.

## Vercel Deployment

Vercel is included as an optional serverless target. It is useful as a backup link, but Render/Railway are better for the full persistent Flask workflow.

1. Push the project to GitHub.
2. Import the repository in Vercel.
3. Vercel detects the Flask `app` in `app.py`.
4. It uses `requirements-deploy.txt` through `vercel.json`.
5. Add:
   - `FLASK_SECRET_KEY`
   - `SESSION_COOKIE_SECURE=1`
6. Deploy and copy the Vercel URL.

Note: without `DATABASE_URL`, Vercel uses temporary SQLite storage at `/tmp/credisense-demo.db`. That is fine for quick recruiter viewing, but not for persistent demo data.

## After Deployment

Open these URLs and verify:

```text
/
/login
/api/health
```

Demo accounts:

| Role | Email | Password |
| --- | --- | --- |
| Customer | `customer@credisense.ai` | `Customer@123` |
| Bank Officer | `officer@credisense.ai` | `Officer@123` |
| Risk Admin | `admin@credisense.ai` | `Admin@123` |

## Resume Line

```text
CrediSense AI - Full-stack credit-risk decision platform with role-based dashboards, explainable ML, model governance, audit memos, and counterfactual approval rescue plans. Live demo: <paste URL>
```
