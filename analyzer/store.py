import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE, salt TEXT, password TEXT);
            CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id INTEGER, expires REAL);
            CREATE TABLE IF NOT EXISTS tokens(hash TEXT PRIMARY KEY, user_id INTEGER, created REAL);
            CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, user_id INTEGER, project TEXT, created REAL, data TEXT);
            CREATE INDEX IF NOT EXISTS run_owner ON runs(user_id, project, created);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        finally:
            db.close()

    @staticmethod
    def password_hash(password, salt):
        return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 240000).hex()

    def register(self, username, password):
        if not 3 <= len(username) <= 40 or not username.replace("_", "").replace("-", "").isalnum():
            raise ValueError("Username: 3–40 letters, numbers, underscores or hyphens.")
        if not 10 <= len(password) <= 200:
            raise ValueError("Password must have 10–200 characters.")
        salt = secrets.token_hex(16)
        with self.connect() as db:
            try:
                cur = db.execute("INSERT INTO users(username,salt,password) VALUES(?,?,?)",
                                 (username, salt, self.password_hash(password, salt)))
            except sqlite3.IntegrityError as exc:
                raise ValueError("Username is already registered.") from exc
            return cur.lastrowid

    def login(self, username, password):
        with self.connect() as db:
            row = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if not row or not hmac.compare_digest(row["password"], self.password_hash(password, row["salt"])):
            raise ValueError("Invalid username or password.")
        return row["id"]

    def session(self, user_id):
        token = secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
            db.execute("INSERT INTO sessions VALUES(?,?,?)", (token, user_id, time.time() + 86400))
        return token

    def user(self, session=None, bearer=None):
        with self.connect() as db:
            if bearer:
                row = db.execute("SELECT user_id FROM tokens WHERE hash=?", (hashlib.sha256(bearer.encode()).hexdigest(),)).fetchone()
            else:
                row = db.execute("SELECT user_id FROM sessions WHERE token=? AND expires>?", (session, time.time())).fetchone()
            if not row:
                return None
            return dict(db.execute("SELECT id,username FROM users WHERE id=?", (row["user_id"],)).fetchone())

    def logout(self, token):
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE token=?", (token,))

    def api_token(self, user_id):
        token = secrets.token_urlsafe(40)
        with self.connect() as db:
            db.execute("DELETE FROM tokens WHERE user_id=?", (user_id,))
            db.execute("INSERT INTO tokens VALUES(?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), user_id, time.time()))
        return token

    def save_run(self, user_id, run):
        with self.connect() as db:
            db.execute("INSERT INTO runs VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                       (run["id"], user_id, run["project"], run["created"], json.dumps(run)))

    def runs(self, user_id, project=None):
        with self.connect() as db:
            if project:
                rows = db.execute("SELECT data FROM runs WHERE user_id=? AND project=? ORDER BY created DESC", (user_id, project)).fetchall()
            else:
                rows = db.execute("SELECT data FROM runs WHERE user_id=? ORDER BY created DESC", (user_id,)).fetchall()
        return [json.loads(r["data"]) for r in rows]

    def run(self, user_id, run_id):
        with self.connect() as db:
            row = db.execute("SELECT data FROM runs WHERE user_id=? AND id=?", (user_id, run_id)).fetchone()
        return json.loads(row["data"]) if row else None

    def history(self, user_id, project, suite, name, before):
        outcomes = []
        for run in reversed(self.runs(user_id, project)):
            if run["created"] >= before:
                continue
            for t in run["tests"]:
                if t["name"] == name and t["suite"] == suite and t["status"] != "skipped":
                    outcomes.append(t["status"])
        return outcomes[-20:]
