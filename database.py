import os
import sqlite3


BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def default_sqlite_path():
    if os.environ.get("VERCEL") and not os.environ.get("DATABASE_URL"):
        return os.path.join("/tmp", "credisense-demo.db")
    return os.path.join(BASE_DIR, "app.db")


DATABASE_PATH = os.environ.get("DATABASE_PATH", default_sqlite_path())
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # psycopg is only required when DATABASE_URL points to Postgres.
    psycopg = None
    dict_row = None


def is_postgres():
    return DATABASE_URL.startswith(("postgres://", "postgresql://"))


def database_label():
    return "postgresql" if is_postgres() else "sqlite"


if psycopg:
    IntegrityError = (sqlite3.IntegrityError, psycopg.IntegrityError)
else:
    IntegrityError = (sqlite3.IntegrityError,)


def postgres_url():
    if DATABASE_URL.startswith("postgres://"):
        return "postgresql://" + DATABASE_URL[len("postgres://") :]
    return DATABASE_URL


def translate_query(query):
    return query.replace("?", "%s")


def translate_schema(query):
    return translate_query(query).replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")


class CursorProxy:
    def __init__(self, cursor, backend):
        self.cursor = cursor
        self.backend = backend

    def fetchone(self):
        return self.cursor.fetchone()

    def fetchall(self):
        return self.cursor.fetchall()

    @property
    def lastrowid(self):
        return getattr(self.cursor, "lastrowid", None)


class DatabaseConnection:
    def __init__(self):
        self.backend = database_label()
        if self.backend == "postgresql":
            if psycopg is None:
                raise RuntimeError("Install psycopg[binary] to use DATABASE_URL with PostgreSQL.")
            self.conn = psycopg.connect(postgres_url(), row_factory=dict_row)
        else:
            self.conn = sqlite3.connect(DATABASE_PATH)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA foreign_keys = ON")
            self.conn.execute("PRAGMA journal_mode = WAL")

    def execute(self, query, params=()):
        if self.backend == "postgresql":
            cursor = self.conn.cursor()
            cursor.execute(translate_query(query), params)
            return CursorProxy(cursor, self.backend)
        return CursorProxy(self.conn.execute(query, params), self.backend)

    def executescript(self, script):
        if self.backend == "postgresql":
            for statement in script.split(";"):
                statement = statement.strip()
                if statement:
                    self.execute(translate_schema(statement))
            return None
        return self.conn.executescript(script)

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        self.conn.close()


def get_db():
    return DatabaseConnection()
