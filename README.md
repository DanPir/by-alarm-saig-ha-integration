# Vimar By-alarm (SAIG) per Home Assistant

Integrazione custom per controllare un sistema antintrusione **Vimar By-alarm**
(gateway modello **SAIG**) direttamente da Home Assistant, senza bisogno
dell'app VIEW Pro.

Il protocollo usato dal gateway (WebSocket cifrato su porta 20615) e' stato
interamente ricostruito tramite reverse engineering: decompilazione delle app
ufficiali, analisi del traffico di rete e instrumentazione runtime. Non e'
un'integrazione ufficiale Vimar.

## Cosa serve prima di iniziare

Per configurare l'integrazione ti servono questi dati del tuo impianto:

| Dato | Dove si trova |
|---|---|
| Indirizzo IP del gateway | Router di casa / app VIEW ("Parametri di connessione") |
| Email account Vimar | Il tuo account VIEW/VIEW Pro |
| User UID | Claim `sub` del token JWT dell'account (richiede un'analisi una tantum del traffico dell'app) |
| DUID del dispositivo | L'identificativo del gateway modello **SAIG** (es. `A32250FBB01025`) tra i dispositivi associati al tuo impianto |
| Password del dispositivo | Salvata (cifrata) da VIEW Pro in `associations.json` |
| PIN | Il codice utente che usi sulla tastiera fisica dell'antifurto |

Recuperare `User UID` e `Password del dispositivo` richiede un'analisi tecnica
del traffico/storage dell'app ufficiale (non sono normalmente visibili
all'utente). Se non sai come procedere, consulta le issue del repository.

## Installazione

### Tramite HACS

1. HACS -> Integrazioni -> menu (⋮) -> Repository personalizzati
2. Aggiungi l'URL di questo repository, categoria "Integrazione"
3. Cerca "Vimar By-alarm (SAIG)" e installa
4. Riavvia Home Assistant

### Manuale

Copia la cartella `custom_components/byalarm_saig` nella cartella
`custom_components` della tua installazione Home Assistant, poi riavvia.

## Configurazione

Impostazioni -> Dispositivi e servizi -> Aggiungi integrazione -> cerca
"Vimar By-alarm" e segui la procedura guidata.

## Funzionalita'

- Entita' `alarm_control_panel` con stato in tempo reale (aggiornamento ogni
  30 secondi)
- Comandi: Disinserisci, Inserimento Totale (Away), Inserimento Interno
  (Home), Inserimento Parziale (Night)
- Attributi extra: zone aperte, zone in tamper, zone escluse, stato
  manutenzione

## Card personalizzata

L'integrazione registra automaticamente una card Lovelace custom
(`byalarm-card`), con etichette leggibili invece di quelle generiche
"Armed Home/Away/Night" della card standard.

Per usarla, aggiungi manualmente una card alla tua dashboard (modalita' YAML
della card, oppure "Aggiungi card" -> cerca "Vimar By-alarm Card" se compare
nell'elenco):

```yaml
type: custom:byalarm-card
entity: alarm_control_panel.vimar_by_alarm_sirene_2_villetta_3
show_zones: true
labels:
  disarmed: "Disinserito"
  armed_home: "In casa (INT)"
  armed_away: "Fuori casa (ON)"
  armed_night: "Notte (PAR)"
```

Le etichette sono personalizzabili: modifica il blocco `labels` con il testo
che preferisci. `show_zones: false` nasconde la lista delle zone
aperte/tamper sotto ai pulsanti.

## Entita' disponibili

- `alarm_control_panel.*` — stato e comandi dell'area antifurto
- `binary_sensor.*` — una coppia (aperta/tamper) per ogni zona rilevata, piu'
  7 sensori di stato generale (allarme, memoria, tamper dispositivo,
  manutenzione)

## Mappatura dei modi di inserimento

| Modo By-alarm | Stato Home Assistant |
|---|---|
| Totale | Armed Away |
| Interno | Armed Home |
| Parziale | Armed Night |

## Limitazioni note

- Solo la prima area/partizione rilevata viene esposta come entita' (impianti
  multi-area richiederebbero un'estensione)
- Non ufficiale, non supportata da Vimar
- La password del dispositivo salvata da VIEW Pro e' risultata stabile nei
  test, ma il gateway potrebbe in teoria rigenerarla: in tal caso andrebbe
  aggiornata nella configurazione dell'integrazione

## Sicurezza

Le credenziali (inclusi PIN e password del dispositivo) sono salvate nella
configurazione di Home Assistant come qualsiasi altra integrazione. Nessun
dato viene inviato a servizi esterni: la comunicazione avviene direttamente
in LAN tra Home Assistant e il gateway.
