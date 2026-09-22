"""Encrypted storage of account logins.

Credentials are encrypted with a key derived from a master password the
operator types when the app starts. The master password is never stored; the
database only holds a random salt and a small verifier blob.
"""
from __future__ import annotations

import base64
import json
import os
from dataclasses import asdict, dataclass, field
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from .db import Database

_SALT_KEY = "vault_salt"
_VERIFIER_KEY = "vault_verifier"
_ITERATIONS = 600_000


class VaultLocked(Exception):
    pass


class WrongMasterPassword(Exception):
    pass


@dataclass
class Credentials:
    password: str = ""
    recovery_email: str = ""
    recovery_phone: str = ""
    notes: str = ""
    extra: dict = field(default_factory=dict)

    def to_bytes(self) -> bytes:
        return json.dumps(asdict(self)).encode()

    @classmethod
    def from_bytes(cls, raw: bytes) -> "Credentials":
        data = json.loads(raw.decode())
        return cls(**data)


def _derive_key(master_password: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(), length=32, salt=salt, iterations=_ITERATIONS
    )
    return base64.urlsafe_b64encode(kdf.derive(master_password.encode()))


class Vault:
    def __init__(self, db: Database):
        self.db = db
        self._fernet: Optional[Fernet] = None

    @property
    def is_initialised(self) -> bool:
        return self.db.get_setting(_SALT_KEY) is not None

    @property
    def is_unlocked(self) -> bool:
        return self._fernet is not None

    def initialise(self, master_password: str) -> None:
        if self.is_initialised:
            raise RuntimeError("vault already initialised")
        if len(master_password) < 8:
            raise ValueError("master password must be at least 8 characters")
        salt = os.urandom(16)
        fernet = Fernet(_derive_key(master_password, salt))
        self.db.set_setting(_SALT_KEY, base64.b64encode(salt).decode())
        self.db.set_setting(_VERIFIER_KEY, fernet.encrypt(b"omniflow-vault-ok").decode())
        self._fernet = fernet
        self.db.log("vault", "vault initialised")

    def unlock(self, master_password: str) -> None:
        salt_b64 = self.db.get_setting(_SALT_KEY)
        verifier = self.db.get_setting(_VERIFIER_KEY)
        if salt_b64 is None or verifier is None:
            raise RuntimeError("vault not initialised")
        fernet = Fernet(_derive_key(master_password, base64.b64decode(salt_b64)))
        try:
            if fernet.decrypt(verifier.encode()) != b"omniflow-vault-ok":
                raise WrongMasterPassword()
        except InvalidToken as exc:
            raise WrongMasterPassword() from exc
        self._fernet = fernet

    def lock(self) -> None:
        self._fernet = None

    def _require(self) -> Fernet:
        if self._fernet is None:
            raise VaultLocked("unlock the vault with the master password first")
        return self._fernet

    def store(self, account_id: int, creds: Credentials) -> None:
        token = self._require().encrypt(creds.to_bytes())
        self.db.put_credentials(account_id, token)

    def load(self, account_id: int) -> Optional[Credentials]:
        blob = self.db.get_credentials(account_id)
        if blob is None:
            return None
        return Credentials.from_bytes(self._require().decrypt(blob))

    def change_master_password(self, old: str, new: str) -> None:
        self.unlock(old)
        old_fernet = self._require()
        if len(new) < 8:
            raise ValueError("master password must be at least 8 characters")
        salt = os.urandom(16)
        new_fernet = Fernet(_derive_key(new, salt))
        rows = self.db.conn.execute("SELECT account_id, ciphertext FROM credentials").fetchall()
        with self.db.tx() as c:
            for r in rows:
                plain = old_fernet.decrypt(bytes(r["ciphertext"]))
                c.execute(
                    "UPDATE credentials SET ciphertext=? WHERE account_id=?",
                    (new_fernet.encrypt(plain), r["account_id"]),
                )
            c.execute(
                "UPDATE settings SET value=? WHERE key=?",
                (base64.b64encode(salt).decode(), _SALT_KEY),
            )
            c.execute(
                "UPDATE settings SET value=? WHERE key=?",
                (new_fernet.encrypt(b"omniflow-vault-ok").decode(), _VERIFIER_KEY),
            )
        self._fernet = new_fernet
        self.db.log("vault", "master password changed")
