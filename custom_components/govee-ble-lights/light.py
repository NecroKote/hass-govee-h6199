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
from homeassistant.helpers import device_registry as dr

from .const import (
    DOMAIN,
    UUID_CONTROL_CHARACTERISTIC,
    ColorModeId,
    PacketTypeId,
    PowerValue,
)


async def async_setup_entry(hass, config_entry, async_add_entities):
    light = hass.data[DOMAIN][config_entry.entry_id]
    # bluetooth setup
    ble_device = bluetooth.async_ble_device_from_address(
        hass, light.address.upper(), False
    )

    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, light.address)},
        name="Govee DreamView T1",
        manufacturer="Govee",
        model="H1699",
        sw_version="1.0",
    )

    async_add_entities([GoveeH1699(light, ble_device)])


class GoveeH1699(LightEntity):
    _attr_color_mode = ColorMode.RGB
    _attr_supported_color_modes = {ColorMode.RGB}
    _attr_supported_features = LightEntityFeature.EFFECT
    _attr_effect = EFFECT_OFF
    _attr_effect_list = ["music", "video_movie", "video_game"]

    def __init__(self, light, ble_device) -> None:
        """Initialize an bluetooth light."""
        self._mac: str = light.address
        self._ble_device = ble_device
        self._state = None
        self._brightness = None

    @property
    def name(self) -> str:
        """Return the name of the switch."""
        return "DreamView T1"

    @property
    def unique_id(self) -> str:
        """Return a unique, Home Assistant friendly identifier for this entity."""
        mac = self._mac.replace(":", "")
        return f"govee_h6199_{mac}"

    @property
    def brightness(self):
        return self._brightness

    @property
    def is_on(self) -> bool | None:
        """Return true if light is on."""
        return self._state

    async def async_turn_on(self, **kwargs) -> None:
        await self._sendBluetoothData(PacketTypeId.POWER, [0x1])
        self._state = True

        if effect := kwargs.get(ATTR_EFFECT):
            self._attr_color_mode = ColorMode.ONOFF
            self._attr_effect = effect

            match effect:
                case "music":
                    await self._sendBluetoothData(
                        PacketTypeId.COLOR, [ColorModeId.MUSIC, 0x03]
                    )
                case "video_movie":
                    await self._sendBluetoothData(
                        PacketTypeId.COLOR, [ColorModeId.VIDEO, 0x01, 0x00, 0x64]
                    )
                case "video_game":
                    await self._sendBluetoothData(
                        PacketTypeId.COLOR, [ColorModeId.VIDEO, 0x01, 0x01, 0x64]
                    )
        else:
            self._attr_color_mode = ColorMode.RGB

        if brightness := kwargs.get(ATTR_BRIGHTNESS, 255):
            await self._sendBluetoothData(PacketTypeId.BRIGHTNESS, [brightness])
            self._brightness = brightness

        elif rgb := kwargs.get(ATTR_RGB_COLOR):
            red, green, blue = rgb
            await self._sendBluetoothData(
                PacketTypeId.COLOR,
                [ColorModeId.SEGMENT, red, green, blue, 0x00, 0x00, 0xFF, 0x7F],
            )

    async def async_turn_off(self, **kwargs) -> None:
        await self._sendBluetoothData(PacketTypeId.POWER, [PowerValue.OFF])
        self._state = False

    async def _connectBluetooth(self) -> BleakClient:
        client = await bleak_retry_connector.establish_connection(
            BleakClient, self._ble_device, self.unique_id
        )
        return client

    async def _sendBluetoothData(self, cmd: int, payload: bytes | list[int]):
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
        client = await self._connectBluetooth()
        await client.write_gatt_char(UUID_CONTROL_CHARACTERISTIC, frame, False)
