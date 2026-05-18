// ═══════════════════════════════════════
// PULSΞ Shared Utilities
// ═══════════════════════════════════════

// ── Background init (legacy alias kept for older HTML inline scripts) ──
function initUnicorn() {
  if (typeof initBackground === 'function') initBackground();
}

// ── Formatters ──
function fmt(n, d = 2) {
  if (n == null) return '—';
  if (n >= 1e12) return '$' + (n / 1e12).toFixed(2) + 'T';
  if (n >= 1e9) return '$' + (n / 1e9).toFixed(2) + 'B';
  if (n >= 1e6) return '$' + (n / 1e6).toFixed(2) + 'M';
  if (n >= 1) return '$' + n.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
  return '$' + n.toFixed(6);
}
function pct(n) { return n == null ? '—' : (n >= 0 ? '+' : '') + n.toFixed(2) + '%'; }
function pc(n) { return n >= 0 ? 'var(--g)' : 'var(--r)'; }

// ── Crypto icon from JSDelivr CDN (free, no CoinGecko) ──
function getCryptoIcon(symbol) {
  if (!symbol) return '';
  const s = symbol.toLowerCase();
  // Using cryptocurrency-icons via JSDelivr CDN
  return `https://cdn.jsdelivr.net/npm/cryptocurrency-icons@0.18.1/svg/color/${s}.svg`;
}

// ── Fallback for missing crypto icons ──
function showIconFallback(img, symbol) {
  // Hide the failed image
  img.style.display = 'none';
  // Show first letter of symbol as fallback
  const parent = img.parentElement;
  parent.style.background = 'linear-gradient(135deg, rgba(100,120,255,.2) 0%, rgba(0,255,163,.15) 100%)';
  parent.style.display = 'flex';
  parent.style.alignItems = 'center';
  parent.style.justifyContent = 'center';
  parent.style.fontSize = '12px';
  parent.style.fontWeight = '600';
  parent.style.color = 'rgba(255,255,255,.8)';
  parent.textContent = symbol ? symbol[0].toUpperCase() : '?';
}

// ── Image proxy (for loading images via backend) ──
function proxyImg(url) {
  // Deprecated: use getCryptoIcon instead
  if (!url) return '';
  // If CoinGecko URL, proxy through backend
  if (url.includes('coin-images.coingecko.com')) {
    const path = url.replace('https://coin-images.coingecko.com/', '');
    return `${API_BASE}/api/market/image-proxy/${path}`;
  }
  return url;
}

// ── Clock ──
function startClock(el) {
  function tick() {
    const n = new Date();
    el.textContent = n.toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' }) + ' · ' +
      n.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'UTC' }) + ' UTC';
  }
  tick(); setInterval(tick, 1000);
}

// ── Profile ──
function initProfile() {
  const u = API.getUser();
  if (!u) return;
  const el = (id) => document.getElementById(id);
  const name = u.username || 'User';
  if (el('pN')) el('pN').textContent = name;
  if (el('pE')) el('pE').textContent = name;
  if (el('pM')) el('pM').textContent = 'via ' + (u.method || 'jwt');
  if (el('avL')) el('avL').textContent = (name[0] || 'U').toUpperCase();
}

function toggleProfile() {
  document.getElementById('pD').classList.toggle('open');
}

function closeProfileOnClickOut() {
  document.addEventListener('click', e => {
    if (!e.target.closest('.pw')) document.getElementById('pD')?.classList.remove('open');
  });
}

async function doLogout() {
  await API.logout();
  window.location.href = '/';
}

// ── Auth guard ──
function requireAuth() {
  if (!API.isLoggedIn()) { window.location.href = '/'; return false; }
  return true;
}

// ── Coin lookup ──
const COINS = {
  bitcoin: { s: 'BTC', n: 'Bitcoin' }, ethereum: { s: 'ETH', n: 'Ethereum' },
  solana: { s: 'SOL', n: 'Solana' }, dogecoin: { s: 'DOGE', n: 'Dogecoin' },
  ripple: { s: 'XRP', n: 'Ripple' }, cardano: { s: 'ADA', n: 'Cardano' },
  'the-open-network': { s: 'TON', n: 'Toncoin' }, 'avalanche-2': { s: 'AVAX', n: 'Avalanche' }
};
const TICKER_MAP = {};
Object.entries(COINS).forEach(([id, c]) => { TICKER_MAP[c.s.toLowerCase()] = id; });

function tickerToId(ticker) { return TICKER_MAP[(ticker || '').toLowerCase().replace('$', '')]; }

// ── Mock data fallback ──
const MOCK_MARKET = [
  { id:'bitcoin',symbol:'btc',name:'Bitcoin',image:'https://assets.coingecko.com/coins/images/1/small/bitcoin.png',current_price:97842,market_cap:1937e9,total_volume:38.2e9,price_change_percentage_1h_in_currency:.32,price_change_percentage_24h_in_currency:2.18,price_change_percentage_7d_in_currency:5.41,high_24h:98100,low_24h:95200,ath:109000,ath_change_percentage:-10.2 },
  { id:'ethereum',symbol:'eth',name:'Ethereum',image:'https://assets.coingecko.com/coins/images/279/small/ethereum.png',current_price:2734.5,market_cap:329e9,total_volume:14.8e9,price_change_percentage_1h_in_currency:-.15,price_change_percentage_24h_in_currency:1.87,price_change_percentage_7d_in_currency:3.22,high_24h:2780,low_24h:2680,ath:4878,ath_change_percentage:-43.9 },
  { id:'solana',symbol:'sol',name:'Solana',image:'https://assets.coingecko.com/coins/images/4128/small/solana.png',current_price:196.4,market_cap:95.6e9,total_volume:3.4e9,price_change_percentage_1h_in_currency:.88,price_change_percentage_24h_in_currency:4.52,price_change_percentage_7d_in_currency:12.3,high_24h:198.5,low_24h:186.2,ath:294,ath_change_percentage:-33.2 },
  { id:'ripple',symbol:'xrp',name:'XRP',image:'https://assets.coingecko.com/coins/images/44/small/xrp-symbol-white-128.png',current_price:2.54,market_cap:146e9,total_volume:4.2e9,price_change_percentage_1h_in_currency:.12,price_change_percentage_24h_in_currency:-.74,price_change_percentage_7d_in_currency:1.85,high_24h:2.59,low_24h:2.48,ath:3.84,ath_change_percentage:-33.8 },
  { id:'dogecoin',symbol:'doge',name:'Dogecoin',image:'https://assets.coingecko.com/coins/images/5/small/dogecoin.png',current_price:.2583,market_cap:38.2e9,total_volume:1.8e9,price_change_percentage_1h_in_currency:.45,price_change_percentage_24h_in_currency:3.21,price_change_percentage_7d_in_currency:8.76,high_24h:.262,low_24h:.248,ath:.7376,ath_change_percentage:-65 },
  { id:'cardano',symbol:'ada',name:'Cardano',image:'https://assets.coingecko.com/coins/images/975/small/cardano.png',current_price:.782,market_cap:27.8e9,total_volume:680e6,price_change_percentage_1h_in_currency:-.22,price_change_percentage_24h_in_currency:1.15,price_change_percentage_7d_in_currency:4.33,high_24h:.795,low_24h:.768,ath:3.09,ath_change_percentage:-74.7 },
  { id:'the-open-network',symbol:'ton',name:'Toncoin',image:'https://assets.coingecko.com/coins/images/17980/small/ton_symbol.png',current_price:3.62,market_cap:12.5e9,total_volume:320e6,price_change_percentage_1h_in_currency:.08,price_change_percentage_24h_in_currency:-1.32,price_change_percentage_7d_in_currency:2.1,high_24h:3.71,low_24h:3.55,ath:8.25,ath_change_percentage:-56.1 },
  { id:'avalanche-2',symbol:'avax',name:'Avalanche',image:'https://assets.coingecko.com/coins/images/12559/small/Avalanche_Circle_RedWhite_Trans.png',current_price:24.18,market_cap:9.9e9,total_volume:410e6,price_change_percentage_1h_in_currency:.55,price_change_percentage_24h_in_currency:2.67,price_change_percentage_7d_in_currency:6.9,high_24h:24.5,low_24h:23.2,ath:146.22,ath_change_percentage:-83.5 }
];
