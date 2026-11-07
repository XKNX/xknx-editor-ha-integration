// XKNX Editor panel: the editor's web UI in an iframe. Like the Ingress of Home Assistant apps,
// the panel gets a session over Home Assistant's websocket and stores it in a cookie that only
// the proxy below /api/xknx_editor/ receives; it keeps the session live while it is open.

const COOKIE = "xknx_editor_session";
const COOKIE_PATH = "/api/xknx_editor/";
const UI_URL = "/api/xknx_editor/ui/";
const RENEW_MS = 60 * 1000;
// The editor's page reports a request without a live session (proxy.py); the panel then loads it
// again with a renewed session, but not more often than this (without cookies it never helps).
const SIGNED_OUT = "xknx_editor/signed_out";
const RELOAD_GAP_MS = 10 * 1000;
// The session of this browser tab, kept while it is live: Home Assistant may create the panel
// more than once, and the editor gives control back to a returning session (a reload) only.
const STORAGE_KEY = "xknx_editor_session";
// The browser tab's own id: its new sessions stay the same browser for the editor (proxy.py).
const TAB_KEY = "xknx_editor_tab";
// How often the editor's page asked in a row (proxy.py), reset once a session works.
const TRIES_KEY = "xknx_editor_signin_tries";
// After a failed renewal (Home Assistant not reachable yet), the next try.
const RETRY_MS = 2 * 1000;
// The element of the page asking for a session (proxy.py).
const SIGNED_OUT_ID = "xknx-editor-signed-out";

function tabId() {
  try {
    let id = sessionStorage.getItem(TAB_KEY);
    if (!id) {
      const bytes = crypto.getRandomValues(new Uint8Array(16));
      id = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
      sessionStorage.setItem(TAB_KEY, id);
    }
    return id;
  } catch (err) {
    return null;
  }
}

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
// else a new one. ``stale``: the session that stopped working; a new one is made only when no
// other panel of this tab made one in its place already (or is making it).
let sessionRequest = null;
let sessionValue = null;

function tabSession(hass, stale = null) {
  if (!sessionRequest || (stale !== null && sessionValue === stale)) {
    const renew = stale !== null;
    sessionValue = null;
    sessionRequest = (async () => {
      const stored = renew ? null : storedSession();
      if (stored) {
        const result = await hass.callWS({ type: "xknx_editor/session/validate", session: stored });
        if (result.valid) {
          sessionValue = stored;
          return stored;
        }
      }
      const tab = tabId();
      const created = await hass.callWS(
        tab ? { type: "xknx_editor/session", tab } : { type: "xknx_editor/session" }
      );
      storeSession(created.session);
      sessionValue = created.session;
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
    this._reloaded = 0;
    this._reloadTimer = null;
    this._retryTimer = null;
    this._connection = null;
    this._onMessage = (event) => {
      const frame = this.shadowRoot.querySelector("iframe");
      if (
        frame &&
        event.source === frame.contentWindow &&
        event.origin === location.origin &&
        event.data &&
        event.data.type === SIGNED_OUT
      ) {
        this._renew(true);
      }
    };
    // Timers of a hidden tab are slowed down or stopped, so its session may have run out.
    this._onVisible = () => {
      if (document.visibilityState === "visible") this._renew();
    };
    // Home Assistant is back after a restart, without the sessions it had.
    this._onReady = () => this._renew();
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
    clearTimeout(this._reloadTimer);
    this._reloadTimer = null;
    clearTimeout(this._retryTimer);
    this._retryTimer = null;
    this._started = false;
    window.removeEventListener("message", this._onMessage);
    document.removeEventListener("visibilitychange", this._onVisible);
    if (this._connection) this._connection.removeEventListener("ready", this._onReady);
    this._connection = null;
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
    window.addEventListener("message", this._onMessage);
    document.addEventListener("visibilitychange", this._onVisible);
    this._connection = this._hass.connection || null;
    if (this._connection) this._connection.addEventListener("ready", this._onReady);
  }

  _useSession(session) {
    this._session = session;
    const secure = location.protocol === "https:" ? ";Secure" : "";
    document.cookie = `${COOKIE}=${this._session};path=${COOKIE_PATH};SameSite=Strict${secure}`;
  }

  // Keeps the session live, takes a new one when it ran out, and then loads the editor again;
  // `signedOut`: the editor's page got no answer for want of a session, so it is loaded again
  // either way (its cookie may be gone while the session is still live).
  async _renew(signedOut = false) {
    if (!this._started || !this._session) return;
    try {
      const result = await this._hass.callWS({
        type: "xknx_editor/session/validate",
        session: this._session,
      });
      if (result.valid) {
        if (signedOut) this._useSession(this._session);
      } else {
        this._useSession(await tabSession(this._hass, this._session));
      }
      if (!result.valid || signedOut) this._reload();
    } catch (err) {
      // Home Assistant is not reachable (yet), e.g. while it restarts: the editor's page waits
      // for a session, so try again shortly; otherwise the next round asks again.
      if (signedOut && !this._retryTimer) {
        this._retryTimer = setTimeout(() => {
          this._retryTimer = null;
          this._renew(true);
        }, RETRY_MS);
      }
    }
  }

  // The frame loaded a page: the editor itself (not the page asking for a session) resets the
  // count of the page's tries.
  _onFrameLoad(frame) {
    try {
      if (!frame.contentDocument || frame.contentDocument.getElementById(SIGNED_OUT_ID)) return;
      sessionStorage.removeItem(TRIES_KEY);
    } catch (err) {
      // Not readable: nothing to reset.
    }
  }

  // Loads the editor again, at the earliest RELOAD_GAP_MS after the last time.
  _reload() {
    const frame = this.shadowRoot.querySelector("iframe");
    if (!frame || this._reloadTimer) return;
    const wait = this._reloaded + RELOAD_GAP_MS - Date.now();
    if (wait > 0) {
      this._reloadTimer = setTimeout(() => {
        this._reloadTimer = null;
        this._reload();
      }, wait);
      return;
    }
    this._reloaded = Date.now();
    frame.src = UI_URL;
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
      frame.addEventListener("load", () => this._onFrameLoad(frame));
      frame.src = UI_URL;
      this.shadowRoot.append(style, toolbar, frame);
    }
    this.toggleAttribute("narrow", this._narrow);
  }
}

customElements.define("xknx-editor-panel", XknxEditorPanel);
