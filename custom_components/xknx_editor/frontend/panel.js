// XKNX Editor panel: the editor's web UI in an iframe. Like the Ingress of Home Assistant apps,
// the panel gets a session over Home Assistant's websocket and stores it in a cookie that only
// the proxy below /api/xknx_editor/ receives; it keeps the session live while it is open.

const COOKIE = "xknx_editor_session";
const COOKIE_PATH = "/api/xknx_editor/";
const UI_URL = "/api/xknx_editor/ui/";
const RENEW_MS = 60 * 1000;
// The session of this browser tab, kept while it is live: Home Assistant may create the panel
// more than once, and the editor gives control back to a returning session (a reload) only.
const STORAGE_KEY = "xknx_editor_session";

function storedSession() {
  try {
    return sessionStorage.getItem(STORAGE_KEY);
  } catch (err) {
    return null;
  }
}

function storeSession(session) {
  try {
    sessionStorage.setItem(STORAGE_KEY, session);
  } catch (err) {
    // Without storage every page load gets a session of its own.
  }
}

// One request at a time for all panels of this page: the stored session when it is still live,
// else a new one.
let sessionRequest = null;

function tabSession(hass, renew = false) {
  if (!sessionRequest || renew) {
    sessionRequest = (async () => {
      const stored = renew ? null : storedSession();
      if (stored) {
        const result = await hass.callWS({ type: "xknx_editor/session/validate", session: stored });
        if (result.valid) return stored;
      }
      const created = await hass.callWS({ type: "xknx_editor/session" });
      storeSession(created.session);
      return created.session;
    })();
    sessionRequest.catch(() => {
      sessionRequest = null;
    });
  }
  return sessionRequest;
}

class XknxEditorPanel extends HTMLElement {
  constructor() {
    super();
    this._hass = null;
    this._narrow = false;
    this._session = null;
    this._timer = null;
    this._started = false;
    this.attachShadow({ mode: "open" });
  }

  set hass(hass) {
    this._hass = hass;
    const menu = this.shadowRoot.querySelector("ha-menu-button");
    if (menu) menu.hass = hass;
    if (!this._started && this.isConnected) this._start();
  }

  set narrow(narrow) {
    this._narrow = narrow;
    this._render();
  }

  connectedCallback() {
    if (this._hass && !this._started) this._start();
  }

  disconnectedCallback() {
    clearInterval(this._timer);
    this._timer = null;
    this._started = false;
  }

  async _start() {
    this._started = true;
    try {
      this._useSession(await tabSession(this._hass));
    } catch (err) {
      this._showError(err);
      return;
    }
    this._render();
    this._timer = setInterval(() => this._renew(), RENEW_MS);
  }

  _useSession(session) {
    this._session = session;
    const secure = location.protocol === "https:" ? ";Secure" : "";
    document.cookie = `${COOKIE}=${this._session};path=${COOKIE_PATH};SameSite=Strict${secure}`;
  }

  async _renew() {
    try {
      const result = await this._hass.callWS({
        type: "xknx_editor/session/validate",
        session: this._session,
      });
      if (!result.valid) {
        this._useSession(await tabSession(this._hass, true));
        const frame = this.shadowRoot.querySelector("iframe");
        if (frame) frame.src = UI_URL;
      }
    } catch (err) {
      // Home Assistant restarts: the next round asks again.
    }
  }

  _showError(err) {
    this.shadowRoot.innerHTML = "";
    const text = document.createElement("p");
    text.style.padding = "16px";
    text.textContent = `XKNX Editor: ${err && err.message ? err.message : err}`;
    this.shadowRoot.append(text);
  }

  _render() {
    if (!this._session) return;
    let frame = this.shadowRoot.querySelector("iframe");
    if (!frame) {
      const style = document.createElement("style");
      style.textContent = `
        :host { display: flex; flex-direction: column; height: 100vh; height: 100dvh; }
        .toolbar { display: none; align-items: center; height: var(--header-height, 56px);
          padding: 0 4px; background: var(--app-header-background-color);
          color: var(--app-header-text-color, white); font-size: 20px; }
        :host([narrow]) .toolbar { display: flex; }
        .title { margin-left: 8px; }
        iframe { border: 0; width: 100%; flex: 1; display: block; }`;
      const toolbar = document.createElement("div");
      toolbar.className = "toolbar";
      const menu = document.createElement("ha-menu-button");
      menu.hass = this._hass;
      menu.narrow = true;
      const title = document.createElement("div");
      title.className = "title";
      title.textContent = "XKNX Editor";
      toolbar.append(menu, title);
      frame = document.createElement("iframe");
      frame.title = "XKNX Editor";
      frame.allow = "clipboard-read; clipboard-write; fullscreen";
      frame.src = UI_URL;
      this.shadowRoot.append(style, toolbar, frame);
    }
    this.toggleAttribute("narrow", this._narrow);
  }
}

customElements.define("xknx-editor-panel", XknxEditorPanel);
