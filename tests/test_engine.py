import socket
import threading
from datetime import datetime

import pytest

from tallytapir_app import protocol as p
from tallytapir_app.engine import Settings, TslSender, load_settings


@pytest.fixture
def udp_receiver():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(2.0)
    yield sock
    sock.close()


@pytest.fixture
def tcp_receiver():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    received = bytearray()
    ready = threading.Event()

    def serve():
        conn, _ = server.accept()
        ready.set()
        with conn:
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                received.extend(chunk)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    yield server.getsockname()[1], received, ready, thread
    server.close()


def make_sender(**settings):
    return TslSender(Settings(**settings))


def test_udp_send_reaches_receiver(udp_receiver):
    sender = make_sender(port=udp_receiver.getsockname()[1], text="CAM 1", umd_id=4, tally_bits=0b000001)
    assert sender.connect()
    data, _ = udp_receiver.recvfrom(4096)  # sent once on connect
    packet = p.parse_v50(data)
    assert packet.byte_count_ok
    assert packet.displays[0].index == 4
    assert packet.displays[0].text == "CAM 1"
    assert packet.displays[0].rh == 1
    assert sender.snapshot()["status"]["sent_count"] == 1


def test_change_is_sent_immediately_when_connected(udp_receiver):
    sender = make_sender(port=udp_receiver.getsockname()[1])
    sender.connect()
    udp_receiver.recvfrom(4096)
    sender.update(tally_bits=0b110000, text="PGM")
    data, _ = udp_receiver.recvfrom(4096)
    display = p.parse_v50(data).displays[0]
    assert (display.lh, display.text) == (3, "PGM")


def test_no_automatic_send_when_send_on_change_is_off(udp_receiver):
    sender = make_sender(port=udp_receiver.getsockname()[1], send_on_change=False)
    sender.connect()
    sender.update(text="QUIET")
    udp_receiver.settimeout(0.2)
    with pytest.raises(socket.timeout):
        udp_receiver.recvfrom(4096)
    assert sender.send()
    udp_receiver.settimeout(2.0)
    data, _ = udp_receiver.recvfrom(4096)
    assert p.parse_v50(data).displays[0].text == "QUIET"


def test_tcp_sends_wrapped_packets(tcp_receiver):
    port, received, ready, thread = tcp_receiver
    sender = make_sender(transport="tcp", port=port, protocol="v5.0w", umd_id=0xFE, text="A")
    assert sender.connect()
    assert ready.wait(2.0)
    sender.update(text="B")
    sender.disconnect()
    thread.join(2.0)
    first = p.wrap_v50(p.build_v50(0xFE, 0, 3, "A"))
    second = p.wrap_v50(p.build_v50(0xFE, 0, 3, "B"))
    assert bytes(received) == first + second


def test_tcp_connect_failure_is_reported():
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    sender = make_sender(transport="tcp", port=port)
    assert not sender.connect()
    status = sender.snapshot()["status"]
    assert not status["connected"]
    assert "Cannot connect" in status["error"]


def test_send_without_connection_reports_error():
    sender = make_sender()
    assert not sender.send()
    assert sender.snapshot()["status"]["error"] == "Not connected"


def test_changing_connection_settings_disconnects(udp_receiver):
    sender = make_sender(port=udp_receiver.getsockname()[1])
    sender.connect()
    sender.update(port=1234)
    assert not sender.connected


def test_interval_resends_and_toggles_tallies(udp_receiver):
    sender = make_sender(
        port=udp_receiver.getsockname()[1],
        tally_bits=0b000011,
        interval_ms=100,
        interval_enabled=True,
        tally_toggle=True,
    )
    sender.connect()
    udp_receiver.recvfrom(4096)
    sender.tick(10.0)   # arms the timer
    sender.tick(10.05)  # too early
    sender.tick(10.15)  # first interval: tallies off
    sender.tick(10.25)  # second interval: tallies on again
    states = [p.parse_v50(udp_receiver.recvfrom(4096)[0]).displays[0].rh for _ in range(2)]
    assert states == [0, 3]
    assert sender.settings.tally_bits == 0b000011  # the setting itself stays


def test_clock_replaces_text_and_sends_on_change(udp_receiver):
    stamps = iter([
        datetime(2026, 11, 10, 14, 5, 9, 340_000),
        datetime(2026, 11, 10, 14, 5, 9, 380_000),
        datetime(2026, 11, 10, 14, 5, 9, 410_000),
    ])
    sender = TslSender(Settings(port=udp_receiver.getsockname()[1]), now=lambda: next(stamps))
    sender.connect()
    udp_receiver.recvfrom(4096)
    sender.update(clock=True, clock_tenths=True)       # 14:05:09.3
    assert p.parse_v50(udp_receiver.recvfrom(4096)[0]).displays[0].text == "14:05:09.3"
    sender.tick(1.0)                                   # still .3, nothing sent
    sender.tick(1.02)                                  # 14:05:09.4
    assert p.parse_v50(udp_receiver.recvfrom(4096)[0]).displays[0].text == "14:05:09.4"
    assert sender.snapshot()["status"]["sent_count"] == 3


def test_invalid_packet_is_reported_not_sent(udp_receiver):
    sender = make_sender(port=udp_receiver.getsockname()[1], protocol="v3.1", umd_id=200)
    sender.connect()
    status = sender.snapshot()["status"]
    assert "UMD ID must be 0 to 126" in status["packet_error"]
    assert status["sent_count"] == 0


def test_update_validates_input():
    sender = make_sender()
    with pytest.raises(ValueError):
        sender.update(brightness=4)
    with pytest.raises(ValueError):
        sender.update(protocol="v6")
    with pytest.raises(ValueError):
        sender.update(nonsense=1)
    with pytest.raises(ValueError):
        sender.update(utf16="yes")
    assert sender.update(umd_id="17", port="9000")["settings"]["umd_id"] == 17


def test_update_is_all_or_nothing():
    sender = make_sender()
    with pytest.raises(ValueError):
        sender.update(text="NEW", brightness=9)
    assert sender.settings.text != "NEW"


def test_settings_are_saved_and_loaded(tmp_path):
    path = tmp_path / "settings.json"
    sender = TslSender(config_path=path)
    sender.update(text="SAVED", protocol="v4.0", port=5005)
    sender.stop()
    loaded = load_settings(path)
    assert (loaded.text, loaded.protocol, loaded.port) == ("SAVED", "v4.0", 5005)


def test_broken_settings_file_falls_back_to_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"protocol": "v9", "brightness": 2, "port": "abc"}', encoding="utf-8")
    loaded = load_settings(path)
    assert (loaded.protocol, loaded.brightness, loaded.port) == ("v5.0", 2, 9800)
