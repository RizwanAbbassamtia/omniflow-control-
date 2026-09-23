"""SQLite storage for accounts, credentials, credits, jobs and settings."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Iterator, Optional

DEFAULT_DB = Path.home() / ".omniflow_control" / "control.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    email           TEXT NOT NULL UNIQUE,
    label           TEXT NOT NULL DEFAULT '',
    team_member     TEXT NOT NULL DEFAULT '',
    credits_monthly INTEGER NOT NULL DEFAULT 1000,
    cycle_start     TEXT NOT NULL,              -- ISO timestamp the current credit month began
    status          TEXT NOT NULL DEFAULT 'active', -- active | exhausted | disabled
    profile_dir     TEXT NOT NULL DEFAULT '',   -- browser profile the human logged in with
    proxy_type      TEXT NOT NULL DEFAULT '',   -- http | https | socks5; empty means no proxy
    proxy_host      TEXT NOT NULL DEFAULT '',
    proxy_port      INTEGER NOT NULL DEFAULT 0,
    api_base_url    TEXT NOT NULL DEFAULT '',   -- Omni Flash API address for this account's key
    notes           TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS credentials (
    account_id  INTEGER PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
    ciphertext  BLOB NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS credit_ledger (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id  INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    delta       INTEGER NOT NULL,               -- negative = spent
    reason      TEXT NOT NULL DEFAULT '',
    job_id      INTEGER,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt        TEXT NOT NULL,
    title         TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'queued', -- queued | running | done | failed | cancelled
    account_id    INTEGER REFERENCES accounts(id),
    credits_cost  INTEGER NOT NULL DEFAULT 0,     -- expected cost, used for the pre-run check
    credits_spent INTEGER NOT NULL DEFAULT 0,     -- actual cost reported by the runner
    output_path   TEXT NOT NULL DEFAULT '',
    error         TEXT NOT NULL DEFAULT '',
    created_by    TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    started_at    TEXT,
    finished_at   TEXT
);
CREATE TABLE IF NOT EXISTS quota_snapshots (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id   INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    fetched_at   TEXT NOT NULL,
    billing_mode TEXT NOT NULL DEFAULT '',
    label        TEXT NOT NULL DEFAULT '',
    quota_limit  INTEGER,
    used         INTEGER,
    remaining    INTEGER,
    balance      REAL,
    reset_at     TEXT NOT NULL DEFAULT '',
    error        TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,
    message    TEXT NOT NULL,
    actor      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="microseconds")


def _cycle_iso(value: date | datetime) -> str:
    """Start-of-cycle marker comparable with ledger timestamps (lexicographic ISO)."""
    if isinstance(value, datetime):
        return value.isoformat(timespec="microseconds")
    return datetime(value.year, value.month, value.day).isoformat()


class Database:
    def __init__(self, path: Path | str = DEFAULT_DB):
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(accounts)")}
        if "api_base_url" not in cols:
            self.conn.execute("ALTER TABLE accounts ADD COLUMN api_base_url TEXT NOT NULL DEFAULT ''")
        for name, definition in (("proxy_type", "TEXT NOT NULL DEFAULT ''"),
                                 ("proxy_host", "TEXT NOT NULL DEFAULT ''"),
                                 ("proxy_port", "INTEGER NOT NULL DEFAULT 0")):
            if name not in cols:
                self.conn.execute(f"ALTER TABLE accounts ADD COLUMN {name} {definition}")
        self.conn.commit()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    # ---- settings -------------------------------------------------------
    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        row = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    # ---- events ---------------------------------------------------------
    def log(self, kind: str, message: str, actor: str = "") -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO events(kind,message,actor,created_at) VALUES(?,?,?,?)",
                (kind, message, actor, _now()),
            )

    def recent_events(self, limit: int = 50) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()

    # ---- accounts -------------------------------------------------------
    def add_account(
        self,
        email: str,
        label: str = "",
        team_member: str = "",
        credits_monthly: int = 1000,
        cycle_start: Optional[date] = None,
        profile_dir: str = "",
        notes: str = "",
    ) -> int:
        cycle_start_iso = _cycle_iso(cycle_start) if cycle_start else _now()
        with self.tx() as c:
            cur = c.execute(
                "INSERT INTO accounts(email,label,team_member,credits_monthly,cycle_start,"
                "profile_dir,notes,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    email.strip().lower(),
                    label,
                    team_member,
                    credits_monthly,
                    cycle_start_iso,
                    profile_dir,
                    notes,
                    _now(),
                ),
            )
            return int(cur.lastrowid)

    def update_account(self, account_id: int, **fields) -> None:
        allowed = {
            "label", "team_member", "credits_monthly", "cycle_start",
            "status", "profile_dir", "notes", "api_base_url",
            "proxy_type", "proxy_host", "proxy_port",
        }
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"cannot update fields: {sorted(bad)}")
        if not fields:
            return
        if isinstance(fields.get("cycle_start"), (date, datetime)):
            fields["cycle_start"] = _cycle_iso(fields["cycle_start"])
        sets = ", ".join(f"{k}=?" for k in fields)
        with self.tx() as c:
            c.execute(f"UPDATE accounts SET {sets} WHERE id=?", (*fields.values(), account_id))

    def get_account(self, account_id: int) -> Optional[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()

    def get_account_by_email(self, email: str) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM accounts WHERE email=?", (email.strip().lower(),)
        ).fetchone()

    def list_accounts(self, status: Optional[str] = None) -> list[sqlite3.Row]:
        if status:
            return self.conn.execute(
                "SELECT * FROM accounts WHERE status=? ORDER BY email", (status,)
            ).fetchall()
        return self.conn.execute("SELECT * FROM accounts ORDER BY email").fetchall()

    def delete_account(self, account_id: int) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM accounts WHERE id=?", (account_id,))

    # ---- credentials ----------------------------------------------------
    def put_credentials(self, account_id: int, ciphertext: bytes) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO credentials(account_id,ciphertext,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(account_id) DO UPDATE SET ciphertext=excluded.ciphertext, "
                "updated_at=excluded.updated_at",
                (account_id, ciphertext, _now()),
            )

    def get_credentials(self, account_id: int) -> Optional[bytes]:
        row = self.conn.execute(
            "SELECT ciphertext FROM credentials WHERE account_id=?", (account_id,)
        ).fetchone()
        return bytes(row["ciphertext"]) if row else None

    # ---- credits --------------------------------------------------------
    def add_credit_entry(
        self, account_id: int, delta: int, reason: str = "", job_id: Optional[int] = None
    ) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO credit_ledger(account_id,delta,reason,job_id,created_at) "
                "VALUES(?,?,?,?,?)",
                (account_id, delta, reason, job_id, _now()),
            )

    def credits_used_this_cycle(self, account_id: int) -> int:
        acc = self.get_account(account_id)
        if acc is None:
            raise KeyError(account_id)
        row = self.conn.execute(
            "SELECT COALESCE(SUM(-delta),0) AS used FROM credit_ledger "
            "WHERE account_id=? AND delta<0 AND created_at>=?",
            (account_id, acc["cycle_start"]),
        ).fetchone()
        return int(row["used"])

    def credits_remaining(self, account_id: int) -> int:
        acc = self.get_account(account_id)
        if acc is None:
            raise KeyError(account_id)
        return int(acc["credits_monthly"]) - self.credits_used_this_cycle(account_id)

    # ---- live quota snapshots (accounts with an Omni Flash key) -------------
    def add_quota_snapshot(self, account_id: int, *, billing_mode: str = "", label: str = "",
                           quota_limit=None, used=None, remaining=None, balance=None,
                           reset_at: str = "", error: str = "") -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO quota_snapshots(account_id,fetched_at,billing_mode,label,quota_limit,used,"
                "remaining,balance,reset_at,error) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (account_id, _now(), billing_mode, label, quota_limit, used, remaining, balance, reset_at, error),
            )

    def latest_quota(self, account_id: int) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM quota_snapshots WHERE account_id=? ORDER BY id DESC LIMIT 1", (account_id,)
        ).fetchone()

    def effective_remaining(self, account_id: int) -> int:
        """Live remaining from the newest successful quota check when there is
        one, otherwise the manual monthly ledger."""
        snap = self.latest_quota(account_id)
        if snap is not None and not snap["error"] and snap["remaining"] is not None:
            return int(snap["remaining"])
        return self.credits_remaining(account_id)

    def start_new_cycle(self, account_id: int, on: Optional[date] = None) -> None:
        """Operator confirms Google renewed the credits; reset the month window.

        Spend recorded before this moment no longer counts against the new cycle.
        """
        stamp = _cycle_iso(on) if on else _now()
        self.update_account(account_id, cycle_start=stamp, status="active")
        self.log("cycle", f"new credit cycle started for account {account_id} on {on}")

    # ---- jobs -----------------------------------------------------------
    def add_job(
        self, prompt: str, title: str = "", credits_cost: int = 0, created_by: str = ""
    ) -> int:
        with self.tx() as c:
            cur = c.execute(
                "INSERT INTO jobs(prompt,title,credits_cost,created_by,created_at) "
                "VALUES(?,?,?,?,?)",
                (prompt, title, credits_cost, created_by, _now()),
            )
            return int(cur.lastrowid)

    def get_job(self, job_id: int) -> Optional[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()

    def list_jobs(self, status: Optional[str] = None, limit: int = 500) -> list[sqlite3.Row]:
        if status:
            return self.conn.execute(
                "SELECT * FROM jobs WHERE status=? ORDER BY id LIMIT ?", (status, limit)
            ).fetchall()
        return self.conn.execute(
            "SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()

    def next_queued_job(self) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM jobs WHERE status='queued' ORDER BY id LIMIT 1"
        ).fetchone()

    def mark_job_running(self, job_id: int, account_id: int) -> None:
        with self.tx() as c:
            c.execute(
                "UPDATE jobs SET status='running', account_id=?, started_at=? WHERE id=?",
                (account_id, _now(), job_id),
            )

    def mark_job_done(self, job_id: int, output_path: str, credits_spent: int) -> None:
        with self.tx() as c:
            c.execute(
                "UPDATE jobs SET status='done', output_path=?, credits_spent=?, finished_at=? "
                "WHERE id=?",
                (output_path, credits_spent, _now(), job_id),
            )

    def mark_job_failed(self, job_id: int, error: str) -> None:
        with self.tx() as c:
            c.execute(
                "UPDATE jobs SET status='failed', error=?, finished_at=? WHERE id=?",
                (error[:2000], _now(), job_id),
            )

    def requeue_job(self, job_id: int) -> None:
        with self.tx() as c:
            c.execute(
                "UPDATE jobs SET status='queued', error='', account_id=NULL, "
                "started_at=NULL, finished_at=NULL WHERE id=?",
                (job_id,),
            )

    def cancel_job(self, job_id: int) -> None:
        with self.tx() as c:
            c.execute(
                "UPDATE jobs SET status='cancelled', finished_at=? WHERE id=? AND status='queued'",
                (_now(), job_id),
            )

    def job_counts(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status"
        ).fetchall()
        return {r["status"]: int(r["n"]) for r in rows}

    def close(self) -> None:
        self.conn.close()
