# Omni Flow Control Centre

**Current version: 0.2.2.** Accounts accept a pasted
`host:port:username:password` proxy and the Import CSV page provides a simple
`email,password,proxy,credits_monthly,team_member` template. Password means the
Gmail password; it is encrypted on import. The proxy username and password are
encrypted separately. It is a separate application from the Omni Flow desktop installer.

| App | Start with | What it does |
|-----|-----------|--------------|
| `omniflow_control/app.py` | `streamlit run omniflow_control/app.py` | Central control of every Google account the team uses for Omni Flow: encrypted logins, monthly credit ledger, prompt queue, batch runs, dashboard. |

## Install on the Windows laptop (no command line)

1. Install Python 3.11 or newer from python.org and tick **Add python.exe to PATH**.
2. Clone this repository with Git for Windows if you want one-click updates:
   `git clone https://github.com/RizwanAbbassamtia/omniflow-control-`.
   Alternatively, download the release ZIP and extract it to a folder such as
   `C:\OmniFlowControl`.
3. Double-click **Start-OmniFlowControl.bat**. The first run installs the packages, then the dashboard opens at http://localhost:8501.
4. In the sidebar create the master password, then use the **Accounts** page to import your CSV.

If you cloned the repository, double-click **Update-OmniFlowControl.bat** to pull
newer versions. If you installed from a ZIP, download and extract a new release
instead; the update batch file needs a Git clone. Account data and encrypted
credentials are stored outside the code folder in
`%USERPROFILE%\.omniflow_control\control.db` by default. Back up this database
before changing computers or reinstalling Windows.

## Install (command line)

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # Mac / Linux
pip install -r requirements.txt
```

## Omni Flow Control Centre

### How it works

1. **Accounts** are stored in a local SQLite database. Logins are encrypted with a
   master password that is typed at startup and never written to disk.
2. Each account has a **monthly credit allowance** and a **cycle start**. Every job
   records what it spent, so the dashboard shows remaining credits per account.
   When Google renews an account, click **Start new credit cycle**.
3. **Prompts** are queued centrally, one by one, in bulk, or from the command line.
4. An operator **activates one account**. Batch runs work through the queue on that
   account only. When its credits are gone the queue pauses, the account is marked
   exhausted, and the dashboard asks a person to activate the next account.
   The software never chooses or switches accounts by itself.
5. Each job hands off to your existing bulk-creation tool through the
   **runner command** (Settings page). Outputs land under the output root, which can
   be a local folder or a Google Drive for Desktop folder.
6. **Add a single Google Flow account with a proxy**: unlock the Vault, open
   Accounts → Add one, and enter the Gmail address, monthly Flow credits for
   tracking, optional Gmail password/recovery details, and a pasted proxy such
   as `proxy.example.com:8080:sample-user:sample-pass`. HTTP is the default;
   select HTTPS or SOCKS5 when needed. The account is saved only after proxy settings
   pass validation. Then use Accounts → Manage → Browser profile and proxy to
   test and open it. Sign in to Google once in that isolated browser. Future
   opens reuse the browser's session while Google keeps it valid; Google may
   ask for sign-in or verification again. Stored passwords are reference
   details and never perform sign-in automatically.
7. **Browser profiles**: open Accounts → Manage → Browser profile and proxy.
   Paste the proxy, save, test the connection, then click **Open profile**.
   This creates a persistent, separate Chrome/Edge user-data directory per
   account (or uses the directory specified on that account). Sign in manually
   in the opened browser. Each launch checks the proxy first; it will not open
   the browser if the check fails. The observed IP is displayed after testing.
   Leave the app running while the browser is open; its loopback relay supplies
   upstream proxy authentication without putting secrets on the command line.

The 1,000 Google Flow credits are tracked manually, not read from the website.
The Run page is for a separate Omni Flash API or a configured external runner;
it does not use Google Flow credits or the browser proxy. No API key is needed
for manual use of Google Flow in the opened browser.

### Omni Flash and 9 Sigma

9 Sigma Automation (the `sceneforge` app) generates clips through the
**Omni Flash Generation API v2** with an API key that carries its own daily
limit or balance. The control centre keeps one key per account, encrypted in the
vault, and does three things with it:

1. **Check live quota**: reads `/api/v2/usage` for one account or for all of
   them, stores the result, and shows it on the dashboard next to the manual
   ledger. A refused or disabled key marks the account so nobody wastes time on it.
2. **Generate directly**: on the Run page choose *Omni Flash API* and the queue
   submits each prompt, polls, and downloads the clip with the active account's
   key. A 429 from the service parks the account as exhausted, returns the job
   to the queue and stops. An operator then activates the next account.
3. **Hand over to 9 Sigma**: one click writes the chosen account's key into
   9 Sigma's sealed credential store and its address into 9 Sigma's
   `config.json`, using 9 Sigma's own code. From then on 9 Sigma productions run
   on that account. Set the two folders on the Settings page first. This only
   works on the Windows PC that runs 9 Sigma, because 9 Sigma seals keys to the
   Windows user.

```bash
python -m omniflow_control.cli quota                  # live quota for every keyed account
python -m omniflow_control.cli quota you@gmail.com
python -m omniflow_control.cli handover you@gmail.com  # 9 Sigma now uses this key
python -m omniflow_control.cli run --omniflash --max 20
```

### Runner contract

The runner command is started once per job with these environment variables:

```
OMNI_JOB_ID, OMNI_TITLE, OMNI_PROMPT, OMNI_OUTPUT_DIR,
OMNI_ACCOUNT_EMAIL, OMNI_PROFILE_DIR, OMNI_CREDITS_REMAINING,
OMNI_API_KEY, OMNI_BASE_URL
```

It must print one JSON object as its last stdout line:

```json
{"output_path": "C:/OmniFlowOutput/acc/000012-scene1/clip.mp4", "credits_spent": 10, "detail": "optional"}
```

`OMNI_PROFILE_DIR` is the browser profile a team member already signed into for
that account. Login stays a human step. The external runner is independently
responsible for its network settings; the local browser relay applies only to
the **Open profile** button.

### Command line

```bash
python -m omniflow_control.cli init-vault
python -m omniflow_control.cli import-accounts accounts.csv --with-passwords
python -m omniflow_control.cli add-job "Night-time police chase, dash cam view" --title "Scene 1" --cost 10
python -m omniflow_control.cli activate someone@gmail.com
python -m omniflow_control.cli run --max 20          # or --dry-run to test the queue
python -m omniflow_control.cli status
```

Download the simple template from Accounts → Import CSV. Its columns are
`email,password,proxy,credits_monthly,team_member`. `password` means Gmail password,
and `proxy` means `host:port:username:password`. Both are encrypted on import.
Optional `proxy_type` selects `https` or `socks5` (default `http`). The previous
detailed columns (`label, cycle_start, profile_dir, api_base_url, notes, proxy_host,
proxy_port, proxy_username, proxy_password, recovery_email, recovery_phone, api_key`)
remain accepted. Existing emails are skipped; change their proxy on the Manage page.
Unlock the vault before importing secrets and delete filled CSV files after import.

### Tests

```bash
pytest -q
```

### Later: several machines

This release is local to one Windows user and one computer. Keep `OMNI_DB` on
the local disk: a SQLite database file on an SMB or synced shared drive can
be damaged by concurrent access. The local Streamlit server has no team login
or role enforcement and must not be exposed to other computers. A central
multi-user installation needs a server-side database, authenticated users,
role checks and encrypted session backup before deployment.
