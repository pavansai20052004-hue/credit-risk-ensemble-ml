import json
import pandas as pd


def load_dataset(path: str) -> pd.DataFrame:
    """
    Load dataset from CSV file
    """
    df = pd.read_csv(path)
    return df


def normalize_target(y: pd.Series):
    """
    Convert target column to 0/1 format
    """
    if pd.api.types.is_numeric_dtype(y):
        return y.astype(int)

    y = y.astype(str).str.lower().str.strip()

    positive_values = ["yes", "y", "1", "true", "default"]

    return y.apply(lambda x: 1 if x in positive_values else 0)


def load_json(path: str):
    """
    Load JSON file
    """
    with open(path, "r") as f:
        return json.load(f)


def json_to_dataframe(data: dict) -> pd.DataFrame:
    """
    Convert JSON request to DataFrame
    """
    return pd.DataFrame([data])