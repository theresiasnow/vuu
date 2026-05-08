"""Tests for the CSV parser helpers in `main.py`."""
import pytest

from main import _parse_chirp_row, _parse_csv_row


# --- _parse_csv_row (VUU's own export format) -------------------------------

def _vuu_row(**overrides):
    base = {
        "index": "5",
        "name": "TEST",
        "freq_hz": "145500000",
        "offset_hz": "600000",
        "duplex": "-",
        "tx_tone": "88.5 Hz",
        "rx_tone": "None",
        "mode": "FM",
        "power": "Low (1.5W)",
        "step_khz": "12.5",
        "bclo": "false",
        "scanlist1": "true",
        "scanlist2": "0",
    }
    base.update(overrides)
    return base


def test_parse_csv_row_happy_path():
    parsed = _parse_csv_row(_vuu_row())
    assert parsed["index"] == 5
    assert parsed["freq_hz"] == 145_500_000
    assert parsed["offset_hz"] == 600_000
    assert parsed["bclo"] is False
    assert parsed["scanlist1"] is True
    assert parsed["scanlist2"] is False


@pytest.mark.parametrize("freq", ["0", "-1", "42949672950", "999999999999"])
def test_parse_csv_row_rejects_invalid_freq(freq):
    assert _parse_csv_row(_vuu_row(freq_hz=freq)) is None


def test_parse_csv_row_returns_none_when_freq_missing():
    row = _vuu_row()
    del row["freq_hz"]
    assert _parse_csv_row(row) is None


def test_parse_csv_row_clamps_bogus_offset():
    parsed = _parse_csv_row(_vuu_row(offset_hz="42949672950"))
    assert parsed["offset_hz"] == 0


def test_parse_csv_row_normalises_question_tone():
    parsed = _parse_csv_row(_vuu_row(tx_tone="?", rx_tone=""))
    assert parsed["tx_tone"] == "None"
    assert parsed["rx_tone"] == "None"


@pytest.mark.parametrize("raw,expected", [
    ("1", True), ("true", True), ("YES", True), ("y", True),
    ("0", False), ("false", False), ("no", False), ("", False),
])
def test_parse_csv_row_bool_coercion(raw, expected):
    assert _parse_csv_row(_vuu_row(bclo=raw))["bclo"] is expected


# --- _parse_chirp_row (CHIRP CSV format) ------------------------------------

def _chirp_row(**overrides):
    base = {
        "Location": "1",
        "Name": "REPEATER",
        "Frequency": "145.500000",
        "Duplex": "-",
        "Offset": "0.600000",
        "Tone": "Tone",
        "rToneFreq": "88.5",
        "cToneFreq": "88.5",
        "DtcsCode": "023",
        "RxDtcsCode": "023",
        "TStep": "12.5",
        "Mode": "FM",
        "Power": "5.0W",
        "Skip": "",
    }
    base.update(overrides)
    return base


def test_parse_chirp_row_indexes_from_zero():
    # Location 1 in CHIRP becomes index 0 internally
    assert _parse_chirp_row(_chirp_row(Location="1"))["index"] == 0
    assert _parse_chirp_row(_chirp_row(Location="200"))["index"] == 199


def test_parse_chirp_row_converts_mhz_to_hz():
    parsed = _parse_chirp_row(_chirp_row(Frequency="145.500000", Offset="0.600000"))
    assert parsed["freq_hz"] == 145_500_000
    assert parsed["offset_hz"] == 600_000


def test_parse_chirp_row_zeros_offset_when_simplex():
    parsed = _parse_chirp_row(_chirp_row(Duplex="", Offset="0.600000"))
    assert parsed["duplex"] == ""
    assert parsed["offset_hz"] == 0


@pytest.mark.parametrize("tone,expected_tx,expected_rx", [
    ("Tone", "88.5 Hz", "None"),                 # tx CTCSS only
    ("TSQL", "88.5 Hz", "88.5 Hz"),              # both CTCSS, uses cToneFreq
    ("DTCS", "D023N", "D023N"),                  # both DCS
    ("", "None", "None"),                        # no tone
    ("Cross", "None", "None"),                   # unknown kind
])
def test_parse_chirp_row_tone_mapping(tone, expected_tx, expected_rx):
    parsed = _parse_chirp_row(_chirp_row(Tone=tone))
    assert parsed["tx_tone"] == expected_tx
    assert parsed["rx_tone"] == expected_rx


@pytest.mark.parametrize("power,expected", [
    ("5.0W", "High (5W)"),
    ("High", "High (5W)"),
    ("3.0W", "Med (3W)"),
    ("Mid", "Med (3W)"),
    ("Med", "Med (3W)"),
    ("1.0W", "Low (1.5W)"),
    ("", "Low (1.5W)"),
])
def test_parse_chirp_row_power_mapping(power, expected):
    assert _parse_chirp_row(_chirp_row(Power=power))["power"] == expected


@pytest.mark.parametrize("mode,expected", [
    ("FM", "FM"),
    ("NFM", "NFM"),
    ("AM", "AM"),
    ("WFM", "FM"),       # unsupported -> fallback FM
    ("garbage", "FM"),
])
def test_parse_chirp_row_mode_mapping(mode, expected):
    assert _parse_chirp_row(_chirp_row(Mode=mode))["mode"] == expected


def test_parse_chirp_row_skip_disables_scanlist1():
    assert _parse_chirp_row(_chirp_row(Skip="S"))["scanlist1"] is False
    assert _parse_chirp_row(_chirp_row(Skip=""))["scanlist1"] is True


def test_parse_chirp_row_returns_none_for_bad_input():
    assert _parse_chirp_row({"Location": "abc", "Frequency": "145.5"}) is None
    assert _parse_chirp_row({"Frequency": "145.5"}) is None  # missing Location


def test_parse_chirp_row_invalid_tstep_falls_back():
    assert _parse_chirp_row(_chirp_row(TStep="garbage"))["step_khz"] == 5.0
