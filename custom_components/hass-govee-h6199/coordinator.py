import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import cast

from async_timeout import timeout
from bleak import BleakClient
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import BleakConnectionError, establish_connection
from govee_h6199_ble import Command, GoveeH6199
from govee_h6199_ble.commands import (
    GetBrightness,
    GetColorMode,
    GetFirmwareVersion,
    GetHardwareVersion,
    GetMacAddress,
    GetPowerState,
)
from govee_h6199_ble.const import UUID_SERVICE
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    TimestampDataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util.dt import utcnow

from .const import DOMAIN, UPDATE_INTERVAL_SEC, UPDATE_TIMEOUT_SEC
from .data import DeviceInfo, GoveeH6199Data, State

type CustomConfigEntry = ConfigEntry['Coordinator']

class Coordinator(TimestampDataUpdateCoordinator[GoveeH6199Data]):
    config_entry: CustomConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: CustomConfigEntry,
        device: BLEDevice,
    ) -> None:
        super().__init__(
            hass,
            logging.getLogger(__name__ + '@' + str(id(self))),
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SEC),
        )

        self._device = device
        self._condition = asyncio.Condition()
        self._connected_device: GoveeH6199 | None = None
        self._disconnect_task: asyncio.Task | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._is_first_data_update = True
        self.data = GoveeH6199Data(self._device.address)

    async def _connect(self):
        task_id = id(asyncio.current_task())
        self.logger.debug('[%d] connecting to %s ...', task_id, self._device.address)

        try:
            client = await establish_connection(
                BleakClient,
                self._device,
                self._device.address,
                disconnected_callback=lambda cl: self._handle_disconnect(cl, task_id),
                # micro optimisation: only request the service we need
                # in theory should speed up connection time
                services=set([UUID_SERVICE])
            )
        except BleakConnectionError:
            self.logger.debug('[%d] connection failed (connection error)', task_id)
            return

        device = GoveeH6199(client)

        async with self._ignore_disconnected():
            self.logger.debug('[%d] starting device ...', task_id)
            await device.start()

            self.logger.debug('[%d] fetching device info ...', task_id)
            mac, fw_version, hw_version = await device.send_commands([
                GetMacAddress(),
                GetFirmwareVersion(),
                GetHardwareVersion()
            ])
            async with self._condition:
                self.logger.info('[%d] connected', task_id)
                self._connected_device = device
                self.data.device_info = DeviceInfo(mac=mac, fw_version=fw_version, hw_version=hw_version)
                self._condition.notify_all()

            refresh_task = self.config_entry.async_create_background_task(self.hass, self.async_request_refresh(), 'refresh_after_connect', eager_start=False)
            refresh_task.add_done_callback(lambda t: self.logger.debug('[%d] refresh_after_connect done', id(t)))

    def _handle_disconnect(self, client: BleakClient, connect_task_id: int):
        """called every time a 'retry' is disconnected"""

        if client.is_connected:
            self.logger.debug('[%d] fake disconnect. (still connected)', connect_task_id)
            return

        if not self._disconnect_task or self._disconnect_task.done():
            disconnect_task = self.config_entry.async_create_background_task(self.hass, self._on_disconnect(connect_task_id), 'on_disconnect')
            disconnect_task.add_done_callback(lambda t: self.logger.debug('[%d] on_disconnect done', id(t)))
            self._disconnect_task = disconnect_task

    async def _on_disconnect(self, connect_task_id: int):
        task_id = id(asyncio.current_task())
        self.logger.info('[%d] disconnected %d', task_id, connect_task_id)

        async with self._condition:
            self._connected_device = None
            self._condition.notify_all()

        # Schedule reconnection (ensure we only have one reconnect task)
        if not self._reconnect_task or self._reconnect_task.done():
            def _cleanup_reconnect_task(task_id: int):
                self._reconnect_task = None
                self.logger.debug('[%d] reconnect done', task_id)

            reconnect_task = self.config_entry.async_create_task(self.hass, self._connect(), 'reconnect', eager_start=False)
            reconnect_task.add_done_callback(lambda t: _cleanup_reconnect_task(id(t)))

            self.logger.debug('[%d] reconnect task scheduled: %d', task_id, id(reconnect_task))
            self._reconnect_task = reconnect_task

        self._disconnect_task = None

    async def _ping(self):
        PING_INTERVAL_SEC = 2
        """Periodically send ping command to keep connection alive."""
        while True:
            try:
                async with timeout(PING_INTERVAL_SEC * 2 + PING_INTERVAL_SEC // 2):
                    async with self._exclusive_access() as device:
                        await device.send_command(GetPowerState())
                        self.data.last_pong = utcnow()

            except Exception:
                pass

            await asyncio.sleep(PING_INTERVAL_SEC)

    @asynccontextmanager
    async def _ignore_disconnected(self):
        try:
            yield
        except (BleakError) as err:
            if str(err) != 'disconnected':
                self.logger.debug('Unexpected error: %s', err)
                raise err

    @asynccontextmanager
    async def _exclusive_access(self):
        """Context manager for exclusive access to the device, ignoring disconnects."""
        async with self._condition:
            await self._condition.wait_for(lambda: self._connected_device is not None)
            async with self._ignore_disconnected():
                yield cast(GoveeH6199, self._connected_device)

    async def send_commands(self, commands: list[Command]):
        async with self._exclusive_access() as device:
            await device.send_commands(commands)

        await self.async_refresh()

    async def _async_setup(self) -> None:
        """
        - establish resilient Bleak connection
        - schedule "ping" task
        """

        connect_task = self.config_entry.async_create_background_task(self.hass, self._connect(), 'connect', eager_start=False)
        connect_task.add_done_callback(lambda t: self.logger.debug('[%d] connect done', id(t)))
        self.config_entry.async_create_background_task(self.hass, self._ping(), 'ping', eager_start=False)

    async def _async_update_data(self):
        task_id = id(asyncio.current_task())
        self.logger.debug('[%d] Starting data update...', task_id)
        if self._is_first_data_update:
            self._is_first_data_update = False
            return self.data

        try:
            async with timeout(UPDATE_TIMEOUT_SEC):
                self.logger.debug('[%d] Updating data...', task_id)
                async with self._exclusive_access() as device:
                    self.logger.debug('[%d] Fetching state...', task_id)
                    power = await device.send_command(GetPowerState())
                    mode = await device.send_command(GetColorMode())
                    brightness = await device.send_command(GetBrightness())

                    self.logger.debug('[%d] Fetched state: power=%s, mode=%s, brightness=%s', task_id, power, mode, brightness)
                    self.data.state = State(power, mode, None, brightness)
                    return self.data

        except (BleakError, RuntimeError) as err:
            raise UpdateFailed(f'{err!r}') from err

        # if we landed here, it means that the data was not fully updated
        # so we consider previous data, if it's "fresh enough"
        if self.last_update_success_time and (
            (utcnow() - self.last_update_success_time).total_seconds()
            < self.update_interval.total_seconds() * 2
        ):
            self.logger.info('[%d] Reusing previous data', task_id)
            return self.data

        raise UpdateFailed('Data update failed and no recent data to reuse')

