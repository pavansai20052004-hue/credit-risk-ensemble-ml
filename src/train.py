import argparse
import os
import joblib
import numpy as np

from sklearn.model_selection import train_test_split, StratifiedKFold, cross_val_score
from sklearn.pipeline import Pipeline
from sklearn.metrics import f1_score, classification_report

try:
    from .utils import load_dataset, normalize_target
    from .preprocessing import get_feature_types, build_preprocessor
    from .models import get_models
except ImportError:
    from utils import load_dataset, normalize_target
    from preprocessing import get_feature_types, build_preprocessor
    from models import get_models


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True, help="Path to dataset CSV")
    parser.add_argument("--target", default="default", help="Target column name")
    parser.add_argument("--output-model", default="models/best_model.joblib", help="Path for the saved model")
    args = parser.parse_args()

    # 1) Load dataset
    df = load_dataset(args.data)

    if args.target not in df.columns:
        raise ValueError(f"Target column '{args.target}' not found in dataset columns.")

    # 2) Normalize target to 0/1
    df[args.target] = normalize_target(df[args.target])

    # 3) Split X and y
    X = df.drop(columns=[args.target])
    y = df[args.target].astype(int)

    # 4) Train-test split
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    # 5) Build preprocess pipeline
    numeric_cols, categorical_cols = get_feature_types(df, args.target)
    preprocessor = build_preprocessor(numeric_cols, categorical_cols)

    # 6) Get models
    models = get_models(random_state=42)

    best_model_name = None
    best_score = -1
    best_pipeline = None

    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)

    print("\nTraining started...\n")

    # 7) Train and compare models using CV F1 score
    for name, model in models.items():
        pipeline = Pipeline(steps=[
            ("preprocess", preprocessor),
            ("model", model)
        ])

        try:
            scores = cross_val_score(pipeline, X_train, y_train, cv=cv, scoring="f1")
            cv_f1 = float(np.mean(scores))
        except Exception:
            pipeline.fit(X_train, y_train)
            preds = pipeline.predict(X_train)
            cv_f1 = float(f1_score(y_train, preds))

        print(f"{name}  --> CV F1: {cv_f1:.4f}")

        if cv_f1 > best_score:
            best_score = cv_f1
            best_model_name = name
            best_pipeline = pipeline

    # 8) Fit best model on full train data
    print("\nBest Model:", best_model_name, "with CV F1:", round(best_score, 4))
    best_pipeline.fit(X_train, y_train)

    # 9) Evaluate on test set
    y_pred = best_pipeline.predict(X_test)

    print("\nTest Set Report:\n")
    print(classification_report(y_test, y_pred))

    # 10) Save model
    output_dir = os.path.dirname(args.output_model)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    joblib.dump(best_pipeline, args.output_model)
    print("\nModel saved to:", args.output_model)


if __name__ == "__main__":
    main()
