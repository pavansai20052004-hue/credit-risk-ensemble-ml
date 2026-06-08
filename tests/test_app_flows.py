import os
import re
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

    def csrf_from_response(self, response):
        html = response.get_data(as_text=True)
        match = re.search(r'name="_csrf_token" value="([^"]+)"', html)
        if not match:
            match = re.search(r'name="csrf-token" content="([^"]+)"', html)
        self.assertIsNotNone(match, "CSRF token missing from rendered page")
        return match.group(1)

    def login(self, email, password):
        login_page = self.client.get("/login")
        csrf_token = self.csrf_from_response(login_page)
        return self.client.post(
            "/login",
            data={"email": email, "password": password, "_csrf_token": csrf_token},
            follow_redirects=True,
        )

    def test_health_reports_security_and_integrity(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(response.headers.get("X-Frame-Options"), "SAMEORIGIN")
        self.assertIn("frame-ancestors 'self'", response.headers.get("Content-Security-Policy", ""))
        payload = response.get_json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["status_integrity_issues"], 0)
        self.assertIn("model_governance", payload)
        self.assertIn("score", payload["model_governance"])
        self.assertIn("drift_status", payload["model_governance"])

    def test_state_changing_json_requires_csrf_token(self):
        self.login("customer@credisense.ai", "Customer@123")
        response = self.client.post(
            "/simulate",
            json={"score": 700, "income": 60000, "loan": 25000},
        )
        self.assertEqual(response.status_code, 400)

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
        self.assertIn("Portfolio command", html)
        self.assertIn("Expected loss", html)
        self.assertIn("System assurance", html)
        self.assertIn("Governance center", html)
        self.assertIn("Live model card", html)
        self.assertIn("Status Integrity", html)
        self.assertIn("Declined", html)
        self.assertIn('id="credisense-chart-data"', html)
        self.assertNotIn("window.CREDISENSE =", html)
        self.assertNotIn("<" * 7, html)

    def test_model_governance_flags_drift_and_policy_exceptions(self):
        def item(index, score, risk, result, age, education, status):
            return {
                "id": index,
                "age": age,
                "income": 62000,
                "loan_amount": 36000,
                "loan_term_months": 36,
                "credit_score": score,
                "education": education,
                "marital_status": "single",
                "approval_probability": 100 - risk,
                "risk_score": risk,
                "result": result,
                "status": status,
                "model_source": "Saved Random Forest model",
                "created_at_label": "08 May 2026",
                "explain": [{"feature": "Credit Score", "impact": 28}],
                "intelligence": {"payment_to_income": 24},
            }

        recent = [
            item(index, 585, 78, "Approved", 24, "high_school", "needs_review")
            for index in range(1, 9)
        ]
        baseline = [
            item(index, 748, 18, "Approved", 42, "master", "pre_approved")
            for index in range(9, 25)
        ]
        governance = credit_app.build_model_governance(recent + baseline, recent)

        self.assertLess(governance["score"], 100)
        self.assertEqual(governance["drift"]["status"], "Drift alert")
        self.assertGreater(governance["alignment"]["high_risk_approved"], 0)
        self.assertGreater(governance["alignment"]["exception_count"], 0)

    def test_decision_audit_memo_renders_for_authorized_user(self):
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
                "Memo Applicant",
                "memo-applicant@example.com",
                "9000000001",
                36,
                72000,
                28000,
                36,
                735,
                "single",
                "bachelor",
                1,
                7,
                82,
                18,
                84,
                "Approved",
                "pre_approved",
                "test-model",
                '[{"feature": "Credit Score", "impact": 32}]',
                '["Keep utilization low."]',
                credit_app.now_iso(),
            ),
            return_id=True,
        )

        self.login("customer@credisense.ai", "Customer@123")
        response = self.client.get(f"/decision/{prediction_id}/memo")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Decision memo", html)
        self.assertIn("Governance snapshot", html)
        self.assertIn("Policy controls", html)
        self.assertIn("Approval rescue plan", html)
        self.assertIn("Academic prototype notice", html)

    def test_decision_intelligence_flags_high_risk_profiles(self):
        intelligence = credit_app.build_decision_intelligence(
            score=585,
            income=36000,
            loan=52000,
            experience=1,
            loan_term_months=84,
            approval_probability=26,
            risk_score=74,
            result="Rejected",
        )
        self.assertIn(intelligence["grade"], {"D", "E"})
        self.assertGreater(intelligence["expected_loss"], 0)
        self.assertGreater(intelligence["estimated_apr"], 0)
        self.assertTrue(intelligence["policy_flags"])
        self.assertEqual(intelligence["action_tone"], "rejected")
        self.assertIn("rescue_plan", intelligence)
        self.assertIn(intelligence["rescue_plan"]["status"], {"Rescue path found", "Manual recovery needed"})
        self.assertGreaterEqual(intelligence["rescue_plan"]["target_score"], 585)
        self.assertLessEqual(intelligence["rescue_plan"]["target_loan"], 52000)
        self.assertTrue(intelligence["rescue_plan"]["steps"])

    def test_simulator_handles_invalid_json_numbers(self):
        dashboard = self.login("customer@credisense.ai", "Customer@123")
        csrf_token = self.csrf_from_response(dashboard)
        response = self.client.post(
            "/simulate",
            json={
                "score": "not-a-score",
                "income": "not-income",
                "loan": "not-loan",
                "experience": "not-experience",
                "loan_term_months": "not-term",
            },
            headers={"X-CSRFToken": csrf_token},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIn(payload["result"], {"Approved", "Rejected"})
        self.assertIn("risk_score", payload)
        self.assertIn("intelligence", payload)
        self.assertIn("grade", payload["intelligence"])
        self.assertIn("policy_flags", payload["intelligence"])
        self.assertIn("rescue_plan", payload["intelligence"])
        self.assertIn("scenarios", payload["intelligence"]["rescue_plan"])


if __name__ == "__main__":
    unittest.main()
