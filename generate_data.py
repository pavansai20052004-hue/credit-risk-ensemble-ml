import argparse
import math
import os
import random

import pandas as pd


MARITAL_STATUSES = ["single", "married", "divorced"]
EDUCATION_LEVELS = ["high_school", "bachelor", "master"]


def sigmoid(value):
    return 1 / (1 + math.exp(-value))


def default_probability(row):
    debt_to_income = row["loan_amount"] / max(row["income"], 1)

    risk = -2.2
    risk += (650 - row["credit_score"]) / 120
    risk += (debt_to_income - 0.45) * 2.8
    risk += 0.45 if row["income"] < 35000 else 0
    risk += 0.35 if row["employment_years"] < 2 else 0
    risk += 0.25 if row["loan_term_months"] > 60 else 0
    risk += 0.15 * row["dependents"]

    if row["education"] == "master":
        risk -= 0.25
    elif row["education"] == "high_school":
        risk += 0.2

    return min(max(sigmoid(risk), 0.03), 0.92)


def build_dataset(rows, seed):
    random.seed(seed)
    data = []

    for _ in range(rows):
        age = random.randint(21, 64)
        income = random.randint(18000, 125000)
        credit_score = random.randint(300, 900)
        employment_years = min(max(age - random.randint(21, 30), 0), 40)
        loan_amount = random.randint(5000, 120000)
        loan_term_months = random.choice([12, 24, 36, 48, 60, 72, 84])
        marital_status = random.choice(MARITAL_STATUSES)
        education = random.choice(EDUCATION_LEVELS)
        dependents = random.randint(0, 5)

        row = {
            "age": age,
            "income": income,
            "loan_amount": loan_amount,
            "loan_term_months": loan_term_months,
            "credit_score": credit_score,
            "employment_years": employment_years,
            "marital_status": marital_status,
            "education": education,
            "dependents": dependents,
        }
        row["default"] = int(random.random() < default_probability(row))
        data.append(row)

    return pd.DataFrame(data)


def main():
    parser = argparse.ArgumentParser(description="Generate a synthetic credit-risk training dataset.")
    parser.add_argument("--rows", type=int, default=1500)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", default=os.path.join("data", "credit_dataset.csv"))
    args = parser.parse_args()

    df = build_dataset(args.rows, args.seed)
    output_dir = os.path.dirname(args.output)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    df.to_csv(args.output, index=False)
    print(f"Generated {len(df)} rows at {args.output}")
    print(f"Default rate: {df['default'].mean() * 100:.2f}%")


if __name__ == "__main__":
    main()
