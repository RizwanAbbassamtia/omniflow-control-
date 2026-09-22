import json
import sys
from pathlib import Path

import pytest

from omniflow_control import queue as q
from omniflow_control.accounts_io import export_accounts, import_accounts
from omniflow_control.db import Database
from omniflow_control.runners import CommandRunner, DryRunRunner
from omniflow_control.vault import Credentials, Vault, VaultLocked, WrongMasterPassword


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    d.set_setting(q.OUTPUT_ROOT_KEY, str(tmp_path / "out"))
    yield d
    d.close()


def test_credit_ledger(db):
    a = db.add_account("a@gmail.com", credits_monthly=100)
    assert db.credits_remaining(a) == 100
    db.add_credit_entry(a, -30, "job")
    assert db.credits_used_this_cycle(a) == 30
    assert db.credits_remaining(a) == 70


def test_vault_roundtrip(db):
    v = Vault(db)
    a = db.add_account("a@gmail.com")
    with pytest.raises(VaultLocked):
        v.store(a, Credentials(password="x"))
    v.initialise("correct horse battery")
    v.store(a, Credentials(password="s3cret", recovery_email="r@x.com"))
    v.lock()
    with pytest.raises(WrongMasterPassword):
        v.unlock("nope-nope-nope")
    v.unlock("correct horse battery")
    assert v.load(a).password == "s3cret"
    assert b"s3cret" not in db.get_credentials(a)
    v.change_master_password("correct horse battery", "another long one")
    v.lock(); v.unlock("another long one")
    assert v.load(a).recovery_email == "r@x.com"


def test_queue_requires_operator_to_activate(db):
    db.add_account("a@gmail.com")
    db.add_job("p")
    with pytest.raises(q.NoActiveAccount):
        q.run_next(db, DryRunRunner())


def test_queue_runs_and_charges_active_account(db):
    a = db.add_account("a@gmail.com", credits_monthly=25)
    q.activate_account(db, a)
    for i in range(3):
        db.add_job(f"prompt {i}", title=f"scene {i}", credits_cost=10)
    reports = q.run_batch(db, DryRunRunner(credits_per_job=10), max_jobs=10)
    assert [r.ok for r in reports] == [True, True]
    assert db.credits_remaining(a) == 5
    assert db.get_account(a)["status"] == "exhausted"
    assert db.job_counts() == {"done": 2, "queued": 1}
    assert Path(reports[0].result.output_path).read_text() == "prompt 0"


def test_exhausted_account_pauses_and_never_switches(db):
    a = db.add_account("a@gmail.com", credits_monthly=10)
    b = db.add_account("b@gmail.com", credits_monthly=1000)
    q.activate_account(db, a)
    db.add_job("p1", credits_cost=10)
    db.add_job("p2", credits_cost=10)
    q.run_batch(db, DryRunRunner(credits_per_job=10), max_jobs=5)
    assert q.get_active_account_id(db) == a
    with pytest.raises(q.AccountExhausted):
        q.run_next(db, DryRunRunner(credits_per_job=10))
    assert q.get_active_account_id(db) == a
    assert db.credits_remaining(b) == 1000
    # operator picks the next account
    q.activate_account(db, b, actor="rizwan")
    r = q.run_next(db, DryRunRunner(credits_per_job=10))
    assert r.ok and r.account_id == b


def test_runner_failure_marks_job_failed(db):
    a = db.add_account("a@gmail.com")
    q.activate_account(db, a)
    jid = db.add_job("p", credits_cost=5)

    class Boom:
        name = "boom"
        def generate(self, job, account):
            raise RuntimeError("browser crashed")

    r = q.run_next(db, Boom())
    assert not r.ok and "browser crashed" in r.error
    assert db.get_job(jid)["status"] == "failed"
    assert db.credits_remaining(a) == 1000
    db.requeue_job(jid)
    assert db.get_job(jid)["status"] == "queued"


def test_command_runner_contract(db, tmp_path):
    script = tmp_path / "tool.py"
    script.write_text(
        "import json, os, pathlib\n"
        "p = pathlib.Path(os.environ['OMNI_OUTPUT_DIR']) / 'clip.mp4'\n"
        "p.write_bytes(b'x')\n"
        "print('log noise')\n"
        "print(json.dumps({'output_path': str(p), 'credits_spent': 7, 'detail': os.environ['OMNI_ACCOUNT_EMAIL']}))\n"
    )
    a = db.add_account("a@gmail.com", profile_dir="/profiles/a")
    q.activate_account(db, a)
    db.add_job("make a clip", title="Scene 1")
    r = q.run_next(db, CommandRunner(f"{sys.executable} {script}"))
    assert r.ok
    assert r.result.credits_spent == 7 and r.result.detail == "a@gmail.com"
    assert db.credits_remaining(a) == 993
    assert Path(r.result.output_path).exists()


def test_new_cycle_resets_credits(db):
    a = db.add_account("a@gmail.com", credits_monthly=10)
    db.add_credit_entry(a, -10)
    db.update_account(a, status="exhausted")
    assert db.credits_remaining(a) == 0
    db.start_new_cycle(a)
    assert db.credits_remaining(a) == 10
    assert db.get_account(a)["status"] == "active"


def test_csv_import_export(db, tmp_path):
    csv = tmp_path / "acc.csv"
    csv.write_text(
        "email,label,team_member,credits_monthly,password\n"
        "one@gmail.com,A,ali,1000,pw1\n"
        "two@gmail.com,B,sara,1000,\n"
        "one@gmail.com,dup,,1000,\n"
    )
    v = Vault(db); v.initialise("master-password")
    assert import_accounts(db, csv, v) == (2, 1)
    assert v.load(db.get_account_by_email("one@gmail.com")["id"]).password == "pw1"
    out = tmp_path / "out.csv"
    assert export_accounts(db, out) == 2
    assert "pw1" not in out.read_text()
