from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import config_validation as cv
from .const import DOMAIN
from .config_flow import has_production
import logging

_LOGGER = logging.getLogger(__name__)

# Define CONFIG_SCHEMA as config entry only
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

async def async_setup(hass: HomeAssistant, config: dict):
    """Set up the integration via configuration.yaml (if applicable)."""
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Set up the integration from a config entry."""
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = entry.data

    # Migrate existing entities to be grouped under a single device
    await migrate_existing_entities_to_device(hass, entry)

    sync_entity_registry(hass, entry)

    # Forward the setup to sensor platform
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])
    
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Handle removal of an entry."""
    unload_ok = await hass.config_entries.async_forward_entry_unload(entry, "sensor")
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok

def sync_entity_registry(hass: HomeAssistant, entry: ConfigEntry):
    """Remove the old 15min sensors; disable output (A-) sensors without production, re-enable them with it."""
    registry = er.async_get(hass)
    production = has_production(entry)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        # Replaced by long-term statistics and the last reading sensor.
        if entity.unique_id.endswith(("_15min_input", "_15min_output")):
            registry.async_remove(entity.entity_id)
            continue
        if "_output" not in entity.unique_id:
            continue
        # Entities disabled by the user are left alone.
        if not production and entity.disabled_by is None:
            registry.async_update_entity(entity.entity_id, disabled_by=er.RegistryEntryDisabler.INTEGRATION)
        elif production and entity.disabled_by is er.RegistryEntryDisabler.INTEGRATION:
            registry.async_update_entity(entity.entity_id, disabled_by=None)


async def migrate_existing_entities_to_device(hass: HomeAssistant, entry: ConfigEntry):
    """Migrate old entities without devices to a single device."""
    entity_registry = er.async_get(hass)
    meter_id = entry.data.get("meter_id")
    
    _LOGGER.info(f"Entity migration started for meter_id {meter_id}.")

    # Find all entities for this integration
    for entity_entry in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        # If the entity doesn't have a device_id, we'll assign it to the new device
        if entity_entry.device_id is None:
            _LOGGER.debug(f"Migrating entity {entity_entry.entity_id} to new device.")
    
    _LOGGER.debug(f"Entity migration completed for integration {DOMAIN}.")
