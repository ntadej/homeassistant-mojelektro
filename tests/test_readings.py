"""Tests for the 15-minute reading helpers (run: python3 -m pytest tests)."""

from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
from zoneinfo import ZoneInfo

# Load readings.py alone: the package __init__ needs Home Assistant.
_spec = importlib.util.spec_from_file_location(
    "readings", Path(__file__).parents[1] / "custom_components/mojelektro/readings.py"
)
readings = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(readings)

UTC = timezone.utc
LJ = ZoneInfo("Europe/Ljubljana")
READING_TYPE = "32.0.2.4.1.2.12.0.0.0.0.0.0.0.0.3.72.0"


def block(local_day, count, value=0.1, reading_type=READING_TYPE):
    """A Moj Elektro interval block: `count` readings from local midnight, timestamped at interval end."""
    start = datetime(local_day.year, local_day.month, local_day.day, tzinfo=LJ).astimezone(UTC)
    return {
        "readingType": reading_type,
        "intervalReadings": [
            {
                "readingQualities": [],
                "timestamp": (start + (i + 1) * readings.INTERVAL).astimezone(LJ).isoformat(),
                "value": f"{value:.4f}",
            }
            for i in range(count)
        ],
    }


def test_parse_shifts_to_interval_start_in_utc():
    parsed = readings.parse([block(datetime(2026, 11, 15), 96), block(datetime(2026, 11, 15), 96, 9, "other")], READING_TYPE)
    assert len(parsed) == 96
    assert min(parsed) == datetime(2026, 11, 14, 23, 0, tzinfo=UTC)  # 00:15 local -> 00:00 local = 23:00 UTC
    assert set(parsed.values()) == {0.1}


def test_hourly_full_day():
    parsed = readings.parse([block(datetime(2026, 11, 15), 96)], READING_TYPE)
    start = datetime(2026, 11, 14, 23, tzinfo=UTC)
    hours = readings.hourly(parsed, start, settled_before=start)
    assert len(hours) == 24
    assert all(len(v) == 4 for _, v in hours)
    assert round(sum(sum(v) for _, v in hours), 4) == 9.6


def test_hourly_summer_time_day_has_23_hours():
    parsed = readings.parse([block(datetime(2026, 3, 29), 92)], READING_TYPE)
    start = datetime(2026, 3, 28, 23, tzinfo=UTC)
    hours = readings.hourly(parsed, start, settled_before=start)
    assert len(hours) == 23
    assert hours[-1][0] == datetime(2026, 3, 29, 21, tzinfo=UTC)  # 23:00 CEST


def test_hourly_stops_at_incomplete_recent_hour():
    parsed = readings.parse([block(datetime(2026, 11, 15), 96)], READING_TYPE)
    del parsed[datetime(2026, 11, 15, 5, 30, tzinfo=UTC)]
    start = datetime(2026, 11, 14, 23, tzinfo=UTC)
    hours = readings.hourly(parsed, start, settled_before=start)
    assert [h for h, _ in hours][-1] == datetime(2026, 11, 15, 4, tzinfo=UTC)


def test_hourly_keeps_incomplete_settled_hour():
    parsed = readings.parse([block(datetime(2026, 11, 15), 96)], READING_TYPE)
    del parsed[datetime(2026, 11, 15, 5, 30, tzinfo=UTC)]
    start = datetime(2026, 11, 14, 23, tzinfo=UTC)
    hours = readings.hourly(parsed, start, settled_before=datetime(2026, 11, 16, tzinfo=UTC))
    assert len(hours) == 24
    assert dict(hours)[datetime(2026, 11, 15, 5, tzinfo=UTC)] == [0.1, 0.1, 0.1]


def test_hourly_starts_at_first_reading_not_before():
    parsed = readings.parse([block(datetime(2026, 11, 15), 96)], READING_TYPE)
    hours = readings.hourly(parsed, datetime(2024, 11, 16, tzinfo=UTC), settled_before=datetime(2026, 11, 16, tzinfo=UTC))
    assert hours[0][0] == datetime(2026, 11, 14, 23, tzinfo=UTC)
    assert readings.hourly({}, datetime(2026, 11, 14, 23, tzinfo=UTC), datetime(2026, 11, 16, tzinfo=UTC)) == []


def test_profile_marks_gaps_with_none():
    parsed = readings.parse([block(datetime(2026, 11, 15), 96)], READING_TYPE)
    del parsed[datetime(2026, 11, 15, 5, 30, tzinfo=UTC)]
    since = datetime(2026, 11, 14, 23, tzinfo=UTC)
    start, values = readings.profile(parsed, since)
    assert start == since and len(values) == 96
    assert values.count(None) == 1 and values[26] is None
    assert readings.profile({}, since) == (since, [])
    # profile window starting after the first reading drops older ones
    assert len(readings.profile(parsed, since + timedelta(hours=12))[1]) == 48
