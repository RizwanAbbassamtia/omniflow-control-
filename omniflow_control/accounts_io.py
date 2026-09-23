"""CSV import/export of the account list (never includes passwords)."""
from __future__ import annotations

import csv
from contextlib import nullcontext
from datetime import date
from pathlib import Path
from typing import Optional, TextIO

from .db import Database
from .vault import Credentials, Vault
from .browser_profiles import ProxyConfig

COLUMNS = ["email", "label", "team_member", "credits_monthly", "cycle_start", "profile_dir",
           "api_base_url", "notes", "proxy_type", "proxy_host", "proxy_port"]


def import_accounts(db: Database, path: Path | TextIO, vault: Optional[Vault] = None) -> tuple[int, int]:
    """Import accounts from CSV. Returns (added, skipped).

    Optional ``password`` / ``recovery_email`` columns are stored in the vault
    when an unlocked vault is supplied, and are never written to the accounts
    table.
    """
    added = skipped = 0
    source = nullcontext(path) if hasattr(path, "read") else open(path, newline="", encoding="utf-8-sig")
    with source as f:
        for row in csv.DictReader(f):
            email = (row.get("email") or "").strip().lower()
            if not email or db.get_account_by_email(email):
                skipped += 1
                continue
            cs = (row.get("cycle_start") or "").strip()
            kind = (row.get("proxy_type") or "").strip().lower()
            host = (row.get("proxy_host") or "").strip()
            port = int(row.get("proxy_port") or 0)
            if kind:
                ProxyConfig(kind, host, port, row.get("proxy_username") or "", row.get("proxy_password") or "").validate()
            secret_fields = ("password", "recovery_email", "recovery_phone", "api_key", "proxy_username", "proxy_password")
            if any(row.get(k) for k in secret_fields) and (vault is None or not vault.is_unlocked):
                raise ValueError("Unlock the vault before importing accounts with credentials or proxy authentication")
            account_id = db.add_account(
                email=email,
                label=(row.get("label") or "").strip(),
                team_member=(row.get("team_member") or "").strip(),
                credits_monthly=int(row.get("credits_monthly") or 1000),
                cycle_start=date.fromisoformat(cs) if cs else None,
                profile_dir=(row.get("profile_dir") or "").strip(),
                notes=(row.get("notes") or "").strip(),
            )
            if (row.get("api_base_url") or "").strip():
                db.update_account(account_id, api_base_url=row["api_base_url"].strip())
            if kind:
                db.update_account(account_id, proxy_type=kind, proxy_host=host, proxy_port=port)
            if any(row.get(k) for k in secret_fields):
                vault.store(
                    account_id,
                    Credentials(
                        password=row.get("password") or "",
                        recovery_email=row.get("recovery_email") or "",
                        recovery_phone=row.get("recovery_phone") or "",
                        api_key=(row.get("api_key") or "").strip(),
                        proxy_username=row.get("proxy_username") or "",
                        proxy_password=row.get("proxy_password") or "",
                    ),
                )
            added += 1
    db.log("import", f"imported {added} accounts from {getattr(path, 'name', 'upload')}, skipped {skipped}")
    return added, skipped


def export_accounts(db: Database, path: Path) -> int:
    rows = db.list_accounts()
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS + ["status", "credits_remaining"])
        for a in rows:
            w.writerow([a[c] for c in COLUMNS] + [a["status"], db.credits_remaining(a["id"])])
    return len(rows)
