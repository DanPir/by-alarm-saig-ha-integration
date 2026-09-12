"""Coordinator per l'integrazione Vimar By-alarm (SAIG)."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ByAlarmClient, ByAlarmConnectionError, ByAlarmError, SystemStatus
from .const import DEFAULT_SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)


class ByAlarmCoordinator(DataUpdateCoordinator[SystemStatus]):
    """Mantiene la connessione al gateway e aggiorna periodicamente lo stato."""

    def __init__(self, hass: HomeAssistant, client: ByAlarmClient, area_idsf: int) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="Vimar By-alarm",
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.client = client
        self.area_idsf = area_idsf

    async def _async_update_data(self) -> SystemStatus:
        try:
            return await self.client.async_get_status()
        except (ByAlarmConnectionError, ByAlarmError) as err:
            raise UpdateFailed(f"Errore comunicando con il gateway: {err}") from err

    async def async_arm(self, mode: str) -> None:
        await self.client.async_arm(mode, self.area_idsf)
        await self.async_request_refresh()

    async def async_disarm(self) -> None:
        await self.client.async_disarm(self.area_idsf)
        await self.async_request_refresh()
