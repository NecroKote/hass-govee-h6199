from bleak import BleakClient
import bleak_retry_connector

from homeassistant.components import bluetooth
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ATTR_RGB_COLOR,
    EFFECT_OFF,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    UUID_CONTROL_CHARACTERISTIC,
    ColorModeId,
    PacketTypeId,
    PowerValue,
)


class Hub:
    def __init__(self, hass: HomeAssistant, address: str) -> None:
        """Init dummy hub."""
        self.address = address
        self.unique_id = address.replace(":", "")
        self.device = bluetooth.async_ble_device_from_address(
            hass, address.upper(), False
        )

    async def connect(self) -> BleakClient:
        return await bleak_retry_connector.establish_connection(
            BleakClient, self.device, self.unique_id
        )

    async def sendCommand(
        self, cmd: int, payload: bytes | list[int], client: BleakClient | None = None
    ):
        if len(payload) > 17:
            raise ValueError("Payload too long")

        cmd = cmd & 0xFF
        payload = bytes(payload)

        frame = bytes([0x33, cmd]) + bytes(payload)
        # pad frame data to 19 bytes (plus checksum)
        frame += bytes([0] * (19 - len(frame)))

        # The checksum is calculated by XORing all data bytes
        checksum = 0
        for b in frame:
            checksum ^= b

        frame += bytes([checksum & 0xFF])

        if client is None:
            client = await self.connect()

        await client.write_gatt_char(UUID_CONTROL_CHARACTERISTIC, frame)

    async def sendMultiple(self, commands: list[tuple[int, bytes | list[int]]]):
        client = await self.connect()
        for cmd, payload in commands:
            await self.sendCommand(cmd, payload, client)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
):
    address = entry.unique_id
    assert address is not None

    hub = hass.data.setdefault(DOMAIN, {})[entry.entry_id] = Hub(hass, address=address)
    async_add_entities([GoveeH1699(hub, entry)])


class GoveeH1699(LightEntity):
    _attr_color_mode = ColorMode.RGB
    _attr_supported_color_modes = {ColorMode.ONOFF, ColorMode.RGB}
    _attr_supported_features = LightEntityFeature.EFFECT
    _attr_effect = EFFECT_OFF
    _attr_effect_list = [EFFECT_OFF, "music", "video_movie", "video_game"]

    def __init__(self, hub: Hub, entry: ConfigEntry) -> None:
        """Initialize an bluetooth light."""
        self._mac = hub.address
        self._attr_is_on = None
        self._config = entry

        # TODO: read actual state ?
        mac = self._mac.replace(":", "")
        self._attr_unique_id = f"govee_h6199_{mac}"
        self._attr_device_info = dr.DeviceInfo(
            connections={
                (
                    dr.CONNECTION_BLUETOOTH,
                    self._mac,
                )
            },
            manufacturer="Govee",
            model="H1699",
            name="Govee DreamView T1",
            sw_version="1.0",
        )

    @property
    def name(self) -> str:
        """Return the name of the switch."""
        return "DreamView T1"

    @property
    def _hub(self) -> Hub | None:
        return self.hass.data.get(DOMAIN, {}).get(self._config.entry_id)

    async def async_turn_on(self, **kwargs) -> None:
        if (hub := self._hub) is None:
            return

        commands = [(PacketTypeId.POWER, [0x1])]

        self._attr_is_on = True

        if brightness := kwargs.get(ATTR_BRIGHTNESS, 255):
            commands.append((PacketTypeId.BRIGHTNESS, [brightness]))
            self._attr_brightness = brightness

        if (effect := kwargs.get(ATTR_EFFECT)) != EFFECT_OFF:
            self._attr_color_mode = ColorMode.ONOFF
            self._attr_effect = effect

            match effect:
                case "music":
                    commands.append((PacketTypeId.COLOR, [ColorModeId.MUSIC, 0x03]))
                case "video_movie":
                    commands.append(
                        (PacketTypeId.COLOR, [ColorModeId.VIDEO, 0x01, 0x00, 0x64])
                    )
                case "video_game":
                    commands.append(
                        (PacketTypeId.COLOR, [ColorModeId.VIDEO, 0x01, 0x01, 0x64])
                    )
        else:
            self._attr_color_mode = ColorMode.RGB
            self._attr_effect = EFFECT_OFF

            if rgb := kwargs.get(ATTR_RGB_COLOR):
                red, green, blue = rgb
                self._attr_rgb_color = (red, green, blue)

                commands.append(
                    (
                        PacketTypeId.COLOR,
                        [ColorModeId.SEGMENT, red, green, blue, 0x00, 0x00, 0xFF, 0x7F],
                    )
                )

        await hub.sendMultiple(commands)

    async def async_turn_off(self, **kwargs) -> None:
        if (hub := self._hub) is None:
            return

        await hub.sendCommand(PacketTypeId.POWER, [PowerValue.OFF])
        self._attr_is_on = False
