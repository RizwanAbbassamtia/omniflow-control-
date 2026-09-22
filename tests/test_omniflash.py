import json
from pathlib import Path

import pytest

from omniflow_control import ninesigma, queue as q
from omniflow_control.db import Database
from omniflow_control.omniflash import OmniFlashClient, OmniFlashError, QuotaExhausted
from omniflow_control.runners import OmniFlashRunner
from omniflow_control.vault import Credentials, Vault


class Resp:
    def __init__(self, code, data=None, content=b""):
        self.status_code = code
        self._data = data
        self.content = content
        self.headers = {"Content-Type": "video/mp4"}
        self.text = json.dumps(data) if data else ""

    def json(self):
        if self._data is None:
            raise ValueError
        return self._data


class FakeOmniFlash:
    """Stands in for the Generation API v2: a key with a daily limit."""

    def __init__(self, limit=3):
        self.limit, self.polls, self.calls = limit, {}, []
        self.used_by_key = {}
        self.key = ""

    @property
    def used(self):
        return self.used_by_key.get(self.key, 0)

    @used.setter
    def used(self, value):
        self.used_by_key[self.key] = value

    def __call__(self, method, url, headers, body, timeout, stream):
        self.calls.append((method, url))
        if url.startswith("https://cdn.example/"):
            assert "Authorization" not in headers          # key never goes to a third-party host
            return Resp(200, content=b"\x00" * 20_000)
        assert headers.get("Authorization", "").startswith("Bearer ")
        self.key = headers["Authorization"]
        if url.endswith("/api/v2/usage"):
            return Resp(200, {"label": "team-a", "omniflash_daily_limit": self.limit,
                              "omniflash_used_today": self.used,
                              "omniflash_remaining_today": self.limit - self.used,
                              "reset_at": "2026-09-23T00:00:00Z"})
        if url.endswith("/api/v2/videos/generate"):
            if self.used >= self.limit:
                return Resp(429, {"detail": "daily limit reached"})
            self.used += 1
            jid = f"job{len(self.polls) + 1}"
            self.polls[jid] = 0
            return Resp(200, {"job_id": jid, "status": "pending"})
        if "/api/v2/videos/status/" in url:
            jid = url.rsplit("/", 1)[1]
            self.polls[jid] += 1
            if self.polls[jid] < 2:
                return Resp(200, {"status": "processing"})
            return Resp(200, {"status": "done", "video_url": f"https://cdn.example/{jid}.mp4?t=1",
                              "remaining_today": self.limit - self.used})
        return Resp(404, {"detail": "no"})


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    d.set_setting(q.OUTPUT_ROOT_KEY, str(tmp_path / "out"))
    yield d
    d.close()


@pytest.fixture
def vault(db):
    v = Vault(db)
    v.initialise("master-password")
    return v


def test_client_generates_clip(tmp_path):
    api = FakeOmniFlash()
    c = OmniFlashClient("k", "omni.example.com", transport=api, sleep=lambda s: None)
    assert c.base_url == "https://omni.example.com"
    assert c.usage().remaining == 3
    path, final = c.generate("a scene", tmp_path / "clip.mp4")
    assert path.exists() and path.stat().st_size == 20_000
    assert final["remaining_today"] == 2


def test_client_reports_quota_and_key_errors():
    def refused(method, url, headers, body, timeout, stream):
        return Resp(403, {"detail": "disabled"})
    with pytest.raises(OmniFlashError) as e:
        OmniFlashClient("k", "https://x").usage.__func__(OmniFlashClient("k", "https://x", transport=refused))
    assert e.value.fatal
    with pytest.raises(OmniFlashError):
        OmniFlashClient("", "https://x")


def test_sync_quota_stores_snapshot_and_status(db, vault):
    a = db.add_account("a@gmail.com", credits_monthly=1000)
    db.update_account(a, api_base_url="https://omni.example.com")
    vault.store(a, Credentials(api_key="key-a"))
    api = FakeOmniFlash(limit=0)
    quota = ninesigma.sync_quota(db, vault, a, transport=api)
    assert quota.remaining == 0
    assert db.get_account(a)["status"] == "exhausted"
    assert db.effective_remaining(a) == 0          # live number beats the manual ledger
    api.limit = 5
    ninesigma.sync_quota(db, vault, a, transport=api)
    assert db.get_account(a)["status"] == "active"
    assert db.effective_remaining(a) == 5


def test_sync_all_only_touches_keyed_accounts(db, vault):
    a = db.add_account("a@gmail.com"); db.update_account(a, api_base_url="https://o"); vault.store(a, Credentials(api_key="k"))
    b = db.add_account("b@gmail.com")
    out = ninesigma.sync_all_quotas(db, vault, transport=FakeOmniFlash())
    assert set(out) == {a}
    assert db.latest_quota(b) is None


def test_omniflash_runner_stops_when_service_refuses(db, vault):
    a = db.add_account("a@gmail.com"); db.update_account(a, api_base_url="https://omni.example.com")
    b = db.add_account("b@gmail.com"); db.update_account(b, api_base_url="https://omni.example.com")
    vault.store(a, Credentials(api_key="key-a")); vault.store(b, Credentials(api_key="key-b"))
    api = FakeOmniFlash(limit=2)
    runner = OmniFlashRunner(transport=api, sleep=lambda s: None)
    q.activate_account(db, a)
    for i in range(3):
        db.add_job(f"scene {i}")
    reports = q.run_batch(db, runner, 10, vault=vault)
    assert len(reports) == 2 and all(r.ok for r in reports)
    assert Path(reports[0].result.output_path).suffix == ".mp4"
    assert db.get_account(a)["status"] == "exhausted"
    assert db.job_counts() == {"done": 2, "queued": 1}     # third job went back to the queue
    assert q.get_active_account_id(db) == a                 # nothing switched
    assert api.used_by_key == {"Bearer key-a": 2} and db.effective_remaining(b) == 1000
    q.activate_account(db, b, actor="operator")
    r = q.run_next(db, runner, vault=vault)
    assert r.ok and r.account_id == b


def test_hand_over_writes_key_and_address(db, vault, tmp_path, monkeypatch):
    # a stand-in for the sceneforge package: same module paths, no DPAPI
    pkg = tmp_path / "code" / "sceneforge"
    (pkg / "production").mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "seal.py").write_text("def available():\n    return True\n")
    (pkg / "production" / "__init__.py").write_text("")
    (pkg / "production" / "credentials.py").write_text(
        "import json, pathlib\nSTORE = pathlib.Path(__file__).with_name('store.json')\n"
        "class Credentials:\n"
        "    def set(self, provider, key):\n"
        "        STORE.write_text(json.dumps({provider: key}))\n"
    )
    cfg_dir = tmp_path / "cfg"
    a = db.add_account("a@gmail.com"); db.update_account(a, api_base_url="https://omni.example.com")
    vault.store(a, Credentials(api_key="key-a"))
    with pytest.raises(ninesigma.HandOverError):
        ninesigma.hand_over(db, vault, a)
    msg = ninesigma.hand_over(db, vault, a, sceneforge_dir=str(tmp_path / "code"), config_dir=str(cfg_dir))
    assert "a@gmail.com" in msg
    assert json.loads((pkg / "production" / "store.json").read_text()) == {"omniflash": "key-a"}
    assert json.loads((cfg_dir / "config.json").read_text())["omniflash_base_url"] == "https://omni.example.com"
    b = db.add_account("b@gmail.com")
    with pytest.raises(ninesigma.HandOverError):
        ninesigma.hand_over(db, vault, b, sceneforge_dir=str(tmp_path / "code"), config_dir=str(cfg_dir))


def test_old_credentials_blob_without_api_key_still_loads(db, vault):
    a = db.add_account("a@gmail.com")
    from omniflow_control.vault import Credentials as C
    import json as j
    blob = vault._require().encrypt(j.dumps({"password": "p", "recovery_email": "", "recovery_phone": "", "notes": "", "extra": {}}).encode())
    db.put_credentials(a, blob)
    assert vault.load(a).api_key == "" and vault.load(a).password == "p"
