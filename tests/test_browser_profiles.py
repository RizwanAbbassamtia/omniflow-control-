"""Check isolation and authenticated proxy forwarding without external network."""
import socket
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from omniflow_control.browser_profiles import ProxyConfig, ProxyRelay, ProfileError, launch_profile
from omniflow_control.db import Database
from omniflow_control.vault import Credentials, Vault


def test_add_one_form_saves_proxy_and_encrypted_login(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("OMNI_DB", str(tmp_path / "form.db"))
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "omniflow_control/app.py").run()
    app.sidebar.text_input(key="init_pw").set_value("a safe master password")
    next(b for b in app.sidebar.button if b.label == "Initialise vault").click().run()
    app.sidebar.radio[0].set_value("Accounts").run()
    field = lambda elements, label: next(e for e in elements if e.label == label)
    field(app.text_input, "Gmail address").set_value("manual@example.com")
    field(app.text_input, "Gmail password (optional, encrypted)").set_value("google-login")
    field(app.text_input, "Recovery email (optional, encrypted)").set_value("backup@example.com")
    field(app.selectbox, "New account proxy type").set_value("http")
    field(app.text_input, "New account proxy host").set_value("proxy.example.com")
    field(app.number_input, "New account proxy port").set_value(8080)
    field(app.text_input, "New account proxy username").set_value("login")
    field(app.text_input, "New account proxy password").set_value("secret")
    field(app.button, "Add account").click().run()

    assert not app.exception and any("Added manual@example.com" in x.value for x in app.success)
    db = Database(tmp_path / "form.db")
    account = db.get_account_by_email("manual@example.com")
    assert (account["proxy_type"], account["proxy_host"], account["proxy_port"]) == ("http", "proxy.example.com", 8080)
    vault = Vault(db)
    vault.unlock("a safe master password")
    saved = vault.load(account["id"])
    assert saved.proxy_password == "secret"
    assert saved.password == "google-login" and saved.recovery_email == "backup@example.com"
    assert b"secret" not in db.get_credentials(account["id"])
    assert b"google-login" not in db.get_credentials(account["id"])
    db.close()


def test_add_one_form_rejects_invalid_proxy_before_account_creation(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("OMNI_DB", str(tmp_path / "invalid.db"))
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "omniflow_control/app.py").run()
    app.sidebar.radio[0].set_value("Accounts").run()
    field = lambda elements, label: next(e for e in elements if e.label == label)
    field(app.text_input, "Gmail address").set_value("invalid@example.com")
    field(app.selectbox, "New account proxy type").set_value("http")
    field(app.text_input, "New account proxy host").set_value("proxy.example.com")
    field(app.button, "Add account").click().run()

    assert any("port" in x.value.lower() for x in app.error)
    db = Database(tmp_path / "invalid.db")
    assert db.get_account_by_email("invalid@example.com") is None
    db.close()


def test_authenticated_http_connect_forwards_and_never_exposes_secret_to_browser():
    upstream = socket.socket()
    upstream.bind(("127.0.0.1", 0))
    upstream.listen(1)
    observed = []

    def serve():
        peer, _ = upstream.accept()
        with peer:
            data = b""
            while b"\r\n\r\n" not in data:
                data += peer.recv(4096)
            observed.append(data)
            peer.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            observed.append(peer.recv(4))
            peer.sendall(b"pong")

    thread = threading.Thread(target=serve)
    thread.start()
    relay = ProxyRelay(ProxyConfig("http", "127.0.0.1", upstream.getsockname()[1], "user", "secret"))
    try:
        with socket.create_connection(("127.0.0.1", relay.port), timeout=2) as client:
            client.settimeout(2)
            client.sendall(b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com\r\n\r\n")
            assert b"200 Connection Established" in client.recv(4096)
            client.sendall(b"ping")
            assert client.recv(4) == b"pong"
        thread.join(timeout=2)
        assert b"Proxy-Authorization: Basic dXNlcjpzZWNyZXQ=" in observed[0]
        assert observed[1] == b"ping"
    finally:
        relay.close()
        upstream.close()


def test_proxy_validation_and_no_launch_on_failed_check(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    account_id = db.add_account("a@example.com")
    db.update_account(account_id, proxy_type="http", proxy_host="proxy.example", proxy_port=8080)
    vault = Vault(db)
    vault.initialise("a safe master password")
    vault.store(account_id, Credentials(proxy_username="name", proxy_password="secret"))
    with patch("omniflow_control.browser_profiles.subprocess.Popen") as popen:
        with pytest.raises(ProfileError):
            launch_profile(db, vault, account_id, check=lambda _: (_ for _ in ()).throw(ProfileError("unavailable")))
        popen.assert_not_called()
    assert b"secret" not in db.get_credentials(account_id)
    db.close()


def test_two_accounts_cannot_share_a_profile_directory(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    folder = str(tmp_path / "shared")
    first = db.add_account("a@example.com", profile_dir=folder)
    second = db.add_account("b@example.com", profile_dir=folder)
    db.update_account(first, proxy_type="socks5", proxy_host="proxy.example", proxy_port=1080)
    vault = Vault(db)
    vault.initialise("a safe master password")
    with pytest.raises(ProfileError, match="another account"):
        launch_profile(db, vault, first, check=lambda _: "203.0.113.1")
    db.close()


def test_socks5_username_password_connect():
    from omniflow_control.browser_profiles import _socks_connect
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    observed = []

    def serve():
        peer, _ = server.accept()
        with peer:
            peer.settimeout(2)
            observed.append(peer.recv(4))
            peer.sendall(b"\x05\x02")
            observed.append(peer.recv(255))
            peer.sendall(b"\x01\x00")
            observed.append(peer.recv(255))
            peer.sendall(b"\x05\x00\x00\x01\x7f\x00\x00\x01\x12\x34")

    thread = threading.Thread(target=serve)
    thread.start()
    with socket.create_connection(("127.0.0.1", server.getsockname()[1]), timeout=2) as client:
        _socks_connect(client, "flow.google.com", 443, ProxyConfig("socks5", "127.0.0.1", 1080, "user", "secret"))
    thread.join(timeout=2)
    server.close()
    assert observed[0] == b"\x05\x02\x00\x02"
    assert b"user" in observed[1] and b"secret" in observed[1]
    assert b"flow.google.com" in observed[2]


def test_csv_import_keeps_proxy_password_in_vault(tmp_path):
    from omniflow_control.accounts_io import export_accounts, import_accounts
    db = Database(tmp_path / "db.sqlite")
    vault = Vault(db)
    vault.initialise("a safe master password")
    csv_file = tmp_path / "accounts.csv"
    csv_file.write_text("email,proxy_type,proxy_host,proxy_port,proxy_username,proxy_password\n"
                        "a@example.com,http,proxy.example,8080,login,secret\n")
    assert import_accounts(db, csv_file, vault) == (1, 0)
    account = db.get_account_by_email("a@example.com")
    assert account["proxy_host"] == "proxy.example"
    assert vault.load(account["id"]).proxy_password == "secret"
    exported = tmp_path / "export.csv"
    export_accounts(db, exported)
    assert "secret" not in exported.read_text()
    assert "proxy.example" in exported.read_text()
    db.close()
