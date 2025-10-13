import asyncio
import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from async_timeout import timeout
from bleak.exc import BleakError
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DEFAULT_SCAN_INTERVAL, DOMAIN, UPDATE_TIMEOUT
from .data import GoveeH6199Data

if TYPE_CHECKING:
    from .device import GoveeH6199Device

type CustomConfigEntry = ConfigEntry['GoveeH6199DataCoordinator']

MAX_SOFT_FAILURES = 5


class GoveeH6199DataCoordinator(DataUpdateCoordinator[GoveeH6199Data]):
    """Class to manage fetching Govee H6199 BLE data."""

    device: 'GoveeH6199Device'
    config_entry: CustomConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: CustomConfigEntry,
        device: 'GoveeH6199Device',
    ) -> None:
        self.device = device
        self._consecutive_failures = 0

        super().__init__(
            hass,
            logging.getLogger(__name__ + '@' + str(id(self))),
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )

    async def _async_setup(self) -> None:
        self.logger.debug('Setting up coordinator...')
        self.hass.async_create_task(
            self.device.init(),
            name='connect to govee device',
        )

    async def _async_update_data(self) -> GoveeH6199Data:
        self.logger.debug('Updating data...')

        try:
            # Bound how long we wait for a full update; if the BLE link is
            # too weak and we can't talk to the device within UPDATE_TIMEOUT
            # seconds, treat it as unavailable instead of hanging indefinitely.
            async with timeout(UPDATE_TIMEOUT):
                await self.device.update()

        except asyncio.TimeoutError as err:
            self._consecutive_failures += 1
            self.logger.warning(
                'Timeout updating Govee H6199 after %s consecutive failures: %s',
                self._consecutive_failures,
                err,
            )
            raise UpdateFailed('Timeout updating Govee H6199 device') from err

        except BleakError as err:
            # Transient BLE issue - keep last known state for a while
            self._consecutive_failures += 1
            self.logger.warning(
                'BLE error while updating Govee H6199 (%s/%s): %s',
                self._consecutive_failures,
                MAX_SOFT_FAILURES,
                err,
            )

            if self._consecutive_failures < MAX_SOFT_FAILURES and self.device.data:
                # Don't kill entities yet; just return stale data
                return self.device.data

            # After too many failures in a row, let HA know we are in trouble
            raise UpdateFailed(
                f'Unable to fetch data after ' f'{self._consecutive_failures} BLE failures: {err!r}'
            ) from err

        except Exception as err:
            # Non-BLE error - treat as hard failure immediately
            self._consecutive_failures += 1
            raise UpdateFailed(f'Unable to fetch data: {err!r}') from err

        else:
            # Successful update - reset failure counter
            self._consecutive_failures = 0

        return self.device.data
