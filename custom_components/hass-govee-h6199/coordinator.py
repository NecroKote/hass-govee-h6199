import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import cast

from async_timeout import timeout
from bleak import BleakClient
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import close_stale_connections, establish_connection
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

from .const import DOMAIN, RECONNECT_DELAY_SEC, UPDATE_INTERVAL_SEC, UPDATE_TIMEOUT_SEC
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
        self._connect_task: asyncio.Task | None = None
        self._is_first_data_update = True
        self.data = GoveeH6199Data(self._device.address)

    async def _connect(self):
        task_id = id(asyncio.current_task())
        self.logger.debug('[%d] connecting to %s ...', task_id, self._device.address)

        client = await establish_connection(
            BleakClient,
            self._device,
            self._device.address,
            disconnected_callback=lambda cl: self._handle_disconnect(cl, task_id),
            # micro optimisation: only request the service we need
            # in theory should speed up connection time
            services=set([UUID_SERVICE])
        )

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

    def _handle_disconnect(self, _: BleakClient | None, connect_task_id: int):
        if not self._disconnect_task or self._disconnect_task.done():
            def cleanup(task: asyncio.Task):
                task_id = id(task)
                try:
                    exception = task.exception()
                    self.logger.debug('[%d] on_disconnect done', task_id, exc_info=exception)
                except asyncio.CancelledError:
                    self.logger.debug('[%d] on_disconnect cancelled', task_id)
                    pass

                self._disconnect_task = None

            task = self.config_entry.async_create_background_task(self.hass, self._on_disconnect(connect_task_id), 'on_disconnect')
            task.add_done_callback(cleanup)
            self._disconnect_task = task

    async def _schedule_connect(self, after_delay: float  | None = None):
        task_id = id(asyncio.current_task())

        if after_delay is not None:
            await asyncio.sleep(after_delay)

        # Schedule connection (ensure we only have one connect task)
        if self._connect_task and not self._connect_task.done():
            self.logger.debug('[%d] connect already scheduled: %d', task_id, id(self._connect_task))
            return

        def cleanup(task: asyncio.Task):
            task_id = id(task)
            try:
                exception = task.exception()
                self.logger.debug('[%d] connect done', task_id, exc_info=exception)
            except asyncio.CancelledError:
                self.logger.debug('[%d] connect cancelled', task_id)
                pass

            self._connect_task = None

            # schedule reconnect on failure
            if task.exception() is not None:
                self.logger.info('[%d] scheduling reconnect after failure', task_id)
                self.config_entry.async_create_background_task(self.hass, self._schedule_connect(after_delay=RECONNECT_DELAY_SEC), 'reconnect', eager_start=False)

        task = self.config_entry.async_create_background_task(self.hass, self._connect(), 'connect', eager_start=False)
        task.add_done_callback(cleanup)

        self.logger.debug('[%d] connect task scheduled: %d', task_id, id(task))
        self._connect_task = task

    async def _on_disconnect(self, connect_task_id: int):
        task_id = id(asyncio.current_task())
        self.logger.info('[%d] disconnected %d', task_id, connect_task_id)

        try:
            await close_stale_connections(self._device)
        except Exception:
            pass

        async with self._condition:
            self._connected_device = None
            self._condition.notify_all()

        await self._schedule_connect(after_delay=RECONNECT_DELAY_SEC)

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
                self.logger.debug('Non-disconnected error', exc_info=err)
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

        self.config_entry.async_create_background_task(self.hass, self._schedule_connect(), 'initial_connect', eager_start=False)
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

