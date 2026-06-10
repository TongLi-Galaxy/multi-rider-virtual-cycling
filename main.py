from __future__ import annotations

import argparse
import asyncio
import signal
import sys
import time
from pathlib import Path

from app.ble.device_client import MockTrainerDeviceClient, TrainerDeviceClient
from app.ble.scanner import scan_ble_devices
from app.utils.logger import get_app_logger


def print_scan_results(devices: list[dict]) -> None:
    if not devices:
        print("未发现 BLE 设备")
        return

    print(f"发现 {len(devices)} 个 BLE 设备:")
    for index, device in enumerate(devices, start=1):
        services = device.get("service_uuids", []) or []
        service_text = ", ".join(services) if services else "(广播未暴露服务 UUID)"
        print("-" * 88)
        print(f"{index}. 名称: {device.get('name') or 'Unknown'}")
        print(f"   地址: {device.get('address')}")
        print(f"   RSSI: {device.get('rssi')}")
        print(f"   识别: {device.get('support_label')}")
        print(f"   服务: {service_text}")


async def run_scan(timeout: float) -> None:
    devices = await scan_ble_devices(timeout)
    print_scan_results([device.to_dict() for device in devices])


async def run_read(address: str, name: str, duration: int, mock: bool = False) -> None:
    logger = get_app_logger("cycling_exam_cli")

    def on_power(slot: int, power: int, timestamp: float) -> None:
        elapsed = timestamp - started_at
        print(f"[{elapsed:6.2f}s] {slot}号 {power} W")

    def on_status(slot: int, status: str, message: str) -> None:
        print(f"[状态] {slot}号 {status} {message}")

    def on_log(message: str) -> None:
        logger.info(message)
        print(f"[日志] {message}")

    started_at = time.time()
    if mock:
        client = MockTrainerDeviceClient(1, on_power, on_status, on_log)
    else:
        client = TrainerDeviceClient(1, address, name or address, on_power, on_status, on_log)

    task = asyncio.create_task(client.run())
    try:
        await asyncio.sleep(duration)
    except KeyboardInterrupt:
        pass
    finally:
        await client.stop()
        await task


async def run_multi(addresses: list[str], duration: int, mock: bool = False) -> None:
    logger = get_app_logger("cycling_exam_cli")
    started_at = time.time()

    def on_power(slot: int, power: int, timestamp: float) -> None:
        elapsed = timestamp - started_at
        print(f"[{elapsed:6.2f}s] {slot}号 {power} W")

    def on_status(slot: int, status: str, message: str) -> None:
        print(f"[状态] {slot}号 {status} {message}")

    def on_log(message: str) -> None:
        logger.info(message)
        print(f"[日志] {message}")

    clients = []
    if mock:
        clients = [
            MockTrainerDeviceClient(slot, on_power, on_status, on_log)
            for slot in range(1, 5)
        ]
    else:
        clients = [
            TrainerDeviceClient(index, address, address, on_power, on_status, on_log)
            for index, address in enumerate(addresses[:4], start=1)
        ]

    tasks = [asyncio.create_task(client.run()) for client in clients]
    try:
        await asyncio.sleep(duration)
    except KeyboardInterrupt:
        pass
    finally:
        for client in clients:
            await client.stop()
        await asyncio.gather(*tasks, return_exceptions=True)


def launch_gui(mock: bool) -> int:
    from PySide6 import QtCore, QtWidgets

    from app.gui.main_window import MainWindow

    QtWidgets.QApplication.setHighDpiScaleFactorRoundingPolicy(
        QtCore.Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow(mock=mock)
    window.show()
    return app.exec()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="多人骑行台功率考试软件 MVP")
    parser.add_argument("--mock", action="store_true", help="启动桌面 mock 模式")

    subparsers = parser.add_subparsers(dest="command")

    scan_parser = subparsers.add_parser("scan", help="扫描附近 BLE 设备")
    scan_parser.add_argument("--timeout", type=float, default=6.0, help="扫描秒数")

    read_parser = subparsers.add_parser("read", help="连接单台设备并打印实时功率")
    read_parser.add_argument("--address", default="", help="BLE 地址或 MAC")
    read_parser.add_argument("--name", default="", help="设备名称")
    read_parser.add_argument("--duration", type=int, default=60, help="读取秒数")
    read_parser.add_argument("--mock", action="store_true", help="使用 mock 功率源")

    multi_parser = subparsers.add_parser("multi", help="并发连接最多 4 台设备")
    multi_parser.add_argument("--addresses", nargs="*", default=[], help="BLE 地址列表")
    multi_parser.add_argument("--duration", type=int, default=60, help="读取秒数")
    multi_parser.add_argument("--mock", action="store_true", help="使用 4 台 mock 功率源")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if hasattr(signal, "SIGINT"):
        signal.signal(signal.SIGINT, signal.SIG_DFL)

    if args.command == "scan":
        asyncio.run(run_scan(args.timeout))
        return 0

    if args.command == "read":
        if not args.mock and not args.address:
            parser.error("read 需要 --address，或使用 --mock")
        asyncio.run(run_read(args.address, args.name, args.duration, args.mock))
        return 0

    if args.command == "multi":
        if not args.mock and not args.addresses:
            parser.error("multi 需要 --addresses，或使用 --mock")
        asyncio.run(run_multi(args.addresses, args.duration, args.mock))
        return 0

    return launch_gui(mock=args.mock)


if __name__ == "__main__":
    raise SystemExit(main())
