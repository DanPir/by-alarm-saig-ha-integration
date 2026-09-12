/**
 * byalarm-card.js
 *
 * Wrapper attorno alla card nativa "tile" + feature "alarm-modes" di Home
 * Assistant: aspetto e dimensioni identici all'originale, con l'aggiunta di
 * tooltip/etichette personalizzate sui pulsanti, un'icona custom per il modo
 * "Perimetrale" e il riepilogo zone aperte/tamper accanto allo stato.
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
   * Applica sia le patch statiche (tooltip/icone dei pulsanti) sia
   * l'aggiornamento del riepilogo zone. Ritorna true se entrambe sono
   * andate a buon fine (cioe' gli elementi target esistono gia' nel DOM).
   */
  _applyPatches() {
    const optionsPatched = this._patchOptions();
    const zonesPatched = this._patchZonesInfo();
    return optionsPatched && zonesPatched;
  }

  /**
   * Cerca ricorsivamente dentro tutti gli shadow DOM annidati (la card Tile
   * con "features" nidifica piu' livelli di componenti) le opzioni della
   * fila di inserimento (ha-control-select le rende come
   * `[role="radio"] id="option-<mode>"`, dove <mode> combacia con le chiavi
   * di `this._labels`/`this._icons`) e ne sovrascrive title/aria-label e,
   * dove configurata, l'icona. Usare l'id invece dell'icona per il
   * riconoscimento della modalita' e' necessario perche' le icone native qui
   * sono renderizzate con un path SVG raw (nessun attributo "icon"
   * leggibile). Ritorna true se ha trovato ed etichettato almeno un
   * elemento.
   */
  _patchOptions() {
    if (!this._innerCard) return false;

    const options = this._deepQueryAll(this._innerCard, '[role="radio"][id^="option-"]');
    if (!options.length) return false;

    let labeled = 0;
    options.forEach((optEl) => {
      const mode = optEl.id.replace(/^option-/, "");
      const label = this._labels[mode];
      if (label) {
        optEl.title = label;
        optEl.setAttribute("aria-label", label);
        labeled++;
      }

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
    });
    return labeled > 0;
  }

  /**
   * Crea (una sola volta) e aggiorna un <div> aggiuntivo, ultimo figlio
   * "light DOM" di <ha-card>: lo shadow root di ha-card e' un semplice
   * <slot> di default, quindi qualunque figlio in piu' che aggiungiamo
   * viene renderizzato in fondo alla card, sotto la riga dei pulsanti
   * (icona/nome/stato e la feature alarm-modes sono dentro
   * <ha-tile-container>, che resta il primo figlio). Non tocca nulla che
   * la tile card gestisce, quindi sopravvive ai suoi ri-render interni.
   */
  _patchZonesInfo() {
    if (!this._innerCard) return false;

    const card = this._deepQueryAll(this._innerCard, "ha-card")[0];
    if (!card) return false;

    if (!this._zonesEl || this._zonesEl.parentElement !== card) {
      this._zonesEl = document.createElement("div");
      this._zonesEl.className = "byalarm-zones-info";
      this._zonesEl.style.cssText = "padding: 0 16px 16px 16px; font-size: 0.85em;";
      card.appendChild(this._zonesEl);
    }

    const stateObj = this._hass?.states[this._config.entity];
    if (!stateObj) return true;

    const openZones = stateObj.attributes.zone_aperte || [];
    const tamperZones = stateObj.attributes.zone_tamper || [];
    const parts = [];
    if (openZones.length) parts.push(`Aperte: ${openZones.join(", ")}`);
    if (tamperZones.length) parts.push(`Tamper: ${tamperZones.join(", ")}`);

    const hasIssue = parts.length > 0;
    let text = parts.join(" · ");
    if (!text && this._config.show_zones !== false) {
      text = "Tutte le zone chiuse";
    }

    this._zonesEl.textContent = text;
    this._zonesEl.style.color = hasIssue ? "var(--error-color, #db4437)" : "var(--secondary-text-color)";
    this._zonesEl.style.fontWeight = hasIssue ? "500" : "normal";
    return true;
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
    return (this._innerCard && this._innerCard.getCardSize ? this._innerCard.getCardSize() : 4) + 1;
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
