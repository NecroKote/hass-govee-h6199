from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from bleak import BleakClient
from bleak.exc import BleakError
from bleak_retry_connector import establish_connection
from govee_h6199_ble import GetFirmwareVersion, GetMacAddress, connected
from govee_h6199_ble.const import UUID_SERVICE
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow
from homeassistant.const import CONF_ADDRESS
from homeassistant.data_entry_flow import AbortFlow

from .const import DEVICE_NAME_PREFIX, DOMAIN

if TYPE_CHECKING:
    from bleak.backends.device import BLEDevice


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


def short_address(address: str) -> str:
    """Convert a Bluetooth address to a short address."""
    results = address.replace("-", ":").split(":")
    return f"{results[-2].upper()}{results[-1].upper()}"[-4:]


class GoveeConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    _discovered_device: Discovery | None
    _discovered_devices: dict[str, Discovery]

    def __init__(self) -> None:
        self._log = logging.getLogger(__name__)
        self._discovered_device = None
        self._discovered_devices = {}

    async def _get_device_info(self, device: BLEDevice) -> DeviceData:
        self._log.debug("_get_device_info for device: %s", device)
        client = await establish_connection(
            BleakClient,
            device,
            device.name or device.address,
            services=set([UUID_SERVICE]),
        )
        try:
            async with connected(client) as govee:
                self._log.debug("Connected to device: %s", device)
                mac = await govee.send_command(GetMacAddress())
                firmware = await govee.send_command(GetFirmwareVersion())
                self._log.debug("Got device info: mac=%s firmware=%s", mac, firmware)
        finally:
            self._log.debug("Disconnecting from device: %s", device)
            await client.disconnect()
            self._log.debug("Disconnected from device: %s", device)

        return DeviceData(mac, firmware)

    async def async_step_bluetooth(self, discovery_info: BluetoothServiceInfoBleak):
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()

        name = discovery_info.name
        self.context["title_placeholders"] = {
            "name": name,
            "address": short_address(discovery_info.address),
        }

        try:
            device_data = await self._get_device_info(discovery_info.device)
            self._discovered_device = Discovery(name, discovery_info, device_data)
        except BleakError as e:
            self._log.warning(
                "Failed to connect to device %s (%s): %s",
                name,
                discovery_info.address,
                e,
            )
            raise AbortFlow("cannot_connect")

        self._log.debug("Discovered device: %s", self._discovered_device)
        return await self.async_step_confirm()

    async def _async_create_entry_from_device(self):
        assert self._discovered_device is not None
        return self.async_create_entry(title=self._discovered_device.name, data={})

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None):
        """Confirm discovery."""

        assert self._discovered_device is not None
        if user_input is not None:
            return await self._async_create_entry_from_device()

        self._set_confirm_only()
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
            description_placeholders={
                "name": self._discovered_device.name,
            },
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

            return await self._async_create_entry_from_device()

        current_addresses = self._async_current_ids()
        discovered = list(async_discovered_service_info(self.hass))
        for discovery_info in discovered:
            address = discovery_info.address
            if address in current_addresses or address in self._discovered_devices:
                continue

            name = discovery_info.name
            if name and not name.startswith(DEVICE_NAME_PREFIX):
                continue

            try:
                data = await self._get_device_info(discovery_info.device)
                self._discovered_devices[address] = Discovery(
                    name, discovery_info, data
                )
            except BleakError as e:
                self._log.warning(
                    "Failed to connect to device %s (%s): %s", name, address, e
                )

        if not self._discovered_devices:
            raise AbortFlow("no_devices_found")

        titles = {
            address: discovery.name
            for (address, discovery) in self._discovered_devices.items()
        }

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_ADDRESS): vol.In(titles)}),
        )
