"""Command line entry points.

    python -m omniflow_control.cli init-vault
    python -m omniflow_control.cli import-accounts accounts.csv
    python -m omniflow_control.cli export-accounts out.csv
    python -m omniflow_control.cli add-job "prompt text" --title "Scene 1" --cost 10
    python -m omniflow_control.cli activate you@gmail.com
    python -m omniflow_control.cli run --max 20 [--dry-run]
    python -m omniflow_control.cli status
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

from . import queue as q
from .accounts_io import export_accounts, import_accounts
from .db import DEFAULT_DB, Database
from .runners import CommandRunner, DryRunRunner
from .vault import Vault

RUNNER_COMMAND_KEY = "runner_command"


def _db(args) -> Database:
    return Database(args.db)


def cmd_init_vault(args):
    db = _db(args)
    v = Vault(db)
    if v.is_initialised:
        print("vault already initialised")
        return 0
    pw = getpass.getpass("New master password: ")
    if pw != getpass.getpass("Repeat: "):
        print("passwords differ", file=sys.stderr)
        return 1
    v.initialise(pw)
    print("vault initialised")
    return 0


def cmd_import(args):
    db = _db(args)
    vault = None
    if args.with_passwords:
        vault = Vault(db)
        vault.unlock(os.environ.get("OMNI_MASTER_PASSWORD") or getpass.getpass("Master password: "))
    added, skipped = import_accounts(db, Path(args.csv), vault)
    print(f"added {added}, skipped {skipped}")
    return 0


def cmd_export(args):
    n = export_accounts(_db(args), Path(args.csv))
    print(f"exported {n} accounts")
    return 0


def cmd_add_job(args):
    db = _db(args)
    jid = db.add_job(args.prompt, title=args.title, credits_cost=args.cost, created_by=args.actor)
    print(f"job {jid} queued")
    return 0


def cmd_activate(args):
    db = _db(args)
    acc = db.get_account_by_email(args.email)
    if acc is None:
        print("no such account", file=sys.stderr)
        return 1
    q.activate_account(db, int(acc["id"]), args.actor)
    print(f"active: {acc['email']} ({db.credits_remaining(acc['id'])} credits left)")
    return 0


def cmd_run(args):
    db = _db(args)
    if args.dry_run:
        runner = DryRunRunner(credits_per_job=args.dry_run_cost)
    else:
        cmd = db.get_setting(RUNNER_COMMAND_KEY) or ""
        if not cmd:
            print("no runner command configured; set it in the app Settings page or use --dry-run",
                  file=sys.stderr)
            return 1
        runner = CommandRunner(cmd)
    try:
        reports = q.run_batch(db, runner, args.max, args.actor)
    except q.NoActiveAccount as exc:
        print(f"stopped: {exc}", file=sys.stderr)
        return 2
    for r in reports:
        print(f"job {r.job_id}: {'ok ' + r.result.output_path if r.ok else 'FAILED ' + r.error}")
    active = q.get_active_account_id(db)
    if active:
        acc = db.get_account(active)
        print(f"{acc['email']}: status={acc['status']} remaining={db.credits_remaining(active)}")
        if acc["status"] == "exhausted":
            print("active account is out of credits; an operator must activate another one")
            return 3
    return 0


def cmd_status(args):
    db = _db(args)
    active = q.get_active_account_id(db)
    print("active account:", db.get_account(active)["email"] if active else "none")
    print("jobs:", db.job_counts())
    for st in ("active", "exhausted", "disabled"):
        print(f"{st}: {len(db.list_accounts(st))}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="omniflow_control")
    p.add_argument("--db", default=str(DEFAULT_DB))
    p.add_argument("--actor", default=os.environ.get("USERNAME") or os.environ.get("USER") or "")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init-vault").set_defaults(fn=cmd_init_vault)
    s = sub.add_parser("import-accounts"); s.add_argument("csv")
    s.add_argument("--with-passwords", action="store_true"); s.set_defaults(fn=cmd_import)
    s = sub.add_parser("export-accounts"); s.add_argument("csv"); s.set_defaults(fn=cmd_export)
    s = sub.add_parser("add-job"); s.add_argument("prompt"); s.add_argument("--title", default="")
    s.add_argument("--cost", type=int, default=0); s.set_defaults(fn=cmd_add_job)
    s = sub.add_parser("activate"); s.add_argument("email"); s.set_defaults(fn=cmd_activate)
    s = sub.add_parser("run"); s.add_argument("--max", type=int, default=10)
    s.add_argument("--dry-run", action="store_true"); s.add_argument("--dry-run-cost", type=int, default=0)
    s.set_defaults(fn=cmd_run)
    sub.add_parser("status").set_defaults(fn=cmd_status)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
