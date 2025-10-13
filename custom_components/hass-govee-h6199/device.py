import asyncio
import logging
from dataclasses import replace
from typing import Awaitable, Callable, TypeVar, cast

from bleak import BleakClient
from bleak.backends.device import BLEDevice
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
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

    ping_interval = 2

    def __init__(self, address: str, device: BLEDevice) -> None:
        self.data = None  # type: ignore
        self._ble_device = device
        self.address = address
        self.logger = logging.getLogger(__name__)
        self._lock = asyncio.Lock()

        self._client: BleakClient | None = None
        self._govee_device: GoveeH6199 | None = None
        self._connected_event = asyncio.Event()
        self._init_event = asyncio.Event()
        self._ping_task: asyncio.Task | None = None

    async def _connect(self):
        self.logger.debug("Starting connection task...")

        """Establish connection and set up Govee device."""
        while True:
            try:
                self.logger.debug("Connecting to %s ...", self._ble_device.address)
                client = await establish_connection(
                    BleakClient,
                    self._ble_device,
                    self._ble_device.address,
                    disconnected_callback=self._handle_disconnect,
                )
                self.logger.debug("Connected to %s", self._ble_device.address)
                self._client = client
                if device := GoveeH6199(client):
                    self.logger.debug("Starting Govee device")
                    await device.start()
                    self._govee_device = device

                self._connected_event.set()

                if existing := self._ping_task:
                    if not existing.done():
                        self.logger.debug("Ping task already running.")
                        return
                else:
                    self.logger.debug("Starting ping task...")
                    self._ping_task = asyncio.create_task(self._ping_loop())

                return
            except Exception as e:
                self.logger.warning(f"Connection failed: {e}, retrying in 2s...")
                await asyncio.sleep(2)

    def _handle_disconnect(self, client: BleakClient):
        """Handle disconnect from device."""

        if self._connected_event.is_set():
            self.logger.debug("Disconnected from %s", client.address)
            self._connected_event.clear()
            self._init_event.clear()

            # stop ping task
            if self._ping_task and not self._ping_task.done():
                self._ping_task.cancel()
                self._ping_task = None

            # Schedule reconnection
            asyncio.create_task(self._connect())

    async def _get_device_info(self, device: GoveeH6199):
        """Get device info using persistent connection."""
        self.logger.debug("Getting device info...")

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
                self.logger.debug("Pinging device...")
                power = await self._send_commands([GetPowerState()])
                if self.data:
                    self.data = replace(self.data, power_state=power)

            except Exception as e:
                self.logger.warning(f"Ping failed: {e}")

            await asyncio.sleep(self.ping_interval)

    async def init(self):
        """Initialize device and start ping task."""
        await self._connect()

        while True:
            await self._connected_event.wait()
            try:

                self.logger.debug(
                    f"Initializing device: {self._govee_device!r}",
                )

                async with self._lock:
                    device = cast(GoveeH6199, self._govee_device)

                    mac = await device.send_command(GetMacAddress())
                    self.logger.debug("Device MAC: %s", mac)
                    fw_version = await device.send_command(GetFirmwareVersion())
                    self.logger.debug("Device Firmware Version: %s", fw_version)
                    hw_version = await device.send_command(GetHardwareVersion())
                    self.logger.debug("Device Hardware Version: %s", hw_version)

                power, mode, br = await self._get_device_info(device)
            except Exception as e:
                self.logger.warning(f"Initialization failed: {e}, retrying...")
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

        self.logger.debug("Initial data: %s", self.data)
        self._init_event.set()

    async def update(self):
        """Update device info, waiting for connection if needed."""
        await self._connected_event.wait()
        await self._init_event.wait()

        power, mode, br = await self._get_device_info(self._govee_device)

        self.data = replace(
            self.data,
            power_state=power,
            brightness=br,
            mode=mode,
        )
        self.logger.debug("Updated data: %s", self.data)

    async def _send_commands(self, commands: list[Command]):
        """Send commands to the device using persistent connection."""
        await self._connected_event.wait()
        await self._init_event.wait()

        if device := self._govee_device:
            async with self._lock:
                self.logger.debug("Sending commands: %s", commands)
                await device.send_commands(commands)

    async def power_on(self, builder: PowerOnCommandBuilder):
        if new_state := builder.predict_state():
            old_state = self.data
            self.data = new_state

            self.logger.debug("Powering on ...")
            try:
                await self._send_commands(builder.build())
            except:
                self.data = old_state
                raise

    async def power_off(self):
        self.data = replace(self.data, power_state=False)
        await self._send_commands([PowerOff()])
