from datetime import date, datetime

from custom_components.ha_workouts.models import Activity, ActivityType
from custom_components.ha_workouts.stats import compute_stats

_next_id = iter(range(1, 10_000))


def act(day, type_=ActivityType.RUNNING, km=5.0, minutes=30.0, hm=None, hour=8):
    y, m, d = (int(x) for x in day.split("-"))
    return Activity(
        source="strava",
        source_id=str(next(_next_id)),
        activity_type=type_,
        start=datetime(y, m, d, hour, 0),
        duration_seconds=minutes * 60,
        distance_meters=km * 1000 if km is not None else None,
        elevation_gain_meters=hm,
    )


def test_week_boundaries_monday_and_sunday_start():
    # Sunday 2026-10-04, Monday 2026-10-05
    items = [act("2026-09-28"), act("2026-10-03"), act("2026-10-04"), act("2026-10-05")]
    mon = compute_stats(items, date(2026, 10, 5), 0)
    assert mon["woche_start"] == "2026-10-05" and mon["woche"]["gesamt"]["n"] == 1
    assert mon["vorwoche"]["gesamt"]["n"] == 3
    sun = compute_stats(items, date(2026, 10, 5), 6)
    assert sun["woche_start"] == "2026-10-04" and sun["woche"]["gesamt"]["n"] == 2
    assert sun["vorwoche"]["gesamt"]["n"] == 2


def test_week_totals_per_type_only_with_activity():
    items = [
        act("2026-09-28", km=6.67, minutes=45),
        act("2026-09-29", ActivityType.WALKING, km=4.95, minutes=62),
    ]
    st = compute_stats(items, date(2026, 10, 2), 0)
    assert st["woche"] == {
        "gesamt": {"n": 2, "km": 11.62, "min": 107},
        "running": {"n": 1, "km": 6.67, "min": 45},
        "walking": {"n": 1, "km": 4.95, "min": 62},
    }
    assert "cycling" not in st["woche"]


def test_streak_with_gap_and_fresh_week():
    # active weeks (Mon): 09-07, 09-14, 09-21 — gap at 08-31 — 08-24
    items = [act("2026-09-08"), act("2026-09-16"), act("2026-09-22"), act("2026-08-25")]
    # current week 09-28 has no activity yet: counts from the previous week
    assert compute_stats(items, date(2026, 9, 29), 0)["serie_wochen"] == 3
    # current week active: counts it too
    items.append(act("2026-09-29"))
    assert compute_stats(items, date(2026, 9, 29), 0)["serie_wochen"] == 4
    # two weeks without activity breaks the streak
    assert compute_stats(items, date(2026, 10, 13), 0)["serie_wochen"] == 0


def test_run_streak_ignores_other_types():
    items = [act("2026-09-22"), act("2026-09-15", ActivityType.WALKING), act("2026-09-29")]
    st = compute_stats(items, date(2026, 9, 30), 0)
    assert st["serie_laufwochen"] == 2 and st["serie_wochen"] == 3


def test_previous_year_to_date_across_leap_year():
    items = [
        act("2024-02-29", km=10),
        act("2024-03-01", km=1),
        act("2025-02-28", km=3),
        act("2025-03-01", km=2),
    ]
    st = compute_stats(items, date(2025, 3, 1), 0)
    assert st["jahr"]["running"]["km"] == 5
    assert st["vorjahr_bis_heute"]["running"]["km"] == 11  # includes 29 Feb 2024
    # 29 Feb today compares against 28 Feb of the previous year
    st = compute_stats([act("2027-02-28", km=7), act("2027-03-01", km=1)], date(2028, 2, 29), 0)
    assert st["vorjahr_bis_heute"]["running"]["km"] == 7


def test_personal_bests():
    items = [
        act("2026-01-01", km=4.0, minutes=20),  # fast, but under 5 km
        act("2026-01-02", km=5.0, minutes=35),
        act("2026-01-03", km=10.0, minutes=60),  # 6:00/km, longest
        act("2026-01-04", km=6.0, minutes=38),
        act("2026-02-01", ActivityType.CYCLING, km=40, minutes=100, hm=300),
        act("2026-02-02", ActivityType.CYCLING, km=62, minutes=170, hm=500),
        act("2026-03-01", ActivityType.HIKING, km=15, minutes=300, hm=1100),
    ]
    best = compute_stats(items, date(2026, 3, 2), 0)["bestwerte"]
    assert best["schnellster_lauf_5km"] == {"datum": "2026-01-03", "km": 10.0, "pace": 6.0}
    assert best["laengster_lauf"] == {"datum": "2026-01-03", "km": 10.0}
    assert best["weiteste_radtour"] == {"datum": "2026-02-02", "km": 62.0}
    assert best["meiste_hoehenmeter"] == {"datum": "2026-03-01", "km": 15.0, "hm": 1100, "typ": "hiking"}


def test_latest_with_history_and_best():
    items = [act(f"2026-09-{d:02d}", km=5, minutes=35) for d in (1, 3, 5, 8, 10)]
    items.append(act("2026-09-22", km=6, minutes=36))  # previous week
    items.append(act("2026-09-28", km=2, minutes=14))  # earlier this week
    items.append(act("2026-10-01", km=10, minutes=57))  # latest, 5:42/km vs avg 7:00..
    st = compute_stats(items, date(2026, 10, 2), 0)
    la = st["letzte"]
    assert la["typ"] == "running" and la["nr_woche"] == 2
    assert la["vorwoche_n"] == 1 and la["vorwoche_km"] == 6.0
    assert la["schnitt10"] is not None and la["diff"] < 0  # faster than average (s/km)
    assert la["bestwert"] == "schnellster_lauf_5km"


def test_latest_with_few_predecessors_has_no_comparison():
    items = [act("2026-09-01"), act("2026-09-02"), act("2026-10-01", km=20, minutes=100)]
    la = compute_stats(items, date(2026, 10, 2), 0)["letzte"]
    assert la["schnitt10"] is None and la["diff"] is None and la["bestwert"] is None


def test_latest_without_distance_uses_duration():
    items = [act(f"2026-09-{d:02d}", ActivityType.STRENGTH_TRAINING, km=None, minutes=60) for d in (1, 3, 5)]
    items.append(act("2026-10-01", ActivityType.STRENGTH_TRAINING, km=None, minutes=45))
    st = compute_stats(items, date(2026, 10, 2), 0)
    assert st["letzte"]["schnitt10"] == 60.0 and st["letzte"]["diff"] == -15
    assert st["woche"]["strength_training"] == {"n": 1, "km": 0.0, "min": 45}
    last_month = st["monate"][-1]
    assert last_month["monat"] == "2026-10" and last_month["min"] == {"strength_training": 45}
    assert last_month["km"] == {}


def test_cycling_compares_in_kmh():
    items = [act(f"2026-09-{d:02d}", ActivityType.CYCLING, km=20, minutes=60) for d in (1, 3, 5)]
    items.append(act("2026-10-01", ActivityType.CYCLING, km=25.3, minutes=60))
    la = compute_stats(items, date(2026, 10, 2), 0)["letzte"]
    assert la["schnitt10"] == 20.0 and la["diff"] == 5.3


def test_months_and_days_series():
    items = [
        act("2025-10-15", km=5),  # 12 months back from Oct 2026 starts at 2025-11 -> excluded
        act("2025-11-03", km=6),
        act("2026-10-01", km=3, minutes=20),
        act("2026-10-01", ActivityType.WALKING, km=2, minutes=40, hour=18),
    ]
    st = compute_stats(items, date(2026, 10, 2), 0)
    months = st["monate"]
    assert len(months) == 12 and months[0]["monat"] == "2025-11" and months[-1]["monat"] == "2026-10"
    assert months[0]["km"] == {"running": 6.0}
    assert months[-1]["km"] == {"running": 3.0, "walking": 2.0}
    # 26 weeks back from the week of 2026-09-28 is 2026-04-06: older days are left out
    assert st["tage"] == [["2026-10-01", "walking", 60]]
