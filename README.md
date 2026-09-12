# Vimar By-alarm (SAIG) for Home Assistant

Custom integration to control a **Vimar By-alarm** intrusion system (**SAIG**
model gateway) directly from Home Assistant, without needing the VIEW Pro
app.

The protocol used by the gateway (encrypted WebSocket on port 20615) was
fully reverse-engineered: decompilation of the official apps, network
traffic analysis, and runtime instrumentation. This is not an official
Vimar integration.

## What you need before starting

To configure the integration you'll need the following data from your
system:

| Data | Where to find it |
|---|---|
| Gateway IP address | Home router / VIEW app ("Connection parameters") |
| Vimar account email | Your VIEW/VIEW Pro account |
| User UID | `sub` claim of the account's JWT token (requires a one-time analysis of the app's traffic) |
| Device DUID | The identifier of the **SAIG** model gateway (e.g. `A32250FBB01025`) among the devices associated with your system |
| Device password | Saved (encrypted) by VIEW Pro in `associations.json` |
| PIN | The user code you use on the alarm's physical keypad |

Retrieving the `User UID` and `Device password` requires technical analysis
of the official app's traffic/storage (they're not normally visible to the
user). If you're not sure how to proceed, check the repository issues.

## Installation

### Via HACS

1. HACS -> Integrations -> menu (⋮) -> Custom repositories
2. Add this repository's URL, category "Integration"
3. Search for "Vimar By-alarm (SAIG)" and install
4. Restart Home Assistant

### Manual

Copy the `custom_components/byalarm_saig` folder into the
`custom_components` folder of your Home Assistant installation, then
restart.

## Configuration

Settings -> Devices & services -> Add integration -> search for
"Vimar By-alarm" and follow the wizard.

## Features

- `alarm_control_panel` entity with real-time state (updated every 30
  seconds)
- Commands: Disarm, Full Arm (Away), Home Arm (Home), Partial Arm (Night)
- Extra attributes: open zones, tampered zones, bypassed zones, maintenance
  status

## Custom card

The integration automatically registers a custom Lovelace card
(`byalarm-card`), with readable labels instead of the standard card's
generic "Armed Home/Away/Night".

To use it, manually add a card to your dashboard (card YAML mode, or "Add
card" -> search for "Vimar By-alarm Card" if it shows up in the list):

```yaml
type: custom:byalarm-card
entity: alarm_control_panel.vimar_by_alarm_sirene_2_villetta_3
show_zones: true
labels:
  disarmed: "Disarmed"
  armed_home: "Home (INT)"
  armed_away: "Away (ON)"
  armed_night: "Night (PAR)"
```

Labels are customizable: edit the `labels` block with whatever text you
prefer. `show_zones: false` hides the list of open/tampered zones below the
buttons.

## Available entities

- `alarm_control_panel.*` — state and commands for the alarm area
- `binary_sensor.*` — one pair (open/tamper) for each detected zone, plus
  7 general status sensors (alarm, memory, device tamper, maintenance)

## Arming mode mapping

| By-alarm mode | Home Assistant state |
|---|---|
| Totale | Armed Away |
| Interno | Armed Home |
| Parziale | Armed Night |

## Known limitations

- Only the first detected area/partition is exposed as an entity
  (multi-area systems would require an extension)
- Unofficial, not supported by Vimar
- The device password saved by VIEW Pro was stable in testing, but the
  gateway could theoretically regenerate it: if that happens, it would need
  to be updated in the integration's configuration

## Security

Credentials (including PIN and device password) are stored in the Home
Assistant configuration like any other integration. No data is sent to
external services: communication happens directly over the LAN between
Home Assistant and the gateway.
