(() => {
  'use strict';
  const funding = (rate, side, notional, hours) => Number.isFinite(rate) ? -side * rate * notional * hours : null;
  const journalMath = (p, exit, feePct, closed) => {
    const qty = p.size / p.entry;
    const pnl = p.side * qty * (exit - p.entry) - (p.fee || 0) - qty * exit * feePct / 100;
    const risk = p.sl > 0 ? -p.side * qty * (p.sl - p.entry) + (p.fee || 0) + qty * p.sl * feePct / 100 : 0;
    const start = Date.parse(p.opened || '');
    return { pnl, r: risk > 0 ? pnl / risk : null, hours: Number.isFinite(start) ? Math.max(0, (closed - start) / 3600000) : null };
  };
  const summary = (assets) => assets.reduce((s, a) => {
    const x = a.analysis;
    if (x && Object.prototype.hasOwnProperty.call(s, x.trend)) s[x.trend]++;
    if (x && x.aligned) s.aligned++;
    return s;
  }, { up: 0, down: 0, sideways: 0, aligned: 0 });
  window.WaltExtras = { funding, journalMath, summary };
  if (typeof document === 'undefined') return;

  const API = 'https://walt-positions.mrsupetbred.workers.dev/positions';
  const get = (key, fallback) => { try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; } };
  const put = (key, value) => { try { localStorage.setItem(key, JSON.stringify(value)); } catch {} };
  const esc = (v) => String(v).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const money = (n) => n == null || !Number.isFinite(n) ? '—' : `${n < 0 ? '−' : '+'}$${Math.abs(n).toFixed(2)}`;
  let journal = get('waltJournal', []); if (!Array.isArray(journal)) journal = [];
  let key = localStorage.getItem('waltSyncKey') || '';
  if (location.hash.startsWith('#sync=')) {
    try { key = decodeURIComponent(location.hash.slice(6)); localStorage.setItem('waltSyncKey', key); } catch {}
    history.replaceState(null, '', location.pathname + location.search);
  }
  let lastLocal = localStorage.getItem('waltPositions') || '[]', dirty = false, busy = false, timer = null, status = key ? 'Connecting…' : 'Not synced — enter your sync key';
  function statusView() { const el = document.getElementById('extras-sync-status'); if (el) el.textContent = status; }
  function changed() { dirty = true; clearTimeout(timer); timer = setTimeout(push, 1000); }
  async function push() {
    if (!key || busy || !dirty) return;
    busy = true; dirty = false;
    const currentKey = key;
    try {
      const r = await fetch(API, { method: 'PUT', headers: { Authorization: 'Bearer ' + currentKey, 'Content-Type': 'application/json' }, body: JSON.stringify({ positions: POS, journal }) });
      if (!r.ok) throw Error('sync');
      status = 'Synced ✓ ' + new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch { dirty = true; status = 'Sync failed — retrying'; }
    busy = false; statusView();
    if (dirty) { clearTimeout(timer); timer = setTimeout(push, 5000); }
  }
  async function pull() {
    if (!key || busy || dirty) return;
    busy = true; const currentKey = key;
    try {
      const r = await fetch(API, { headers: { Authorization: 'Bearer ' + currentKey }, cache: 'no-store' });
      if (!r.ok) throw Error('sync');
      const data = await r.json();
      if (!Array.isArray(data.positions)) throw Error('sync');
      if (!dirty && currentKey === key) {
        POS = data.positions; journal = Array.isArray(data.journal) ? data.journal : [];
        savePos(); put('waltJournal', journal); lastLocal = localStorage.getItem('waltPositions') || '[]';
        status = 'Synced ✓ ' + new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        render();
      }
    } catch { status = 'Sync failed — check your key or connection'; }
    busy = false; statusView(); if (dirty) { clearTimeout(timer); timer = setTimeout(push, 1000); }
  }
  const originalChart = window.chartSvg;
  window.chartSvg = function(a) {
    const ch = a.analysis && a.analysis.chart;
    if (!ch || !ch.candles || !ch.candles.length) return originalChart(a);
    const range = get('waltChartRanges', {})[a.symbol] || '72h';
    const offset = range === '24h' ? Math.max(0, ch.candles.length - 24) : 0;
    let chartAsset = a;
    if (offset) {
      const channel = { ...ch.channel };
      if (channel.from < offset) {
        const fraction = (offset - channel.from) / (ch.candles.length - 1 - channel.from || 1);
        ['hi', 'lo', 'mid'].forEach(k => { channel[k] = [channel[k][0] + (channel[k][1] - channel[k][0]) * fraction, channel[k][1]]; });
      }
      channel.from = Math.max(0, channel.from - offset);
      chartAsset = { ...a, analysis: { ...a.analysis, chart: { ...ch, channel, candles: ch.candles.slice(offset), ema_fast: ch.ema_fast.slice(offset), ema_slow: ch.ema_slow.slice(offset), flips: ch.flips.filter(f => f.i >= offset).map(f => ({ ...f, i: f.i - offset })) } } };
    }
    const candles = chartAsset.analysis.chart.candles;
    const volumes = candles.map(c => c[5]); const max = Math.max(0, ...volumes.filter(v => Number.isFinite(v)));
    const strip = max > 0 ? `<svg class="extras-volume" viewBox="0 0 600 25" preserveAspectRatio="none" role="img" aria-label="Hourly volume">${volumes.map((v, i) => Number.isFinite(v) ? `<rect x="${4 + i * 534 / candles.length}" y="${24 - 22 * v / max}" width="${Math.max(1, 534 / candles.length * .7)}" height="${22 * v / max}" fill="var(--muted)" opacity=".5"/>` : '').join('')}</svg>` : '';
    return `<div class="extras-chart-controls" role="group" aria-label="Chart range">${['24h','72h','7d'].map(v => `<button type="button" data-extra-range="${v}" data-extra-symbol="${esc(a.symbol)}" aria-pressed="${v === range}">${v}</button>`).join('')}</div>${originalChart(chartAsset)}${strip}${range === '7d' ? '<div class="note">7-day view needs more data</div>' : ''}`;
  };
  const originalRender = window.render;
  window.render = function() {
    originalRender();
    if (!DATA) return;
    const nowLocal = localStorage.getItem('waltPositions') || '[]';
    if (nowLocal !== lastLocal) { lastLocal = nowLocal; changed(); }
    let header = document.getElementById('extras-header');
    if (!header) { header = document.createElement('div'); header.id = 'extras-header'; document.querySelector('header').after(header); }
    const counts = summary(DATA.assets), latest = Math.max(0, ...DATA.assets.map(a => a.walt_at || 0));
    const delayed = !DATA.updated || Date.now() - DATA.updated > 1200000 || !latest || Date.now() - latest > 600000;
    header.innerHTML = `${delayed ? '<div class="warn extras-warning">Price feed delayed</div>' : ''}<div class="extras-summary">${counts.down} of ${DATA.assets.length} trending down · ${counts.up} up · ${counts.sideways} sideways · ★ ${counts.aligned} aligned</div>`;
    let sync = document.getElementById('extras-sync');
    if (!sync) { sync = document.createElement('div'); sync.id = 'extras-sync'; document.getElementById('mypos').before(sync); }
    sync.innerHTML = `<span id="extras-sync-status"></span> <button type="button" data-extra-key>Set sync key</button>`; statusView();
    document.querySelectorAll('#grid .card').forEach((el, i) => {
      // Cards without history have no id, so use the same sorted list as the page.
      const list = DATA.assets.filter(a => state.group === 'All' || a.group === state.group).sort((a,b) => {
        const rank = x => !x.analysis ? -2 : x.analysis.trend === 'sideways' ? -1 + x.analysis.score / 100 : x.analysis.score + (x.analysis.aligned ? .25 : 0);
        return rank(b) - rank(a);
      });
      const a = list[i]; if (!a) return;
      const top = el.querySelector('.top'), px = el.querySelector('.px');
      const age = a.walt_at ? Math.max(0, Math.floor((Date.now() - a.walt_at) / 60000)) : null;
      if (top) top.insertAdjacentHTML('afterend', `<div class="extras-badges"><span class="extras-age ${age == null || age >= 15 ? 'old' : age >= 3 ? 'aging' : 'fresh'}">● ${age == null ? 'Price age unknown' : age + ' min old'}</span>${a.market_open === false ? ' <span class="extras-closed">Market closed</span>' : ''}</div>${a.market_open === false ? '<div class="note">Prices frozen until the market opens; trends over closed hours are less meaningful.</div>' : ''}`);
      if (px && a.market_open === false) px.classList.add('extras-frozen');
      if (a.analysis && a.analysis.trend !== 'sideways') {
        const table = el.querySelector('table'), s = scenario(a, state.per[a.symbol] ?? state.lev), f = funding(a.funding_h, s.side, 100 * s.L, 24);
        if (table) table.insertAdjacentHTML('beforeend', `<tr><td>Funding per day at ${s.L}×</td><td>${f == null ? '—' : (f >= 0 ? '+' : '') + f.toFixed(2) + '%'}</td></tr><tr><td>After 24 h incl. funding (stop / TP)</td><td>${f == null ? '—' : (-s.lossM + f).toFixed(1) + '% / ' + (s.gainM + f).toFixed(1) + '%'}</td></tr>`);
      }
    });
    document.querySelectorAll('#ov [data-go]').forEach(row => { const a = DATA.assets.find(x => x.symbol === row.dataset.go); if (a && a.market_open === false) { row.cells[0].insertAdjacentHTML('beforeend', ' <span class="extras-closed">Market closed</span>'); row.cells[1].classList.add('extras-frozen'); } });
    document.querySelectorAll('#mypos .poscard').forEach((el, i) => {
      const id = el.querySelector('[data-edit]')?.dataset.edit, p = POS.find(x => String(x.id) === id), a = p && DATA.assets.find(x => x.symbol === p.sym); if (!a) return;
      const opened = Date.parse(p.opened || ''), hours = Number.isFinite(opened) ? Math.max(0, (Date.now() - opened) / 3600000) : null;
      const total = hours == null ? null : funding(a.funding_h, p.side, p.size, hours), daily = funding(a.funding_h, p.side, p.size, 24);
      el.querySelector('.actions').insertAdjacentHTML('beforebegin', `<div class="note">Funding so far ≈ ${money(total)} · per day ≈ ${money(daily)} · estimate at the current rate</div>`);
      el.querySelector('.actions').insertAdjacentHTML('beforeend', `<button class="rm" type="button" data-extra-close="${esc(p.id)}">Close position</button>`);
    });
    let section = document.getElementById('extras-journal');
    if (!section) { section = document.createElement('details'); section.id = 'extras-journal'; section.className = 'overview'; document.getElementById('mypos').after(section); }
    const stats = journal.map(p => journalMath(p, p.exit, p.feePct, p.closed));
    const rs = stats.filter(s => s.r != null); const wins = stats.filter(s => s.pnl > 0).length;
    section.innerHTML = `<summary>Trade journal (${journal.length})</summary><div class="extras-journal-body"><p>${journal.length ? `Win rate ${(wins / journal.length * 100).toFixed(0)}% · Total ${money(stats.reduce((s,x) => s + x.pnl, 0))} · Average R ${rs.length ? (rs.reduce((s,x) => s + x.r, 0) / rs.length).toFixed(2) : '—'}` : 'No closed trades yet.'}</p>${journal.map((p,i) => { const s = stats[i]; return `<div class="extras-trade"><b>${esc(p.sym)} ${p.side < 0 ? 'short' : 'long'}</b> · ${esc(new Date(p.closed).toLocaleString())} · ${money(s.pnl)} · R ${s.r == null ? '—' : s.r.toFixed(2)} · ${s.hours == null ? 'holding time unknown' : s.hours.toFixed(1) + ' h'}</div>`; }).join('')}</div>`;
  };
  document.addEventListener('click', e => {
    const range = e.target.closest('[data-extra-range]');
    if (range) { const ranges = get('waltChartRanges', {}); ranges[range.dataset.extraSymbol] = range.dataset.extraRange; put('waltChartRanges', ranges); render(); return; }
    if (e.target.closest('[data-extra-key]')) {
      const value = prompt('Sync key (leave empty to disable sync)', key); if (value === null) return;
      key = value.trim(); if (key) localStorage.setItem('waltSyncKey', key); else localStorage.removeItem('waltSyncKey');
      dirty = false; status = key ? 'Connecting…' : 'Not synced — enter your sync key'; statusView(); if (key) pull(); return;
    }
    const close = e.target.closest('[data-extra-close]'); if (!close) return;
    const p = POS.find(x => String(x.id) === close.dataset.extraClose); if (!p) return;
    const a = DATA.assets.find(x => x.symbol === p.sym), price = LIVE[p.sym] ?? a?.walt_price ?? a?.analysis?.price;
    const value = prompt('Exit price', price ?? ''); if (value === null) return;
    const exit = Number(value); if (!value.trim() || !Number.isFinite(exit) || exit <= 0) { alert('Enter a valid positive exit price.'); return; }
    const time = prompt('Exit time (local date and time)', new Date().toLocaleString()); if (time === null) return;
    const closed = Date.parse(time); if (!Number.isFinite(closed) || (p.opened && closed < Date.parse(p.opened))) { alert('Enter a valid exit time after opening.'); return; }
    journal.unshift({ ...p, exit, closed, feePct: DATA.fee_pct }); put('waltJournal', journal);
    POS = POS.filter(x => x !== p); savePos(); changed(); render();
  });
  if (key) pull(); setInterval(pull, 60000);
  if (DATA) render();
})();

/* Backtest (bot/data/market/backtest.json, 90 days of hourly history): how the page's 2:1 stop/take-profit rule did on
   each asset vs a random long/short at the same moments. Shown on each card and as one overall line. Past data only. */
(function () {
  let BT = null;
  fetch('bot/data/market/backtest.json').then((r) => r.ok ? r.json() : null).then((d) => { BT = d; if (window.render) window.render(); }).catch(() => {});
  const pct = (v) => v == null ? '—' : (v * 100).toFixed(0) + '%';
  const line = (s, label) => {
    if (!s || !s.paired_episodes) return '';
    const edge = s.paired_tp_rate - s.random_tp_rate, pnl = s.episode && s.episode.avg_pnl_pct;
    const cls = edge > 0.03 && pnl > 0 ? 'ok' : edge < -0.03 || pnl < 0 ? 'bad' : 'mid';
    return `<div class="bt-line ${cls}" title="${s.paired_episodes} signals over ~90 days; one trade per trend episode; fees included">${label}: `
      + `take-profit hit ${pct(s.paired_tp_rate)} vs ${pct(s.random_tp_rate)} random · avg ${pnl == null ? '—' : (pnl > 0 ? '+' : '') + pnl.toFixed(2) + '%'} per trade · ${s.paired_episodes} trades</div>`;
  };
  const origCard = window.card;
  window.card = function (a, best) {
    const html = origCard(a, best);
    const s = BT && BT.assets && BT.assets[a.symbol];
    if (!s) return html;
    return html.replace(/<\/div>\s*$/, line(s.all, 'Rule, last 90 days') + '</div>');
  };
  const origRender = window.render;
  window.render = function () {
    origRender();
    if (!BT || !BT.overall) return;
    let el = document.getElementById('bt-overall');
    if (!el) { el = document.createElement('div'); el.id = 'bt-overall'; const ov = document.querySelector('.overview') || document.getElementById('grid'); ov && ov.parentNode.insertBefore(el, ov); }
    const o = BT.overall.all || BT.overall, al = BT.overall.alignment && (BT.overall.alignment.aligned || BT.overall.alignment.true || BT.overall.alignment['true']);
    el.innerHTML = line(o, 'Backtest, all assets') + (al ? line(al, '★ aligned trends only') : '')
      + '<div class="note">The rule\'s results on the last ~90 days of hourly history, against a coin-flip long/short at the same moments. Past data; not a forecast.</div>';
  };
})();
