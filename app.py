from flask import Flask, request, jsonify
import joblib
import numpy as np
from src.utils import json_to_dataframe

app = Flask(__name__)

# Load trained model once at startup
model = joblib.load("models/best_model.joblib")


@app.route("/")
def home():
    return "Credit Risk Prediction API is running!"


@app.route("/predict", methods=["POST"])
def predict():
    try:
        data = request.get_json()

        X = json_to_dataframe(data)

        if hasattr(model, "predict_proba"):
            proba = float(model.predict_proba(X)[:, 1][0])
        else:
            score = float(model.decision_function(X)[0])
            proba = float(1 / (1 + np.exp(-score)))

        label = int(proba >= 0.5)

        return jsonify({
            "probability_of_default": round(proba, 6),
            "predicted_label": label
        })

    except Exception as e:
        return jsonify({"error": str(e)})


if __name__ == "__main__":
    app.run(debug=True)