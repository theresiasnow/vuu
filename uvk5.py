"""UV-K5 serial protocol — based on sq5bpf/k5prog reference implementation."""
import struct
import serial

BAUD_RATE = 38400
MEM_BLOCK = 0x80

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


DEBUG = False


def _send_cmd(ser: serial.Serial, cmd: bytes) -> None:
    """Frame and send a cleartext command. cmd = id(2) + size(2) + payload."""
    crc = _crc16(cmd)
    obfuscated = _xor(cmd + struct.pack("<H", crc))
    frame = (b"\xab\xcd" +
             struct.pack("<H", len(cmd)) +
             obfuscated +
             b"\xdc\xba")
    ser.reset_input_buffer()
    if DEBUG:
        print(f"[TX] {frame.hex()}")
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
    if DEBUG:
        print(f"[RX] hdr={hdr.hex()} body={body.hex()} ftr={footer.hex()}  -> clear={cleartext.hex()}")
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
    for block_start in range(0x0F50, 0x1050, MEM_BLOCK):
        block_len = min(MEM_BLOCK, 0x1050 - block_start)
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
