"""Aggregated statistics over the persisted activity log (week, year, months,
calendar days, streaks, personal bests, comparison for the latest activity).

Pure functions on a list of Activity records — no API calls and no recorder
access, so the stats sensor (sensor.py's StatsSensor) can recompute everything
on every coordinator update at no cost. Activity.start is local wall-clock
time for Strava/Garmin, so .date() is already the local day.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from .models import Activity, ActivityType
from .statistics_import import DISTANCE_ACTIVITY_TYPES

#: Weeks shown in the "days" calendar (including the current one).
CALENDAR_WEEKS = 26
#: Months in the "months" series (including the current one).
MONTHS = 12
#: Minimum earlier activities of the same type before the latest one is
#: compared with their average or flagged as a personal best.
MIN_HISTORY = 3
#: Number of earlier activities of the same type averaged for the comparison.
COMPARE_COUNT = 10
#: Minimum distance for the "fastest run" personal best.
FASTEST_RUN_MIN_KM = 5.0

PACE_TYPES = (ActivityType.RUNNING, ActivityType.WALKING, ActivityType.HIKING)


def _km(activity: Activity) -> float:
    return (activity.distance_meters or 0.0) / 1000


def _minutes(activity: Activity) -> float:
    return (activity.duration_seconds or 0.0) / 60


def week_start(day: date, week_start_day: int) -> date:
    """First day of the week containing `day` (Monday=0 .. Sunday=6)."""
    return day - timedelta(days=(day.weekday() - week_start_day) % 7)


def _same_day_previous_year(day: date) -> date:
    try:
        return day.replace(year=day.year - 1)
    except ValueError:  # 29 Feb -> 28 Feb
        return day.replace(year=day.year - 1, day=28)


def _totals(activities: list[Activity]) -> dict:
    """{"gesamt": {n, km, min}, "<type>": {n, km, min}, ...}; only types with n > 0."""
    groups: dict[str, list[Activity]] = defaultdict(list)
    for activity in activities:
        groups[activity.activity_type.value].append(activity)

    def summed(items: list[Activity]) -> dict:
        return {
            "n": len(items),
            "km": round(sum(_km(a) for a in items), 2),
            "min": round(sum(_minutes(a) for a in items)),
        }

    result = {"gesamt": summed(activities)}
    for activity_type in sorted(groups):
        result[activity_type] = summed(groups[activity_type])
    return result


def _in_range(activities: list[Activity], first: date, last: date) -> list[Activity]:
    return [a for a in activities if first <= a.start.date() <= last]


def _streak(week_starts: set[date], current_week: date) -> int:
    """Consecutive weeks with an activity; counts from the current week if it
    already has one, otherwise from the previous week (so a week that has just
    begun does not break the streak)."""
    week = current_week if current_week in week_starts else current_week - timedelta(weeks=1)
    count = 0
    while week in week_starts:
        count += 1
        week -= timedelta(weeks=1)
    return count


def _month_series(activities: list[Activity], today: date) -> list[dict]:
    first_month = today.replace(day=1)
    months = []
    year, month = first_month.year, first_month.month
    for _ in range(MONTHS):
        months.append((year, month))
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    months.reverse()
    index = {m: {"monat": f"{m[0]}-{m[1]:02d}", "km": {}, "min": {}} for m in months}
    for activity in activities:
        entry = index.get((activity.start.year, activity.start.month))
        if entry is None or activity.start.date() > today:
            continue
        type_ = activity.activity_type
        metric, value = ("km", _km(activity)) if type_ in DISTANCE_ACTIVITY_TYPES else (
            "min",
            _minutes(activity),
        )
        entry[metric][type_.value] = entry[metric].get(type_.value, 0.0) + value
    for entry in index.values():
        for metric in ("km", "min"):
            digits = 1 if metric == "km" else 0
            entry[metric] = {
                k: round(v, digits) if digits else round(v) for k, v in sorted(entry[metric].items())
            }
    return [index[m] for m in months]


def _calendar_days(activities: list[Activity], today: date, week_start_day: int) -> list[list]:
    first = week_start(today, week_start_day) - timedelta(weeks=CALENDAR_WEEKS - 1)
    per_day: dict[date, list[Activity]] = defaultdict(list)
    for activity in _in_range(activities, first, today):
        per_day[activity.start.date()].append(activity)
    days = []
    for day in sorted(per_day):
        items = per_day[day]
        longest = max(items, key=_minutes)
        days.append([day.isoformat(), longest.activity_type.value, round(sum(_minutes(a) for a in items))])
    return days


def _pace(activity: Activity) -> float | None:
    """Minutes per km, or None without a distance."""
    km = _km(activity)
    return _minutes(activity) / km if km > 0 else None


def _best_holders(activities: list[Activity]) -> dict[str, Activity]:
    """Activity currently holding each personal best (earliest wins a tie)."""
    runs = [a for a in activities if a.activity_type == ActivityType.RUNNING]
    fast = [a for a in runs if _km(a) >= FASTEST_RUN_MIN_KM and _pace(a)]
    rides = [a for a in activities if a.activity_type == ActivityType.CYCLING]
    climbs = [a for a in activities if (a.elevation_gain_meters or 0) > 0]
    holders: dict[str, Activity] = {}
    # max/min return the first of equal items; activities are sorted by start.
    if fast:
        holders["schnellster_lauf_5km"] = min(fast, key=lambda a: _pace(a))
    if runs:
        holders["laengster_lauf"] = max(runs, key=_km)
    if rides:
        holders["weiteste_radtour"] = max(rides, key=_km)
    if climbs:
        holders["meiste_hoehenmeter"] = max(climbs, key=lambda a: a.elevation_gain_meters)
    return holders


def _best_entry(key: str, activity: Activity) -> dict:
    entry: dict = {"datum": activity.start.date().isoformat(), "km": round(_km(activity), 2)}
    if key == "schnellster_lauf_5km":
        entry["pace"] = round(_pace(activity), 3)
    elif key == "meiste_hoehenmeter":
        entry["hm"] = round(activity.elevation_gain_meters)
        entry["typ"] = activity.activity_type.value
    return entry


def _compare_value(type_: ActivityType, items: list[Activity]) -> float | None:
    """Average over `items` in the type's own unit, weighted by distance/duration:
    min/km (running, walking, hiking), km/h (cycling), min/100 m (swimming),
    otherwise mean duration in minutes."""
    if type_ in PACE_TYPES or type_ in (ActivityType.CYCLING, ActivityType.SWIMMING):
        with_distance = [a for a in items if _km(a) > 0]
        km = sum(_km(a) for a in with_distance)
        minutes = sum(_minutes(a) for a in with_distance)
        if km <= 0 or minutes <= 0:
            return None
        if type_ in PACE_TYPES:
            return minutes / km
        if type_ == ActivityType.CYCLING:
            return km / (minutes / 60)
        return minutes / (km * 10)
    if not items:
        return None
    return sum(_minutes(a) for a in items) / len(items)


def _latest(activities: list[Activity], week_start_day: int, holders: dict[str, Activity]) -> dict:
    latest = max(activities, key=lambda a: a.start)
    type_ = latest.activity_type
    same_type = [a for a in activities if a.activity_type == type_ and a.start < latest.start]
    this_week = week_start(latest.start.date(), week_start_day)
    prev_week = this_week - timedelta(weeks=1)
    week_items = [
        a
        for a in activities
        if a.activity_type == type_
        and a.start <= latest.start
        and week_start(a.start.date(), week_start_day) == this_week
    ]
    prev_items = [
        a
        for a in activities
        if a.activity_type == type_ and week_start(a.start.date(), week_start_day) == prev_week
    ]
    result: dict = {
        "source_id": latest.source_id,
        "typ": type_.value,
        "nr_woche": len(week_items),
        "vorwoche_n": len(prev_items),
        "vorwoche_km": round(sum(_km(a) for a in prev_items), 2),
        "schnitt10": None,
        "diff": None,
        "bestwert": None,
    }
    # Distance types are compared in pace/speed, so only activities with a distance count.
    previous = [a for a in same_type if _km(a) > 0] if type_ in DISTANCE_ACTIVITY_TYPES else same_type
    mine = _compare_value(type_, [latest])
    if len(previous) >= MIN_HISTORY and mine is not None:
        average = _compare_value(type_, sorted(previous, key=lambda a: a.start)[-COMPARE_COUNT:])
        if average is not None:
            result["schnitt10"] = round(average, 2)
            delta = mine - average
            if type_ in PACE_TYPES or type_ == ActivityType.SWIMMING:
                result["diff"] = round(delta * 60)  # seconds per km / per 100 m
            elif type_ == ActivityType.CYCLING:
                result["diff"] = round(delta, 1)  # km/h
            else:
                result["diff"] = round(delta)  # minutes
    if len(same_type) >= MIN_HISTORY:
        for key, holder in holders.items():
            if holder.source_id == latest.source_id:
                result["bestwert"] = key
                break
    return result


def compute_stats(activities: list[Activity], today: date, week_start_day: int) -> dict:
    """All statistics for the stats sensor; `today` is the local date."""
    activities = sorted((a for a in activities if a.start.date() <= today), key=lambda a: a.start)
    this_week = week_start(today, week_start_day)
    prev_week = this_week - timedelta(weeks=1)
    year_start = today.replace(month=1, day=1)
    last_year_today = _same_day_previous_year(today)

    week_starts = {week_start(a.start.date(), week_start_day) for a in activities}
    run_week_starts = {
        week_start(a.start.date(), week_start_day)
        for a in activities
        if a.activity_type == ActivityType.RUNNING
    }
    holders = _best_holders(activities)

    stats = {
        "woche_start": this_week.isoformat(),
        "woche": _totals(_in_range(activities, this_week, today)),
        "vorwoche": _totals(_in_range(activities, prev_week, this_week - timedelta(days=1))),
        "jahr": _totals(_in_range(activities, year_start, today)),
        "vorjahr_bis_heute": _totals(
            _in_range(activities, year_start.replace(year=year_start.year - 1), last_year_today)
        ),
        "monate": _month_series(activities, today),
        "tage": _calendar_days(activities, today, week_start_day),
        "serie_wochen": _streak(week_starts, this_week),
        "serie_laufwochen": _streak(run_week_starts, this_week),
        "bestwerte": {key: _best_entry(key, holder) for key, holder in holders.items()},
    }
    if activities:
        stats["letzte"] = _latest(activities, week_start_day, holders)
    return stats
