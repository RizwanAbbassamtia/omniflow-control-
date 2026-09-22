# Omni Flow Control Centre


| App | Start with | What it does |
|-----|-----------|--------------|
| `omniflow_control/app.py` | `streamlit run omniflow_control/app.py` | Central control of every Google account the team uses for Omni Flow: encrypted logins, monthly credit ledger, prompt queue, batch runs, dashboard. |

## Install

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

### Runner contract

The runner command is started once per job with these environment variables:

```
OMNI_JOB_ID, OMNI_TITLE, OMNI_PROMPT, OMNI_OUTPUT_DIR,
OMNI_ACCOUNT_EMAIL, OMNI_PROFILE_DIR, OMNI_CREDITS_REMAINING
```

It must print one JSON object as its last stdout line:

```json
{"output_path": "C:/OmniFlowOutput/acc/000012-scene1/clip.mp4", "credits_spent": 10, "detail": "optional"}
```

`OMNI_PROFILE_DIR` is the browser profile a team member already signed into for
that account. Login stays a human step.

### Command line

```bash
python -m omniflow_control.cli init-vault
python -m omniflow_control.cli import-accounts accounts.csv --with-passwords
python -m omniflow_control.cli add-job "Night-time police chase, dash cam view" --title "Scene 1" --cost 10
python -m omniflow_control.cli activate someone@gmail.com
python -m omniflow_control.cli run --max 20          # or --dry-run to test the queue
python -m omniflow_control.cli status
```

CSV columns: `email, label, team_member, credits_monthly, cycle_start, profile_dir, notes`
plus optional `password, recovery_email, recovery_phone` (stored encrypted, never exported).

### Tests

```bash
pytest -q
```

### Later: several machines

The database is a single SQLite file. To let several PCs share it, point `OMNI_DB`
at a file on a shared drive for light use, or swap `db.py` for Postgres when the
team grows. The queue and vault code do not depend on SQLite specifics.
