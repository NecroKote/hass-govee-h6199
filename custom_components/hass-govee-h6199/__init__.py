import logging

from bleak_retry_connector import close_stale_connections_by_address
from homeassistant.components import bluetooth
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .coordinator import Coordinator, CustomConfigEntry

PLATFORMS = [Platform.LIGHT]

log = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: CustomConfigEntry) -> bool:
    """Set up Govee BLE device from a config entry."""

    log.debug("Setting up config entry: %s", entry.entry_id)

    address = entry.unique_id
    assert address is not None
    await close_stale_connections_by_address(address)

    ble_device = bluetooth.async_ble_device_from_address(hass, address)
    if not ble_device:
        raise ConfigEntryNotReady(
            f"Could not find Govee H6199 device with address {address}"
        )

    coordinator = Coordinator(hass, entry, ble_device)
    entry.runtime_data = coordinator

    # won't do much in terms of data, but will start the connection process
    await coordinator.async_config_entry_first_refresh()

    log.debug("Forwarding setup to platforms for entry: %s", entry.entry_id)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    log.debug("Setup complete for config entry: %s", entry.entry_id)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CustomConfigEntry) -> bool:
    """Unload a config entry."""
    log.debug("Unloading config entry: %s", entry.entry_id)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
