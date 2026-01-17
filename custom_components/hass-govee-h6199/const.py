from enum import StrEnum

DOMAIN = 'hass-govee-h6199'

DEVICE_NAME_PREFIX = "Govee_H6199_"
DEFAULT_SCAN_INTERVAL = 15
UPDATE_TIMEOUT = 15

BRIGHTNESS_SCALE = (1, 100)


class Effect(StrEnum):
    FILM = 'film'
    GAME = 'game'
    MUSIC = 'music'
