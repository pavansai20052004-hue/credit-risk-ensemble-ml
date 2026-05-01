import argparse
import joblib
import numpy as np

try:
    from .utils import load_json, json_to_dataframe
except ImportError:
    from utils import load_json, json_to_dataframe


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="models/best_model.joblib", help="Path to saved model")
    parser.add_argument("--input-json", default="src/sample_request.json", help="Path to JSON payload")
    parser.add_argument("--threshold", type=float, default=0.5, help="Decision threshold")
    args = parser.parse_args()

    pipe = joblib.load(args.model)
    payload = load_json(args.input_json)
    X = json_to_dataframe(payload)

    if hasattr(pipe, "predict_proba"):
        proba = float(pipe.predict_proba(X)[:, 1][0])
    else:
        score = float(pipe.decision_function(X)[0])
        proba = float(1 / (1 + np.exp(-score)))

    label = int(proba >= args.threshold)

    print("Prediction Result")
    print("Probability of Default:", round(proba, 6))
    print("Predicted Label (1=Default, 0=No Default):", label)


if __name__ == "__main__":
    main()
