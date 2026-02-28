# Credit Risk Assessment using Ensemble Learning

## Project Overview
This project predicts whether a customer will default on a loan using Machine Learning ensemble models.

## Technologies Used
- Python
- Pandas
- Scikit-learn
- XGBoost
- Flask

## Models Used
- Logistic Regression
- Random Forest
- XGBoost

Best Model selected using Cross Validation F1 Score.

## How to Run

1. Install requirements:
pip install -r requirements.txt

2. Train model:
python src/train.py --data data/credit_dataset.csv --target default

3. Run API:
python app.py

Server runs at:
http://127.0.0.1:5000

## Prediction Endpoint
POST /predict

Returns probability of default and predicted class.

## Author
Pavan Sai
