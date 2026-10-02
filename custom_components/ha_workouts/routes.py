"""Persisted route polylines, keyed by Activity.source_id.

Deliberately separate from activity_log.py's store: the period-to-date
sensors re-read the whole activity log on every poll, and a few KB of
encoded polyline per activity would multiply that cost for data only the
latest-activity sensor ever reads.
"""
from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .models import Activity

_STORAGE_VERSION = 1


def _store(hass: HomeAssistant, entry_slug: str) -> Store[dict[str, str]]:
    return Store(hass, _STORAGE_VERSION, f"ha_workouts_routes_{entry_slug}")


async def async_record_routes(
    hass: HomeAssistant, entry_slug: str, activities: list[Activity]
) -> None:
    """Upsert the polylines of activities that have one; skip the write if none do."""
    new_routes = {a.source_id: a.summary_polyline for a in activities if a.summary_polyline}
    if not new_routes:
        return
    store = _store(hass, entry_slug)
    routes = await store.async_load() or {}
    routes.update(new_routes)
    await store.async_save(routes)


async def async_get_route(hass: HomeAssistant, entry_slug: str, source_id: str) -> str | None:
    routes = await _store(hass, entry_slug).async_load() or {}
    return routes.get(source_id)


async def async_wipe_routes(hass: HomeAssistant, entry_slug: str) -> None:
    """Delete all stored routes for entry_slug (see activity_log.async_wipe_activity_log)."""
    await _store(hass, entry_slug).async_remove()
