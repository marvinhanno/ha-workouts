from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
import pytest
from homeassistant.util import dt as dt_util

from custom_components.ha_workouts.sources import strava
from custom_components.ha_workouts.sources.strava import StravaSource, _local_day_bounds
from custom_components.ha_workouts import activity_log, routes
from custom_components.ha_workouts.models import ActivityType

ITEM = {
    "id": 42, "sport_type": "Run", "name": "Nachtlauf",
    "start_date": "2026-10-01T23:30:00Z", "start_date_local": "2026-10-02T01:30:00Z",
    "moving_time": 1800, "distance": 5000.0, "average_heartrate": 150.4,
    "total_elevation_gain": 12.0, "map": {"summary_polyline": "_p~iF~ps|U_ulLnnqC_mqNvxq`@"},
}

async def test_bounds_and_parse(hass):
    await hass.config.async_set_time_zone("Europe/Berlin")
    s, e = _local_day_bounds(date(2026, 10, 2), date(2026, 10, 2))
    assert s.isoformat() == "2026-10-02T00:00:00+02:00"
    assert e.isoformat() == "2026-10-02T23:59:59+02:00"
    # the 01:30-local activity (23:30 UTC the day before) falls into "today"
    utc_start = datetime.fromisoformat("2026-10-01T23:30:00+00:00")
    assert s <= utc_start <= e
    src = StravaSource(MagicMock())
    a = src._parse_activity(ITEM, calories=300)
    assert a.start == datetime(2026, 10, 2, 1, 30) and a.start.tzinfo is None
    assert a.activity_type == ActivityType.RUNNING
    assert a.summary_polyline == ITEM["map"]["summary_polyline"]
    assert dt_util.as_local(a.start).isoformat() == "2026-10-02T01:30:00+02:00"

async def test_fetch_passes_local_window(hass):
    await hass.config.async_set_time_zone("Europe/Berlin")
    src = StravaSource(MagicMock())
    calls = []
    async def fake_request(method, path, params=None):
        calls.append((path, params))
        if path == "/athlete/activities":
            return [ITEM] if params["page"] == 1 else []
        return {"calories": 321}
    src._request = fake_request
    data = await src.async_fetch(date(2026, 10, 2))
    p = calls[0][1]
    assert p["after"] == int(datetime.fromisoformat("2026-10-02T00:00:00+02:00").timestamp())
    assert data.activities[0].calories == 321

async def test_routes_store_and_latest(hass):
    src = StravaSource(MagicMock())
    old = src._parse_activity({**ITEM, "id": 1, "start_date_local": "2026-09-01T08:00:00Z", "map": {"summary_polyline": ""}}, None)
    new = src._parse_activity(ITEM, None)
    await activity_log.async_record_activities(hass, "strava", [old, new])
    latest = await activity_log.async_get_latest_activity(hass, "strava")
    assert latest.source_id == "42"
    assert latest.summary_polyline is None  # not stored in the log
    raw = await activity_log._store(hass, "strava").async_load()
    assert all("summary_polyline" not in r for r in raw)
    assert await routes.async_get_route(hass, "strava", "42") == ITEM["map"]["summary_polyline"]
    assert await routes.async_get_route(hass, "strava", "1") is None
    await activity_log.async_wipe_activity_log(hass, "strava")
    assert await routes.async_get_route(hass, "strava", "42") is None
