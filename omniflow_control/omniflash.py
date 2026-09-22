"""Minimal client for the Omni Flash Generation API v2, the same service
9 Sigma Automation talks to (docs/omniflash-v2-api.md in the sceneforge repo).

Endpoints used:
    GET  /api/v2/usage                  today's quota or balance, free
    POST /api/v2/videos/generate        submit one video job
    GET  /api/v2/videos/status/{job}    poll until done or failed
    GET  video_url                      download the finished clip

Each account in the control centre carries its own key and address.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

MODEL = "omniflash-10s"
USAGE_PATH = "/api/v2/usage"
GENERATE_PATH = "/api/v2/videos/generate"
STATUS_PATH = "/api/v2/videos/status/"
MAX_PROMPT = 4000
POLL_SECONDS = 10.0
SUBMIT_TIMEOUT = 360
STATUS_TIMEOUT = 60
DOWNLOAD_TIMEOUT = 600
MIN_CLIP_BYTES = 10_000


class OmniFlashError(Exception):
    def __init__(self, message: str, code: int = 0, fatal: bool = False):
        super().__init__(message)
        self.code = code
        self.fatal = fatal


class QuotaExhausted(OmniFlashError):
    """429: the key has no generations left until it resets."""


@dataclass
class Quota:
    billing_mode: str = ""          # "" (daily limit) or "payg"
    label: str = ""
    limit: Optional[int] = None
    used: Optional[int] = None
    remaining: Optional[int] = None
    balance: Optional[float] = None
    video_price: Optional[float] = None
    reset_at: str = ""
    video_enabled: bool = True
    raw: dict = None

    def summary(self) -> str:
        if self.billing_mode == "payg":
            parts = ["pay-as-you-go"]
            if self.balance is not None:
                parts.append(f"balance {self.balance:g}")
            if self.remaining is not None:
                parts.append(f"about {self.remaining} videos")
            return ", ".join(parts)
        text = f"{self.used if self.used is not None else '?'} used of {self.limit if self.limit is not None else '?'}, {self.remaining if self.remaining is not None else '?'} left"
        if self.reset_at:
            text += f", resets {self.reset_at}"
        return text


def _requests_transport(method: str, url: str, headers: dict, body, timeout: float, stream: bool):
    import requests  # imported lazily so tests need no network stack

    return requests.request(method, url, headers=headers, json=body, timeout=timeout, stream=stream)


def normalise_address(text: str) -> str:
    t = (text or "").strip().rstrip("/")
    if t and not t.lower().startswith(("http://", "https://")):
        t = "https://" + t
    return t


def _num(v):
    try:
        return None if v is None or isinstance(v, bool) else float(v)
    except (TypeError, ValueError):
        return None


class OmniFlashClient:
    def __init__(self, api_key: str, base_url: str, model: str = MODEL,
                 transport: Optional[Callable] = None, sleep: Callable[[float], None] = time.sleep):
        self.api_key = (api_key or "").strip()
        self.base_url = normalise_address(base_url)
        self.model = model or MODEL
        self.transport = transport or _requests_transport
        self.sleep = sleep
        if not self.base_url:
            raise OmniFlashError("Omni Flash address is empty", fatal=True)
        if not self.api_key:
            raise OmniFlashError("Omni Flash API key is empty", fatal=True)

    # ---- plumbing ---------------------------------------------------------
    def _headers(self) -> dict:
        return {"Authorization": "Bearer " + self.api_key, "Accept": "application/json"}

    def _call(self, method: str, path: str, body=None, timeout: float = STATUS_TIMEOUT) -> dict:
        try:
            resp = self.transport(method, self.base_url + path, self._headers(), body, timeout, False)
        except Exception as exc:  # noqa: BLE001
            raise OmniFlashError(f"could not reach Omni Flash: {type(exc).__name__}: {exc}") from exc
        code = int(getattr(resp, "status_code", 0) or 0)
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            data = {}
        if code == 200:
            return data if isinstance(data, dict) else {}
        detail = str((data or {}).get("detail") or getattr(resp, "text", "") or "")[:300]
        if code == 429:
            raise QuotaExhausted(f"quota exhausted: {detail}", code)
        if code in (401, 403):
            raise OmniFlashError(f"key refused ({code}): {detail}", code, fatal=True)
        if code == 404 and path.startswith(STATUS_PATH):
            raise OmniFlashError("job no longer known to Omni Flash", code)
        raise OmniFlashError(f"Omni Flash answered {code}: {detail}", code)

    # ---- API --------------------------------------------------------------
    def usage(self) -> Quota:
        d = self._call("GET", USAGE_PATH)
        q = Quota(raw=d, label=str(d.get("label") or ""), reset_at=str(d.get("reset_at") or ""),
                  billing_mode=str(d.get("billing_mode") or "").lower(),
                  video_enabled=d.get("video_gen_enabled") is not False)
        if q.billing_mode == "payg":
            q.balance = _num(d.get("balance"))
            q.video_price = _num(d.get("payg_video_price"))
            if q.balance is not None and q.video_price:
                q.remaining = int(q.balance // q.video_price)
        else:
            if d.get("omniflash_daily_limit") is not None:
                q.limit, q.used, q.remaining = d.get("omniflash_daily_limit"), d.get("omniflash_used_today"), d.get("omniflash_remaining_today")
            else:
                q.limit, q.used, q.remaining = d.get("image_daily_limit"), d.get("image_used_today"), d.get("image_remaining_today")
        return q

    def submit(self, prompt: str, start_frame: Optional[str] = None) -> str:
        text = (prompt or "").strip()
        if not text or len(text) > MAX_PROMPT:
            raise OmniFlashError(f"prompt must be 1 to {MAX_PROMPT} characters (got {len(text)})", fatal=False)
        d = self._call("POST", GENERATE_PATH, {"prompt": text, "model": self.model, "start_frame": start_frame or None},
                       timeout=SUBMIT_TIMEOUT)
        job_id = str(d.get("job_id") or "").strip()
        if not job_id:
            raise OmniFlashError("Omni Flash returned no job id: " + json.dumps(d)[:200])
        return job_id

    def wait(self, job_id: str, max_wait: float = 1800.0) -> dict:
        """Poll until the job is done. Returns the final status payload."""
        waited = 0.0
        while True:
            d = self._call("GET", STATUS_PATH + job_id)
            status = str(d.get("status") or "").lower()
            if status == "done":
                if not d.get("video_url"):
                    raise OmniFlashError(f"job {job_id} done but no video link")
                return d
            if status == "failed":
                raise OmniFlashError(f"job {job_id} failed: {d.get('error') or 'generation failed'}")
            if waited >= max_wait:
                raise OmniFlashError(f"job {job_id} still {status or 'pending'} after {int(max_wait)}s")
            self.sleep(POLL_SECONDS)
            waited += POLL_SECONDS

    def download(self, url: str, dest: Path) -> Path:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        partial = dest.with_suffix(dest.suffix + ".part")
        same_host = url.split("/")[2:3] == self.base_url.split("/")[2:3]
        headers = self._headers() if same_host else {}
        last: Optional[OmniFlashError] = None
        for attempt in range(1, 4):
            try:
                resp = self.transport("GET", url, headers, None, DOWNLOAD_TIMEOUT, True)
            except Exception as exc:  # noqa: BLE001
                last = OmniFlashError(f"download failed: {type(exc).__name__}")
                self.sleep(2 * attempt)
                continue
            code = int(getattr(resp, "status_code", 0) or 0)
            if code >= 500:
                last = OmniFlashError(f"video host answered {code}")
                self.sleep(2 * attempt)
                continue
            if code != 200:
                raise OmniFlashError(f"video link refused ({code}); links expire after about 24 hours")
            written = 0
            with open(partial, "wb") as out:
                chunks = getattr(resp, "iter_content", None)
                for chunk in (chunks(256 * 1024) if callable(chunks) else [getattr(resp, "content", b"")]):
                    if chunk:
                        out.write(chunk)
                        written += len(chunk)
            if written < MIN_CLIP_BYTES:
                partial.unlink(missing_ok=True)
                last = OmniFlashError(f"download was only {written} bytes")
                continue
            partial.replace(dest)
            return dest
        partial.unlink(missing_ok=True)
        raise last or OmniFlashError("clip could not be downloaded")

    def generate(self, prompt: str, dest: Path) -> tuple[Path, dict]:
        job_id = self.submit(prompt)
        final = self.wait(job_id)
        path = self.download(str(final["video_url"]), dest)
        return path, final
