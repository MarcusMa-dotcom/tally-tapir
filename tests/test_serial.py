"""Serial sending and receiving over a pseudo terminal (no hardware needed)."""

import os
import select
import sys
import time

import pytest

pytest.importorskip("serial")
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="pseudo terminals are POSIX only")

from tallytapir_app import protocol as p  # noqa: E402
from tallytapir_app.engine import Settings, TslSender  # noqa: E402
from tallytapir_app.receiver import ReceiverSettings, TslReceiver  # noqa: E402


@pytest.fixture
def pty_pair():
    master, slave = os.openpty()
    yield master, os.ttyname(slave)
    os.close(master)
    os.close(slave)


def read_exactly(fd, size, timeout=2.0):
    data = b""
    deadline = time.monotonic() + timeout
    while len(data) < size and time.monotonic() < deadline:
        ready, _, _ = select.select([fd], [], [], 0.1)
        if ready:
            data += os.read(fd, size - len(data))
    return data


def test_sender_writes_v40_packet_to_serial_port(pty_pair):
    master, device = pty_pair
    sender = TslSender(Settings(transport="serial", serial_port=device, protocol="v4.0",
                                umd_id=3, text="SERIAL", tally_bits=0b000001))
    assert sender.connect(), sender.snapshot()["status"]["error"]
    data = read_exactly(master, 22)
    assert data == p.build_v40(3, 0b000001, 3, "SERIAL")
    assert "38400 8E1" in sender.snapshot()["status"]["connection"]
    sender.stop()


def test_receiver_reads_v31_and_v40_from_serial_port(pty_pair):
    master, device = pty_pair
    receiver = TslReceiver(ReceiverSettings(transport="serial", serial_port=device))
    assert receiver.listen(), receiver.snapshot()["status"]["error"]
    try:
        os.write(master, p.build_v31(5, 0b000010, 2, "THREE"))
        os.write(master, p.build_v40(6, 0b110000, 3, "FOUR"))
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and receiver.snapshot()["status"]["packet_count"] < 2:
            time.sleep(0.02)
        status = receiver.snapshot()["status"]
        assert status["packet_count"] == 2 and status["problem_count"] == 0
        seen = {(d["protocol"], d["index"], d["text"]) for d in status["displays"]}
        assert seen == {("v3.1", 5, "THREE"), ("v4.0", 6, "FOUR")}
    finally:
        receiver.close()


def test_missing_serial_port_is_reported():
    sender = TslSender(Settings(transport="serial", serial_port=""))
    assert not sender.connect()
    assert sender.snapshot()["status"]["error"] == "No serial port selected"
    receiver = TslReceiver(ReceiverSettings(transport="serial", serial_port="/dev/does-not-exist"))
    assert not receiver.listen()
    assert "Cannot open" in receiver.snapshot()["status"]["error"]
