# Omni Flow Control Centre

Local Streamlit app that holds every Omni Flow account the team uses,
encrypts their logins and API keys, tracks live quota, queues video prompts,
generates clips through the Omni Flash Generation API v2, and hands a chosen
account to 9 Sigma Automation.

## Layout

| Path | Purpose |
|------|---------|
| `omniflow_control/app.py` | Streamlit user interface (Dashboard, Accounts, Queue, Run, Settings) |
| `omniflow_control/cli.py` | Same operations from the command line |
| `omniflow_control/db.py` | SQLite storage: accounts, credentials, credit ledger, quota snapshots, jobs |
| `omniflow_control/vault.py` | Master-password encryption of logins and API keys |
| `omniflow_control/queue.py` | Runs queued prompts on the operator-activated account |
| `omniflow_control/runners.py` | Dry run, external command runner, Omni Flash API runner |
| `omniflow_control/omniflash.py` | Client for the Omni Flash Generation API v2 |
| `omniflow_control/ninesigma.py` | Live quota sync and hand-over of a key to 9 Sigma |
| `tests/` | pytest suite, must pass before any push |

Database lives at `%USERPROFILE%\.omniflow_control\control.db` unless `OMNI_DB` is set.

## Commands

```
py -3 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m streamlit run omniflow_control/app.py
```

Or double-click `Start-OmniFlowControl.bat`.

## Rules for changes

- Account selection is an operator action. Do not add code that picks or
  switches accounts on its own when one runs out of credits. The queue must
  keep pausing and asking a person.
- Never write API keys, passwords or the master password to disk in plain
  text, to logs, or into commit messages.
- Run the tests and render every page with `streamlit.testing.v1.AppTest`
  before pushing.
- The 9 Sigma source lives in the separate `sceneforge` repository. The
  hand-over writes through 9 Sigma's own `Credentials` and `config.json` and
  only works on the Windows PC where 9 Sigma runs.
