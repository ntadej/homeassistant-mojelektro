"""Import Moj Elektro 15-minute readings into long-term statistics."""

import asyncio
from datetime import timedelta
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
from .readings import INTERVAL, parse, hourly, profile

_LOGGER = logging.getLogger(__name__)

# Days re-imported on every run, so late or corrected readings replace old ones.
REWRITE_DAYS = 7
# API limits: one request spans at most 35 days and starts at most 2 years back.
MAX_SPAN_DAYS = 35
MAX_AGE_DAYS = 730


def statistic_id(meter_id, kind):
    """Return the external statistic ID for a meter, e.g. mojelektro:6_18246_consumption."""
    return f"{DOMAIN}:{re.sub(r'[^a-z0-9]+', '_', meter_id.lower()).strip('_')}_{kind}"


async def async_import_statistics(hass, api, meter_id):
    """Fetch new 15-minute readings, import hourly statistics, return the recent profile."""
    consumption_id = statistic_id(meter_id, "consumption")
    today = dt_util.now().date()
    settled_before = dt_util.as_utc(dt_util.start_of_local_day(today - timedelta(days=REWRITE_DAYS)))

    last = await get_instance(hass).async_add_executor_job(
        get_last_statistics, hass, 1, consumption_id, True, set()
    )
    if last.get(consumption_id):
        last_day = dt_util.as_local(dt_util.utc_from_timestamp(last[consumption_id][0]["start"])).date()
        fetch_from = min(last_day, today - timedelta(days=REWRITE_DAYS))
    else:
        fetch_from = today - timedelta(days=MAX_AGE_DAYS)
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

    await _async_import_energy(hass, consumption_id, "consumption", consumption, start, settled_before)
    await _async_import_energy(hass, statistic_id(meter_id, "return"), "return", returned, start, settled_before)
    _import_power(hass, statistic_id(meter_id, "power"), consumption, start, settled_before)

    since, consumption_profile = profile(consumption, settled_before)
    _, return_profile = profile(returned, settled_before)
    return {
        "last_reading": max(consumption) + INTERVAL if consumption else None,
        "start": since.isoformat(),
        "consumption": consumption_profile,
        "return": return_profile,
    }


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
