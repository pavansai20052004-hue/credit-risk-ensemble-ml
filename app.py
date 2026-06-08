import json
import math
import os
import secrets
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from functools import wraps

from flask import (
    abort,
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
OPEN_REVIEW_STATUSES = {"needs_review", "pre_approved"}
FINAL_REJECTION_STATUS = "declined"
STATUS_LABELS = {
    "needs_review": "Needs Review",
    "pre_approved": "AI Pre-Approved",
    "approved_by_officer": "Officer Approved",
    FINAL_REJECTION_STATUS: "Declined",
    "loan_disbursed": "Loan Disbursed",
}
STATUS_TONES = {
    "needs_review": "neutral",
    "pre_approved": "approved",
    "approved_by_officer": "approved",
    FINAL_REJECTION_STATUS: "rejected",
    "loan_disbursed": "approved",
}

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

GOVERNANCE_FEATURES = [
    {
        "key": "credit_score",
        "label": "Credit score",
        "bins": [0, 600, 660, 720, 780, 1000],
    },
    {
        "key": "income",
        "label": "Income",
        "bins": [0, 25000, 50000, 75000, 100000, 1000000000],
    },
    {
        "key": "loan_amount",
        "label": "Loan amount",
        "bins": [0, 10000, 25000, 50000, 100000, 1000000000],
    },
    {
        "key": "risk_score",
        "label": "Default risk",
        "bins": [0, 20, 40, 60, 80, 101],
    },
]

EDUCATION_MODEL_MAP = {
    "graduate": "bachelor",
    "non-graduate": "high_school",
    "high_school": "high_school",
    "bachelor": "bachelor",
    "master": "master",
}


app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "0") == "1",
    JSON_SORT_KEYS=False,
)

_model_loaded = False
_credit_model = None


def get_csrf_token():
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


@app.before_request
def enforce_csrf_protection():
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return None

    expected_token = session.get("_csrf_token")
    supplied_token = (
        request.form.get("_csrf_token")
        or request.headers.get("X-CSRFToken")
        or request.headers.get("X-CSRF-Token")
    )
    if not expected_token or not supplied_token or not secrets.compare_digest(expected_token, supplied_token):
        abort(400, description="Invalid or missing CSRF token.")
    return None


@app.after_request
def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "script-src 'self' https://cdn.jsdelivr.net https://unpkg.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'self'",
    )
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(), geolocation=(), payment=(), usb=()",
    )
    if app.config["SESSION_COOKIE_SECURE"]:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


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


def is_today_iso(value):
    parsed = parse_iso(value)
    if not parsed:
        return False
    return parsed.astimezone(timezone.utc).date() == datetime.now(timezone.utc).date()


def format_short_datetime(value):
    parsed = parse_iso(value)
    if not parsed:
        return ""
    return parsed.astimezone(timezone.utc).strftime("%d %b %Y")


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
    result = str(result or "").strip().title()
    risk_score = float(risk_score or 0)
    if result == "Rejected":
        return FINAL_REJECTION_STATUS
    if result == "Approved" and risk_score <= 35:
        return "pre_approved"
    if result == "Approved":
        return "needs_review"
    return FINAL_REJECTION_STATUS


def status_key(status):
    value = str(status or "").strip().lower().replace("-", "_").replace(" ", "_")
    return value


def normalize_prediction_status(status, result=None, risk_score=None):
    value = status_key(status)
    aliases = {
        "review": "needs_review",
        "under_review": "needs_review",
        "in_review": "needs_review",
        "pending": "needs_review",
        "pending_review": "needs_review",
        "awaiting_review": "needs_review",
        "approved": "approved_by_officer",
        "officer_approved": "approved_by_officer",
        "rejected": FINAL_REJECTION_STATUS,
        "denied": FINAL_REJECTION_STATUS,
    }
    value = aliases.get(value, value)
    normalized_result = str(result or "").strip().title()
    if normalized_result == "Rejected" and value in OPEN_REVIEW_STATUSES:
        return FINAL_REJECTION_STATUS
    allowed = OPEN_REVIEW_STATUSES | {"approved_by_officer", FINAL_REJECTION_STATUS, "loan_disbursed"}
    if value in allowed:
        return value
    return decision_status(result or "Rejected", risk_score or 100)


def prediction_status_label(status):
    return STATUS_LABELS.get(status, status.replace("_", " ").title())


def is_review_open(item):
    return normalize_prediction_status(item.get("status"), item.get("result"), item.get("risk_score")) in OPEN_REVIEW_STATUSES


def import_csv_history(conn):
    if not os.path.exists(DATA_FILE):
        return

    demo_user = conn.execute("SELECT id FROM users WHERE email = ?", ("customer@credisense.ai",)).fetchone()
    fallback_user_id = demo_user["id"] if demo_user else 1

    try:
        import pandas as pd

        df = pd.read_csv(DATA_FILE)
    except ImportError:
        print("pandas is not installed. Skipping optional CSV history import.")
        return
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

            CREATE TABLE IF NOT EXISTS payment_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                loan_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                balance_after REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'completed',
                provider TEXT NOT NULL DEFAULT 'demo-card',
                created_at TEXT NOT NULL,
                FOREIGN KEY (loan_id) REFERENCES loan_accounts(id),
                FOREIGN KEY (user_id) REFERENCES users(id)
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
            "provider": "TEXT NOT NULL DEFAULT 'provider'",
            "created_at": "TEXT",
        }
        for column, sql in notification_columns.items():
            add_column_if_missing(conn, "notifications", column, sql)

        payment_columns = {
            "loan_id": "INTEGER",
            "user_id": "INTEGER",
            "amount": "REAL NOT NULL DEFAULT 0",
            "balance_after": "REAL NOT NULL DEFAULT 0",
            "status": "TEXT NOT NULL DEFAULT 'completed'",
            "provider": "TEXT NOT NULL DEFAULT 'demo-card'",
            "created_at": "TEXT",
        }
        for column, sql in payment_columns.items():
            add_column_if_missing(conn, "payment_transactions", column, sql)

        conn.execute("UPDATE users SET password_hash = password WHERE password_hash IS NULL AND password IS NOT NULL")
        conn.execute("UPDATE users SET role = 'customer' WHERE role IS NULL OR role = ''")
        conn.execute("UPDATE users SET plan = 'Starter' WHERE plan IS NULL OR plan = ''")
        conn.execute("UPDATE users SET created_at = ? WHERE created_at IS NULL OR created_at = ''", (now_iso(),))
        conn.execute("UPDATE predictions SET status = 'needs_review' WHERE status IS NULL OR status = ''")
        conn.execute("UPDATE predictions SET status = 'needs_review' WHERE lower(replace(replace(coalesce(status, ''), ' ', '_'), '-', '_')) IN ('review', 'under_review', 'in_review', 'pending', 'pending_review', 'awaiting_review')")
        conn.execute("UPDATE predictions SET status = 'approved_by_officer' WHERE lower(replace(replace(coalesce(status, ''), ' ', '_'), '-', '_')) IN ('approved', 'officer_approved')")
        conn.execute("UPDATE predictions SET status = 'declined' WHERE lower(replace(replace(coalesce(status, ''), ' ', '_'), '-', '_')) IN ('rejected', 'denied')")
        conn.execute("UPDATE predictions SET status = 'declined' WHERE lower(trim(coalesce(result, ''))) = 'rejected' AND lower(replace(replace(coalesce(status, ''), ' ', '_'), '-', '_')) IN ('needs_review', 'pre_approved', 'review', 'under_review', 'in_review', 'pending', 'pending_review', 'awaiting_review')")
        conn.execute("UPDATE predictions SET result = 'Approved' WHERE status IN ('approved_by_officer', 'loan_disbursed') AND result <> 'Approved'")
        conn.execute("UPDATE predictions SET result = 'Rejected' WHERE status = 'declined' AND result <> 'Rejected'")
        conn.execute("UPDATE predictions SET created_at = ? WHERE created_at IS NULL OR created_at = ''", (now_iso(),))

        seed_user(conn, "Customer Account", "customer@credisense.ai", "Customer@123", "customer", "Starter")
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
            CREATE INDEX IF NOT EXISTS idx_payment_transactions_loan_id ON payment_transactions(loan_id);
            CREATE INDEX IF NOT EXISTS idx_payment_transactions_user_id ON payment_transactions(user_id);
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


def public_applicant_name(item):
    applicant_name = (item.get("applicant_name") or "").strip()
    if applicant_name.lower().startswith("demo customer"):
        reference = item.get("prediction_id") or item.get("id") or ""
        return f"Applicant #{reference}" if reference else "Applicant"
    return applicant_name or "Applicant"


def public_customer_name(item):
    applicant_name = public_applicant_name(item)
    owner_name = (item.get("owner_name") or "").strip()
    owner_email = (item.get("owner_email") or "").strip().lower()

    if owner_email == "customer@credisense.ai":
        return applicant_name
    if owner_name and owner_name.lower() not in {"demo customer", "customer account"}:
        return owner_name
    return applicant_name or owner_name or "Customer"


def customer_contact_label(item):
    email = (item.get("applicant_email") or item.get("owner_email") or "").strip()
    if email:
        return email
    phone = (item.get("phone") or "").strip()
    if phone:
        return mask_phone(phone)
    return ""


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


def email_configured():
    return bool(os.environ.get("EMAIL_SENDER") and os.environ.get("EMAIL_APP_PASSWORD"))


def sms_configured():
    return bool(
        os.environ.get("TWILIO_ACCOUNT_SID")
        and os.environ.get("TWILIO_AUTH_TOKEN")
        and os.environ.get("TWILIO_FROM_PHONE")
    )


def send_email_notification(user_id, email, subject, body, prediction_id=None, loan_id=None):
    recipient = (email or "").strip()
    provider = "smtp"

    if not recipient:
        status = "missing_recipient"
        record_notification(user_id, "email", "", subject, status, provider, prediction_id, loan_id)
        return False

    sender_email = os.environ.get("EMAIL_SENDER")
    sender_password = os.environ.get("EMAIL_APP_PASSWORD")
    smtp_host = os.environ.get("EMAIL_SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("EMAIL_SMTP_PORT", "587"))

    if not sender_email or not sender_password:
        status = "not_configured"
        record_notification(user_id, "email", recipient, subject, status, provider, prediction_id, loan_id)
        return False

    msg = MIMEMultipart()
    msg["From"] = f"CrediSense AI <{sender_email}>"
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(sender_email, sender_password)
            server.send_message(msg)
        status = "sent"
    except Exception as exc:
        status = "failed"
        print(f"Email failed for {recipient}: {exc}")

    record_notification(user_id, "email", recipient, subject, status, provider, prediction_id, loan_id)
    return status == "sent"


def send_sms_notification(user_id, phone, message, prediction_id=None, loan_id=None):
    recipient = (phone or "").strip()
    provider = "twilio"

    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
    from_phone = os.environ.get("TWILIO_FROM_PHONE")

    if not recipient:
        status = "missing_recipient"
        record_notification(user_id, "sms", "", message, status, provider, prediction_id, loan_id)
        return False

    if not account_sid or not auth_token or not from_phone:
        status = "not_configured"
        record_notification(user_id, "sms", recipient, message, status, provider, prediction_id, loan_id)
        return False

    try:
        from twilio.rest import Client

        client = Client(account_sid, auth_token)
        twilio_message = client.messages.create(body=message, from_=from_phone, to=recipient)
        status = getattr(twilio_message, "status", "sent")
    except Exception as exc:
        status = "failed"
        print(f"SMS failed for {mask_phone(recipient)}: {exc}")

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
        import joblib

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

    try:
        import pandas as pd
    except ImportError:
        print("pandas is not installed. Falling back to policy scoring.")
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


def clamp(value, lower, upper):
    return max(lower, min(upper, value))


def calculate_monthly_payment(principal, annual_rate, months):
    principal = float(principal or 0)
    months = int(months or DEFAULT_LOAN_TERM_MONTHS)
    if principal <= 0 or months <= 0:
        return 0

    monthly_rate = float(annual_rate or 0) / 100 / 12
    if monthly_rate <= 0:
        return round(principal / months, 2)

    payment = principal * monthly_rate * (1 + monthly_rate) ** months
    payment /= (1 + monthly_rate) ** months - 1
    return round(payment, 2)


def build_policy_flags(score, income, loan, experience, loan_term_months, risk_score, payment_to_income):
    loan_to_income = loan / max(income, 1)
    flags = []

    if score < 620:
        flags.append(
            {
                "label": "Credit floor breach",
                "detail": "Credit score is below the safer underwriting band.",
                "tone": "rejected",
                "icon": "triangle-alert",
            }
        )
    elif score < 680:
        flags.append(
            {
                "label": "Thin credit buffer",
                "detail": "Credit score is acceptable but has limited room for adverse movement.",
                "tone": "neutral",
                "icon": "circle-alert",
            }
        )

    if loan_to_income > 1.1:
        flags.append(
            {
                "label": "High exposure request",
                "detail": "Requested loan is above annual income and needs senior review.",
                "tone": "rejected",
                "icon": "scale",
            }
        )
    elif loan_to_income > 0.65:
        flags.append(
            {
                "label": "Elevated loan-to-income",
                "detail": "Requested exposure is high relative to declared annual income.",
                "tone": "neutral",
                "icon": "scale",
            }
        )

    if payment_to_income > 40:
        flags.append(
            {
                "label": "Affordability stress",
                "detail": "Estimated monthly payment consumes more than 40% of monthly income.",
                "tone": "rejected",
                "icon": "badge-dollar-sign",
            }
        )
    elif payment_to_income > 28:
        flags.append(
            {
                "label": "Affordability watch",
                "detail": "Payment load should be verified against existing obligations.",
                "tone": "neutral",
                "icon": "badge-dollar-sign",
            }
        )

    if experience < 2:
        flags.append(
            {
                "label": "Employment seasoning",
                "detail": "Employment history is below the preferred two-year stability window.",
                "tone": "neutral",
                "icon": "briefcase-business",
            }
        )

    if loan_term_months > 72:
        flags.append(
            {
                "label": "Long-tenor sensitivity",
                "detail": "Long repayment term increases exposure to income or rate shocks.",
                "tone": "neutral",
                "icon": "calendar-clock",
            }
        )

    if risk_score >= 65:
        flags.append(
            {
                "label": "Default-risk escalation",
                "detail": "Modelled default risk exceeds the senior review threshold.",
                "tone": "rejected",
                "icon": "shield-alert",
            }
        )

    if not flags:
        flags.append(
            {
                "label": "Policy clear",
                "detail": "Profile clears core credit, exposure, affordability, and stability checks.",
                "tone": "approved",
                "icon": "shield-check",
            }
        )

    return flags


def decision_grade(risk_score, approval_probability, payment_to_income):
    if risk_score <= 20 and approval_probability >= 78 and payment_to_income <= 22:
        return "A"
    if risk_score <= 35 and approval_probability >= 65 and payment_to_income <= 28:
        return "B"
    if risk_score <= 55 and approval_probability >= 45 and payment_to_income <= 36:
        return "C"
    if risk_score <= 72 and payment_to_income <= 44:
        return "D"
    return "E"


def grade_tone(grade):
    if grade in {"A", "B"}:
        return "approved"
    if grade == "C":
        return "warning"
    return "rejected"


def estimate_risk_apr(score, risk_score):
    score = float(score or 0)
    risk_score = float(risk_score or 0)
    return clamp(
        7.4 + risk_score * 0.16 + max(0, 680 - score) * 0.015 - max(0, score - 740) * 0.01,
        6.5,
        29.9,
    )


def format_money(value):
    return f"${float(value or 0):,.0f}"


def counterfactual_scenario(label, score, income, loan, experience, loan_term_months):
    score = float(score or 0)
    income = float(income or 0)
    loan = float(loan or 0)
    experience = float(experience or 0)
    loan_term_months = int(loan_term_months or DEFAULT_LOAN_TERM_MONTHS)
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
    approval_probability = round(float(prediction["approval_probability"]), 2)
    risk_score = round(float(prediction["risk_score"]), 2)
    apr = estimate_risk_apr(score, risk_score)
    monthly_payment = calculate_monthly_payment(loan, apr, loan_term_months)
    payment_to_income = round((monthly_payment / max(income / 12, 1)) * 100, 1)
    grade = decision_grade(risk_score, approval_probability, payment_to_income)

    return {
        "label": label,
        "result": prediction["result"],
        "approval_probability": approval_probability,
        "risk_score": risk_score,
        "risk_label": risk_label(risk_score),
        "grade": grade,
        "grade_tone": grade_tone(grade),
        "score": int(round(score)),
        "income": round(income, 2),
        "loan_amount": round(loan, 2),
        "loan_term_months": loan_term_months,
        "experience": round(experience, 1),
        "estimated_apr": round(apr, 2),
        "monthly_payment": monthly_payment,
        "payment_to_income": payment_to_income,
    }


def build_approval_rescue_plan(
    score,
    income,
    loan,
    experience,
    loan_term_months,
    approval_probability,
    risk_score,
    result,
    payment_to_income,
):
    score = float(score or 0)
    income = float(income or 0)
    loan = float(loan or 0)
    experience = float(experience or 0)
    loan_term_months = int(loan_term_months or DEFAULT_LOAN_TERM_MONTHS)
    approval_probability = float(approval_probability or 0)
    risk_score = float(risk_score or 0)
    payment_to_income = float(payment_to_income or 0)

    if risk_score >= 65 or score < 620:
        target_ratio = 0.35
        target_score = max(score, 680)
    elif risk_score >= 55:
        target_ratio = 0.45
        target_score = max(score, 700)
    elif risk_score >= 35:
        target_ratio = 0.55
        target_score = max(score, 700 if score < 680 else score)
    else:
        target_ratio = 0.7
        target_score = max(score, 700 if score < 650 else score)

    target_loan = loan
    if income > 0 and loan > 0:
        target_loan = min(loan, round((income * target_ratio) / 500) * 500)
        target_loan = max(1000, target_loan) if loan >= 1000 else target_loan

    target_term = loan_term_months
    if payment_to_income > 36 and loan_term_months < 60:
        target_term = min(60, max(48, loan_term_months + 12))
    elif payment_to_income > 28 and loan_term_months < 48:
        target_term = 48

    target_experience = max(experience, 2) if experience < 2 else experience
    candidate_profiles = [
        (target_score, target_loan, target_term, target_experience),
        (max(target_score, 720), min(target_loan, round(income * 0.4 / 500) * 500) if income else target_loan, min(max(target_term, 48), 60), target_experience),
        (max(target_score, 740), min(target_loan, round(income * 0.3 / 500) * 500) if income else target_loan, min(max(target_term, 48), 60), target_experience),
    ]

    best_path = None
    for candidate_score, candidate_loan, candidate_term, candidate_experience in candidate_profiles:
        scenario = counterfactual_scenario(
            "Rescue path",
            candidate_score,
            income,
            max(0, candidate_loan),
            candidate_experience,
            candidate_term,
        )
        if (
            scenario["result"] == "Approved"
            and scenario["risk_score"] <= 55
            and scenario["payment_to_income"] <= 36
        ):
            best_path = scenario
            break
        if not best_path or (
            scenario["approval_probability"],
            -scenario["risk_score"],
            -scenario["payment_to_income"],
        ) > (
            best_path["approval_probability"],
            -best_path["risk_score"],
            -best_path["payment_to_income"],
        ):
            best_path = scenario

    current = {
        "label": "Current request",
        "result": result,
        "approval_probability": round(approval_probability, 2),
        "risk_score": round(risk_score, 2),
        "risk_label": risk_label(risk_score),
        "grade": decision_grade(risk_score, approval_probability, payment_to_income),
        "grade_tone": grade_tone(decision_grade(risk_score, approval_probability, payment_to_income)),
        "score": int(round(score)),
        "income": round(income, 2),
        "loan_amount": round(loan, 2),
        "loan_term_months": loan_term_months,
        "experience": round(experience, 1),
        "payment_to_income": round(payment_to_income, 1),
    }
    conservative_loan = min(
        best_path["loan_amount"],
        round((income * 0.25) / 500) * 500 if income else best_path["loan_amount"],
    )
    conservative = counterfactual_scenario(
        "Low-stress offer",
        max(best_path["score"], 720),
        income,
        max(0, conservative_loan),
        best_path["experience"],
        min(max(best_path["loan_term_months"], 36), 60),
    )

    approval_lift = round(best_path["approval_probability"] - approval_probability, 2)
    risk_reduction = round(risk_score - best_path["risk_score"], 2)
    readiness_score = int(
        round(
            clamp(
                best_path["approval_probability"] * 0.55
                + (100 - best_path["risk_score"]) * 0.35
                + min(best_path["score"] / 900 * 10, 10),
                0,
                100,
            )
        )
    )

    steps = []
    score_delta = max(0, best_path["score"] - score)
    if score_delta:
        steps.append(
            {
                "label": "Credit lift",
                "current": str(int(round(score))),
                "target": str(best_path["score"]),
                "detail": f"Raise the score by about {int(round(score_delta))} points before requesting a larger limit.",
                "tone": "warning" if score_delta <= 50 else "rejected",
                "icon": "trending-up",
            }
        )

    loan_delta = max(0, loan - best_path["loan_amount"])
    if loan_delta:
        steps.append(
            {
                "label": "Resize request",
                "current": format_money(loan),
                "target": format_money(best_path["loan_amount"]),
                "detail": f"Cut exposure by {format_money(loan_delta)} to move closer to policy appetite.",
                "tone": "warning",
                "icon": "scissors",
            }
        )

    if best_path["loan_term_months"] != loan_term_months:
        steps.append(
            {
                "label": "Payment design",
                "current": f"{loan_term_months} mo",
                "target": f"{best_path['loan_term_months']} mo",
                "detail": "Use a term that keeps payment load inside the affordable band without creating excessive tenor risk.",
                "tone": "neutral",
                "icon": "calendar-clock",
            }
        )

    if experience < 2:
        steps.append(
            {
                "label": "Stability proof",
                "current": f"{round(experience, 1)} yr",
                "target": "2 yrs",
                "detail": "Add employer verification, a co-applicant, or income continuity evidence.",
                "tone": "neutral",
                "icon": "briefcase-business",
            }
        )

    steps.append(
        {
            "label": "Verification pack",
            "current": "Basic KYC",
            "target": "Income, bank, obligations",
            "detail": "Attach bank statements, income proof, and existing debt obligations to reduce manual review friction.",
            "tone": "approved" if best_path["result"] == "Approved" else "neutral",
            "icon": "folder-check",
        }
    )

    blockers = []
    if score < 680:
        blockers.append("Credit score below prime band")
    if income and loan > income * target_ratio:
        blockers.append("Requested exposure above rescue appetite")
    if payment_to_income > 28:
        blockers.append("Monthly payment pressure")
    if risk_score >= 55:
        blockers.append("Default-risk escalation")
    if experience < 2:
        blockers.append("Short employment history")
    if not blockers:
        blockers.append("Maintain verification discipline")

    if result == "Approved" and risk_score <= 35:
        status = "Protection plan"
        tone = "approved"
        summary = "This file is already in a strong band. The plan protects pricing and keeps the offer audit-ready."
    elif best_path["result"] == "Approved":
        status = "Rescue path found"
        tone = "warning" if risk_score >= 35 else "approved"
        summary = "A policy-fit path is available by adjusting the riskiest levers before final review."
    else:
        status = "Manual recovery needed"
        tone = "rejected"
        summary = "The profile remains outside automated appetite even after mitigation, so a human exception review is required."

    return {
        "status": status,
        "tone": tone,
        "summary": summary,
        "readiness_score": readiness_score,
        "approval_lift": approval_lift,
        "risk_reduction": risk_reduction,
        "target_score": best_path["score"],
        "target_loan": best_path["loan_amount"],
        "target_term": best_path["loan_term_months"],
        "target_payment_to_income": best_path["payment_to_income"],
        "steps": steps[:5],
        "blockers": blockers[:4],
        "scenarios": [current, best_path, conservative],
    }


def build_decision_intelligence(
    score,
    income,
    loan,
    experience,
    loan_term_months,
    approval_probability,
    risk_score,
    result,
    include_rescue_plan=True,
):
    income = float(income or 0)
    loan = float(loan or 0)
    score = float(score or 0)
    experience = float(experience or 0)
    loan_term_months = int(loan_term_months or DEFAULT_LOAN_TERM_MONTHS)
    approval_probability = float(approval_probability or 0)
    risk_score = float(risk_score or 0)

    loan_to_income = loan / max(income, 1)
    estimated_apr = estimate_risk_apr(score, risk_score)
    monthly_payment = calculate_monthly_payment(loan, estimated_apr, loan_term_months)
    payment_to_income = (monthly_payment / max(income / 12, 1)) * 100
    loss_given_default = clamp(40 + max(0, loan_to_income - 0.55) * 18 + (8 if score < 640 else 0), 35, 68)
    expected_loss = loan * (risk_score / 100) * (loss_given_default / 100)

    safe_lti = 0.85 if risk_score < 25 else 0.65 if risk_score < 45 else 0.45 if risk_score < 60 else 0.25
    if score < 620:
        safe_lti -= 0.1
    if experience < 2:
        safe_lti -= 0.05
    safe_lti = clamp(safe_lti, 0.15, 0.9)
    recommended_limit = round(max(0, income * safe_lti) / 500) * 500 if income else 0
    recommended_amount = min(loan, recommended_limit) if recommended_limit else 0

    grade = decision_grade(risk_score, approval_probability, payment_to_income)
    if result == "Rejected":
        action = "Decline until mitigation is documented"
        action_tone = "rejected"
        review_sla = "Reapply after improvement"
    elif risk_score <= 30 and payment_to_income <= 25:
        action = "Fast-track with standard verification"
        action_tone = "approved"
        review_sla = "Same day"
    elif risk_score <= 55 and payment_to_income <= 35:
        action = "Officer review with income verification"
        action_tone = "neutral"
        review_sla = "24 hours"
    else:
        action = "Senior review before any offer"
        action_tone = "warning"
        review_sla = "48 hours"

    safeguards = []
    if recommended_limit and loan > recommended_limit:
        safeguards.append(f"Reprice or resize offer near ${recommended_limit:,.0f}.")
    if payment_to_income > 28:
        safeguards.append("Verify bank statements and existing monthly obligations.")
    if risk_score >= 55:
        safeguards.append("Request collateral, guarantor support, or a smaller exposure.")
    if score < 680:
        safeguards.append("Document credit-score improvement path before increasing limits.")
    if not safeguards:
        safeguards.append("Maintain automated audit trail and standard KYC verification.")

    intelligence = {
        "grade": grade,
        "grade_tone": grade_tone(grade),
        "loan_to_income": round(loan_to_income * 100, 1),
        "payment_to_income": round(payment_to_income, 1),
        "estimated_apr": round(estimated_apr, 2),
        "monthly_payment": monthly_payment,
        "loss_given_default": round(loss_given_default, 1),
        "expected_loss": round(expected_loss, 2),
        "recommended_limit": recommended_limit,
        "recommended_amount": round(recommended_amount, 2),
        "action": action,
        "action_tone": action_tone,
        "review_sla": review_sla,
        "policy_flags": build_policy_flags(
            score,
            income,
            loan,
            experience,
            loan_term_months,
            risk_score,
            payment_to_income,
        ),
        "safeguards": safeguards[:4],
    }
    if include_rescue_plan:
        intelligence["rescue_plan"] = build_approval_rescue_plan(
            score,
            income,
            loan,
            experience,
            loan_term_months,
            approval_probability,
            risk_score,
            result,
            payment_to_income,
        )
    return intelligence


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


def coerce_float(value, default=0, min_value=None, max_value=None):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = float(default)
    if min_value is not None:
        parsed = max(float(min_value), parsed)
    if max_value is not None:
        parsed = min(float(max_value), parsed)
    return parsed


def coerce_int(value, default=0, min_value=None, max_value=None):
    return int(coerce_float(value, default, min_value, max_value))


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
        "csrf_token": get_csrf_token,
    }


def scoped_prediction_query(user):
    base_query = """
        SELECT
            p.*,
            u.name AS owner_name,
            u.email AS owner_email,
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
    item["age"] = int(item.get("age") or 0)
    item["income"] = float(item.get("income") or 0)
    item["loan_amount"] = float(item.get("loan_amount") or 0)
    item["loan_term_months"] = int(item.get("loan_term_months") or DEFAULT_LOAN_TERM_MONTHS)
    item["risk_score"] = float(item.get("risk_score") or 0)
    item["approval_probability"] = float(item.get("approval_probability") or 0)
    item["ai_score"] = float(item.get("ai_score") or 0)
    item["credit_score"] = float(item.get("credit_score") or 0)
    item["employment_years"] = int(item.get("employment_years") or 0)
    item["dependents"] = int(item.get("dependents") or 0)
    item["risk_label"] = risk_label(item["risk_score"])
    item["status"] = normalize_prediction_status(item.get("status"), item.get("result"), item["risk_score"])
    item["status_label"] = prediction_status_label(item["status"])
    item["status_tone"] = STATUS_TONES.get(item["status"], "neutral")
    item["applicant_label"] = public_applicant_name(item)
    item["owner_label"] = public_customer_name(item)
    item["owner_detail"] = customer_contact_label(item)
    item["reviewer_name"] = item.get("reviewer_name") or ""
    item["created_at_label"] = format_short_datetime(item.get("created_at"))
    item["reviewed_at_label"] = format_short_datetime(item.get("reviewed_at"))
    item["is_today"] = is_today_iso(item.get("created_at"))
    if item.get("reviewed_by"):
        item["review_owner_label"] = item["reviewer_name"] or "Review officer"
    elif item.get("status") == FINAL_REJECTION_STATUS:
        item["review_owner_label"] = "AI declined"
    elif item.get("status") == "loan_disbursed":
        item["review_owner_label"] = "Loan disbursed"
    elif item.get("status") == "approved_by_officer":
        item["review_owner_label"] = "Officer approved"
    elif item.get("status") == "pre_approved":
        item["review_owner_label"] = "AI pre-approval"
    else:
        item["review_owner_label"] = "Awaiting officer review"
    try:
        item["explain"] = json.loads(item.get("explain_json") or "[]")
    except json.JSONDecodeError:
        item["explain"] = []
    try:
        item["suggestions"] = json.loads(item.get("suggestions_json") or "[]")
    except json.JSONDecodeError:
        item["suggestions"] = []
    item["intelligence"] = build_decision_intelligence(
        item["credit_score"],
        item["income"],
        item["loan_amount"],
        item["employment_years"],
        item["loan_term_months"],
        item["approval_probability"],
        item["risk_score"],
        item.get("result"),
        include_rescue_plan=False,
    )
    return item


def clean_loan(row):
    item = dict(row)
    item["principal_amount"] = float(item.get("principal_amount") or 0)
    item["disbursed_amount"] = float(item.get("disbursed_amount") or 0)
    item["balance_amount"] = float(item.get("balance_amount") or 0)
    item["monthly_payment"] = float(item.get("monthly_payment") or 0)
    item["interest_rate"] = float(item.get("interest_rate") or 0)
    item["applicant_label"] = public_applicant_name(item)
    item["owner_label"] = public_customer_name(item)
    item["owner_detail"] = customer_contact_label(item)
    item["total_paid"] = float(item.get("total_paid") or 0)
    item["payments_made"] = int(item.get("payments_made") or 0)
    item["payment_due_amount"] = min(item["monthly_payment"], item["balance_amount"])
    item["days_until_due"] = days_until(item.get("due_date"))
    item["status_label"] = (item.get("status") or "active").replace("_", " ").title()
    item["last_payment_at_label"] = format_short_datetime(item.get("last_payment_at") or item.get("last_payment_recorded_at"))
    payment_status = item.get("payment_status") or "pending"
    if item["balance_amount"] <= 0 or item.get("status") == "closed":
        item["payment_status_label"] = "Paid Off"
        item["payment_status_tone"] = "approved"
    elif payment_status == "paid":
        item["payment_status_label"] = "Paid"
        item["payment_status_tone"] = "approved"
    else:
        item["payment_status_label"] = payment_status.replace("_", " ").title()
        item["payment_status_tone"] = "neutral"
    return item


def fetch_customer_loans(user_id):
    rows = fetch_all(
        """
        SELECT
            l.*,
            p.applicant_name,
            p.result,
            p.status AS prediction_status,
            COALESCE(pay.total_paid, 0) AS total_paid,
            COALESCE(pay.payments_made, 0) AS payments_made,
            pay.last_payment_recorded_at
        FROM loan_accounts l
        JOIN predictions p ON p.id = l.prediction_id
        LEFT JOIN (
            SELECT
                loan_id,
                SUM(amount) AS total_paid,
                COUNT(*) AS payments_made,
                MAX(created_at) AS last_payment_recorded_at
            FROM payment_transactions
            WHERE status = 'completed'
            GROUP BY loan_id
        ) pay ON pay.loan_id = l.id
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
    return prediction.get("result") == "Approved" and status == "approved_by_officer"


def customer_loan_offers(user_id):
    rows = fetch_all(
        """
        SELECT p.*
        FROM predictions p
        LEFT JOIN loan_accounts l ON l.prediction_id = p.id
        WHERE p.user_id = ? AND p.result = 'Approved' AND p.status = 'approved_by_officer' AND l.id IS NULL
        ORDER BY p.created_at DESC, p.id DESC
        """,
        (user_id,),
    )
    return [clean_prediction(row) for row in rows]


def status_integrity_issue_count():
    row = fetch_one(
        """
        SELECT COUNT(*) AS total
        FROM predictions
        WHERE lower(trim(coalesce(result, ''))) = 'rejected'
          AND lower(replace(replace(coalesce(status, ''), ' ', '_'), '-', '_')) IN (
              'needs_review', 'pre_approved', 'review', 'under_review', 'in_review',
              'pending', 'pending_review', 'awaiting_review'
          )
        """
    )
    return int(row.get("total") or 0) if row else 0


def notification_readiness_label():
    ready = []
    if email_configured():
        ready.append("Email")
    if sms_configured():
        ready.append("SMS")
    return " + ".join(ready) if ready else "Audit Log"


def build_assurance_checks(review_queue):
    integrity_issues = status_integrity_issue_count()
    model_ready = os.path.exists(MODEL_FILE)
    open_reviews = len(review_queue)
    backend = database_label().title()

    return [
        {
            "label": "Status Integrity",
            "value": "Passed" if integrity_issues == 0 else f"{integrity_issues} Issues",
            "detail": "Rejected decisions stay declined across history and review queues.",
            "tone": "approved" if integrity_issues == 0 else "rejected",
            "icon": "shield-check" if integrity_issues == 0 else "shield-alert",
        },
        {
            "label": "Decision Engine",
            "value": "Model Ready" if model_ready else "Policy Fallback",
            "detail": "ML scoring is available with policy fallback for resilient demos.",
            "tone": "approved" if model_ready else "neutral",
            "icon": "brain-circuit",
        },
        {
            "label": "Data Layer",
            "value": backend,
            "detail": "SQLite locally, PostgreSQL-ready through DATABASE_URL.",
            "tone": "neutral",
            "icon": "database",
        },
        {
            "label": "Ops Queue",
            "value": f"{open_reviews} Open",
            "detail": "Only true review-ready applications remain actionable.",
            "tone": "approved" if open_reviews == 0 else "neutral",
            "icon": "clipboard-check",
        },
        {
            "label": "Notifications",
            "value": notification_readiness_label(),
            "detail": "Every delivery attempt is recorded for audit traceability.",
            "tone": "approved" if email_configured() or sms_configured() else "neutral",
            "icon": "send",
        },
    ]


def percentage(part, total):
    return round((float(part or 0) / total) * 100, 2) if total else 0


def rate_tone(rate, warning_threshold, alert_threshold):
    rate = float(rate or 0)
    if rate >= alert_threshold:
        return "rejected"
    if rate >= warning_threshold:
        return "warning"
    return "approved"


def coverage_tone(coverage, warning_threshold, alert_threshold):
    coverage = float(coverage or 0)
    if coverage <= alert_threshold:
        return "rejected"
    if coverage <= warning_threshold:
        return "warning"
    return "approved"


def split_monitoring_windows(history):
    total = len(history)
    if total < 8:
        return [], history

    recent_count = min(30, max(4, total // 3))
    if total - recent_count < 5:
        recent_count = max(3, total - 5)

    return history[recent_count:], history[:recent_count]


def bucket_index(value, bins):
    value = float(value or 0)
    for index in range(len(bins) - 1):
        upper = bins[index + 1]
        if value < upper or index == len(bins) - 2:
            return index
    return len(bins) - 2


def population_stability_index(reference, current, feature):
    minimum_ready = len(reference) >= 5 and len(current) >= 3
    if not minimum_ready:
        return {
            "label": feature["label"],
            "value": "Insufficient",
            "psi": 0,
            "status": "Building baseline",
            "tone": "neutral",
            "detail": "More scored applications are needed before drift can be measured.",
        }

    bins = feature["bins"]
    reference_counts = [0 for _ in range(len(bins) - 1)]
    current_counts = [0 for _ in range(len(bins) - 1)]

    for item in reference:
        reference_counts[bucket_index(item.get(feature["key"]), bins)] += 1
    for item in current:
        current_counts[bucket_index(item.get(feature["key"]), bins)] += 1

    psi = 0
    for reference_count, current_count in zip(reference_counts, current_counts):
        reference_share = max(reference_count / len(reference), 0.005)
        current_share = max(current_count / len(current), 0.005)
        psi += (current_share - reference_share) * math.log(current_share / reference_share)

    psi = round(max(0, psi), 3)
    if psi >= 0.25:
        status = "Distribution shift"
        tone = "rejected"
        detail = "Recent applications have moved materially away from the baseline sample."
    elif psi >= 0.1:
        status = "Watch"
        tone = "warning"
        detail = "Recent applications show moderate drift and should be reviewed."
    else:
        status = "Stable"
        tone = "approved"
        detail = "Recent applications are aligned with the baseline distribution."

    return {
        "label": feature["label"],
        "value": f"{psi:.3f}",
        "psi": psi,
        "status": status,
        "tone": tone,
        "detail": detail,
    }


def build_drift_monitor(history):
    reference, current = split_monitoring_windows(history)
    metrics = [population_stability_index(reference, current, feature) for feature in GOVERNANCE_FEATURES]
    alert_count = sum(1 for metric in metrics if metric["tone"] == "rejected")
    watch_count = sum(1 for metric in metrics if metric["tone"] == "warning")
    max_psi = max((metric["psi"] for metric in metrics), default=0)

    if not reference or len(current) < 3:
        status = "Baseline pending"
        tone = "neutral"
    elif alert_count:
        status = "Drift alert"
        tone = "rejected"
    elif watch_count:
        status = "Watch"
        tone = "warning"
    else:
        status = "Stable"
        tone = "approved"

    return {
        "status": status,
        "tone": tone,
        "max_psi": round(max_psi, 3),
        "alert_count": alert_count,
        "watch_count": watch_count,
        "baseline_count": len(reference),
        "recent_count": len(current),
        "metrics": metrics,
    }


def age_band(item):
    age = int(item.get("age") or 0)
    if age < 30:
        return "18-29"
    if age < 45:
        return "30-44"
    if age < 60:
        return "45-59"
    return "60+"


def title_value(value):
    return str(value or "Unknown").replace("_", " ").strip().title() or "Unknown"


def build_group_dimension(history, label, group_func):
    groups = {}
    for item in history:
        group = group_func(item)
        groups.setdefault(group, {"total": 0, "approved": 0, "risk_total": 0})
        groups[group]["total"] += 1
        groups[group]["approved"] += 1 if item.get("result") == "Approved" else 0
        groups[group]["risk_total"] += float(item.get("risk_score") or 0)

    qualified = {
        group: {
            "total": values["total"],
            "approval_rate": percentage(values["approved"], values["total"]),
            "avg_risk": round(values["risk_total"] / values["total"], 2),
        }
        for group, values in groups.items()
        if values["total"] >= 2
    }

    if len(qualified) < 2:
        return {
            "label": label,
            "value": "Insufficient",
            "approval_gap": 0,
            "risk_gap": 0,
            "status": "Building sample",
            "tone": "neutral",
            "detail": "At least two populated groups are needed for this monitor.",
        }

    highest = max(qualified.items(), key=lambda pair: pair[1]["approval_rate"])
    lowest = min(qualified.items(), key=lambda pair: pair[1]["approval_rate"])
    high_risk = max(qualified.items(), key=lambda pair: pair[1]["avg_risk"])
    low_risk = min(qualified.items(), key=lambda pair: pair[1]["avg_risk"])
    approval_gap = round(highest[1]["approval_rate"] - lowest[1]["approval_rate"], 2)
    risk_gap = round(high_risk[1]["avg_risk"] - low_risk[1]["avg_risk"], 2)

    if approval_gap >= 30 or risk_gap >= 25:
        status = "Escalate"
        tone = "rejected"
    elif approval_gap >= 18 or risk_gap >= 15:
        status = "Watch"
        tone = "warning"
    else:
        status = "Stable"
        tone = "approved"

    return {
        "label": label,
        "value": f"{approval_gap:.1f} pts",
        "approval_gap": approval_gap,
        "risk_gap": risk_gap,
        "status": status,
        "tone": tone,
        "detail": (
            f"Approval ranges from {lowest[1]['approval_rate']:.1f}% ({lowest[0]}) "
            f"to {highest[1]['approval_rate']:.1f}% ({highest[0]})."
        ),
    }


def build_fairness_proxy_monitor(history):
    dimensions = [
        build_group_dimension(history, "Age band", age_band),
        build_group_dimension(history, "Education", lambda item: title_value(item.get("education"))),
        build_group_dimension(history, "Marital status", lambda item: title_value(item.get("marital_status"))),
    ]
    alert_count = sum(1 for item in dimensions if item["tone"] == "rejected")
    watch_count = sum(1 for item in dimensions if item["tone"] == "warning")
    largest_gap = max((item["approval_gap"] for item in dimensions), default=0)

    if len(history) < 8:
        status = "Sample pending"
        tone = "neutral"
    elif alert_count:
        status = "Fairness review"
        tone = "rejected"
    elif watch_count:
        status = "Watch"
        tone = "warning"
    else:
        status = "Stable"
        tone = "approved"

    return {
        "status": status,
        "tone": tone,
        "largest_gap": round(largest_gap, 2),
        "alert_count": alert_count,
        "watch_count": watch_count,
        "dimensions": dimensions,
    }


def build_data_quality_monitor(history):
    total = len(history)
    if not total:
        return {
            "status": "No samples",
            "tone": "neutral",
            "score": 0,
            "alert_count": 0,
            "watch_count": 0,
            "checks": [],
        }

    invalid_inputs = sum(
        1
        for item in history
        if not (
            18 <= int(item.get("age") or 0) <= 80
            and 300 <= float(item.get("credit_score") or 0) <= 900
            and float(item.get("income") or 0) > 0
            and float(item.get("loan_amount") or 0) > 0
            and 12 <= int(item.get("loan_term_months") or DEFAULT_LOAN_TERM_MONTHS) <= 120
        )
    )
    missing_contact = sum(1 for item in history if not item.get("applicant_email") or not item.get("phone"))
    missing_explainability = sum(1 for item in history if not item.get("explain"))
    affordability_outliers = sum(
        1
        for item in history
        if item.get("intelligence", {}).get("payment_to_income", 0) > 45
    )

    valid_rate = percentage(total - invalid_inputs, total)
    contact_rate = percentage(total - missing_contact, total)
    explainability_rate = percentage(total - missing_explainability, total)
    outlier_rate = percentage(affordability_outliers, total)
    quality_score = round(
        clamp(
            valid_rate * 0.4 + contact_rate * 0.2 + explainability_rate * 0.25 + (100 - outlier_rate) * 0.15,
            0,
            100,
        ),
        2,
    )

    checks = [
        {
            "label": "Input validity",
            "value": f"{valid_rate:.1f}%",
            "tone": coverage_tone(valid_rate, 95, 90),
            "detail": f"{invalid_inputs} records have impossible or missing numeric underwriting inputs.",
        },
        {
            "label": "Contact readiness",
            "value": f"{contact_rate:.1f}%",
            "tone": coverage_tone(contact_rate, 92, 85),
            "detail": "Applications need email and phone for notification traceability.",
        },
        {
            "label": "Explainability coverage",
            "value": f"{explainability_rate:.1f}%",
            "tone": coverage_tone(explainability_rate, 95, 90),
            "detail": "Every scored decision should retain adverse-action style reasons.",
        },
        {
            "label": "Affordability outliers",
            "value": f"{outlier_rate:.1f}%",
            "tone": rate_tone(outlier_rate, 8, 15),
            "detail": f"{affordability_outliers} records exceed the 45% payment-to-income stress threshold.",
        },
    ]
    alert_count = sum(1 for check in checks if check["tone"] == "rejected")
    watch_count = sum(1 for check in checks if check["tone"] == "warning")

    if alert_count:
        status = "Quality alert"
        tone = "rejected"
    elif watch_count:
        status = "Watch"
        tone = "warning"
    else:
        status = "Strong"
        tone = "approved"

    return {
        "status": status,
        "tone": tone,
        "score": quality_score,
        "alert_count": alert_count,
        "watch_count": watch_count,
        "checks": checks,
    }


def build_policy_alignment(history):
    total = len(history)
    high_risk_approved = [
        item for item in history if item.get("result") == "Approved" and float(item.get("risk_score") or 0) >= 65
    ]
    low_risk_declined = [
        item for item in history if item.get("result") == "Rejected" and float(item.get("risk_score") or 0) <= 25
    ]
    open_high_risk = [
        item for item in history if is_review_open(item) and float(item.get("risk_score") or 0) >= 65
    ]
    exception_count = len(high_risk_approved) + len(low_risk_declined)
    exception_rate = percentage(exception_count, total)
    tone = rate_tone(exception_rate, 5, 12)
    if tone == "rejected":
        status = "Exceptions high"
    elif tone == "warning":
        status = "Watch"
    else:
        status = "Aligned"

    return {
        "status": status,
        "tone": tone if total else "neutral",
        "exception_count": exception_count,
        "exception_rate": exception_rate,
        "high_risk_approved": len(high_risk_approved),
        "low_risk_declined": len(low_risk_declined),
        "open_high_risk": len(open_high_risk),
        "checks": [
            {
                "label": "High-risk approvals",
                "value": len(high_risk_approved),
                "tone": "rejected" if high_risk_approved else "approved",
                "detail": "Approved applications with 65% or higher default risk.",
            },
            {
                "label": "Low-risk declines",
                "value": len(low_risk_declined),
                "tone": "warning" if low_risk_declined else "approved",
                "detail": "Declined applications with 25% or lower default risk.",
            },
            {
                "label": "High-risk queue",
                "value": len(open_high_risk),
                "tone": "warning" if open_high_risk else "approved",
                "detail": "Open review cases above the senior risk threshold.",
            },
        ],
    }


def build_model_card(history):
    total = len(history)
    source_counts = {}
    for item in history:
        source = item.get("model_source") or "Unknown source"
        source_counts[source] = source_counts.get(source, 0) + 1

    sources = [
        {
            "label": source,
            "count": count,
            "share": percentage(count, total),
        }
        for source, count in sorted(source_counts.items(), key=lambda pair: pair[1], reverse=True)
    ]
    fallback_count = sum(
        count
        for source, count in source_counts.items()
        if "fallback" in source.lower() or "policy" in source.lower()
    )
    last_scored = history[0].get("created_at_label") if history else "No scores yet"

    return {
        "sample_size": total,
        "model_available": os.path.exists(MODEL_FILE),
        "model_status": "Saved model active" if os.path.exists(MODEL_FILE) else "Policy fallback",
        "fallback_share": percentage(fallback_count, total),
        "last_scored": last_scored,
        "monitored_features": [feature["label"] for feature in GOVERNANCE_FEATURES],
        "sources": sources[:4],
    }


def build_model_governance(history, review_queue):
    drift = build_drift_monitor(history)
    fairness = build_fairness_proxy_monitor(history)
    quality = build_data_quality_monitor(history)
    alignment = build_policy_alignment(history)
    model_card = build_model_card(history)

    penalty = (
        drift["alert_count"] * 9
        + drift["watch_count"] * 4
        + fairness["alert_count"] * 10
        + fairness["watch_count"] * 4
        + quality["alert_count"] * 8
        + quality["watch_count"] * 3
        + min(18, alignment["exception_rate"] * 0.9)
    )
    score = int(round(clamp(100 - penalty, 0, 100))) if history else 0

    if not history:
        stage = "Awaiting data"
        tone = "neutral"
    elif score >= 90:
        stage = "Audit ready"
        tone = "approved"
    elif score >= 75:
        stage = "Controlled"
        tone = "warning"
    else:
        stage = "Needs review"
        tone = "rejected"

    return {
        "score": score,
        "stage": stage,
        "tone": tone,
        "sample_size": len(history),
        "open_reviews": len(review_queue),
        "drift": drift,
        "fairness": fairness,
        "quality": quality,
        "alignment": alignment,
        "model_card": model_card,
        "summary_cards": [
            {
                "label": "Governance score",
                "value": f"{score}/100" if history else "No data",
                "tone": tone,
                "detail": "Composite of drift, fairness proxy, quality, and policy-alignment controls.",
                "icon": "shield-check",
            },
            {
                "label": "Drift monitor",
                "value": drift["status"],
                "tone": drift["tone"],
                "detail": f"{drift['recent_count']} recent records compared with {drift['baseline_count']} baseline records.",
                "icon": "activity",
            },
            {
                "label": "Fairness proxy",
                "value": fairness["status"],
                "tone": fairness["tone"],
                "detail": f"Largest approval-rate gap: {fairness['largest_gap']:.1f} percentage points.",
                "icon": "scale",
            },
            {
                "label": "Policy alignment",
                "value": alignment["status"],
                "tone": alignment["tone"],
                "detail": f"{alignment['exception_count']} exception patterns across scored applications.",
                "icon": "clipboard-check",
            },
        ],
    }


def fetch_monitoring_history(limit=500):
    rows = fetch_all(
        """
        SELECT
            p.*,
            u.name AS owner_name,
            u.email AS owner_email,
            reviewer.name AS reviewer_name
        FROM predictions p
        JOIN users u ON u.id = p.user_id
        LEFT JOIN users reviewer ON reviewer.id = p.reviewed_by
        ORDER BY p.created_at DESC, p.id DESC
        LIMIT ?
        """,
        (limit,),
    )
    return [clean_prediction(row) for row in rows]


def build_applicant_query(args):
    query = {
        "name": (args.get("name") or "").strip(),
        "email": (args.get("email") or "").strip().lower(),
        "phone": (args.get("phone") or "").strip(),
        "age": (args.get("age") or "").strip(),
        "income": (args.get("income") or "").strip(),
        "loan": (args.get("loan") or "").strip(),
        "loan_term_months": (args.get("loan_term_months") or "").strip(),
        "score": (args.get("score") or "").strip(),
        "marital_status": (args.get("marital_status") or "").strip(),
        "education": (args.get("education") or "").strip(),
        "dependents": (args.get("dependents") or "").strip(),
        "experience": (args.get("experience") or "").strip(),
    }
    query["searched"] = any(query.get(key) for key in ("name", "email", "phone"))
    return query


def applicant_conditions(query, prediction_alias="p", user_alias="u"):
    conditions = []
    params = []
    name = query.get("name")
    email = query.get("email")
    phone = query.get("phone")

    if email:
        conditions.append(f"lower({prediction_alias}.applicant_email) = lower(?)")
        params.append(email)
    if phone:
        conditions.append(f"{prediction_alias}.phone LIKE ?")
        params.append(f"%{phone}%")
    if name:
        conditions.append(f"(lower({prediction_alias}.applicant_name) LIKE lower(?) OR lower({user_alias}.name) LIKE lower(?))")
        params.extend((f"%{name}%", f"%{name}%"))

    return conditions, params


def fetch_applicant_history(query, limit=12):
    if not query.get("searched"):
        return []

    conditions, params = applicant_conditions(query)
    rows = fetch_all(
        f"""
        SELECT
            p.*,
            u.name AS owner_name,
            u.email AS owner_email,
            reviewer.name AS reviewer_name
        FROM predictions p
        JOIN users u ON u.id = p.user_id
        LEFT JOIN users reviewer ON reviewer.id = p.reviewed_by
        WHERE {" OR ".join(conditions)}
        ORDER BY p.created_at DESC, p.id DESC
        LIMIT ?
        """,
        tuple(params + [limit]),
    )
    return [clean_prediction(row) for row in rows]


def fetch_applicant_loan_history(query, limit=8):
    if not query.get("searched"):
        return []

    conditions, params = applicant_conditions(query)
    rows = fetch_all(
        f"""
        SELECT
            l.*,
            p.applicant_name,
            p.phone,
            p.applicant_email,
            p.result,
            p.status AS prediction_status,
            u.name AS owner_name,
            u.email AS owner_email,
            COALESCE(pay.total_paid, 0) AS total_paid,
            COALESCE(pay.payments_made, 0) AS payments_made,
            pay.last_payment_recorded_at
        FROM loan_accounts l
        JOIN predictions p ON p.id = l.prediction_id
        JOIN users u ON u.id = l.user_id
        LEFT JOIN (
            SELECT
                loan_id,
                SUM(amount) AS total_paid,
                COUNT(*) AS payments_made,
                MAX(created_at) AS last_payment_recorded_at
            FROM payment_transactions
            WHERE status = 'completed'
            GROUP BY loan_id
        ) pay ON pay.loan_id = l.id
        WHERE {" OR ".join(conditions)}
        ORDER BY l.created_at DESC, l.id DESC
        LIMIT ?
        """,
        tuple(params + [limit]),
    )
    return [clean_loan(row) for row in rows]


def build_applicant_lookup(query):
    history = fetch_applicant_history(query)
    loans = fetch_applicant_loan_history(query)
    total = len(history)
    approved = sum(1 for item in history if item.get("result") == "Approved")
    rejected = sum(1 for item in history if item.get("result") == "Rejected")
    outstanding = round(sum(loan.get("balance_amount", 0) for loan in loans), 2)
    total_paid = round(sum(loan.get("total_paid", 0) for loan in loans), 2)
    total_borrowed = round(sum(loan.get("disbursed_amount", 0) for loan in loans), 2)

    return {
        "query": query,
        "searched": bool(query.get("searched")),
        "history": history,
        "loans": loans,
        "summary": {
            "total": total,
            "approved": approved,
            "rejected": rejected,
            "active_loans": sum(1 for loan in loans if loan.get("status") != "closed"),
            "total_borrowed": total_borrowed,
            "outstanding": outstanding,
            "total_paid": total_paid,
        },
    }


def build_risk_admin_insights(history):
    today_candidates = [item for item in history if item.get("is_today")]
    approved_today = [item for item in today_candidates if item.get("result") == "Approved"]
    daily_pool = approved_today or today_candidates

    top_risk_candidates = sorted(
        history,
        key=lambda item: (item["risk_score"], item["loan_amount"]),
        reverse=True,
    )[:5]
    daily_best_candidates = sorted(
        daily_pool,
        key=lambda item: (item["approval_probability"], item["credit_score"], -item["risk_score"]),
        reverse=True,
    )[:5]
    officer_approvals = sorted(
        [
            item
            for item in history
            if item.get("reviewed_by") and item.get("result") == "Approved"
        ],
        key=lambda item: item.get("reviewed_at") or item.get("created_at") or "",
        reverse=True,
    )[:8]

    today_total = len(today_candidates)
    today_avg_risk = (
        round(sum(item["risk_score"] for item in today_candidates) / today_total, 2)
        if today_total
        else 0
    )
    today_approval_rate = (
        round((len(approved_today) / today_total) * 100, 2)
        if today_total
        else 0
    )

    return {
        "today_label": datetime.now(timezone.utc).strftime("%d %b %Y"),
        "today_total": today_total,
        "today_approved": len(approved_today),
        "today_avg_risk": today_avg_risk,
        "today_approval_rate": today_approval_rate,
        "top_risk_candidates": top_risk_candidates,
        "daily_best_candidates": daily_best_candidates,
        "officer_approvals": officer_approvals,
    }


def build_portfolio_intelligence(history, review_queue, customer_loans):
    total_exposure = round(sum(item["loan_amount"] for item in history), 2)
    approved_exposure = round(
        sum(item["loan_amount"] for item in history if item.get("result") == "Approved"),
        2,
    )
    high_risk_exposure = round(
        sum(item["loan_amount"] for item in history if item.get("risk_score", 0) >= 60),
        2,
    )
    open_review_exposure = round(sum(item["loan_amount"] for item in review_queue), 2)
    expected_loss = round(sum(item["intelligence"]["expected_loss"] for item in history), 2)
    total = len(history)
    avg_approval = round(sum(item["approval_probability"] for item in history) / total, 2) if total else 0
    high_risk_share = round((high_risk_exposure / total_exposure) * 100, 2) if total_exposure else 0
    expected_loss_rate = round((expected_loss / total_exposure) * 100, 2) if total_exposure else 0
    risk_bands = {
        "Low": sum(1 for item in history if item["risk_label"] == "Low"),
        "Medium": sum(1 for item in history if item["risk_label"] == "Medium"),
        "High": sum(1 for item in history if item["risk_label"] == "High"),
    }

    avg_risk = round(sum(item["risk_score"] for item in history) / total, 2) if total else 0
    if not total:
        portfolio_grade = "N/A"
        portfolio_tone = "neutral"
    elif avg_risk <= 25 and high_risk_share <= 10:
        portfolio_grade = "A"
        portfolio_tone = "approved"
    elif avg_risk <= 40 and high_risk_share <= 25:
        portfolio_grade = "B"
        portfolio_tone = "approved"
    elif avg_risk <= 58:
        portfolio_grade = "C"
        portfolio_tone = "warning"
    else:
        portfolio_grade = "D"
        portfolio_tone = "rejected"

    action_items = []
    if review_queue:
        action_items.append(
            {
                "title": "Clear review exposure",
                "detail": f"${open_review_exposure:,.0f} is waiting for officer action.",
                "tone": "neutral",
                "icon": "clipboard-check",
            }
        )
    if high_risk_exposure:
        action_items.append(
            {
                "title": "Escalate high-risk cases",
                "detail": f"${high_risk_exposure:,.0f} sits in the high-risk band.",
                "tone": "rejected" if high_risk_share > 30 else "warning",
                "icon": "shield-alert",
            }
        )
    pending_payments = sum(
        1
        for loan in customer_loans
        if loan.get("balance_amount", 0) > 0 and loan.get("payment_status") != "paid"
    )
    if pending_payments:
        action_items.append(
            {
                "title": "Payment follow-up",
                "detail": f"{pending_payments} active loan payment checkpoint needs attention.",
                "tone": "neutral",
                "icon": "receipt-text",
            }
        )
    if not action_items:
        action_items.append(
            {
                "title": "Control room clear",
                "detail": "No urgent portfolio action is visible in the current data slice.",
                "tone": "approved",
                "icon": "badge-check",
            }
        )

    return {
        "portfolio_grade": portfolio_grade,
        "portfolio_tone": portfolio_tone,
        "total_exposure": total_exposure,
        "approved_exposure": approved_exposure,
        "open_review_exposure": open_review_exposure,
        "high_risk_exposure": high_risk_exposure,
        "high_risk_share": high_risk_share,
        "expected_loss": expected_loss,
        "expected_loss_rate": expected_loss_rate,
        "avg_approval": avg_approval,
        "risk_bands": risk_bands,
        "action_items": action_items[:3],
    }


def get_dashboard_payload(user, applicant_query=None):
    query, params = scoped_prediction_query(user)
    rows = fetch_all(query, params)
    history = [clean_prediction(row) for row in rows]

    total = len(history)
    approved = sum(1 for item in history if item["result"] == "Approved")
    rejected = sum(1 for item in history if item["result"] == "Rejected")
    in_review = sum(1 for item in history if is_review_open(item))
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
    risk_insights = build_risk_admin_insights(history) if user["role"] == "risk_admin" else {}
    applicant_lookup = build_applicant_lookup(applicant_query or build_applicant_query({}))
    review_queue = [item for item in history if is_review_open(item)][:12]
    loan_payment_summary = {
        "active": sum(1 for loan in customer_loans if loan.get("status") != "closed"),
        "pending": sum(
            1
            for loan in customer_loans
            if loan.get("balance_amount", 0) > 0 and loan.get("payment_status") != "paid"
        ),
        "paid": sum(1 for loan in customer_loans if loan.get("payment_status") == "paid"),
    }
    portfolio_intelligence = build_portfolio_intelligence(history, review_queue, customer_loans)
    model_governance = build_model_governance(history, review_queue)

    return {
        "history": history,
        "recent_history": history[:12],
        "review_queue": review_queue,
        "customer_loans": customer_loans,
        "loan_payment_summary": loan_payment_summary,
        "loan_offers": loan_offers,
        "notifications": notifications,
        "risk_insights": risk_insights,
        "applicant_lookup": applicant_lookup,
        "portfolio_intelligence": portfolio_intelligence,
        "model_governance": model_governance,
        "assurance_checks": build_assurance_checks(review_queue),
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
            "risk_bands": portfolio_intelligence["risk_bands"],
        },
    }


def fetch_prediction_detail(prediction_id):
    row = fetch_one(
        """
        SELECT
            p.*,
            u.name AS owner_name,
            u.email AS owner_email,
            reviewer.name AS reviewer_name
        FROM predictions p
        JOIN users u ON u.id = p.user_id
        LEFT JOIN users reviewer ON reviewer.id = p.reviewed_by
        WHERE p.id = ?
        """,
        (prediction_id,),
    )
    return clean_prediction(row) if row else None


def accessible_monitoring_history(user):
    if user["role"] in {"bank_officer", "risk_admin"}:
        return fetch_monitoring_history()

    query, params = scoped_prediction_query(user)
    rows = fetch_all(query, params)
    return [clean_prediction(row) for row in rows]


def build_decision_memo_context(prediction_id, user):
    prediction = fetch_prediction_detail(prediction_id)
    if not prediction:
        return None

    if user["role"] == "customer" and prediction.get("user_id") != user["id"]:
        abort(403)

    history = accessible_monitoring_history(user)
    review_queue = [item for item in history if is_review_open(item)]
    governance = build_model_governance(history, review_queue)
    intelligence = build_decision_intelligence(
        prediction["credit_score"],
        prediction["income"],
        prediction["loan_amount"],
        prediction["employment_years"],
        prediction["loan_term_months"],
        prediction["approval_probability"],
        prediction["risk_score"],
        prediction["result"],
    )
    prediction["intelligence"] = intelligence

    applicant_profile = [
        {"label": "Applicant", "value": prediction["applicant_label"]},
        {"label": "Contact", "value": prediction.get("applicant_email") or prediction.get("owner_email") or "Not provided"},
        {"label": "Phone", "value": mask_phone(prediction.get("phone")) or "Not provided"},
        {"label": "Age", "value": prediction["age"]},
        {"label": "Education", "value": title_value(prediction.get("education"))},
        {"label": "Marital status", "value": title_value(prediction.get("marital_status"))},
        {"label": "Dependents", "value": prediction["dependents"]},
        {"label": "Employment", "value": f"{prediction['employment_years']} years"},
    ]
    decision_metrics = [
        {"label": "Decision", "value": prediction["result"], "tone": "approved" if prediction["result"] == "Approved" else "rejected"},
        {"label": "Status", "value": prediction["status_label"], "tone": prediction["status_tone"]},
        {"label": "Approval probability", "value": f"{prediction['approval_probability']}%", "tone": "approved"},
        {"label": "Default risk", "value": f"{prediction['risk_score']}%", "tone": "rejected" if prediction["risk_score"] >= 60 else "warning" if prediction["risk_score"] >= 30 else "approved"},
        {"label": "Underwriting grade", "value": intelligence["grade"], "tone": intelligence["grade_tone"]},
        {"label": "Expected loss", "value": f"${intelligence['expected_loss']:,.0f}", "tone": "neutral"},
        {"label": "Risk APR", "value": f"{intelligence['estimated_apr']}%", "tone": "neutral"},
        {"label": "Recommended limit", "value": f"${intelligence['recommended_limit']:,.0f}", "tone": "approved"},
    ]
    loan_terms = [
        {"label": "Annual income", "value": f"${prediction['income']:,.0f}"},
        {"label": "Requested loan", "value": f"${prediction['loan_amount']:,.0f}"},
        {"label": "Loan term", "value": f"{prediction['loan_term_months']} months"},
        {"label": "Monthly payment estimate", "value": f"${intelligence['monthly_payment']:,.0f}"},
        {"label": "Payment-to-income", "value": f"{intelligence['payment_to_income']}%"},
        {"label": "Loan-to-income", "value": f"{intelligence['loan_to_income']}%"},
    ]
    governance_snapshot = [
        {"label": "Governance score", "value": f"{governance['score']}/100", "tone": governance["tone"]},
        {"label": "Drift", "value": governance["drift"]["status"], "tone": governance["drift"]["tone"]},
        {"label": "Fairness proxy", "value": governance["fairness"]["status"], "tone": governance["fairness"]["tone"]},
        {"label": "Data quality", "value": governance["quality"]["status"], "tone": governance["quality"]["tone"]},
        {"label": "Policy alignment", "value": governance["alignment"]["status"], "tone": governance["alignment"]["tone"]},
        {"label": "Model mode", "value": governance["model_card"]["model_status"], "tone": "approved" if governance["model_card"]["model_available"] else "neutral"},
    ]
    audit_events = [
        {
            "label": "Application scored",
            "value": prediction["created_at_label"] or "Recorded",
            "detail": f"{prediction['model_source'] or 'Decision engine'} generated the first decision.",
        }
    ]
    if prediction.get("reviewed_at_label"):
        audit_events.append(
            {
                "label": "Officer review",
                "value": prediction["reviewed_at_label"],
                "detail": f"{prediction.get('reviewer_name') or 'Review officer'} set status to {prediction['status_label']}.",
            }
        )
    elif is_review_open(prediction):
        audit_events.append(
            {
                "label": "Review pending",
                "value": prediction["status_label"],
                "detail": "Application remains available in the officer review queue.",
            }
        )
    else:
        audit_events.append(
            {
                "label": "Final status",
                "value": prediction["status_label"],
                "detail": "Decision status is locked outside the open review queue.",
            }
        )

    return {
        "memo_id": f"CS-{prediction_id:06d}",
        "generated_at": format_short_datetime(now_iso()),
        "prediction": prediction,
        "applicant_profile": applicant_profile,
        "decision_metrics": decision_metrics,
        "loan_terms": loan_terms,
        "governance_snapshot": governance_snapshot,
        "audit_events": audit_events,
        "governance": governance,
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
    applicant_query = build_applicant_query(request.args) if user["role"] in {"bank_officer", "risk_admin"} else None
    payload = get_dashboard_payload(user, applicant_query)
    last_prediction = session.pop("last_prediction", None)
    return render_template("index.html", **payload, last_prediction=last_prediction)


@app.route("/decision/<int:prediction_id>/memo")
@login_required
def decision_memo(prediction_id):
    user = current_user()
    context = build_decision_memo_context(prediction_id, user)
    if not context:
        abort(404)
    return render_template("decision_memo.html", **context)


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
    intelligence = build_decision_intelligence(
        score,
        income,
        loan,
        experience,
        loan_term_months,
        approval_probability,
        risk_score,
        result,
    )
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
        subject = "Loan application approved by CrediSense AI"
        email_body = f"""
Hello {applicant_name},

Your loan application #{prediction_id} for ${loan:,.0f} has passed the CrediSense AI risk check.

An officer will complete the final review before disbursement.

CrediSense AI
"""
        send_email_notification(user["id"], applicant_email, subject, email_body, prediction_id=prediction_id)
        sms = (
            f"CrediSense AI: Your loan application #{prediction_id} is approved for ${loan:,.0f}. "
            "Officer review is the final step before disbursement."
        )
        send_sms_notification(user["id"], phone, sms, prediction_id=prediction_id)
    else:
        subject = "Loan application update from CrediSense AI"
        email_body = f"""
Hello {applicant_name},

Your loan application #{prediction_id} is currently {result}.

Open your dashboard to review the risk factors and recommended next steps.

CrediSense AI
"""
        send_email_notification(user["id"], applicant_email, subject, email_body, prediction_id=prediction_id)
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
        "intelligence": intelligence,
        "status": prediction_status_label(status),
    }
    flash(f"Application #{prediction_id} scored successfully.", "success")
    return redirect(url_for("dashboard"))


@app.route("/review/<int:prediction_id>", methods=["POST"])
@role_required("bank_officer", "risk_admin")
def review_prediction(prediction_id):
    user = current_user()
    raw_status = request.form.get("status", "needs_review")
    status = normalize_prediction_status(raw_status)
    note = request.form.get("review_note", "").strip()
    allowed = {"pre_approved", "needs_review", "approved_by_officer", FINAL_REJECTION_STATUS}
    raw_status_key = status_key(raw_status)
    status_aliases = {"review", "under_review", "in_review", "pending", "pending_review", "awaiting_review", "approved", "officer_approved", "rejected", "denied"}
    if status not in allowed or raw_status_key not in allowed | status_aliases:
        flash("Invalid review status.", "error")
        return redirect(url_for("dashboard"))

    prediction = fetch_one(
        """
        SELECT user_id, applicant_name, applicant_email, phone, loan_amount, result
        FROM predictions
        WHERE id = ?
        """,
        (prediction_id,),
    )
    if not prediction:
        flash("Application not found.", "error")
        return redirect(url_for("dashboard"))

    next_result = prediction.get("result") or "Rejected"
    if status == "approved_by_officer":
        next_result = "Approved"
    elif status == FINAL_REJECTION_STATUS:
        next_result = "Rejected"

    execute(
        """
        UPDATE predictions
        SET status = ?, result = ?, review_note = ?, reviewed_by = ?, reviewed_at = ?
        WHERE id = ?
        """,
        (status, next_result, note, user["id"], now_iso(), prediction_id),
    )
    log_activity(user["id"], "application_reviewed", f"Application #{prediction_id} set to {status}")
    if prediction and status in {"approved_by_officer", FINAL_REJECTION_STATUS}:
        if status == "approved_by_officer":
            subject = "Your loan has been approved"
            message = (
                f"CrediSense AI: Your loan application #{prediction_id} is officer-approved for "
                f"${float(prediction.get('loan_amount') or 0):,.0f}. Log in to take the loan."
            )
            email_body = f"""
Hello {prediction.get("applicant_name") or "Applicant"},

Your loan application #{prediction_id} has been approved by {user["name"]}.

Approved amount: ${float(prediction.get("loan_amount") or 0):,.0f}

Log in to your CrediSense AI dashboard to take the loan and view the repayment schedule.

CrediSense AI
"""
        else:
            subject = "Your loan review is complete"
            message = f"CrediSense AI: Your loan application #{prediction_id} has been declined after review."
            email_body = f"""
Hello {prediction.get("applicant_name") or "Applicant"},

Your loan application #{prediction_id} has been declined after officer review.

Log in to your CrediSense AI dashboard to review the decision factors and next steps.

CrediSense AI
"""
        send_email_notification(
            prediction["user_id"],
            prediction.get("applicant_email"),
            subject,
            email_body,
            prediction_id=prediction_id,
        )
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
        f"CrediSense AI: ${principal:,.0f} has been credited to your loan account. "
        f"First payment ${monthly_payment:,.2f} is due in 30 days."
    )
    email_body = f"""
Hello {prediction.get("applicant_name") or user["name"]},

Your loan #{loan_id} has been disbursed in CrediSense AI.

Credited amount: ${principal:,.0f}
First payment: ${monthly_payment:,.2f}
Due date: {format_short_datetime(due_date)}

CrediSense AI
"""
    send_email_notification(
        user["id"],
        prediction.get("applicant_email") or user["email"],
        "Your loan has been disbursed",
        email_body,
        prediction_id=prediction_id,
        loan_id=loan_id,
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
            u.name AS owner_name,
            u.email AS owner_email,
            COALESCE(pay.total_paid, 0) AS total_paid,
            COALESCE(pay.payments_made, 0) AS payments_made,
            pay.last_payment_recorded_at
        FROM loan_accounts l
        JOIN predictions p ON p.id = l.prediction_id
        JOIN users u ON u.id = l.user_id
        LEFT JOIN (
            SELECT
                loan_id,
                SUM(amount) AS total_paid,
                COUNT(*) AS payments_made,
                MAX(created_at) AS last_payment_recorded_at
            FROM payment_transactions
            WHERE status = 'completed'
            GROUP BY loan_id
        ) pay ON pay.loan_id = l.id
        WHERE l.id = ?
    """
    params = [loan_id]
    if user["role"] == "customer":
        query += " AND l.user_id = ?"
        params.append(user["id"])
    loan = fetch_one(query, tuple(params))
    return clean_loan(loan) if loan else None


def complete_demo_payment(loan_id, user_id, payment_amount, new_balance, next_status, next_payment_status, next_due_date):
    paid_at = now_iso()
    conn = get_db()
    try:
        conn.execute(
            """
            UPDATE loan_accounts
            SET balance_amount = ?, status = ?, payment_status = ?, due_date = ?,
                last_payment_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (new_balance, next_status, next_payment_status, next_due_date, paid_at, paid_at, loan_id),
        )
        conn.execute(
            """
            INSERT INTO payment_transactions (
                loan_id, user_id, amount, balance_after, status, provider, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (loan_id, user_id, payment_amount, new_balance, "completed", "demo-card", paid_at),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@app.route("/payment/<int:loan_id>", methods=["GET", "POST"])
@login_required
def fake_payment(loan_id):
    user = current_user()
    loan = fetch_loan_for_user(loan_id, user)
    if not loan:
        flash("Loan account not found.", "error")
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        if loan["balance_amount"] <= 0:
            flash("This demo loan is already paid off.", "success")
            return redirect(url_for("dashboard"))

        payment_amount = min(loan["monthly_payment"], loan["balance_amount"])
        new_balance = round(max(0, loan["balance_amount"] - payment_amount), 2)
        next_status = "closed" if new_balance <= 0 else "active"
        next_payment_status = "paid"
        next_due_date = loan["due_date"] if next_status == "closed" else add_days_iso(30)

        complete_demo_payment(
            loan_id,
            loan["user_id"],
            payment_amount,
            new_balance,
            next_status,
            next_payment_status,
            next_due_date,
        )

        message = (
            f"CrediSense AI: Payment of ${payment_amount:,.2f} received for loan #{loan_id}. "
            f"Remaining balance is ${new_balance:,.2f}."
        )
        email_body = f"""
Hello {loan.get("applicant_name") or loan.get("owner_label") or "Customer"},

Your payment for loan #{loan_id} has been received.

Payment amount: ${payment_amount:,.2f}
Remaining balance: ${new_balance:,.2f}

CrediSense AI
"""
        send_email_notification(
            loan["user_id"],
            loan.get("applicant_email"),
            "Loan payment received",
            email_body,
            loan_id=loan_id,
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
    score = coerce_float(data.get("score"), 650, 300, 900)
    income = coerce_float(data.get("income"), 50000, 0)
    loan = coerce_float(data.get("loan"), 20000, 0)
    experience = coerce_float(data.get("experience"), 2, 0, 50)
    loan_term_months = coerce_int(data.get("loan_term_months"), DEFAULT_LOAN_TERM_MONTHS, 12, 120)

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
    intelligence = build_decision_intelligence(
        score,
        income,
        loan,
        experience,
        loan_term_months,
        prediction["approval_probability"],
        prediction["risk_score"],
        prediction["result"],
    )

    return jsonify(
        {
            "result": prediction["result"],
            "approval_probability": prediction["approval_probability"],
            "risk_score": prediction["risk_score"],
            "risk_label": risk_label(prediction["risk_score"]),
            "intelligence": intelligence,
        }
    )


@app.route("/chat", methods=["POST"])
@login_required
def chat():
    data = request.get_json(silent=True) or {}
    message = str(data.get("message", "")).lower()
    score = coerce_float(data.get("score"), 0, 0)
    income = coerce_float(data.get("income"), 0, 0)
    loan = coerce_float(data.get("loan"), 0, 0)
    experience = coerce_float(data.get("experience"), 2, 0, 50)
    loan_term_months = coerce_int(data.get("loan_term_months"), DEFAULT_LOAN_TERM_MONTHS, 12, 120)
    scenario_intelligence = None
    if score and income and loan:
        scenario_prediction = predict_credit_risk(
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
        scenario_intelligence = build_decision_intelligence(
            score,
            income,
            loan,
            experience,
            loan_term_months,
            scenario_prediction["approval_probability"],
            scenario_prediction["risk_score"],
            scenario_prediction["result"],
        )

    if "rescue" in message or "counterfactual" in message or "path" in message:
        if scenario_intelligence and scenario_intelligence.get("rescue_plan"):
            plan = scenario_intelligence["rescue_plan"]
            reply = (
                f"{plan['status']}: target a {plan['target_score']} score, "
                f"{format_money(plan['target_loan'])} loan, and {plan['target_term']}-month term. "
                f"That path changes approval by {plan['approval_lift']} points and risk by {plan['risk_reduction']} points."
            )
        else:
            reply = "Add score, income, loan, term, and experience so I can build a counterfactual approval path."
    elif "reject" in message or "decline" in message:
        reply = "The most common rejection drivers are low credit score, high loan-to-income ratio, and weak employment stability."
    elif "grade" in message or "policy" in message or "underwriting" in message:
        if scenario_intelligence:
            reply = (
                f"This scenario is grade {scenario_intelligence['grade']}. "
                f"Primary action: {scenario_intelligence['action']}. "
                f"Top control: {scenario_intelligence['policy_flags'][0]['label']}."
            )
        else:
            reply = "Add score, income, and loan values so I can produce a policy grade and top underwriting control."
    elif "price" in message or "apr" in message or "rate" in message:
        if scenario_intelligence:
            reply = (
                f"Risk-adjusted pricing guidance is about {scenario_intelligence['estimated_apr']}% APR, "
                f"with an estimated monthly payment of ${scenario_intelligence['monthly_payment']:,.2f}."
            )
        else:
            reply = "Pricing needs a score, income, and requested loan amount."
    elif "loss" in message or "expected" in message:
        if scenario_intelligence:
            reply = (
                f"Expected-loss estimate is ${scenario_intelligence['expected_loss']:,.2f}, "
                f"using {scenario_intelligence['loss_given_default']}% loss given default."
            )
        else:
            reply = "Expected loss needs a scored scenario first."
    elif "governance" in message or "drift" in message or "fairness" in message or "audit" in message:
        payload = get_dashboard_payload(current_user())
        governance = payload["model_governance"]
        reply = (
            f"Governance is {governance['stage']} at {governance['score']}/100. "
            f"Drift is {governance['drift']['status']}, fairness proxy is {governance['fairness']['status']}, "
            f"and policy alignment is {governance['alignment']['status']}."
        )
    elif "payment" in message or "due" in message:
        reply = "Open the loan wallet on your customer dashboard to see payment due time, balance, and the demo payment button."
    elif "credited" in message or "money" in message or "take loan" in message:
        reply = "If your application is approved, use Take Loan in the customer loan wallet. The app will show a credited message and payment schedule."
    elif "improve" in message:
        if scenario_intelligence and scenario_intelligence.get("rescue_plan"):
            plan = scenario_intelligence["rescue_plan"]
            top_step = plan["steps"][0] if plan["steps"] else {"label": "Verification pack", "detail": "Add supporting documents."}
            reply = (
                f"Start with {top_step['label']}: {top_step['detail']} "
                f"The rescue target is {format_money(plan['target_loan'])} at score {plan['target_score']}."
            )
        else:
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
    history = fetch_monitoring_history()
    review_queue = [item for item in history if is_review_open(item)]
    governance = build_model_governance(history, review_queue)
    return jsonify(
        {
            "status": "ok",
            "database": database_label(),
            "model_loaded": model is not None,
            "model_file": os.path.exists(MODEL_FILE),
            "email_configured": email_configured(),
            "sms_configured": sms_configured(),
            "status_integrity_issues": status_integrity_issue_count(),
            "model_governance": {
                "score": governance["score"],
                "stage": governance["stage"],
                "drift_status": governance["drift"]["status"],
                "fairness_status": governance["fairness"]["status"],
                "quality_status": governance["quality"]["status"],
                "policy_exceptions": governance["alignment"]["exception_count"],
            },
        }
    )


init_database()


if __name__ == "__main__":
    local_port = int(os.environ.get("PORT", 5000))
    local_debug = os.environ.get("FLASK_DEBUG", "1") == "1"
    print("Starting CrediSense AI local server")
    print(f"Open http://127.0.0.1:{local_port}")
    print("Flask serves both frontend templates and backend routes.")
    print("Use gunicorn app:app only on Render/Linux, not on Windows.")
    app.run(host="127.0.0.1", port=local_port, debug=local_debug)
