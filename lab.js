// Strategy lab (any rule, any asset, any period) and Research results.
// Mirrors waltbot/multiasset.py: a signal at day t's close sets the position held from day t+1's
// open to day t+2's open; cost = Walt taker fee + 0.05 % slippage on every unit traded; funding
// per calendar day held. Exposure multiplies returns, costs and funding; 0 equity is a wipe-out.
(function () {
  const DAY = 86400e3;
  const FEE = { crypto: 0.0009, stock: 0.0008, index: 0.0008, commodity: 0.0008, fx: 0.0008, rates: 0.0008 };
  const SLIP = 0.0005;
  const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  const pct = (x, d = 1) => (x == null || !isFinite(x) ? '–' : (x > 0 ? '+' : '') + x.toFixed(d) + '%');
  const ymd = d3.utcFormat('%Y-%m-%d');
  let ASSETS = [], VIX = null, FNG = null;
  const store = new Map();

  async function getJSON(p) { const r = await fetch(p); if (!r.ok) throw new Error(p + ' ' + r.status); return r.json(); }
  async function series(name) {
    if (store.has(name)) return store.get(name);
    const d = await getJSON(`data/daily/${name}.json`);
    const s = { t: d.t.map((x) => x * DAY), o: d.o, h: d.h, l: d.l, c: d.c, v: d.v };
    store.set(name, s); return s;
  }
  async function aux(name) { const d = await getJSON(`data/daily/${name}.json`); return new Map(d.t.map((t, i) => [t * DAY, d.c[i]])); }
  const ffill = (t, map) => { const out = new Array(t.length).fill(NaN); let last = NaN; const keys = [...map.keys()].sort((a, b) => a - b); let j = 0;
    for (let i = 0; i < t.length; i++) { while (j < keys.length && keys[j] <= t[i]) { last = map.get(keys[j]); j++; } out[i] = last; } return out; };

  // ---------- indicators
  const sma = (c, n) => { const o = new Array(c.length).fill(NaN); let s = 0; for (let i = 0; i < c.length; i++) { s += c[i]; if (i >= n) s -= c[i - n]; if (i >= n - 1) o[i] = s / n; } return o; };
  const pctchg = (c) => c.map((x, i) => (i ? x / c[i - 1] - 1 : NaN));
  const rollStd = (r, n) => { const o = new Array(r.length).fill(NaN); for (let i = n; i < r.length; i++) { let m = 0, k = 0; for (let j = i - n + 1; j <= i; j++) { if (isFinite(r[j])) { m += r[j]; k++; } } if (k < n) continue; m /= k; let v = 0; for (let j = i - n + 1; j <= i; j++) v += (r[j] - m) ** 2; o[i] = Math.sqrt(v / (k - 1)); } return o; };
  const rollMean = (x, n) => { const o = new Array(x.length).fill(NaN); let s = 0; for (let i = 0; i < x.length; i++) { s += x[i]; if (i >= n) s -= x[i - n]; if (i >= n - 1) o[i] = s / n; } return o; };
  function rsi(c, n) {
    const a = 1 / n, o = new Array(c.length).fill(NaN); let up = NaN, dn = NaN;
    for (let i = 1; i < c.length; i++) { const d = c[i] - c[i - 1], u = Math.max(d, 0), w = Math.max(-d, 0);
      if (i === 1) { up = u; dn = w; } else { up += a * (u - up); dn += a * (w - dn); }
      o[i] = dn === 0 ? NaN : 100 - 100 / (1 + up / dn); }
    return o;
  }
  const holdAfter = (trig, days, side = 1) => { const o = new Array(trig.length).fill(0); let left = 0; for (let i = 0; i < trig.length; i++) { if (trig[i]) left = days; if (left > 0) { o[i] = side; left--; } } return o; };
  const sign = (x) => (isFinite(x) ? Math.sign(x) : 0);

  // ---------- rules: each returns a position array (-1..1), known at each close
  const RULES = {
    buy_and_hold: { label: 'Buy and hold (benchmark)', group: 'Benchmark', f: (s) => s.c.map(() => 1) },
    sma_cross: { label: 'Moving-average cross', group: 'Trend', params: { fast: 50, slow: 200, shorts: 0 },
      f: (s, p) => { const a = sma(s.c, p.fast), b = sma(s.c, p.slow); return a.map((x, i) => { const v = sign(x - b[i]); return p.shorts ? v : Math.max(v, 0); }); } },
    momentum: { label: 'Time-series momentum (own past return)', group: 'Trend', params: { lookback: 252, shorts: 0 },
      f: (s, p) => s.c.map((x, i) => { const v = i >= p.lookback ? sign(x / s.c[i - p.lookback] - 1) : 0; return p.shorts ? v : Math.max(v, 0); }) },
    above_sma: { label: 'Hold only above the long average', group: 'Trend', params: { days: 200 },
      f: (s, p) => { const a = sma(s.c, p.days); return s.c.map((x, i) => (isFinite(a[i]) && x > a[i] ? 1 : 0)); } },
    momentum_volscaled: { label: 'Momentum, sized by volatility (target 15%/yr)', group: 'Trend', params: { lookback: 252 },
      f: (s, p) => { const v = rollStd(pctchg(s.c), 60); return s.c.map((x, i) => (i >= p.lookback && isFinite(v[i]) ? sign(x / s.c[i - p.lookback] - 1) * Math.min(1, 0.15 / (v[i] * Math.sqrt(252))) : 0)); } },
    rsi2_dip: { label: 'Buy panic dips in an uptrend (RSI-2 < 10, above 200-day avg)', group: 'Panic / reversal',
      f: (s) => { const r = rsi(s.c, 2), a200 = sma(s.c, 200), a5 = sma(s.c, 5); const o = []; let st = 0;
        for (let i = 0; i < s.c.length; i++) { if (st === 0 && r[i] < 10 && s.c[i] > (isFinite(a200[i]) ? a200[i] : Infinity)) st = 1; else if (st === 1 && s.c[i] > a5[i]) st = 0; o.push(st); } return o; } },
    big_drop: { label: 'Buy after a 2.5-sigma one-day crash, hold 5 days', group: 'Panic / reversal',
      f: (s) => { const r = pctchg(s.c), v = rollStd(r, 60); return holdAfter(r.map((x, i) => x < -2.5 * v[i]), 5); } },
    three_down: { label: 'Buy after 3 down days, hold 3 days', group: 'Panic / reversal',
      f: (s) => { const r = pctchg(s.c); return holdAfter(r.map((x, i) => i > 2 && x < 0 && r[i - 1] < 0 && r[i - 2] < 0), 3); } },
    shock_follow: { label: 'News shock: follow a big move on heavy volume for 20 days', group: 'News / earnings proxy',
      f: (s) => { const r = pctchg(s.c), v = rollStd(r, 60), va = rollMean(s.v, 60); const sh = r.map((x, i) => Math.abs(x) > 3 * v[i] && s.v[i] > 2 * va[i]);
        const up = holdAfter(sh.map((x, i) => x && r[i] > 0), 20), dn = holdAfter(sh.map((x, i) => x && r[i] < 0), 20); return up.map((x, i) => x - dn[i]); } },
    shock_fade: { label: 'News shock: bet against a big move for 5 days', group: 'News / earnings proxy',
      f: (s) => { const r = pctchg(s.c), v = rollStd(r, 60), va = rollMean(s.v, 60); const sh = r.map((x, i) => Math.abs(x) > 3 * v[i] && s.v[i] > 2 * va[i]);
        const up = holdAfter(sh.map((x, i) => x && r[i] < 0), 5), dn = holdAfter(sh.map((x, i) => x && r[i] > 0), 5); return up.map((x, i) => x - dn[i]); } },
    turn_of_month: { label: 'Turn of the month (last day + first 3 days)', group: 'Calendar',
      f: (s) => { const mo = s.t.map((t) => new Date(t).getUTCMonth()); const n = mo.length;
        const last = mo.map((m, i) => i === n - 1 || m !== mo[i + 1]); const first3 = mo.map((m, i) => (i < 1 || m !== mo[i - 1]) || (i < 2 || m !== mo[i - 2]) || (i < 3 || m !== mo[i - 3]));
        return mo.map((_, i) => ((i + 1 < n && last[i + 1]) || last[i] || (i + 1 < n && first3[i + 1]) ? 1 : 0)); } },
    vix_high: { label: 'Market fear: buy when VIX > 30, hold 20 days', group: 'Fear / sentiment', needs: 'vix',
      f: (s) => { const v = ffill(s.t, VIX); return holdAfter(v.map((x) => x > 30), 20); } },
    vix_spike: { label: 'Market fear: buy a VIX spike (1.3x its 20-day avg), hold 10 days', group: 'Fear / sentiment', needs: 'vix',
      f: (s) => { const v = ffill(s.t, VIX), m = rollMean(v.map((x) => (isFinite(x) ? x : 0)), 20); return holdAfter(v.map((x, i) => x > 1.3 * m[i]), 10); } },
    fng_fear: { label: 'Crypto Fear & Greed: buy extreme fear (<20), sell above 50', group: 'Fear / sentiment', needs: 'fng',
      f: (s) => { const f = ffill(s.t, FNG); const o = []; let st = 0; for (const x of f) { if (isFinite(x)) { if (st === 0 && x < 20) st = 1; else if (st === 1 && x > 50) st = 0; } o.push(st); } return o; } },
    fng_contrarian: { label: 'Crypto Fear & Greed: long in fear (<25), short in greed (>75)', group: 'Fear / sentiment', needs: 'fng',
      f: (s) => ffill(s.t, FNG).map((x) => (x < 25 ? 1 : x > 75 ? -1 : 0)) },
  };

  function backtest(s, pos, cls, o) {
    // returns daily net returns (index i = day held i, from open i to open i+1) within [from, to]
    const n = s.c.length, out = [];
    const cost = (FEE[cls] ?? 0.0008) + SLIP;
    for (let i = 1; i < n - 1; i++) {
      if (s.t[i] < o.from || s.t[i] > o.to) continue;
      const held = (isFinite(pos[i - 1]) ? pos[i - 1] : 0) * o.lev;
      const prev = i >= 2 && s.t[i - 1] >= o.from ? (isFinite(pos[i - 2]) ? pos[i - 2] : 0) * o.lev : 0;
      const oo = s.o[i + 1] / s.o[i] - 1;
      const days = Math.max(1, Math.round((s.t[i + 1] - s.t[i]) / DAY));
      const rate = o.funding * 24 * days;
      const fund = o.fundMode === 'walt' ? held * rate : o.fundMode === 'adverse' ? Math.abs(held) * rate : 0;
      out.push({ t: s.t[i], r: held * oo - Math.abs(held - prev) * cost - fund, held, fee: Math.abs(held - prev) * cost, fund });
    }
    return out;
  }
  function stats(rs) {
    if (rs.length < 30) return null;
    let eq = 1, peak = 1, dd = 0, wiped = false; const curve = [];
    for (const x of rs) { eq = wiped ? 0 : eq * (1 + x.r); if (eq <= 0) { eq = 0; wiped = true; } peak = Math.max(peak, eq); dd = Math.max(dd, peak ? 1 - eq / peak : 1); curve.push({ t: x.t, v: eq }); }
    const r = rs.map((x) => x.r), m = d3.mean(r), sd = d3.deviation(r) || 1e-12;
    const years = (rs[rs.length - 1].t - rs[0].t) / (365.25 * DAY);
    const changes = rs.reduce((a, x, i) => a + (i && Math.sign(x.held) !== Math.sign(rs[i - 1].held) ? 1 : 0), 0);
    return { curve, total: (eq - 1) * 100, cagr: eq > 0 ? (eq ** (1 / Math.max(years, 1e-6)) - 1) * 100 : -100, sharpe: m / sd * Math.sqrt(252),
      maxdd: dd * 100, years, wiped, inMarket: rs.filter((x) => x.held !== 0).length / rs.length * 100,
      trades: changes / 2 / Math.max(years, 1e-6), fees: d3.sum(rs, (x) => x.fee) * 100, funding: d3.sum(rs, (x) => x.fund) * 100 };
  }
  function yearly(rs) { const g = d3.rollup(rs, (v) => (v.reduce((a, x) => a * (1 + x.r), 1) - 1) * 100, (x) => new Date(x.t).getUTCFullYear()); return g; }

  // ---------- Lab UI
  function labUI(root) {
    const groups = d3.group(ASSETS, (a) => a.class);
    const classNames = { crypto: 'Crypto', index: 'Stock indices', stock: 'US stocks', commodity: 'Commodities', fx: 'Currencies', rates: 'Interest rates' };
    root.innerHTML = `
      <section class="section">
        <h2>Strategy lab</h2>
        <p class="muted" style="margin:6px 0 12px;max-width:85ch">Test a rule on any asset Walt lists, over any period since its data begins. Daily prices, Walt's fees and slippage, and funding as you set it. Buy and hold is drawn alongside for comparison. Nothing here trades.</p>
        <div class="form">
          <label>Asset <select id="lAsset">${[...groups].map(([c, xs]) => `<optgroup label="${classNames[c] || c}">${xs.map((a) => `<option value="${a.asset}">${a.asset.replace('LTC_proxy_', '')} (${a.from.slice(0, 4)}–)</option>`).join('')}</optgroup>`).join('')}</select></label>
          <label>Strategy <select id="lRule">${[...d3.group(Object.entries(RULES), ([, r]) => r.group)].map(([g, xs]) => `<optgroup label="${g}">${xs.map(([k, r]) => `<option value="${k}">${r.label}</option>`).join('')}</optgroup>`).join('')}</select></label>
          <span id="lParams" class="form" style="display:contents"></span>
          <label>From <input type="date" id="lFrom"></label>
          <label>To <input type="date" id="lTo"></label>
          <label>Exposure <select id="lLev">${[0.5, 1, 2, 3, 5].map((x) => `<option value="${x}" ${x === 1 ? 'selected' : ''}>${x}x</option>`).join('')}</select></label>
          <label>Funding <select id="lFund"><option value="none">none</option><option value="walt" selected>longs pay, shorts receive</option><option value="adverse">stress: every position pays</option></select></label>
          <label>Funding, % per hour <input type="number" id="lRate" step="0.001" value="0.0032"></label>
        </div>
        <div class="bar-row" style="margin-top:10px">
          <button class="run" id="lRun" type="button">Run</button>
          <button class="run ghost" id="lAll" type="button">Compare all strategies here</button>
          <div class="seg" id="lQuick"><button data-q="all">All data</button><button data-q="10">10 years</button><button data-q="5">5 years</button><button data-q="3">3 years</button><button data-q="1">1 year</button>
            <button data-q="2008">2008 crash</button><button data-q="2020">2020 crash</button><button data-q="2022">2022 bear</button></div>
        </div>
        <div id="lStatus" class="muted" style="margin-top:8px"></div>
      </section>
      <section class="section" id="lOut" hidden>
        <div class="kpis wide" id="lKpis"></div>
        <div class="bar-row" style="margin-top:12px;justify-content:space-between"><div class="label">Growth of 1 USD (strategy in color, buy and hold in grey)</div><label class="muted" style="font-size:12px"><input type="checkbox" id="lLog"> log scale</label></div>
        <div class="chart" id="lChart" style="height:300px"></div>
        <div class="label" style="margin-top:12px">Return by calendar year</div>
        <div class="chart" id="lYears" style="height:200px"></div>
      </section>
      <section class="section" id="lCmp" hidden>
        <h2 id="lCmpTitle">All strategies</h2>
        <div class="tablewrap"><table class="tbl" id="lCmpTbl"></table></div>
      </section>`;
    const $ = (id) => root.querySelector('#' + id);
    const paramUI = () => {
      const r = RULES[$('lRule').value], p = r.params || {};
      $('lParams').innerHTML = Object.entries(p).map(([k, v]) => k === 'shorts'
        ? `<label class="check"><input type="checkbox" data-p="${k}" ${v ? 'checked' : ''}> allow shorts</label>`
        : `<label>${{ fast: 'Fast average, days', slow: 'Slow average, days', lookback: 'Look back, days', days: 'Average, days' }[k] || k} <input type="number" data-p="${k}" value="${v}" min="2" step="1"></label>`).join('');
    };
    const readParams = () => { const o = {}; root.querySelectorAll('#lParams [data-p]').forEach((x) => { o[x.dataset.p] = x.type === 'checkbox' ? (x.checked ? 1 : 0) : +x.value; }); return o; };
    const setDefaultsFor = (name) => { const a = ASSETS.find((x) => x.asset === name); $('lFrom').value = a.from; $('lTo').value = a.to;
      $('lRate').value = a.class === 'fx' || a.class === 'rates' ? '0' : a.class === 'crypto' ? '0.0096' : '0.0032'; };
    $('lRule').value = 'sma_cross'; paramUI();
    $('lAsset').value = 'SPY'; setDefaultsFor('SPY');
    $('lRule').addEventListener('change', paramUI);
    $('lAsset').addEventListener('change', () => setDefaultsFor($('lAsset').value));
    root.querySelectorAll('#lQuick button').forEach((b) => b.addEventListener('click', () => {
      const a = ASSETS.find((x) => x.asset === $('lAsset').value), end = Date.parse(a.to), q = b.dataset.q;
      const range = { all: [a.from, a.to], 2008: ['2007-10-01', '2009-12-31'], 2020: ['2020-01-01', '2020-12-31'], 2022: ['2022-01-01', '2022-12-31'] }[q]
        || [ymd(new Date(end - +q * 365.25 * DAY)), a.to];
      $('lFrom').value = range[0] < a.from ? a.from : range[0]; $('lTo').value = range[1]; run();
    }));
    const opts = () => ({ from: Date.parse($('lFrom').value), to: Date.parse($('lTo').value) + DAY - 1, lev: +$('lLev').value,
      fundMode: $('lFund').value, funding: $('lFund').value === 'none' ? 0 : +$('lRate').value / 100 });
    async function prep(rule) { if (RULES[rule].needs === 'vix' && !VIX) VIX = await aux('_VIX'); if (RULES[rule].needs === 'fng' && !FNG) FNG = await aux('_FNG'); }
    async function run() {
      const name = $('lAsset').value, rule = $('lRule').value, a = ASSETS.find((x) => x.asset === name), o = opts();
      if (!(o.to > o.from)) { $('lStatus').textContent = 'The end date must be after the start date.'; return; }
      $('lStatus').textContent = 'Loading…';
      try {
        const s = await series(name); await prep(rule);
        if (RULES[rule].needs === 'fng' && a.class !== 'crypto') $('lStatus').textContent = 'Fear & Greed is a crypto index; it is shown here as an experiment.';
        else $('lStatus').textContent = '';
        const pos = RULES[rule].f(s, readParams()), rs = backtest(s, pos, a.class, o), st = stats(rs);
        const bh = stats(backtest(s, s.c.map(() => 1), a.class, { ...o, lev: 1, fundMode: 'none', funding: 0 }));
        if (!st) { $('lStatus').textContent = 'Too few days in this period.'; return; }
        renderOne(a, rule, st, bh, rs, backtest(s, s.c.map(() => 1), a.class, { ...o, lev: 1, fundMode: 'none', funding: 0 }));
      } catch (e) { $('lStatus').textContent = 'Could not run: ' + e.message; }
    }
    function renderOne(a, rule, st, bh, rs, bhrs) {
      $('lOut').hidden = false;
      const k = [
        ['Yearly return', pct(st.cagr), `buy and hold ${pct(bh.cagr)} · over ${st.years.toFixed(1)} years`, st.cagr],
        ['Total', pct(st.total, 0), `buy and hold ${pct(bh.total, 0)}${st.wiped ? ' · account wiped out' : ''}`, st.total],
        ['Worst drop', '-' + st.maxdd.toFixed(1) + '%', `buy and hold -${bh.maxdd.toFixed(1)}%`, 0],
        ['Risk-adjusted (Sharpe)', st.sharpe.toFixed(2), `buy and hold ${bh.sharpe.toFixed(2)} · above 0.5 is decent`, st.sharpe - bh.sharpe],
        ['Trading', `${st.trades.toFixed(1)} / year`, `in the market ${st.inMarket.toFixed(0)}% of days`, 0],
        ['Costs', `fees ${st.fees.toFixed(1)}%`, `funding ${st.funding > 0 ? 'paid' : 'received'} ${Math.abs(st.funding).toFixed(1)}% (sum of daily %)`, -st.funding],
      ];
      $('lKpis').innerHTML = k.map(([l, v, s2, sg]) => `<div class="kpi"><div class="label">${l}</div><div class="v ${sg > 0 ? 'up' : sg < 0 ? 'down' : ''}">${v}</div><div class="s">${s2}</div></div>`).join('');
      drawGrowth($('lChart'), st.curve, bh.curve, $('lLog').checked);
      $('lLog').onchange = () => drawGrowth($('lChart'), st.curve, bh.curve, $('lLog').checked);
      drawYears($('lYears'), yearly(rs), yearly(bhrs));
    }
    async function compareAll() {
      const name = $('lAsset').value, a = ASSETS.find((x) => x.asset === name), o = opts();
      $('lStatus').textContent = 'Running every strategy…';
      try {
        const s = await series(name);
        const rows = [];
        for (const [k, r] of Object.entries(RULES)) {
          if (r.needs === 'fng' && a.class !== 'crypto') continue;
          if (r.needs === 'vix' && !['stock', 'index'].includes(a.class)) continue;
          await prep(k);
          const variants = k === 'sma_cross' ? [{ fast: 20, slow: 100, shorts: 0 }, { fast: 50, slow: 200, shorts: 0 }, { fast: 50, slow: 200, shorts: 1 }]
            : k === 'momentum' ? [{ lookback: 63, shorts: 0 }, { lookback: 126, shorts: 0 }, { lookback: 252, shorts: 0 }, { lookback: 252, shorts: 1 }] : [r.params || {}];
          for (const p of variants) {
            const st = stats(backtest(s, r.f(s, p), a.class, k === 'buy_and_hold' ? { ...o } : o));
            if (st) rows.push({ k, label: r.label + (Object.keys(p).length ? ' (' + Object.entries(p).map(([a2, b]) => a2 === 'shorts' ? (b ? 'with shorts' : 'long only') : `${a2} ${b}`).join(', ') + ')' : ''), ...st });
          }
        }
        rows.sort((x, y) => y.sharpe - x.sharpe);
        $('lCmp').hidden = false;
        $('lCmpTitle').textContent = `All strategies on ${name}, ${$('lFrom').value} to ${$('lTo').value}, ${o.lev}x, funding ${o.fundMode === 'none' ? 'none' : (o.funding * 100).toFixed(4) + '%/h'}`;
        $('lCmpTbl').innerHTML = `<thead><tr><th>Strategy</th><th>Yearly</th><th>Total</th><th>Worst drop</th><th>Sharpe</th><th>Trades / yr</th><th>In market</th></tr></thead><tbody>` +
          rows.map((r) => `<tr><td>${r.label}</td><td class="${r.cagr >= 0 ? 'up' : 'down'}">${pct(r.cagr)}</td><td>${pct(r.total, 0)}</td><td>-${r.maxdd.toFixed(1)}%</td><td><b>${r.sharpe.toFixed(2)}</b></td><td>${r.trades.toFixed(1)}</td><td>${r.inMarket.toFixed(0)}%</td></tr>`).join('') + '</tbody>';
        $('lStatus').textContent = '';
        $('lCmp').scrollIntoView({ behavior: 'smooth', block: 'start' });
      } catch (e) { $('lStatus').textContent = 'Could not run: ' + e.message; }
    }
    $('lRun').addEventListener('click', run);
    $('lAll').addEventListener('click', compareAll);
    run();
  }

  function drawGrowth(el, cur, bh, log) {
    el.replaceChildren();
    const W = el.clientWidth || 600, H = el.clientHeight || 300, m = { t: 10, r: 52, b: 22, l: 4 };
    const all = cur.concat(bh).map((d) => d.v).filter((v) => v > 0);
    const x = d3.scaleUtc().domain(d3.extent(cur, (d) => d.t)).range([m.l, W - m.r]);
    const y = (log ? d3.scaleLog() : d3.scaleLinear()).domain(d3.extent(all.concat([1]))).nice().range([H - m.b, m.t]);
    const svg = d3.select(el).append('svg').attr('viewBox', `0 0 ${W} ${H}`);
    const ticks = y.ticks(5).filter((v, i, a) => !log || a.length < 8 || i % 2 === 0);
    svg.append('g').selectAll('line').data(ticks).join('line').attr('x1', m.l).attr('x2', W - m.r).attr('y1', y).attr('y2', y).attr('stroke', css('--grid'));
    svg.append('g').selectAll('text').data(ticks).join('text').attr('x', W - m.r + 6).attr('y', (v) => y(v) + 3.5).text((v) => (v >= 100 ? d3.format(',.0f')(v) : d3.format('.2~f')(v)));
    svg.append('g').attr('transform', `translate(0,${H - m.b})`).call(d3.axisBottom(x).ticks(Math.max(2, W / 100)).tickSize(0)).call((g) => g.select('.domain').remove()).selectAll('text').attr('dy', '1.2em');
    svg.append('line').attr('x1', m.l).attr('x2', W - m.r).attr('y1', y(1)).attr('y2', y(1)).attr('stroke', css('--faint')).attr('stroke-dasharray', '4 4');
    const ln = d3.line().defined((d) => d.v > 0).x((d) => x(d.t)).y((d) => y(d.v));
    svg.append('path').datum(bh).attr('d', ln).attr('fill', 'none').attr('stroke', css('--faint')).attr('stroke-width', 1.4);
    const end = cur[cur.length - 1].v;
    svg.append('path').datum(cur).attr('d', ln).attr('fill', 'none').attr('stroke', end >= 1 ? css('--up') : css('--down')).attr('stroke-width', 2);
    const tip = d3.select(el).append('div').attr('class', 'tip').style('display', 'none');
    const g = svg.append('line').attr('y1', m.t).attr('y2', H - m.b).attr('stroke', css('--faint')).style('display', 'none');
    svg.append('rect').attr('x', m.l).attr('width', W - m.l - m.r).attr('height', H).attr('fill', 'transparent')
      .on('pointermove', (ev) => { const t = +x.invert(d3.pointer(ev)[0]); const i = Math.min(cur.length - 1, d3.bisector((d) => d.t).center(cur, t)), c = cur[i], b = bh[Math.min(bh.length - 1, d3.bisector((d) => d.t).center(bh, t))];
        g.attr('x1', x(c.t)).attr('x2', x(c.t)).style('display', null);
        tip.html(`<div class="t">${ymd(new Date(c.t))}</div>strategy <b>${c.v.toFixed(3)}</b><br>buy and hold ${b.v.toFixed(3)}`).style('display', null);
        const px = x(c.t) / W * el.clientWidth, tw = tip.node().offsetWidth; tip.style('left', (px + 12 + tw > el.clientWidth ? px - tw - 12 : px + 12) + 'px').style('top', '6px'); })
      .on('pointerleave', () => { tip.style('display', 'none'); g.style('display', 'none'); });
  }
  function drawYears(el, ys, bys) {
    el.replaceChildren();
    const years = [...ys.keys()].sort();
    const W = el.clientWidth || 600, H = el.clientHeight || 200, m = { t: 10, r: 40, b: 22, l: 4 };
    const x = d3.scaleBand().domain(years).range([m.l, W - m.r]).padding(0.2), x2 = d3.scaleBand().domain(['s', 'b']).range([0, x.bandwidth()]).padding(0.1);
    const vals = years.flatMap((yy) => [ys.get(yy), bys.get(yy) ?? 0]);
    const y = d3.scaleLinear().domain(d3.extent(vals.concat([0]))).nice(4).range([H - m.b, m.t]);
    const svg = d3.select(el).append('svg').attr('viewBox', `0 0 ${W} ${H}`);
    svg.append('g').selectAll('line').data(y.ticks(4)).join('line').attr('x1', m.l).attr('x2', W - m.r).attr('y1', y).attr('y2', y).attr('stroke', css('--grid'));
    svg.append('g').selectAll('text').data(y.ticks(4)).join('text').attr('x', W - m.r + 6).attr('y', (v) => y(v) + 3.5).text((v) => v + '%');
    const g = svg.append('g');
    years.forEach((yy) => {
      const s = ys.get(yy), b = bys.get(yy) ?? 0;
      g.append('rect').attr('x', x(yy) + x2('b')).attr('width', x2.bandwidth()).attr('y', y(Math.max(0, b))).attr('height', Math.abs(y(b) - y(0))).attr('fill', css('--faint')).attr('fill-opacity', .45).append('title').text(`${yy} buy and hold ${pct(b)}`);
      g.append('rect').attr('x', x(yy) + x2('s')).attr('width', x2.bandwidth()).attr('y', y(Math.max(0, s))).attr('height', Math.abs(y(s) - y(0))).attr('fill', s >= 0 ? css('--up') : css('--down')).append('title').text(`${yy} strategy ${pct(s)}`);
    });
    const every = Math.ceil(years.length / Math.max(2, W / 46));
    svg.append('g').selectAll('text').data(years.filter((_, i) => i % every === 0)).join('text').attr('x', (yy) => x(yy) + x.bandwidth() / 2).attr('y', H - 6).attr('text-anchor', 'middle').text((yy) => yy);
  }

  // ---------- Research results page
  function researchUI(root, rows) {
    const nice = { tsmom_63d_ls: 'Momentum 3 months, long and short', tsmom_63d_long: 'Momentum 3 months, long only', tsmom_126d_ls: 'Momentum 6 months, long and short', tsmom_126d_long: 'Momentum 6 months, long only',
      tsmom_252d_ls: 'Momentum 12 months, long and short', tsmom_252d_long: 'Momentum 12 months, long only', tsmom_252d_volscaled: 'Momentum 12 months, volatility-sized',
      sma50_200_ls: 'Average cross 50/200, long and short', sma50_200_long: 'Average cross 50/200, long only', sma20_100_ls: 'Average cross 20/100, long and short',
      price_above_sma200_long: 'Hold only above 200-day average', rsi2_dip_in_uptrend: 'Buy panic dips in an uptrend', 'buy_2.5sigma_drop_hold5': 'Buy after a one-day crash',
      buy_3_down_days_hold3: 'Buy after 3 down days', shock_follow_hold20: 'News shock: follow', shock_fade_hold5: 'News shock: fade', turn_of_month_long: 'Turn of the month',
      vix_above_30_hold20: 'Buy when VIX > 30', 'vix_spike_1.3x_hold10': 'Buy a VIX spike', fng_extreme_fear_long: 'Fear & Greed: buy extreme fear', fng_contrarian_ls: 'Fear & Greed: contrarian' };
    const classes = ['crypto', 'index', 'stock', 'commodity', 'fx', 'rates'];
    root.innerHTML = `
      <section class="section">
        <h2>Research results</h2>
        <p class="muted" style="margin:6px 0 10px;max-width:85ch">${rows.filter((r) => r.funding === 'none').length} tests: 25 strategies on ${new Set(rows.map((r) => r.asset)).size} assets, using all the daily history available (up to 56 years), each with three funding assumptions. A test "holds up" when it made money in every era it covers, with a Sharpe of at least 0.6 and a t-statistic of at least 3. Open any of them in the Strategy lab.</p>
        <div class="form">
          <label>Funding <select id="rFund"><option value="none">none</option><option value="walt_typical" selected>Walt typical: longs pay 0.0032%/h</option><option value="adverse">stress: every position pays</option></select></label>
          <label>Asset class <select id="rClass"><option value="">all</option>${classes.map((c) => `<option>${c}</option>`).join('')}</select></label>
          <label class="check"><input type="checkbox" id="rHold"> only tests that hold up</label>
        </div>
        <div class="label" style="margin-top:14px">Median Sharpe of each strategy by asset class (green good, red bad)</div>
        <div class="tablewrap"><table class="tbl heat" id="rHeat"></table></div>
        <div class="label" style="margin-top:14px">Every test</div>
        <div class="tablewrap" style="max-height:520px;overflow:auto"><table class="tbl" id="rTbl"></table></div>
      </section>`;
    const $ = (id) => root.querySelector('#' + id);
    const P = ['to 2014', '2015-19', '2020-22', '2023-now'];
    const holds = (r) => { const v = P.map((p) => r[p]).filter((x) => x != null); return v.length >= 2 && v.every((x) => x > 0) && r.sharpe >= 0.6 && r.t >= 3; };
    let sortKey = 'sharpe', dir = -1;
    function draw() {
      const f = $('rFund').value, c = $('rClass').value, onlyHold = $('rHold').checked;
      const sel = rows.filter((r) => r.funding === f && (!c || r.class === c) && r.class !== 'basket');
      const rules = [...new Set(sel.map((r) => r.rule))];
      const med = (rule, cl) => { const v = sel.filter((r) => r.rule === rule && r.class === cl).map((r) => r.sharpe); return v.length ? d3.median(v) : null; };
      const cls = c ? [c] : classes;
      const col = d3.scaleLinear().domain([-0.5, 0, 0.6]).range([css('--down'), css('--panel'), css('--up')]).clamp(true);
      const rowsH = rules.map((ru) => ({ ru, vals: cls.map((cl) => med(ru, cl)) })).sort((a, b) => d3.mean(b.vals.filter((v) => v != null)) - d3.mean(a.vals.filter((v) => v != null)));
      $('rHeat').innerHTML = `<thead><tr><th>Strategy</th>${cls.map((cl) => `<th>${cl}</th>`).join('')}</tr></thead><tbody>` +
        rowsH.map(({ ru, vals }) => `<tr><td>${nice[ru] || ru}</td>${vals.map((v) => `<td style="background:${v == null ? 'transparent' : col(v)};${v != null && Math.abs(v) > 0.35 ? 'color:#fff;' : ''}text-align:center">${v == null ? '' : v.toFixed(2)}</td>`).join('')}</tr>`).join('') + '</tbody>';
      let list = sel.filter((r) => !onlyHold || holds(r));
      list.sort((a, b) => dir * (((a[sortKey] ?? -1e9) > (b[sortKey] ?? -1e9)) - ((a[sortKey] ?? -1e9) < (b[sortKey] ?? -1e9))));
      const cols = [['asset', 'Asset'], ['rule', 'Strategy'], ['years', 'Years'], ['cagr', 'Yearly %'], ['buyhold_cagr', 'Hold %'], ['sharpe', 'Sharpe'], ['maxdd', 'Worst drop %'], ['t', 't-stat'], ['to 2014', 'to 2014'], ['2015-19', '2015-19'], ['2020-22', '2020-22'], ['2023-now', '2023-now']];
      const bh = new Map(rows.filter((r) => r.funding === 'none' && r.buyhold_cagr != null).map((r) => [r.asset, r.buyhold_cagr]));
      $('rTbl').innerHTML = `<thead><tr>${cols.map(([k, l]) => `<th data-k="${k}" style="cursor:pointer">${l}${k === sortKey ? (dir < 0 ? ' ▼' : ' ▲') : ''}</th>`).join('')}<th>Holds up</th></tr></thead><tbody>` +
        list.slice(0, 600).map((r) => `<tr><td><b>${r.asset.replace('LTC_proxy_', '')}</b></td><td>${nice[r.rule] || r.rule}</td><td>${(r.years ?? 0).toFixed(0)}</td><td class="${r.cagr >= 0 ? 'up' : 'down'}">${(r.cagr ?? 0).toFixed(1)}</td><td>${bh.has(r.asset) ? bh.get(r.asset).toFixed(1) : ''}</td><td><b>${(r.sharpe ?? 0).toFixed(2)}</b></td><td>${(r.maxdd ?? 0).toFixed(0)}</td><td>${(r.t ?? 0).toFixed(1)}</td>${P.map((p) => `<td class="${r[p] > 0 ? 'up' : r[p] < 0 ? 'down' : ''}">${r[p] == null ? '' : r[p].toFixed(1)}</td>`).join('')}<td>${holds(r) ? '✓' : ''}</td></tr>`).join('') + '</tbody>';
      root.querySelectorAll('#rTbl th[data-k]').forEach((th) => th.addEventListener('click', () => { const k = th.dataset.k; dir = sortKey === k ? -dir : -1; sortKey = k; draw(); }));
    }
    ['rFund', 'rClass', 'rHold'].forEach((id) => $(id).addEventListener('change', draw));
    draw();
  }

  window.Lab = {
    async mount(root) {
      if (root.dataset.ready) return; root.dataset.ready = '1';
      root.innerHTML = '<p class="muted">Loading assets…</p>';
      try { ASSETS = await getJSON('data/assets.json'); labUI(root); } catch (e) { root.innerHTML = `<p class="warn">Could not load the lab data (${e.message}).</p>`; }
    },
    async mountResearch(root) {
      if (root.dataset.ready) return; root.dataset.ready = '1';
      root.innerHTML = '<p class="muted">Loading results…</p>';
      try { researchUI(root, await getJSON('data/research.json')); } catch (e) { root.innerHTML = `<p class="warn">Could not load the results (${e.message}).</p>`; }
    },
    _rules: RULES, _backtest: backtest, _stats: stats, _setAux(v, f) { VIX = v; FNG = f; },
  };
})();
