# CrediSense AI Presentation Guide

## One-Minute Introduction

CrediSense AI is a credit-risk SaaS prototype for loan screening. It predicts whether a borrower profile is likely to be approved or rejected, stores decisions in SQL, supports Neon PostgreSQL deployment, and gives customers, bank officers, and risk admins role-specific dashboards.

## Problem Statement

Manual loan screening can be slow, inconsistent, and difficult to explain. This project solves that by combining machine learning, transparent risk factors, and officer review workflow in one dashboard.

## Objectives

- Predict loan approval risk from customer financial details.
- Store users, applications, decisions, and review status in SQL.
- Support local SQLite and production Neon PostgreSQL.
- Compare multiple machine learning models during training.
- Save the best model and use it inside a Flask dashboard.
- Support customer, bank officer, and risk admin login flows.
- Provide explainability, charts, history tracking, a what-if simulator, voice explanation, SMS-style mobile updates, fake loan disbursement, and demo repayment.

## Technology Stack

- Frontend: HTML, CSS, JavaScript, Chart.js, Lucide icons
- Backend: Python Flask
- Database: SQLite locally, Neon PostgreSQL in production
- Authentication: Flask session with Werkzeug password hashing
- Machine Learning: pandas, scikit-learn, XGBoost-ready training script
- Model storage: joblib
- Deployment: Render with Gunicorn
- Notifications: Twilio SMS when configured, demo SQL message log otherwise

## System Architecture

```text
Browser UI
   |
Flask routes and session auth
   |
SQL users, predictions, reviews, loan accounts, notifications, activity log
   |
Saved ML model pipeline
   |
Risk score, approval probability, explanations
```

## Model Pipeline

The training script loads a credit-risk dataset, splits it into train and test sets, preprocesses numeric and categorical columns, compares models using F1 score, and saves the best pipeline to `models/best_model.joblib`.

Candidate models:

- Logistic Regression
- Random Forest
- Gradient Boosting
- AdaBoost
- XGBoost

## Features To Demonstrate

- Customer login and application submission.
- Bank officer login and review queue.
- Risk admin login and full portfolio dashboard.
- New loan risk prediction.
- Approval probability and default risk score.
- Explainable AI-style factor impact.
- Voice explanation for decisions and AI assistant replies.
- Mobile approval, credited, and payment messages.
- Customer loan wallet with Take Loan and fake payment page.
- What-if simulator.
- Dashboard charts.
- Recent application history.
- Advisor chatbot for rejection and improvement questions.

## Suggested Slide Order

1. Title and team details.
2. Problem statement.
3. Existing system vs proposed SaaS system.
4. System architecture.
5. Database schema and role-based access.
6. Deployment architecture: Render + Neon.
7. Dataset and features.
8. ML model workflow.
9. Web dashboard screens.
10. Loan disbursement and fake repayment demo.
11. Results and evaluation metrics.
12. Limitations and future scope.

## Common Viva Questions

**Why SQLite?**  
SQLite gives the project a real SQL backend without needing a separate database server. For live deployment, the app also supports Neon PostgreSQL by setting `DATABASE_URL`.

**Why Render and Neon?**  
Render runs the Flask backend with Gunicorn, and Neon provides a managed PostgreSQL database. This separates application hosting from persistent production data.

**Are the SMS and payment features real?**  
They are presentation-safe demo features. SMS can become real if Twilio environment variables are configured. The payment page is fake and only updates the demo loan balance.

**Which model was selected?**  
The training pipeline compares multiple models and saves the one with the best cross-validation F1 score. The included saved model is loaded from `models/best_model.joblib`.

**Why compare multiple models?**  
Credit risk is a tabular classification problem, and different algorithms capture different patterns. Comparing models makes the final choice evidence-based.

**What is default risk?**  
Default risk is the model-estimated chance that a borrower profile may fail repayment. The app converts it into approval probability as `100 - default risk`.

**What are the user roles?**  
Customers can submit and view their own applications. Bank officers can review applications. Risk admins can view the full portfolio and operational metrics.

**Is this real banking software?**  
No. It is an academic prototype. A production lending system needs real historical data, fairness testing, privacy controls, audit logs, stronger security, and compliance review.

**What is the future scope?**  
Use a real banking dataset, add SHAP explanations, production KYC, real payment gateway integration, fairness dashboards, and full compliance review.
