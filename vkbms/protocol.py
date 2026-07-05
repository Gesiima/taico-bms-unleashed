"""
V-KING / TAICO BMS protocol (PACE / Pylontech family, vendor version 0x52).

Pure, dependency-free encoding/decoding of the serial frames used by the
"BMS Tool V2.B" application. Verified against live interface captures.

Frame layout (everything ASCII; each data byte = 2 ASCII hex chars):

    ~  VER  ADR  CID1  CID2  LENGTH   INFO        CHKSUM  CR
    7E "52" "NN" "46"  "xx"  4 chars  2*n chars   4 chars 0D
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

SOI = 0x7E          # '~'
EOI = 0x0D          # CR
VER = "52"          # vendor protocol version
CID1 = "46"         # battery

# ---- Command codes (CID2) -------------------------------------------------
CID2_ANALOG          = "42"   # read live analog values
CID2_STATUS          = "44"   # read status / warning / protection flags
CID2_PARAM_READ      = "47"   # read parameter block
CID2_PARAM_WRITE     = "49"   # write parameter block
CID2_HISTORY_RECORD  = "4B"   # read history records (paged)
CID2_WARNING_RECORD  = "4C"   # read warning/alarm records (paged)
CID2_RTC_READ        = "4D"
CID2_RTC_WRITE       = "4E"
CID2_MOS_CONTROL     = "E2"   # CFET/DFET/current-limiting control
CID2_CAP_READ        = "E5"   # capacity
CID2_VERSION_READ    = "E9"   # firmware string
CID2_TOTAL_DISCHARGE = "EB"
CID2_CALIBRATION     = "ED"
CID2_POWER_OFF       = "EF"
CID2_INFO_READ       = "F1"   # production information
CID2_INFO_WRITE      = "F2"

# MOS control bit masks (within the 2-byte control word)
MOS_CFET = 0x40   # charge MOSFET open
MOS_DFET = 0x20   # discharge MOSFET open
MOS_CURRENT_LIMIT = 0x80


class ProtocolError(Exception):
    pass


# ---- low-level helpers ----------------------------------------------------
def _len_field(info_ascii_len: int) -> str:
    """Encode the PACE LENGTH field: 4-bit checksum nibble + 12-bit LENID."""
    lenid = info_ascii_len & 0x0FFF
    s = (lenid & 0xF) + ((lenid >> 4) & 0xF) + ((lenid >> 8) & 0xF)
    chk = (~(s % 16) + 1) & 0xF
    return f"{chk:X}{lenid:03X}"


def _checksum(payload: str) -> str:
    s = sum(ord(c) for c in payload)
    return f"{(~(s % 65536) + 1) & 0xFFFF:04X}"


def build_frame(cid2: str, address: int, info: str = "") -> bytes:
    """Build a complete request frame. `info` is an ASCII-hex string."""
    payload = f"{VER}{address:02X}{CID1}{cid2}{_len_field(len(info))}{info}"
    frame = "~" + payload + _checksum(payload) + "\r"
    return frame.encode("ascii")


@dataclass
class Response:
    address: int
    rtn: int            # return code, 0 = OK
    info: bytes         # decoded INFO payload (raw bytes)
    raw: bytes

    @property
    def ok(self) -> bool:
        return self.rtn == 0


def parse_frame(frame: bytes) -> Response:
    """Validate and parse a complete response frame (must end in CR)."""
    if not frame or frame[0] != SOI:
        raise ProtocolError("missing SOI")
    if frame[-1] != EOI:
        raise ProtocolError("missing EOI")
    text = frame[1:-1].decode("ascii", errors="replace")  # strip ~ and CR
    if len(text) < 16:
        raise ProtocolError("frame too short")
    body, chk = text[:-4], text[-4:]
    if _checksum(body) != chk.upper():
        raise ProtocolError("checksum mismatch")
    adr = int(body[2:4], 16)
    rtn = int(body[6:8], 16)
    info_hex = body[12:]
    try:
        info = bytes.fromhex(info_hex)
    except ValueError:
        info = b""
    return Response(address=adr, rtn=rtn, info=info, raw=frame)


# ---- request builders -----------------------------------------------------
def req_analog(address: int) -> bytes:
    return build_frame(CID2_ANALOG, address, f"{address:02X}")


def req_status(address: int) -> bytes:
    return build_frame(CID2_STATUS, address, f"{address:02X}")


def req_param_read(address: int) -> bytes:
    return build_frame(CID2_PARAM_READ, address, f"{address:02X}")


def req_record(address: int, kind: str = "history", cursor: int = 0) -> bytes:
    """cursor: 0 = first, 1 = next, 3 = stop/end."""
    cid2 = CID2_HISTORY_RECORD if kind == "history" else CID2_WARNING_RECORD
    return build_frame(cid2, address, f"{cursor:02X}{address:02X}")


def req_mos_control(address: int, mask: int) -> bytes:
    """`mask` is the full desired state (OR of MOS_* bits)."""
    return build_frame(CID2_MOS_CONTROL, address, f"{address:02X}{mask:04X}")


def req_power_off(address: int) -> bytes:
    return build_frame(CID2_POWER_OFF, address, f"{address:02X}")


# ---- value decoders -------------------------------------------------------
def _u16(b: bytes, i: int) -> int:
    return (b[i] << 8) | b[i + 1]


def _s16(b: bytes, i: int) -> int:
    v = _u16(b, i)
    return v - 0x10000 if v >= 0x8000 else v


@dataclass
class AnalogReading:
    address: int
    source: str = ""               # unique display identity (set by poller)
    pack_key: str = ""             # unique routing/MQTT key, e.g. "bms1/pack2"
    warnings: list = field(default_factory=list)      # set by poller from status
    protections: list = field(default_factory=list)   # set by poller from status
    balance_mask: int = 0          # 16-bit per-cell balancing mask (bit0 = cell 1)
    cells_mv: list[int] = field(default_factory=list)
    temps_c: list[int] = field(default_factory=list)   # CellT1..4, Env, MOS
    current_a: float = 0.0          # negative = discharge
    voltage_v: float = 0.0
    remain_ah: float = 0.0
    full_ah: float = 0.0
    cycles: int = 0
    soc: int = 0
    soh: int = 0

    @property
    def min_mv(self) -> int:
        return min(self.cells_mv) if self.cells_mv else 0

    @property
    def max_mv(self) -> int:
        return max(self.cells_mv) if self.cells_mv else 0

    @property
    def delta_mv(self) -> int:
        return self.max_mv - self.min_mv


def decode_analog(resp: Response) -> AnalogReading:
    b = resp.info
    p = 0
    p += 1                       # data flag
    ncell = b[p]; p += 1
    cells = [_u16(b, p + 2 * i) for i in range(ncell)]; p += 2 * ncell
    ntemp = b[p]; p += 1
    temps = [_u16(b, p + 2 * i) - 40 for i in range(ntemp)]; p += 2 * ntemp
    current = _s16(b, p) * 0.01; p += 2
    voltage = _u16(b, p) * 0.01; p += 2
    remain = _u16(b, p) * 0.01;  p += 2
    p += 1                       # P / reserved
    full = _u16(b, p) * 0.01;    p += 2
    cycles = _u16(b, p);         p += 2
    soc = b[p];                  p += 1
    soh = b[p];                  p += 1
    # tail (vendor extension): 16-bit balancing mask, big-endian, bit0 = cell 1
    balance = (b[p] << 8) | b[p + 1] if len(b) >= p + 2 else 0
    return AnalogReading(
        address=resp.address, cells_mv=cells, temps_c=temps,
        current_a=round(current, 2), voltage_v=round(voltage, 2),
        remain_ah=round(remain, 2), full_ah=round(full, 2),
        cycles=cycles, soc=soc, soh=soh, balance_mask=balance,
    )


@dataclass
class StatusReading:
    address: int
    cell_flags: list[int] = field(default_factory=list)
    temp_flags: list[int] = field(default_factory=list)
    state_bytes: bytes = b""

    @property
    def any_alarm(self) -> bool:
        return any(self.cell_flags) or any(self.temp_flags)

    # MOS state, verified against rest / charge / discharge captures:
    #   CFET (charge)    = state byte 35, bit 1 (0x02)
    #   DFET (discharge) = state byte 35, bit 0 (0x01)
    # (byte 34 only flags current direction: bit6 charge, bit7 discharge;
    #  current-limiting is not distinguishable in the status block.)
    @property
    def cfet_on(self) -> Optional[bool]:
        return bool(self.state_bytes[10] & 0x02) if len(self.state_bytes) > 10 else None

    @property
    def dfet_on(self) -> Optional[bool]:
        return bool(self.state_bytes[10] & 0x01) if len(self.state_bytes) > 10 else None

    # Warn-/Schutz-Flags, verifiziert gegen Mitschnitte:
    #   state byte 5 = Warnungen:  bit0 (0x01) Zelle Überspannung, bit2 (0x04) Pack Überspannung
    #   state byte 6 = Schutz:     bit0 (0x01) Zelle Überspannung
    @property
    def warnings(self) -> list[str]:
        out = []
        w = self.state_bytes[5] if len(self.state_bytes) > 5 else 0
        if w & 0x01: out.append("Zelle Überspannung")
        if w & 0x04: out.append("Pack Überspannung")
        return out

    @property
    def protections(self) -> list[str]:
        out = []
        pr = self.state_bytes[6] if len(self.state_bytes) > 6 else 0
        if pr & 0x01: out.append("Zelle Überspannung")
        return out


def decode_status(resp: Response) -> StatusReading:
    b = resp.info
    p = 0
    p += 1
    ncell = b[p]; p += 1
    cell_flags = list(b[p:p + ncell]); p += ncell
    ntemp = b[p]; p += 1
    temp_flags = list(b[p:p + ntemp]); p += ntemp
    return StatusReading(
        address=resp.address, cell_flags=cell_flags,
        temp_flags=temp_flags, state_bytes=b[p:],
    )
