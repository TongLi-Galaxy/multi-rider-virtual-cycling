from __future__ import annotations

from dataclasses import dataclass, field

from bleak import BleakScanner


FTMS_SERVICE_UUID = "00001826-0000-1000-8000-00805f9b34fb"
FTMS_INDOOR_BIKE_DATA_UUID = "00002ad2-0000-1000-8000-00805f9b34fb"
FTMS_CONTROL_POINT_UUID = "00002ad9-0000-1000-8000-00805f9b34fb"
CYCLING_POWER_SERVICE_UUID = "00001818-0000-1000-8000-00805f9b34fb"
CYCLING_POWER_MEASUREMENT_UUID = "00002a63-0000-1000-8000-00805f9b34fb"


def normalize_uuid(uuid: str) -> str:
    value = uuid.lower()
    if value.startswith("0x"):
        value = value[2:]
    if len(value) == 4:
        return f"0000{value}-0000-1000-8000-00805f9b34fb"
    return value


@dataclass(slots=True)
class ScannedDevice:
    name: str
    address: str
    rssi: int | None = None
    service_uuids: list[str] = field(default_factory=list)

    @property
    def supports_ftms(self) -> bool:
        services = {normalize_uuid(item) for item in self.service_uuids}
        return FTMS_SERVICE_UUID in services or FTMS_INDOOR_BIKE_DATA_UUID in services

    @property
    def supports_cycling_power(self) -> bool:
        services = {normalize_uuid(item) for item in self.service_uuids}
        return (
            CYCLING_POWER_SERVICE_UUID in services
            or CYCLING_POWER_MEASUREMENT_UUID in services
        )

    @property
    def support_label(self) -> str:
        labels: list[str] = []
        if self.supports_ftms:
            labels.append("FTMS")
        if self.supports_cycling_power:
            labels.append("Cycling Power")
        return ", ".join(labels) if labels else "未知"

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "address": self.address,
            "rssi": self.rssi,
            "service_uuids": self.service_uuids,
            "supports_ftms": self.supports_ftms,
            "supports_cycling_power": self.supports_cycling_power,
            "support_label": self.support_label,
        }


async def scan_ble_devices(timeout: float = 6.0) -> list[ScannedDevice]:
    """Scan BLE advertisements and return visible devices.

    On Windows, some devices do not advertise all services. A blank service
    list does not necessarily mean the trainer is unsupported; it only means
    those UUIDs were not visible during scanning.
    """
    devices: dict[str, ScannedDevice] = {}

    try:
        discovered = await BleakScanner.discover(timeout=timeout, return_adv=True)
        for address, pair in discovered.items():
            device, advertisement = pair
            service_uuids = [
                normalize_uuid(item)
                for item in getattr(advertisement, "service_uuids", []) or []
            ]
            devices[address] = ScannedDevice(
                name=device.name or advertisement.local_name or "Unknown",
                address=device.address,
                rssi=getattr(advertisement, "rssi", None),
                service_uuids=sorted(set(service_uuids)),
            )
    except TypeError:
        # Older bleak versions do not support return_adv.
        legacy_devices = await BleakScanner.discover(timeout=timeout)
        for device in legacy_devices:
            metadata = getattr(device, "metadata", {}) or {}
            service_uuids = [
                normalize_uuid(item) for item in metadata.get("uuids", []) or []
            ]
            devices[device.address] = ScannedDevice(
                name=device.name or "Unknown",
                address=device.address,
                rssi=getattr(device, "rssi", None),
                service_uuids=sorted(set(service_uuids)),
            )

    return sorted(
        devices.values(),
        key=lambda item: (item.rssi if item.rssi is not None else -999),
        reverse=True,
    )
