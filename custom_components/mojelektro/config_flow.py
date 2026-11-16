import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .moj_elektro_api import MojElektroApi
from .const import DOMAIN, CONF_TOKEN, CONF_METER_ID, CONF_DECIMAL, CONF_PRODUCTION  # Assuming you define CONF_DECIMAL in your .const module

class MojeElektroFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1
    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            session = async_get_clientsession(self.hass)
            decimal = user_input.get(CONF_DECIMAL)  # This returns None if CONF_DECIMAL is not in user_input
            api = MojElektroApi(user_input[CONF_TOKEN], user_input[CONF_METER_ID], decimal, session)
            valid = await api.validate_token()
            if valid:
                return self.async_create_entry(title="Moj Elektro", data=user_input)
            else:
                errors["base"] = "invalid_auth"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_TOKEN): str,
                vol.Required(CONF_METER_ID): str,
                vol.Optional(CONF_DECIMAL): vol.All(vol.Coerce(float), vol.Range(min=0, max=10)),
                vol.Optional(CONF_PRODUCTION, default=True): bool,

                #vol.Optional(CONF_DECIMAL): int  # New field for Decimal input
            }),
            errors=errors,
        )

    @staticmethod
    def async_get_options_flow(config_entry):
        return MojElektroOptionsFlow()


class MojElektroOptionsFlow(config_entries.OptionsFlowWithReload):
    """Options: whether the house produces electricity (output sensors)."""

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required(CONF_PRODUCTION, default=has_production(self.config_entry)): bool,
            }),
        )


def has_production(entry):
    """Whether output (A-) sensors are enabled; the option overrides the setup value."""
    return entry.options.get(CONF_PRODUCTION, entry.data.get(CONF_PRODUCTION, True))
