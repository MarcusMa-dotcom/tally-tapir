"""The example scripts in examples/ must keep working against the real program."""

import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from tallytapir_app import protocol as p
from tallytapir_app.access import Access
from tallytapir_app.engine import Settings, TslSender
from tallytapir_app.web import WebServer

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


@pytest.fixture
def udp_receiver():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(3.0)
    yield sock
    sock.close()


@pytest.fixture
def server(udp_receiver):
    engine = TslSender(Settings(port=udp_receiver.getsockname()[1]))
    web = WebServer(engine, access=Access(), port=0)
    assert web.start()
    yield web, engine
    web.stop()
    engine.stop()


def last_display(sock):
    data = None
    sock.settimeout(1.0)
    try:
        while True:
            data, _ = sock.recvfrom(4096)
    except socket.timeout:
        pass
    return p.parse_v50(data).displays[0]


def test_python_client_example(server, udp_receiver):
    web, engine = server
    result = subprocess.run(
        [sys.executable, str(EXAMPLES / "api_client.py"), "--base", f"http://localhost:{web.port}",
         "--id", "4", "--text", "EXAMPLE", "--right", "red", "--left", "green"],
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "Sent to UDP 127.0.0.1" in result.stdout
    display = last_display(udp_receiver)
    assert (display.index, display.text, display.rh, display.lh) == (4, "EXAMPLE", 1, 2)


def test_python_client_reports_a_refused_login(server):
    web, _ = server
    web.access.update(api_user="robot", api_password="long enough password", api_login=True)
    result = subprocess.run(
        [sys.executable, str(EXAMPLES / "api_client.py"), "--base", f"http://localhost:{web.port}"],
        capture_output=True, text=True, timeout=30)
    assert result.returncode != 0 and "401" in result.stderr


@pytest.mark.skipif(shutil.which("curl") is None or sys.platform == "win32", reason="needs curl and a POSIX shell")
def test_curl_example(server, udp_receiver):
    web, _ = server
    result = subprocess.run(
        ["sh", str(EXAMPLES / "set_tally.sh"), "7", "CURL", "17"],
        capture_output=True, text=True, timeout=30, env={"BASE": f"http://localhost:{web.port}", "PATH": "/usr/bin:/bin"})
    assert result.returncode == 0, result.stderr
    display = last_display(udp_receiver)
    assert (display.index, display.text, display.rh, display.lh) == (7, "CURL", 1, 1)


def test_watch_example_starts(server):
    result = subprocess.run([sys.executable, str(EXAMPLES / "watch_receiver.py"), "--help"],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0 and "watched display" in result.stdout
