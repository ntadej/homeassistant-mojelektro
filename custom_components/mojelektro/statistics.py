"""Import Moj Elektro 15-minute readings into long-term statistics."""

import asyncio
from datetime import date, timedelta
from functools import partial
import logging
import re

import aiohttp

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.const import UnitOfEnergy, UnitOfPower
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util

from .const import DOMAIN, READING_TYPE_15MIN_IMPORT, READING_TYPE_15MIN_EXPORT
from .readings import INTERVAL, contracted_power, parse, hourly, profile, split

_LOGGER = logging.getLogger(__name__)

# Days re-imported on every run, so late or corrected readings replace old ones.
REWRITE_DAYS = 7
# API limits: one request spans at most 35 days and starts at most 3 years back
# (its error message says 2 years, but 3 are served).
MAX_SPAN_DAYS = 35
MAX_AGE_YEARS = 3
# Energy statistics with a running sum; a missing one makes the next run back-fill all.
ENERGY_KINDS = ("consumption", "return", "consumption_vt", "consumption_mt")
# Contracted power statistics start here (time blocks are billed from October 2024).
CONTRACT_START = date(2024, 1, 1)


def statistic_id(meter_id, kind):
    """Return the external statistic ID for a meter, e.g. mojelektro:6_18246_consumption."""
    return f"{DOMAIN}:{re.sub(r'[^a-z0-9]+', '_', meter_id.lower()).strip('_')}_{kind}"


def years_back(day, years):
    """The same date `years` earlier (Feb 29 becomes Feb 28)."""
    try:
        return day.replace(year=day.year - years)
    except ValueError:
        return day.replace(year=day.year - years, day=28)


async def async_import_statistics(hass, api, meter_id):
    """Fetch new 15-minute readings, import hourly statistics, return the recent profile."""
    today = dt_util.now().date()
    settled_before = dt_util.as_utc(dt_util.start_of_local_day(today - timedelta(days=REWRITE_DAYS)))

    # Continue from the series that is furthest behind; a new series (e.g. added
    # by an update) back-fills everything, rewriting the others unchanged.
    last_days = [await _async_last_day(hass, statistic_id(meter_id, kind)) for kind in ENERGY_KINDS]
    if all(last_days):
        fetch_from = min(*last_days, today - timedelta(days=REWRITE_DAYS))
    else:
        fetch_from = years_back(today, MAX_AGE_YEARS)
    start = dt_util.as_utc(dt_util.start_of_local_day(fetch_from))

    blocks = []
    day = fetch_from
    while day < today:
        if blocks:
            await asyncio.sleep(1)  # the API allows 5 requests per second
        end = min(day + timedelta(days=MAX_SPAN_DAYS), today)
        try:
            chunk = await api.get_15min_readings(day, end)
        except aiohttp.ClientError as err:
            _LOGGER.error("Error fetching 15-minute readings: %s", err)
            chunk = None
        if chunk is None:
            if not blocks:
                raise UpdateFailed(f"No 15-minute readings for {day} to {end}")
            # Keep what was fetched; the next run continues from there.
            _LOGGER.warning("Stopped fetching 15-minute readings at %s, continuing on the next run", day)
            break
        blocks += chunk
        day = end

    consumption = parse(blocks, READING_TYPE_15MIN_IMPORT)
    returned = parse(blocks, READING_TYPE_15MIN_EXPORT)

    tz = dt_util.get_default_time_zone()
    tariffs = await hass.async_add_executor_job(split, consumption, partial(_energy_tariff, api, tz), ("vt", "mt"))

    for kind, name, readings in (
        ("consumption", "consumption", consumption),
        ("return", "return", returned),
        ("consumption_vt", "consumption VT", tariffs["vt"]),
        ("consumption_mt", "consumption MT", tariffs["mt"]),
    ):
        await _async_import_energy(hass, statistic_id(meter_id, kind), name, readings, start, settled_before)
    _import_power(hass, statistic_id(meter_id, "power"), consumption, start, settled_before)
    if consumption:
        await _async_import_contracted_power(
            hass, api, statistic_id(meter_id, "contracted_power"), today, max(consumption).replace(minute=0)
        )

    since, consumption_profile = profile(consumption, settled_before)
    _, return_profile = profile(returned, settled_before)
    return {
        "last_reading": max(consumption) + INTERVAL if consumption else None,
        "start": since.isoformat(),
        "consumption": consumption_profile,
        "return": return_profile,
    }


def _energy_tariff(api, tz, hour):
    """Energy tariff of an hour: VT on workdays 6:00-22:00, MT otherwise (matches the meter's registers)."""
    local = hour.astimezone(tz)
    return "vt" if 6 <= local.hour < 22 and not api.is_weekend_or_holiday(local) else "mt"


async def _async_last_day(hass, stat_id):
    """Local date of the newest stored hour of a statistic, or None."""
    last = await get_instance(hass).async_add_executor_job(get_last_statistics, hass, 1, stat_id, True, set())
    if not last.get(stat_id):
        return None
    return dt_util.as_local(dt_util.utc_from_timestamp(last[stat_id][0]["start"])).date()


async def _async_import_contracted_power(hass, api, stat_id, today, last_hour):
    """Import the contracted power of each hour's time block, up to `last_hour`."""
    periods = await api.get_dogovorjene_moci()
    if not periods:
        _LOGGER.warning("No contracted power periods, skipping their statistics")
        return

    last_day = await _async_last_day(hass, stat_id)
    first_day = min(last_day, today - timedelta(days=REWRITE_DAYS)) if last_day else CONTRACT_START
    hours = await hass.async_add_executor_job(
        contracted_power,
        periods,
        api.calculate_tariff,
        dt_util.as_utc(dt_util.start_of_local_day(first_day)),
        last_hour,
        dt_util.get_default_time_zone(),
    )
    if not hours:
        return

    async_add_external_statistics(
        hass,
        StatisticMetaData(
            mean_type=StatisticMeanType.ARITHMETIC,
            has_sum=False,
            name="Moj Elektro contracted power",
            source=DOMAIN,
            statistic_id=stat_id,
            unit_class="power",
            unit_of_measurement=UnitOfPower.KILO_WATT,
        ),
        [StatisticData(start=hour, mean=kw, min=kw, max=kw) for hour, kw in hours],
    )


async def _async_import_energy(hass, stat_id, name, readings, start, settled_before):
    """Import hourly kWh with a running sum continuing from the stored statistics."""
    hours = hourly(readings, start, settled_before)
    if not hours:
        return

    # The sum just before the first rewritten hour; daily rows skip small gaps.
    first = hours[0][0]
    before = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        first - timedelta(days=MAX_SPAN_DAYS),
        first,
        {stat_id},
        "day",
        None,
        {"sum"},
    )
    total = before[stat_id][-1]["sum"] if before.get(stat_id) else 0.0

    statistics = []
    for hour, values in hours:
        total += sum(values)
        statistics.append(StatisticData(start=hour, sum=total))

    async_add_external_statistics(
        hass,
        StatisticMetaData(
            mean_type=StatisticMeanType.NONE,
            has_sum=True,
            name=f"Moj Elektro {name}",
            source=DOMAIN,
            statistic_id=stat_id,
            unit_class="energy",
            unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        ),
        statistics,
    )
    _LOGGER.debug("Imported %s hours into %s, sum %.3f kWh", len(statistics), stat_id, total)


def _import_power(hass, stat_id, readings, start, settled_before):
    """Import hourly mean/min/max of the 15-minute average power (kW)."""
    statistics = [
        StatisticData(start=hour, mean=sum(kw) / len(kw), min=min(kw), max=max(kw))
        for hour, values in hourly(readings, start, settled_before)
        if values
        for kw in [[v * 4 for v in values]]
    ]
    if not statistics:
        return

    async_add_external_statistics(
        hass,
        StatisticMetaData(
            mean_type=StatisticMeanType.ARITHMETIC,
            has_sum=False,
            name="Moj Elektro power",
            source=DOMAIN,
            statistic_id=stat_id,
            unit_class="power",
            unit_of_measurement=UnitOfPower.KILO_WATT,
        ),
        statistics,
    )
