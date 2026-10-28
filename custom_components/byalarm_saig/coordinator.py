"""Coordinator per l'integrazione Vimar By-alarm (SAIG)."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    ByAlarmAuthError,
    ByAlarmClient,
    ByAlarmConnectionError,
    ByAlarmError,
    SystemStatus,
)
from .const import (
    CONFIRM_INTERVAL,
    CONFIRM_TIMEOUT,
    DEFAULT_SCAN_INTERVAL,
    RECONNECT_CONFIRM_DELAY,
    RECONNECT_CONFIRM_READS,
)

_LOGGER = logging.getLogger(__name__)


def _area_states(status: SystemStatus) -> dict:
    return {a.idsf: a.insert for a in status.areas}


class ByAlarmCoordinator(DataUpdateCoordinator[SystemStatus]):
    """Mantiene la connessione al gateway e aggiorna periodicamente lo stato."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: ByAlarmClient, area_idsf: int) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name="Vimar By-alarm",
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.client = client
        self.area_idsf = area_idsf
        self._confirm_targets: dict[int, str] = {}
        self._confirm_task: asyncio.Task | None = None

    async def _async_update_data(self) -> SystemStatus:
        try:
            status = await self.client.async_get_status()
            if not self.last_update_success and self.data is not None:
                status = await self._async_confirm_after_reconnect(status)
            return status
        except ByAlarmAuthError as err:
            # il gateway rifiuta le credenziali salvate (es. password del
            # dispositivo rigenerata): ferma il polling e avvia il reauth
            raise ConfigEntryAuthFailed(str(err)) from err
        except (ByAlarmConnectionError, ByAlarmError) as err:
            raise UpdateFailed(f"Errore comunicando con il gateway: {err}") from err

    @property
    def saig2(self) -> bool:
        return bool(self.data and self.data.saig2)

    async def _async_confirm_after_reconnect(self, status: SystemStatus) -> SystemStatus:
        """Appena ricollegati, la prima lettura puo' essere sbagliata (visto su una By-alarm Plus
        dopo un cambio dei permessi del codice utente: un'area inserita letta "Dis" per un giro).
        Se lo stato di un'area e' cambiato rispetto a prima della disconnessione, si rilegge
        finche' due letture di fila sono d'accordo."""
        if _area_states(status) == _area_states(self.data):
            return status
        for _ in range(RECONNECT_CONFIRM_READS):
            await asyncio.sleep(RECONNECT_CONFIRM_DELAY)
            again = await self.client.async_get_status()
            if _area_states(again) == _area_states(status):
                return again
            status = again
        return status

    async def async_arm(self, mode: str, area_idsf: int | None = None) -> None:
        area_idsf = area_idsf if area_idsf is not None else self.area_idsf
        if self.saig2:
            await self.client.async_insert_saig2(area_idsf, mode)
        else:
            await self.client.async_arm(mode, area_idsf)
        self._start_confirm(area_idsf, mode)

    async def async_disarm(self, area_idsf: int | None = None) -> None:
        area_idsf = area_idsf if area_idsf is not None else self.area_idsf
        if self.saig2:
            await self.client.async_insert_saig2(area_idsf, "Dis")
        else:
            await self.client.async_disarm(area_idsf)
        self._start_confirm(area_idsf, "Dis")

    def area_insert(self, area_idsf: int) -> str | None:
        """Stato di inserimento attuale dell'area ("Dis"/"Tot"/...), None se sconosciuto."""
        areas = self.data.areas if self.data else []
        area = next((a for a in areas if a.idsf == area_idsf), areas[0] if len(areas) == 1 else None)
        return area.insert if area else None

    def _start_confirm(self, area_idsf: int, target: str) -> None:
        # il comando e' partito: la centrale cambia stato dopo qualche secondo, quindi invece di
        # aspettare la lettura normale (ogni 30 s) si rilegge spesso finche' non conferma.
        # Un solo ciclo per tutti i comandi in attesa; il task e' legato alla config entry,
        # quindi viene cancellato se l'entry viene scaricata o ricaricata.
        self._confirm_targets[area_idsf] = target
        if self._confirm_task and not self._confirm_task.done():
            self._confirm_task.cancel()
        self._confirm_task = self.config_entry.async_create_background_task(
            self.hass, self._async_confirm(), name="byalarm_confirm"
        )

    async def _async_confirm(self) -> None:
        for _ in range(CONFIRM_TIMEOUT // CONFIRM_INTERVAL):
            await asyncio.sleep(CONFIRM_INTERVAL)
            await self.async_refresh()
            for area_idsf, target in list(self._confirm_targets.items()):
                if self.area_insert(area_idsf) == target:
                    del self._confirm_targets[area_idsf]
            if not self._confirm_targets:
                return
        for area_idsf, target in self._confirm_targets.items():
            _LOGGER.warning(
                "Il gateway non ha confermato '%s' sull'area %s entro %s s", target, area_idsf, CONFIRM_TIMEOUT
            )
        self._confirm_targets.clear()
