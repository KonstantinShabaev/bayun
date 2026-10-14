"""Интеграция Баюна для Home Assistant.

Тонкий conversation agent: пересылает текст в bayun-brain /converse и возвращает ответ.
ConversationEntity и config flow появятся в этапе M2 (docs/ROADMAP.md).
"""

from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    hass.data.setdefault(DOMAIN, {})
    return True
