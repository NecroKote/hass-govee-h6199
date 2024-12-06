from enum import StrEnum

DOMAIN = "hass-govee-h6199"

DEFAULT_SCAN_INTERVAL = 15
UPDATE_TIMEOUT = 3

BRIGHTNESS_SCALE = (1, 100)


class Effect(StrEnum):
    FILM = "film"
    GAME = "game"
    MUSIC = "music"
