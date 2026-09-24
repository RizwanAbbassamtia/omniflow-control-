"""Per-account persistent Chromium profiles with fail-closed upstream proxies.

The local relay keeps authenticated upstream proxy secrets out of Chrome's
command line. It is bound to loopback and lives only while the browser runs.
"""
from __future__ import annotations

import base64
import os
import select
import shutil
import socket
import ssl
import subprocess
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .db import Database
from .vault import Vault, VaultLocked

FLOW_URL = "https://flow.google.com/"
IP_CHECK_URL = "https://api.ipify.org/"
PROFILE_ROOT = Path.home() / ".omniflow_control" / "profiles"


class ProfileError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProxyConfig:
    kind: str
    host: str
    port: int
    username: str = ""
    password: str = ""

    def validate(self) -> None:
        if self.kind not in {"http", "https", "socks5"}:
            raise ProfileError("Choose HTTP, HTTPS or SOCKS5 proxy")
        if not self.host or any(ch in self.host for ch in "/@\r\n \t"):
            raise ProfileError("Enter a valid proxy host")
        if not 1 <= self.port <= 65535:
            raise ProfileError("Proxy port must be between 1 and 65535")
        if self.password and not self.username:
            raise ProfileError("Proxy username is required with a password")


def parse_proxy_line(value: str, kind: str = "http") -> ProxyConfig:
    """Parse host:port:username:password as supplied by proxy vendors."""
    parts = value.strip().split(":", 3)
    if len(parts) != 4 or not all(parts):
        raise ProfileError("Paste proxy as host:port:username:password")
    try:
        port = int(parts[1])
    except ValueError as exc:
        raise ProfileError("Proxy port must be a number") from exc
    config = ProxyConfig(kind, parts[0].strip(), port, parts[2], parts[3])
    config.validate()
    return config


def _read_exact(sock: socket.socket, n: int) -> bytes:
    result = bytearray()
    while len(result) < n:
        part = sock.recv(n - len(result))
        if not part:
            raise ProfileError("Proxy closed the connection")
        result.extend(part)
    return bytes(result)


def _read_headers(sock: socket.socket) -> tuple[bytes, bytes]:
    raw = bytearray()
    while b"\r\n\r\n" not in raw:
        chunk = sock.recv(4096)
        if not chunk:
            raise ProfileError("Proxy closed the connection")
        raw.extend(chunk)
        if len(raw) > 65536:
            raise ProfileError("Proxy headers are too large")
    head, tail = bytes(raw).split(b"\r\n\r\n", 1)
    return head, tail


def _socks_connect(sock: socket.socket, host: str, port: int, cfg: ProxyConfig) -> None:
    methods = b"\x00\x02" if cfg.username else b"\x00"
    sock.sendall(b"\x05" + bytes([len(methods)]) + methods)
    version, method = _read_exact(sock, 2)
    if version != 5 or method == 255:
        raise ProfileError("SOCKS5 authentication method rejected")
    if method == 2:
        username = cfg.username.encode()
        password = cfg.password.encode()
        if not username or len(username) > 255 or len(password) > 255:
            raise ProfileError("Invalid SOCKS5 credentials")
        sock.sendall(b"\x01" + bytes([len(username)]) + username + bytes([len(password)]) + password)
        if _read_exact(sock, 2) != b"\x01\x00":
            raise ProfileError("SOCKS5 authentication failed")
    elif method != 0:
        raise ProfileError("Unsupported SOCKS5 authentication")
    address = host.encode("idna")
    if len(address) > 255:
        raise ProfileError("Destination host is too long")
    sock.sendall(b"\x05\x01\x00\x03" + bytes([len(address)]) + address + port.to_bytes(2, "big"))
    reply = _read_exact(sock, 4)
    if reply[0] != 5 or reply[1] != 0:
        raise ProfileError("SOCKS5 proxy refused the destination")
    length = {1: 4, 4: 16}.get(reply[3])
    if reply[3] == 3:
        length = _read_exact(sock, 1)[0]
    if length is None:
        raise ProfileError("Invalid SOCKS5 reply")
    _read_exact(sock, length + 2)


def _pump(left: socket.socket, right: socket.socket) -> None:
    left.settimeout(None)
    right.settimeout(None)
    peers = (left, right)
    while True:
        readable, _, _ = select.select(peers, [], [], 1)
        for source in readable:
            data = source.recv(65536)
            if not data:
                return
            (right if source is left else left).sendall(data)


class ProxyRelay:
    def __init__(self, cfg: ProxyConfig):
        cfg.validate()
        self.cfg = cfg
        self._server = socket.socket()
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(32)
        self._server.settimeout(0.5)
        self.port = self._server.getsockname()[1]
        self._stop = threading.Event()
        threading.Thread(target=self._serve, daemon=True).start()

    def close(self) -> None:
        self._stop.set()
        self._server.close()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                client, _ = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle, args=(client,), daemon=True).start()

    def _handle(self, client: socket.socket) -> None:
        with client:
            client.settimeout(12)
            try:
                head, tail = _read_headers(client)
                line, *headers = head.split(b"\r\n")
                method, target, version = line.decode("ascii").split(" ", 2)
                if method == "CONNECT":
                    parsed = urlsplit("//" + target)
                    host, port = parsed.hostname, parsed.port or 443
                else:
                    parsed = urlsplit(target)
                    host, port = parsed.hostname, parsed.port or 80
                if not host or not 1 <= port <= 65535:
                    raise ProfileError("Invalid destination")
                cfg = self.cfg
                upstream = socket.create_connection((cfg.host, cfg.port), timeout=12)
                with upstream:
                    upstream.settimeout(12)
                    if cfg.kind == "https":
                        upstream = ssl.create_default_context().wrap_socket(upstream, server_hostname=cfg.host)
                    if cfg.kind == "socks5":
                        _socks_connect(upstream, host, port, cfg)
                        if method == "CONNECT":
                            client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                            if tail:
                                upstream.sendall(tail)
                        else:
                            origin = (parsed.path or "/") + ("?" + parsed.query if parsed.query else "")
                            upstream.sendall(method.encode() + b" " + origin.encode() + b" " + version.encode() +
                                             b"\r\n" + b"\r\n".join(headers) + b"\r\n\r\n" + tail)
                    else:
                        auth = (b"Proxy-Authorization: Basic " + base64.b64encode(
                            (cfg.username + ":" + cfg.password).encode()) + b"\r\n") if cfg.username else b""
                        upstream.sendall(line + b"\r\n" + b"\r\n".join(headers) + b"\r\n" + auth + b"\r\n" +
                                         (b"" if method == "CONNECT" else tail))
                        if method == "CONNECT":
                            response, remainder = _read_headers(upstream)
                            status = response.split(b"\r\n", 1)[0]
                            if b" 200 " not in status:
                                raise ProfileError("Upstream proxy refused the connection")
                            client.sendall(response + b"\r\n\r\n" + remainder)
                            if tail:
                                upstream.sendall(tail)
                    _pump(client, upstream)
            except (OSError, ValueError, UnicodeError, ProfileError):
                try:
                    client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
                except OSError:
                    pass


def check_proxy(cfg: ProxyConfig, *, timeout: int = 12) -> str:
    """Return the observed public IP; never retry over a direct connection."""
    relay = ProxyRelay(cfg)
    try:
        handler = urllib.request.ProxyHandler({"http": f"http://127.0.0.1:{relay.port}",
                                               "https": f"http://127.0.0.1:{relay.port}"})
        opener = urllib.request.build_opener(handler)
        with opener.open(IP_CHECK_URL, timeout=timeout) as response:
            ip = response.read(128).decode("ascii").strip()
        import ipaddress
        ipaddress.ip_address(ip)
        return ip
    except Exception as exc:
        raise ProfileError("Proxy connection test failed; profile was not opened") from exc
    finally:
        relay.close()


def find_browser() -> str:
    for name in ("chrome", "msedge", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    if os.name == "nt":
        for root in (os.environ.get("PROGRAMFILES", ""), os.environ.get("PROGRAMFILES(X86)", ""),
                     os.environ.get("LOCALAPPDATA", "")):
            for relative in ("Google/Chrome/Application/chrome.exe", "Microsoft/Edge/Application/msedge.exe"):
                candidate = Path(root) / relative
                if root and candidate.is_file():
                    return str(candidate)
    raise ProfileError("Chrome or Edge was not found")


_active: dict[int, tuple[subprocess.Popen, ProxyRelay]] = {}
_lock = threading.Lock()


def launch_profile(db: Database, vault: Vault, account_id: int, actor: str = "", *,
                   browser: str | None = None, check=check_proxy) -> str:
    account = db.get_account(account_id)
    if account is None:
        raise ProfileError("Account not found")
    if not vault.is_unlocked:
        raise VaultLocked("Unlock the vault before opening a profile")
    secrets = vault.load(account_id)
    cfg = ProxyConfig(account["proxy_type"], account["proxy_host"], int(account["proxy_port"]),
                      secrets.proxy_username if secrets else "",
                      secrets.proxy_password if secrets else "")
    cfg.validate()
    supplied = Path(account["profile_dir"]).expanduser() if account["profile_dir"] else None
    if supplied is not None and not supplied.is_absolute():
        raise ProfileError("Use an absolute browser user-data directory or leave it blank")
    folder = (supplied or PROFILE_ROOT / str(account_id)).resolve()
    for other in db.list_accounts():
        other_folder = Path(other["profile_dir"] or PROFILE_ROOT / str(other["id"])).expanduser().resolve()
        if other["id"] != account_id and other_folder == folder:
            raise ProfileError("This browser profile directory is assigned to another account")
    with _lock:
        current = _active.get(account_id)
        if current and current[0].poll() is None:
            raise ProfileError("This profile is already open")
    ip = check(cfg)
    executable = browser or find_browser()
    folder.mkdir(parents=True, exist_ok=True)
    relay = ProxyRelay(cfg)
    try:
        process = subprocess.Popen([executable, f"--user-data-dir={folder}",
                                    f"--proxy-server=http://127.0.0.1:{relay.port}",
                                    "--no-first-run", "--disable-quic", "--new-window", FLOW_URL],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        relay.close()
        raise
    with _lock:
        _active[account_id] = (process, relay)
    db.log("browser", f"opened profile for {account['email']} via proxy {cfg.host}:{cfg.port} (IP {ip})", actor)

    def cleanup() -> None:
        process.wait()
        relay.close()
        with _lock:
            if _active.get(account_id, (None,))[0] is process:
                _active.pop(account_id, None)

    threading.Thread(target=cleanup, daemon=True).start()
    return ip
