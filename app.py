import json
import os
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from functools import wraps

import joblib
import pandas as pd
from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from database import IntegrityError, database_label, get_db, is_postgres


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "data.csv")
MODEL_FILE = os.path.join(BASE_DIR, "models", "best_model.joblib")
DEFAULT_LOAN_TERM_MONTHS = 36

VALID_ROLES = {
    "customer": "Customer",
    "bank_officer": "Bank Officer",
    "risk_admin": "Risk Admin",
}

PLAN_LIMITS = {
    "Starter": 10,
    "Pro": 250,
    "Enterprise": 1000,
}

EDUCATION_MODEL_MAP = {
    "graduate": "bachelor",
    "non-graduate": "high_school",
    "high_school": "high_school",
    "bachelor": "bachelor",
    "master": "master",
}


app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")

_model_loaded = False
_credit_model = None


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def add_days_iso(days):
    return (datetime.now(timezone.utc) + timedelta(days=days)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def days_until(value):
    target = parse_iso(value)
    if not target:
        return 0
    delta = target - datetime.now(timezone.utc)
    return max(0, delta.days)


def row_to_dict(row):
    if row is None:
        return None
    return row if isinstance(row, dict) else dict(row)


def fetch_one(query, params=()):
    conn = get_db()
    try:
        return row_to_dict(conn.execute(query, params).fetchone())
    finally:
        conn.close()


def fetch_all(query, params=()):
    conn = get_db()
    try:
        return [row_to_dict(row) for row in conn.execute(query, params).fetchall()]
    finally:
        conn.close()


def execute(query, params=(), return_id=False):
    conn = get_db()
    try:
        if return_id and is_postgres():
            query = query.rstrip().rstrip(";") + " RETURNING id"
        cur = conn.execute(query, params)
        returned_id = None
        if return_id and is_postgres():
            row = cur.fetchone()
            returned_id = row_to_dict(row).get("id") if row else None
        conn.commit()
        return returned_id if return_id and is_postgres() else cur.lastrowid
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def table_columns(conn, table_name):
    if is_postgres():
        rows = conn.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = ?
            """,
            (table_name,),
        ).fetchall()
        return {row_to_dict(row)["column_name"] for row in rows}
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


def add_column_if_missing(conn, table_name, column_name, column_sql):
    if column_name not in table_columns(conn, table_name):
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}")


def seed_user(conn, name, email, password, role, plan):
    password_hash = generate_password_hash(password)
    conn.execute(
        """
        INSERT INTO users (name, email, password_hash, role, plan, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(email) DO UPDATE SET
            name = excluded.name,
            password_hash = excluded.password_hash,
            role = excluded.role,
            plan = excluded.plan
        """,
        (name, email, password_hash, role, plan, now_iso()),
    )


def decision_status(result, risk_score):
    risk_score = float(risk_score or 0)
    if result == "Approved" and risk_score <= 35:
        return "pre_approved"
    if result == "Approved":
        return "needs_review"
    if risk_score >= 75:
        return "declined"
    return "needs_review"


def import_csv_history(conn):
    if not os.path.exists(DATA_FILE):
        return

    demo_user = conn.execute("SELECT id FROM users WHERE email = ?", ("customer@credisense.ai",)).fetchone()
    fallback_user_id = demo_user["id"] if demo_user else 1

    try:
        df = pd.read_csv(DATA_FILE)
    except Exception:
        return

    for index, row in df.iterrows():
        external_id = f"data.csv:{index}"
        exists = conn.execute(
            "SELECT id FROM predictions WHERE external_id = ?",
            (external_id,),
        ).fetchone()
        if exists:
            continue

        applicant_email = str(row.get("email", "") or "").strip()
        owner = None
        if applicant_email:
            owner = conn.execute("SELECT id FROM users WHERE lower(email) = lower(?)", (applicant_email,)).fetchone()

        user_id = owner["id"] if owner else fallback_user_id
        result = str(row.get("result", "Rejected") or "Rejected").strip().title()
        risk_score = float(row.get("risk_score", 100) or 100)
        approval_probability = float(row.get("approval_probability", max(0, 100 - risk_score)) or 0)
        credit_score = float(row.get("score", 0) or 0)
        income = float(row.get("income", 0) or 0)
        loan = float(row.get("loan", 0) or 0)
        employment_years = int(float(row.get("experience", 0) or 0))
        loan_term_months = int(float(row.get("loan_term_months", DEFAULT_LOAN_TERM_MONTHS) or DEFAULT_LOAN_TERM_MONTHS))
        ai_score = calculate_ai_score(credit_score, income, loan, employment_years)
        explanations = build_explainability(credit_score, income, loan, employment_years, loan_term_months)
        suggestions = build_suggestions(credit_score, income, loan, employment_years)

        conn.execute(
            """
            INSERT INTO predictions (
                user_id, applicant_name, applicant_email, phone, age, income, loan_amount,
                loan_term_months, credit_score, marital_status, education, dependents,
                employment_years, approval_probability, risk_score, ai_score, result,
                status, model_source, explain_json, suggestions_json, external_id, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                str(row.get("name", "Demo Applicant") or "Demo Applicant"),
                applicant_email,
                str(row.get("phone", "") or ""),
                int(float(row.get("age", 0) or 0)),
                income,
                loan,
                loan_term_months,
                credit_score,
                str(row.get("marital_status", "single") or "single"),
                str(row.get("education", "bachelor") or "bachelor"),
                int(float(row.get("dependents", 0) or 0)),
                employment_years,
                approval_probability,
                risk_score,
                ai_score,
                result,
                decision_status(result, risk_score),
                "Imported demo history",
                json.dumps(explanations),
                json.dumps(suggestions),
                external_id,
                now_iso(),
            ),
        )


def init_database():
    conn = get_db()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT,
                password TEXT,
                role TEXT NOT NULL DEFAULT 'customer',
                plan TEXT NOT NULL DEFAULT 'Starter',
                created_at TEXT NOT NULL,
                last_login_at TEXT
            );

            CREATE TABLE IF NOT EXISTS predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                applicant_name TEXT,
                applicant_email TEXT,
                phone TEXT,
                age INTEGER,
                income REAL,
                loan_amount REAL,
                loan_term_months INTEGER,
                credit_score REAL,
                marital_status TEXT,
                education TEXT,
                dependents INTEGER,
                employment_years INTEGER,
                approval_probability REAL,
                risk_score REAL,
                ai_score REAL,
                result TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'needs_review',
                model_source TEXT,
                explain_json TEXT,
                suggestions_json TEXT,
                review_note TEXT,
                reviewed_by INTEGER,
                reviewed_at TEXT,
                external_id TEXT UNIQUE,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                event_type TEXT NOT NULL,
                details TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS loan_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prediction_id INTEGER UNIQUE NOT NULL,
                user_id INTEGER NOT NULL,
                principal_amount REAL NOT NULL,
                disbursed_amount REAL NOT NULL,
                balance_amount REAL NOT NULL,
                interest_rate REAL NOT NULL DEFAULT 9.0,
                term_months INTEGER NOT NULL,
                monthly_payment REAL NOT NULL,
                due_date TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                payment_status TEXT NOT NULL DEFAULT 'pending',
                credited_at TEXT,
                last_payment_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (prediction_id) REFERENCES predictions(id),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                prediction_id INTEGER,
                loan_id INTEGER,
                channel TEXT NOT NULL,
                recipient TEXT NOT NULL,
                message TEXT NOT NULL,
                status TEXT NOT NULL,
                provider TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (prediction_id) REFERENCES predictions(id),
                FOREIGN KEY (loan_id) REFERENCES loan_accounts(id)
            );
            """
        )

        user_columns = {
            "name": "TEXT",
            "email": "TEXT",
            "password_hash": "TEXT",
            "password": "TEXT",
            "role": "TEXT NOT NULL DEFAULT 'customer'",
            "plan": "TEXT NOT NULL DEFAULT 'Starter'",
            "created_at": "TEXT",
            "last_login_at": "TEXT",
        }
        for column, sql in user_columns.items():
            add_column_if_missing(conn, "users", column, sql)

        prediction_columns = {
            "applicant_name": "TEXT",
            "applicant_email": "TEXT",
            "phone": "TEXT",
            "age": "INTEGER",
            "income": "REAL",
            "loan_amount": "REAL",
            "loan_term_months": "INTEGER",
            "credit_score": "REAL",
            "marital_status": "TEXT",
            "education": "TEXT",
            "dependents": "INTEGER",
            "employment_years": "INTEGER",
            "approval_probability": "REAL",
            "risk_score": "REAL",
            "ai_score": "REAL",
            "status": "TEXT NOT NULL DEFAULT 'needs_review'",
            "model_source": "TEXT",
            "explain_json": "TEXT",
            "suggestions_json": "TEXT",
            "review_note": "TEXT",
            "reviewed_by": "INTEGER",
            "reviewed_at": "TEXT",
            "external_id": "TEXT",
            "created_at": "TEXT",
        }
        for column, sql in prediction_columns.items():
            add_column_if_missing(conn, "predictions", column, sql)

        loan_columns = {
            "prediction_id": "INTEGER",
            "user_id": "INTEGER",
            "principal_amount": "REAL NOT NULL DEFAULT 0",
            "disbursed_amount": "REAL NOT NULL DEFAULT 0",
            "balance_amount": "REAL NOT NULL DEFAULT 0",
            "interest_rate": "REAL NOT NULL DEFAULT 9.0",
            "term_months": "INTEGER NOT NULL DEFAULT 36",
            "monthly_payment": "REAL NOT NULL DEFAULT 0",
            "due_date": "TEXT",
            "status": "TEXT NOT NULL DEFAULT 'active'",
            "payment_status": "TEXT NOT NULL DEFAULT 'pending'",
            "credited_at": "TEXT",
            "last_payment_at": "TEXT",
            "created_at": "TEXT",
            "updated_at": "TEXT",
        }
        for column, sql in loan_columns.items():
            add_column_if_missing(conn, "loan_accounts", column, sql)

        notification_columns = {
            "user_id": "INTEGER",
            "prediction_id": "INTEGER",
            "loan_id": "INTEGER",
            "channel": "TEXT NOT NULL DEFAULT 'sms'",
            "recipient": "TEXT",
            "message": "TEXT",
            "status": "TEXT NOT NULL DEFAULT 'demo'",
            "provider": "TEXT NOT NULL DEFAULT 'demo-log'",
            "created_at": "TEXT",
        }
        for column, sql in notification_columns.items():
            add_column_if_missing(conn, "notifications", column, sql)

        conn.execute("UPDATE users SET password_hash = password WHERE password_hash IS NULL AND password IS NOT NULL")
        conn.execute("UPDATE users SET role = 'customer' WHERE role IS NULL OR role = ''")
        conn.execute("UPDATE users SET plan = 'Starter' WHERE plan IS NULL OR plan = ''")
        conn.execute("UPDATE users SET created_at = ? WHERE created_at IS NULL OR created_at = ''", (now_iso(),))
        conn.execute("UPDATE predictions SET status = 'needs_review' WHERE status IS NULL OR status = ''")
        conn.execute("UPDATE predictions SET created_at = ? WHERE created_at IS NULL OR created_at = ''", (now_iso(),))

        seed_user(conn, "Demo Customer", "customer@credisense.ai", "Customer@123", "customer", "Starter")
        seed_user(conn, "Bank Officer", "officer@credisense.ai", "Officer@123", "bank_officer", "Pro")
        seed_user(conn, "Risk Admin", "admin@credisense.ai", "Admin@123", "risk_admin", "Enterprise")

        conn.executescript(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_unique ON users(email);
            CREATE INDEX IF NOT EXISTS idx_predictions_user_id ON predictions(user_id);
            CREATE INDEX IF NOT EXISTS idx_predictions_result ON predictions(result);
            CREATE INDEX IF NOT EXISTS idx_predictions_status ON predictions(status);
            CREATE INDEX IF NOT EXISTS idx_predictions_created_at ON predictions(created_at);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_predictions_external_id_unique
                ON predictions(external_id)
                WHERE external_id IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_loan_accounts_user_id ON loan_accounts(user_id);
            CREATE INDEX IF NOT EXISTS idx_loan_accounts_prediction_id ON loan_accounts(prediction_id);
            CREATE INDEX IF NOT EXISTS idx_notifications_user_id ON notifications(user_id);
            """
        )

        import_csv_history(conn)
        conn.commit()
    finally:
        conn.close()


def log_activity(user_id, event_type, details=""):
    execute(
        "INSERT INTO activity_log (user_id, event_type, details, created_at) VALUES (?, ?, ?, ?)",
        (user_id, event_type, details, now_iso()),
    )


def mask_phone(phone):
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    if len(digits) <= 4:
        return phone or ""
    return f"***{digits[-4:]}"


def record_notification(user_id, channel, recipient, message, status, provider, prediction_id=None, loan_id=None):
    execute(
        """
        INSERT INTO notifications (
            user_id, prediction_id, loan_id, channel, recipient, message, status, provider, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (user_id, prediction_id, loan_id, channel, recipient, message, status, provider, now_iso()),
    )


def send_sms_notification(user_id, phone, message, prediction_id=None, loan_id=None):
    recipient = (phone or "").strip()
    provider = "demo-log"
    status = "demo"

    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
    from_phone = os.environ.get("TWILIO_FROM_PHONE")

    if recipient and account_sid and auth_token and from_phone:
        try:
            from twilio.rest import Client

            client = Client(account_sid, auth_token)
            twilio_message = client.messages.create(body=message, from_=from_phone, to=recipient)
            provider = "twilio"
            status = getattr(twilio_message, "status", "sent")
        except Exception as exc:
            provider = "twilio"
            status = "failed"
            print(f"SMS failed for {mask_phone(recipient)}: {exc}")
    else:
        print(f"Demo SMS to {mask_phone(recipient)}: {message}")

    record_notification(user_id, "sms", recipient, message, status, provider, prediction_id, loan_id)
    return status not in {"failed"}


def load_credit_model():
    global _credit_model, _model_loaded

    if _model_loaded:
        return _credit_model

    _model_loaded = True
    if not os.path.exists(MODEL_FILE):
        print(f"Model not found at {MODEL_FILE}. Falling back to policy scoring.")
        return None

    try:
        _credit_model = joblib.load(MODEL_FILE)
    except Exception as exc:
        print(f"Could not load model: {exc}")
        _credit_model = None

    return _credit_model


def normalize_education_for_model(value):
    value = (value or "").strip().lower()
    return EDUCATION_MODEL_MAP.get(value, "bachelor")


def calculate_rule_score(score, income, loan, experience):
    score_component = max(0, min((score - 300) / 600, 1)) * 45
    income_component = min(income / max(loan, 1), 2) / 2 * 35
    experience_component = min(max(experience, 0), 10) / 10 * 10
    affordability_bonus = 10 if loan <= income else 0

    approval_probability = round(
        max(5, min(score_component + income_component + experience_component + affordability_bonus, 95)),
        2,
    )
    risk_score = round(100 - approval_probability, 2)
    result = "Approved" if approval_probability >= 55 and score >= 620 and loan <= income * 1.25 else "Rejected"

    return {
        "result": result,
        "approval_probability": approval_probability,
        "risk_score": risk_score,
        "confidence": approval_probability if result == "Approved" else risk_score,
        "source": "Policy scorecard fallback",
    }


def predict_credit_risk(age, income, loan, score, marital_status, education, dependents, experience, loan_term_months):
    model = load_credit_model()
    if model is None:
        return calculate_rule_score(score, income, loan, experience)

    model_input = pd.DataFrame(
        [
            {
                "age": age,
                "income": income,
                "loan_amount": loan,
                "loan_term_months": loan_term_months,
                "credit_score": score,
                "employment_years": experience,
                "marital_status": marital_status or "single",
                "education": normalize_education_for_model(education),
                "dependents": dependents,
            }
        ]
    )

    try:
        if hasattr(model, "predict_proba"):
            probabilities = model.predict_proba(model_input)[0]
            classes = list(getattr(model, "classes_", [0, 1]))
            default_index = classes.index(1) if 1 in classes else len(probabilities) - 1
            default_probability = float(probabilities[default_index] * 100)
        else:
            prediction = int(model.predict(model_input)[0])
            default_probability = 80.0 if prediction == 1 else 20.0

        risk_score = round(max(0, min(default_probability, 100)), 2)
        approval_probability = round(100 - risk_score, 2)
        result = "Approved" if approval_probability >= 50 else "Rejected"

        return {
            "result": result,
            "approval_probability": approval_probability,
            "risk_score": risk_score,
            "confidence": approval_probability if result == "Approved" else risk_score,
            "source": model_source_name(model),
        }
    except Exception as exc:
        print(f"Model prediction failed: {exc}")
        return calculate_rule_score(score, income, loan, experience)


def model_source_name(model):
    estimator = model
    if hasattr(model, "named_steps"):
        estimator = model.named_steps.get("model", model)
    name = estimator.__class__.__name__.replace("Classifier", "").replace("_", " ")
    return f"Saved {name} model"


def risk_label(risk_score):
    risk_score = float(risk_score or 0)
    if risk_score < 30:
        return "Low"
    if risk_score < 60:
        return "Medium"
    return "High"


def build_explainability(score, income, loan, experience, loan_term_months):
    explanations = []
    debt_to_income = loan / max(income, 1)

    if score >= 750:
        explanations.append({"feature": "Credit Score", "impact": 32})
    elif score >= 650:
        explanations.append({"feature": "Credit Score", "impact": 18})
    else:
        explanations.append({"feature": "Credit Score", "impact": -32})

    if debt_to_income <= 0.5:
        explanations.append({"feature": "Loan Affordability", "impact": 25})
    elif debt_to_income <= 1:
        explanations.append({"feature": "Loan Affordability", "impact": 8})
    else:
        explanations.append({"feature": "Loan Affordability", "impact": -25})

    if income >= 50000:
        explanations.append({"feature": "Income Strength", "impact": 16})
    elif income < 30000:
        explanations.append({"feature": "Income Strength", "impact": -12})

    if experience >= 5:
        explanations.append({"feature": "Employment Stability", "impact": 12})
    elif experience < 2:
        explanations.append({"feature": "Employment Stability", "impact": -10})

    if loan_term_months > 60:
        explanations.append({"feature": "Loan Tenure", "impact": -6})

    return explanations


def build_suggestions(score, income, loan, experience):
    suggestions = []

    if score < 700:
        suggestions.append("Improve credit score toward 700+ before applying for a larger loan.")
    if loan > income * 0.6:
        suggestions.append("Reduce requested loan amount or add documented repayment capacity.")
    if income < 40000:
        suggestions.append("Add income proof, co-applicant support, or collateral details.")
    if experience < 2:
        suggestions.append("Show stable employment history for at least two years.")

    if not suggestions:
        suggestions.append("Profile is strong. Keep debt-to-income ratio low and payment history clean.")

    return suggestions


def calculate_ai_score(score, income, loan, experience):
    return round(
        min(
            100,
            (score / 900) * 40
            + min(income / max(loan, 1), 2) / 2 * 35
            + min(max(experience, 0), 10) / 10 * 15
            + (10 if loan <= income else 0),
        ),
        2,
    )


def build_auto_explanation(result, score, income, loan, approval_probability, risk_score):
    if result == "Approved":
        return (
            f"Approved with {approval_probability}% approval probability because the profile has "
            f"a credit score of {int(score)} and a manageable loan-to-income ratio."
        )

    reasons = []
    if score < 650:
        reasons.append("credit score below the safer approval band")
    if loan > income:
        reasons.append("loan amount higher than income")
    if not reasons:
        reasons.append(f"modelled default risk of {risk_score}%")

    return "Rejected due to " + ", ".join(reasons) + "."


def parse_float(name, default=0, min_value=None, max_value=None):
    raw = str(request.form.get(name, default)).replace(",", "").strip()
    value = float(raw or default)
    if min_value is not None and value < min_value:
        raise ValueError(f"{name.replace('_', ' ').title()} must be at least {min_value}.")
    if max_value is not None and value > max_value:
        raise ValueError(f"{name.replace('_', ' ').title()} must be at most {max_value}.")
    return value


def parse_int(name, default=0, min_value=None, max_value=None):
    return int(parse_float(name, default, min_value, max_value))


def verify_password(stored_hash, password):
    if not stored_hash:
        return False
    try:
        return check_password_hash(stored_hash, password)
    except ValueError:
        return stored_hash == password


def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    return fetch_one("SELECT * FROM users WHERE id = ?", (user_id,))


def login_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not current_user():
            flash("Please sign in to continue.", "warning")
            return redirect(url_for("login"))
        return view_func(*args, **kwargs)

    return wrapped


def role_required(*roles):
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            user = current_user()
            if not user:
                flash("Please sign in to continue.", "warning")
                return redirect(url_for("login"))
            if user["role"] not in roles:
                flash("You do not have permission to perform that action.", "error")
                return redirect(url_for("dashboard"))
            return view_func(*args, **kwargs)

        return wrapped

    return decorator


@app.context_processor
def inject_globals():
    user = current_user()
    return {
        "current_user": user,
        "role_labels": VALID_ROLES,
        "plan_limits": PLAN_LIMITS,
    }


def scoped_prediction_query(user):
    base_query = """
        SELECT
            p.*,
            u.name AS owner_name,
            reviewer.name AS reviewer_name
        FROM predictions p
        JOIN users u ON u.id = p.user_id
        LEFT JOIN users reviewer ON reviewer.id = p.reviewed_by
    """
    params = ()
    if user["role"] == "customer":
        base_query += " WHERE p.user_id = ?"
        params = (user["id"],)
    base_query += " ORDER BY p.created_at DESC, p.id DESC"
    return base_query, params


def clean_prediction(row):
    item = dict(row)
    item["income"] = float(item.get("income") or 0)
    item["loan_amount"] = float(item.get("loan_amount") or 0)
    item["risk_score"] = float(item.get("risk_score") or 0)
    item["approval_probability"] = float(item.get("approval_probability") or 0)
    item["ai_score"] = float(item.get("ai_score") or 0)
    item["credit_score"] = float(item.get("credit_score") or 0)
    item["risk_label"] = risk_label(item["risk_score"])
    item["status_label"] = (item.get("status") or "needs_review").replace("_", " ").title()
    try:
        item["explain"] = json.loads(item.get("explain_json") or "[]")
    except json.JSONDecodeError:
        item["explain"] = []
    try:
        item["suggestions"] = json.loads(item.get("suggestions_json") or "[]")
    except json.JSONDecodeError:
        item["suggestions"] = []
    return item


def clean_loan(row):
    item = dict(row)
    item["principal_amount"] = float(item.get("principal_amount") or 0)
    item["disbursed_amount"] = float(item.get("disbursed_amount") or 0)
    item["balance_amount"] = float(item.get("balance_amount") or 0)
    item["monthly_payment"] = float(item.get("monthly_payment") or 0)
    item["interest_rate"] = float(item.get("interest_rate") or 0)
    item["days_until_due"] = days_until(item.get("due_date"))
    item["status_label"] = (item.get("status") or "active").replace("_", " ").title()
    item["payment_status_label"] = (item.get("payment_status") or "pending").replace("_", " ").title()
    return item


def fetch_customer_loans(user_id):
    rows = fetch_all(
        """
        SELECT
            l.*,
            p.applicant_name,
            p.result,
            p.status AS prediction_status
        FROM loan_accounts l
        JOIN predictions p ON p.id = l.prediction_id
        WHERE l.user_id = ?
        ORDER BY l.created_at DESC, l.id DESC
        """,
        (user_id,),
    )
    return [clean_loan(row) for row in rows]


def fetch_notifications(user_id, limit=8):
    return fetch_all(
        """
        SELECT *
        FROM notifications
        WHERE user_id = ?
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (user_id, limit),
    )


def loan_offer_available(prediction):
    status = prediction.get("status")
    return prediction.get("result") == "Approved" and status in {"pre_approved", "approved_by_officer"}


def customer_loan_offers(user_id):
    rows = fetch_all(
        """
        SELECT p.*
        FROM predictions p
        LEFT JOIN loan_accounts l ON l.prediction_id = p.id
        WHERE p.user_id = ? AND p.result = 'Approved' AND p.status IN ('pre_approved', 'approved_by_officer') AND l.id IS NULL
        ORDER BY p.created_at DESC, p.id DESC
        """,
        (user_id,),
    )
    return [clean_prediction(row) for row in rows]


def get_dashboard_payload(user):
    query, params = scoped_prediction_query(user)
    rows = fetch_all(query, params)
    history = [clean_prediction(row) for row in rows]

    total = len(history)
    approved = sum(1 for item in history if item["result"] == "Approved")
    rejected = sum(1 for item in history if item["result"] == "Rejected")
    in_review = sum(1 for item in history if item.get("status") == "needs_review")
    approval_rate = round((approved / total) * 100, 2) if total else 0
    avg_risk = round(sum(item["risk_score"] for item in history) / total, 2) if total else 0
    avg_ticket = round(sum(item["loan_amount"] for item in history) / total, 2) if total else 0

    users = fetch_all(
        """
        SELECT role, plan, COUNT(*) AS count
        FROM users
        GROUP BY role, plan
        ORDER BY role, plan
        """
    )

    customer_loans = fetch_customer_loans(user["id"]) if user["role"] == "customer" else []
    notifications = fetch_notifications(user["id"]) if user["role"] == "customer" else []
    loan_offers = customer_loan_offers(user["id"]) if user["role"] == "customer" else []

    return {
        "history": history,
        "recent_history": history[:12],
        "review_queue": [item for item in history if item.get("status") == "needs_review"][:8],
        "customer_loans": customer_loans,
        "loan_offers": loan_offers,
        "notifications": notifications,
        "metrics": {
            "total": total,
            "approved": approved,
            "rejected": rejected,
            "in_review": in_review,
            "approval_rate": approval_rate,
            "avg_risk": avg_risk,
            "avg_ticket": avg_ticket,
        },
        "tenant_metrics": users,
        "chart_data": {
            "approved": approved,
            "rejected": rejected,
            "income": [item["income"] for item in history[:20]][::-1],
            "loan": [item["loan_amount"] for item in history[:20]][::-1],
            "scores": [item["credit_score"] for item in history[:20]][::-1],
            "risk_scores": [item["risk_score"] for item in history[:20]][::-1],
            "labels": [f"#{item['id']}" for item in history[:20]][::-1],
        },
    }


@app.route("/")
def home():
    if current_user():
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        role = request.form.get("role", "customer")

        if role not in {"customer", "bank_officer"}:
            role = "customer"

        if not name or not email or len(password) < 8:
            flash("Use a name, valid email, and password with at least 8 characters.", "error")
            return redirect(url_for("register"))

        try:
            execute(
                """
                INSERT INTO users (name, email, password_hash, role, plan, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (name, email, generate_password_hash(password), role, "Starter", now_iso()),
            )
        except IntegrityError:
            flash("An account with that email already exists.", "error")
            return redirect(url_for("register"))

        flash("Account created. Sign in to open your dashboard.", "success")
        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        identifier = (request.form.get("email") or request.form.get("username") or "").strip().lower()
        password = request.form.get("password", "")
        user = fetch_one(
            """
            SELECT * FROM users
            WHERE lower(email) = lower(?) OR lower(name) = lower(?)
            """,
            (identifier, identifier),
        )

        stored_hash = user.get("password_hash") or user.get("password") if user else None
        if user and verify_password(stored_hash, password):
            session.clear()
            session["user_id"] = user["id"]
            session["role"] = user["role"]
            execute("UPDATE users SET last_login_at = ? WHERE id = ?", (now_iso(), user["id"]))
            log_activity(user["id"], "login", "User signed in")
            return redirect(url_for("dashboard"))

        flash("Invalid login details. Try a demo account or your registered email.", "error")
        return redirect(url_for("login"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    user_id = session.get("user_id")
    if user_id:
        log_activity(user_id, "logout", "User signed out")
    session.clear()
    flash("Signed out successfully.", "success")
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    user = current_user()
    payload = get_dashboard_payload(user)
    last_prediction = session.pop("last_prediction", None)
    return render_template("index.html", **payload, last_prediction=last_prediction)


@app.route("/predict", methods=["POST"])
@login_required
def create_prediction():
    user = current_user()
    try:
        applicant_name = request.form.get("name", "").strip() or user["name"]
        applicant_email = request.form.get("email", "").strip().lower() or user["email"]
        phone = request.form.get("phone", "").strip()
        age = parse_int("age", min_value=18, max_value=80)
        income = parse_float("income", min_value=0)
        loan = parse_float("loan", min_value=0)
        score = parse_float("score", min_value=300, max_value=900)
        loan_term_months = parse_int("loan_term_months", DEFAULT_LOAN_TERM_MONTHS, min_value=12, max_value=120)
        marital_status = request.form.get("marital_status", "single")
        education = request.form.get("education", "bachelor")
        dependents = parse_int("dependents", 0, min_value=0, max_value=10)
        experience = parse_int("experience", 0, min_value=0, max_value=50)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("dashboard"))

    prediction = predict_credit_risk(
        age,
        income,
        loan,
        score,
        marital_status,
        education,
        dependents,
        experience,
        loan_term_months,
    )

    result = prediction["result"]
    approval_probability = round(prediction["approval_probability"], 2)
    risk_score = round(prediction["risk_score"], 2)
    ai_score = calculate_ai_score(score, income, loan, experience)
    explanations = build_explainability(score, income, loan, experience, loan_term_months)
    suggestions = build_suggestions(score, income, loan, experience)
    auto_explain = build_auto_explanation(result, score, income, loan, approval_probability, risk_score)
    status = decision_status(result, risk_score)

    prediction_id = execute(
        """
        INSERT INTO predictions (
            user_id, applicant_name, applicant_email, phone, age, income, loan_amount,
            loan_term_months, credit_score, marital_status, education, dependents,
            employment_years, approval_probability, risk_score, ai_score, result,
            status, model_source, explain_json, suggestions_json, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user["id"],
            applicant_name,
            applicant_email,
            phone,
            age,
            income,
            loan,
            loan_term_months,
            score,
            marital_status,
            education,
            dependents,
            experience,
            approval_probability,
            risk_score,
            ai_score,
            result,
            status,
            prediction["source"],
            json.dumps(explanations),
            json.dumps(suggestions),
            now_iso(),
        ),
        return_id=True,
    )

    log_activity(user["id"], "prediction_created", f"Application #{prediction_id} {result}")
    if result == "Approved":
        send_approval_email(applicant_email, applicant_name, loan)
        sms = (
            f"CrediSense AI: Your loan application #{prediction_id} is approved for ${loan:,.0f}. "
            "Open your customer dashboard to take the loan and view payment due time."
        )
        send_sms_notification(user["id"], phone, sms, prediction_id=prediction_id)
    else:
        sms = (
            f"CrediSense AI: Your loan application #{prediction_id} is currently {result}. "
            "Open your dashboard to view risk factors and next steps."
        )
        send_sms_notification(user["id"], phone, sms, prediction_id=prediction_id)

    session["last_prediction"] = {
        "id": prediction_id,
        "result": result,
        "confidence": round(prediction["confidence"], 2),
        "approval_probability": approval_probability,
        "risk_score": risk_score,
        "risk_label": risk_label(risk_score),
        "ai_score": ai_score,
        "auto_explain": auto_explain,
        "model_source": prediction["source"],
        "explain": explanations,
        "suggestions": suggestions,
        "status": status.replace("_", " ").title(),
    }
    flash(f"Application #{prediction_id} scored successfully.", "success")
    return redirect(url_for("dashboard"))


@app.route("/review/<int:prediction_id>", methods=["POST"])
@role_required("bank_officer", "risk_admin")
def review_prediction(prediction_id):
    user = current_user()
    status = request.form.get("status", "needs_review")
    note = request.form.get("review_note", "").strip()
    allowed = {"pre_approved", "needs_review", "approved_by_officer", "declined"}
    if status not in allowed:
        flash("Invalid review status.", "error")
        return redirect(url_for("dashboard"))

    execute(
        """
        UPDATE predictions
        SET status = ?, review_note = ?, reviewed_by = ?, reviewed_at = ?
        WHERE id = ?
        """,
        (status, note, user["id"], now_iso(), prediction_id),
    )
    log_activity(user["id"], "application_reviewed", f"Application #{prediction_id} set to {status}")
    prediction = fetch_one(
        """
        SELECT user_id, applicant_name, phone, loan_amount
        FROM predictions
        WHERE id = ?
        """,
        (prediction_id,),
    )
    if prediction and status in {"approved_by_officer", "declined"}:
        if status == "approved_by_officer":
            message = (
                f"CrediSense AI: Your loan application #{prediction_id} is officer-approved for "
                f"${float(prediction.get('loan_amount') or 0):,.0f}. Log in to take the loan."
            )
        else:
            message = f"CrediSense AI: Your loan application #{prediction_id} has been declined after review."
        send_sms_notification(prediction["user_id"], prediction.get("phone"), message, prediction_id=prediction_id)
    flash(f"Application #{prediction_id} review saved.", "success")
    return redirect(url_for("dashboard"))


@app.route("/billing/upgrade", methods=["POST"])
@login_required
def upgrade_plan():
    user = current_user()
    execute("UPDATE users SET plan = ? WHERE id = ?", ("Pro", user["id"]))
    log_activity(user["id"], "plan_upgraded", "Plan changed to Pro")
    flash("Plan upgraded to Pro for the demo workspace.", "success")
    return redirect(url_for("dashboard"))


@app.route("/loan/<int:prediction_id>/take", methods=["POST"])
@login_required
def take_loan(prediction_id):
    user = current_user()
    if user["role"] != "customer":
        flash("Only customers can take a loan from an approved offer.", "error")
        return redirect(url_for("dashboard"))

    prediction = fetch_one(
        """
        SELECT *
        FROM predictions
        WHERE id = ? AND user_id = ?
        """,
        (prediction_id, user["id"]),
    )
    if not prediction:
        flash("Loan offer not found.", "error")
        return redirect(url_for("dashboard"))

    prediction = clean_prediction(prediction)
    if not loan_offer_available(prediction):
        flash("This application is not ready for loan disbursement yet.", "error")
        return redirect(url_for("dashboard"))

    existing = fetch_one("SELECT id FROM loan_accounts WHERE prediction_id = ?", (prediction_id,))
    if existing:
        flash("This approved loan is already credited.", "warning")
        return redirect(url_for("dashboard"))

    principal = float(prediction.get("loan_amount") or 0)
    term_months = int(prediction.get("loan_term_months") or DEFAULT_LOAN_TERM_MONTHS)
    interest_rate = 9.0
    total_payable = principal * (1 + (interest_rate / 100) * (term_months / 12))
    monthly_payment = round(total_payable / max(term_months, 1), 2)
    due_date = add_days_iso(30)

    loan_id = execute(
        """
        INSERT INTO loan_accounts (
            prediction_id, user_id, principal_amount, disbursed_amount, balance_amount,
            interest_rate, term_months, monthly_payment, due_date, status, payment_status,
            credited_at, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            prediction_id,
            user["id"],
            principal,
            principal,
            round(total_payable, 2),
            interest_rate,
            term_months,
            monthly_payment,
            due_date,
            "credited",
            "pending",
            now_iso(),
            now_iso(),
            now_iso(),
        ),
        return_id=True,
    )

    execute("UPDATE predictions SET status = ? WHERE id = ?", ("loan_disbursed", prediction_id))
    message = (
        f"CrediSense AI: ${principal:,.0f} has been credited to your demo loan wallet. "
        f"First payment ${monthly_payment:,.2f} is due in 30 days."
    )
    send_sms_notification(user["id"], prediction.get("phone"), message, prediction_id=prediction_id, loan_id=loan_id)
    log_activity(user["id"], "loan_disbursed", f"Loan #{loan_id} credited for application #{prediction_id}")
    flash(f"Loan credited successfully. First demo payment is due in 30 days.", "success")
    return redirect(url_for("dashboard"))


def fetch_loan_for_user(loan_id, user):
    query = """
        SELECT
            l.*,
            p.applicant_name,
            p.phone,
            p.applicant_email,
            u.name AS owner_name
        FROM loan_accounts l
        JOIN predictions p ON p.id = l.prediction_id
        JOIN users u ON u.id = l.user_id
        WHERE l.id = ?
    """
    params = [loan_id]
    if user["role"] == "customer":
        query += " AND l.user_id = ?"
        params.append(user["id"])
    loan = fetch_one(query, tuple(params))
    return clean_loan(loan) if loan else None


@app.route("/payment/<int:loan_id>", methods=["GET", "POST"])
@login_required
def fake_payment(loan_id):
    user = current_user()
    loan = fetch_loan_for_user(loan_id, user)
    if not loan:
        flash("Loan account not found.", "error")
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        payment_amount = min(loan["monthly_payment"], loan["balance_amount"])
        new_balance = round(max(0, loan["balance_amount"] - payment_amount), 2)
        next_status = "closed" if new_balance <= 0 else "active"
        next_payment_status = "paid" if next_status == "closed" else "pending"
        next_due_date = loan["due_date"] if next_status == "closed" else add_days_iso(30)

        execute(
            """
            UPDATE loan_accounts
            SET balance_amount = ?, status = ?, payment_status = ?, due_date = ?,
                last_payment_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (new_balance, next_status, next_payment_status, next_due_date, now_iso(), now_iso(), loan_id),
        )

        message = (
            f"CrediSense AI: Demo payment of ${payment_amount:,.2f} received for loan #{loan_id}. "
            f"Remaining balance is ${new_balance:,.2f}."
        )
        send_sms_notification(loan["user_id"], loan.get("phone"), message, loan_id=loan_id)
        log_activity(user["id"], "payment_completed", f"Fake payment for loan #{loan_id}")
        flash("Demo payment completed successfully.", "success")
        return redirect(url_for("dashboard"))

    return render_template("payment.html", loan=loan)


@app.route("/data")
@login_required
def get_data():
    user = current_user()
    payload = get_dashboard_payload(user)
    return jsonify(payload["chart_data"])


@app.route("/simulate", methods=["POST"])
@login_required
def simulate():
    data = request.get_json(silent=True) or {}
    score = float(data.get("score", 650) or 650)
    income = float(data.get("income", 50000) or 50000)
    loan = float(data.get("loan", 20000) or 20000)
    experience = float(data.get("experience", 2) or 2)
    loan_term_months = int(data.get("loan_term_months", DEFAULT_LOAN_TERM_MONTHS) or DEFAULT_LOAN_TERM_MONTHS)

    prediction = predict_credit_risk(
        age=30,
        income=income,
        loan=loan,
        score=score,
        marital_status="single",
        education="bachelor",
        dependents=0,
        experience=experience,
        loan_term_months=loan_term_months,
    )

    return jsonify(
        {
            "result": prediction["result"],
            "approval_probability": prediction["approval_probability"],
            "risk_score": prediction["risk_score"],
            "risk_label": risk_label(prediction["risk_score"]),
        }
    )


@app.route("/chat", methods=["POST"])
@login_required
def chat():
    data = request.get_json(silent=True) or {}
    message = str(data.get("message", "")).lower()
    score = float(data.get("score", 0) or 0)
    income = float(data.get("income", 0) or 0)
    loan = float(data.get("loan", 0) or 0)

    if "reject" in message or "decline" in message:
        reply = "The most common rejection drivers are low credit score, high loan-to-income ratio, and weak employment stability."
    elif "payment" in message or "due" in message:
        reply = "Open the loan wallet on your customer dashboard to see payment due time, balance, and the demo payment button."
    elif "credited" in message or "money" in message or "take loan" in message:
        reply = "If your application is approved, use Take Loan in the customer loan wallet. The app will show a credited message and payment schedule."
    elif "improve" in message:
        safe_loan = int(max(income * 0.45, 0))
        reply = f"Improve the score above 700, keep the requested loan near {safe_loan}, and add stable income proof."
    elif "safe" in message or "loan" in message:
        safe_loan = int(max(income * 0.45, 0))
        reply = f"A conservative loan range for this income is around {safe_loan}, assuming low existing debt."
    elif score < 600:
        reply = "The score is below the safer approval band. Raise it above 650 before requesting a large loan."
    elif loan > income * 0.7:
        reply = "The requested loan is heavy compared with income. Reducing the amount should improve approval probability."
    else:
        reply = "This profile is moderate to strong. Keep credit score high and loan size below half of annual income."

    return jsonify({"reply": reply})


@app.route("/api/health")
def health():
    model = load_credit_model()
    return jsonify(
        {
            "status": "ok",
            "database": database_label(),
            "model_loaded": model is not None,
            "model_file": os.path.exists(MODEL_FILE),
        }
    )


def send_approval_email(user_email, user_name, loan_amount):
    sender_email = os.environ.get("EMAIL_SENDER")
    sender_password = os.environ.get("EMAIL_APP_PASSWORD")

    if not sender_email or not sender_password or not user_email:
        return False

    msg = MIMEMultipart()
    msg["From"] = f"CrediSense AI <{sender_email}>"
    msg["To"] = user_email
    msg["Subject"] = "Loan Pre-Approved - CrediSense AI Demo"

    body = f"""
Hello {user_name},

Your loan application for {loan_amount} has been pre-approved by the CrediSense AI risk engine.

This is a simulated academic demo notification.

Best regards,
CrediSense AI
"""
    msg.attach(MIMEText(body, "plain"))

    try:
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(sender_email, sender_password)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as exc:
        print(f"Failed to send email: {exc}")
        return False


init_database()


if __name__ == "__main__":
    local_port = int(os.environ.get("PORT", 5000))
    local_debug = os.environ.get("FLASK_DEBUG", "1") == "1"
    print("Starting CrediSense AI local server")
    print(f"Open http://127.0.0.1:{local_port}")
    print("Flask serves both frontend templates and backend routes.")
    print("Use gunicorn app:app only on Render/Linux, not on Windows.")
    app.run(host="127.0.0.1", port=local_port, debug=local_debug)
