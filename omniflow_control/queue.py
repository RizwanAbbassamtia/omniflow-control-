"""Job queue that runs prompts against the account an operator activated.

Rules enforced here:

* Only one account is active at a time and only a person activates it.
* A job runs only if the active account has enough credits left.
* When credits run out the account is marked exhausted, the queue pauses and
  ``AccountExhausted`` is raised so the UI can ask an operator what to do.
  Nothing in this module selects another account.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .db import Database
from .omniflash import QuotaExhausted
from .runners import AccountContext, JobSpec, Runner, RunResult

ACTIVE_ACCOUNT_KEY = "active_account_id"
OUTPUT_ROOT_KEY = "output_root"
DEFAULT_CREDITS_PER_JOB_KEY = "default_credits_per_job"


class NoActiveAccount(Exception):
    pass


class AccountExhausted(Exception):
    def __init__(self, account_id: int, email: str, remaining: int, needed: int):
        self.account_id = account_id
        self.email = email
        self.remaining = remaining
        self.needed = needed
        super().__init__(
            f"{email} has {remaining} credits left, next job needs {needed}. "
            "Activate another account to continue."
        )


class QueueEmpty(Exception):
    pass


@dataclass
class RunReport:
    job_id: int
    account_id: int
    result: Optional[RunResult]
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.result is not None


def get_active_account_id(db: Database) -> Optional[int]:
    v = db.get_setting(ACTIVE_ACCOUNT_KEY)
    return int(v) if v else None


def activate_account(db: Database, account_id: int, actor: str = "") -> None:
    """A person chooses which account the queue works on."""
    acc = db.get_account(account_id)
    if acc is None:
        raise KeyError(account_id)
    if acc["status"] == "disabled":
        raise ValueError(f"{acc['email']} is disabled")
    db.set_setting(ACTIVE_ACCOUNT_KEY, str(account_id))
    db.log("activate", f"{acc['email']} activated", actor)


def deactivate_account(db: Database, actor: str = "") -> None:
    db.set_setting(ACTIVE_ACCOUNT_KEY, "")
    db.log("activate", "no active account", actor)


def output_root(db: Database) -> Path:
    return Path(db.get_setting(OUTPUT_ROOT_KEY) or (Path.home() / "OmniFlowOutput"))


def _safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_") or "job"


def account_context(db: Database, account_id: int, vault=None) -> AccountContext:
    acc = db.get_account(account_id)
    api_key = ""
    if vault is not None and vault.is_unlocked:
        creds = vault.load(account_id)
        api_key = creds.api_key if creds else ""
    return AccountContext(
        account_id=account_id,
        email=acc["email"],
        profile_dir=acc["profile_dir"],
        credits_remaining=db.effective_remaining(account_id),
        api_key=api_key,
        api_base_url=acc["api_base_url"],
    )


def run_next(db: Database, runner: Runner, actor: str = "", vault=None) -> RunReport:
    """Run the oldest queued job on the active account."""
    account_id = get_active_account_id(db)
    if account_id is None:
        raise NoActiveAccount("no account is active; an operator must activate one")
    acc = db.get_account(account_id)
    if acc is None:
        deactivate_account(db, actor)
        raise NoActiveAccount("active account no longer exists")
    if acc["status"] == "exhausted":
        raise AccountExhausted(account_id, acc["email"], db.credits_remaining(account_id), 0)

    job = db.next_queued_job()
    if job is None:
        raise QueueEmpty()

    remaining = db.effective_remaining(account_id)
    default_cost = int(db.get_setting(DEFAULT_CREDITS_PER_JOB_KEY) or 0)
    needed = int(job["credits_cost"]) or default_cost
    if remaining <= 0 or remaining < needed:
        db.update_account(account_id, status="exhausted")
        db.log("exhausted", f"{acc['email']} out of credits ({remaining} left)", actor)
        raise AccountExhausted(account_id, acc["email"], remaining, needed)

    out_dir = output_root(db) / _safe_name(acc["email"]) / f"{job['id']:06d}-{_safe_name(job['title'] or 'job')}"
    spec = JobSpec(job_id=int(job["id"]), title=job["title"], prompt=job["prompt"], output_dir=out_dir)
    ctx = account_context(db, account_id, vault)
    db.mark_job_running(int(job["id"]), account_id)
    try:
        result = runner.generate(spec, ctx)
    except QuotaExhausted as exc:
        # The service itself said no: put the job back, park the account, stop.
        db.requeue_job(int(job["id"]))
        db.update_account(account_id, status="exhausted")
        db.add_quota_snapshot(account_id, remaining=0, error="")
        db.log("exhausted", f"{acc['email']} refused by Omni Flash: {exc}", actor)
        raise AccountExhausted(account_id, acc["email"], 0, needed) from exc
    except Exception as exc:  # runner problems must not crash the queue loop
        db.mark_job_failed(int(job["id"]), str(exc))
        db.log("job-failed", f"job {job['id']} failed on {acc['email']}: {exc}", actor)
        return RunReport(int(job["id"]), account_id, None, str(exc))

    spent = result.credits_spent if result.credits_spent else needed
    db.mark_job_done(int(job["id"]), result.output_path, spent)
    if spent:
        db.add_credit_entry(account_id, -spent, f"job {job['id']}", int(job["id"]))
    db.log("job-done", f"job {job['id']} done on {acc['email']} ({spent} credits)", actor)
    snap = db.latest_quota(account_id)
    if snap is not None and not snap["error"] and snap["remaining"] is not None:
        db.add_quota_snapshot(account_id, billing_mode=snap["billing_mode"], label=snap["label"],
                              quota_limit=snap["quota_limit"],
                              used=(snap["used"] or 0) + spent, remaining=max(0, int(snap["remaining"]) - spent),
                              balance=snap["balance"], reset_at=snap["reset_at"])
    if db.effective_remaining(account_id) <= 0:
        db.update_account(account_id, status="exhausted")
        db.log("exhausted", f"{acc['email']} out of credits", actor)
    return RunReport(int(job["id"]), account_id, result)


def run_batch(db: Database, runner: Runner, max_jobs: int, actor: str = "", vault=None) -> list[RunReport]:
    """Run up to ``max_jobs`` queued jobs on the active account.

    Stops early, without switching accounts, when the queue empties or the
    active account runs out of credits.
    """
    reports: list[RunReport] = []
    for _ in range(max_jobs):
        try:
            reports.append(run_next(db, runner, actor, vault))
        except QueueEmpty:
            break
        except AccountExhausted:
            break
    return reports
