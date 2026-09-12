#!/usr/bin/env python3
"""
byalarm_sai_client.py

Libreria per controllare un gateway Vimar By-alarm (modello SAIG) tramite il
canale WebSocket moderno "ipconnector-protocol" (porta 20615), interamente
ricostruito da reverse engineering (decompilazione di By-alarm Manager, delle
app Vimar View e View Pro, cattura di rete e instrumentazione runtime).

Funziona in totale autonomia: non serve VIEW Pro ne' alcun "risveglio" del
gateway - a differenza del vecchio protocollo legacy sulla porta 12000, questo
canale e' sempre raggiungibile.

Uso rapido:

    from byalarm_sai_client import ByAlarmClient

    client = ByAlarmClient(
        host="192.168.178.32",
        username="daniele.pirro@gmail.com",
        useruid="99675407-2df9-4f6f-b3dc-c8225611af80",
        duid="A32250FBB01025",
        device_password="vN*FGufacU*pjLJk",
        source_uid="53e7af14-b92f-4376-a2d4-f69a293e0c1e",
        pin="051329",
    )
    client.connect()
    print(client.get_status())
    client.arm("Int")   # Tot / Int / Par
    client.disarm()
    client.disconnect()

Richiede: pip install cryptography
"""

from __future__ import annotations

import base64
import json
import os
import random
import socket
import ssl
import string
import struct
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import load_pem_public_key


# ---------------------------------------------------------------------------
# Costanti del protocollo (verificate via reverse engineering)
# ---------------------------------------------------------------------------

WS_PATH = "/wss"
WS_SUBPROTOCOL = "ipconnector-protocol"

# clientinfo dell'app "installer" (View Pro) - valori CRITICI, non cambiare:
# con clienttag="userapp" (l'app consumer) il gateway rifiuta con permission denied.
CLIENT_TAG = "instapp"
MANUFACTURER_TAG = "Vimar"
SF_MODEL_VERSION = "1.0.0"
PROTOCOL_VERSION = "2.2"

KEEPALIVE_INTERVAL = 25  # secondi, dal codice originale (TimeSpan 0:0:25)
REQUEST_TIMEOUT = 15     # secondi di attesa per una risposta

# Modo di inserimento -> (sfetype del comando, valore SFState)
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

    def __init__(self, code: int, function: str, message: str = ""):
        self.code = code
        self.function = function
        friendly = ERROR_MESSAGES.get(code, "")
        text = f"Errore {code} su '{function}'"
        if friendly:
            text += f": {friendly}"
        if message:
            text += f" ({message})"
        super().__init__(text)


@dataclass
class AreaStatus:
    idsf: int
    name: str
    insert: str          # "Dis" / "Tot" / "Int" / "Par"
    armed: bool
    status: str          # es. "Idle"
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
    areas: list = field(default_factory=list)   # list[AreaStatus]
    zones: list = field(default_factory=list)   # list[ZoneStatus]


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


def _ws_handshake(sock: ssl.SSLSocket, host: str, port: int) -> None:
    key = base64.b64encode(os.urandom(16)).decode()
    request = (
        f"GET {WS_PATH} HTTP/1.1\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        f"Connection: Upgrade\r\nUpgrade: websocket\r\n"
        f"Sec-WebSocket-Version: 13\r\n"
        f"Sec-WebSocket-Protocol: {WS_SUBPROTOCOL}\r\n"
        f"Host: {host}:{port}\r\nCache-Control: no-cache\r\n"
        f"Cookie: Token={_gen_key(12)}\r\n\r\n"
    )
    sock.sendall(request.encode())
    resp = b""
    while b"\r\n\r\n" not in resp:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("connessione chiusa durante l'handshake WebSocket")
        resp += chunk
    if b"101" not in resp.split(b"\r\n", 1)[0]:
        raise ConnectionError(f"upgrade WebSocket fallito: {resp.split(chr(13).encode())[0]}")


def _ws_send(sock: ssl.SSLSocket, text: str) -> None:
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
    sock.sendall(header + mask + masked)


def _ws_recv(sock: ssl.SSLSocket) -> Optional[bytes]:
    def recv_exact(n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("connessione chiusa")
            buf += chunk
        return buf

    b0, b1 = recv_exact(2)
    opcode = b0 & 0x0F
    masked = (b1 & 0x80) != 0
    length = b1 & 0x7F
    if length == 126:
        length = struct.unpack("!H", recv_exact(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", recv_exact(8))[0]
    mask_key = recv_exact(4) if masked else None
    payload = recv_exact(length)
    if masked and mask_key:
        payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
    if opcode == 0x8:
        raise ConnectionError(f"frame di chiusura ricevuto: {payload!r}")
    if opcode == 0x9:  # ping
        return None
    return payload


class ByAlarmClient:
    """Client per il gateway Vimar By-alarm (SAIG) via ipconnector-protocol."""

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
    ):
        self.host = host
        self.port = port
        self.username = username
        self.useruid = useruid
        self.duid = duid
        self.device_password = device_password
        self.source_uid = source_uid
        self.pin = pin
        self.on_event = on_event  # callback opzionale per messaggi non richiesti

        self._sock: Optional[ssl.SSLSocket] = None
        self._session_token: Optional[str] = None
        self._public_key_pem: Optional[str] = None
        self._sai_counter = 0

        self._lock = threading.Lock()
        self._msgid_counter = 0
        self._pending: dict[str, dict] = {}   # msgid -> {"event": Event, "response": dict|None}

        self._reader_thread: Optional[threading.Thread] = None
        self._keepalive_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.connected = False

    # -- infrastruttura di basso livello -----------------------------------

    def _next_msgid(self) -> str:
        with self._lock:
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

    def _send_and_wait(self, function: str, args=None, params=None, timeout: float = REQUEST_TIMEOUT) -> dict:
        if not self._sock:
            raise ConnectionError("non connesso")
        msg = self._base_msg(function, args, params)
        msgid = msg["msgid"]
        ev = threading.Event()
        self._pending[msgid] = {"event": ev, "response": None}

        _ws_send(self._sock, json.dumps(msg))

        if not ev.wait(timeout):
            self._pending.pop(msgid, None)
            raise TimeoutError(f"nessuna risposta a '{function}' entro {timeout}s")

        entry = self._pending.pop(msgid)
        resp = entry["response"]
        if resp.get("error", 0) != 0:
            raise ByAlarmError(resp["error"], function)
        return resp

    def _reader_loop(self):
        while not self._stop.is_set():
            try:
                self._sock.settimeout(1.0)
                payload = _ws_recv(self._sock)
            except socket.timeout:
                continue
            except (ConnectionError, OSError):
                self.connected = False
                return
            if payload is None:
                continue
            try:
                obj = json.loads(payload.decode(errors="replace"))
            except json.JSONDecodeError:
                continue

            msgid = obj.get("msgid")
            if msgid in self._pending:
                self._pending[msgid]["response"] = obj
                self._pending[msgid]["event"].set()
            elif self.on_event:
                self.on_event(obj)

    def _keepalive_loop(self):
        while not self._stop.wait(KEEPALIVE_INTERVAL):
            try:
                self._send_and_wait("keepalive", timeout=10)
            except Exception:
                self.connected = False
                return

    # -- cifratura PIN SAI ----------------------------------------------------

    def _sai_accesscode(self) -> str:
        self._sai_counter += 1
        plaintext = f"{self._session_token}:{self.pin}:{self._sai_counter}".encode("utf-8")
        pub = load_pem_public_key(self._public_key_pem.encode("utf-8"))
        ciphertext = pub.encrypt(plaintext, padding.PKCS1v15())
        return base64.b64encode(ciphertext).decode("ascii")

    # -- ciclo di vita della connessione --------------------------------------

    def connect(self):
        """Apre la connessione: session -> (nuova connessione) -> attach. Avvia keepalive."""
        ctx = _make_ssl_context()

        # passo 1: "session" su una connessione temporanea, come fa il client ufficiale
        raw1 = socket.create_connection((self.host, self.port), timeout=10)
        sock1 = ctx.wrap_socket(raw1, server_hostname=self.host)
        sock1.settimeout(10)
        _ws_handshake(sock1, self.host, self.port)
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
        _ws_send(sock1, json.dumps(session_msg))
        try:
            _ws_recv(sock1)  # risposta ignorata: serve solo a completare la fase
        except Exception:
            pass
        sock1.close()

        # passo 2: nuova connessione per l'attach vero e proprio
        raw2 = socket.create_connection((self.host, self.port), timeout=10)
        self._sock = ctx.wrap_socket(raw2, server_hostname=self.host)
        self._sock.settimeout(10)
        _ws_handshake(self._sock, self.host, self.port)

        self._stop.clear()
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()

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
        resp = self._send_and_wait("attach", args=attach_args)
        result = resp["result"][0]
        self._session_token = result["token"]
        self._public_key_pem = result["secureinfo"]["publickey"]
        self._sai_counter = 0
        self.connected = True

        self._keepalive_thread = threading.Thread(target=self._keepalive_loop, daemon=True)
        self._keepalive_thread.start()

    def disconnect(self):
        self._stop.set()
        self.connected = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    @staticmethod
    def _local_ip() -> str:
        """Determina l'IP locale usato per raggiungere Internet (non apre connessioni reali)."""
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        except Exception:
            return "0.0.0.0"
        finally:
            s.close()

    # -- API pubblica ----------------------------------------------------------

    def get_status(self) -> SystemStatus:
        """Legge lo stato completo: gruppo/aree, zone, allarmi, tamper."""
        self._send_and_wait("sfdiscovery", args=[{"sfcategory": "ConfGateway"}])
        time.sleep(0.3)
        self._send_and_wait("sfdiscovery", args=[{"sfcategory": "InfoGateway"}])
        time.sleep(0.3)

        code = self._sai_accesscode()
        resp = self._send_and_wait(
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
                zones_raw = get_elem(elements, "SFE_State_Zones") or "[]"
                alarms_raw = get_elem(elements, "SFE_State_ZoneAlarm") or "[]"
                try:
                    zone_ids = json.loads(zones_raw)
                except json.JSONDecodeError:
                    zone_ids = []
                try:
                    zone_alarms = json.loads(alarms_raw)
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

    def _do_area_action(self, idsf: int, sfetype: str, value: str):
        code = self._sai_accesscode()
        self._send_and_wait(
            "doaction",
            args=[{"idsf": idsf, "sfetype": sfetype, "value": value}],
            params=[{"accesscode": code}],
        )

    def arm(self, mode: str = "Tot", area_idsf: Optional[int] = None):
        """Inserisce l'area. mode: 'Tot' (totale), 'Int' (interno), 'Par' (parziale).
        Se area_idsf non e' specificato, usa la prima area trovata via get_status()."""
        if mode not in ARM_COMMANDS:
            raise ValueError(f"mode deve essere uno tra {list(ARM_COMMANDS)}")
        sfetype, value = ARM_COMMANDS[mode]
        idsf = area_idsf or self._first_area_idsf()
        self._do_area_action(idsf, sfetype, value)

    def disarm(self, area_idsf: Optional[int] = None):
        """Disinserisce l'area."""
        sfetype, value = DISARM_COMMAND
        idsf = area_idsf or self._first_area_idsf()
        self._do_area_action(idsf, sfetype, value)

    def _first_area_idsf(self) -> int:
        status = self.get_status()
        if not status.areas:
            raise RuntimeError("Nessuna area trovata nella discovery SAI")
        return status.areas[0].idsf

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()


if __name__ == "__main__":
    # Esempio di utilizzo da riga di comando.
    import argparse

    parser = argparse.ArgumentParser(description="Client Vimar By-alarm (SAIG)")
    parser.add_argument("--host", required=True)
    parser.add_argument("--username", required=True)
    parser.add_argument("--useruid", required=True)
    parser.add_argument("--duid", required=True)
    parser.add_argument("--password", required=True, help="Password del dispositivo (associations.json)")
    parser.add_argument("--source", required=True, help="Source UID (View Pro install id)")
    parser.add_argument("--pin", required=True, help="Codice utente per i comandi SAI")
    parser.add_argument("--arm", choices=["Tot", "Int", "Par"], help="Inserisci l'impianto")
    parser.add_argument("--disarm", action="store_true", help="Disinserisci l'impianto")
    args = parser.parse_args()

    client = ByAlarmClient(
        host=args.host, username=args.username, useruid=args.useruid,
        duid=args.duid, device_password=args.password, source_uid=args.source,
        pin=args.pin,
    )

    print("Connessione...")
    client.connect()
    print("Connesso.\n")

    if args.arm:
        print(f"Inserimento ({args.arm})...")
        try:
            client.arm(args.arm)
            print("Fatto.\n")
        except ByAlarmError as e:
            print(f"Comando rifiutato dal gateway: {e}\n")
    elif args.disarm:
        print("Disinserimento...")
        try:
            client.disarm()
            print("Fatto.\n")
        except ByAlarmError as e:
            print(f"Comando rifiutato dal gateway: {e}\n")

    status = client.get_status()
    print("Stato sistema:")
    print(f"  Manutenzione:      {status.maintenance}")
    print(f"  Area armata:       {status.area_armed}")
    print(f"  Allarme in corso:  {status.area_alarm}")
    print(f"  Allarme in memoria:{status.area_alarm_memory}")
    print(f"  Tamper zona:       {status.zone_tamper}")
    print(f"  Tamper dispositivo:{status.device_tamper_alarm}")
    for area in status.areas:
        print(f"\n  Area '{area.name}' (idsf={area.idsf}): {area.insert} - {area.status}")
        print(f"    Zone appartenenti: {area.zone_ids}")
    for zone in status.zones:
        flags = []
        if zone.opened: flags.append("APERTA")
        if zone.tamper: flags.append("TAMPER")
        if zone.excluded: flags.append("ESCLUSA")
        print(f"  Zona {zone.zone_id} '{zone.name}': {', '.join(flags) or 'OK'}")

    client.disconnect()
