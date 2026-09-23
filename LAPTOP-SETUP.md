# Setting up the Omni Flow Control Centre on the laptop

This guide covers Control Centre **v0.2.1**, including per-account proxy
settings and isolated browser profiles. It does not install the separate
Omni Flow desktop application.

To add one account manually, unlock the Vault and use **Accounts → Add one**.
Enter its email, monthly credits, proxy type, host, port and optional proxy
username/password. Then select the account under **Manage** to test its proxy
and open its isolated browser profile.

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

1. **Sidebar, Vault**: type a master password of at least 8 characters and click
   *Initialise vault*. Write this password down somewhere safe. It is never
   stored and cannot be recovered.
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

Prepare `accounts.csv` with these columns (header row required):

```
email,label,team_member,credits_monthly,api_base_url,api_key,password,recovery_email
```

Only `email` is required. `api_key` and `password` are stored encrypted.
Then **Accounts page, Import CSV tab**: choose the file, click *Import*.

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
   profile and proxy**. Enter its proxy type, host, port and, if required,
   username/password. Unlock the vault and save, click **Test proxy** to see
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
