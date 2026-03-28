from flask import Flask, render_template, request
import joblib
import json
import pandas as pd

app = Flask(__name__)

# Load model
model = joblib.load("models/best_model.joblib")  # change name if needed

@app.route('/')
def home():
    return render_template("index.html")
@app.route('/predict', methods=['POST'])
def predict():
    try:
        # Get input from form
        data = request.form.to_dict()

        # Convert only numeric fields
        numeric_fields = ['age', 'income', 'loan_amount', 'credit_score', 'loan_term_months', 'employment_years']

        for key in numeric_fields:
            if key in data:
                data[key] = float(data[key])

        # Convert to DataFrame
        df = pd.DataFrame([data])

        # Predict
        if hasattr(model, "predict_proba"):
            proba = model.predict_proba(df)[0][1]
        else:
            proba = model.predict(df)[0]

        label = int(proba > 0.5)

        return render_template("index.html",
                               prediction=label,
                               probability=round(proba, 2))

    except Exception as e:
        return f"Error: {e}"

if __name__ == "__main__":
    app.run(debug=True)