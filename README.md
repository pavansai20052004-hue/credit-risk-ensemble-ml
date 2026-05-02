# CrediSense AI - Credit Risk Decision Platform

CrediSense AI is a full-stack Flask credit-risk platform for loan screening demos. It combines role-based dashboards, ML-backed risk scoring, explainability, officer review workflows, SQL persistence, loan disbursement simulation, payment tracking, notifications, and operational assurance checks.

The application runs locally with SQLite and is deployment-ready for Render plus PostgreSQL through `DATABASE_URL`.

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
Procfile                  Gunicorn process definition
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
```

Set `SESSION_COOKIE_SECURE=1` only when running behind HTTPS.

## Render + PostgreSQL Deployment

1. Push this repository to GitHub.
2. Create a PostgreSQL database, such as Neon.
3. Create a Render Web Service from the GitHub repository.
4. Use:
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `gunicorn app:app`
   - Health Check Path: `/api/health`
5. Add:
   - `DATABASE_URL`
   - `FLASK_SECRET_KEY`
   - optional email and Twilio variables

The app initializes its tables automatically on startup.

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

Returns database mode, model availability, notification configuration, and status-integrity issue count.

## Tests

Run the regression suite:

```powershell
.\venv\Scripts\python.exe -m unittest discover -s tests -v
```

The tests cover health/security headers, rejected-status integrity, officer dashboard rendering, and simulator input hardening.

## Demo Flow

1. Sign in as customer and submit an application.
2. Review approval probability, default risk, AI score, factors, and suggestions.
3. Use the simulator and advisor to test better loan scenarios.
4. Sign in as bank officer and approve or decline review-ready applications.
5. Sign in as customer, take an approved loan, and complete a payment.
6. Sign in as risk admin to view portfolio insights and officer history.

## Important Note

This is an academic and portfolio-grade demonstration. It should not be used for real lending decisions without real bank data, fairness testing, privacy review, production security controls, audit approvals, and regulatory validation.
