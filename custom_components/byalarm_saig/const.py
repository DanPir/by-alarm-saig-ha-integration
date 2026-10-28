"""Costanti per l'integrazione Vimar By-alarm (SAIG)."""

DOMAIN = "byalarm_saig"

CONF_USERUID = "useruid"
CONF_DUID = "duid"
CONF_DEVICE_PASSWORD = "device_password"
CONF_SOURCE_UID = "source_uid"
CONF_PIN = "pin"
CONF_AREA_IDSF = "area_idsf"

DEFAULT_PORT = 20615
DEFAULT_SCAN_INTERVAL = 30  # secondi
# dopo un comando: letture ravvicinate finche' la centrale conferma il nuovo stato
CONFIRM_INTERVAL = 2  # secondi tra una lettura e l'altra
CONFIRM_TIMEOUT = 20  # secondi massimi di attesa della conferma
# appena ricollegati al gateway: un cambio di stato di un'area vale solo se una nuova lettura lo conferma
RECONNECT_CONFIRM_DELAY = 5  # secondi tra le letture di conferma
RECONNECT_CONFIRM_READS = 3  # letture di conferma al massimo
