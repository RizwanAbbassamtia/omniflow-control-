"""CSV import/export of the account list (never includes passwords)."""
from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from typing import Optional

from .db import Database
from .vault import Credentials, Vault

COLUMNS = ["email", "label", "team_member", "credits_monthly", "cycle_start", "profile_dir", "notes"]


def import_accounts(db: Database, path: Path, vault: Optional[Vault] = None) -> tuple[int, int]:
    """Import accounts from CSV. Returns (added, skipped).

    Optional ``password`` / ``recovery_email`` columns are stored in the vault
    when an unlocked vault is supplied, and are never written to the accounts
    table.
    """
    added = skipped = 0
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            email = (row.get("email") or "").strip().lower()
            if not email or db.get_account_by_email(email):
                skipped += 1
                continue
            cs = (row.get("cycle_start") or "").strip()
            account_id = db.add_account(
                email=email,
                label=(row.get("label") or "").strip(),
                team_member=(row.get("team_member") or "").strip(),
                credits_monthly=int(row.get("credits_monthly") or 1000),
                cycle_start=date.fromisoformat(cs) if cs else None,
                profile_dir=(row.get("profile_dir") or "").strip(),
                notes=(row.get("notes") or "").strip(),
            )
            if vault is not None and vault.is_unlocked and (row.get("password") or row.get("recovery_email")):
                vault.store(
                    account_id,
                    Credentials(
                        password=row.get("password") or "",
                        recovery_email=row.get("recovery_email") or "",
                        recovery_phone=row.get("recovery_phone") or "",
                    ),
                )
            added += 1
    db.log("import", f"imported {added} accounts from {path.name}, skipped {skipped}")
    return added, skipped


def export_accounts(db: Database, path: Path) -> int:
    rows = db.list_accounts()
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS + ["status", "credits_remaining"])
        for a in rows:
            w.writerow([a[c] for c in COLUMNS] + [a["status"], db.credits_remaining(a["id"])])
    return len(rows)
