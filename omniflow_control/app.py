"""Streamlit control centre. Run with:  streamlit run omniflow_control/app.py"""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from omniflow_control import queue as q  # noqa: E402
from omniflow_control.accounts_io import export_accounts, import_accounts  # noqa: E402
from omniflow_control.cli import RUNNER_COMMAND_KEY  # noqa: E402
from omniflow_control.db import DEFAULT_DB, Database  # noqa: E402
from omniflow_control.runners import CommandRunner, DryRunRunner  # noqa: E402
from omniflow_control.vault import Credentials, Vault, WrongMasterPassword  # noqa: E402

st.set_page_config(page_title="Omni Flow Control", layout="wide")


@st.cache_resource
def get_db() -> Database:
    return Database(os.environ.get("OMNI_DB", str(DEFAULT_DB)))


@st.cache_resource
def get_vault() -> Vault:
    return Vault(get_db())


db = get_db()
vault = get_vault()
actor = st.sidebar.text_input("Your name", value=st.session_state.get("actor", ""), key="actor")

# ---- vault gate -------------------------------------------------------------
with st.sidebar.expander("Vault", expanded=not vault.is_unlocked):
    if not vault.is_initialised:
        pw = st.text_input("Create master password", type="password", key="init_pw")
        if st.button("Initialise vault") and pw:
            try:
                vault.initialise(pw)
                st.success("Vault created")
                st.rerun()
            except ValueError as e:
                st.error(str(e))
    elif not vault.is_unlocked:
        pw = st.text_input("Master password", type="password", key="unlock_pw")
        if st.button("Unlock") and pw:
            try:
                vault.unlock(pw)
                st.rerun()
            except WrongMasterPassword:
                st.error("Wrong master password")
    else:
        st.write("Unlocked")
        if st.button("Lock"):
            vault.lock()
            st.rerun()

page = st.sidebar.radio("Page", ["Dashboard", "Accounts", "Queue", "Run", "Settings"])


def account_label(a) -> str:
    return f"{a['email']}  [{a['status']}, {db.credits_remaining(a['id'])} left]"


# ---- Dashboard --------------------------------------------------------------
if page == "Dashboard":
    st.title("Omni Flow Control")
    active_id = q.get_active_account_id(db)
    accounts = db.list_accounts()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Accounts", len(accounts))
    c2.metric("With credits", sum(1 for a in accounts if a["status"] == "active" and db.credits_remaining(a["id"]) > 0))
    c3.metric("Exhausted", sum(1 for a in accounts if a["status"] == "exhausted"))
    counts = db.job_counts()
    c4.metric("Queued jobs", counts.get("queued", 0))

    if active_id:
        a = db.get_account(active_id)
        rem = db.credits_remaining(active_id)
        if a["status"] == "exhausted" or rem <= 0:
            st.error(f"Active account {a['email']} is out of credits. Activate another account on the Accounts page.")
        else:
            st.success(f"Active account: {a['email']} with {rem} credits left this cycle")
    else:
        st.warning("No active account. Choose one on the Accounts page.")

    st.subheader("Credits by account")
    st.dataframe(
        [
            {
                "email": a["email"],
                "team": a["team_member"],
                "status": a["status"],
                "monthly": a["credits_monthly"],
                "used": db.credits_used_this_cycle(a["id"]),
                "remaining": db.credits_remaining(a["id"]),
                "cycle_start": a["cycle_start"],
            }
            for a in accounts
        ],
        use_container_width=True,
    )
    st.subheader("Recent activity")
    st.table([{"time": e["created_at"], "kind": e["kind"], "who": e["actor"], "message": e["message"]} for e in db.recent_events(30)])

# ---- Accounts ---------------------------------------------------------------
elif page == "Accounts":
    st.title("Accounts")
    tab_list, tab_add, tab_import = st.tabs(["Manage", "Add one", "Import CSV"])

    with tab_list:
        accounts = db.list_accounts()
        if not accounts:
            st.info("No accounts yet.")
        else:
            chosen = st.selectbox("Account", accounts, format_func=account_label)
            a = chosen
            rem = db.credits_remaining(a["id"])
            st.write(f"**{a['email']}**  status: {a['status']}  remaining: {rem}/{a['credits_monthly']}  cycle start: {a['cycle_start']}")
            b1, b2, b3, b4 = st.columns(4)
            if b1.button("Activate this account", disabled=a["status"] == "disabled"):
                q.activate_account(db, a["id"], actor)
                st.rerun()
            if b2.button("Start new credit cycle (renewed)"):
                db.start_new_cycle(a["id"])
                st.rerun()
            if b3.button("Disable" if a["status"] != "disabled" else "Enable"):
                db.update_account(a["id"], status="disabled" if a["status"] != "disabled" else "active")
                st.rerun()
            if b4.button("Delete account", type="secondary"):
                db.delete_account(a["id"])
                if q.get_active_account_id(db) == a["id"]:
                    q.deactivate_account(db, actor)
                st.rerun()

            with st.form("edit"):
                label = st.text_input("Label", a["label"])
                team = st.text_input("Team member", a["team_member"])
                monthly = st.number_input("Monthly credits", 0, 100000, int(a["credits_monthly"]))
                profile = st.text_input("Browser profile directory (logged in by a human)", a["profile_dir"])
                notes = st.text_area("Notes", a["notes"])
                if st.form_submit_button("Save"):
                    db.update_account(a["id"], label=label, team_member=team, credits_monthly=int(monthly), profile_dir=profile, notes=notes)
                    st.success("Saved")
                    st.rerun()

            with st.form("credit_adjust"):
                st.caption("Manual credit correction, for when the Omni Flow page shows a different balance")
                delta = st.number_input("Credits to add (negative to record spend)", -100000, 100000, 0)
                reason = st.text_input("Reason")
                if st.form_submit_button("Apply") and delta:
                    db.add_credit_entry(a["id"], int(delta), reason or "manual adjustment")
                    st.rerun()

            st.subheader("Login details")
            if not vault.is_unlocked:
                st.info("Unlock the vault in the sidebar to view or edit login details.")
            else:
                creds = vault.load(a["id"]) or Credentials()
                with st.form("creds"):
                    pw = st.text_input("Password", creds.password, type="password")
                    rec = st.text_input("Recovery email", creds.recovery_email)
                    ph = st.text_input("Recovery phone", creds.recovery_phone)
                    cn = st.text_area("Login notes", creds.notes)
                    if st.form_submit_button("Save login details"):
                        vault.store(a["id"], Credentials(pw, rec, ph, cn))
                        db.log("credentials", f"login details updated for {a['email']}", actor)
                        st.success("Stored encrypted")
                if st.checkbox("Reveal password"):
                    st.code(creds.password or "(none)")

    with tab_add:
        with st.form("add"):
            email = st.text_input("Gmail address")
            label = st.text_input("Label")
            team = st.text_input("Team member")
            monthly = st.number_input("Monthly credits", 0, 100000, 1000)
            cs = st.date_input("Credit cycle start", date.today())
            profile = st.text_input("Browser profile directory")
            pw = st.text_input("Password (stored encrypted)", type="password")
            if st.form_submit_button("Add account"):
                if not email:
                    st.error("email required")
                elif db.get_account_by_email(email):
                    st.error("already exists")
                else:
                    aid = db.add_account(email, label, team, int(monthly), cs, profile)
                    if pw:
                        if vault.is_unlocked:
                            vault.store(aid, Credentials(password=pw))
                        else:
                            st.warning("Vault locked, password not stored")
                    db.log("account", f"added {email}", actor)
                    st.success(f"Added {email}")

    with tab_import:
        st.write("CSV columns: email, label, team_member, credits_monthly, cycle_start, profile_dir, notes, and optionally password, recovery_email, recovery_phone.")
        up = st.file_uploader("accounts.csv", type="csv")
        if up is not None and st.button("Import"):
            tmp = Path(st.session_state.get("tmp_dir", ".")) / "import.csv"
            tmp.write_bytes(up.getvalue())
            added, skipped = import_accounts(db, tmp, vault if vault.is_unlocked else None)
            tmp.unlink(missing_ok=True)
            st.success(f"Imported {added}, skipped {skipped}")
        exp = st.text_input("Export to", str(Path.home() / "accounts_export.csv"))
        if st.button("Export CSV (no passwords)"):
            n = export_accounts(db, Path(exp))
            st.success(f"Exported {n} accounts to {exp}")

# ---- Queue ------------------------------------------------------------------
elif page == "Queue":
    st.title("Prompt queue")
    with st.form("newjob"):
        title = st.text_input("Title")
        prompt = st.text_area("Video prompt", height=160)
        cost = st.number_input("Expected credits (0 = use default)", 0, 10000, 0)
        if st.form_submit_button("Queue job") and prompt.strip():
            jid = db.add_job(prompt.strip(), title, int(cost), actor)
            st.success(f"Queued job {jid}")
    st.subheader("Bulk add")
    st.caption("One prompt per line. A line like  Title | prompt text  sets the title.")
    bulk = st.text_area("Prompts", height=120, key="bulk")
    if st.button("Queue all lines") and bulk.strip():
        n = 0
        for line in bulk.splitlines():
            if not line.strip():
                continue
            t, _, p = line.partition("|") if "|" in line else ("", "", line)
            db.add_job(p.strip(), t.strip(), 0, actor)
            n += 1
        st.success(f"Queued {n} jobs")

    st.subheader("Jobs")
    flt = st.selectbox("Status", ["all", "queued", "running", "done", "failed", "cancelled"])
    jobs = db.list_jobs(None if flt == "all" else flt)
    st.dataframe(
        [
            {"id": j["id"], "status": j["status"], "title": j["title"], "account": j["account_id"],
             "spent": j["credits_spent"], "output": j["output_path"], "error": j["error"],
             "prompt": j["prompt"][:80]}
            for j in jobs
        ],
        use_container_width=True,
    )
    jid = st.number_input("Job id", 0, step=1)
    cA, cB = st.columns(2)
    if cA.button("Requeue failed job") and jid:
        db.requeue_job(int(jid)); st.rerun()
    if cB.button("Cancel queued job") and jid:
        db.cancel_job(int(jid)); st.rerun()

# ---- Run --------------------------------------------------------------------
elif page == "Run":
    st.title("Run queue on the active account")
    active_id = q.get_active_account_id(db)
    if not active_id:
        st.warning("Activate an account first.")
    else:
        a = db.get_account(active_id)
        st.info(f"Active: {a['email']}  remaining credits: {db.credits_remaining(active_id)}")
        max_jobs = st.number_input("Max jobs this batch", 1, 500, 10)
        dry = st.checkbox("Dry run (no credits, writes prompt files only)")
        if st.button("Run batch"):
            cmd = db.get_setting(RUNNER_COMMAND_KEY) or ""
            if dry:
                runner = DryRunRunner()
            elif not cmd:
                st.error("Set the runner command on the Settings page first.")
                st.stop()
            else:
                runner = CommandRunner(cmd)
            reports = []
            prog = st.progress(0.0)
            try:
                for i in range(int(max_jobs)):
                    reports.append(q.run_next(db, runner, actor))
                    prog.progress((i + 1) / int(max_jobs))
            except q.QueueEmpty:
                st.info("Queue is empty.")
            except q.AccountExhausted as e:
                st.error(str(e))
            except q.NoActiveAccount as e:
                st.error(str(e))
            for r in reports:
                if r.ok:
                    st.write(f"job {r.job_id}: done, {r.result.output_path}")
                else:
                    st.write(f"job {r.job_id}: FAILED, {r.error}")

# ---- Settings ---------------------------------------------------------------
elif page == "Settings":
    st.title("Settings")
    out = st.text_input("Output root folder (local disk or a synced Google Drive folder)", str(q.output_root(db)))
    cmd = st.text_area("Runner command (your bulk-creation tool)", db.get_setting(RUNNER_COMMAND_KEY) or "",
                       help="Receives OMNI_PROMPT, OMNI_OUTPUT_DIR, OMNI_ACCOUNT_EMAIL, OMNI_PROFILE_DIR, ... and prints a JSON result line.")
    default_cost = st.number_input("Default credits per job", 0, 10000, int(db.get_setting(q.DEFAULT_CREDITS_PER_JOB_KEY) or 0))
    if st.button("Save settings"):
        db.set_setting(q.OUTPUT_ROOT_KEY, out)
        db.set_setting(RUNNER_COMMAND_KEY, cmd)
        db.set_setting(q.DEFAULT_CREDITS_PER_JOB_KEY, str(int(default_cost)))
        st.success("Saved")
    st.subheader("Change master password")
    if vault.is_initialised:
        with st.form("chpw"):
            old = st.text_input("Current", type="password")
            new = st.text_input("New", type="password")
            if st.form_submit_button("Change"):
                try:
                    vault.change_master_password(old, new)
                    st.success("Changed")
                except WrongMasterPassword:
                    st.error("Current password wrong")
                except ValueError as e:
                    st.error(str(e))
    st.caption(f"Database: {db.path}")
