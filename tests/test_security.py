"""Probes from the security audit, kept as tests so the findings stay fixed."""

import http.client
import logging
import socket
import time

import pytest

import tallytapir_app.access as access_module
from tallytapir_app import protocol as p
from tallytapir_app.access import Access
from tallytapir_app.engine import Settings, TslSender
from tallytapir_app.receiver import MAX_DISPLAYS, ReceiverSettings, TslReceiver
from tallytapir_app.web import WebServer, create_app
from tallytapir.receiver import MAX_TCP_CLIENTS

LOCAL = {"base_url": "http://localhost:8088"}
PASSWORD = "correct horse battery"


@pytest.fixture
def engine():
    return TslSender(Settings(port=9))


@pytest.fixture
def access():
    return Access()


@pytest.fixture
def client(engine, access):
    return create_app(engine, access=access).test_client()


def wait_for(condition, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False


# -- input handling --------------------------------------------------------

@pytest.mark.parametrize("change", [
    {"text": {"a": 1}}, {"text": ["x"]}, {"text": 5}, {"umd_id": [1]}, {"umd_id": {"$gt": 0}},
    {"umd_id": -1}, {"umd_id": 1e99}, {"umd_id": 1.5}, {"port": 0}, {"port": 70000},
    {"tally_bits": True}, {"brightness": "3 OR 1=1"}, {"host": ""}, {"host": "a" * 300},
    {"host": "evil\u0000host"}, {"host": "two words"}, {"host": "line\nbreak"},
    {"protocol": None}, {"interval_ms": 1}, {"utf16": 1}, {"__class__": "x"}, {"settings": {}},
])
def test_bad_sender_input_is_refused_and_changes_nothing(client, engine, change):
    before = engine.snapshot()["settings"]
    response = client.patch("/api/v1/sender", json=change, **LOCAL)
    assert response.status_code == 400, response.get_data(as_text=True)
    assert "error" in response.get_json()
    assert engine.snapshot()["settings"] == before


@pytest.mark.parametrize("body, content_type", [
    ("[1, 2]", "application/json"), ("null", "application/json"), ("{broken", "application/json"),
    ('"text"', "application/json"), ("", "application/json"),
])
def test_bodies_that_are_no_json_object_are_refused(client, body, content_type):
    for path in ("/api/v1/sender", "/api/v1/receiver", "/ui/api/access"):
        assert client.patch(path, data=body, content_type=content_type, **LOCAL).status_code == 400


@pytest.mark.parametrize("name", [
    "/etc/passwd", "../../etc/passwd", "/dev/../etc/passwd", "C:\\Windows\\win.ini", "COM1/../x",
    "socket://10.0.0.1:23", "/dev/tty USB0", "/tmp/fifo", "\\\\server\\share",
])
def test_serial_port_must_be_a_device_name(client, engine, name):
    response = client.patch("/api/v1/sender", json={"serial_port": name}, **LOCAL)
    assert response.status_code == 400 and "device" in response.get_json()["error"]
    assert client.patch("/api/v1/receiver", json={"serial_port": name}, **LOCAL).status_code == 400
    assert engine.settings.serial_port == ""


@pytest.mark.parametrize("name", [
    "/dev/ttyUSB0", "/dev/cu.usbserial-A50285BI", "/dev/serial/by-id/usb-FTDI_FT232R-if00-port0",
    "/dev/serial/by-path/pci-0000:00:14.0-usb-0:1:1.0-port0", "COM3", "com12", "\\\\.\\COM12", "",
])
def test_real_serial_device_names_are_accepted(client, name):
    assert client.patch("/api/v1/sender", json={"serial_port": name}, **LOCAL).status_code == 200


def test_odd_host_names_cannot_block_the_sender(engine):
    """A host the system cannot handle must end in an error, not in a stuck sender."""
    for transport_name in ("udp", "tcp"):
        engine.update(transport=transport_name)
        engine.settings.host = "evil\u0000host"       # as if it had slipped past the check
        assert engine.connect() is False
        status = engine.snapshot()["status"]
        assert status["connecting"] is False and status["connected"] is False
        assert status["error"]
        engine.settings.host = "a" * 70 + ".example"   # label too long for a host name
        assert engine.connect() is False
        assert engine.snapshot()["status"]["connecting"] is False


def test_receiver_survives_odd_listen_addresses():
    receiver = TslReceiver(ReceiverSettings())
    receiver.settings.bind = "evil\u0000host"
    assert receiver.listen() is False
    assert "Cannot listen" in receiver.snapshot()["status"]["error"]
    assert not receiver.listening


# -- what answers reveal ---------------------------------------------------

def test_internal_errors_show_no_details(engine, access, monkeypatch):
    app = create_app(engine, access=access)
    monkeypatch.setattr(engine, "snapshot", lambda: (_ for _ in ()).throw(RuntimeError("secret /Users/x")))
    logging.disable(logging.CRITICAL)
    try:
        response = app.test_client().get("/api/v1/sender", **LOCAL)
    finally:
        logging.disable(logging.NOTSET)
    assert response.status_code == 500
    assert response.get_json() == {"error": "Internal error"}
    assert "secret" not in response.get_data(as_text=True)


def test_no_cross_origin_reading_is_allowed(client):
    response = client.get("/api/v1/sender", headers={"Origin": "http://evil.example"}, **LOCAL)
    assert "Access-Control-Allow-Origin" not in response.headers
    preflight = client.options("/api/v1/sender", headers={
        "Origin": "http://evil.example", "Access-Control-Request-Method": "PATCH"}, **LOCAL)
    assert "Access-Control-Allow-Origin" not in preflight.headers


@pytest.mark.parametrize("path", [
    "/static/../access.py", "/static/..%2faccess.py", "/static/%2e%2e/%2e%2e/tallytapir_app/access.py",
    "/static/..%5caccess.py", "/static//etc/passwd", "/static/app.js/../../access.py",
])
def test_static_files_cannot_reach_outside_their_folder(client, path):
    response = client.get(path, **LOCAL)
    assert response.status_code in (301, 308, 404)
    assert b"hash_password" not in response.data and b"root:" not in response.data


def test_other_methods_are_refused(client):
    assert client.open("/api/v1/sender", method="TRACE", **LOCAL).status_code == 405
    assert client.delete("/api/v1/sender", json={}, **LOCAL).status_code == 405
    assert client.put("/api/v1/sender", json={}, **LOCAL).status_code == 405
    assert client.post("/api/v1/sender", json={"text": "X"}, **LOCAL).status_code == 405


def test_server_header_names_no_versions(engine, access):
    server = WebServer(engine, access=access, port=0)
    try:
        assert server.start()
        connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=3)
        connection.request("GET", "/api/v1/", headers={"Host": f"localhost:{server.port}"})
        response = connection.getresponse()
        banner = response.getheader("Server")
        response.read()
        connection.close()
        assert banner == "Tally-Tapir"
        assert "Werkzeug" not in banner and "Python" not in banner
    finally:
        server.stop()


# -- logins ------------------------------------------------------------------

def test_unknown_user_costs_the_same_hash_work(access, monkeypatch):
    access.update(web_user="operator", web_password=PASSWORD, web_login=True)
    calls = []
    real = access_module.check_password_hash
    monkeypatch.setattr(access_module, "check_password_hash",
                        lambda stored, password: calls.append(stored) or real(stored, password))
    assert not access.verify("web", "nobody", "some password")
    assert not access.verify("api", "anyone", "some password")      # no API login configured at all
    assert len(calls) == 2 and all(stored for stored in calls)


def test_passwords_never_reach_logs_or_answers(client, access, caplog):
    access.update(web_user="operator", web_password=PASSWORD, web_login=True)
    with caplog.at_level(logging.DEBUG):
        wrong = client.post("/ui/api/login", json={"user": "operator", "password": "guess-" + PASSWORD}, **LOCAL)
        right = client.post("/ui/api/login", json={"user": "operator", "password": PASSWORD}, **LOCAL)
        state = client.get("/ui/api/access", **LOCAL)
    everything = caplog.text + wrong.get_data(as_text=True) + right.get_data(as_text=True) \
        + state.get_data(as_text=True) + str(dict(right.headers))
    assert PASSWORD not in everything
    assert "Failed web login from 127.0.0.1" in caplog.text        # but the attempt is on record


@pytest.mark.parametrize("body", [
    {"user": ["operator"], "password": {"x": 1}}, {"user": None, "password": None}, {},
    {"user": "operator"}, {"user": "m" * 5000, "password": "p" * 5000},
])
def test_odd_login_bodies_are_plain_failures(client, access, body):
    access.update(web_user="operator", web_password=PASSWORD, web_login=True)
    assert client.post("/ui/api/login", json=body, **LOCAL).status_code == 401


@pytest.mark.parametrize("header", [
    "Basic", "Basic !!!not-base64!!!", "Basic " + "QQ==" * 3000, "Bearer abc", "Digest username=robot",
    "Basic cm9ib3Q=",       # "robot" without a colon
])
def test_odd_authorization_headers_are_plain_failures(client, access, header):
    access.update(api_user="robot", api_password=PASSWORD, api_login=True)
    response = client.get("/api/v1/sender", headers={"Authorization": header}, **LOCAL)
    assert response.status_code == 401


def test_session_cookies_cannot_be_made_up(engine, access):
    access.update(web_user="operator", web_password=PASSWORD, web_login=True)
    app = create_app(engine, access=access)
    forged = app.test_client()
    forged.set_cookie("tally_tapir_session", "eyJpbiI6dHJ1ZSwiZyI6MX0.made.up", domain="localhost")
    assert forged.get("/ui/api/sender", **LOCAL).status_code == 401

    # A cookie from another run of the program (other secret key) is worthless here.
    other = create_app(engine, access=access).test_client()
    login = other.post("/ui/api/login", json={"user": "operator", "password": PASSWORD}, **LOCAL)
    value = login.headers["Set-Cookie"].split(";")[0].split("=", 1)[1]
    replay = app.test_client()
    replay.set_cookie("tally_tapir_session", value, domain="localhost")
    assert replay.get("/ui/api/sender", **LOCAL).status_code == 401


def test_other_machines_cannot_change_access_without_a_web_login(engine, access):
    app = create_app(engine, access=access, local_only=False)
    remote = {"base_url": "http://192.168.1.20:8088", "environ_base": {"REMOTE_ADDR": "192.168.1.77"}}
    here = {"base_url": "http://192.168.1.20:8088", "environ_base": {"REMOTE_ADDR": "127.0.0.1"}}
    client = app.test_client()
    takeover = client.patch("/ui/api/access",
                            json={"web_user": "intruder", "web_password": PASSWORD, "web_login": True}, **remote)
    assert takeover.status_code == 403
    assert client.patch("/ui/api/access", json={"api_login": False}, **remote).status_code == 403
    assert not access.login_required("web") and access.public()["web_user"] == ""
    assert client.get("/ui/api/access", **remote).status_code == 200        # looking is allowed

    assert client.patch("/ui/api/access",
                        json={"web_user": "operator", "web_password": PASSWORD, "web_login": True}, **here).status_code == 200
    visitor = app.test_client()        # another browser, on another machine
    assert visitor.patch("/ui/api/access", json={"api_enabled": False}, **remote).status_code == 401
    visitor.post("/ui/api/login", json={"user": "operator", "password": PASSWORD}, **remote)
    assert visitor.patch("/ui/api/access", json={"api_enabled": False}, **remote).status_code == 200


# -- limits ------------------------------------------------------------------

def test_receiver_keeps_a_bounded_number_of_displays():
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    receiver = TslReceiver(ReceiverSettings(bind="127.0.0.1", port=port))
    receiver.listen()
    try:
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        total = MAX_DISPLAYS + 150
        for index in range(total):
            sender.sendto(p.build_v50(index, 0, 3, "X"), ("127.0.0.1", port))
            if index % 50 == 0:
                time.sleep(0.01)
        sender.close()
        wait_for(lambda: receiver.snapshot()["status"]["packet_count"] >= total * 0.9)
        status = receiver.snapshot()["status"]
        assert status["display_count"] <= MAX_DISPLAYS
        assert len(status["displays"]) <= 200 and len(status["log"]) <= 40
    finally:
        receiver.close()


def test_receiver_limits_tcp_connections():
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    receiver = TslReceiver(ReceiverSettings(transport="tcp", bind="127.0.0.1", port=port))
    assert receiver.listen()
    connections = []
    try:
        for _ in range(MAX_TCP_CLIENTS + 10):
            connections.append(socket.create_connection(("127.0.0.1", port), timeout=2))
        assert wait_for(lambda: receiver.snapshot()["status"]["clients"] == MAX_TCP_CLIENTS)
        time.sleep(0.3)
        assert receiver.snapshot()["status"]["clients"] == MAX_TCP_CLIENTS
    finally:
        for connection in connections:
            connection.close()
        receiver.close()
