<<<<<<< HEAD
# CrediSense AI - Credit Risk SaaS

CrediSense AI is a Flask and SQLite based credit-risk SaaS prototype for loan screening demos. It includes customer login, bank officer login, risk admin login, ML-backed approval scoring, explainability, review workflow, charts, a what-if simulator, and a lightweight advisor chatbot.

## Demo Logins

| Role | Email | Password |
| --- | --- | --- |
| Customer | `customer@credisense.ai` | `Customer@123` |
| Bank Officer | `officer@credisense.ai` | `Officer@123` |
| Risk Admin | `admin@credisense.ai` | `Admin@123` |

## What The Project Shows

- Role-based authentication with Werkzeug password hashing.
- SQLite locally and Neon PostgreSQL in production through `DATABASE_URL`.
- SQL backend for users, predictions, officer reviews, loan accounts, notifications, plans, and audit activity.
- Credit risk prediction using the saved scikit-learn model pipeline.
- Approval probability, default risk score, AI score, and decision confidence.
- Explainability factors and improvement suggestions for each application.
- Officer review queue for applications that need manual decisioning.
- Customer loan wallet with approved offers, fake disbursement, payment due time, and demo repayment.
- SMS notification abstraction using Twilio when configured, with demo message logging when not configured.
- Product-style SaaS dashboard with charts, simulator, history, and plan upgrade demo.
- Optional approval email demo using environment variables.

## Project Structure

```text
app.py                    Flask app, routes, scoring, auth, DB migrations
database.py               SQLite connection helper
init_db.py                Database initializer
generate_data.py          Synthetic training dataset generator
src/train.py              Model training and comparison script
src/predict.py            CLI prediction script
src/preprocessing.py      Numeric/categorical preprocessing pipeline
src/models.py             Candidate ML models
models/best_model.joblib  Saved trained model
templates/                Flask templates
static/                   Dashboard CSS and JavaScript
docs/PRESENTATION_GUIDE.md
render.yaml               Render deployment blueprint
Procfile                  Gunicorn process definition
```

## Setup

### Local Windows Start

The local app has one Flask server. It serves both the backend and frontend, so you do not need to start a separate frontend.

Easiest option:

```powershell
.\start_local.ps1
```

Or double-click:

```text
start_local.bat
```

Manual option:

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python init_db.py
python app.py
```

Open `http://127.0.0.1:5000` and use one of the demo logins above.

Do not run `gunicorn app:app` on Windows. Gunicorn is only for Render/Linux deployment and will fail on Windows with `No module named 'fcntl'`.

Optional email and SMS demo settings:

```bash
set FLASK_SECRET_KEY=change-this-before-demo
set EMAIL_SENDER=your-email@gmail.com
set EMAIL_APP_PASSWORD=your-email-app-password
set TWILIO_ACCOUNT_SID=your-twilio-sid
set TWILIO_AUTH_TOKEN=your-twilio-token
set TWILIO_FROM_PHONE=+15551234567
```

If Twilio settings are not configured, the app still records demo SMS messages in the dashboard.

## Render + Neon Deployment

1. Push this project to GitHub.
2. Create a Neon Postgres database and copy its pooled connection string.
3. In Render, create a new Web Service from the GitHub repo.
4. Render settings:
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `gunicorn app:app`
   - Health Check Path: `/api/health`
5. Add environment variables in Render:
   - `DATABASE_URL`: Neon connection string, including `sslmode=require`
   - `FLASK_SECRET_KEY`: any strong random value
   - Optional: `EMAIL_SENDER`, `EMAIL_APP_PASSWORD`
   - Optional: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_PHONE`
6. Deploy. The app initializes tables automatically through `python init_db.py` in `render.yaml`.

## Rebuild The Model

Generate a reproducible synthetic credit-risk dataset:

```bash
python generate_data.py --rows 1500 --output data/credit_dataset.csv
```

Train and compare models:

```bash
python src/train.py --data data/credit_dataset.csv --target default
```

The best model is saved to `models/best_model.joblib`.

## Presentation Flow

1. Sign in as the customer and submit a loan application.
2. Show approval probability, default risk, AI score, factor impacts, and suggestions.
3. Use the simulator to change score, income, loan, term, and experience.
4. Sign in as the bank officer and open the review queue.
5. Save an officer decision and note.
6. Sign in as the customer again, use Take Loan, show the credited mobile message, and complete the fake payment.
7. Sign in as risk admin to show the full portfolio view.

## Important Note

This is an academic demonstration project. It should not be used for real lending decisions without real bank data, fairness testing, privacy review, stronger security controls, audit requirements, and regulatory validation.
=======
# Credit Risk Assessment using Ensemble Learning

## Project Overview
This project predicts whether a customer will default on a loan using Machine Learning ensemble models.

## Technologies Used
- Python
- Pandas
- Scikit-learn
- XGBoost
- Flask

## Models Used
- Logistic Regression
- Random Forest
- XGBoost

Best Model selected using Cross Validation F1 Score.

## How to Run

1. Install requirements:
pip install -r requirements.txt

2. Train model:
python src/train.py --data data/credit_dataset.csv --target default

3. Run API:
python app.py

Server runs at:
http://127.0.0.1:5000

## Prediction Endpoint
POST /predict

Returns probability of default and predicted class.

## Author
Pavan Sai
>>>>>>> 0eaa4cfa2a9644d4d94f7b492918778c797d197d
