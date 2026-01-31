from enum import StrEnum

DOMAIN = "hass-govee-h6199"

DEVICE_NAME_PREFIX = "Govee_H6199_"
UPDATE_INTERVAL_SEC = 30
UPDATE_TIMEOUT_SEC = 15
RECONNECT_DELAY_SEC = 3

BRIGHTNESS_SCALE = (1, 100)


class Effect(StrEnum):
    FILM = "film"
    GAME = "game"
    MUSIC = "music"
