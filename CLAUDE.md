# Vimar By-alarm (SAIG) - Integrazione Home Assistant

Integrazione custom non ufficiale per controllare un sistema antintrusione
Vimar By-alarm (gateway modello **SAIG**) da Home Assistant, senza VIEW Pro.

Il protocollo (WebSocket cifrato, porta 20615, sottoprotocollo
"ipconnector-protocol") è stato interamente ricostruito tramite reverse
engineering: decompilazione di By-alarm Manager (.NET), delle app Vimar View
(Android/Kotlin) e View Pro (Xamarin/.NET), cattura di rete con mitmproxy,
instrumentazione runtime con Frida, e decifratura dello storage locale di
View Pro (DPAPI + AES). Lavoro svolto in una lunghissima sessione di chat con
Claude (claude.ai) - questo file ne è il riassunto tecnico.

## Struttura del repository

```
custom_components/byalarm_saig/   integrazione Home Assistant (HACS-ready)
  api.py                          client asyncio del protocollo
  config_flow.py                  form di configurazione UI (valida le
                                   credenziali connettendosi davvero al gateway)
  const.py
  coordinator.py                  DataUpdateCoordinator, polling ogni 30s
  alarm_control_panel.py          entita' principale
  binary_sensor.py                zone + stato sistema
  www/byalarm-card.js             card Lovelace custom (incapsula la card
                                   nativa "tile" + feature "alarm-modes",
                                   aggiunge tooltip personalizzati + zone aperte)
  manifest.json, strings.json, translations/
tools/byalarm_sai_client.py       libreria standalone sincrona, per test/debug
                                   da riga di comando senza Home Assistant
hacs.json, README.md
```

## Protocollo (riassunto tecnico)

- Canale: WebSocket TLS su porta **20615**, path `/wss`, sottoprotocollo
  `ipconnector-protocol`. Nessun certificato client richiesto (TLS standard,
  verificato via cattura handshake).
- Flusso di connessione: prima un messaggio `session` su una connessione
  temporanea (comunica IP/porta del client), poi una **nuova** connessione
  per il vero `attach` (con le credenziali). Il gateway non richiede alcuno
  "sblocco" precedente (a differenza del vecchio protocollo legacy sulla
  porta 12000, che dipendeva da VIEW Pro - problema risolto passando del
  tutto a questo canale moderno).
- Messaggio `attach`: richiede `clientinfo.clienttag = "instapp"` e
  `protocolversion = "2.2"` (i valori dell'app **installer**, non
  `"userapp"`/`"2.4"` dell'app consumer - con quelli il gateway rifiuta con
  permission denied).
- Password del dispositivo (per l'attach) e' salvata da View Pro in
  `associations.json` (localstate dell'app, cifrato AES-256-CBC con chiave
  derivata da MD5(email+useruid) via PBKDF2-HMAC-SHA1, salt 8 zero-byte,
  5000 iterazioni - vedi `ApplicationUser.CreateRepositoryPassword` nel
  codice decompilato). **Risultata stabile nei test** (non ruota ad ogni
  attach nonostante il codice suggerisse una possibile rotazione).
- Comandi antifurto (arm/disarm) richiedono un secondo livello di
  autenticazione: un "accesscode" ottenuto cifrando con **RSA/PKCS1v15** (la
  chiave pubblica del gateway, ricevuta nella risposta di attach) la stringa
  `"{sessionToken}:{PIN_utente}:{contatore_progressivo}"`. Il contatore
  parte da 0 e viene incrementato PRIMA di ogni uso (quindi il primo valore
  usato e' 1).
- Comandi `doaction`: ogni azione e' una voce **diretta** nell'array `args`
  (NON annidata sotto una chiave "actions" - errore fatto e corretto durante
  lo sviluppo). Il valore del comando segue l'enum `SFState` del codice
  originale: `SFE_Cmd_Dis`->`"Dis"`, `SFE_Cmd_Int`->`"Int"`,
  `SFE_Cmd_Par`->`"Par"`, **`SFE_Cmd_On`->`"On"`** (non `"Tot"` - bug reale
  gia' corretto, causato da un'assunzione per analogia sbagliata durante la
  ricostruzione).
- Discovery (`sfdiscovery`, categoria `Sai`) richiede nei `params` sia
  `accesscode` che `idambient: []` e `withvalues: true` insieme (altrimenti
  errore "malformed args").
- Keepalive ogni 25 secondi sulla stessa connessione per non far scadere la
  sessione.
- Mappatura codici di errore nota (IPErrorType): 2=permission denied,
  3=invalid target, 7=malformed args, 10=dati non validi per il comando
  (es. valore sbagliato), 21=invalid password, 23=system block (es. il
  pannello rifiuta l'inserimento perche' ci sono zone aperte - comportamento
  normale, non un bug).

## Decisioni di design prese

- Mappatura modi: Totale->Away, Interno->Home, Parziale->Night (scelta
  ragionevole, personalizzabile).
- Solo la prima area/partizione rilevata viene esposta (impianto attuale ne
  ha una sola; multi-area richiederebbe un'estensione).
- La card Lovelace custom incapsula la card nativa Home Assistant
  (`type: tile`, `features: [{type: "alarm-modes"}]`) via
  `window.loadCardHelpers()`, per garantire aspetto identico all'originale;
  aggiunge poi tooltip personalizzati facendo una ricerca ricorsiva
  attraverso gli shadow DOM annidati (attraversando sia lo shadow root che i
  figli "light DOM" di ogni nodo, necessario perche' `ha-card` proietta il
  suo contenuto reale via `<slot>`), e identifica ogni pulsante tramite
  l'attributo `id="option-<mode>"` che `ha-control-select` assegna gia' a
  ciascuna opzione (`option-disarmed`, `option-armed_home`,
  `option-armed_away`, `option-armed_night` - combaciano con le chiavi di
  `labels`). **Confermato funzionante** dopo debug dal vivo via DevTools
  (vedi sotto).
- Le risorse statiche servite da Home Assistant (`www/`) hanno cache
  browser di 31 giorni. L'URL della card include quindi `?v=<versione
  manifest>` (vedi `__init__.py`), cosi' ogni bump di versione forza il
  download della nuova card invece di servire una copia in cache obsoleta.
- La card viene registrata come **risorsa Lovelace persistente**
  (`hass.data["lovelace"].resources`, creata/aggiornata da
  `_async_ensure_lovelace_resource`), non con `add_extra_js_url` (usato
  inizialmente): quest'ultimo tiene lo stato solo in memoria di processo,
  ricalcolato ad ogni riavvio, e si e' rivelato inaffidabile su alcuni
  client (in particolare l'app companion Android mostrava "Custom element
  doesn't exist: byalarm-card" anche quando browser desktop funzionavano).
  Le risorse persistenti sono lo stesso meccanismo usato da HACS per le
  sue card e vengono rilette da ogni client ad ogni connessione. **Nota**:
  lo storage delle risorse potrebbe non essere ancora caricato quando
  l'integrazione fa il proprio setup durante l'avvio (anche con "lovelace"
  come dipendenza dichiarata) - la registrazione va quindi rimandata
  all'evento `EVENT_HOMEASSISTANT_STARTED` quando si parte da un boot
  freddo (altrimenti si rischia di creare una risorsa duplicata invece di
  aggiornare quella esistente - successo dal vivo durante lo sviluppo).

## Come testare senza Home Assistant

```
pip install cryptography
python tools/byalarm_sai_client.py --host <IP> --username <email> \
  --useruid <uid> --duid <duid> --password "<device_password>" \
  --source <source_uid> --pin <pin>
```

## To-do list (stato a fine sessione chat)

1. [FATTO] Bug "Fuori casa" non funzionava (valore sbagliato "Tot" invece
   di "On") - confermato risolto
2. [FATTO] Label personalizzate - risolto tramite card custom; tooltip
   confermati funzionanti dopo debug dal vivo (vedi sopra)
3. [FATTO] Stato zone (binary_sensor)
4. [FATTO] Stato sistema generale (binary_sensor)
5. Integrazione con Alarmo - opzione aggiuntiva/alternativa alla card
   custom, non ancora iniziata; richiederebbe automazioni-ponte
   bidirezionali, mantenendo la nostra entita' nativa come fonte di verita'
6. Guida HACS su dove reperire i parametri di configurazione + disclaimer
   generale (compatibilita'/sicurezza non garantite) - non ancora scritta
7. Pubblicazione su HACS (repository GitHub reale, al momento
   manifest.json/README hanno placeholder "CHANGEME" da sostituire)
8. [FATTO] File con tutti i parametri dell'utente (tenuto FUORI da questo
   repository per sicurezza - non committare mai credenziali reali)
9. Documentazione completa di tutto il lavoro (parzialmente coperta da
   questo file + i commenti nel codice; potrebbe servire un documento
   piu' esteso e discorsivo)
10. [FATTO] Rimossa la variabile d'ambiente di debug (WEBVIEW2_ADDITIONAL_
    BROWSER_ARGUMENTS) e verificata l'esenzione di rete residua (innocua,
    gia' orfana)
11. Nuova automazione (non legata all'integrazione By-alarm): notifica
    Telegram + notifica HA per apertura/chiusura della cassetta delle
    lettere (entita' Aqara "Cassetta Lettere")

## Sicurezza / cose a cui fare attenzione

- MAI committare credenziali reali (password dispositivo, PIN, useruid,
  duid) in questo repository, specialmente se diventa pubblico su GitHub
  per HACS. Tenerle in un file locale non tracciato (vedi `.gitignore`).
- Il meccanismo e' interamente non ufficiale: nessuna garanzia che Vimar non
  cambi il protocollo lato server in futuro.
