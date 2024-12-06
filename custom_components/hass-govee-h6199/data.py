from dataclasses import dataclass

from govee_h6199_ble import Modes


@dataclass
class GoveeH6199Data:
    address: str
    mac: str
    fw_version: str
    hw_version: str

    power_state: bool
    mode: Modes
    color: tuple[int, int, int] | None
    brightness: int
