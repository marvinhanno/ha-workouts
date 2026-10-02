import asyncio
from datetime import date, datetime, timedelta
from unittest.mock import patch
import pytest

from custom_components.ha_workouts import activity_log, statistics_import as si
from custom_components.ha_workouts.backfill_progress import BackfillProgress
from custom_components.ha_workouts.models import Activity, ActivityType
from custom_components.ha_workouts.sources.base import WorkoutSource
from custom_components.ha_workouts.sources.strava import _map_activity_type

DAYS = [date(2021, 8, 1), date(2022, 5, 10), date(2022, 6, 1), date(2024, 3, 17), date(2026, 9, 28)]

class FakeSource(WorkoutSource):
    key = "strava"
    backfill_chunk_pause_seconds = 0
    def __init__(self, supports=True):
        self.calls = []; self.supports = supports
    async def async_authenticate(self): pass
    async def async_fetch(self, d): raise NotImplementedError
    async def async_fetch_activities_range(self, s, e):
        self.calls.append(("range", s, e))
        return [Activity("strava", str(d), ActivityType.HIKING, datetime.combine(d, datetime.min.time()).replace(hour=9), 3600, 10000.0, summary_polyline="abc") for d in DAYS if s <= d <= e]
    async def async_latest_activity_day_before(self, day):
        if not self.supports: raise NotImplementedError
        self.calls.append(("before", day))
        older = [d for d in DAYS if d < day]
        return max(older) if older else None
    @classmethod
    def config_schema_fields(cls): return {}

async def run(hass, src):
    p = BackfillProgress(); seen = []
    p.add_listener(lambda: seen.append(p.state)); p.add_listener(lambda: None)
    await si.async_backfill_activity_statistics(hass, "strava", src, 0, p, asyncio.Lock())
    return p, seen

async def test_jumps_gap(recorder_mock, hass):
    src = FakeSource()
    p, seen = await run(hass, src)
    assert p.state == "complete", p.error
    acts = await activity_log.async_load_activities(hass, "strava")
    assert sorted(a.start.date() for a in acts.values()) == DAYS
    assert await si.async_get_earliest_known_activity_day(hass, "strava") <= DAYS[0]
    assert "running" in seen and seen[-1] == "complete"   # listener fired

    assert len(src.calls) < 15

async def test_stale_cache_rescanned(recorder_mock, hass):
    await si.async_set_earliest_known_activity_day(hass, "strava", date(2023, 4, 21))
    src = FakeSource()
    p, _ = await run(hass, src)
    assert p.state == "complete", p.error
    acts = await activity_log.async_load_activities(hass, "strava")
    assert len(acts) == len(DAYS)

async def test_valid_cache_skips(recorder_mock, hass):
    await run(hass, FakeSource())
    src = FakeSource()
    p, _ = await run(hass, src)
    assert p.state == "complete"
    assert [c[0] for c in src.calls] == ["before"]  # only the one validation lookup

async def test_unsupported_source_keeps_old_heuristic(recorder_mock, hass):
    src = FakeSource(supports=False)
    p, _ = await run(hass, src)
    acts = await activity_log.async_load_activities(hass, "strava")
    # old heuristic: 3 empty chunks before 2026-09-28 → stops, older not reached
    assert sorted(a.start.date() for a in acts.values()) == DAYS[4:]

def test_ebike():
    assert _map_activity_type("EBikeRide") == ActivityType.CYCLING
    assert _map_activity_type("EMountainBikeRide") == ActivityType.CYCLING
