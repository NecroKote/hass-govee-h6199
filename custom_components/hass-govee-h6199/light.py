import logging
import math
from functools import cached_property

from govee_h6199_ble import MusicColorMode, VideoColorMode
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ATTR_RGB_COLOR,
    EFFECT_OFF,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util.color import brightness_to_value, value_to_brightness

from .const import BRIGHTNESS_SCALE, Effect
from .coordinator import CustomConfigEntry, GoveeH6199DataCoordinator
from .device import PowerOnCommandBuilder


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CustomConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    async_add_entities([GoveeH1699(entry)])


class GoveeH1699(CoordinatorEntity[GoveeH6199DataCoordinator], LightEntity):
    _attr_color_mode = ColorMode.RGB
    _attr_supported_color_modes = {ColorMode.RGB}
    _attr_supported_features = LightEntityFeature.EFFECT
    _attr_effect_list = [EFFECT_OFF, Effect.MUSIC, Effect.FILM, Effect.GAME]

    def __init__(
        self,
        entry: CustomConfigEntry,
    ) -> None:
        """Initialize an bluetooth light."""
        super().__init__(entry.runtime_data)

        self._log = logging.getLogger(__name__)

        btmac = self._data.address
        device_id = btmac.replace(":", "").lower()

        self._attr_unique_id = f"{device_id}_light"
        self._attr_device_info = dr.DeviceInfo(
            connections={(dr.CONNECTION_BLUETOOTH, btmac)},
            manufacturer="Govee",
            model_id="H1699",
            model="Govee DreamView T1",
            sw_version=self._data.fw_version,
            hw_version=self._data.hw_version,
        )

    @property
    def _data(self):
        return self.coordinator.data

    @cached_property
    def name(self) -> str:
        """Return the name of the light."""
        return "Light"

    @property
    def brightness(self) -> int | None:
        return value_to_brightness(BRIGHTNESS_SCALE, self._data.brightness)

    @property
    def is_on(self) -> bool | None:
        return self._data.power_state

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        return self._data.color

    @property
    def effect(self) -> str | None:
        match self._data.mode:
            case MusicColorMode():
                return Effect.MUSIC
            case VideoColorMode(game_mode=game_mode):
                if game_mode:
                    return Effect.GAME
                return Effect.FILM

        return EFFECT_OFF

    async def async_turn_on(self, **kwargs) -> None:
        on_command = PowerOnCommandBuilder(self._data)

        if raw_brightness := kwargs.get(ATTR_BRIGHTNESS):
            brightness = math.ceil(
                brightness_to_value(BRIGHTNESS_SCALE, raw_brightness)
            )
            on_command.with_brightness(brightness)

        if effect := kwargs.get(ATTR_EFFECT):
            on_command.with_effect(effect)

        if rgb := kwargs.get(ATTR_RGB_COLOR):
            on_command.with_color(rgb)

        await self.coordinator.device.power_on(on_command)
        await self.async_update()

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.device.power_off()
        await self.async_update()
