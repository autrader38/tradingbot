/* Velocity visual workspace. Synthetic data only; no trading or broker APIs. */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const state = { snapshot: null, symbol: 'ABCD', timeframe: '1m', count: 72,
    hover: null, vwap: true, levels: true, trade: null };
  const colors = { grid: '#213047', muted: '#6d819c', text: '#cad7e9', up: '#40d6a4',
    down: '#ef7b87', cyan: '#39cafa', gold: '#d8b972' };
  const money = (value) => Number(value).toFixed(2);
  const volume = (value) => value >= 1000000 ? `${(value / 1000000).toFixed(2)}M`
    : value >= 1000 ? `${(value / 1000).toFixed(1)}K` : String(value);
  const utcTime = (value, long = false) => {
    const date = new Date(value);
    return long ? `${date.toISOString().slice(0, 10)} ${date.toISOString().slice(11, 16)} UTC`
      : state.timeframe === '1D' || state.timeframe === '1W'
        ? date.toISOString().slice(5, 10) : date.toISOString().slice(11, 16);
  };
  const node = (tag, text, className) => {
    const result = document.createElement(tag);
    if (text !== undefined) result.textContent = text;
    if (className) result.className = className;
    return result;
  };

  function selectSymbol(symbol, trade = null) {
    if (!state.snapshot.scanner.rows.some((row) => row.symbol === symbol)) return;
    state.symbol = symbol;
    state.trade = trade;
    state.hover = null;
    $('chart-symbol').textContent = symbol;
    $('symbol-tile').textContent = symbol[0];
    document.querySelectorAll('#scanner-rows tr').forEach((row) => {
      row.classList.toggle('selected', row.dataset.symbol === symbol);
      row.querySelector('button').setAttribute('aria-pressed', String(row.dataset.symbol === symbol));
    });
    const row = state.snapshot.scanner.rows.find((item) => item.symbol === symbol);
    $('chart-price').textContent = money(series().candles.at(-1).close);
    $('chart-change').textContent = `${row.change_percent > 0 ? '+' : ''}${row.change_percent.toFixed(2)}% · DEMO`;
    $('chart-change').className = row.change_percent >= 0 ? 'positive' : 'negative';
    drawChart();
  }

  function renderScanner() {
    $('scanner-rows').replaceChildren(...state.snapshot.scanner.rows.map((row) => {
      const tr = node('tr');
      tr.dataset.symbol = row.symbol;
      const symbol = node('td');
      const button = node('button', row.symbol, 'symbol-button');
      button.type = 'button';
      button.setAttribute('aria-label', `Show ${row.symbol} demo chart`);
      button.append(node('span', 'SYNTHETIC'));
      symbol.append(button);
      const change = `${row.change_percent >= 0 ? '+' : ''}${row.change_percent.toFixed(2)}%`;
      tr.append(symbol, node('td', money(row.price)), node('td', change, row.change_percent >= 0 ? 'positive' : 'negative'));
      for (const [label, value] of [['Vol', volume(row.volume)], ['Rel vol', `${row.relative_volume.toFixed(1)}×`],
        ['Setup', row.setup], ['Score', String(row.score)], ['Status', row.status]]) {
        const td = node('td');
        td.append(node('span', label, 'mini-label'));
        td.append(node('span', value, label === 'Status' ? `state-pill ${row.status.toLowerCase()}` : ''));
        tr.append(td);
      }
      tr.addEventListener('click', () => selectSymbol(row.symbol));
      return tr;
    }));
  }

  function renderTrades() {
    $('trade-rows').replaceChildren(...state.snapshot.trades.rows.map((trade) => {
      const row = node('tr');
      const symbol = node('td');
      const button = node('button', trade.symbol, 'trade-button');
      button.type = 'button';
      button.setAttribute('aria-label', `Explore ${trade.symbol} demo trade markers`);
      button.append(node('small', trade.setup));
      button.addEventListener('click', () => {
        setTimeframe('1m');
        selectSymbol(trade.symbol, trade);
        $('charts').scrollIntoView({ block: 'center', behavior: 'auto' });
      });
      symbol.append(button);
      row.append(symbol, node('td', money(trade.entry)), node('td', money(trade.exit)), node('td', String(trade.shares)),
        node('td', `${trade.pnl >= 0 ? '+' : '−'}$${money(Math.abs(trade.pnl))}`, trade.pnl >= 0 ? 'positive' : 'negative'));
      const outcome = node('td');
      outcome.append(node('span', trade.outcome, `outcome ${trade.pnl < 0 ? 'negative loss' : 'positive'}`));
      row.append(outcome, node('td', trade.time, 'muted'));
      return row;
    }));
  }

  function renderActivity() {
    $('activity-items').replaceChildren(...state.snapshot.activity.map((item) => {
      const row = node('li');
      const content = node('div');
      content.append(node('strong', item.label), node('p', item.detail));
      row.append(node('span', undefined, `activity-dot ${item.kind === 'safety' ? 'safety' : ''}`), content);
      return row;
    }));
  }

  function renderPerformance() {
    const metrics = state.snapshot.performance;
    $('metric-pnl').textContent = metrics.daily_pnl;
    $('metric-trades').replaceChildren(document.createTextNode(metrics.trades), node('span', 'trades', 'metric-unit'));
    $('metric-win').textContent = metrics.win_rate;
    $('metric-power').textContent = metrics.buying_power;
    $('metric-risk').textContent = metrics.risk_used;
    document.querySelector('.equity-summary strong').textContent = metrics.daily_pnl;
  }

  function series() {
    return state.snapshot.chart.series.find((item) => item.symbol === state.symbol && item.timeframe === state.timeframe);
  }

  function setTimeframe(timeframe) {
    if (!state.snapshot.chart.timeframes.includes(timeframe)) return;
    state.timeframe = timeframe;
    state.hover = null;
    state.trade = null;
    document.querySelectorAll('[data-timeframe]').forEach((button) => {
      const selected = button.dataset.timeframe === timeframe;
      button.classList.toggle('selected', selected);
      button.setAttribute('aria-pressed', String(selected));
    });
    $('chart-price').textContent = money(series().candles.at(-1).close);
    drawChart();
  }

  function canvasSize(canvas) {
    const { width, height } = canvas.getBoundingClientRect();
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    const ctx = canvas.getContext('2d');
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, width, height);
    ctx.font = '9px Consolas, monospace';
    return { ctx, width, height };
  }

  function geometry(width, height) {
    const all = series().candles;
    const data = all.slice(-Math.min(state.count, all.length));
    const left = 11, right = width - 58, top = 22, bottom = height - 108;
    const entry = state.trade ? state.trade.entry : data[Math.floor(data.length * .39)].close;
    const levels = [{ label: 'ENTRY', value: entry, color: colors.cyan },
      { label: 'STOP', value: entry * .977, color: colors.down },
      { label: 'TARGET', value: entry * 1.046, color: colors.up }];
    const prices = data.flatMap((candle) => [candle.high, candle.low]);
    if (state.levels) prices.push(...levels.map((level) => level.value));
    const minimum = Math.min(...prices), maximum = Math.max(...prices);
    const padding = Math.max((maximum - minimum) * .12, .08);
    const lo = minimum - padding, hi = maximum + padding;
    const step = (right - left) / data.length;
    return { data, left, right, top, bottom, levels, lo, hi, step,
      x: (index) => left + (index + .5) * step,
      y: (price) => bottom - (price - lo) / (hi - lo) * (bottom - top) };
  }

  function line(ctx, x1, y1, x2, y2, color, dash = []) {
    ctx.strokeStyle = color;
    ctx.lineWidth = 1;
    ctx.setLineDash(dash);
    ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
    ctx.setLineDash([]);
  }

  function drawChart() {
    if (!state.snapshot) return;
    const { ctx, width, height } = canvasSize($('candlestick-chart'));
    if (width < 80 || height < 150) return;
    const g = geometry(width, height);
    ctx.textAlign = 'left';
    for (let i = 0; i <= 5; i++) {
      const price = g.lo + (g.hi - g.lo) * i / 5;
      const y = g.y(price);
      line(ctx, g.left, y, g.right, y, colors.grid);
      ctx.fillStyle = colors.muted;
      ctx.fillText(money(price), g.right + 9, y + 3);
    }
    const volumeTop = height - 83, volumeBottom = height - 26;
    line(ctx, g.left, volumeTop - 9, g.right, volumeTop - 9, colors.grid);
    const maxVolume = Math.max(...g.data.map((candle) => candle.volume), 1);
    const candleWidth = Math.max(1, Math.min(11, g.step * .65));
    g.data.forEach((candle, i) => {
      const x = g.x(i), up = candle.close >= candle.open;
      const color = up ? colors.up : colors.down;
      line(ctx, x, g.y(candle.high), x, g.y(candle.low), color);
      ctx.fillStyle = color;
      ctx.fillRect(x - candleWidth / 2, Math.min(g.y(candle.open), g.y(candle.close)), candleWidth,
        Math.max(1.2, Math.abs(g.y(candle.close) - g.y(candle.open))));
      ctx.fillStyle = up ? '#40d6a43d' : '#ef7b8740';
      const bar = candle.volume / maxVolume * (volumeBottom - volumeTop);
      ctx.fillRect(x - candleWidth / 2, volumeBottom - bar, candleWidth, bar);
    });
    ctx.fillStyle = colors.muted;
    ctx.fillText('VOL · DEMO', g.left + 3, volumeTop + 3);
    const tickCount = width < 420 ? 4 : 6;
    for (let i = 0; i < tickCount; i++) {
      const index = Math.round(i * (g.data.length - 1) / (tickCount - 1));
      ctx.textAlign = 'center';
      ctx.fillText(utcTime(g.data[index].time), Math.max(g.left + 20, Math.min(g.right - 20, g.x(index))), height - 9);
    }
    ctx.textAlign = 'left';
    if (state.vwap) {
      // Synthetic typical-price VWAP for a visual overlay; not a strategy rule.
      let totalValue = 0, totalVolume = 0;
      const original = series().candles;
      const values = original.map((candle) => {
        totalValue += (candle.high + candle.low + candle.close) / 3 * candle.volume;
        totalVolume += candle.volume;
        return totalVolume ? totalValue / totalVolume : candle.close;
      }).slice(-g.data.length);
      ctx.save(); ctx.beginPath(); ctx.rect(g.left, g.top, g.right - g.left, g.bottom - g.top); ctx.clip();
      ctx.strokeStyle = colors.gold; ctx.lineWidth = 1.3; ctx.beginPath();
      values.forEach((value, i) => i ? ctx.lineTo(g.x(i), g.y(value)) : ctx.moveTo(g.x(i), g.y(value)));
      ctx.stroke(); ctx.restore();
    }
    if (state.levels) {
      for (const level of g.levels) {
        const y = g.y(level.value);
        line(ctx, g.left, y, g.right, y, `${level.color}70`, [4, 5]);
        ctx.fillStyle = '#101a29ee';
        ctx.fillRect(g.left + 3, y - 10, 96, 14);
        ctx.fillStyle = level.color; ctx.font = '8px Consolas, monospace';
        ctx.fillText(`${level.label} ${money(level.value)} · DEMO`, g.left + 6, y);
      }
      for (const [fraction, label, color, isBuy] of [[.39, 'BUY', colors.up, true], [.78, 'SELL', colors.down, false]]) {
        const i = Math.floor(g.data.length * fraction), candle = g.data[i], x = g.x(i);
        const y = g.y(isBuy ? candle.low : candle.high) + (isBuy ? 16 : -16);
        ctx.fillStyle = color; ctx.beginPath();
        ctx.moveTo(x, y + (isBuy ? -6 : 6));
        ctx.lineTo(x - 3, y); ctx.lineTo(x + 3, y); ctx.closePath(); ctx.fill();
        ctx.font = 'bold 7px Segoe UI, sans-serif'; ctx.textAlign = 'center';
        ctx.fillText(label, x, y + (isBuy ? 10 : -5));
      }
    }
    ctx.font = '9px Consolas, monospace'; ctx.textAlign = 'left';
    const last = g.data.at(-1), lastY = g.y(last.close);
    line(ctx, g.left, lastY, g.right, lastY, '#39cafa40', [2, 4]);
    ctx.fillStyle = '#12344b'; ctx.fillRect(g.right + 2, lastY - 8, 53, 16);
    ctx.fillStyle = colors.cyan; ctx.fillText(money(last.close), g.right + 8, lastY + 3);
    if (state.hover !== null) {
      const index = Math.max(0, Math.min(g.data.length - 1, state.hover.index));
      const x = g.x(index), y = Math.max(g.top, Math.min(g.bottom, state.hover.y));
      line(ctx, x, g.top, x, volumeBottom, '#9fb6cf77', [3, 4]);
      line(ctx, g.left, y, g.right, y, '#9fb6cf77', [3, 4]);
      tooltip(g.data[index], x, y, width, height);
    } else {
      $('chart-tooltip').hidden = true;
      readout(last);
    }
    $('chart-context').textContent = `${state.symbol} · ${state.timeframe} · ${state.trade ? 'Example trade markers' : 'Synthetic candles'} · UTC`;
  }

  function readout(candle) {
    $('ohlc-readout').textContent = `${utcTime(candle.time, true)}  O ${money(candle.open)}  H ${money(candle.high)}  L ${money(candle.low)}  C ${money(candle.close)}  VOL ${volume(candle.volume)} · DEMO`;
  }

  function tooltip(candle, x, y, width, height) {
    const tip = $('chart-tooltip');
    const list = node('dl');
    for (const [label, value] of [['Open', money(candle.open)], ['High', money(candle.high)],
      ['Low', money(candle.low)], ['Close', money(candle.close)], ['Volume', candle.volume.toLocaleString('en-US')]]) {
      list.append(node('dt', label), node('dd', value));
    }
    tip.replaceChildren(node('strong', `${utcTime(candle.time, true)} · DEMO`), list);
    tip.hidden = false;
    tip.style.left = `${Math.max(6, Math.min(width - tip.offsetWidth - 6, x + 14))}px`;
    tip.style.top = `${Math.max(6, Math.min(height - tip.offsetHeight - 6, y + 14))}px`;
    readout(candle);
  }

  function drawEquity() {
    if (!state.snapshot) return;
    const { ctx, width, height } = canvasSize($('equity-chart'));
    const values = state.snapshot.performance.equity.values;
    const lo = Math.min(...values) - 60, hi = Math.max(...values) + 60;
    const x = (i) => 5 + i / (values.length - 1) * (width - 10);
    const y = (v) => height - 15 - (v - lo) / (hi - lo) * (height - 25);
    for (let i = 1; i <= 3; i++) line(ctx, 0, height * i / 4, width, height * i / 4, '#23314680');
    const fill = ctx.createLinearGradient(0, 0, 0, height);
    fill.addColorStop(0, '#39cafa25'); fill.addColorStop(1, '#39cafa00');
    ctx.beginPath(); ctx.moveTo(x(0), height);
    values.forEach((value, i) => ctx.lineTo(x(i), y(value)));
    ctx.lineTo(x(values.length - 1), height); ctx.closePath(); ctx.fillStyle = fill; ctx.fill();
    ctx.beginPath(); values.forEach((value, i) => i ? ctx.lineTo(x(i), y(value)) : ctx.moveTo(x(i), y(value)));
    ctx.lineWidth = 1.8; ctx.strokeStyle = colors.cyan; ctx.stroke();
    ctx.fillStyle = colors.cyan; ctx.beginPath(); ctx.arc(x(values.length - 1), y(values.at(-1)), 3, 0, Math.PI * 2); ctx.fill();
  }

  function zoom(amount) {
    state.count = Math.max(24, Math.min(128, state.count + amount));
    state.hover = null;
    drawChart();
  }

  function bindInteractions() {
    document.querySelectorAll('[data-timeframe]').forEach((button) => {
      button.addEventListener('click', () => setTimeframe(button.dataset.timeframe));
    });
    for (const [id, property] of [['vwap-toggle', 'vwap'], ['levels-toggle', 'levels']]) {
      $(id).addEventListener('click', () => {
        state[property] = !state[property];
        $(id).classList.toggle('on', state[property]);
        $(id).setAttribute('aria-pressed', String(state[property]));
        drawChart();
      });
    }
    $('zoom-in').addEventListener('click', () => zoom(-12));
    $('zoom-out').addEventListener('click', () => zoom(12));
    $('reset-chart').addEventListener('click', () => { state.count = 72; state.hover = null; drawChart(); });
    const canvas = $('candlestick-chart');
    canvas.addEventListener('pointermove', (event) => {
      const rect = canvas.getBoundingClientRect(), g = geometry(rect.width, rect.height);
      const x = event.clientX - rect.left;
      state.hover = x < g.left || x > g.right ? null
        : { index: Math.floor((x - g.left) / g.step), y: event.clientY - rect.top };
      drawChart();
    });
    canvas.addEventListener('pointerleave', () => { state.hover = null; drawChart(); });
    canvas.addEventListener('blur', () => { state.hover = null; drawChart(); });
    canvas.addEventListener('keydown', (event) => {
      if (event.key === '+' || event.key === '=') { event.preventDefault(); zoom(-12); }
      else if (event.key === '-') { event.preventDefault(); zoom(12); }
      else if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
        event.preventDefault();
        const rect = canvas.getBoundingClientRect(), g = geometry(rect.width, rect.height);
        const index = Math.max(0, Math.min(g.data.length - 1,
          (state.hover ? state.hover.index : g.data.length - 1) + (event.key === 'ArrowLeft' ? -1 : 1)));
        state.hover = { index, y: g.y(g.data[index].close) };
        drawChart();
      } else if (event.key === 'Escape') { state.hover = null; drawChart(); }
    });
    new ResizeObserver(() => { drawChart(); drawEquity(); }).observe($('chart-wrap'));
    new ResizeObserver(drawEquity).observe($('equity-chart').parentElement);
  }

  document.querySelectorAll('[data-section]').forEach((button) => {
    button.addEventListener('click', () => {
      document.querySelectorAll('[data-section]').forEach((other) => {
        other.classList.toggle('active', other === button);
        if (other === button) other.setAttribute('aria-current', 'page');
        else other.removeAttribute('aria-current');
      });
      $(button.dataset.section).scrollIntoView({ block: 'start',
        behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
    });
  });

  async function start() {
    try {
      const response = await fetch('/api/snapshot', { cache: 'no-store', credentials: 'omit' });
      if (!response.ok) throw new Error('snapshot unavailable');
      const snapshot = await response.json();
      if (snapshot.system.mode !== 'PAPER' || snapshot.system.trading_authority !== 'BLOCKED'
          || snapshot.system.live_supported !== false || snapshot.system.demo_market_data !== true
          || !snapshot.scanner.demo || !snapshot.chart.demo || !snapshot.performance.demo || !snapshot.trades.demo
          || snapshot.evidence.active !== false) throw new Error('unsupported snapshot');
      state.snapshot = snapshot;
      renderScanner(); renderTrades(); renderActivity(); renderPerformance(); bindInteractions(); selectSymbol(state.symbol); drawEquity();
      $('health-label').textContent = 'LOCAL UI READY';
    } catch {
      $('load-error').hidden = false;
      $('health-label').textContent = 'DATA UNAVAILABLE';
    }
  }
  start();
})();
