from __future__ import annotations

from typing import Any


def _read_u16_le(data: bytes | bytearray, offset: int) -> int:
    if offset + 2 > len(data):
        raise ValueError("not enough bytes for uint16")
    return int.from_bytes(data[offset : offset + 2], byteorder="little", signed=False)


def _read_i16_le(data: bytes | bytearray, offset: int) -> int:
    if offset + 2 > len(data):
        raise ValueError("not enough bytes for int16")
    return int.from_bytes(data[offset : offset + 2], byteorder="little", signed=True)


def _skip(data: bytes | bytearray, offset: int, length: int) -> int:
    next_offset = offset + length
    if next_offset > len(data):
        raise ValueError("not enough bytes for flagged field")
    return next_offset


def parse_ftms_indoor_bike_data(data: bytearray | bytes) -> dict[str, Any] | None:
    """Parse FTMS Indoor Bike Data (UUID 0x2AD2).

    Reference: Bluetooth SIG Fitness Machine Service, Indoor Bike Data
    characteristic. The first two bytes are little-endian flags. The
    instantaneous power field is present when bit 6 is set and is encoded as a
    little-endian signed int16. Earlier fields are skipped according to flags
    before reading power.
    """
    if len(data) < 2:
        return None

    flags = _read_u16_le(data, 0)
    offset = 2

    # Bit 0 is "More Data". When it is 0, Instantaneous Speed is present.
    if not flags & 0x0001:
        offset = _skip(data, offset, 2)

    if flags & 0x0002:  # Average Speed
        offset = _skip(data, offset, 2)
    if flags & 0x0004:  # Instantaneous Cadence
        offset = _skip(data, offset, 2)
    if flags & 0x0008:  # Average Cadence
        offset = _skip(data, offset, 2)
    if flags & 0x0010:  # Total Distance, uint24
        offset = _skip(data, offset, 3)
    if flags & 0x0020:  # Resistance Level, sint16
        offset = _skip(data, offset, 2)

    result: dict[str, Any] = {
        "source": "ftms_indoor_bike_data",
        "flags": flags,
        "instantaneous_power": None,
        "power_present": bool(flags & 0x0040),
    }

    if flags & 0x0040:
        result["instantaneous_power"] = _read_i16_le(data, offset)
        offset = _skip(data, offset, 2)

    result["parsed_bytes"] = offset
    return result


def parse_cycling_power_measurement(data: bytearray | bytes) -> dict[str, Any] | None:
    """Parse Cycling Power Measurement (UUID 0x2A63).

    Reference: Bluetooth SIG Cycling Power Service, Cycling Power Measurement
    characteristic. Instantaneous Power follows the two-byte flags field and is
    encoded as little-endian signed int16.
    """
    if len(data) < 4:
        return None

    flags = _read_u16_le(data, 0)
    instantaneous_power = _read_i16_le(data, 2)
    return {
        "source": "cycling_power_measurement",
        "flags": flags,
        "instantaneous_power": instantaneous_power,
        "power_present": True,
        "parsed_bytes": 4,
    }
