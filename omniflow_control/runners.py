"""Runners turn a queued prompt into a finished clip on the active account.

The control centre does not know how your bulk-creation tool works, so it
talks to it through a small contract:

* ``CommandRunner`` launches a configurable shell command with the job details
  in environment variables and expects one JSON line on stdout.
* ``DryRunRunner`` writes a placeholder file so the queue can be exercised
  without spending credits.

Every runner receives the account the operator activated. A runner never
picks or changes accounts.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class AccountContext:
    account_id: int
    email: str
    profile_dir: str          # browser profile a human already logged in with
    credits_remaining: int
    api_key: str = ""         # Omni Flash key for this account, from the vault
    api_base_url: str = ""    # Omni Flash address for that key


@dataclass(frozen=True)
class JobSpec:
    job_id: int
    title: str
    prompt: str
    output_dir: Path


@dataclass(frozen=True)
class RunResult:
    output_path: str
    credits_spent: int
    detail: str = ""


class Runner(Protocol):
    name: str

    def generate(self, job: JobSpec, account: AccountContext) -> RunResult: ...


class DryRunRunner:
    """Writes the prompt to a text file instead of calling Omni Flow."""

    name = "dry-run"

    def __init__(self, credits_per_job: int = 0):
        self.credits_per_job = credits_per_job

    def generate(self, job: JobSpec, account: AccountContext) -> RunResult:
        job.output_dir.mkdir(parents=True, exist_ok=True)
        out = job.output_dir / f"job-{job.job_id}.prompt.txt"
        out.write_text(job.prompt, encoding="utf-8")
        return RunResult(str(out), self.credits_per_job, "dry run, nothing generated")


class CommandRunner:
    """Runs your existing bulk-creation tool as a subprocess.

    The command receives these environment variables:

        OMNI_JOB_ID, OMNI_TITLE, OMNI_PROMPT, OMNI_OUTPUT_DIR,
        OMNI_ACCOUNT_EMAIL, OMNI_PROFILE_DIR, OMNI_CREDITS_REMAINING

    and must print a single JSON object on its last stdout line:

        {"output_path": "...", "credits_spent": 12, "detail": "optional"}

    A non-zero exit code or missing JSON marks the job failed.
    """

    name = "command"

    def __init__(self, command: str, timeout_seconds: int = 3600):
        if not command.strip():
            raise ValueError("runner command is empty")
        self.command = command
        self.timeout_seconds = timeout_seconds

    def generate(self, job: JobSpec, account: AccountContext) -> RunResult:
        job.output_dir.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env.update(
            {
                "OMNI_JOB_ID": str(job.job_id),
                "OMNI_TITLE": job.title,
                "OMNI_PROMPT": job.prompt,
                "OMNI_OUTPUT_DIR": str(job.output_dir),
                "OMNI_ACCOUNT_EMAIL": account.email,
                "OMNI_PROFILE_DIR": account.profile_dir,
                "OMNI_CREDITS_REMAINING": str(account.credits_remaining),
                "OMNI_API_KEY": account.api_key,
                "OMNI_BASE_URL": account.api_base_url,
            }
        )
        proc = subprocess.run(
            shlex.split(self.command) if os.name != "nt" else self.command,
            env=env,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
            shell=(os.name == "nt"),
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"runner exited {proc.returncode}: {proc.stderr.strip()[-1500:]}"
            )
        last_line = next(
            (ln for ln in reversed(proc.stdout.splitlines()) if ln.strip()), ""
        )
        try:
            data = json.loads(last_line)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"runner did not print a JSON result line: {last_line!r}") from exc
        return RunResult(
            output_path=str(data.get("output_path", "")),
            credits_spent=int(data.get("credits_spent", 0)),
            detail=str(data.get("detail", "")),
        )


class OmniFlashRunner:
    """Generates the clip itself through the Omni Flash Generation API v2,
    using the active account's own key and address. Credits spent is what the
    service reports: one video per job on a daily-limit key, or the cost on a
    pay-as-you-go key."""

    name = "omniflash"

    def __init__(self, transport=None, sleep=None, max_wait: float = 1800.0):
        self.transport = transport
        self.sleep = sleep
        self.max_wait = max_wait

    def generate(self, job: JobSpec, account: AccountContext) -> RunResult:
        from .omniflash import OmniFlashClient

        kwargs = {}
        if self.transport is not None:
            kwargs["transport"] = self.transport
        if self.sleep is not None:
            kwargs["sleep"] = self.sleep
        client = OmniFlashClient(account.api_key, account.api_base_url, **kwargs)
        job_id = client.submit(job.prompt)
        final = client.wait(job_id, self.max_wait)
        dest = job.output_dir / f"{job.job_id:06d}.mp4"
        client.download(str(final["video_url"]), dest)
        cost = final.get("cost")
        spent = int(round(float(cost))) if isinstance(cost, (int, float)) and cost else 1
        detail = f"omniflash job {job_id}"
        if isinstance(final.get("remaining_today"), int):
            detail += f", {final['remaining_today']} left today"
        return RunResult(str(dest), spent, detail)
