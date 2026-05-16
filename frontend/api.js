// ═══════════════════════════════════════
// PULSΞ API Client
// Backend connection with JWT auth
//
// localStorage keys (single source of truth):
//   pulse_token     — JWT bearer token
//   pulse_username  — display username (no email — backend doesn't use it)
//   pulse_logs      — in-memory activity buffer (last 50)
// ═══════════════════════════════════════

const API = {
  _backendOk: null,

  // ── Auth helpers ────────────────────────────────────────────────
  isLoggedIn() {
    return !!localStorage.getItem('pulse_token');
  },

  getUser() {
    const username = localStorage.getItem('pulse_username');
    const token = localStorage.getItem('pulse_token');
    if (!username && !token) return null;
    return { username: username || '', method: 'jwt' };
  },

  async logout() {
    // Best-effort server-side log; ignore errors.
    try {
      if (await this._backendAvailable()) {
        await this._req('POST', '/api/auth/logout');
      }
    } catch {}
    localStorage.removeItem('pulse_token');
    localStorage.removeItem('pulse_username');
    return true;
  },

  // Local-only activity log (used by dashboard send())
  addLocalLog(type, extra = {}) {
    try {
      const buf = JSON.parse(localStorage.getItem('pulse_logs') || '[]');
      buf.unshift({ type, time: new Date().toISOString(), extra });
      localStorage.setItem('pulse_logs', JSON.stringify(buf.slice(0, 50)));
    } catch {}
  },

  getLocalLogs() {
    try { return JSON.parse(localStorage.getItem('pulse_logs') || '[]'); }
    catch { return []; }
  },

  // ── Internal ────────────────────────────────────────────────────
  _headers() {
    const headers = { 'Content-Type': 'application/json' };
    const token = localStorage.getItem('pulse_token');
    if (token) headers['Authorization'] = `Bearer ${token}`;
    return headers;
  },

  async _req(method, path, body) {
    const opts = { method, headers: this._headers() };
    if (body) opts.body = JSON.stringify(body);
    const r = await fetch(API_BASE + path, opts);

    if (r.status === 401) {
      localStorage.removeItem('pulse_token');
      localStorage.removeItem('pulse_username');
      if (window.location.pathname !== '/') window.location.href = '/';
      throw { status: 401, message: 'Session expired' };
    }

    // Non-JSON responses (e.g. 204) shouldn't blow up
    const text = await r.text();
    const data = text ? JSON.parse(text) : null;
    if (!r.ok) throw { status: r.status, ...(data || {}) };
    return data;
  },

  async _backendAvailable() {
    if (this._backendOk !== null) return this._backendOk;
    try {
      const r = await fetch(API_BASE + '/health', { signal: AbortSignal.timeout(2000) });
      this._backendOk = r.ok;
    } catch { this._backendOk = false; }
    setTimeout(() => { this._backendOk = null; }, 30000);
    return this._backendOk;
  },

  // ── Market (backend → CoinGecko fallback, no embedded API key) ──
  async market() {
    try {
      if (await this._backendAvailable()) return await this._req('GET', '/api/market');
    } catch {}
    try {
      const r = await fetch(
        `https://api.coingecko.com/api/v3/coins/markets`
        + `?vs_currency=usd`
        + `&ids=bitcoin,ethereum,solana,dogecoin,ripple,cardano,the-open-network,avalanche-2`
        + `&order=market_cap_desc&sparkline=false&price_change_percentage=1h,24h,7d`
      );
      if (r.ok) return await r.json();
    } catch {}
    return null;
  },

  async coin(id) {
    try {
      if (await this._backendAvailable()) return await this._req('GET', '/api/market/' + id);
    } catch {}
    try {
      const r = await fetch(
        `https://api.coingecko.com/api/v3/coins/${id}`
        + `?localization=false&tickers=false&community_data=false&developer_data=false&sparkline=false`
      );
      if (r.ok) return await r.json();
    } catch {}
    return null;
  },

  async fng() {
    try {
      if (await this._backendAvailable()) return await this._req('GET', '/api/market/fng');
    } catch {}
    try {
      const r = await fetch('https://api.alternative.me/fng/?limit=1');
      return (await r.json())?.data?.[0] || null;
    } catch { return null; }
  },

  async topCoins(limit = 100) {
    try {
      if (await this._backendAvailable()) return await this._req('GET', `/api/market/top/${limit}`);
    } catch {}
    // Public CoinGecko fallback (no API key — leaking it in static JS is a no-go).
    try {
      const r = await fetch(
        `https://api.coingecko.com/api/v3/coins/markets`
        + `?vs_currency=usd&order=market_cap_desc&per_page=${limit}&page=1`
        + `&sparkline=false&price_change_percentage=1h,24h,7d`
      );
      if (r.ok) return await r.json();
    } catch {}
    return null;
  },

  async cashtags(symbols = 'btc,eth,sol,doge,xrp,ada,ton,avax') {
    try {
      if (await this._backendAvailable()) {
        const result = await this._req('GET', `/api/market/cashtags?symbols=${symbols}`);
        if (result && result.status === 'error') {
          console.warn('Cashtag backend error:', result.message);
          return { status: 'error', data: [], message: result.message };
        }
        return result;
      }
    } catch (e) {
      console.error('Cashtags API error:', e);
    }
    return { status: 'error', data: [], message: 'Backend unavailable' };
  },

  // ── Radar ───────────────────────────────────────────────────────
  async radarSignals(limit = 80) {
    try {
      if (await this._backendAvailable()) {
        return await this._req('GET', `/api/radar/signals?limit=${limit}`);
      }
    } catch {}
    return null;
  },

  async radarBreakdown(symbol) {
    try {
      if (await this._backendAvailable()) {
        return await this._req('GET', `/api/radar/breakdown/${encodeURIComponent(symbol)}`);
      }
    } catch {}
    return null;
  },

  // ── AI Agent ────────────────────────────────────────────────────
  async chat(message, context) {
    try {
      if (await this._backendAvailable()) {
        const d = await this._req('POST', '/api/agent/chat', { message, context });
        if (d && d.error) { console.error('AI Error:', d.error); return null; }
        return d?.reply || null;
      }
    } catch (e) {
      console.error('Chat error:', e);
    }
    return null;
  },

  async mission(task, marketData = []) {
    try {
      if (await this._backendAvailable()) {
        const d = await this._req('POST', '/api/agent/mission', {
          task,
          market_data: JSON.stringify(marketData),
        });
        return d?.synthesis || null;
      }
    } catch {}
    return null;
  },

  async signals(marketData = []) {
    try {
      if (await this._backendAvailable()) {
        const d = await this._req('POST', '/api/agent/signals', {
          market_data: JSON.stringify(marketData),
        });
        return d?.signals || null;
      }
    } catch {}
    return null;
  },

  async getCachedSignals(hours = 24, limit = 50) {
    try {
      if (await this._backendAvailable()) {
        const d = await this._req('GET', `/api/agent/signals/cached?hours=${hours}&limit=${limit}`);
        return d?.signals || [];
      }
    } catch {}
    return [];
  },

  async getLatestSignals(limit = 20) {
    try {
      if (await this._backendAvailable()) {
        const d = await this._req('GET', `/api/agent/signals/latest?limit=${limit}`);
        return d?.signals || [];
      }
    } catch {}
    return [];
  },

  async getSignalsStats() {
    try {
      if (await this._backendAvailable()) {
        return await this._req('GET', '/api/agent/signals/stats');
      }
    } catch {}
    return null;
  },

  async cleanupOldSignals(hours = 168) {
    try {
      if (await this._backendAvailable()) {
        return await this._req('DELETE', `/api/agent/signals/cleanup?hours=${hours}`);
      }
    } catch {}
    return null;
  },
};
