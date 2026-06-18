"""Light platform for VIGI cameras — spotlight / white light entity.

Replaces the spotlight on/off switch and spotlight intensity number with a
single HA light entity that supports brightness. The camera exposes a 1–5
step intensity scale; this is mapped linearly to HA's 1–255 range.

Phase 2 implementation — stub for Phase 1 release.
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Light entities added in Phase 2."""
