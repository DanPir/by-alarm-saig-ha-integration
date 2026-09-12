/**
 * byalarm-card.js
 *
 * Wrapper attorno alla card nativa "alarm-panel" di Home Assistant: aspetto
 * identico all'originale, con l'aggiunta di tooltip personalizzati sui
 * pulsanti (via title/aria-label) e dell'elenco zone aperte/tamper sotto.
 *
 * Nessuna build necessaria: puro JavaScript, caricato direttamente.
 */

class ByAlarmCard extends HTMLElement {
  setConfig(config) {
    if (!config.entity) {
      throw new Error("Devi specificare 'entity' (es. alarm_control_panel.vimar_by_alarm)");
    }
    this._config = config;
    this._labels = {
      disarmed: "Disinserito",
      armed_home: "In casa (INT)",
      armed_away: "Fuori casa (ON)",
      armed_night: "Notte (PAR)",
      ...(config.labels || {}),
    };
    this._buildCard();
  }

  async _buildCard() {
    if (this._buildingPromise) return this._buildingPromise;
    this._buildingPromise = (async () => {
      const helpers = await window.loadCardHelpers();
      this._innerCard = await helpers.createCardElement({
        type: "tile",
        entity: this._config.entity,
        name: this._config.title,
        features: [{ type: "alarm-modes" }],
      });
      if (this._hass) this._innerCard.hass = this._hass;

      this.innerHTML = "";
      this.appendChild(this._innerCard);

      this._zonesEl = document.createElement("div");
      this._zonesEl.style.cssText =
        "padding: 0 16px 16px 16px; font-size: 0.85em; color: var(--secondary-text-color);";
      this.appendChild(this._zonesEl);

      this._built = true;
      this._updateZones();
      // il componente nativo fa il suo render interno in modo asincrono:
      // aspettiamo un attimo prima di provare a patchare i tooltip, e poi
      // ci riproviamo periodicamente finche' non troviamo i pulsanti.
      this._scheduleTooltipPatch();
    })();
    return this._buildingPromise;
  }

  _scheduleTooltipPatch(attempt = 0) {
    clearTimeout(this._tooltipTimer);
    this._tooltipTimer = setTimeout(() => {
      const done = this._patchTooltips();
      if (!done && attempt < 20) {
        this._scheduleTooltipPatch(attempt + 1);
      }
    }, 100);
  }

  set hass(hass) {
    this._hass = hass;
    if (this._innerCard) {
      this._innerCard.hass = hass;
    }
    if (this._built) {
      this._updateZones();
    }
  }

  _updateZones() {
    const stateObj = this._hass?.states[this._config.entity];
    if (!stateObj || !this._zonesEl) return;
    const openZones = stateObj.attributes.zone_aperte || [];
    const tamperZones = stateObj.attributes.zone_tamper || [];
    let html = "";
    if (openZones.length) {
      html += `<div style="color: var(--error-color, #db4437); font-weight:500;">Aperte: ${openZones.join(", ")}</div>`;
    }
    if (tamperZones.length) {
      html += `<div style="color: var(--error-color, #db4437); font-weight:500;">Tamper: ${tamperZones.join(", ")}</div>`;
    }
    this._zonesEl.innerHTML = html || (this._config.show_zones === false ? "" : "<div>Tutte le zone chiuse</div>");
  }

  /**
   * Cerca ricorsivamente dentro tutti gli shadow DOM annidati (la card Tile
   * con "features" nidifica piu' livelli di componenti) i pulsanti/icone
   * della fila di inserimento, e aggiunge un title/aria-label leggibile
   * deducendo la modalita' dall'icona mostrata (piu' affidabile del testo,
   * che dipende dalla lingua). Ritorna true se ha trovato ed etichettato
   * almeno un elemento.
   */
  _patchTooltips() {
    if (!this._innerCard) return false;

    const icons = this._deepQueryAll(this._innerCard, "ha-icon, ha-svg-icon, ha-state-icon");
    console.log("[byalarm-card] icone trovate:", icons.length, icons);
    if (!icons.length) return false;

    let labeled = 0;
    icons.forEach((iconEl) => {
      const icon = iconEl.getAttribute("icon") || iconEl.icon || "";
      console.log("[byalarm-card] icona:", icon, iconEl);
      const label = this._labelForIcon(icon);
      if (!label) return;
      // il title/aria-label vanno sull'elemento cliccabile (il genitore piu'
      // vicino con un ruolo interattivo), non sulla sola icona
      const target =
        iconEl.closest("ha-control-button, button, [role='button'], [role='option'], [role='radio']") || iconEl;
      target.title = label;
      target.setAttribute("aria-label", label);
      labeled++;
    });
    console.log("[byalarm-card] pulsanti etichettati:", labeled);
    return labeled > 0;
  }

  _deepQueryAll(root, selector) {
    const scope = root.shadowRoot || root;
    let results = Array.from(scope.querySelectorAll(selector));
    const all = scope.querySelectorAll("*");
    all.forEach((el) => {
      if (el.shadowRoot) {
        results = results.concat(this._deepQueryAll(el, selector));
      }
    });
    return results;
  }

  _labelForIcon(icon) {
    if (!icon) return null;
    if (icon.includes("shield-off")) return this._labels.disarmed;
    if (icon.includes("shield-home") || icon.includes("home")) return this._labels.armed_home;
    if (icon.includes("shield-lock") || icon.includes("lock")) return this._labels.armed_away;
    if (icon.includes("shield-moon") || icon.includes("moon") || icon.includes("weather-night")) return this._labels.armed_night;
    return null;
  }

  getCardSize() {
    return (this._innerCard && this._innerCard.getCardSize ? this._innerCard.getCardSize() : 4) + 1;
  }

  static getStubConfig(hass) {
    const entities = Object.keys(hass.states).filter((e) => e.startsWith("alarm_control_panel."));
    return { entity: entities[0] || "" };
  }
}

customElements.define("byalarm-card", ByAlarmCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "byalarm-card",
  name: "Vimar By-alarm Card",
  description: "Card nativa Home Assistant con tooltip personalizzati e zone aperte",
});
