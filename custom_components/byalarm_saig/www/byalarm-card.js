/**
 * byalarm-card.js
 *
 * Wrapper attorno alla card nativa "tile" + feature "alarm-modes" di Home
 * Assistant: aspetto e dimensioni identici all'originale, con l'aggiunta di
 * tooltip/etichette personalizzate sui pulsanti, un'icona custom per il modo
 * "Perimetrale" e un avviso (icona arancione + elenco zone nel tooltip) sui
 * pulsanti di inserimento quando ci sono zone aperte o in tamper.
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
      armed_home: "Perimetrale (INT)",
      armed_away: "Fuori casa (ON)",
      armed_night: "Notte (PAR)",
      ...(config.labels || {}),
    };
    this._icons = {
      armed_home: "mdi:dots-square",
      ...(config.icons || {}),
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

      this._built = true;
      // il componente nativo fa il suo render interno in modo asincrono:
      // aspettiamo un attimo prima di provare a patchare pulsanti/zone, e ci
      // riproviamo periodicamente finche' non troviamo gli elementi giusti.
      this._schedulePatch();
    })();
    return this._buildingPromise;
  }

  _schedulePatch(attempt = 0) {
    clearTimeout(this._patchTimer);
    this._patchTimer = setTimeout(() => {
      const done = this._applyPatches();
      if (!done && attempt < 20) {
        this._schedulePatch(attempt + 1);
      }
    }, 100);
  }

  set hass(hass) {
    this._hass = hass;
    if (this._innerCard) {
      this._innerCard.hass = hass;
    }
    if (this._built) {
      // il componente nativo puo' ri-renderizzare i propri elementi interni
      // ad ogni aggiornamento di stato, perdendo le nostre patch: le
      // riapplichiamo quindi ad ogni update (sono idempotenti ed economiche).
      this._applyPatches();
    }
  }

  /**
   * Applica le patch (tooltip/icone/avviso zone) ai pulsanti. Ritorna true
   * se gli elementi target esistono gia' nel DOM (per far smettere il
   * retry di _schedulePatch).
   */
  _applyPatches() {
    return this._patchOptions();
  }

  /**
   * Cerca ricorsivamente dentro tutti gli shadow DOM annidati (la card Tile
   * con "features" nidifica piu' livelli di componenti) le opzioni della
   * fila di inserimento (ha-control-select le rende come
   * `[role="radio"] id="option-<mode>"`, dove <mode> combacia con le chiavi
   * di `this._labels`/`this._icons`) e ne sovrascrive title/aria-label,
   * dove configurata l'icona, e - se ci sono zone aperte/tamper - colora
   * l'icona dei pulsanti di inserimento (non "Disinserito") di arancione,
   * aggiungendo l'elenco delle zone al tooltip. Il protocollo non espone
   * quali zone blocchino quale specifica modalita' (verificato via dump
   * live della discovery: nessun campo di raggruppamento), quindi l'avviso
   * e' lo stesso su tutti e tre i pulsanti di inserimento, senza
   * disabilitarli. Usare l'id invece dell'icona per il riconoscimento
   * della modalita' e' necessario perche' le icone native qui sono
   * renderizzate con un path SVG raw (nessun attributo "icon" leggibile).
   * Ritorna true se ha trovato ed etichettato almeno un elemento.
   */
  _patchOptions() {
    if (!this._innerCard) return false;

    const options = this._deepQueryAll(this._innerCard, '[role="radio"][id^="option-"]');
    if (!options.length) return false;

    const stateObj = this._hass?.states[this._config.entity];
    const openZones = (this._config.show_zones !== false && stateObj?.attributes.zone_aperte) || [];
    const tamperZones = (this._config.show_zones !== false && stateObj?.attributes.zone_tamper) || [];
    const warningParts = [];
    if (openZones.length) warningParts.push(`Zone aperte: ${openZones.join(", ")}`);
    if (tamperZones.length) warningParts.push(`Zone in tamper: ${tamperZones.join(", ")}`);
    const warningText = warningParts.join(" · ");

    let labeled = 0;
    options.forEach((optEl) => {
      const mode = optEl.id.replace(/^option-/, "");
      const isArmButton = mode !== "disarmed";
      const showWarning = isArmButton && warningText.length > 0;

      const iconName = this._icons[mode];
      if (iconName) {
        const currentIcon = optEl.querySelector("ha-svg-icon, ha-icon");
        const alreadyPatched = currentIcon && currentIcon.tagName === "HA-ICON" && currentIcon.icon === iconName;
        if (currentIcon && !alreadyPatched) {
          const replacement = document.createElement("ha-icon");
          replacement.icon = iconName;
          currentIcon.replaceWith(replacement);
        }
      }

      const label = this._labels[mode];
      if (label) {
        const finalLabel = showWarning ? `${label} — ${warningText}` : label;
        optEl.title = finalLabel;
        optEl.setAttribute("aria-label", finalLabel);
        labeled++;
      }

      const iconEl = optEl.querySelector("ha-svg-icon, ha-icon");
      if (iconEl) {
        iconEl.style.color = showWarning ? "var(--warning-color, #ff9800)" : "";
      }
    });
    return labeled > 0;
  }

  /**
   * Attraversa sia lo shadow DOM che i figli "light DOM" di ogni nodo:
   * necessario perche' alcuni componenti (es. ha-card) hanno uno shadow
   * root che e' solo un <slot>, mentre il contenuto vero e proprio resta
   * nel light DOM dell'elemento e viene solo proiettato nello slot.
   */
  _deepQueryAll(root, selector) {
    const results = [];
    const visit = (node) => {
      if (node.matches && node.matches(selector)) results.push(node);
      if (node.shadowRoot) {
        Array.from(node.shadowRoot.children).forEach(visit);
      }
      Array.from(node.children || []).forEach(visit);
    };
    visit(root);
    return results;
  }

  getCardSize() {
    return this._innerCard && this._innerCard.getCardSize ? this._innerCard.getCardSize() : 4;
  }

  /**
   * Delega alla card "tile" nativa: senza questo, nelle dashboard a
   * sezioni/griglia Home Assistant non sa quanto e' larga di default questa
   * card custom e le assegna una larghezza generica (piu' larga
   * dell'originale).
   */
  getGridOptions() {
    if (this._innerCard && this._innerCard.getGridOptions) {
      return this._innerCard.getGridOptions();
    }
    return { columns: 6, rows: 2, min_columns: 6, min_rows: 2 };
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
