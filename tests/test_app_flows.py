import os
import tempfile
import unittest


TEST_DB = os.path.join(tempfile.gettempdir(), "credisense-test-app.db")
if os.path.exists(TEST_DB):
    os.remove(TEST_DB)

os.environ["DATABASE_PATH"] = TEST_DB
os.environ["FLASK_SECRET_KEY"] = "test-secret"

import app as credit_app  # noqa: E402


class CrediSenseFlowTests(unittest.TestCase):
    def setUp(self):
        self.client = credit_app.app.test_client()

    def login(self, email, password):
        return self.client.post(
            "/login",
            data={"email": email, "password": password},
            follow_redirects=True,
        )

    def test_health_reports_security_and_integrity(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(response.headers.get("X-Frame-Options"), "SAMEORIGIN")
        payload = response.get_json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["status_integrity_issues"], 0)

    def test_rejected_application_never_returns_to_review_queue(self):
        customer = credit_app.fetch_one(
            "SELECT * FROM users WHERE email = ?",
            ("customer@credisense.ai",),
        )
        prediction_id = credit_app.execute(
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
                customer["id"],
                "Regression Rejected",
                "regression-rejected@example.com",
                "9000000000",
                34,
                50000,
                45000,
                36,
                615,
                "single",
                "bachelor",
                0,
                4,
                48,
                52,
                48,
                "Rejected",
                "needs_review",
                "test",
                "[]",
                "[]",
                credit_app.now_iso(),
            ),
            return_id=True,
        )

        credit_app.init_database()
        row = credit_app.fetch_one("SELECT result, status FROM predictions WHERE id = ?", (prediction_id,))
        self.assertEqual(row["result"], "Rejected")
        self.assertEqual(row["status"], "declined")

        officer = credit_app.fetch_one(
            "SELECT * FROM users WHERE email = ?",
            ("officer@credisense.ai",),
        )
        payload = credit_app.get_dashboard_payload(officer)
        self.assertNotIn(prediction_id, {item["id"] for item in payload["review_queue"]})

    def test_officer_dashboard_renders_assurance_and_declined_history(self):
        response = self.login("officer@credisense.ai", "Officer@123")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("System assurance", html)
        self.assertIn("Status Integrity", html)
        self.assertIn("Declined", html)
        self.assertNotIn("<" * 7, html)

    def test_simulator_handles_invalid_json_numbers(self):
        self.login("customer@credisense.ai", "Customer@123")
        response = self.client.post(
            "/simulate",
            json={
                "score": "not-a-score",
                "income": "not-income",
                "loan": "not-loan",
                "experience": "not-experience",
                "loan_term_months": "not-term",
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIn(payload["result"], {"Approved", "Rejected"})
        self.assertIn("risk_score", payload)


if __name__ == "__main__":
    unittest.main()
