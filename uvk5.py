"""UV-K5 serial protocol — based on VUURWERK/egzumer firmware source."""
import struct
import time
import serial

BAUD_RATE = 38400
MEM_BLOCK = 0x80

_XOR_KEY = bytes([0x16, 0x6C, 0x14, 0xE6, 0x2E, 0x91, 0x0D, 0x40,
                  0x21, 0x35, 0xD5, 0x40, 0x13, 0x03, 0xE9, 0x80])

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


def _send(ser: serial.Serial, cmd_id: int, payload: bytes, encrypted: bool) -> None:
    # Header: ID (LE u16) + Size (LE u16)
    body = struct.pack("<HH", cmd_id, len(payload)) + payload
    crc = _crc16(body)
    body_with_crc = body + struct.pack("<H", crc)
    if encrypted:
        body_with_crc = _xor(body_with_crc)
    # Frame: 0xABCD + size_byte + 0x00 + body_with_crc + 0xDCBA
    frame = (struct.pack(">H", 0xABCD) +
             bytes([len(body_with_crc) - 2, 0x00]) +
             body_with_crc +
             struct.pack(">H", 0xDCBA))
    ser.reset_input_buffer()
    ser.write(frame)


def _recv(ser: serial.Serial, encrypted: bool) -> tuple[int, bytes]:
    """Read one response frame, return (reply_id, data_bytes)."""
    # Header: 0xABCD (2) + size (1) + 0x00 (1)
    hdr = ser.read(4)
    if len(hdr) < 4 or hdr[0] != 0xAB or hdr[1] != 0xCD:
        raise IOError(f"Bad response header: {hdr.hex()}")
    body_len = hdr[2]
    body = ser.read(body_len)
    footer = ser.read(4)
    if len(footer) < 4 or footer[2] != 0xDC or footer[3] != 0xBA:
        raise IOError(f"Bad response footer: {footer.hex()}")
    if encrypted:
        body = _xor(body)
    # body = reply_id (u16 LE) + size (u16 LE) + data + crc (u16)
    reply_id = struct.unpack_from("<H", body, 0)[0]
    data = body[4:-2]
    return reply_id, data


def handshake(ser: serial.Serial) -> tuple[str, int]:
    """Send CMD_0514, receive REPLY_0515. Returns (firmware_version, timestamp)."""
    timestamp = int(time.time()) & 0xFFFFFFFF
    payload = struct.pack("<I", timestamp)
    _send(ser, 0x0514, payload, encrypted=True)
    reply_id, data = _recv(ser, encrypted=True)
    if reply_id != 0x0515:
        raise IOError(f"Handshake failed — got reply 0x{reply_id:04x}")
    version = data[0:16].rstrip(b"\x00\xff").decode("ascii", errors="replace")
    return version, timestamp


def read_eeprom(ser: serial.Serial, offset: int, length: int, timestamp: int) -> bytes:
    """Send CMD_051B, receive REPLY_051C."""
    payload = struct.pack("<HBBI", offset, length, 0, timestamp)
    _send(ser, 0x051B, payload, encrypted=False)
    reply_id, data = _recv(ser, encrypted=True)
    if reply_id != 0x051C:
        raise IOError(f"read_eeprom: got reply 0x{reply_id:04x}")
    return data[4:4 + length]


def read_all_channels(ser: serial.Serial, timestamp: int | None = None) -> list[dict]:
    """Read all 200 channel slots and return list of channel dicts."""
    if timestamp is None:
        _, timestamp = handshake(ser)

    chan_data = b""
    for block_start in range(0x0000, 0x0C80, MEM_BLOCK):
        block_len = min(MEM_BLOCK, 0x0C80 - block_start)
        chan_data += read_eeprom(ser, block_start, block_len, timestamp)

    attr_data = read_eeprom(ser, 0x0D60, 200, timestamp)

    name_data = b""
    for block_start in range(0x0F50, 0x1050, MEM_BLOCK):
        block_len = min(MEM_BLOCK, 0x1050 - block_start)
        name_data += read_eeprom(ser, block_start, block_len, timestamp)

    channels = []
    for i in range(200):
        entry = chan_data[i * 16:(i + 1) * 16]
        attr = attr_data[i] if i < len(attr_data) else 0xFF
        name_raw = name_data[i * 16:(i + 1) * 16] if len(name_data) >= (i + 1) * 16 else b""

        is_free = bool(attr & 0x10)
        if is_free:
            continue

        freq_raw, offset_raw = struct.unpack_from("<II", entry, 0)
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
                return f"{CTCSS_TONES[code]:.1f} Hz" if code < len(CTCSS_TONES) else "?"
            return f"D{DCS_CODES[code]:03d}N" if code < len(DCS_CODES) else "?"

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
