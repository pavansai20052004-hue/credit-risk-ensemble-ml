from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier, AdaBoostClassifier
from xgboost import XGBClassifier


def get_models(random_state: int = 42):
    """
    Return a dictionary of candidate models for comparison.
    """

    models = {
        "logistic_regression": LogisticRegression(
            max_iter=2000,
            class_weight="balanced"
        ),

        "random_forest": RandomForestClassifier(
            n_estimators=300,
            random_state=random_state,
            n_jobs=-1,
            class_weight="balanced_subsample"
        ),

        "gradient_boosting": GradientBoostingClassifier(
            random_state=random_state
        ),

        "adaboost": AdaBoostClassifier(
            n_estimators=300,
            learning_rate=0.8,
            random_state=random_state
        ),

        "xgboost": XGBClassifier(
            n_estimators=500,
            learning_rate=0.05,
            max_depth=5,
            subsample=0.9,
            colsample_bytree=0.9,
            reg_lambda=1.0,
            random_state=random_state,
            eval_metric="logloss",
            tree_method="hist"
        )
    }

    return models