"""UV-K5 serial protocol — based on sq5bpf/k5prog reference implementation."""
import struct
import serial

BAUD_RATE = 38400
MEM_BLOCK = 0x80
MAX_CHANNELS = 200
MAX_EEPROM_RAW = 0xFFFFFFFF

_XOR_KEY = bytes([0x16, 0x6C, 0x14, 0xE6, 0x2E, 0x91, 0x0D, 0x40,
                  0x21, 0x35, 0xD5, 0x40, 0x13, 0x03, 0xE9, 0x80])

# Magic 4-byte "session id" — must be identical on every packet of a session.
# Per k5prog (uvk5_hello / uvk5_readmem1): 0x6a 0x39 0x57 0x64
SESSION_ID = bytes([0x6A, 0x39, 0x57, 0x64])

CTCSS_TONES = [
    67.0, 69.3, 71.9, 74.4, 77.0, 79.7, 82.5, 85.4, 88.5, 91.5,
    94.8, 97.4, 100.0, 103.5, 107.2, 110.9, 114.8, 118.8, 123.0, 127.3,
    131.8, 136.5, 141.3, 146.2, 151.4, 156.7, 159.8, 162.2, 165.5, 167.9,
    171.3, 173.8, 177.3, 179.9, 183.5, 186.2, 189.9, 192.8, 196.6, 199.5,
    203.5, 206.5, 210.7, 218.1, 225.7, 229.1, 233.6, 241.8, 250.3, 254.1,
]

DCS_CODES = [
    23, 25, 26, 31, 32, 36, 43, 47, 51, 53, 54, 65, 71, 72, 73, 74,
    114, 115, 116, 122, 125, 131, 132, 134, 143, 145, 152, 155, 156, 162,
    165, 172, 174, 205, 212, 223, 225, 226, 243, 244, 245, 246, 251, 252,
    255, 261, 263, 265, 266, 271, 274, 306, 311, 315, 325, 331, 332, 343,
    346, 351, 356, 364, 365, 371, 411, 412, 413, 423, 431, 432, 445, 446,
    452, 454, 455, 462, 464, 465, 466, 503, 506, 516, 523, 526, 532, 546,
    565, 606, 612, 624, 627, 631, 632, 654, 662, 664, 703, 712, 723, 731,
    732, 734, 743, 754,
]

STEPS_KHZ = [2.5, 5.0, 6.25, 10.0, 12.5, 25.0]
POWER_LABELS = ["Low (1.5W)", "Med (3W)", "High (5W)"]


def _crc16(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = (crc << 1) ^ 0x1021 if crc & 0x8000 else crc << 1
    return crc & 0xFFFF


def _xor(data: bytes) -> bytes:
    return bytes(b ^ _XOR_KEY[i % 16] for i, b in enumerate(data))


def _send_cmd(ser: serial.Serial, cmd: bytes) -> None:
    """Frame and send a cleartext command. cmd = id(2) + size(2) + payload."""
    crc = _crc16(cmd)
    obfuscated = _xor(cmd + struct.pack("<H", crc))
    frame = (b"\xab\xcd" +
             struct.pack("<H", len(cmd)) +
             obfuscated +
             b"\xdc\xba")
    ser.reset_input_buffer()
    ser.write(frame)


def _recv_cmd(ser: serial.Serial) -> bytes:
    """Read one response frame, return deobfuscated cleartext cmd (without crc)."""
    hdr = ser.read(4)
    if len(hdr) < 4:
        raise IOError(f"Timeout waiting for response (got {len(hdr)}/4 hdr bytes: {hdr.hex()})")
    if hdr[0] != 0xAB or hdr[1] != 0xCD:
        raise IOError(f"Bad magic: {hdr.hex()}")
    cmd_len = hdr[2] | (hdr[3] << 8)
    rest = ser.read(cmd_len + 2 + 2)  # cmd + crc + footer
    if len(rest) < cmd_len + 4:
        raise IOError(f"Short read: expected {cmd_len + 4}, got {len(rest)} ({rest.hex()})")
    body = rest[:cmd_len + 2]  # cmd + crc (encrypted)
    footer = rest[cmd_len + 2:]
    if footer != b"\xdc\xba":
        raise IOError(f"Bad footer: {footer.hex()}")
    cleartext = _xor(body)
    # Radio sends 0xffff instead of a real CRC — don't validate.
    return cleartext[:-2]


def handshake(ser: serial.Serial) -> tuple[str, bytes]:
    """Send hello, return (firmware_version, session_id)."""
    cmd = bytes([0x14, 0x05, 0x04, 0x00]) + SESSION_ID
    _send_cmd(ser, cmd)
    reply = _recv_cmd(ser)
    if len(reply) < 4 or reply[0] != 0x15 or reply[1] != 0x05:
        raise IOError(f"Handshake failed — reply: {reply.hex()}")
    # reply layout: id(2) + size(2) + version(16) + ...
    version = reply[4:20].rstrip(b"\x00\xff").decode("ascii", errors="replace").strip()
    return version, SESSION_ID


def read_eeprom(ser: serial.Serial, offset: int, length: int, session_id: bytes = SESSION_ID) -> bytes:
    """Read `length` bytes from EEPROM at `offset`. length max = 0x80."""
    if not (0 < length <= MEM_BLOCK):
        raise ValueError(f"read_eeprom: invalid length ({length})")
    if not (0 <= offset <= 0xFFFF):
        raise ValueError(f"read_eeprom: invalid offset ({offset})")
    cmd = (bytes([0x1B, 0x05, 0x08, 0x00]) +
           struct.pack("<HBB", offset, length, 0) +
           session_id)
    _send_cmd(ser, cmd)
    reply = _recv_cmd(ser)
    if len(reply) < 8 or reply[0] != 0x1C or reply[1] != 0x05:
        raise IOError(f"read_eeprom 0x{offset:04x}: bad reply id ({reply[:4].hex()})")
    # reply: id(2) + size(2) + offset(2) + length(1) + 0(1) + data
    return reply[8:8 + length]


def read_all_channels(ser: serial.Serial, session_id: bytes | None = None) -> list[dict]:
    """Read all 200 channel slots and return list of channel dicts."""
    if session_id is None:
        _, session_id = handshake(ser)

    chan_data = b""
    for block_start in range(0x0000, 0x0C80, MEM_BLOCK):
        block_len = min(MEM_BLOCK, 0x0C80 - block_start)
        chan_data += read_eeprom(ser, block_start, block_len, session_id)

    attr_data = read_eeprom(ser, 0x0D60, 200, session_id)

    name_data = b""
    for block_start in range(0x0F50, 0x1BD0, MEM_BLOCK):
        block_len = min(MEM_BLOCK, 0x1BD0 - block_start)
        name_data += read_eeprom(ser, block_start, block_len, session_id)

    channels = []
    for i in range(200):
        entry = chan_data[i * 16:(i + 1) * 16]
        attr = attr_data[i] if i < len(attr_data) else 0xFF
        name_raw = name_data[i * 16:(i + 1) * 16] if len(name_data) >= (i + 1) * 16 else b""

        is_free = bool(attr & 0x10)
        if is_free:
            continue

        freq_raw, offset_raw = struct.unpack_from("<II", entry, 0)
        if freq_raw in (0x00000000, 0xFFFFFFFF):
            continue
        if offset_raw == 0xFFFFFFFF:
            offset_raw = 0
        rx_code = entry[8]
        tx_code = entry[9]
        code_flags = entry[10]
        flags1 = entry[11]
        flags2 = entry[12]
        step_idx = entry[14]

        tx_tone_mode = code_flags & 0x0F
        rx_tone_mode = (code_flags >> 4) & 0x0F
        shift = flags1 & 0x03
        enable_am = bool(flags1 & 0x08)
        bandwidth = bool(flags2 & 0x02)
        tx_power = (flags2 >> 2) & 0x03
        bclo = bool(flags2 & 0x10)

        name = name_raw.rstrip(b"\x00\xff").decode("ascii", errors="replace").strip()

        def tone_label(mode, code):
            if mode == 0:
                return "None"
            if mode == 1:
                return f"{CTCSS_TONES[code]:.1f} Hz" if code < len(CTCSS_TONES) else "None"
            return f"D{DCS_CODES[code]:03d}N" if code < len(DCS_CODES) else "None"

        duplex = ["", "-", "+"][shift] if shift < 3 else ""

        channels.append({
            "index": i,
            "name": name,
            "freq_hz": freq_raw * 10,
            "offset_hz": offset_raw * 10,
            "duplex": duplex,
            "tx_tone": tone_label(tx_tone_mode, tx_code),
            "rx_tone": tone_label(rx_tone_mode, rx_code),
            "mode": "AM" if enable_am else ("NFM" if bandwidth else "FM"),
            "power": POWER_LABELS[min(tx_power, 2)],
            "step_khz": STEPS_KHZ[step_idx] if step_idx < len(STEPS_KHZ) else 5.0,
            "bclo": bclo,
            "scanlist1": bool(attr & 0x01),
            "scanlist2": bool(attr & 0x02),
        })

    return channels


def _parse_tone(label: str) -> tuple[int, int]:
    """Return (mode, code). mode: 0=None, 1=CTCSS, 2=DCS."""
    s = (label or "").strip()
    if not s or s.lower() in ("none", "?", "off"):
        return 0, 0
    if s.upper().startswith("D"):
        try:
            num = int(s[1:].rstrip("NIni"))
            return 2, DCS_CODES.index(num)
        except (ValueError, IndexError):
            return 0, 0
    try:
        hz = float(s.split()[0])
        nearest = min(range(len(CTCSS_TONES)), key=lambda i: abs(CTCSS_TONES[i] - hz))
        return 1, nearest
    except (ValueError, IndexError):
        return 0, 0


def _encode_channel(ch: dict) -> tuple[bytes, int, bytes]:
    """Return (entry_16, attr_byte, name_16) for a channel dict."""
    freq_raw = int(round(ch["freq_hz"] / 10))
    offset_raw = int(round(ch.get("offset_hz", 0) / 10))
    if not (0 < freq_raw < MAX_EEPROM_RAW):
        raise ValueError(f"invalid channel frequency: {ch['freq_hz']}")
    if not (0 <= offset_raw < MAX_EEPROM_RAW):
        raise ValueError(f"invalid channel offset: {ch.get('offset_hz', 0)}")

    rx_mode, rx_code = _parse_tone(ch.get("rx_tone", "None"))
    tx_mode, tx_code = _parse_tone(ch.get("tx_tone", "None"))
    code_flags = (rx_mode << 4) | (tx_mode & 0x0F)

    duplex = ch.get("duplex") or ""
    shift = {"": 0, "-": 1, "+": 2}.get(duplex, 0)
    mode = (ch.get("mode") or "FM").upper()
    enable_am = mode == "AM"
    bandwidth_narrow = mode == "NFM"
    flags1 = shift & 0x03
    if enable_am:
        flags1 |= 0x08

    power_label = ch.get("power") or POWER_LABELS[0]
    try:
        tx_power = POWER_LABELS.index(power_label)
    except ValueError:
        tx_power = 0
    flags2 = ((tx_power & 0x03) << 2)
    if bandwidth_narrow:
        flags2 |= 0x02
    if ch.get("bclo"):
        flags2 |= 0x10

    step_khz = ch.get("step_khz", 5.0)
    try:
        step_value = float(step_khz)
    except (TypeError, ValueError):
        step_value = 5.0
    try:
        step_idx = STEPS_KHZ.index(step_value)
    except (TypeError, ValueError):
        step_idx = min(range(len(STEPS_KHZ)),
                       key=lambda i: abs(STEPS_KHZ[i] - step_value))

    entry = bytearray(16)
    struct.pack_into("<II", entry, 0, freq_raw & 0xFFFFFFFF, offset_raw & 0xFFFFFFFF)
    entry[8] = rx_code & 0xFF
    entry[9] = tx_code & 0xFF
    entry[10] = code_flags & 0xFF
    entry[11] = flags1 & 0xFF
    entry[12] = flags2 & 0xFF
    entry[13] = 0xFF
    entry[14] = step_idx & 0xFF
    entry[15] = 0xFF

    # attr: bit0 scanlist1, bit1 scanlist2, bit4 free (0=in use). Upper bits left set.
    attr = 0xEC  # default upper bits per k5prog
    if ch.get("scanlist1"):
        attr |= 0x01
    if ch.get("scanlist2"):
        attr |= 0x02
    attr &= ~0x10  # in-use

    name_bytes = (ch.get("name") or "").encode("ascii", errors="replace")[:16]
    name = name_bytes + b"\x00" * (16 - len(name_bytes))

    return bytes(entry), attr & 0xFF, name


_FREE_ENTRY = b"\xFF" * 16
_FREE_ATTR = 0xFF
_FREE_NAME = b"\xFF" * 16


def write_eeprom(ser: serial.Serial, offset: int, data: bytes,
                 session_id: bytes = SESSION_ID) -> None:
    """Write `data` (max 0x80 bytes) to EEPROM at `offset`."""
    if not (0 < len(data) <= MEM_BLOCK):
        raise ValueError(f"write_eeprom: invalid length ({len(data)})")
    if not (0 <= offset <= 0xFFFF):
        raise ValueError(f"write_eeprom: invalid offset ({offset})")
    cmd = (bytes([0x1D, 0x05, len(data) + 8, 0x00]) +
           struct.pack("<HBB", offset, len(data), 0) +
           session_id +
           data)
    _send_cmd(ser, cmd)
    reply = _recv_cmd(ser)
    if len(reply) < 4 or reply[0] != 0x1E or reply[1] != 0x05:
        raise IOError(f"write_eeprom 0x{offset:04x}: bad reply ({reply[:4].hex()})")


def write_all_channels(ser: serial.Serial, channels: list[dict],
                       session_id: bytes | None = None,
                       progress=None) -> None:
    """Write all 200 channel slots. `channels` may be sparse (by 'index').

    `progress(done, total)` is called after each EEPROM block written.
    """
    if session_id is None:
        _, session_id = handshake(ser)

    by_index: dict[int, dict] = {}
    for ch in channels:
        idx = ch.get("index")
        if idx is None or not (0 <= idx < MAX_CHANNELS):
            continue
        by_index[idx] = ch

    chan_buf = bytearray(MAX_CHANNELS * 16)
    attr_buf = bytearray([_FREE_ATTR] * MAX_CHANNELS)
    name_buf = bytearray(MAX_CHANNELS * 16)
    for i in range(MAX_CHANNELS):
        if i in by_index:
            entry, attr, name = _encode_channel(by_index[i])
        else:
            entry, attr, name = _FREE_ENTRY, _FREE_ATTR, _FREE_NAME
        chan_buf[i * 16:(i + 1) * 16] = entry
        attr_buf[i] = attr
        name_buf[i * 16:(i + 1) * 16] = name

    # Total blocks: chan (0x0C80/0x80=25) + attr (1, 200 bytes <= 0x80? 200 > 128, so 2) + names (0x100/0x80=2)
    chan_blocks = [(0x0000 + s, bytes(chan_buf[s:s + min(MEM_BLOCK, len(chan_buf) - s)]))
                   for s in range(0, len(chan_buf), MEM_BLOCK)]
    attr_blocks = [(0x0D60 + s, bytes(attr_buf[s:s + min(MEM_BLOCK, len(attr_buf) - s)]))
                   for s in range(0, len(attr_buf), MEM_BLOCK)]
    name_blocks = [(0x0F50 + s, bytes(name_buf[s:s + min(MEM_BLOCK, len(name_buf) - s)]))
                   for s in range(0, len(name_buf), MEM_BLOCK)]
    blocks = chan_blocks + attr_blocks + name_blocks
    total = len(blocks)
    for n, (offset, data) in enumerate(blocks, 1):
        write_eeprom(ser, offset, data, session_id)
        if progress is not None:
            progress(n, total)
