import asyncio

import pytest
from bleak.exc import BleakError
from custom_components.hass_govee_h6199.coordinator import MAX_SOFT_FAILURES, GoveeH6199DataCoordinator
from custom_components.hass_govee_h6199.data import GoveeH6199Data
from homeassistant.helpers.update_coordinator import UpdateFailed


class FakeDevice:
    """Fake BLE device used by the coordinator in tests."""

    def __init__(self):
        self.data = GoveeH6199Data(
            address='AA',
            mac='AA',
            fw_version='1.0',
            hw_version='1',
            power_state='on',
            mode='static',
            color=(255, 0, 0),
            brightness=100,
        )
        self._fail_times = 0
        self._fail_with_ble_error = False

    async def init(self):
        # no-op for tests
        return None

    async def update(self):
        if self._fail_with_ble_error and self._fail_times > 0:
            self._fail_times -= 1
            raise BleakError('simulated BLE failure')


class FakeHass:
    """Minimal stand-in for HomeAssistant needed by the coordinator."""

    def __init__(self):
        self.tasks = []

    def async_create_task(self, coro, *, name=None):
        task = asyncio.create_task(coro, name=name)
        self.tasks.append(task)
        return task


class DummyConfigEntry:
    """Minimal stand-in for ConfigEntry for unit tests."""

    def __init__(self, entry_id: str = 'test-entry') -> None:
        self.entry_id = entry_id
        self._unload_callbacks: list[callable] = []

    def async_on_unload(self, callback):
        """Mimic HA's ConfigEntry.async_on_unload."""
        self._unload_callbacks.append(callback)
        # In real HA this returns the callback; matching that is fine
        return callback


@pytest.mark.asyncio
async def test_coordinator_keeps_stale_data_on_transient_ble_errors():
    fake_device = FakeDevice()
    hass = FakeHass()
    entry = DummyConfigEntry()
    coord = GoveeH6199DataCoordinator(hass, entry, fake_device)

    # First, one successful update to set data & reset failures
    data = await coord._async_update_data()
    assert data is fake_device.data

    # Now simulate 1 BLE failure
    fake_device._fail_with_ble_error = True
    fake_device._fail_times = 1

    data = await coord._async_update_data()
    # Should not raise, should return stale data
    assert data is fake_device.data


@pytest.mark.asyncio
async def test_coordinator_raises_after_too_many_ble_errors():
    fake_device = FakeDevice()
    hass = FakeHass()
    entry = DummyConfigEntry()
    coord = GoveeH6199DataCoordinator(hass, entry, fake_device)

    # Seed with a successful update
    await coord._async_update_data()

    fake_device._fail_with_ble_error = True
    fake_device._fail_times = MAX_SOFT_FAILURES + 1

    # Consume MAX_SOFT_FAILURES-1 failures without error
    for _ in range(MAX_SOFT_FAILURES - 1):
        data = await coord._async_update_data()
        assert data is fake_device.data

    # Next call should raise UpdateFailed
    with pytest.raises(UpdateFailed):
        await coord._async_update_data()
