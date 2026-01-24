import logging
import math
from functools import cached_property

from govee_h6199_ble import MusicColorMode, VideoColorMode
from govee_h6199_ble.commands import (
    Command,
    PowerOff,
    PowerOn,
    SetBrightness,
    SetMusicModeEnergic,
    SetStaticColor,
    SetVideoMode,
)
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ATTR_RGB_COLOR,
    EFFECT_OFF,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util.color import brightness_to_value, value_to_brightness

from .const import BRIGHTNESS_SCALE, Effect
from .coordinator import Coordinator, CustomConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CustomConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    async_add_entities([GoveeH1699(entry)])


class GoveeH1699(CoordinatorEntity[Coordinator], LightEntity):
    def __init__(
        self,
        entry: CustomConfigEntry,
    ) -> None:
        """Initialize an bluetooth light."""
        super().__init__(entry.runtime_data)

        self._log = logging.getLogger(__name__)

        btmac = self._data.address
        device_id = btmac.replace(':', '').lower()

        self._attr_unique_id = f'{device_id}_light'
        self._attr_device_info = dr.DeviceInfo(
            connections={(dr.CONNECTION_BLUETOOTH, btmac)},
            manufacturer='Govee',
            model_id='H1699',
            model='Govee DreamView T1',
        )

    @property
    def _data(self):
        return self.coordinator.data

    @property
    def _state(self):
        if data := self._data:
            return data.state

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""

        # since device info is awailable only after updated after initial setup, update it here
        if (device_info := self._data.device_info) and self._attr_device_info:
            self._attr_device_info['sw_version'] = device_info.fw_version
            self._attr_device_info['hw_version'] = device_info.hw_version

        self.async_write_ha_state()

    @cached_property
    def name(self) -> str:
        """Return the name of the light."""
        return 'Light'

    @cached_property
    def color_mode(self):
        return ColorMode.RGB

    @cached_property
    def supported_color_modes(self):
        return {ColorMode.RGB}

    @cached_property
    def supported_features(self):
        return LightEntityFeature.EFFECT

    @cached_property
    def effect_list(self):
        return [
        EFFECT_OFF,
        Effect.MUSIC,
        Effect.FILM,
        Effect.GAME,
    ]


    @property
    def brightness(self):
        if state := self._state:
            return value_to_brightness(BRIGHTNESS_SCALE, state.brightness)

    @property
    def is_on(self):
        if state := self._state:
            return state.power_state

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        if state := self._state:
            return state.color

    @property
    def effect(self) -> str | None:
        if (state := self._state) and state.mode:
            match state.mode:
                case MusicColorMode():
                    return Effect.MUSIC
                case VideoColorMode(game_mode=game_mode):
                    if game_mode:
                        return Effect.GAME
                    return Effect.FILM

        return EFFECT_OFF

    async def async_turn_on(self, **kwargs) -> None:
        on_command = PowerOnCommandBuilder()

        if raw_brightness := kwargs.get(ATTR_BRIGHTNESS):
            brightness = math.ceil(brightness_to_value(BRIGHTNESS_SCALE, raw_brightness))
            on_command.with_brightness(brightness)

        if effect := kwargs.get(ATTR_EFFECT):
            on_command.with_effect(effect)

        if rgb := kwargs.get(ATTR_RGB_COLOR):
            on_command.with_color(rgb)

        await self.coordinator.send_commands(on_command.build())

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.send_commands([PowerOff()])


class PowerOnCommandBuilder:
    def __init__(self):
        self._commands: list[Command] = [PowerOn()]

    def with_brightness(self, brightness: int):
        self._commands.append(SetBrightness(brightness))
        return self

    def with_effect(self, effect: Effect):
        match effect:
            case Effect.MUSIC:
                # TODO: read props on effects from attributes
                self._commands.append(SetMusicModeEnergic())
            case Effect.FILM:
                # TODO: read props on effects from attributes
                self._commands.append(SetVideoMode())
            case Effect.GAME:
                self._commands.append(SetVideoMode(game_mode=True))
            case _:
                # OFF means switch back to static color mode
                if (color := (self._state and self._state.color)) is None:
                    color = (248, 51, 255)

                self.with_color(color)
        return self

    def with_color(self, color: tuple[int, int, int]):
        self._commands.append(SetStaticColor(color))
        return self

    def build(self):
        return self._commands
