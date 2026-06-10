// Shared auth for all dashboard pages: token storage (memory + localStorage so
// sign-in survives reloads and server restarts), authenticated fetch, and the
// sign-in screen. The dashboard never sees Google tokens after the initial
// exchange — only our own JWT.

const TOKEN_KEY = "xc_token";
let _token = localStorage.getItem(TOKEN_KEY);

function getToken() { return _token; }

function setToken(t) {
  _token = t;
  if (t) localStorage.setItem(TOKEN_KEY, t);
  else localStorage.removeItem(TOKEN_KEY);
}

function signOut() {
  setToken(null);
  location.href = "/";
}

// Fetch with our Bearer token. On 401 the token is stale/revoked: clear it and
// fall back to the sign-in screen (index) or bounce to / (detail pages).
async function authFetch(url, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  if (_token) headers["Authorization"] = "Bearer " + _token;
  const res = await fetch(url, { ...opts, headers });
  if (res.status === 401) {
    setToken(null);
    if (typeof showSignIn === "function") showSignIn();
    else location.href = "/";
    throw new Error("unauthenticated");
  }
  return res;
}

async function getJSONAuth(url) {
  const res = await authFetch(url);
  if (!res.ok) throw new Error(`${url} -> ${res.status}`);
  return res.json();
}

// Bootstrap: returns {config, me} — me is null when signed out. In DEV_MODE a
// ?dev_token= query param is accepted (handy for headless tests) and stripped.
async function initAuth() {
  const config = await (await fetch("/auth/config")).json();

  const params = new URLSearchParams(location.search);
  if (config.dev_mode && params.get("dev_token")) {
    setToken(params.get("dev_token"));
    params.delete("dev_token");
    history.replaceState(null, "", location.pathname +
      (params.toString() ? "?" + params : ""));
  }

  if (!_token) return { config, me: null };
  try {
    const res = await fetch("/auth/me",
      { headers: { Authorization: "Bearer " + _token } });
    if (!res.ok) { setToken(null); return { config, me: null }; }
    return { config, me: await res.json() };
  } catch {
    return { config, me: null };
  }
}

// ---- Sign-in screen (index.html only) ------------------------------------

async function exchangeGoogleCredential(credential) {
  const res = await fetch("/auth/google", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id_token: credential }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    alert("Google sign-in failed: " + (err.detail || res.status));
    return;
  }
  const data = await res.json();
  setToken(data.access_token);
  // The exchange already returns the athlete — enter the app in place instead
  // of a full page reload (reload kept as fallback for non-index pages).
  if (typeof onSignedIn === "function") onSignedIn(data.athlete);
  else location.reload();
}

function mountGoogleButton(clientId) {
  let tries = 0;
  const timer = setInterval(() => {
    if (window.google && google.accounts && google.accounts.id) {
      clearInterval(timer);
      google.accounts.id.initialize({
        client_id: clientId,
        callback: (resp) => exchangeGoogleCredential(resp.credential),
      });
      google.accounts.id.renderButton(
        document.getElementById("googleBtn"),
        { theme: "outline", size: "large", text: "signin_with" });
    } else if (++tries > 50) {  // ~5s: GIS script failed to load
      clearInterval(timer);
      document.getElementById("googleUnconfigured").hidden = false;
    }
  }, 100);
}

async function devLogin() {
  const email = document.getElementById("devEmail").value.trim();
  if (!email) return;
  const res = await fetch("/auth/dev-login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
  });
  if (!res.ok) {
    alert("Dev login failed: " + res.status);
    return;
  }
  const data = await res.json();
  setToken(data.access_token);
  if (typeof onSignedIn === "function") onSignedIn(data.athlete);
  else location.reload();
}

function renderSignIn(config) {
  const overlay = document.getElementById("signin");
  if (!overlay) { location.href = "/"; return; }
  overlay.hidden = false;
  const appView = document.getElementById("appView");
  if (appView) appView.hidden = true;

  if (config.google_client_id) {
    mountGoogleButton(config.google_client_id);
  } else {
    document.getElementById("googleUnconfigured").hidden = false;
  }
  if (config.dev_mode) {
    document.getElementById("devLogin").hidden = false;
    document.getElementById("devBtn").onclick = devLogin;
    document.getElementById("devEmail").addEventListener("keydown",
      (e) => { if (e.key === "Enter") devLogin(); });
  }
}
