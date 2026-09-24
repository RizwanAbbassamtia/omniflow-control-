# Setting up the Omni Flow Control Centre on the laptop

This guide covers Control Centre **v0.2.3**, including per-account proxy
settings and isolated browser profiles. It does not install the separate
Omni Flow desktop application.

To add one account manually, use **Accounts → Add one**. Windows protects proxy
credentials automatically without a master password. If you already created
a master password vault, unlock it first.
Enter its email, monthly Google Flow credits for tracking, optional Gmail
password/recovery details, and one proxy string in
`host:port:username:password` format. Select the account under **Manage** to test its proxy and
open its isolated browser profile, then sign in to Google once. Subsequent
opens reuse that browser session while Google keeps it valid; the saved Gmail
password does not submit sign-in or verification forms. This
browser workflow needs no Omni Flash API key. The **Run** page uses a separate
API or external runner and does not consume Google Flow website credits.

Follow these steps in order on the Windows PC that will run the control centre.
Each step says what to check before moving on.

## 1. Install the tools

1. Python 3.11 or newer from https://www.python.org/downloads/windows/.
   Tick **Add python.exe to PATH** in the installer.
2. Git for Windows from https://git-scm.com/download/win (default options).

Check: open Command Prompt and run `py --version` and `git --version`. Both print a version.

## 2. Get the code

```
cd %USERPROFILE%
git clone https://github.com/RizwanAbbassamtia/omniflow-control-
cd omniflow-control-
```

Check: the folder contains `Start-OmniFlowControl.bat` and `omniflow_control\app.py`.

## 3. Install and test

```
py -3 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pytest -q
```

Check: the last line says all tests passed.

## 4. Start the user interface

Double-click `Start-OmniFlowControl.bat`, or run:

```
.venv\Scripts\python -m streamlit run omniflow_control\app.py
```

The browser opens at http://localhost:8501. Leave the black window open while using the app.

## 5. First-time setup inside the app

1. **Sidebar, Vault**: on Windows it says "Protected by this Windows user account";
   no master password is required. If an earlier version created a master password
   vault, unlock it with that password. The Gmail password field is optional.
2. **Sidebar, Your name**: type your name so the activity log shows who did what.
   This is a local operator label, not an access-control login. Do not expose
   the dashboard on your office network or the public Internet.
3. **Settings page**:
   - Output root folder: a local folder or a Google Drive for Desktop folder.
   - 9 Sigma code folder: the folder that contains the `sceneforge` package,
     for example `C:\Users\<you>\sceneforge`.
   - 9 Sigma config folder: the folder that holds 9 Sigma's `config.json`.
   - Click *Save settings*.

## 6. Load the accounts

Download the simple template on **Accounts → Import CSV** and enter one account
per row. The columns are:

```
email,password,proxy,credits_monthly,team_member
```

Only `email` is required. The `password` column is the Gmail password, and
`proxy` holds the complete proxy, for example
`proxy.example.com:8080:sample-user:sample-pass`. HTTP is the default. Add an
optional `proxy_type` column for HTTPS or SOCKS5. Unlock the Vault before
importing passwords or proxies; their credentials are encrypted after import.
Existing emails are skipped. To change the proxy for an existing account,
select it under **Manage → Browser profile and proxy**, paste the new proxy,
and save. Delete filled CSV files with passwords after import.

Check: the Dashboard shows the account count.

## 7. Check live quota

Dashboard: click *Refresh live quotas from Omni Flash*. Every account with an
API key and address gets its used, remaining and reset time filled in.
An account whose key is refused is marked disabled with the reason.

## 8. Daily use

1. **Accounts page**: pick an account with credits, click *Activate this account*.
   To make 9 Sigma use it too, click *Hand this account to 9 Sigma*.
2. **Queue page**: paste prompts, one per line, click *Queue all lines*.
3. **Run page**: choose *Omni Flash API*, set the batch size, click *Run batch*.
   Clips land in the output folder under the account's name.
4. When the active account runs out, the run stops and the Dashboard shows a
   red banner. Go to Accounts, activate the next account, run again.
5. To open Google Flow for an account, go to **Accounts → Manage → Browser
   profile and proxy**. Paste the complete proxy, unlock the vault and save,
   click **Test proxy** to see
   the public IP, then click **Open profile**. Sign in yourself the first time.
   Leave the control centre running while the profile is open. A failed proxy
   test stops the launch. Keep separate profile directories per account.

## 9. Updating

Double-click `Update-OmniFlowControl.bat`, or run `git pull` in the folder.

## Command line equivalents

```
.venv\Scripts\python -m omniflow_control.cli init-vault
.venv\Scripts\python -m omniflow_control.cli import-accounts accounts.csv --with-passwords
.venv\Scripts\python -m omniflow_control.cli quota
.venv\Scripts\python -m omniflow_control.cli activate someone@gmail.com
.venv\Scripts\python -m omniflow_control.cli handover someone@gmail.com
.venv\Scripts\python -m omniflow_control.cli add-job "prompt text" --title "Scene 1"
.venv\Scripts\python -m omniflow_control.cli run --omniflash --max 20
.venv\Scripts\python -m omniflow_control.cli status
```

Set `OMNI_MASTER_PASSWORD` in the environment to avoid the password prompt in scripts.

## If something fails

| Symptom | Fix |
|---------|-----|
| `py` is not recognised | Reinstall Python with *Add python.exe to PATH* ticked |
| Wrong master password | There is no reset. Delete `%USERPROFILE%\.omniflow_control\control.db` to start over; stored logins are lost |
| Quota check says key refused | The key is invalid or disabled at the Omni Flash administrator |
| Hand-over says DPAPI unavailable | Run the app on the same Windows PC and user account as 9 Sigma |
| Port 8501 in use | Start with `--server.port 8502` |
