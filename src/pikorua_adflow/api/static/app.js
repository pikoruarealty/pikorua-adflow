/* Pikorua AdFlow — shared frontend helpers (vanilla JS, no build step). */

/* ── Currency: always INR (₹) with Indian digit grouping ── */
const _inr = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 });
function fmtINR(amount) {
  if (amount === null || amount === undefined || isNaN(amount)) return '₹0';
  return '₹' + _inr.format(Math.round(Number(amount)));
}
function fmtINRk(amount) {
  // Compact form for big numbers: ₹1.2L, ₹3.4Cr
  const n = Number(amount) || 0;
  if (n >= 1e7) return '₹' + (n / 1e7).toFixed(1).replace(/\.0$/, '') + 'Cr';
  if (n >= 1e5) return '₹' + (n / 1e5).toFixed(1).replace(/\.0$/, '') + 'L';
  return fmtINR(n);
}

/* ── Timestamps: always rendered in IST (Asia/Kolkata) ── */
function fmtIST(value) {
  if (!value) return '';
  const d = new Date(value);
  if (isNaN(d)) return '';
  try {
    return d.toLocaleString('en-IN', {
      timeZone: 'Asia/Kolkata', day: '2-digit', month: 'short', year: 'numeric',
      hour: 'numeric', minute: '2-digit', hour12: true,
    }) + ' IST';
  } catch (e) {
    return d.toISOString();
  }
}
function fmtISTdate(value) {
  if (!value) return '';
  const d = new Date(value);
  if (isNaN(d)) return '';
  return d.toLocaleDateString('en-IN', {
    timeZone: 'Asia/Kolkata', day: '2-digit', month: 'short', year: 'numeric',
  });
}

/* ── Number formatting (compact counts: 1.2k, 500k) ── */
function fmtCount(n) {
  n = Number(n) || 0;
  if (n >= 1e6) return (n / 1e6).toFixed(1).replace(/\.0$/, '') + 'M';
  if (n >= 1e3) return (n / 1e3).toFixed(1).replace(/\.0$/, '') + 'k';
  return String(n);
}

/* ── Fetch wrapper — returns parsed JSON, throws on HTTP error ── */
async function api(url, options) {
  const res = await fetch(url, options);
  let data = null;
  try { data = await res.json(); } catch (e) { data = null; }
  if (!res.ok) {
    const msg = (data && (data.detail || data.error)) || ('HTTP ' + res.status);
    throw new Error(msg);
  }
  return data;
}

/* ── HTML escape for safe text interpolation ── */
function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

/* ── Light/dark theme toggle (persisted) ── */
function pikToggleTheme() {
  const html = document.documentElement;
  const next = html.classList.contains('dark') ? 'light' : 'dark';
  html.classList.toggle('dark', next === 'dark');
  try { localStorage.setItem('pikorua-theme', next); } catch (e) {}
}
(function initTheme() {
  try {
    const t = localStorage.getItem('pikorua-theme');
    if (t === 'dark') document.documentElement.classList.add('dark');
  } catch (e) {}
})();
