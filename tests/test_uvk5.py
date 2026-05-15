"""Tests for the UV-K5 protocol layer (`uvk5.py`).

Hardware-free: exercises pure functions and the encode/decode round-trip.
"""
import struct

import pytest

import uvk5


# --- low-level primitives ---------------------------------------------------

def test_xor_is_involutive():
    data = bytes(range(64))
    assert uvk5._xor(uvk5._xor(data)) == data


def test_crc16_known_vector():
    # CRC-16/XMODEM of "123456789" is 0x31C3
    assert uvk5._crc16(b"123456789") == 0x31C3


# --- _parse_tone ------------------------------------------------------------

@pytest.mark.parametrize("label,expected", [
    ("None", (0, 0)),
    ("", (0, 0)),
    ("?", (0, 0)),
    ("off", (0, 0)),
    ("88.5 Hz", (1, uvk5.CTCSS_TONES.index(88.5))),
    ("100.0", (1, uvk5.CTCSS_TONES.index(100.0))),
    ("D023N", (2, uvk5.DCS_CODES.index(23))),
    ("D754N", (2, uvk5.DCS_CODES.index(754))),
])
def test_parse_tone(label, expected):
    assert uvk5._parse_tone(label) == expected


def test_parse_tone_snaps_ctcss_to_nearest():
    # 89.0 Hz isn't in the table; 88.5 is the closest valid CTCSS tone.
    mode, code = uvk5._parse_tone("89.0 Hz")
    assert mode == 1
    assert uvk5.CTCSS_TONES[code] == 88.5


def test_parse_tone_invalid_dcs_returns_none():
    # 999 is not a valid DCS code -> falls back to (0, 0)
    assert uvk5._parse_tone("D999N") == (0, 0)


# --- _encode_channel --------------------------------------------------------

def _sample_channel(**overrides):
    base = {
        "index": 0,
        "name": "REPEATER1",
        "freq_hz": 145_500_000,
        "offset_hz": 600_000,
        "duplex": "-",
        "tx_tone": "88.5 Hz",
        "rx_tone": "None",
        "mode": "FM",
        "power": "Med (3W)",
        "step_khz": 12.5,
        "bclo": True,
        "scanlist1": True,
        "scanlist2": False,
    }
    base.update(overrides)
    return base


def test_encode_channel_layout():
    entry, attr, name = uvk5._encode_channel(_sample_channel())

    assert len(entry) == 16
    assert len(name) == 16

    freq_raw, offset_raw = struct.unpack_from("<II", entry, 0)
    assert freq_raw == 145_500_000 // 10
    assert offset_raw == 600_000 // 10

    # tx CTCSS = mode 1, rx None = mode 0 -> low nibble = 1, high nibble = 0
    assert entry[10] & 0x0F == 1
    assert (entry[10] >> 4) & 0x0F == 0

    # flags1: shift "-" = 1, AM bit clear
    assert entry[11] & 0x03 == 1
    assert entry[11] & 0x08 == 0

    # flags2: power Med = idx 1 (bits 2-3), bclo set, narrow bw clear
    assert (entry[12] >> 2) & 0x03 == 1
    assert entry[12] & 0x10 == 0x10
    assert entry[12] & 0x02 == 0

    assert entry[14] == uvk5.STEPS_KHZ.index(12.5)

    # attr: scanlist1 set, scanlist2 clear, free bit clear (in-use)
    assert attr & 0x01
    assert not attr & 0x02
    assert not attr & 0x10

    assert name.startswith(b"REPEATER1")
    assert name[len("REPEATER1"):] == b"\x00" * (16 - len("REPEATER1"))


@pytest.mark.parametrize("mode,expect_am,expect_narrow", [
    ("FM", False, False),
    ("NFM", False, True),
    ("AM", True, False),
])
def test_encode_channel_mode_flags(mode, expect_am, expect_narrow):
    entry, _, _ = uvk5._encode_channel(_sample_channel(mode=mode))
    assert bool(entry[11] & 0x08) is expect_am
    assert bool(entry[12] & 0x02) is expect_narrow


def test_encode_channel_step_snaps_to_nearest():
    entry, _, _ = uvk5._encode_channel(_sample_channel(step_khz=7.0))
    # closest to 7.0 in [2.5, 5.0, 6.25, 10.0, 12.5, 25.0] is 6.25
    assert uvk5.STEPS_KHZ[entry[14]] == 6.25


def test_encode_channel_truncates_long_name():
    entry, _, name = uvk5._encode_channel(
        _sample_channel(name="THIS_NAME_IS_WAY_TOO_LONG_FOR_THE_RADIO")
    )
    assert len(name) == 16
    assert name == b"THIS_NAME_IS_WAY"


def test_encode_channel_unknown_power_falls_back_to_low():
    entry, _, _ = uvk5._encode_channel(_sample_channel(power="Bogus"))
    assert (entry[12] >> 2) & 0x03 == 0


@pytest.mark.parametrize("freq_hz", [0, -10, 42_949_672_950])
def test_encode_channel_rejects_invalid_frequency(freq_hz):
    with pytest.raises(ValueError, match="invalid channel frequency"):
        uvk5._encode_channel(_sample_channel(freq_hz=freq_hz))


@pytest.mark.parametrize("offset_hz", [-10, 42_949_672_950])
def test_encode_channel_rejects_invalid_offset(offset_hz):
    with pytest.raises(ValueError, match="invalid channel offset"):
        uvk5._encode_channel(_sample_channel(offset_hz=offset_hz))


def test_encode_channel_invalid_step_falls_back_to_default():
    entry, _, _ = uvk5._encode_channel(_sample_channel(step_khz="garbage"))
    assert uvk5.STEPS_KHZ[entry[14]] == 5.0
