"""Pure helpers for Moj Elektro 15-minute readings (no Home Assistant imports)."""

from datetime import datetime, timedelta, timezone

INTERVAL = timedelta(minutes=15)
HOUR = timedelta(hours=1)


def parse(blocks, reading_type):
    """Return {interval start (UTC): kWh} for one reading type.

    Moj Elektro timestamps mark the end of each 15-minute interval.
    """
    out = {}
    for block in blocks:
        if block.get("readingType") != reading_type:
            continue
        for reading in block.get("intervalReadings", []):
            end = datetime.fromisoformat(reading["timestamp"])
            out[(end - INTERVAL).astimezone(timezone.utc)] = float(reading["value"])
    return out


def hourly(readings, start, settled_before):
    """Group readings into whole UTC hours from `start`, as [(hour, [kWh, ...])].

    Stops at the first hour without all four readings, so it is retried on
    the next run, unless that hour is older than `settled_before`: Moj Elektro
    is not expected to fill it in any more, so it is kept as it is.
    """
    if not readings:
        return []
    first = min(readings).replace(minute=0)
    hour = max(start, first)
    out = []
    while hour <= max(readings):
        values = [readings[t] for t in (hour + i * INTERVAL for i in range(4)) if t in readings]
        if len(values) < 4 and hour >= settled_before:
            break
        out.append((hour, values))
        hour += HOUR
    return out


def profile(readings, since):
    """Return (since, [kWh or None per 15 minutes]) up to the newest reading."""
    if not readings:
        return since, []
    slots = int((max(readings) - since) / INTERVAL) + 1
    return since, [readings.get(since + i * INTERVAL) for i in range(max(slots, 0))]


def contracted_power(periods, calculate_tariff, first, last, tz):
    """Return [(UTC hour, kW)] from `first` to `last`: the contracted power of the hour's time block.

    `periods` are the API's 'dogovorjeneMoci'; `calculate_tariff` maps an
    interval end timestamp to its block (1-5). Blocks change on whole hours.
    """
    valid = [
        (datetime.fromisoformat(p["datumOd"]).date(), datetime.fromisoformat(p["datumDo"]).date(), p)
        for p in periods
        if p.get("veljavnost")
    ]
    out = []
    hour = first
    while hour <= last:
        local = hour.astimezone(tz)
        period = next((p for start, end, p in valid if start <= local.date() <= end), None)
        value = period and period.get(f"casovniBlok{calculate_tariff((local + INTERVAL).isoformat())}")
        if value is not None:
            out.append((hour, float(value)))
        hour += HOUR
    return out
