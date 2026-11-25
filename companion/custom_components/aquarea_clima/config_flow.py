"""No UI credentials: configuration is provisioned privately on the HA server."""
import json
from pathlib import Path
from homeassistant import config_entries
DOMAIN='aquarea_clima'
class Flow(config_entries.ConfigFlow,domain=DOMAIN):
    VERSION=1
    async def async_step_user(self,user_input=None):
        if self._async_current_entries():return self.async_abort(reason='already_configured')
        path=Path(self.hass.config.path('aquarea-clima-sso','bridge.json'))
        try:data=await self.hass.async_add_executor_job(lambda:json.loads(path.read_text(encoding='utf-8')))
        except (OSError,ValueError):return self.async_abort(reason='not_provisioned')
        if not isinstance(data.get('bridge_secret'),str) or len(data['bridge_secret'])<40:return self.async_abort(reason='not_provisioned')
        return self.async_create_entry(title='Aquarea Clima',data={'bridge_secret':data['bridge_secret']})
