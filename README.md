# Vimar By-alarm (SAIG) for Home Assistant

Custom integration to control a **Vimar By-Alarm** intrusion system (**SAIG**
model gateway) directly from Home Assistant, without needing the VIEW
app. This is not an official Vimar integration.

## What you need before starting

To configure the integration you'll need the following data from your
system:

| Data | Where to find it |
|---|---|
| SAIG gateway IP address | Home router / VIEW app ("Connection parameters") |
| Vimar account email | Your Vimar account |
| User UID | `sub` claim of the account's JWT token (requires a one-time analysis of the app's traffic) |
| Device DUID | The identifier of the **SAIG** model gateway (e.g. `A32250FBB01025`) among the devices associated with your system (VIEW app -> Settings -> System Info) |
| Device password | Saved (encrypted) by VIEW Pro in `associations.json` |
| PIN | The user code you use on the alarm's physical keypad |

Retrieving the `User UID` and `Device password` requires technical analysis
of the official app's traffic/storage (they're not normally visible to the
user) - see the step-by-step guide below. If you're not sure how to
proceed, check the repository issues.

### Getting the User UID

This is the `sub` claim of your Vimar account's JWT access token, issued
whenever you log into a Vimar app. Either **View Pro** (Windows) or the
**View** mobile app work for this, since both authenticate against the
same cloud account:

1. Set up a traffic-capture proxy (e.g. [mitmproxy](https://mitmproxy.org/)
   or similar) as the system/Wi-Fi proxy for the device running the app,
   with its certificate trusted so HTTPS traffic can be inspected.
2. Log into the app while capturing; find the authentication response
   containing a JWT (a long string made of three base64url segments
   separated by `.`).
3. Decode the JWT payload (e.g. at [jwt.io](https://jwt.io), or any
   offline base64url decoder if you'd rather not paste a real token into
   a website) and read the `sub` claim - that's your User UID.

### Getting the Device password

Unlike the User UID, we've only verified this one path: **View Pro**
(Windows) saves it, AES-encrypted, in a local `associations.json` file.
We haven't investigated whether the mobile View app stores an equivalent
locally.

1. Install View Pro on Windows and log in with your Vimar account; it
   downloads and locally caches the password for every device associated
   with your account.
2. Locate `associations.json` in View Pro's local app data folder (the
   exact path varies by version - search your Windows user profile for a
   file with that name after logging in).
3. Run the included decryption tool with your email and the User UID from
   the previous step:
   ```bash
   pip install cryptography
   python tools/decrypt_view_pro_password.py associations.json \
     --email your@account.com --useruid <uid-from-previous-step>
   ```
   It prints the decrypted password for every associated device (by
   DUID) - the one matching your SAIG gateway's DUID is the
   `device_password` this integration needs.

This whole process is inherently fragile: it depends on the exact local
storage format and encryption scheme View Pro happens to use today (see
`tools/decrypt_view_pro_password.py` for the full reverse-engineered
recipe), and Vimar could change it at any time without notice.

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
  armed_home: "Perimeter (INT)"
  armed_away: "Away (ON)"
  armed_night: "Night (PAR)"
icons:
  armed_home: "mdi:dots-square"
```

Labels and icons are customizable: edit the `labels`/`icons` blocks with
whatever text/icon you prefer (only `armed_home` has a custom icon by
default). If any zone is open or tampered, the three arm buttons (not
Disarm) turn to the theme's warning color and their tooltip gets the list
of affected zones appended — buttons are never disabled, since the
protocol doesn't expose which arm mode(s) a given zone would actually
block. Set `show_zones: false` to turn this warning off entirely.

## Optional: bridging to Alarmo

If you use [Alarmo](https://github.com/nielsfaber/alarmo) (a popular
HACS alarm panel integration, e.g. for its multi-user codes or its own
automation/notification system), you can mirror this integration's real
alarm state onto an Alarmo panel with two small automations, keeping the
Vimar entity as the single source of truth: Alarmo becomes a live copy
you interact with, but every command still goes through this integration
to the real gateway. Replace the entity IDs with your own.

```yaml
# Vimar -> Alarmo: mirror the real state, no delay/sensor checks (it's
# just a reflection of a panel that's already armed/disarmed for real).
- alias: "Vimar By-Alarm -> Alarmo (sync state)"
  trigger:
    - platform: state
      entity_id: alarm_control_panel.vimar_by_alarm
      to: ["disarmed", "armed_away", "armed_home", "armed_night"]
  condition:
    - condition: template
      value_template: "{{ states('alarm_control_panel.alarmo') != trigger.to_state.state }}"
  action:
    - choose:
        - conditions: "{{ trigger.to_state.state == 'disarmed' }}"
          sequence:
            - action: alarmo.disarm
              target: { entity_id: alarm_control_panel.alarmo }
        - conditions: "{{ trigger.to_state.state == 'armed_away' }}"
          sequence:
            - action: alarmo.arm
              target: { entity_id: alarm_control_panel.alarmo }
              data: { mode: away, skip_delay: true, force: true }
        - conditions: "{{ trigger.to_state.state == 'armed_home' }}"
          sequence:
            - action: alarmo.arm
              target: { entity_id: alarm_control_panel.alarmo }
              data: { mode: home, skip_delay: true, force: true }
        - conditions: "{{ trigger.to_state.state == 'armed_night' }}"
          sequence:
            - action: alarmo.arm
              target: { entity_id: alarm_control_panel.alarmo }
              data: { mode: night, skip_delay: true, force: true }

# Alarmo -> Vimar: forward whatever Alarmo was told (e.g. from its own
# card or a user code) to the real gateway.
- alias: "Alarmo -> Vimar By-Alarm (forward command)"
  trigger:
    - platform: state
      entity_id: alarm_control_panel.alarmo
      to: ["disarmed", "armed_away", "armed_home", "armed_night"]
  condition:
    - condition: template
      value_template: "{{ states('alarm_control_panel.vimar_by_alarm') != trigger.to_state.state }}"
  action:
    - choose:
        - conditions: "{{ trigger.to_state.state == 'disarmed' }}"
          sequence:
            - action: alarm_control_panel.alarm_disarm
              target: { entity_id: alarm_control_panel.vimar_by_alarm }
        - conditions: "{{ trigger.to_state.state == 'armed_away' }}"
          sequence:
            - action: alarm_control_panel.alarm_arm_away
              target: { entity_id: alarm_control_panel.vimar_by_alarm }
        - conditions: "{{ trigger.to_state.state == 'armed_home' }}"
          sequence:
            - action: alarm_control_panel.alarm_arm_home
              target: { entity_id: alarm_control_panel.vimar_by_alarm }
        - conditions: "{{ trigger.to_state.state == 'armed_night' }}"
          sequence:
            - action: alarm_control_panel.alarm_arm_night
              target: { entity_id: alarm_control_panel.vimar_by_alarm }
```

The condition on each automation ("only act if the target doesn't already
match") is what prevents a feedback loop between the two - each direction
settles after one hop instead of bouncing back and forth. Note the real
gateway round-trip (session + PIN encryption + command) can take several
seconds, longer than Alarmo's own instant state changes.

Alarmo has no direct way to *become* `triggered` from the outside (that
only happens from its own sensors), so a genuine alarm on the Vimar side
won't automatically flip Alarmo to `triggered`. If you want Vimar's real
trigger to run Alarmo-side reactions (sirens, notifications, etc.), add a
third automation that fires on `alarm_control_panel.vimar_by_alarm`
turning `triggered` and calls those same actions/scripts directly.

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
- The device password saved by VIEW Pro was stable in testing, but the
  gateway could theoretically regenerate it: if that happens, it would need
  to be updated in the integration's configuration

## Security

Credentials (including PIN and device password) are stored in the Home
Assistant configuration like any other integration. No data is sent to
external services: communication happens directly over the LAN between
Home Assistant and the gateway.
