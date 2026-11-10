import pytest

from tallytapir_app import protocol as p


def test_v31_packet_bytes():
    packet = p.build_v31(address=1, tally_bits=0b000001, brightness=3, text="CAM 1")
    assert packet == bytes.fromhex("8131") + b"CAM 1" + b" " * 11
    assert len(packet) == 18


def test_v31_maps_boxes_1_2_5_6_to_tally_1_to_4():
    assert p.v3_tallies(0b000001) == 0b0001
    assert p.v3_tallies(0b000010) == 0b0010
    assert p.v3_tallies(0b010000) == 0b0100
    assert p.v3_tallies(0b100000) == 0b1000
    assert p.v3_tallies(0b001100) == 0  # boxes 3 and 4 have no effect in V3.1


def test_v31_truncates_and_pads_text_to_16_characters():
    long = p.build_v31(0, 0, 0, "ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    assert long[2:] == b"ABCDEFGHIJKLMNOP"
    short = p.build_v31(0, 0, 0, "")
    assert short[2:] == b" " * 16


def test_v31_rejects_address_above_126():
    with pytest.raises(p.ProtocolError):
        p.build_v31(127, 0, 0, "X")


def test_v40_packet_bytes():
    packet = p.build_v40(address=1, tally_bits=0b000001, brightness=3, text="CAM 1")
    assert len(packet) == 22
    assert packet[:18] == p.build_v31(1, 0b000001, 3, "CAM 1")
    # Sum of the 18 body bytes is 820, 820 mod 128 = 52, two's complement = 76.
    assert packet[18] == 0x4C
    assert packet[19:] == bytes([0x02, 0x01, 0x01])


def test_v40_checksum_makes_the_seven_bit_sum_zero():
    packet = p.build_v40(42, 0b101101, 2, "PGM")
    assert (sum(packet[:19])) & 0x7F == 0


def test_v50_packet_bytes():
    packet = p.build_v50(index=1, tally_bits=0b000001, brightness=3, text="AB")
    assert packet == bytes.fromhex("0C00 00 00 0000 0100 C100 0200 4142")


def test_v50_utf16_sets_flag_and_doubles_text_bytes():
    packet = p.build_v50(index=2, tally_bits=0, brightness=0, text="AB", utf16=True)
    assert packet == bytes.fromhex("0E00 00 01 0000 0200 0000 0400 41004200")


def test_v50_byte_count_excludes_itself():
    packet = p.build_v50(5, 0, 0, "HELLO")
    assert int.from_bytes(packet[:2], "little") == len(packet) - 2


def test_v50_wrapped_adds_header_and_stuffs_dle():
    plain = p.build_v50(index=0xFE, tally_bits=0, brightness=0, text="A")
    wrapped = p.wrap_v50(plain)
    assert wrapped[:2] == bytes([0xFE, 0x02])
    assert wrapped == bytes.fromhex("FE02 0B00 00 00 0000 FEFE00 0000 0100 41")
    # The byte count stays the count of the unstuffed packet.
    assert int.from_bytes(wrapped[2:4], "little") == len(plain) - 2
    assert p.unwrap_v50(wrapped) == plain


def test_build_packet_selects_protocol():
    args = dict(umd_id=3, tally_bits=0b11, brightness=1, text="X", utf16=False, screen=0)
    assert p.build_packet("v3.1", **args) == p.build_v31(3, 0b11, 1, "X")
    assert p.build_packet("v4.0", **args) == p.build_v40(3, 0b11, 1, "X")
    assert p.build_packet("v5.0", **args) == p.build_v50(3, 0b11, 1, "X")
    assert p.build_packet("v5.0w", **args) == p.wrap_v50(p.build_v50(3, 0b11, 1, "X"))
    with pytest.raises(p.ProtocolError):
        p.build_packet("v9", **args)


def test_parsers_round_trip():
    v3 = p.parse_v31(p.build_v31(12, 0b100001, 2, "STUDIO A"))
    assert (v3.address, v3.tallies, v3.brightness, v3.text.rstrip()) == (12, 0b1001, 2, "STUDIO A")

    v4 = p.parse_v40(p.build_v40(12, 0b100110, 1, "STUDIO B"))
    assert v4.checksum_ok
    assert v4.display_left == {"rh": 2, "txt": 1, "lh": 2}
    assert v4.display_right == v4.display_left

    v5 = p.parse_v50(p.build_v50(300, 0b011001, 3, "Grüße", utf16=True, screen=7))
    assert v5.byte_count_ok and v5.utf16 and v5.screen == 7
    display = v5.displays[0]
    assert (display.index, display.rh, display.txt, display.lh) == (300, 1, 2, 1)
    assert (display.brightness, display.text) == (3, "Grüße")


def test_format_hex():
    assert p.format_hex(b"\x01\xab") == "01 AB"
    assert p.format_hex(b"\x01\xab", braces=True) == "{01}{AB}"
