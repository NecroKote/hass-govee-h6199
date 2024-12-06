import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DEFAULT_SCAN_INTERVAL, DOMAIN
from .data import GoveeH6199Data
from .device import GoveeH6199Device

type CustomConfigEntry = ConfigEntry[GoveeH6199DataCoordinator]


class GoveeH6199DataCoordinator(DataUpdateCoordinator[GoveeH6199Data]):
    """Class to manage fetching Govee H6199 BLE data."""

    config_entry: CustomConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: CustomConfigEntry,
        device: GoveeH6199Device,
    ) -> None:
        """Initialize the coordinator."""

        self.device = device

        super().__init__(
            hass,
            logging.getLogger(__name__),
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )

    async def _async_setup(self) -> None:
        """Set up the coordinator."""

        await self.device.init()

    async def _async_update_data(self) -> GoveeH6199Data:
        """Get data from Airthings BLE."""

        try:
            await self.device.update()

        except Exception as err:
            raise UpdateFailed(f"Unable to fetch data: {err!r}") from err

        return self.device.data
