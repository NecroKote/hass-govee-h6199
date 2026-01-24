from dataclasses import dataclass
from datetime import datetime

from govee_h6199_ble import Modes


@dataclass
class DeviceInfo:
    mac: str
    fw_version: str
    hw_version: str


@dataclass
class State:
    power_state: bool
    mode: Modes
    color: tuple[int, int, int] | None
    brightness: int


@dataclass
class GoveeH6199Data:
    address: str

    device_info: DeviceInfo | None = None
    state: State | None = None

    last_pong: datetime | None = None
