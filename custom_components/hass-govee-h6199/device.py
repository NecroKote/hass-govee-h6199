import asyncio
import logging
from dataclasses import replace
from functools import partial
from typing import Awaitable, Callable, TypeVar

from async_interrupt import interrupt
from bleak import BleakClient
from bleak.backends.device import BLEDevice
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from govee_h6199_ble import Command, GoveeH6199, connected
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

from .const import UPDATE_TIMEOUT, Effect
from .data import GoveeH6199Data

T = TypeVar("T")

type SendCommandsHandle = Callable[[list[Command]], Awaitable[None]]


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

    def __init__(
        self,
        address: str,
        device: BLEDevice,
    ) -> None:
        self.data = None  # type: ignore
        self._ble_device = device
        self.address = address
        self.logger = logging.getLogger(__name__)
        self._lock = asyncio.Lock()

    async def _connect(self, on_disconnect: asyncio.Future | None = None):
        self.logger.debug("Connecting to %s ...", self._ble_device.address)
        client = await establish_connection(
            BleakClientWithServiceCache,
            self._ble_device,
            self._ble_device.address,
            disconnected_callback=on_disconnect
            and partial(self._handle_disconnect, on_disconnect),
        )
        self.logger.debug("Connected to %s", self._ble_device.address)
        return client

    def _handle_disconnect(
        self, disconnect_future: asyncio.Future[bool], client: BleakClient
    ) -> None:
        """Handle disconnect from device."""
        self.logger.debug("Disconnected from %s", client.address)
        if not disconnect_future.done():
            disconnect_future.set_result(True)

    async def _get_device_info(self, device: GoveeH6199):
        power = await device.send_command(GetPowerState())
        mode = await device.send_command(GetColorMode())
        brightness = await device.send_command(GetBrightness())
        return power, mode, brightness

    async def init(self):
        client = await self._connect()
        async with connected(client) as device:

            mac = await device.send_command(GetMacAddress())
            fw_version = await device.send_command(GetFirmwareVersion())
            hw_version = await device.send_command(GetHardwareVersion())
            power, mode, br = await self._get_device_info(device)

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
        self.logger.debug("Initial data: %s", self.data)

    async def update(self):
        if self.data is None:
            await self.init()
            return

        on_disconnect = asyncio.get_running_loop().create_future()
        async with self._lock:
            client = await self._connect(on_disconnect)
            async with (
                interrupt(
                    on_disconnect,
                    Exception,
                    f"Disconnected from {client.address}",
                ),
                asyncio.timeout(UPDATE_TIMEOUT),
            ):
                async with connected(client) as device:
                    power, mode, br = await self._get_device_info(device)

        self.data = replace(
            self.data,
            power_state=power,
            brightness=br,
            mode=mode,
        )
        self.logger.debug("Updated data: %s", self.data)

    async def _send_commands(self, commands: list[Command]):
        """Send commands to the device."""
        on_disconnect = asyncio.get_running_loop().create_future()

        async with self._lock:
            client = await self._connect(on_disconnect)
            async with connected(client) as device:
                self.logger.debug("Sending commands: %s", commands)
                await device.send_commands(commands)

    async def power_on(self, builder: PowerOnCommandBuilder):
        await self._send_commands(builder.build())

        if new_state := builder.predict_state():
            self.data = new_state

    async def power_off(self):
        await self._send_commands([PowerOff()])
        self.data = replace(self.data, power_state=False)
