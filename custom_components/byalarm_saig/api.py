"""Client asyncio per il gateway Vimar By-alarm (SAIG) via ipconnector-protocol.

Protocollo interamente ricostruito via reverse engineering (decompilazione di
By-alarm Manager, delle app Vimar View e View Pro, cattura di rete e
instrumentazione runtime). Funziona in totale autonomia sulla porta 20615,
senza bisogno di VIEW Pro.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import random
import socket
import ssl
import string
import struct
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import load_pem_public_key

_LOGGER = logging.getLogger(__name__)

WS_PATH = "/wss"
WS_SUBPROTOCOL = "ipconnector-protocol"

# clientinfo dell'app "installer" (View Pro) - valori CRITICI: con
# clienttag="userapp" (l'app consumer) il gateway rifiuta con permission denied.
CLIENT_TAG = "instapp"
MANUFACTURER_TAG = "Vimar"
SF_MODEL_VERSION = "1.0.0"
PROTOCOL_VERSION = "2.2"

KEEPALIVE_INTERVAL = 25  # secondi (dal codice originale: TimeSpan 0:0:25)
REQUEST_TIMEOUT = 15

ARM_COMMANDS = {
    "Tot": ("SFE_Cmd_On", "On"),
    "Int": ("SFE_Cmd_Int", "Int"),
    "Par": ("SFE_Cmd_Par", "Par"),
}
DISARM_COMMAND = ("SFE_Cmd_Dis", "Dis")

# Messaggi leggibili per i codici di errore piu' comuni (codice IPErrorType)
ERROR_MESSAGES = {
    2: "Permesso negato (credenziali/permessi non validi per questo dispositivo)",
    3: "Target non valido (duid errato per questo gateway)",
    6: "Tipo di richiesta non valido",
    7: "Argomenti della richiesta malformati",
    10: "Dati non validi per il comando richiesto",
    16: "Errore di sessione",
    21: "Password del dispositivo non valida",
    23: "Blocco di sistema: il pannello rifiuta il comando in queste condizioni "
        "(es. zone aperte che impediscono l'inserimento richiesto)",
    25: "Parametri della richiesta incompleti o malformati",
}


class ByAlarmError(Exception):
    """Errore restituito dal gateway (codice IPErrorType)."""

    def __init__(self, code: int, function: str):
        self.code = code
        self.function = function
        friendly = ERROR_MESSAGES.get(code, "")
        text = f"Errore {code} su '{function}'"
        if friendly:
            text += f": {friendly}"
        super().__init__(text)


class ByAlarmConnectionError(Exception):
    """Errore di connessione/rete verso il gateway."""


class ByAlarmAuthError(Exception):
    """Credenziali rifiutate dal gateway (attach fallito)."""


@dataclass
class AreaStatus:
    idsf: int
    name: str
    insert: str  # "Dis" / "Tot" / "Int" / "Par"
    armed: bool
    status: str
    zone_alarms: list = field(default_factory=list)
    zone_ids: list = field(default_factory=list)


@dataclass
class ZoneStatus:
    idsf: int
    name: str
    zone_id: int
    opened: bool
    tamper: bool
    mask: bool
    excluded: bool
    timed: bool
    alarm_info: str


@dataclass
class SystemStatus:
    maintenance: bool
    area_armed: bool
    area_alarm: bool
    area_alarm_memory: bool
    zone_mask: bool
    zone_tamper: bool
    device_tamper_alarm: bool
    device_tamper_alarm_memory: bool
    areas: list = field(default_factory=list)
    zones: list = field(default_factory=list)


def _gen_key(n: int = 12) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(random.choice(alphabet) for _ in range(n))


def _make_ssl_context() -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    try:
        ctx.set_ciphers("DEFAULT:@SECLEVEL=0")
    except ssl.SSLError:
        ctx.set_ciphers("ALL")
    return ctx


def _build_ws_frame(text: str) -> bytes:
    payload = text.encode("utf-8")
    mask = os.urandom(4)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    n = len(payload)
    if n <= 125:
        header = struct.pack("!BB", 0x81, 0x80 | n)
    elif n <= 65535:
        header = struct.pack("!BBH", 0x81, 0x80 | 126, n)
    else:
        header = struct.pack("!BBQ", 0x81, 0x80 | 127, n)
    return header + mask + masked


class ByAlarmClient:
    """Client asyncio per il gateway Vimar By-alarm (SAIG)."""

    def __init__(
        self,
        host: str,
        username: str,
        useruid: str,
        duid: str,
        device_password: str,
        source_uid: str,
        pin: str,
        port: int = 20615,
        on_event: Optional[Callable[[dict], None]] = None,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.useruid = useruid
        self.duid = duid
        self.device_password = device_password
        self.source_uid = source_uid
        self.pin = pin
        self.on_event = on_event

        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._session_token: Optional[str] = None
        self._public_key_pem: Optional[str] = None
        self._sai_counter = 0

        self._msgid_counter = 0
        self._pending: dict[str, asyncio.Future] = {}

        self._reader_task: Optional[asyncio.Task] = None
        self._keepalive_task: Optional[asyncio.Task] = None
        self._closing = False
        self.connected = False

    # -- basso livello: WebSocket handshake / framing ------------------------

    async def _ws_handshake(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        key = base64.b64encode(os.urandom(16)).decode()
        request = (
            f"GET {WS_PATH} HTTP/1.1\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Connection: Upgrade\r\nUpgrade: websocket\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
            f"Sec-WebSocket-Protocol: {WS_SUBPROTOCOL}\r\n"
            f"Host: {self.host}:{self.port}\r\nCache-Control: no-cache\r\n"
            f"Cookie: Token={_gen_key(12)}\r\n\r\n"
        )
        writer.write(request.encode())
        await writer.drain()
        response = await reader.readuntil(b"\r\n\r\n")
        if b"101" not in response.split(b"\r\n", 1)[0]:
            raise ByAlarmConnectionError(f"upgrade WebSocket fallito: {response!r}")

    @staticmethod
    async def _ws_recv(reader: asyncio.StreamReader) -> Optional[bytes]:
        first2 = await reader.readexactly(2)
        b0, b1 = first2[0], first2[1]
        opcode = b0 & 0x0F
        masked = (b1 & 0x80) != 0
        length = b1 & 0x7F
        if length == 126:
            length = struct.unpack("!H", await reader.readexactly(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", await reader.readexactly(8))[0]
        mask_key = await reader.readexactly(4) if masked else None
        payload = await reader.readexactly(length) if length else b""
        if masked and mask_key:
            payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
        if opcode == 0x8:
            raise ByAlarmConnectionError(f"frame di chiusura ricevuto: {payload!r}")
        if opcode == 0x9:
            return None
        return payload

    async def _open_ws(self):
        ctx = _make_ssl_context()
        reader, writer = await asyncio.open_connection(
            self.host, self.port, ssl=ctx, server_hostname=self.host
        )
        await self._ws_handshake(reader, writer)
        return reader, writer

    # -- messaggi applicativi --------------------------------------------------

    def _next_msgid(self) -> str:
        self._msgid_counter += 1
        return str(self._msgid_counter)

    def _base_msg(self, function: str, args=None, params=None) -> dict:
        return {
            "type": "request",
            "function": function,
            "source": self.source_uid,
            "target": self.duid,
            "token": self._session_token or _gen_key(12),
            "msgid": self._next_msgid(),
            "args": args if args is not None else [],
            "params": params if params is not None else [],
        }

    async def _send_and_wait(self, function: str, args=None, params=None, timeout: float = REQUEST_TIMEOUT) -> dict:
        if not self._writer:
            raise ByAlarmConnectionError("non connesso")
        msg = self._base_msg(function, args, params)
        msgid = msg["msgid"]
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[msgid] = fut

        self._writer.write(_build_ws_frame(json.dumps(msg)))
        await self._writer.drain()

        try:
            resp = await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError as err:
            self._pending.pop(msgid, None)
            raise ByAlarmConnectionError(f"nessuna risposta a '{function}' entro {timeout}s") from err

        if resp.get("error", 0) != 0:
            raise ByAlarmError(resp["error"], function)
        return resp

    async def _reader_loop(self):
        try:
            while not self._closing:
                payload = await self._ws_recv(self._reader)
                if payload is None:
                    continue
                try:
                    obj = json.loads(payload.decode(errors="replace"))
                except json.JSONDecodeError:
                    continue
                msgid = obj.get("msgid")
                fut = self._pending.pop(msgid, None)
                if fut and not fut.done():
                    fut.set_result(obj)
                elif self.on_event:
                    self.on_event(obj)
        except (asyncio.IncompleteReadError, ConnectionError, OSError) as err:
            _LOGGER.debug("Connessione al gateway By-alarm interrotta: %s", err)
            self.connected = False
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(ByAlarmConnectionError("connessione interrotta"))
            self._pending.clear()

    async def _keepalive_loop(self):
        try:
            while not self._closing:
                await asyncio.sleep(KEEPALIVE_INTERVAL)
                if self._closing:
                    return
                await self._send_and_wait("keepalive", timeout=10)
        except Exception as err:  # noqa: BLE001 - qualunque errore chiude la sessione
            _LOGGER.debug("Keepalive fallito, connessione considerata interrotta: %s", err)
            self.connected = False

    # -- cifratura PIN SAI -----------------------------------------------------

    def _sai_accesscode(self) -> str:
        self._sai_counter += 1
        plaintext = f"{self._session_token}:{self.pin}:{self._sai_counter}".encode("utf-8")
        pub = load_pem_public_key(self._public_key_pem.encode("utf-8"))
        ciphertext = pub.encrypt(plaintext, padding.PKCS1v15())
        return base64.b64encode(ciphertext).decode("ascii")

    # -- ciclo di vita -----------------------------------------------------------

    async def connect(self) -> None:
        """Apre la connessione: session (temporanea) -> attach (definitiva)."""
        # passo 1: "session", su una connessione temporanea, come fa il client ufficiale
        reader1, writer1 = await self._open_ws()
        session_msg = {
            "type": "request", "function": "session",
            "source": self.source_uid, "target": self.duid,
            "token": _gen_key(12), "msgid": "1",
            "args": [{"communication": {
                "ipaddress": self._local_ip(), "ipport": 9999,
                "communicationmode": 4, "requireencryption": True,
            }}],
            "params": [],
        }
        writer1.write(_build_ws_frame(json.dumps(session_msg)))
        await writer1.drain()
        try:
            await asyncio.wait_for(self._ws_recv(reader1), timeout=10)
        except Exception:  # noqa: BLE001 - risposta ignorata, serve solo a completare la fase
            pass
        writer1.close()

        # passo 2: nuova connessione per l'attach vero e proprio
        self._reader, self._writer = await self._open_ws()
        self._closing = False
        self._msgid_counter = 0
        self._reader_task = asyncio.create_task(self._reader_loop())

        attach_args = [{
            "credential": {
                "username": self.username,
                "useruid": self.useruid,
                "password": self.device_password,
            },
            "clientinfo": {
                "manufacturertag": MANUFACTURER_TAG,
                "clienttag": CLIENT_TAG,
                "sfmodelversion": SF_MODEL_VERSION,
                "lang": "it",
                "protocolversion": PROTOCOL_VERSION,
            },
            "communication": {"ipaddress": self.host},
        }]
        try:
            resp = await self._send_and_wait("attach", args=attach_args)
        except ByAlarmError as err:
            await self.disconnect()
            raise ByAlarmAuthError(f"Attach rifiutato dal gateway (errore {err.code})") from err

        result = resp["result"][0]
        self._session_token = result["token"]
        self._public_key_pem = result["secureinfo"]["publickey"]
        self._sai_counter = 0
        self.connected = True

        self._keepalive_task = asyncio.create_task(self._keepalive_loop())

    async def disconnect(self) -> None:
        self._closing = True
        self.connected = False
        for task in (self._reader_task, self._keepalive_task):
            if task and not task.done():
                task.cancel()
        if self._writer:
            try:
                self._writer.close()
            except Exception:  # noqa: BLE001
                pass
        self._writer = None
        self._reader = None

    async def ensure_connected(self) -> None:
        if not self.connected:
            await self.connect()

    @staticmethod
    def _local_ip() -> str:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        except Exception:  # noqa: BLE001
            return "0.0.0.0"
        finally:
            s.close()

    # -- API pubblica --------------------------------------------------------------

    async def async_get_status(self) -> SystemStatus:
        await self.ensure_connected()
        await self._send_and_wait("sfdiscovery", args=[{"sfcategory": "ConfGateway"}])
        await asyncio.sleep(0.3)
        await self._send_and_wait("sfdiscovery", args=[{"sfcategory": "InfoGateway"}])
        await asyncio.sleep(0.3)

        code = self._sai_accesscode()
        resp = await self._send_and_wait(
            "sfdiscovery",
            args=[{"sfcategory": "Sai"}],
            params=[{"accesscode": code, "idambient": [], "withvalues": True}],
        )
        return self._parse_sai_discovery(resp["result"])

    def _parse_sai_discovery(self, blocks: list) -> SystemStatus:
        def get_elem(elements, sfetype):
            for e in elements:
                if e.get("sfetype") == sfetype:
                    return e.get("value")
            return None

        status = SystemStatus(
            maintenance=False, area_armed=False, area_alarm=False,
            area_alarm_memory=False, zone_mask=False, zone_tamper=False,
            device_tamper_alarm=False, device_tamper_alarm_memory=False,
        )

        for block in blocks:
            sstype = block.get("sstype")
            elements = block.get("elements", [])

            if sstype == "SS_Service_Saig":
                status.maintenance = get_elem(elements, "SFE_State_Maintenance") == "On"
                status.area_armed = get_elem(elements, "SFE_State_AreaArmed") == "True"
                status.area_alarm = get_elem(elements, "SFE_State_AreaAlarm") == "True"
                status.area_alarm_memory = get_elem(elements, "SFE_State_AreaAlarmMemory") == "True"
                status.zone_mask = get_elem(elements, "SFE_State_ZoneMask") == "True"
                status.zone_tamper = get_elem(elements, "SFE_State_ZoneTamper") == "True"
                status.device_tamper_alarm = get_elem(elements, "SFE_State_DeviceTamperAlarm") == "True"
                status.device_tamper_alarm_memory = get_elem(elements, "SFE_State_DeviceTamperAlarmMemory") == "True"

            elif sstype == "SS_Area":
                insert = get_elem(elements, "SFE_State_Insert") or "Dis"
                try:
                    zone_ids = json.loads(get_elem(elements, "SFE_State_Zones") or "[]")
                except json.JSONDecodeError:
                    zone_ids = []
                try:
                    zone_alarms = json.loads(get_elem(elements, "SFE_State_ZoneAlarm") or "[]")
                except json.JSONDecodeError:
                    zone_alarms = []
                status.areas.append(AreaStatus(
                    idsf=block.get("idsf"),
                    name=block.get("name", ""),
                    insert=insert,
                    armed=insert != "Dis",
                    status=get_elem(elements, "SFE_State_Status") or "",
                    zone_alarms=zone_alarms,
                    zone_ids=zone_ids,
                ))

            elif sstype == "SS_Zone":
                status.zones.append(ZoneStatus(
                    idsf=block.get("idsf"),
                    name=block.get("name", ""),
                    zone_id=int(get_elem(elements, "SFE_State_ZoneId") or 0),
                    opened=get_elem(elements, "SFE_State_Opened") == "True",
                    tamper=get_elem(elements, "SFE_State_Tamper") == "True",
                    mask=get_elem(elements, "SFE_State_Mask") == "True",
                    excluded=get_elem(elements, "SFE_State_Excluded") == "True",
                    timed=get_elem(elements, "SFE_State_Timed") == "True",
                    alarm_info=get_elem(elements, "SFE_State_AlarmInfo") or "",
                ))

        return status

    async def _do_area_action(self, idsf: int, sfetype: str, value: str) -> None:
        await self.ensure_connected()
        code = self._sai_accesscode()
        await self._send_and_wait(
            "doaction",
            args=[{"idsf": idsf, "sfetype": sfetype, "value": value}],
            params=[{"accesscode": code}],
        )

    async def async_arm(self, mode: str, area_idsf: int) -> None:
        if mode not in ARM_COMMANDS:
            raise ValueError(f"mode deve essere uno tra {list(ARM_COMMANDS)}")
        sfetype, value = ARM_COMMANDS[mode]
        await self._do_area_action(area_idsf, sfetype, value)

    async def async_disarm(self, area_idsf: int) -> None:
        sfetype, value = DISARM_COMMAND
        await self._do_area_action(area_idsf, sfetype, value)
