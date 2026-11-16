[![hacs default](https://img.shields.io/badge/HACS-Default-green.svg)](https://hacs.xyz)
[![GitHub issues](https://img.shields.io/github/issues/frlequ/homeassistant-mojelektro)](https://github.com/frlequ/homeassistant-mojelektro/issues) 
![GitHub User's stars](https://img.shields.io/github/stars/frlequ)
![GitHub Repo stars](https://img.shields.io/github/stars/frlequ/homeassistant-mojelektro)


# Home Assistant Integration of Moj Elektro electricity meter 

This is an updated version of the custom component for integrating electric utility meter data into Home Assistant. It no longer requires a local certificate to access Moj Elektro but utilizes a **new API** service provided by Informatika.si.

![Screenshot of a Moj Electro in Home Assistant using Apex Chart Card.](/assets/energy.jpg)
<sub> *Example of Moj Elektro API using Apex Chart Card. You can find yaml in https://github.com/frlequ/mojelektro-apex-chart </sub> 


> [!NOTE]
> **Note about data:** This integration gathers energy information with a **24-hour delay** because API doesn't provide real-time data. Unfortunately, this delay leads to inaccurate readings, especially between midnight and 6 a.m as data aggregates. The problem lies with Moj Elektro, and there's no way around it.


## Setup API

1. Log in to Mojelektro.si using any available login options.
2. Under `API storitve`, find the option to create a token `Kreiraj žeton`. Use unlimited expiration and click  `Kreiraj žeton.`
4. Copy the newly generated token. You'll need it in the configuration step.
5. Your `meter_id` is `EIMM` number found und `Merilna mesta/merilne točke`


## Installation
1. **Either**
    - Method 1 _(easiest)_: Find and download `Moj Elektro` `integration` in HACS. Moj Elektro is now part of default HACS repository.
    
    - Method 2: [![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=frlequ&repository=homeassistant-mojelektro&category=integration)
    
    - Method 3: Manually copy `mojelektro` folder from [latest release](https://github.com/frlequ/homeassistant-mojelektro/releases/latest) to `custom_components` folder.
2. _After download restart Home Assistant!_

## Configuration
In Home Assistant
1. Go to Settings > Add integration > search > Moj Elektro
2. Enter credentials:

![Screenshot of a Moj Electro setup in Home Assistant.](/assets/setup.jpg)

> [!NOTE]
> _As for version 0.2.0 there is no need for `configuration.yaml` file edit!_

## Sensors

Moj Elektro publishes a day's readings the next morning (usually around 5:30),
so the sensors show finished days:

- **Yesterday input/output** (entity IDs `*_daily_*`): energy of the previous
  day, from the meter's daily readings. `peak`/`offpeak` are the high (VT) and
  low (MT) tariff parts.
- **Monthly input/output**: the current month up to the end of yesterday. On
  the 1st it shows the whole previous month.
- **Yesterday input blok 1-5**: yesterday's consumption per network time block.
- **Casovni blok 1-5**: contracted power (dogovorjena moč) per time block, kW.
- **Last reading**: see [15-minute readings for charts](#15-minute-readings-for-charts).

Home Assistant also builds long-term statistics from these sensors, but at
the time each value arrives, a day late. For energy at the hour it was used,
use the [long-term statistics](#long-term-statistics) instead. Upgrading
removes the old `15min_input`/`15min_output` sensors, which showed
yesterday's reading for the current quarter-hour. Old statistics of these
sensors can be deleted under **Developer tools > Statistics**.

### Houses without production

If the house doesn't produce electricity (no solar), turn off **Produces
electricity**, either when adding the integration or later under
**Settings > Devices & services > Moj Elektro > Configure**. The output
sensors (`*_output*`) are then disabled; their history is kept and they are
enabled again if you turn the setting back on. Sensors you disabled yourself
are left alone.

## Long-term statistics

The sensors above can only show data at the time Home Assistant fetches it,
which is a day late. The integration therefore also imports the 15-minute
readings into Home Assistant's long-term statistics, at the hour they belong
to. These statistics are hourly, the finest resolution Home Assistant stores:

- `mojelektro:<meter_id>_consumption`: energy received from the grid, kWh.
- `mojelektro:<meter_id>_return`: energy delivered to the grid, kWh.
- `mojelektro:<meter_id>_power`: hourly mean, minimum and maximum of the
  15-minute average power, kW. The maximum is the highest 15-minute load in
  that hour, which is what contracted power (dogovorjena moč) is compared to.
- `mojelektro:<meter_id>_contracted_power`: contracted power (dogovorjena
  moč) of the time block each hour belongs to, kW, from January 2024. Shown
  with the `_power` maximum in one statistics graph, it tells when the
  15-minute load came close to or over the contracted power.

`<meter_id>` is lowercase with `-` replaced by `_`, e.g. `6_18246`.

Add the consumption and return statistics as grid consumption and return in
**Settings > Dashboards > Energy**. The power statistic can be shown with a
statistics graph card.

How the import works:

- It runs at startup and then every hour.
- The first run imports the last 3 years, the most the API allows. It runs in
  the background and takes about 3 minutes.
  Statistics imported by an older version cover only 2 years. To import the
  third, delete the `mojelektro:` statistics under **Developer tools >
  Statistics** and reload the integration.
- Every run re-imports the last 7 days, so readings that arrive late or are
  corrected by Moj Elektro replace the old values.
- An hour is imported only once all four of its readings are there. After 7
  days an hour is imported with whatever readings exist.
- If the API is unavailable, nothing is lost: the next successful run fills
  the gap.

### 15-minute readings for charts

The **Moj Elektro last reading** sensor shows the end of the newest reading.
Its entity ID is `sensor.moj_elektro_last_reading`, or with the area in front
(e.g. `sensor.home_moj_elektro_last_reading`) if the device is in an area.
Its attributes hold the readings of the last 7 days, for charts:

- `start`: start of the first interval (ISO timestamp, UTC).
- `consumption`, `return`: kWh per 15 minutes from `start`, `null` where a
  reading is missing.

The attributes are not stored in the database. For example, with
[apexcharts-card](https://github.com/RomRider/apexcharts-card):

```yaml
type: custom:apexcharts-card
graph_span: 7d
span:
  end: day
  offset: -1d
series:
  - entity: sensor.moj_elektro_last_reading
    name: Consumption
    type: column
    unit: kW
    data_generator: |
      const start = Date.parse(entity.attributes.start);
      return entity.attributes.consumption.map((v, i) => [start + i * 900000, v === null ? null : v * 4]);
```

## Network Tariff Blocks
If you are also searching for current tariff blocks (omrežnina), you can find integration here:

https://github.com/frlequ/home-assistant-network-tariff

and the custom card:

https://github.com/frlequ/network-tariff-card

## Roadmap

- [X] Add energy output sensors
- [X] Add config flow (Home Assistant UI setup)
- [X] Add decimals options for meters
- [X] Add tariff block power
- [X] Add energy consumption by blocks
- [X] Add option to save to long term statistics
- [X] Multi-language Support
    - [X] English
    - [ ] Slovenian


## Report any issues

Thanks and consider giving me a 🌟 star

<a href="https://www.buymeacoffee.com/frlequ" target="_blank"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy Me A Coffee" style="height: 60px !important;width: 217px !important;" ></a>
