from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.ha_workouts.sensor import StatsSensor


async def test_stats_sensor_refreshes_after_midnight(hass, freezer):
    """The 00:00:05 refresh must run on the event loop (it rolls the week over)."""
    await hass.config.async_set_time_zone("Europe/Berlin")
    freezer.move_to(dt_util.as_utc(datetime(2026, 10, 4, 23, 59, 0, tzinfo=dt_util.get_default_time_zone())))
    coordinator = MagicMock()
    coordinator.entry.options = {}
    entry = MagicMock(entry_id="abc", title="Strava")
    sensor = StatsSensor(coordinator, entry, "strava")
    sensor.hass = hass
    sensor._async_refresh = AsyncMock()
    await sensor.async_added_to_hass()
    assert sensor._async_refresh.await_count == 1

    midnight = dt_util.start_of_local_day(dt_util.now() + timedelta(days=1)) + timedelta(seconds=5)
    freezer.move_to(midnight)
    async_fire_time_changed(hass, midnight)
    await hass.async_block_till_done()
    assert sensor._async_refresh.await_count == 2
