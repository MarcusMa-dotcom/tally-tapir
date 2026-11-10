"""The web server: web page, REST API, switches, logins, session cookie and the guards against requests from elsewhere."""

import base64
import socket
import time

import pytest

from tallytapir_app import protocol as p
from tallytapir_app.access import MAX_FAILURES, Access
from tallytapir_app.engine import Settings, TslSender
from tallytapir_app.receiver import ReceiverSettings, TslReceiver
from tallytapir_app.web import WebServer, create_app

LOCAL = {"base_url": "http://localhost:8088"}
PASSWORD = "correct horse battery"


def basic(user, password):
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.fixture
def udp_receiver():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(2.0)
    yield sock
    sock.close()


@pytest.fixture
def engine(udp_receiver):
    return TslSender(Settings(port=udp_receiver.getsockname()[1]))


@pytest.fixture
def access():
    return Access()


@pytest.fixture
def app(engine, access):
    return create_app(engine, access=access)


@pytest.fixture
def client(app):
    return app.test_client()


# -- web interface ---------------------------------------------------------

def test_page_loads(client):
    response = client.get("/", **LOCAL)
    assert response.status_code == 200
    assert b"Tally Tapir" in response.data
    assert b"V5.0W" in response.data


def test_state_reports_settings_and_packet(client):
    data = client.get("/ui/api/sender", **LOCAL).get_json()
    assert data["settings"]["protocol"] == "v5.0"
    assert data["status"]["connected"] is False
    assert data["status"]["packet_hex"].startswith("24 00 00 00")


def test_update_connect_and_send_over_http(client, udp_receiver):
    assert client.patch("/ui/api/sender", json={"text": "WEB", "tally_bits": 4}, **LOCAL).status_code == 200
    connected = client.post("/ui/api/sender/connect", json={}, **LOCAL).get_json()
    assert connected["ok"] and connected["status"]["connected"]
    display = p.parse_v50(udp_receiver.recvfrom(4096)[0]).displays[0]
    assert (display.text, display.txt) == ("WEB", 1)

    assert client.post("/ui/api/sender/send", json={}, **LOCAL).get_json()["ok"]
    udp_receiver.recvfrom(4096)
    done = client.post("/ui/api/sender/disconnect", json={}, **LOCAL).get_json()
    assert done["status"]["connected"] is False
    assert done["status"]["sent_count"] == 2


def test_invalid_update_returns_400_with_message(client, engine):
    response = client.patch("/ui/api/sender", json={"brightness": 7}, **LOCAL)
    assert response.status_code == 400
    assert "Brightness must be 0 to 3" in response.get_json()["error"]
    assert engine.settings.brightness == 3


def test_receiver_over_http(engine):
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    receiver = TslReceiver(ReceiverSettings(bind="127.0.0.1", port=port))
    client = create_app(engine, receiver).test_client()
    try:
        assert client.patch("/ui/api/receiver", json={"watch_id": 1}, **LOCAL).status_code == 200
        assert client.post("/ui/api/receiver/listen", json={}, **LOCAL).get_json()["ok"]
        engine.update(port=port, text="LOOP")
        engine.connect()
        for _ in range(100):
            data = client.get("/ui/api/receiver", **LOCAL).get_json()
            if data["status"]["packet_count"]:
                break
            time.sleep(0.02)
        assert data["status"]["watched"]["text"] == "LOOP"
        assert data["status"]["displays"][0]["index"] == 1
        bad = client.patch("/ui/api/receiver", json={"protocol": "v9"}, **LOCAL)
        assert bad.status_code == 400 and "Protocol" in bad.get_json()["error"]
        cleared = client.post("/ui/api/receiver/clear", json={}, **LOCAL).get_json()
        assert cleared["status"]["packet_count"] == 0
        stopped = client.post("/ui/api/receiver/stop", json={}, **LOCAL).get_json()
        assert stopped["status"]["listening"] is False
    finally:
        receiver.close()


def test_serial_ports_endpoint(client):
    data = client.get("/ui/api/serial-ports", **LOCAL).get_json()
    assert isinstance(data["ports"], list)
    assert isinstance(data["available"], bool)


# -- protection against other web pages -------------------------------------

def test_changing_request_without_json_content_type_is_rejected(client, engine):
    response = client.post("/ui/api/sender/connect", data="x=1", content_type="text/plain", **LOCAL)
    assert response.status_code == 415
    empty = client.post("/api/v1/sender/connect", **LOCAL)        # no body, no content type
    assert empty.status_code == 415
    assert not engine.connected


def test_cross_site_requests_are_refused(client, engine):
    marked = client.post("/ui/api/sender/connect", json={}, headers={"Sec-Fetch-Site": "cross-site"}, **LOCAL)
    assert marked.status_code == 403
    foreign = client.post("/api/v1/sender/connect", json={}, headers={"Origin": "http://evil.example"}, **LOCAL)
    assert foreign.status_code == 403
    other_port = client.patch("/ui/api/sender", json={"text": "X"}, headers={"Origin": "http://localhost:3000"}, **LOCAL)
    assert other_port.status_code == 403
    assert not engine.connected and engine.settings.text != "X"
    own = client.patch("/ui/api/sender", json={"text": "X"}, headers={"Origin": "http://localhost:8088"}, **LOCAL)
    assert own.status_code == 200


def test_foreign_host_header_is_rejected_on_localhost(client):
    assert client.get("/ui/api/sender", base_url="http://evil.example:8088").status_code == 403
    assert client.get("/api/v1/sender", base_url="http://evil.example:8088").status_code == 403


def test_network_mode_accepts_other_host_names(engine):
    client = create_app(engine, local_only=False).test_client()
    assert client.get("/ui/api/sender", base_url="http://192.168.1.20:8088").status_code == 200


def test_security_headers(client):
    response = client.get("/", **LOCAL)
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]
    assert response.headers["Cache-Control"] == "no-store"
    assert client.get("/api/v1/sender", **LOCAL).headers["Cache-Control"] == "no-store"


def test_oversized_body_is_refused(client):
    response = client.patch("/api/v1/sender", json={"text": "x" * 100_000}, **LOCAL)
    assert response.status_code == 413
    assert "error" in response.get_json()


# -- REST API --------------------------------------------------------------

def test_rest_api_lists_its_endpoints(client):
    data = client.get("/api/v1/", **LOCAL).get_json()
    assert data["name"] == "Tally Tapir"
    paths = {(e["method"], e["path"]) for e in data["endpoints"]}
    assert ("PATCH", "/api/v1/sender") in paths and ("POST", "/api/v1/receiver/listen") in paths


def test_rest_api_controls_the_sender(client, udp_receiver):
    assert client.patch("/api/v1/sender", json={"text": "API", "umd_id": 9}, **LOCAL).status_code == 200
    assert client.post("/api/v1/sender/connect", json={}, **LOCAL).get_json()["ok"]
    display = p.parse_v50(udp_receiver.recvfrom(4096)[0]).displays[0]
    assert (display.text, display.index) == ("API", 9)
    assert client.get("/api/v1/sender", **LOCAL).get_json()["status"]["sent_count"] == 1
    missing = client.get("/api/v1/nothing-here", **LOCAL)
    assert missing.status_code == 404 and "error" in missing.get_json()


def test_api_can_be_switched_off_while_the_web_interface_stays(client, access):
    access.update(api_enabled=False)
    off = client.get("/api/v1/sender", **LOCAL)
    assert off.status_code == 403 and "switched off" in off.get_json()["error"]
    assert client.post("/api/v1/sender/connect", json={}, **LOCAL).status_code == 403
    assert client.get("/ui/api/sender", **LOCAL).status_code == 200
    assert client.get("/", **LOCAL).status_code == 200


def test_web_interface_can_be_switched_off_while_the_api_stays(client, access):
    access.update(web_enabled=False)
    assert client.get("/", **LOCAL).status_code == 403
    assert client.get("/login", **LOCAL).status_code == 403
    assert client.get("/static/app.js", **LOCAL).status_code == 403
    assert client.get("/ui/api/sender", **LOCAL).status_code == 403
    assert client.patch("/ui/api/access", json={"web_enabled": True}, **LOCAL).status_code == 403
    assert client.get("/api/v1/sender", **LOCAL).status_code == 200


def test_api_login_with_http_basic(client, access):
    access.update(api_user="robot", api_password=PASSWORD, api_login=True)
    missing = client.get("/api/v1/sender", **LOCAL)
    assert missing.status_code == 401
    assert missing.headers["WWW-Authenticate"].startswith('Basic realm="Tally Tapir API"')
    assert client.get("/api/v1/sender", headers=basic("robot", "wrong password"), **LOCAL).status_code == 401
    assert client.get("/api/v1/sender", headers=basic("other", PASSWORD), **LOCAL).status_code == 401
    assert client.get("/api/v1/sender", headers=basic("robot", PASSWORD), **LOCAL).status_code == 200
    assert client.get("/api/v1/", headers=basic("robot", PASSWORD), **LOCAL).status_code == 200
    assert client.get("/api/v1/", **LOCAL).status_code == 401       # the endpoint list needs the login too
    assert client.get("/ui/api/sender", **LOCAL).status_code == 200  # the web interface has its own login


def test_api_login_slows_down_guessing(client, access):
    access.update(api_user="robot", api_password=PASSWORD, api_login=True)
    for _ in range(MAX_FAILURES - 1):
        assert client.get("/api/v1/sender", headers=basic("robot", "wrong password"), **LOCAL).status_code == 401
    locked = client.get("/api/v1/sender", headers=basic("robot", "wrong password"), **LOCAL)
    assert locked.status_code == 429 and int(locked.headers["Retry-After"]) >= 1
    still = client.get("/api/v1/sender", headers=basic("robot", PASSWORD), **LOCAL)
    assert still.status_code == 429


def test_api_has_no_door_to_the_access_settings(client):
    assert client.get("/api/v1/access", **LOCAL).status_code == 404
    assert client.patch("/api/v1/access", json={"api_login": False}, **LOCAL).status_code == 404


# -- web login -------------------------------------------------------------

@pytest.fixture
def locked(access):
    access.update(web_user="operator", web_password=PASSWORD, web_login=True)
    return access


def log_in(client, password=PASSWORD, user="operator"):
    return client.post("/ui/api/login", json={"user": user, "password": password}, **LOCAL)


def test_web_login_flow(client, locked):
    page = client.get("/", **LOCAL)
    assert page.status_code == 302 and page.headers["Location"].endswith("/login")
    assert client.get("/ui/api/sender", **LOCAL).status_code == 401
    assert client.patch("/ui/api/sender", json={"text": "X"}, **LOCAL).status_code == 401
    assert client.get("/ui/api/access", **LOCAL).status_code == 401
    assert client.get("/login", **LOCAL).status_code == 200
    assert client.get("/static/app.css", **LOCAL).status_code == 200

    wrong = log_in(client, "wrong password")
    assert wrong.status_code == 401 and wrong.get_json()["error"] == "User name or password is wrong"
    assert log_in(client, user="someone").status_code == 401
    assert client.get("/ui/api/sender", **LOCAL).status_code == 401

    assert log_in(client).status_code == 200
    assert client.get("/", **LOCAL).status_code == 200
    assert client.get("/ui/api/sender", **LOCAL).status_code == 200
    assert client.get("/ui/api/access", **LOCAL).get_json()["logged_in"] is True

    assert client.post("/ui/api/logout", json={}, **LOCAL).status_code == 200
    assert client.get("/ui/api/sender", **LOCAL).status_code == 401


def test_session_cookie_is_protected(client, locked):
    cookie = log_in(client).headers["Set-Cookie"]
    assert cookie.startswith("tally_tapir_session=")
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
    assert PASSWORD not in cookie


def test_session_cookie_is_secure_with_tls(engine, locked):
    client = create_app(engine, access=locked, tls=True).test_client()
    cookie = client.post("/ui/api/login", json={"user": "operator", "password": PASSWORD},
                         base_url="https://localhost:8088").headers["Set-Cookie"]
    assert "Secure" in cookie


def test_web_login_slows_down_guessing(client, locked):
    for _ in range(MAX_FAILURES - 1):
        assert log_in(client, "wrong password").status_code == 401
    assert log_in(client, "wrong password").status_code == 429
    assert log_in(client).status_code == 429


def test_changing_the_web_login_needs_the_current_password(client, locked):
    log_in(client)
    refused = client.patch("/ui/api/access", json={"web_password": "another long one"}, **LOCAL)
    assert refused.status_code == 403
    assert client.patch("/ui/api/access", json={"web_login": False}, **LOCAL).status_code == 403
    assert locked.verify("web", "operator", PASSWORD)

    api_only = client.patch("/ui/api/access", json={"api_enabled": False}, **LOCAL)
    assert api_only.status_code == 200 and api_only.get_json()["api_enabled"] is False

    changed = client.patch("/ui/api/access",
                           json={"web_password": "another long one", "current_password": PASSWORD}, **LOCAL)
    assert changed.status_code == 200
    assert locked.verify("web", "operator", "another long one")
    assert client.get("/ui/api/sender", **LOCAL).status_code == 200     # this browser stays logged in


def test_changing_the_login_ends_other_sessions(app, locked):
    first, second = app.test_client(), app.test_client()
    log_in(first)
    log_in(second)
    first.patch("/ui/api/access", json={"web_password": "another long one", "current_password": PASSWORD}, **LOCAL)
    assert first.get("/ui/api/sender", **LOCAL).status_code == 200
    assert second.get("/ui/api/sender", **LOCAL).status_code == 401


def test_access_view_never_shows_hashes(client, access):
    client.patch("/ui/api/access", json={"api_user": "robot", "api_password": PASSWORD}, **LOCAL)
    text = client.get("/ui/api/access", **LOCAL).get_data(as_text=True)
    assert "hash" not in text and "scrypt" not in text and PASSWORD not in text
    assert client.get("/ui/api/access", **LOCAL).get_json()["api_password_set"] is True


def test_enabling_a_login_needs_user_and_password(client):
    response = client.patch("/ui/api/access", json={"web_login": True}, **LOCAL)
    assert response.status_code == 400 and "user name and a password" in response.get_json()["error"]
    short = client.patch("/ui/api/access", json={"web_user": "operator", "web_password": "short"}, **LOCAL)
    assert short.status_code == 400


def test_enabling_the_web_login_keeps_this_browser_in(client, access):
    done = client.patch("/ui/api/access",
                        json={"web_user": "operator", "web_password": PASSWORD, "web_login": True}, **LOCAL)
    assert done.status_code == 200 and done.get_json()["logged_in"] is True
    assert client.get("/ui/api/sender", **LOCAL).status_code == 200


# -- server ----------------------------------------------------------------

def can_connect(port):
    try:
        socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
        return True
    except OSError:
        return False


def wait_for(condition, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return False


def test_server_closes_the_port_when_both_doors_are_off(engine, access):
    server = WebServer(engine, access=access, port=0)
    try:
        assert server.start() and server.running
        port = server.port
        assert can_connect(port)
        access.update(web_enabled=False)
        time.sleep(0.5)
        assert server.running and can_connect(port)       # the API is still on
        access.update(api_enabled=False)
        assert wait_for(lambda: not server.running)
        assert not can_connect(port)
        access.update(api_enabled=True)
        assert wait_for(lambda: server.running)
    finally:
        server.stop()


def test_server_reports_a_port_that_is_taken(engine, access):
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    server = WebServer(engine, access=access, port=blocker.getsockname()[1])
    try:
        assert not server.start()
        assert "Cannot serve" in server.error
    finally:
        server.stop()
        blocker.close()
