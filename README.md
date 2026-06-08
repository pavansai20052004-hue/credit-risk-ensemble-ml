# CrediSense AI - Credit Risk Decision Platform

CrediSense AI is a full-stack Flask credit-risk platform for loan screening demos. It combines role-based dashboards, ML-backed risk scoring, explainability, officer review workflows, SQL persistence, loan disbursement simulation, payment tracking, notifications, and operational assurance checks.

The application runs locally with SQLite and is deployment-ready for Render plus PostgreSQL through `DATABASE_URL`.

## Live Demo Links

Add these after deployment so recruiters can open the project without cloning the repo.

| Platform | Link |
| --- | --- |
| Render primary demo | `https://your-render-app.onrender.com` |
| Railway backup demo | `https://your-railway-app.up.railway.app` |
| Vercel live demo | `https://credit-risk-project.vercel.app` |

Deployment instructions are in [`LIVE_DEMO.md`](LIVE_DEMO.md).

## Highlights

- Customer, bank officer, and risk admin workspaces with role-based access.
- Loan application scoring with approval probability, default risk, confidence, and AI score.
- Saved scikit-learn model support with a resilient policy fallback.
- Explainability factors and customer improvement suggestions.
- Officer review queue with final approval or decline actions.
- Correct final-status handling: rejected applications remain `Declined` and never reappear as awaiting approval.
- Customer loan wallet with approved offers, disbursement, due dates, and payment history.
- Applicant lookup for bank officers and risk admins.
- Risk admin insights for top-risk candidates, best daily candidates, and officer approval history.
- Email and SMS notification abstraction with auditable delivery records.
- System assurance panel for status integrity, model readiness, database mode, open queue, and notifications.
- Model governance center with drift monitoring, fairness proxy checks, data-quality controls, policy alignment, and a live model card.
- Printable decision audit memo for each application with model output, policy flags, safeguards, review status, and governance snapshot.
- Counterfactual approval rescue plan that converts a risky decision into target score, safer loan amount, term, blocker list, and scenario twins.
- CSRF-protected POST flows, strict browser security headers, and CI-backed regression tests.

## Demo Logins

| Role | Email | Password |
| --- | --- | --- |
| Customer | `customer@credisense.ai` | `Customer@123` |
| Bank Officer | `officer@credisense.ai` | `Officer@123` |
| Risk Admin | `admin@credisense.ai` | `Admin@123` |

## Local Start In VS Code

Open this folder in VS Code:

```text
C:\Users\pavansai\OneDrive\Desktop\credit-risk-project
```

Then run:

```powershell
.\start_local.ps1
```

Open:

```text
http://127.0.0.1:5000
```

If PowerShell blocks the script:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\start_local.ps1
```

Manual start:

```powershell
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
python init_db.py
python app.py
```

Do not run `gunicorn app:app` on Windows. Gunicorn is for Render/Linux.

## Where Data Is Stored

Local data is stored in:

```text
app.db
```

Important SQLite tables:

| Table | Purpose |
| --- | --- |
| `predictions` | Loan application history, risk scores, final status, review notes |
| `users` | Login accounts and roles |
| `loan_accounts` | Disbursed loan records |
| `payment_transactions` | Payment history |
| `notifications` | Email/SMS delivery audit records |
| `activity_log` | Login, review, prediction, loan, and payment activity |

Use a VS Code SQLite extension to browse `app.db`, or run:

```powershell
.\venv\Scripts\python.exe -c "import sqlite3; con=sqlite3.connect('app.db'); [print(r) for r in con.execute('SELECT id, applicant_name, result, status, loan_amount, risk_score, created_at FROM predictions ORDER BY id DESC LIMIT 20')]"
```

## Project Structure

```text
app.py                    Flask app, routes, scoring, auth, DB migrations
database.py               SQLite/PostgreSQL connection helper
init_db.py                Database initializer
generate_data.py          Synthetic training data generator
src/train.py              Model training and comparison script
src/predict.py            CLI prediction helper
src/preprocessing.py      Numeric/categorical preprocessing pipeline
src/models.py             Candidate ML models
models/best_model.joblib  Optional saved trained model
templates/                Flask templates
static/                   Dashboard CSS and JavaScript
docs/PRESENTATION_GUIDE.md
render.yaml               Render deployment blueprint
railway.toml              Railway Docker deployment config
vercel.json               Vercel serverless Flask config
requirements-deploy.txt   Smaller runtime dependency list for cloud demos
Procfile                  Gunicorn process definition
gunicorn.conf.py          Production server binding and worker settings
Dockerfile                Container deployment image
.github/workflows/ci.yml  GitHub Actions regression suite
```

## Environment Variables

```bash
FLASK_SECRET_KEY=change-this-before-demo
DATABASE_PATH=app.db
DATABASE_URL=
EMAIL_SENDER=your-email@gmail.com
EMAIL_APP_PASSWORD=your-email-app-password
EMAIL_SMTP_HOST=smtp.gmail.com
EMAIL_SMTP_PORT=587
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_FROM_PHONE=
SESSION_COOKIE_SECURE=0
WEB_CONCURRENCY=2
GUNICORN_THREADS=4
GUNICORN_TIMEOUT=120
```

Set `SESSION_COOKIE_SECURE=1` only when running behind HTTPS.

## Live Demo Deployment

For recruiters, publish at least one public URL. Render or Railway is recommended for the full Flask workflow.

Primary options:

| Platform | Included config | Notes |
| --- | --- | --- |
| Render | `render.yaml` | Best simple primary demo. Uses Gunicorn and `/api/health`. |
| Railway | `railway.toml` + `Dockerfile` | Good backup demo with Docker deployment. |
| Vercel | `vercel.json` | Optional serverless demo; use PostgreSQL for durable data. |

Cloud runtime install command:

```bash
pip install -r requirements-deploy.txt
```

Cloud start command:

```bash
gunicorn -c gunicorn.conf.py app:app
```

Set:

```text
FLASK_SECRET_KEY=<long-random-secret>
SESSION_COOKIE_SECURE=1
DATABASE_URL=<optional-postgres-url-for-durable-data>
```

See [`LIVE_DEMO.md`](LIVE_DEMO.md) for platform-by-platform steps.

## Docker Deployment

```bash
docker build -t credisense-ai .
docker run --rm -p 10000:10000 \
  -e FLASK_SECRET_KEY=replace-with-a-long-random-secret \
  -e SESSION_COOKIE_SECURE=0 \
  credisense-ai
```

Open:

```text
http://127.0.0.1:10000
```

## Model Training

Generate synthetic data:

```bash
python generate_data.py --rows 1500 --output data/credit_dataset.csv
```

Train and compare models:

```bash
python src/train.py --data data/credit_dataset.csv --target default
```

The best model is saved to:

```text
models/best_model.joblib
```

## Health Check

```text
/api/health
```

Returns database mode, model availability, notification configuration, status-integrity issue count, and model-governance status for drift, fairness proxy, data quality, and policy exceptions.

## Tests

Run the regression suite:

```powershell
.\venv\Scripts\python.exe -m unittest discover -s tests -v
```

The tests cover health/security headers, model governance, rejected-status integrity, officer dashboard rendering, simulator input hardening, and counterfactual rescue-plan output.

## Demo Flow

1. Sign in as customer and submit an application.
2. Review approval probability, default risk, AI score, factors, suggestions, and the approval rescue plan.
3. Use the simulator and advisor to test counterfactual loan scenarios.
4. Sign in as bank officer and approve or decline review-ready applications.
5. Sign in as customer, take an approved loan, and complete a payment.
6. Sign in as risk admin to view portfolio insights and officer history.

## Important Note

This is an academic and portfolio-grade demonstration. It should not be used for real lending decisions without real bank data, fairness testing, privacy review, production security controls, audit approvals, and regulatory validation.
