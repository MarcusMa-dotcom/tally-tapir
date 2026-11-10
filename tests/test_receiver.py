import socket
import time

import pytest

from tallytapir_app import protocol as p
from tallytapir_app.engine import Settings, TslSender
from tallytapir_app.receiver import ReceiverSettings, TslReceiver, load_settings, matches


def free_port(kind=socket.SOCK_DGRAM):
    sock = socket.socket(socket.AF_INET, kind)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def wait_for(condition, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def receiver():
    rx = TslReceiver(ReceiverSettings(bind="127.0.0.1", port=free_port()))
    yield rx
    rx.close()


# -- decoding and checks ---------------------------------------------------

def test_detect_protocol():
    assert p.detect_protocol(p.build_v31(1, 0, 3, "A")) == "v3.1"
    assert p.detect_protocol(p.build_v40(1, 0, 3, "A")) == "v4.0"
    assert p.detect_protocol(p.build_v50(1, 0, 3, "A")) == "v5.0"
    assert p.detect_protocol(p.wrap_v50(p.build_v50(1, 0, 3, "A"))) == "v5.0w"
    # A long V5.0 packet has a first byte above 0x7F and must not look like V3.1.
    assert p.detect_protocol(p.build_v50(1, 0, 3, "X" * 150)) == "v5.0"


def test_decode_gives_one_message_model_for_all_versions():
    v3 = p.decode("auto", p.build_v31(7, 0b100001, 2, "CAM 7"))
    assert v3.problems == []
    assert (v3.messages[0].index, v3.messages[0].tallies, v3.messages[0].screen) == (7, 0b1001, None)

    v4 = p.decode("auto", p.build_v40(7, 0b100001, 2, "CAM 7"))
    assert v4.problems == []
    assert (v4.messages[0].lh, v4.messages[0].rh, v4.messages[0].tallies) == (2, 1, 0b1001)

    v5 = p.decode("auto", p.wrap_v50(p.build_v50(0xFE, 0b000110, 1, "PGM", screen=3)))
    assert v5.problems == []
    message = v5.messages[0]
    assert (message.screen, message.index, message.rh, message.txt, message.text) == (3, 0xFE, 2, 1, "PGM")


def test_decode_reports_problems():
    broken = bytearray(p.build_v40(1, 0, 3, "A"))
    broken[18] ^= 0x01
    assert any("Checksum" in problem for problem in p.decode("v4.0", bytes(broken)).problems)

    wrong_count = bytearray(p.build_v50(1, 0, 3, "ABC"))
    wrong_count[0] += 1
    assert any("Byte count" in problem for problem in p.decode("v5.0", bytes(wrong_count)).problems)

    short = p.decode("v3.1", b"\x81\x00ABC")
    assert short.messages == [] and "18 bytes" in short.problems[0]

    unstuffed = p.decode("v5.0w", bytes([0xFE, 0x02, 0x0B, 0x00, 0, 0, 0, 0, 0xFE, 0, 0, 0, 1, 0, 65]))
    assert any("Single DLE" in problem for problem in unstuffed.problems)


def test_v5_packet_with_several_displays():
    one = p.build_v50(1, 0b01, 3, "ONE")
    two = p.build_v50(2, 0b10, 3, "TWO")
    payload = one[2:] + two[6:]  # second display message appended to the first packet
    packet = len(payload).to_bytes(2, "little") + payload
    decoded = p.decode("auto", packet)
    assert decoded.problems == []
    assert [(m.index, m.text, m.rh) for m in decoded.messages] == [(1, "ONE", 1), (2, "TWO", 2)]


# -- stream framing --------------------------------------------------------

def test_stream_decoder_splits_wrapped_packets_fed_byte_by_byte():
    first = p.wrap_v50(p.build_v50(0xFE, 0, 3, "A"))
    second = p.wrap_v50(p.build_v50(2, 0, 3, "BB"))
    decoder = p.StreamDecoder("auto")
    out = []
    for byte in first + second:
        out.extend(decoder.feed(bytes([byte])))
    assert out == [("v5.0w", first), ("v5.0w", second)]


def test_stream_decoder_tells_v31_from_v40():
    v3 = p.build_v31(1, 0, 3, "THREE")
    v4 = p.build_v40(2, 0, 3, "FOUR")
    decoder = p.StreamDecoder("auto")
    assert decoder.feed(v3 + v4 + v3) == [("v3.1", v3), ("v4.0", v4)]
    assert decoder.idle() == [("v3.1", v3)]  # the last one is complete once the line is quiet


def test_stream_decoder_reports_junk_and_resyncs():
    packet = p.wrap_v50(p.build_v50(1, 0, 3, "OK"))
    decoder = p.StreamDecoder("v5.0w")
    assert decoder.feed(b"\x01\x02\x03" + packet) == [("junk", b"\x01\x02\x03"), ("v5.0w", packet)]


def test_stream_decoder_unwrapped_v5():
    a = p.build_v50(1, 0, 3, "A")
    b = p.build_v50(2, 0, 3, "B" * 40)
    decoder = p.StreamDecoder("v5.0")
    assert decoder.feed(a + b[:10]) == [("v5.0", a)]
    assert decoder.feed(b[10:]) == [("v5.0", b)]


# -- filter ----------------------------------------------------------------

def test_matches_filter_rules():
    v5 = p.Message("v5.0", index=5, text="", brightness=0, screen=2)
    assert matches(v5, None, None)
    assert matches(v5, 2, 5)
    assert not matches(v5, 3, 5)
    assert not matches(v5, 2, 6)
    broadcast = p.Message("v5.0", index=0xFFFF, text="", brightness=0, screen=0xFFFF)
    assert matches(broadcast, 3, 6)
    v3 = p.Message("v3.1", index=5, text="", brightness=0)
    assert matches(v3, 9, 5)       # V3.1 has no screen, the screen filter does not apply
    assert not matches(v3, None, 6)


# -- receiver against the real sender ---------------------------------------

def test_udp_receiver_shows_what_the_sender_sends(receiver):
    assert receiver.listen()
    sender = TslSender(Settings(port=receiver.settings.port, text="CAM 1", umd_id=4, tally_bits=0b100001))
    assert sender.connect()
    assert wait_for(lambda: receiver.snapshot()["status"]["packet_count"] == 1)
    status = receiver.snapshot()["status"]
    assert status["problem_count"] == 0
    watched = status["watched"]
    assert (watched["index"], watched["text"], watched["rh"], watched["lh"]) == (4, "CAM 1", 1, 2)
    entry = status["log"][0]
    assert entry["protocol"] == "v5.0" and entry["matched"]
    assert "ID 4" in entry["summary"] and entry["hex"].startswith("0F 00")
    sender.stop()


def test_watched_display_only_follows_matching_packets(receiver):
    receiver.update(watch_screen=1, watch_id=10)
    receiver.listen()
    sender = TslSender(Settings(port=receiver.settings.port, send_on_change=False, screen=1, umd_id=10, text="MINE"))
    sender.connect()
    sender.send()
    assert wait_for(lambda: receiver.snapshot()["status"]["watched"] is not None)
    sender.update(umd_id=11, text="OTHER ID")
    sender.send()
    sender.update(umd_id=10, screen=2, text="OTHER SCREEN")
    sender.send()
    assert wait_for(lambda: receiver.snapshot()["status"]["packet_count"] == 3)
    status = receiver.snapshot()["status"]
    assert status["watched"]["text"] == "MINE"
    assert [entry["matched"] for entry in status["log"]] == [True, False, False]
    assert status["display_count"] == 3
    # Opening the filter picks the latest display seen, and the log follows the new filter.
    opened = receiver.update(watch_screen="", watch_id=None)["status"]
    assert opened["watched"]["text"] == "OTHER SCREEN"
    assert [entry["matched"] for entry in opened["log"]] == [True, True, True]
    sender.stop()


def test_tcp_receiver_accepts_wrapped_stream():
    rx = TslReceiver(ReceiverSettings(transport="tcp", bind="127.0.0.1", port=free_port(socket.SOCK_STREAM)))
    try:
        assert rx.listen()
        sender = TslSender(Settings(transport="tcp", protocol="v5.0w", port=rx.settings.port,
                                    umd_id=0xFE, text="WRAPPED"))
        assert sender.connect()
        assert wait_for(lambda: rx.snapshot()["status"]["packet_count"] == 1)
        sender.update(text="AGAIN")
        assert wait_for(lambda: rx.snapshot()["status"]["packet_count"] == 2)
        status = rx.snapshot()["status"]
        assert status["clients"] == 1 and status["problem_count"] == 0
        assert status["watched"]["text"] == "AGAIN"
        assert status["log"][-1]["protocol"] == "v5.0w"
        sender.stop()
        assert wait_for(lambda: rx.snapshot()["status"]["clients"] == 0)
    finally:
        rx.close()


def test_tcp_receiver_takes_v31_after_quiet_line():
    rx = TslReceiver(ReceiverSettings(transport="tcp", bind="127.0.0.1", port=free_port(socket.SOCK_STREAM)))
    try:
        rx.listen()
        sender = TslSender(Settings(transport="tcp", protocol="v3.1", port=rx.settings.port,
                                    umd_id=9, text="SERIAL STYLE", tally_bits=0b000011))
        sender.connect()
        assert wait_for(lambda: rx.snapshot()["status"]["packet_count"] == 1)
        watched = rx.snapshot()["status"]["watched"]
        assert (watched["protocol"], watched["index"], watched["tallies"], watched["text"]) == (
            "v3.1", 9, 0b0011, "SERIAL STYLE")
        sender.stop()
    finally:
        rx.close()


def test_problem_packets_are_counted_and_logged(receiver):
    receiver.listen()
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    broken = bytearray(p.build_v40(1, 0, 3, "BAD SUM"))
    broken[18] ^= 0x10
    probe.sendto(bytes(broken), ("127.0.0.1", receiver.settings.port))
    probe.close()
    assert wait_for(lambda: receiver.snapshot()["status"]["packet_count"] == 1)
    status = receiver.snapshot()["status"]
    assert status["problem_count"] == 1
    assert "Checksum" in status["log"][0]["problems"][0]


def test_listen_failure_and_restart(receiver):
    blocker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    blocker.bind(("127.0.0.1", receiver.settings.port))
    assert not receiver.listen()
    assert "Cannot listen" in receiver.snapshot()["status"]["error"]
    blocker.close()
    assert receiver.listen()
    receiver.update(port=free_port())     # changing the port stops listening
    assert not receiver.listening


def test_clear_forgets_everything(receiver):
    receiver.listen()
    sender = TslSender(Settings(port=receiver.settings.port))
    sender.connect()
    assert wait_for(lambda: receiver.snapshot()["status"]["packet_count"] == 1)
    receiver.clear()
    status = receiver.snapshot()["status"]
    assert (status["packet_count"], status["watched"], status["displays"], status["log"]) == (0, None, [], [])
    sender.stop()


def test_receiver_settings_validation_and_persistence(tmp_path):
    path = tmp_path / "receiver.json"
    rx = TslReceiver(config_path=path)
    with pytest.raises(ValueError):
        rx.update(protocol="v9")
    with pytest.raises(ValueError):
        rx.update(watch_id=70000)
    rx.update(watch_id="12", watch_screen="", protocol="v4.0", port=5555)
    loaded = load_settings(path)
    assert (loaded.watch_id, loaded.watch_screen, loaded.protocol, loaded.port) == (12, None, "v4.0", 5555)
