"""Bridges to 9 Sigma Automation (the sceneforge app).

Two things the control centre does for 9 Sigma:

* ``sync_quota``: ask Omni Flash for an account's live quota and store it.
* ``hand_over``: put the operator-chosen account's key and address where
  9 Sigma reads them, so its productions run on that account. 9 Sigma keeps
  its key sealed with Windows DPAPI in credentials.json and the address in
  its config.json; this writes both through 9 Sigma's own code.

Choosing which account to hand over is always the operator's click.
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Optional

from .db import Database
from .omniflash import OmniFlashClient, OmniFlashError, Quota
from .vault import Vault

SCENEFORGE_DIR_KEY = "sceneforge_dir"        # folder that contains the `sceneforge` package
NINESIGMA_CONFIG_DIR_KEY = "ninesigma_config_dir"  # folder holding 9 Sigma's config.json


class HandOverError(Exception):
    pass


def account_client(db: Database, vault: Vault, account_id: int, **kwargs) -> OmniFlashClient:
    acc = db.get_account(account_id)
    if acc is None:
        raise KeyError(account_id)
    creds = vault.load(account_id)
    key = creds.api_key if creds else ""
    return OmniFlashClient(key, acc["api_base_url"], **kwargs)


def sync_quota(db: Database, vault: Vault, account_id: int, actor: str = "", **client_kwargs) -> Optional[Quota]:
    """Fetch and store the live quota. Returns None (and stores the error) on failure."""
    acc = db.get_account(account_id)
    try:
        q = account_client(db, vault, account_id, **client_kwargs).usage()
    except OmniFlashError as exc:
        db.add_quota_snapshot(account_id, error=str(exc)[:300])
        db.log("quota", f"{acc['email']}: quota check failed: {exc}", actor)
        if exc.fatal:
            db.update_account(account_id, status="disabled")
        return None
    db.add_quota_snapshot(
        account_id, billing_mode=q.billing_mode, label=q.label, quota_limit=q.limit,
        used=q.used, remaining=q.remaining, balance=q.balance, reset_at=q.reset_at,
    )
    if q.remaining is not None:
        new_status = "exhausted" if q.remaining <= 0 or not q.video_enabled else "active"
        if acc["status"] != "disabled" and acc["status"] != new_status:
            db.update_account(account_id, status=new_status)
    db.log("quota", f"{acc['email']}: {q.summary()}", actor)
    return q


def sync_all_quotas(db: Database, vault: Vault, actor: str = "", **client_kwargs) -> dict[int, Optional[Quota]]:
    out = {}
    for a in db.list_accounts():
        creds = vault.load(a["id"]) if vault.is_unlocked else None
        if creds and creds.api_key and a["api_base_url"]:
            out[a["id"]] = sync_quota(db, vault, a["id"], actor, **client_kwargs)
    return out


def _load_sceneforge(sceneforge_dir: str):
    d = Path(sceneforge_dir)
    if not (d / "sceneforge").is_dir():
        raise HandOverError(f"no `sceneforge` package under {d}")
    if str(d) not in sys.path:
        sys.path.insert(0, str(d))
    try:
        return importlib.import_module("sceneforge.production.credentials"), importlib.import_module("sceneforge.seal")
    except Exception as exc:  # noqa: BLE001
        raise HandOverError(f"could not import 9 Sigma code from {d}: {exc}") from exc


def hand_over(db: Database, vault: Vault, account_id: int, actor: str = "",
              sceneforge_dir: Optional[str] = None, config_dir: Optional[str] = None) -> str:
    """Write this account's Omni Flash key and address into 9 Sigma."""
    acc = db.get_account(account_id)
    if acc is None:
        raise KeyError(account_id)
    creds = vault.load(account_id)
    if not creds or not creds.api_key:
        raise HandOverError(f"{acc['email']} has no Omni Flash API key in the vault")
    if not acc["api_base_url"]:
        raise HandOverError(f"{acc['email']} has no Omni Flash address")
    sceneforge_dir = sceneforge_dir or db.get_setting(SCENEFORGE_DIR_KEY) or ""
    config_dir = config_dir or db.get_setting(NINESIGMA_CONFIG_DIR_KEY) or ""
    if not sceneforge_dir or not config_dir:
        raise HandOverError("set the 9 Sigma code folder and config folder on the Settings page")

    credentials_mod, seal_mod = _load_sceneforge(sceneforge_dir)
    if not seal_mod.available():
        raise HandOverError("9 Sigma seals keys with Windows DPAPI; hand-over only works on the Windows PC that runs 9 Sigma")
    credentials_mod.Credentials().set("omniflash", creds.api_key)

    cfg_path = Path(config_dir) / "config.json"
    try:
        raw = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
    except (OSError, ValueError):
        raw = {}
    raw["omniflash_base_url"] = acc["api_base_url"]
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    db.log("handover", f"9 Sigma now uses {acc['email']}", actor)
    return f"9 Sigma now uses {acc['email']} ({acc['api_base_url']})"
