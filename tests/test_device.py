import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest
from bleak.backends.device import BLEDevice
from bleak.exc import BleakDBusError, BleakError
from custom_components.hass_govee_h6199.device import GoveeH6199Device


class FakeGoveeDevice:
    """Fake GoveeH6199 for testing the retry logic."""

    def __init__(self) -> None:
        self.calls: list[Any] = []
        self.fail_first: bool = False
        self.fail_kind: str | None = None
        self.response: Any = 'ok'

    async def start(self) -> None:
        # matches real API used in _connect/init, but no-op
        return None

    async def send_command(self, cmd: Any) -> Any:
        self.calls.append(cmd)
        if self.fail_first:
            self.fail_first = False
            if self.fail_kind == 'dbus':
                raise BleakDBusError('org.bluez.Error.Failed', 'ATT 0x0e')
            if self.fail_kind == 'bleak':
                raise BleakError('generic bleak error')
        return self.response


class FakeClient:
    """Fake BleakClient, only what we need for _force_reconnect."""

    def __init__(self, address: str = 'AA:BB:CC:DD:EE:FF') -> None:
        self.address = address
        self.disconnected = False

    async def disconnect(self) -> None:
        self.disconnected = True


@pytest.mark.asyncio
async def test_send_command_retries_on_bleak_dbus_error(monkeypatch: pytest.MonkeyPatch):
    ble = BLEDevice(address='AA:BB:CC:DD:EE:FF', name='test', details=None)
    device = GoveeH6199Device('AA:BB:CC:DD:EE:FF', ble)

    fake_govee = FakeGoveeDevice()
    device._govee_device = fake_govee
    device._client = FakeClient()

    # Pretend we're already connected & initialized
    device._connected_event.set()
    device._init_event.set()

    # Fake reconnect: don't touch real BLE, just keep events set
    async def fake_force_reconnect():
        # In real code we'd drop client & reconnect; here it's a no-op
        device._connected_event.set()
        device._init_event.set()

    async def fake_retry_command_once(command: Any):
        # Just call the fake device one more time
        return await fake_govee.send_command(command)

    # Patch instance methods so _send_command uses our fakes
    monkeypatch.setattr(device, '_force_reconnect', fake_force_reconnect)
    monkeypatch.setattr(device, '_retry_command_once', fake_retry_command_once)

    # First call fails with BleakDBusError, second succeeds
    fake_govee.fail_first = True
    fake_govee.fail_kind = 'dbus'
    fake_govee.response = 'ok-after-retry'

    cmd = object()
    result = await device._send_command(cmd)

    assert result == 'ok-after-retry'
    # We should have called send_command twice: initial + retry
    assert len(fake_govee.calls) == 2


@pytest.mark.asyncio
async def test_send_command_retries_on_bleak_error(monkeypatch: pytest.MonkeyPatch):
    ble = BLEDevice(address='AA:BB:CC:DD:EE:FF', name='test', details=None)
    device = GoveeH6199Device('AA:BB:CC:DD:EE:FF', ble)

    fake_govee = FakeGoveeDevice()
    device._govee_device = fake_govee
    device._client = FakeClient()

    device._connected_event.set()
    device._init_event.set()

    async def fake_force_reconnect():
        device._connected_event.set()
        device._init_event.set()

    async def fake_retry_command_once(command: Any):
        return await fake_govee.send_command(command)

    monkeypatch.setattr(device, '_force_reconnect', fake_force_reconnect)
    monkeypatch.setattr(device, '_retry_command_once', fake_retry_command_once)

    fake_govee.fail_first = True
    fake_govee.fail_kind = 'bleak'
    fake_govee.response = 'ok-after-retry'

    cmd = object()
    result = await device._send_command(cmd)

    assert result == 'ok-after-retry'
    assert len(fake_govee.calls) == 2


@pytest.mark.asyncio
async def test_ensure_reconnect_task_creates_only_one(monkeypatch: pytest.MonkeyPatch):
    ble = BLEDevice(address='AA:BB:CC:DD:EE:FF', name='test', details=None)
    dev = GoveeH6199Device('AA:BB:CC:DD:EE:FF', ble)

    async def dummy_connect():
        await asyncio.sleep(0)  # just yield once

    # Patch method on this instance
    monkeypatch.setattr(dev, '_connect', dummy_connect)

    dev._ensure_reconnect_task()
    first = dev._reconnect_task

    # Second call should *not* replace the existing task
    dev._ensure_reconnect_task()
    second = dev._reconnect_task

    assert first is second
    assert first is not None


@pytest.mark.asyncio
async def test_handle_disconnect_stops_ping_and_reconnects(monkeypatch: pytest.MonkeyPatch):
    ble = BLEDevice(address='AA:BB:CC:DD:EE:FF', name='test', details=None)
    dev = GoveeH6199Device('AA:BB:CC:DD:EE:FF', ble)

    dev._connected_event.set()

    # fake ping task
    dev._ping_task = asyncio.create_task(asyncio.sleep(60))

    ensure_reconnect = MagicMock()
    monkeypatch.setattr(dev, '_ensure_reconnect_task', ensure_reconnect)

    class FakeClient:
        address = ble.address

    dev._handle_disconnect(FakeClient())

    assert not dev._connected_event.is_set()
    assert dev._ping_task is None or dev._ping_task.cancelled()
    ensure_reconnect.assert_called_once()


@pytest.mark.asyncio
async def test_connect_clears_reconnect_task_on_exit(monkeypatch: pytest.MonkeyPatch):
    ble = BLEDevice(address='AA:BB:CC:DD:EE:FF', name='test', details=None)
    dev = GoveeH6199Device('AA:BB:CC:DD:EE:FF', ble)

    # 1) Fake connection so it always "succeeds"
    async def fake_establish_connection(*args, **kwargs):
        class FakeClient:
            def __init__(self, address: str) -> None:
                self.address = address

        return FakeClient(ble.address)

    import custom_components.hass_govee_h6199.device as device_module  # noqa: PLC0415

    monkeypatch.setattr(device_module, 'establish_connection', fake_establish_connection)

    # 2) Fake Govee device so __init__/start() never blow up
    fake_govee = FakeGoveeDevice()
    monkeypatch.setattr(device_module, 'GoveeH6199', MagicMock(return_value=fake_govee))

    # 3) Prevent real ping loop from running forever
    async def dummy_ping_loop():
        await asyncio.sleep(0)

    monkeypatch.setattr(dev, '_ping_loop', dummy_ping_loop)

    # simulate that _connect is running as reconnect task
    dev._reconnect_task = asyncio.create_task(dev._connect())

    # Make sure we don't hang forever if something goes wrong
    await asyncio.wait_for(dev._reconnect_task, timeout=1)

    # After _connect exits, the finally block should have cleared the reference
    assert dev._reconnect_task is None
