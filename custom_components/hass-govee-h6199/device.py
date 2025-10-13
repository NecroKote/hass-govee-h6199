import asyncio
import logging
from dataclasses import replace
from typing import Any, Awaitable, Callable, cast

from bleak import BleakClient
from bleak.backends.device import BLEDevice
from bleak.exc import BleakDBusError, BleakError
from bleak_retry_connector import establish_connection
from govee_h6199_ble import Command, GoveeH6199
from govee_h6199_ble.commands import (
    GetBrightness,
    GetColorMode,
    GetFirmwareVersion,
    GetHardwareVersion,
    GetMacAddress,
    GetPowerState,
    PowerOff,
    PowerOn,
    SetBrightness,
    SetMusicModeEnergic,
    SetStaticColor,
    SetVideoMode,
)

from .const import Effect
from .data import GoveeH6199Data

SendCommandsHandle = Callable[[list[Command]], Awaitable[None]]


class PowerOnCommandBuilder:
    def __init__(self, state: GoveeH6199Data | None = None):
        self._commands: list[Command] = [PowerOn()]
        self._state = state

    def with_brightness(self, brightness: int):
        self._commands.append(SetBrightness(brightness))
        if self._state:
            self._state = replace(self._state, brightness=brightness)
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
        if self._state:
            self._state = replace(self._state, color=color)
        return self

    def build(self):
        return self._commands

    def predict_state(self):
        return self._state


class GoveeH6199Device:
    data: GoveeH6199Data
    ping_interval = 5

    on_data_listeners: list[Callable[[GoveeH6199Data], None]]

    def __init__(self, address: str, device: BLEDevice) -> None:
        self.data = None  # type: ignore
        self.on_data_listeners = []
        self._ble_device = device
        self.address = address
        self.logger = logging.getLogger(__name__ + '@' + str(id(self)))
        self._lock = asyncio.Lock()

        self._client: BleakClient | None = None
        self._govee_device: GoveeH6199 | None = None
        # _connected_event reflects the current BLE link state.
        # _init_event is set once after the initial init() succeeds and is not
        # cleared on reconnects; callers typically wait on both before using
        # the device.
        self._connected_event = asyncio.Event()
        self._init_event = asyncio.Event()
        self._ping_task: asyncio.Task | None = None
        self._reconnect_task: asyncio.Task | None = None

    def add_data_listener(self, listener: Callable[[GoveeH6199Data], None]) -> None:
        self.on_data_listeners.append(listener)

    def notify_listeners(self) -> None:
        for listener in self.on_data_listeners:
            listener(self.data)

    def set_data_and_notify_if_changed(self, new_data: GoveeH6199Data) -> None:
        if new_data != self.data:
            self.data = new_data
            self.notify_listeners()

    def _ensure_reconnect_task(self) -> None:
        """Ensure at most one reconnect task is running."""
        if self._reconnect_task and not self._reconnect_task.done():
            self.logger.debug('Reconnect task already running.')
            return

        self.logger.debug('Scheduling reconnect task...')
        self._reconnect_task = asyncio.create_task(self._connect())

    async def _connect(self):
        """Establish connection and set up Govee device."""
        self.logger.debug('Starting connection task...')
        current_task = asyncio.current_task()

        try:
            while True:
                try:
                    self.logger.debug('Connecting to %s ...', self._ble_device.address)
                    client = await establish_connection(
                        BleakClient,
                        self._ble_device,
                        self._ble_device.address,
                        disconnected_callback=self._handle_disconnect,
                    )
                    self.logger.debug('Connected to %s', self._ble_device.address)
                    self._client = client
                    if device := GoveeH6199(client):
                        self.logger.debug('Starting Govee device...')
                        await device.start()
                        self._govee_device = device

                    self._connected_event.set()

                    if existing := self._ping_task:
                        if not existing.done():
                            self.logger.debug('Ping task already running.')
                            return
                    else:
                        self.logger.debug('Starting ping task...')
                        self._ping_task = asyncio.create_task(self._ping_loop())

                    return
                except Exception as e:
                    self.logger.warning('Connection failed: %s, retrying in 2s...', e)
                    await asyncio.sleep(2)
        finally:
            # If this _connect() is the reconnect task, clear the reference
            if self._reconnect_task is current_task:
                self.logger.debug('Reconnect task finished, clearing reference.')
                self._reconnect_task = None

    def _handle_disconnect(self, client: BleakClient):
        """Handle disconnect from device."""

        if self._connected_event.is_set():
            self.logger.debug('Disconnected from %s', client.address)
            self._connected_event.clear()

            # stop ping task
            if self._ping_task and not self._ping_task.done():
                self._ping_task.cancel()
                self._ping_task = None

            # Schedule reconnection (ensure we only have one reconnect task)
            self._ensure_reconnect_task()

    async def _get_device_info(self, device: GoveeH6199):
        """Get device info using persistent connection."""
        self.logger.debug('Getting device info...')

        await self._connected_event.wait()

        async with self._lock:
            power = await device.send_command(GetPowerState())
            mode = await device.send_command(GetColorMode())
            brightness = await device.send_command(GetBrightness())

        return power, mode, brightness

    async def _ping_loop(self):
        """Background task to ping device every 5 seconds."""
        while True:
            try:
                self.logger.debug('Pinging device...')
                power = await self._send_command(GetPowerState())
                if self.data:
                    new_data = replace(self.data, power_state=power)
                    self.set_data_and_notify_if_changed(new_data)

            except Exception as e:
                self.logger.warning('Ping failed: %s', e)

            await asyncio.sleep(self.ping_interval)

    async def init(self):
        """Initialize device and start ping task (one-time bootstrap).

        This is expected to be called once; on success it sets _init_event.
        Subsequent reconnects only require _connected_event to be set.
        """
        await self._connect()

        while True:
            await self._connected_event.wait()
            try:

                self.logger.debug('Initializing device ...')

                async with self._lock:
                    device = cast('GoveeH6199', self._govee_device)

                    mac = await device.send_command(GetMacAddress())
                    self.logger.debug('Device MAC: %s', mac)
                    fw_version = await device.send_command(GetFirmwareVersion())
                    self.logger.debug('Device Firmware Version: %s', fw_version)
                    hw_version = await device.send_command(GetHardwareVersion())
                    self.logger.debug('Device Hardware Version: %s', hw_version)

                power, mode, br = await self._get_device_info(device)
            except Exception as e:
                self.logger.warning('Initialization failed: %s, retrying...', e)
                await asyncio.sleep(2)
                continue

            self.data = GoveeH6199Data(
                address=self.address,
                mac=mac,
                fw_version=fw_version,
                hw_version=hw_version,
                power_state=power,
                mode=mode,
                color=(0, 0, 0),
                brightness=br,
            )
            break

        self.logger.debug('Initial data: %s', self.data)
        self._init_event.set()

    async def update(self):
        """Update device info, waiting for connection if needed."""
        await self._connected_event.wait()
        await self._init_event.wait()

        if (device := self._govee_device) is None:
            raise RuntimeError('Govee device not initialized')

        power, mode, br = await self._get_device_info(device)

        self.data = replace(
            self.data,
            power_state=power,
            brightness=br,
            mode=mode,
        )
        self.logger.debug('Updated data: %s', self.data)

    async def _send_command(self, command: Any) -> Any:
        """Send a single command to the device using persistent connection.

        On BLE/ATT errors, force a reconnect and retry once.
        """
        # Fast path: make sure we're connected/initialized
        await self._connected_event.wait()
        await self._init_event.wait()

        if not self._govee_device:
            raise RuntimeError('Govee device not initialized')

        async with self._lock:
            try:
                self.logger.debug('Sending command: %s', command)
                return await self._govee_device.send_command(command)

            except BleakDBusError as err:
                # ATT 0x0e (Unlikely / Failed) - usually stale/bad connection
                self.logger.warning(
                    'BLE DBus error while sending %s: %s. ' 'Forcing reconnect and retrying once.',
                    command,
                    err,
                )
                await self._force_reconnect()
                return await self._retry_command_once(command)

            except BleakError as err:
                # Other Bleak-level errors
                self.logger.warning(
                    'Bleak error while sending %s: %s. ' 'Forcing reconnect and retrying once.',
                    command,
                    err,
                )
                await self._force_reconnect()
                return await self._retry_command_once(command)

    async def _retry_command_once(self, command: Any) -> Any:
        """Retry a command one time after we forced a reconnect."""
        # Wait for reconnection + (re)initialization
        await self._connected_event.wait()
        await self._init_event.wait()

        if not self._govee_device:
            raise RuntimeError('Govee device not initialized after reconnect')

        self.logger.debug('Retrying command after reconnect: %s', command)
        async with self._lock:
            return await self._govee_device.send_command(command)

    async def _force_reconnect(self):
        """Try to tear down the current client and trigger reconnect logic."""
        client = self._client
        if client is None:
            # Nothing to do, just ensure a connect task is running
            self.logger.debug('No active client, ensuring connect task.')
            self._ensure_reconnect_task()
            return

        self.logger.debug('Forcing disconnect from %s', client.address)

        # Clear flags so other callers will wait
        self._connected_event.clear()

        # Stop ping loop
        if self._ping_task and not self._ping_task.done():
            self._ping_task.cancel()
            self._ping_task = None

        # Try to disconnect quietly
        try:
            await client.disconnect()
        except Exception as err:
            self.logger.debug('Error during forced disconnect: %s', err)

        # Drop references and schedule reconnect
        self._client = None
        self._govee_device = None

        self._ensure_reconnect_task()

    async def _send_commands(self, commands: list[Command]):
        """Send commands to the device using persistent connection."""
        self.logger.debug('Sending commands: %s', commands)
        for command in commands:
            await self._send_command(command)

    async def power_on(self, builder: PowerOnCommandBuilder):
        if new_state := builder.predict_state():
            self.logger.debug('Powering on ...')
            await self._send_commands(builder.build())
            self.set_data_and_notify_if_changed(new_state)

    async def power_off(self):
        power = await self._send_command(PowerOff())
        self.set_data_and_notify_if_changed(replace(self.data, power_state=power))
