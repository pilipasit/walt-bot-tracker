// What-if simulator: the live bot's exact rule replayed over any past period, with Walt's costs.
// Mirrors waltbot/engine.py: signal at a bar's close, fill at the next bar's open moved 0.05 %
// against the trade, Walt's sizing (margin / (1/leverage + fee), size from the price widened by
// the contract's slippage, floored), taker fee on entry and exit, funding every bar, stop-loss
// and liquidation checked against each bar's high/low (gap through the stop fills at the open),
// no re-entry after a stop until the signal changes. Portfolio level as the live bot runs it:
// fixed margin per position from one shared balance, kill switch on the total account value.
(function () {
  const HOUR = 3600e3;
  const IV = { '1h': HOUR, '4h': 4 * HOUR, '1d': 24 * HOUR };
  const FILL_SLIP = 0.0005;
  const fmtP = (x) => { if (x == null || !isFinite(x)) return '–'; const a = Math.abs(x); const d = a >= 1000 ? 1 : a >= 10 ? 2 : a >= 1 ? 4 : 5; return x.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d }); };
  const usd = (x, s) => (x == null || !isFinite(x) ? '–' : (s && x > 0 ? '+' : '') + x.toFixed(2));
  const pct = (x) => (x == null || !isFinite(x) ? '–' : (x > 0 ? '+' : '') + x.toFixed(2) + '%');
  const day = d3.utcFormat('%Y-%m-%d');
  const when = d3.utcFormat('%d %b %Y %H:%M');
  const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  let SPEC = null;
  const cache = new Map();

  async function klinesRange(sym, iv, from, to) {
    const key = `${sym}|${iv}|${from}|${to}`;
    if (cache.has(key)) return cache.get(key);
    const out = []; let start = from;
    while (start < to) {
      const q = new URLSearchParams({ symbol: sym + 'USDT', interval: iv, startTime: start, endTime: to, limit: 1000 });
      const r = await fetch('https://api.binance.com/api/v3/klines?' + q);
      if (!r.ok) throw new Error(`Binance ${r.status} for ${sym}`);
      const rows = await r.json();
      if (!rows.length) break;
      rows.forEach((k) => out.push({ ts: k[0], o: +k[1], h: +k[2], l: +k[3], c: +k[4] }));
      start = rows[rows.length - 1][0] + IV[iv];
      if (rows.length < 1000) break;
    }
    const now = Date.now();
    const closed = out.filter((b) => b.ts + IV[iv] <= now);
    cache.set(key, closed);
    return closed;
  }

  function simulate(p, bars) {
    // p: {coins, budget, lev, margin, stopPct, fast, slow, iv, from, to, funding, fundMode, killPct, shorts}
    const step = IV[p.iv], hours = step / HOUR;
    const sig = {}, idx = {};
    for (const s of p.coins) {
      const cs = bars[s].map((b) => b.c); let sf = 0, ss = 0;
      sig[s] = new Map();
      bars[s].forEach((b, i) => {
        sf += cs[i]; ss += cs[i];
        if (i >= p.fast) sf -= cs[i - p.fast];
        if (i >= p.slow) ss -= cs[i - p.slow];
        if (i >= p.slow - 1) {
          const f = sf / p.fast, sl = ss / p.slow;
          let t = f > sl ? 1 : f < sl ? -1 : 0;
          if (t < 0 && !p.shorts) t = 0;
          sig[s].set(b.ts, { t, fast: f, slow: sl });
        }
      });
      idx[s] = new Map(bars[s].map((b) => [b.ts, b]));
    }
    const times = [...new Set(p.coins.flatMap((s) => bars[s].map((b) => b.ts)))].sort((a, b) => a - b);
    let cash = p.budget, killed = false, peak = p.budget, maxDD = 0;
    const pos = {}, pending = {}, blocked = {}, per = {}, trades = [], curve = [], waiting = {};
    const totals = { fees: 0, funding: 0, stops: 0, liqs: 0, skips: 0, opens: 0 };
    p.coins.forEach((s) => { pending[s] = 0; blocked[s] = 0; per[s] = { pnl: 0, fees: 0, funding: 0, opens: 0, stops: 0, wins: 0, closes: 0 }; });
    const spec = (s) => SPEC[s] || { fee: 0.0009, slip: 0.01, dec: 4, minBase: 0, minQuote: 1, maxLev: 10, closeout: 0.004 };
    const liqPrice = (q, s) => { const c = spec(s).closeout; return q.side > 0 ? (q.entry * q.size - q.margin + q.funding) / (q.size * (1 - c)) : (q.margin - q.funding + q.entry * q.size) / (q.size * (1 + c)); };
    const unreal = (q, price) => q.side * (price - q.entry) * q.size - q.funding;

    const close = (s, ts, price, kind) => {
      const q = pos[s]; let pnl, fee;
      if (kind === 'liquidation') { pnl = -q.margin; fee = 0; } else { fee = q.size * price * spec(s).fee; pnl = Math.max(unreal(q, price) - fee, -q.margin); }
      cash += pnl; totals.fees += fee; totals.funding += q.funding;
      const net = pnl - q.entryFee;
      per[s].pnl += pnl; per[s].fees += fee; per[s].funding += q.funding; per[s].closes++; if (net > 0) per[s].wins++;
      if (kind === 'stop') { totals.stops++; per[s].stops++; }
      if (kind === 'liquidation') totals.liqs++;
      trades.push({ s, side: q.side, open: q.ts, close: ts, entry: q.entry, exit: price, kind, net });
      delete pos[s];
    };
    const open = (s, ts, side, ref) => {
      const sp = spec(s);
      const used = Object.values(pos).reduce((a, q) => a + q.reserved, 0);
      if (cash - used < p.margin || p.margin < sp.minQuote) { if (!waiting[s]) { totals.skips++; waiting[s] = true; } return; }
      const notional = p.margin / (1 / p.lev + sp.fee);
      const worst = ref * (1 + side * sp.slip), scale = 10 ** sp.dec;
      const size = Math.floor(notional / worst * scale) / scale;
      if (size <= 0 || size < sp.minBase) { if (!waiting[s]) { totals.skips++; waiting[s] = true; } return; }
      waiting[s] = false;
      const fill = ref * (1 + side * FILL_SLIP), fee = size * fill * sp.fee;
      cash -= fee; totals.fees += fee; totals.opens++; per[s].opens++; per[s].fees += fee; per[s].pnl -= fee;
      pos[s] = { side, size, entry: fill, margin: p.margin - fee, reserved: p.margin, funding: 0, ts, entryFee: fee, stop: fill * (1 - side * p.stopPct) };
    };

    let started = false;
    for (const ts of times) {
      for (const s of p.coins) {
        const b = idx[s].get(ts); if (!b) continue;
        const have = pos[s] ? pos[s].side : 0;
        if (pending[s] !== have) {
          if (pos[s]) close(s, ts, b.o * (1 - have * FILL_SLIP), 'close');
          if (pending[s] !== 0 && pending[s] !== blocked[s]) open(s, ts, pending[s], b.o);
        }
        const q = pos[s];
        if (q) {
          const r = p.fundMode === 'adverse' ? Math.abs(p.funding) : q.side * p.funding;
          q.funding += r * q.size * b.o * hours;
          const liq = liqPrice(q, s), adverse = q.side > 0 ? b.l : b.h;
          const stopHit = (adverse - q.stop) * q.side <= 0, liqHit = (adverse - liq) * q.side <= 0;
          if (stopHit && (!liqHit || (q.stop - liq) * q.side > 0)) {
            const level = (b.o - q.stop) * q.side <= 0 ? b.o : q.stop, fill = level * (1 - q.side * FILL_SLIP);
            const side = q.side;
            if ((fill - liq) * side <= 0) close(s, ts, liq, 'liquidation'); else close(s, ts, fill, 'stop');
            blocked[s] = side;
          } else if (liqHit) { const side = q.side; close(s, ts, liq, 'liquidation'); blocked[s] = side; }
        }
      }
      // mark to market at the bar close, update the kill switch, decide next targets
      let value = cash;
      for (const s of Object.keys(pos)) { const b = idx[s].get(ts); if (b) value += unreal(pos[s], b.c); }
      const inWindow = ts + step >= p.from && ts + step <= p.to;
      if (ts + step >= p.from) {
        if (!started) { started = true; }
        curve.push({ t: ts + step, v: value });
        peak = Math.max(peak, value); maxDD = Math.max(maxDD, 1 - value / peak);
        if (!killed && value <= p.budget * (1 - p.killPct)) killed = ts + step;
      }
      for (const s of p.coins) {
        const g = sig[s].get(ts); if (!g) continue;
        if (g.t !== blocked[s]) blocked[s] = 0;
        if (g.t !== pending[s]) waiting[s] = false;
        pending[s] = inWindow && !killed && ts + step < p.to ? g.t : 0;
      }
    }
    // anything still open at the end is closed at the last close
    for (const s of Object.keys(pos)) { const last = bars[s][bars[s].length - 1]; close(s, last.ts + step, last.c * (1 - pos[s].side * FILL_SLIP), 'close'); }
    if (curve.length) curve[curve.length - 1].v = cash;
    return { curve, trades, per, totals, final: cash, maxDD, killed };
  }

  function buyHold(p, bars) {
    const share = p.budget / p.coins.length, pts = new Map();
    for (const s of p.coins) {
      const bs = bars[s].filter((b) => b.ts + IV[p.iv] >= p.from && b.ts + IV[p.iv] <= p.to);
      if (!bs.length) continue;
      const q = share / bs[0].o;
      bs.forEach((b) => pts.set(b.ts + IV[p.iv], (pts.get(b.ts + IV[p.iv]) || 0) + q * b.c));
    }
    return [...pts.entries()].sort((a, b) => a[0] - b[0]).map(([t, v]) => ({ t, v }));
  }

  // ------------------------------------------------------------- UI
  const COINS = ['BTC', 'ETH', 'SOL', 'XRP', 'DOGE', 'ADA', 'LINK', 'AVAX', 'BNB', 'DOT', 'TRX', 'NEAR'];
  function ui(root, defaults) {
    root.innerHTML = `
      <section class="section">
        <h2>What if the bot had run earlier?</h2>
        <p class="muted" style="margin:6px 0 12px;max-width:80ch">Replays the bot's exact rule on real past prices, with Walt's fees, slippage, funding, stop-losses and liquidation. Starts with the settings the real bot uses; change anything and press Run. Nothing here trades.</p>
        <div class="form">
          <label>From <input type="date" id="wFrom"></label>
          <label>To <input type="date" id="wTo"></label>
          <label>Start balance, USD <input type="number" id="wBudget" min="10" step="10"></label>
          <label>Leverage <select id="wLev">${[1, 2, 3, 5, 10, 20].map((x) => `<option value="${x}">${x}x</option>`).join('')}</select></label>
          <label>Margin per position, USD <input type="number" id="wMargin" min="1" step="1"></label>
          <label>Stop-loss, % <input type="number" id="wStop" min="0.5" step="0.5"></label>
          <label>Fast average, bars <input type="number" id="wFast" min="2" step="1"></label>
          <label>Slow average, bars <input type="number" id="wSlow" min="5" step="1"></label>
          <label>Bar size <select id="wIv"><option value="1h">1 hour</option><option value="4h">4 hours</option><option value="1d">1 day</option></select></label>
          <label>Funding <select id="wFund"><option value="none">none</option><option value="walt" selected>Walt crypto now: longs pay, shorts receive</option><option value="adverse">stress: every position pays</option></select></label>
          <label>Funding rate, % per hour <input type="number" id="wRate" step="0.001"></label>
          <label>Kill switch at loss of, % <input type="number" id="wKill" min="5" max="100" step="5"></label>
          <label class="check"><input type="checkbox" id="wShorts" checked> Allow shorts</label>
        </div>
        <div class="coinpick" id="wCoins">${COINS.map((c) => `<label class="chip"><input type="checkbox" value="${c}"> ${c}</label>`).join('')}</div>
        <div class="bar-row" style="margin-top:10px">
          <button class="run" id="wRun" type="button">Run</button>
          <div class="seg" id="wQuick">
            <button data-q="live">Live settings</button><button data-q="y2025">Year 2025</button><button data-q="y2024">Year 2024</button>
            <button data-q="90d">Last 90 days</button><button data-q="1y">Last year</button><button data-q="3y">Last 3 years</button>
          </div>
        </div>
        <div id="wStatus" class="muted" style="margin-top:8px"></div>
      </section>
      <section class="section" id="wOut" hidden>
        <div class="kpis wide" id="wKpis"></div>
        <div class="label" style="margin-top:14px">Account value vs simply holding the same coins (same start balance, no leverage, no fees)</div>
        <div class="chart" id="wChart" style="height:300px"></div>
        <div class="tablewrap"><table class="tbl" id="wPer"></table></div>
        <details style="margin-top:10px"><summary class="muted">All trades</summary><div class="tablewrap"><table class="tbl" id="wTrades"></table></div></details>
      </section>`;
    const $ = (id) => root.querySelector('#' + id);
    const set = (d) => {
      $('wFrom').value = day(new Date(d.from)); $('wTo').value = day(new Date(d.to)); $('wBudget').value = d.budget; $('wLev').value = d.lev;
      $('wMargin').value = d.margin; $('wStop').value = d.stopPct * 100; $('wFast').value = d.fast; $('wSlow').value = d.slow; $('wIv').value = d.iv;
      $('wRate').value = (d.funding * 100).toFixed(4); $('wKill').value = d.killPct * 100; $('wShorts').checked = d.shorts;
      root.querySelectorAll('#wCoins input').forEach((x) => { x.checked = d.coins.includes(x.value); });
    };
    const read = () => ({
      from: Date.parse($('wFrom').value + 'T00:00:00Z'), to: Math.min(Date.now(), Date.parse($('wTo').value + 'T23:59:59Z')),
      budget: +$('wBudget').value, lev: +$('wLev').value, margin: +$('wMargin').value, stopPct: +$('wStop').value / 100,
      fast: Math.round(+$('wFast').value), slow: Math.round(+$('wSlow').value), iv: $('wIv').value,
      fundMode: $('wFund').value, funding: $('wFund').value === 'none' ? 0 : +$('wRate').value / 100,
      killPct: +$('wKill').value / 100, shorts: $('wShorts').checked,
      coins: [...root.querySelectorAll('#wCoins input:checked')].map((x) => x.value),
    });
    set(defaults);
    root.querySelectorAll('#wQuick button').forEach((b) => b.addEventListener('click', () => {
      const now = Date.now(), d = { ...read() };
      const q = b.dataset.q;
      if (q === 'live') Object.assign(d, defaults, { from: d.from, to: d.to });
      if (q === 'y2025') { d.from = Date.UTC(2025, 0, 1); d.to = Date.UTC(2025, 11, 31, 23, 59); }
      if (q === 'y2024') { d.from = Date.UTC(2024, 0, 1); d.to = Date.UTC(2024, 11, 31, 23, 59); }
      if (q === '90d') { d.from = now - 90 * 86400e3; d.to = now; }
      if (q === '1y') { d.from = now - 365 * 86400e3; d.to = now; }
      if (q === '3y') { d.from = now - 3 * 365 * 86400e3; d.to = now; }
      if (q === 'live') { /* keep the chosen dates, reset everything else */ }
      set(d); run();
    }));
    $('wRun').addEventListener('click', run);

    async function run() {
      const p = read();
      if (!p.coins.length) { $('wStatus').textContent = 'Pick at least one coin.'; return; }
      if (!(p.to > p.from)) { $('wStatus').textContent = 'The end date must be after the start date.'; return; }
      if (p.fast >= p.slow) { $('wStatus').textContent = 'The fast average must use fewer bars than the slow one.'; return; }
      const bad = p.coins.filter((s) => SPEC[s] && p.lev > SPEC[s].maxLev);
      if (bad.length) { $('wStatus').textContent = `Walt does not allow ${p.lev}x on ${bad.join(', ')}.`; return; }
      $('wStatus').textContent = 'Loading prices…'; $('wRun').disabled = true;
      try {
        const step = IV[p.iv], warm = p.from - (p.slow + 5) * step;
        const bars = {};
        await Promise.all(p.coins.map(async (s) => { bars[s] = await klinesRange(s, p.iv, warm, p.to); }));
        const missing = p.coins.filter((s) => !bars[s].length);
        if (missing.length) throw new Error('no prices for ' + missing.join(', ') + ' in this period');
        const res = simulate(p, bars), bh = buyHold(p, bars);
        const late = p.coins.filter((s) => bars[s][0].ts > p.from - p.slow * step);
        $('wStatus').textContent = late.length ? `Note: ${late.join(', ')} started trading on Binance after the chosen start, so it joins later.` : '';
        render(p, res, bh);
      } catch (e) { $('wStatus').textContent = 'Could not run: ' + e.message; }
      $('wRun').disabled = false;
    }

    function render(p, res, bh) {
      root.querySelector('#wOut').hidden = false;
      const ret = (res.final / p.budget - 1) * 100;
      const bhRet = bh.length ? (bh[bh.length - 1].v / p.budget - 1) * 100 : null;
      const closes = res.trades.length, wins = res.trades.filter((t) => t.net > 0).length;
      const k = [
        ['Final value', usd(res.final) + ' USD', `${pct(ret)} on ${p.budget} USD`, ret],
        ['Holding the coins instead', bhRet == null ? '–' : usd(p.budget * (1 + bhRet / 100)) + ' USD', pct(bhRet), bhRet],
        ['Worst drop from a high', pct(-res.maxDD * 100), res.killed ? 'kill switch hit ' + when(new Date(res.killed)) : 'kill switch not hit', -1],
        ['Trades', String(closes), `${closes ? Math.round(wins / closes * 100) : 0}% made money · ${res.totals.stops} stopped out · ${res.totals.liqs} liquidated`, 0],
        ['Fees paid', usd(res.totals.fees) + ' USD', `${res.totals.skips} times an entry had to wait for free balance`, 0],
        ['Funding', usd(-res.totals.funding, true) + ' USD', res.totals.funding > 0 ? 'paid' : 'received', -res.totals.funding],
      ];
      root.querySelector('#wKpis').innerHTML = k.map(([l, v, s, sign]) => `<div class="kpi"><div class="label">${l}</div><div class="v ${sign > 0 ? 'up' : sign < 0 && l !== 'Worst drop from a high' ? 'down' : ''}">${v}</div><div class="s">${s}</div></div>`).join('');
      drawCurve(root.querySelector('#wChart'), p, res.curve, bh);
      const per = res.per;
      root.querySelector('#wPer').innerHTML = `<thead><tr><th>Coin</th><th>Trades</th><th>Made money</th><th>Stopped out</th><th>Result after fees, USD</th><th>Fees</th><th>Funding paid</th></tr></thead><tbody>` +
        p.coins.map((s) => `<tr><td><b>${s}</b></td><td>${per[s].closes}</td><td>${per[s].closes ? Math.round(per[s].wins / per[s].closes * 100) + '%' : '–'}</td><td>${per[s].stops}</td><td class="${per[s].pnl >= 0 ? 'up' : 'down'}">${usd(per[s].pnl, true)}</td><td>${usd(per[s].fees)}</td><td>${usd(per[s].funding, true)}</td></tr>`).join('') + '</tbody>';
      root.querySelector('#wTrades').innerHTML = `<thead><tr><th>Opened</th><th>Closed</th><th>Coin</th><th>Side</th><th>Entry</th><th>Exit</th><th>How</th><th>Result, USD</th></tr></thead><tbody>` +
        [...res.trades].reverse().map((t) => `<tr><td>${when(new Date(t.open))}</td><td>${when(new Date(t.close))}</td><td>${t.s}</td><td>${t.side > 0 ? 'long' : 'short'}</td><td>${fmtP(t.entry)}</td><td>${fmtP(t.exit)}</td><td>${t.kind}</td><td class="${t.net >= 0 ? 'up' : 'down'}">${usd(t.net, true)}</td></tr>`).join('') + '</tbody>';
    }
  }

  function drawCurve(el, p, curve, bh) {
    el.replaceChildren();
    if (curve.length < 2) { el.textContent = 'Not enough data in this period.'; return; }
    const W = el.clientWidth || 600, H = el.clientHeight || 300, m = { t: 10, r: 56, b: 22, l: 4 };
    const x = d3.scaleUtc().domain(d3.extent(curve, (d) => d.t)).range([m.l, W - m.r]);
    const y = d3.scaleLinear().domain(d3.extent(curve.map((d) => d.v).concat(bh.map((d) => d.v), [p.budget]))).nice(5).range([H - m.b, m.t]);
    const svg = d3.select(el).append('svg').attr('viewBox', `0 0 ${W} ${H}`);
    svg.append('g').selectAll('line').data(y.ticks(5)).join('line').attr('x1', m.l).attr('x2', W - m.r).attr('y1', y).attr('y2', y).attr('stroke', css('--grid'));
    svg.append('g').selectAll('text').data(y.ticks(5)).join('text').attr('x', W - m.r + 6).attr('y', (v) => y(v) + 3.5).text((v) => v.toFixed(0));
    svg.append('g').attr('transform', `translate(0,${H - m.b})`).call(d3.axisBottom(x).ticks(Math.max(2, W / 110)).tickSize(0)).call((g) => g.select('.domain').remove()).selectAll('text').attr('dy', '1.2em');
    svg.append('line').attr('x1', m.l).attr('x2', W - m.r).attr('y1', y(p.budget)).attr('y2', y(p.budget)).attr('stroke', css('--faint')).attr('stroke-dasharray', '4 4');
    svg.append('path').datum(bh).attr('d', d3.line().x((d) => x(d.t)).y((d) => y(d.v))).attr('fill', 'none').attr('stroke', css('--faint')).attr('stroke-width', 1.5);
    const end = curve[curve.length - 1].v, col = end >= p.budget ? css('--up') : css('--down');
    svg.append('path').datum(curve).attr('d', d3.line().x((d) => x(d.t)).y((d) => y(d.v))).attr('fill', 'none').attr('stroke', col).attr('stroke-width', 2);
    const tip = d3.select(el).append('div').attr('class', 'tip').style('display', 'none');
    const g = svg.append('line').attr('y1', m.t).attr('y2', H - m.b).attr('stroke', css('--faint')).style('display', 'none');
    svg.append('rect').attr('x', m.l).attr('width', W - m.l - m.r).attr('height', H).attr('fill', 'transparent')
      .on('pointermove', (ev) => {
        const t = +x.invert(d3.pointer(ev)[0]);
        const c = curve[Math.min(curve.length - 1, d3.bisector((d) => d.t).center(curve, t))];
        const b = bh.length ? bh[Math.min(bh.length - 1, d3.bisector((d) => d.t).center(bh, t))] : null;
        g.attr('x1', x(c.t)).attr('x2', x(c.t)).style('display', null);
        tip.html(`<div class="t">${when(new Date(c.t))} UTC</div>bot <b>${c.v.toFixed(2)}</b> USD` + (b ? `<br>holding ${b.v.toFixed(2)} USD` : '')).style('display', null);
        const px = x(c.t) / W * el.clientWidth, tw = tip.node().offsetWidth;
        tip.style('left', (px + 12 + tw > el.clientWidth ? px - tw - 12 : px + 12) + 'px').style('top', '6px');
      })
      .on('pointerleave', () => { tip.style('display', 'none'); g.style('display', 'none'); });
  }

  window.WhatIf = {
    async mount(root, bot) {
      if (root.dataset.ready) return;
      root.dataset.ready = '1';
      try { SPEC = await (await fetch('data/contracts.json')).json(); } catch { SPEC = {}; }
      const cfg = bot.config || {};
      const now = Date.now();
      ui(root, {
        from: Date.UTC(2025, 0, 1), to: Date.UTC(2025, 11, 31, 23, 59), budget: cfg.budget || 100, lev: cfg.leverage || 3,
        margin: cfg.margin_per_position || 20, stopPct: cfg.stop_loss_pct || 0.03, fast: bot.fast || 20, slow: bot.slow || 100,
        iv: '4h', funding: 0.000096, fundMode: 'walt', killPct: cfg.max_drawdown_pct || 0.5, shorts: true,
        coins: cfg.symbols || ['BTC', 'ETH', 'SOL', 'XRP'],
      });
      void now;
    },
    _simulate: simulate,
    _setSpec(s) { SPEC = s; },
  };
})();
