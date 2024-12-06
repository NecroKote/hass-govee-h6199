from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import voluptuous as vol
from bleak import BleakClient
from bleak.backends.device import BLEDevice
from govee_h6199_ble import GetFirmwareVersion, GetMacAddress, connected
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow
from homeassistant.const import CONF_ADDRESS

from .const import DOMAIN


@dataclass
class DeviceData:
    mac: str
    fw_version: str


@dataclass
class Discovery:
    """A discovered bluetooth device."""

    name: str
    discovery_info: BluetoothServiceInfoBleak
    device: DeviceData


class GoveeConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    _discovered_device: Discovery | None
    _discovered_devices: dict[str, Discovery]

    def __init__(self) -> None:
        self._log = logging.getLogger(__name__)
        self._discovered_device = None
        self._discovered_devices = {}

    async def _get_device_info(self, device: BLEDevice) -> DeviceData:
        async with BleakClient(device) as client:
            async with connected(client) as govee:
                mac = await govee.send_command(GetMacAddress())
                firmware = await govee.send_command(GetFirmwareVersion())

        return DeviceData(mac, firmware)

    async def async_step_bluetooth(self, discovery_info: BluetoothServiceInfoBleak):
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()

        name = discovery_info.name
        self.context["title_placeholders"] = {"name": name}

        device_data = await self._get_device_info(discovery_info.device)
        self._discovered_device = Discovery(name, discovery_info, device_data)

        self._log.debug("Discovered device: %s", self._discovered_device)
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ):
        """Confirm discovery."""
        if user_input is not None:
            self._log.debug("User confirmed device: %s", user_input)
            return self.async_create_entry(
                title=self.context["title_placeholders"]["name"], data={}
            )

        self._log.debug("Context: %s", self.context)

        self._set_confirm_only()
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders=self.context["title_placeholders"],
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Handle the user step to pick discovered device."""
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()

            discovery = self._discovered_devices[address]
            self._discovered_device = discovery
            self.context["title_placeholders"] = {"name": discovery.name}

            return self.async_create_entry(title=discovery.name, data={})

        current_addresses = self._async_current_ids()
        for discovery_info in async_discovered_service_info(self.hass):
            address = discovery_info.address
            if address in current_addresses or address in self._discovered_devices:
                continue

            name = discovery_info.name
            data = await self._get_device_info(discovery_info.device)
            self._discovered_devices[address] = Discovery(name, discovery_info, data)
            self._log.debug("Discovered device: %s", self._discovered_device)

        if not self._discovered_devices:
            return self.async_abort(reason="no_devices_found")

        titles = {
            address: discovery.name
            for (address, discovery) in self._discovered_devices.items()
        }

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_ADDRESS): vol.In(titles)}),
        )
