"""Streamlit control centre. Run with:  streamlit run omniflow_control/app.py"""
from __future__ import annotations

import os
import sys
from io import StringIO
from datetime import date
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from omniflow_control import __version__, ninesigma, queue as q  # noqa: E402
from omniflow_control.browser_profiles import ProxyConfig, ProfileError, check_proxy, launch_profile, parse_proxy_line  # noqa: E402
from omniflow_control.accounts_io import export_accounts, import_accounts  # noqa: E402
from omniflow_control.cli import RUNNER_COMMAND_KEY  # noqa: E402
from omniflow_control.db import DEFAULT_DB, Database  # noqa: E402
from omniflow_control.runners import CommandRunner, DryRunRunner, OmniFlashRunner  # noqa: E402
from omniflow_control.vault import Credentials, Vault, WrongMasterPassword  # noqa: E402

st.set_page_config(page_title="Omni Flow Control", layout="wide")


@st.cache_resource
def get_db() -> Database:
    return Database(os.environ.get("OMNI_DB", str(DEFAULT_DB)))


db = get_db()
if "vault_instance" not in st.session_state:
    st.session_state.vault_instance = Vault(db)
vault = st.session_state.vault_instance
actor = st.sidebar.text_input("Your name", value=st.session_state.get("actor", ""), key="actor")
st.sidebar.caption(f"Control Centre v{__version__}")

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
    return f"{a['email']}  [{a['status']}, {db.effective_remaining(a['id'])} left]"


# ---- Dashboard --------------------------------------------------------------
if page == "Dashboard":
    st.title("Omni Flow Control")
    active_id = q.get_active_account_id(db)
    accounts = db.list_accounts()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Accounts", len(accounts))
    c2.metric("With credits", sum(1 for a in accounts if a["status"] == "active" and db.effective_remaining(a["id"]) > 0))
    c3.metric("Exhausted", sum(1 for a in accounts if a["status"] == "exhausted"))
    counts = db.job_counts()
    c4.metric("Queued jobs", counts.get("queued", 0))

    if vault.is_unlocked and st.button("Refresh live quotas from Omni Flash (all accounts with a key)"):
        out = ninesigma.sync_all_quotas(db, vault, actor)
        st.success(f"Checked {len(out)} accounts")
        st.rerun()
    if active_id:
        a = db.get_account(active_id)
        rem = db.effective_remaining(active_id)
        if a["status"] == "exhausted" or rem <= 0:
            st.error(f"Active account {a['email']} is out of credits. Activate another account on the Accounts page.")
        else:
            st.success(f"Active account: {a['email']} with {rem} credits left this cycle")
    else:
        st.warning("No active account. Choose one on the Accounts page.")

    st.subheader("Credits by account")
    rows = []
    for a in accounts:
        snap = db.latest_quota(a["id"])
        rows.append({
            "email": a["email"],
            "team": a["team_member"],
            "status": a["status"],
            "remaining": db.effective_remaining(a["id"]),
            "source": "live" if snap is not None and not snap["error"] and snap["remaining"] is not None else "ledger",
            "live_limit": snap["quota_limit"] if snap else None,
            "live_used": snap["used"] if snap else None,
            "resets": snap["reset_at"] if snap else "",
            "checked": snap["fetched_at"][:19] if snap else "",
            "quota_error": snap["error"] if snap else "",
            "ledger_monthly": a["credits_monthly"],
            "ledger_used": db.credits_used_this_cycle(a["id"]),
        })
    st.dataframe(rows, width="stretch")
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
            by_id = {int(x["id"]): x for x in accounts}
            chosen_id = st.selectbox("Account", list(by_id), format_func=lambda i: account_label(by_id[i]))
            a = by_id[chosen_id]
            rem = db.effective_remaining(a["id"])
            snap = db.latest_quota(a["id"])
            st.write(f"**{a['email']}**  status: {a['status']}  remaining: {rem}  cycle start: {a['cycle_start'][:10]}")
            if snap is not None:
                st.caption(("Live quota error: " + snap["error"]) if snap["error"] else
                           f"Live quota at {snap['fetched_at'][:19]}: {snap['used']} used of {snap['quota_limit']}, {snap['remaining']} left, resets {snap['reset_at']}")
            b1, b2, b3, b4 = st.columns(4)
            if b1.button("Activate this account", disabled=a["status"] == "disabled"):
                q.activate_account(db, a["id"], actor)
                st.rerun()
            l1, l2 = st.columns(2)
            if l1.button("Check live quota", disabled=not vault.is_unlocked):
                quota = ninesigma.sync_quota(db, vault, a["id"], actor)
                st.success(quota.summary()) if quota else st.error(db.latest_quota(a["id"])["error"])
            if l2.button("Hand this account to 9 Sigma", disabled=not vault.is_unlocked):
                try:
                    st.success(ninesigma.hand_over(db, vault, a["id"], actor))
                except ninesigma.HandOverError as e:
                    st.error(str(e))
            with st.expander("Browser profile and proxy", expanded=False):
                st.caption("One persistent browser directory per account. Sign in to Google once in its window; later opens reuse that session while Google keeps it valid. Google may request verification again. Proxy credentials stay encrypted in the vault.")
                existing = vault.load(a["id"]) if vault.is_unlocked else None
                with st.form("paste_proxy_settings"):
                    pasted_proxy = st.text_input("Paste proxy (host:port:username:password)", type="password",
                                                 placeholder="proxy.example.com:8080:username:password")
                    pasted_kind = st.selectbox("Proxy protocol", ["http", "https", "socks5"],
                                               index=["http", "https", "socks5"].index(a["proxy_type"]) if a["proxy_type"] in ("http", "https", "socks5") else 0)
                    if st.form_submit_button("Save pasted proxy", disabled=not vault.is_unlocked):
                        try:
                            parsed = parse_proxy_line(pasted_proxy, pasted_kind)
                            db.update_account(a["id"], proxy_type=parsed.kind, proxy_host=parsed.host, proxy_port=parsed.port)
                            creds = existing or Credentials()
                            creds.proxy_username, creds.proxy_password = parsed.username, parsed.password
                            vault.store(a["id"], creds)
                            db.log("proxy", f"proxy configuration changed for {a['email']}", actor)
                            st.success("Proxy saved. Test proxy before opening the profile.")
                        except ProfileError as e:
                            st.error(str(e))
                with st.form("proxy_settings"):
                    st.caption("Or enter proxy details separately")
                    types = ["", "http", "https", "socks5"]
                    kind = st.selectbox("Proxy type", types, index=types.index(a["proxy_type"]) if a["proxy_type"] in types else 0,
                                        format_func=lambda x: x.upper() if x else "Not configured")
                    host = st.text_input("Proxy host", a["proxy_host"])
                    port = st.number_input("Proxy port", min_value=0, max_value=65535, value=int(a["proxy_port"]))
                    username = st.text_input("Proxy username", existing.proxy_username if existing else "", disabled=not vault.is_unlocked)
                    proxy_password = st.text_input("Proxy password", type="password", disabled=not vault.is_unlocked,
                                                   placeholder="Saved" if existing and existing.proxy_password else "")
                    if st.form_submit_button("Save proxy", disabled=not vault.is_unlocked):
                        try:
                            ProxyConfig(kind, host.strip(), int(port), username, proxy_password or (existing.proxy_password if existing else "")).validate()
                            db.update_account(a["id"], proxy_type=kind, proxy_host=host.strip(), proxy_port=int(port))
                            existing = existing or Credentials()
                            existing.proxy_username = username
                            existing.proxy_password = proxy_password or existing.proxy_password
                            vault.store(a["id"], existing)
                            db.log("proxy", f"proxy configuration changed for {a['email']}", actor)
                            st.success("Proxy saved")
                        except ProfileError as e:
                            st.error(str(e))
                p1, p2 = st.columns(2)
                if p1.button("Test proxy", disabled=not vault.is_unlocked):
                    try:
                        secrets = vault.load(a["id"]) or Credentials()
                        ip = check_proxy(ProxyConfig(a["proxy_type"], a["proxy_host"], int(a["proxy_port"]),
                                                     secrets.proxy_username, secrets.proxy_password))
                        st.success(f"Proxy connected. Public IP: {ip}")
                    except ProfileError as e:
                        st.error(str(e))
                if p2.button("Open profile", disabled=not vault.is_unlocked):
                    try:
                        ip = launch_profile(db, vault, a["id"], actor)
                        st.success(f"Browser opened through proxy. Public IP: {ip}")
                    except (ProfileError, OSError) as e:
                        st.error(str(e))
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
                profile = st.text_input("Dedicated browser user-data directory (blank = managed automatically)", a["profile_dir"],
                                        help="Use a separate absolute directory for each account. Existing regular Chrome profile names such as Default are not user-data directories.")
                api_url = st.text_input("Omni Flash address for this account's key", a["api_base_url"])
                notes = st.text_area("Notes", a["notes"])
                if st.form_submit_button("Save"):
                    db.update_account(a["id"], label=label, team_member=team, credits_monthly=int(monthly), profile_dir=profile, api_base_url=api_url, notes=notes)
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
                    pw = st.text_input("Gmail password (optional, encrypted)", creds.password, type="password")
                    rec = st.text_input("Recovery email", creds.recovery_email)
                    ph = st.text_input("Recovery phone", creds.recovery_phone)
                    api_key = st.text_input("Omni Flash API key (optional; not used for Google Flow)", creds.api_key, type="password")
                    cn = st.text_area("Login notes", creds.notes)
                    if st.form_submit_button("Save login details"):
                        creds.password, creds.recovery_email, creds.recovery_phone = pw, rec, ph
                        creds.notes, creds.api_key = cn, api_key
                        vault.store(a["id"], creds)
                        db.log("credentials", f"login details updated for {a['email']}", actor)
                        st.success("Stored encrypted")
                if st.checkbox("Reveal password"):
                    st.code(creds.password or "(none)")

    with tab_add:
        st.info("Google Flow uses your Google account and its browser profile. Save the account and proxy here, then use Manage → Test proxy → Open profile. Sign in to Google once in that window; future opens reuse its session while Google keeps it valid. A stored Gmail password does not perform sign-in or verification. No Omni Flash API key is needed.")
        with st.form("add"):
            details, connection = st.columns(2)
            with details:
                st.markdown("**Google account**")
                email = st.text_input("Gmail address")
                label = st.text_input("Label")
                team = st.text_input("Team member")
                monthly = st.number_input("Monthly Google Flow credits (tracking only)", 0, 100000, 1000)
                cs = st.date_input("Credit cycle start", date.today())
                pw = st.text_input("Gmail password (optional, encrypted)", type="password",
                                   help="Saved for your reference. Sign in once in the isolated browser; later opens reuse its session while Google keeps it valid.")
                recovery_email = st.text_input("Recovery email (optional, encrypted)")
                recovery_phone = st.text_input("Recovery phone (optional, encrypted)")
            with connection:
                st.markdown("**Browser proxy**")
                compact_proxy = st.text_input("Paste proxy (host:port:username:password)", type="password",
                                              placeholder="proxy.example.com:8080:username:password")
                st.caption("Default protocol: HTTP. To use HTTPS or SOCKS5, select that type below. Separate fields remain optional.")
                proxy_type = st.selectbox("New account proxy type", ["", "http", "https", "socks5"],
                                          format_func=lambda x: x.upper() if x else "Not configured")
                proxy_host = st.text_input("New account proxy host")
                proxy_port = st.number_input("New account proxy port", min_value=0, max_value=65535, value=0)
                proxy_username = st.text_input("New account proxy username")
                proxy_password = st.text_input("New account proxy password", type="password")
                profile = st.text_input("Dedicated browser user-data directory (optional)",
                                        help="Leave blank to create an isolated browser directory automatically.")
            if st.form_submit_button("Add account"):
                proxy_input = bool(compact_proxy.strip() or proxy_host.strip() or proxy_port or proxy_username or proxy_password)
                secrets_entered = bool(pw or recovery_email or recovery_phone or compact_proxy or proxy_username or proxy_password)
                if not email.strip():
                    st.error("email required")
                elif db.get_account_by_email(email):
                    st.error("already exists")
                elif proxy_input and not proxy_type and not compact_proxy.strip():
                    st.error("Choose a proxy type or clear the proxy fields")
                elif secrets_entered and not vault.is_unlocked:
                    st.error("Unlock the Vault before adding an account with Gmail or proxy login details")
                else:
                    try:
                        if compact_proxy.strip():
                            parsed = parse_proxy_line(compact_proxy, proxy_type or "http")
                            proxy_type, proxy_host, proxy_port = parsed.kind, parsed.host, parsed.port
                            proxy_username, proxy_password = parsed.username, parsed.password
                        if proxy_type:
                            ProxyConfig(proxy_type, proxy_host.strip(), int(proxy_port),
                                        proxy_username, proxy_password).validate()
                    except ProfileError as e:
                        st.error(str(e))
                    else:
                        aid = db.add_account(email, label, team, int(monthly), cs, profile)
                        if proxy_type:
                            db.update_account(aid, proxy_type=proxy_type, proxy_host=proxy_host.strip(),
                                              proxy_port=int(proxy_port))
                        if secrets_entered:
                            vault.store(aid, Credentials(password=pw, recovery_email=recovery_email,
                                                         recovery_phone=recovery_phone,
                                                         proxy_username=proxy_username,
                                                         proxy_password=proxy_password))
                        db.log("account", f"added {email}", actor)
                        st.success(f"Added {email}. Select it in Manage, test the proxy, then open its browser profile to sign in to Google.")

    with tab_import:
        st.write("Download the simple template, enter one Gmail and proxy per row, and save it as CSV. The `password` column is the Gmail password, stored encrypted after import. Proxy format: host:port:username:password. Protocol defaults to HTTP; an optional `proxy_type` column can specify HTTPS or SOCKS5. Sign in to Google once manually in each browser profile.")
        st.download_button("Download simple CSV template", "email,password,proxy,credits_monthly,team_member\n",
                           file_name="accounts-simple-template.csv", mime="text/csv")
        st.caption("Unlock the Vault before importing Gmail passwords or proxies. An existing Gmail is skipped; update its proxy under Manage → Browser profile and proxy. Delete filled CSV files containing passwords after import.")
        up = st.file_uploader("accounts.csv", type="csv")
        if up is not None and st.button("Import"):
            try:
                added, skipped = import_accounts(db, StringIO(up.getvalue().decode("utf-8-sig")),
                                                 vault if vault.is_unlocked else None)
                st.success(f"Imported {added}, skipped {skipped}")
            except (ValueError, OSError, ProfileError) as e:
                st.error(str(e))
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
        width="stretch",
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
    st.info("This page runs API or external commands. Google Flow's 1,000 account credits are used only when you open that account's browser profile and generate in the Flow website; this Run page does not spend Flow credits or use its browser proxy.")
    active_id = q.get_active_account_id(db)
    if not active_id:
        st.warning("Activate an account first.")
    else:
        a = db.get_account(active_id)
        st.info(f"Active: {a['email']}  remaining credits: {db.credits_remaining(active_id)}")
        max_jobs = st.number_input("Max jobs this batch", 1, 500, 10)
        mode = st.radio("Generate with", ["Omni Flash API (this account's key)", "Runner command (Settings page)", "Dry run (no credits)"])
        if st.button("Run batch"):
            cmd = db.get_setting(RUNNER_COMMAND_KEY) or ""
            if mode.startswith("Dry"):
                runner = DryRunRunner()
            elif mode.startswith("Omni"):
                if not vault.is_unlocked:
                    st.error("Unlock the vault so the account's API key can be used.")
                    st.stop()
                runner = OmniFlashRunner()
            elif not cmd:
                st.error("Set the runner command on the Settings page first.")
                st.stop()
            else:
                runner = CommandRunner(cmd)
            reports = []
            prog = st.progress(0.0)
            try:
                for i in range(int(max_jobs)):
                    reports.append(q.run_next(db, runner, actor, vault))
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
    st.subheader("9 Sigma Automation")
    sf_dir = st.text_input("9 Sigma code folder (contains the `sceneforge` package)", db.get_setting(ninesigma.SCENEFORGE_DIR_KEY) or "")
    cfg_dir = st.text_input("9 Sigma config folder (holds config.json)", db.get_setting(ninesigma.NINESIGMA_CONFIG_DIR_KEY) or "")
    if st.button("Save settings"):
        db.set_setting(q.OUTPUT_ROOT_KEY, out)
        db.set_setting(RUNNER_COMMAND_KEY, cmd)
        db.set_setting(q.DEFAULT_CREDITS_PER_JOB_KEY, str(int(default_cost)))
        db.set_setting(ninesigma.SCENEFORGE_DIR_KEY, sf_dir)
        db.set_setting(ninesigma.NINESIGMA_CONFIG_DIR_KEY, cfg_dir)
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
