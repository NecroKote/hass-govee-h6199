from enum import IntEnum

DOMAIN = "hass-govee-h6199"

UUID_CONTROL_CHARACTERISTIC = "00010203-0405-0607-0809-0a0b0c0d2b11"


class PacketTypeId(IntEnum):
    POWER = 0x01
    BRIGHTNESS = 0x04
    COLOR = 0x05


class PowerValue(IntEnum):
    ON = 0x01
    OFF = 0x00


class ColorModeId(IntEnum):
    VIDEO = 0x00
    SCENE = 0x04
    SEGMENT = 0x0B
    MUSIC = 0x0C
