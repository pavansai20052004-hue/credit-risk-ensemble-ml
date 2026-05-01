# Start CrediSense AI On Windows

Use **one** of these options. You do not need a separate frontend server.

## Easiest Option

Double-click:

```text
start_local.bat
```

It will install dependencies, prepare the database, and start the Flask app.

Open:

```text
http://127.0.0.1:5000
```

## VS Code Terminal Option

Run:

```powershell
cd C:\Users\pavansai\OneDrive\Desktop\credit-risk-project
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe init_db.py
.\venv\Scripts\python.exe app.py
```

## Important

Do **not** run this locally on Windows:

```powershell
gunicorn app:app
```

Gunicorn is for Render/Linux deployment only. On Windows it fails with:

```text
ModuleNotFoundError: No module named 'fcntl'
```

## Demo Login

```text
customer@credisense.ai
Customer@123
```

## Render Deployment

Render should still use:

```text
gunicorn app:app
```
