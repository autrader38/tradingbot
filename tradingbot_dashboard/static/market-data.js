/* Optional read-only EOD surface. No provider credential or external request. */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const node = (tag, text) => { const n = document.createElement(tag); n.textContent = text; return n; };
  let ready = false, busy = false, history = [];
  const fieldLabels = Object.freeze({market_cap: 'Market cap', average_volume_10d: 'Average 10-day volume',
    relative_volume: 'Relative volume', volatility: 'Volatility', setup_score: 'Setup score',
    intraday_price_change: 'Intraday price change', ABCD_signal: 'ABCD signal'});
  const reasons = Object.freeze({
    PREVIEW_DISABLED: 'Preview disabled. Start with --market-data-preview to opt in; no provider request has been made.',
    KEY_NOT_CONFIGURED: 'Provider key unavailable. Configure MASSIVE_API_KEY securely on the server, then restart. Do not enter credentials here.',
    USER_LOAD_REQUIRED: 'Ready for a manual EOD load. No provider request has been made. Select one ticker and load a bar or daily history.',
    AUTHENTICATION_FAILED: 'Provider authentication failed. Check the server-side key; no credential details are displayed.',
    ACCESS_DENIED: 'Provider denied access. This account may lack the required entitlement.',
    NOT_FOUND: 'Provider did not find the requested observation.',
    RATE_LIMITED: 'Request allowance or provider cooldown reached. Wait before manually retrying.',
    REQUEST_IN_PROGRESS: 'A provider request is already in progress.',
    PROVIDER_UNAVAILABLE: 'Provider unavailable. Wait before manually retrying.',
    REDIRECT_REJECTED: 'Provider redirect rejected; no other host was contacted.',
    TIMEOUT: 'Provider request timed out. No automatic retry.',
    NETWORK_UNAVAILABLE: 'Provider network unavailable. No automatic retry.',
    INVALID_RESPONSE: 'Provider response failed validation. No invalid source values are shown.',
    NO_BAR_AVAILABLE: 'No source bar returned. Absence does not establish that no trading occurred.',
    MISSING_SOURCE_FIELDS: 'Source fields are missing. This observation is incomplete.',
    ADJUSTMENT_MISMATCH: 'Provider adjustment status differs from the requested split-adjusted policy.',
    END_OF_DAY_NOT_ESTABLISHED: 'Delayed or current-session source data; completed EOD status is not established.',
    OLDER_THAN_FOUR_CALENDAR_DAYS: 'Source bar is older than four calendar days. This is a conservative age rule, not an exchange calendar.',
    VALIDATED_DAILY_SOURCE: 'Validated historical EOD observation. Recency does not prove the latest session is complete.'
  });
  function controls() {
    for (const id of ['market-ticker', 'market-load-previous', 'market-load-history']) $(id).disabled = !ready || busy;
  }
  function status(state, reason, retry = 0) {
    const allowed = ['REAL END-OF-DAY', 'DELAYED', 'STALE', 'UNAVAILABLE'];
    $('market-state').textContent = allowed.includes(state) ? state : 'UNAVAILABLE';
    $('market-state').classList.toggle('eod', state === 'REAL END-OF-DAY');
    $('market-state').classList.toggle('aged', state === 'STALE' || state === 'DELAYED');
    $('market-message').textContent = `${reasons[reason] || 'Observation unavailable.'}${retry > 0 ? ` Retry after at least ${retry} seconds; no automatic retry.` : ''}`;
  }
  function clearObservation() {
    $('market-observation').hidden = true; $('real-history-wrap').hidden = true;
    $('market-values').replaceChildren(); $('real-history-rows').replaceChildren();
    history = [];
  }
  async function load(view) {
    if (!ready || busy) return;
    busy = true; controls(); clearObservation(); status('UNAVAILABLE', 'USER_LOAD_REQUIRED');
    $('market-message').textContent = 'Requesting one historical source observation…';
    try {
      const response = await fetch(`/api/market-data/observation?ticker=${encodeURIComponent($('market-ticker').value)}&view=${view}`,
        {cache: 'no-store', credentials: 'omit', mode: 'same-origin', headers: {'X-Velocity-Market-Preview': '1'}});
      if (!response.ok) throw new Error('unavailable');
      const result = await response.json();
      status(result.state, result.reason, result.retry_after_seconds);
      if (!Array.isArray(result.bars) || !result.bars.length) return;
      const bar = result.bars.at(-1);
      $('market-observation').hidden = false;
      $('market-source').textContent = `${bar.ticker} · Massive · Session ${bar.session_date} ET · ${result.cached ? 'CACHED SOURCE' : 'PROVIDER RESPONSE'} · ${result.state}`;
      $('market-values').replaceChildren(...['open', 'high', 'low', 'close', 'volume'].flatMap((key) =>
        [node('dt', key === 'volume' ? 'DAILY VOLUME' : key.toUpperCase()), node('dd', bar[key] === null ? 'UNAVAILABLE' : bar[key])]));
      $('market-provenance').textContent = `Source timestamp: ${bar.source_at} (${bar.source_timestamp_ms} ms). ${result.timestamp_semantics}. Retrieved: ${result.retrieved_at}. Split adjusted: ${result.adjusted_for_splits === null ? 'UNAVAILABLE' : String(result.adjusted_for_splits)}. Provider status: ${result.provider_status}. ${result.market_session}.`;
      $('market-missing').textContent = `NOT A SCANNER RESULT · Missing qualification: ${result.missing_scanner_fields.map((field) => fieldLabels[field]).join(', ')}.${result.missing_fields.length ? ` Missing source: ${result.missing_fields.join(', ')}.` : ''}`;
      $('market-quality-list').replaceChildren(...result.quality.map((quality) => node('li', quality.replaceAll('_', ' '))));
      if (view === 'history' && ['REAL END-OF-DAY', 'DELAYED', 'STALE'].includes(result.state) && result.missing_fields.length === 0) {
        history = result.bars; $('real-history-wrap').hidden = false;
        $('real-history-state').textContent = result.state;
        $('real-history-rows').replaceChildren(...history.map((bar) => {
          const row = document.createElement('tr');
          row.append(...[bar.session_date, bar.open, bar.high, bar.low, bar.close, bar.volume].map((value) => node('td', value)));
          return row;
        }));
        drawHistory();
      }
    } catch { clearObservation(); status('UNAVAILABLE', 'NETWORK_UNAVAILABLE'); }
    finally { busy = false; controls(); }
  }
  function drawHistory() {
    if (!history.length || $('real-history-wrap').hidden) return;
    const canvas = $('real-history-chart'), rect = canvas.getBoundingClientRect();
    const ratio = window.devicePixelRatio || 1, width = rect.width, height = rect.height;
    canvas.width = Math.round(width * ratio); canvas.height = Math.round(height * ratio);
    const ctx = canvas.getContext('2d'); ctx.scale(ratio, ratio); ctx.clearRect(0, 0, width, height);
    const left = 12, right = width - 72, top = 18, bottom = height - 66;
    const lows = history.map((bar) => Number(bar.low)), highs = history.map((bar) => Number(bar.high));
    const low = Math.min(...lows), high = Math.max(...highs);
    const pad = Math.max((high - low) * .05, high * .001, 1e-12);
    const lower = Math.max(0, low - pad), upper = high + pad, range = upper - lower;
    const y = (value) => top + (upper - Number(value)) / range * (bottom - top);
    const step = (right - left) / history.length;
    ctx.font = '10px Segoe UI, Arial, sans-serif';
    for (let i = 0; i <= 4; i++) {
      const level = lower + range * i / 4, position = y(level);
      ctx.strokeStyle = '#233146'; ctx.beginPath(); ctx.moveTo(left, position); ctx.lineTo(right, position); ctx.stroke();
      ctx.fillStyle = '#8b9bb2'; ctx.fillText(level < .01 ? level.toPrecision(3) : level.toFixed(2), right + 8, position + 3);
    }
    const maximumVolume = Math.max(1, ...history.map((bar) => Number(bar.volume)));
    history.forEach((bar, index) => {
      const x = left + step * (index + .5), up = Number(bar.close) >= Number(bar.open);
      ctx.strokeStyle = ctx.fillStyle = up ? '#40d6a4' : '#f17d88';
      ctx.beginPath(); ctx.moveTo(x, y(bar.high)); ctx.lineTo(x, y(bar.low)); ctx.stroke();
      ctx.fillRect(x - Math.max(1, step * .3), Math.min(y(bar.open), y(bar.close)), Math.max(2, step * .6), Math.max(1, Math.abs(y(bar.open) - y(bar.close))));
      ctx.globalAlpha = .4; ctx.fillRect(x - step * .3, height - 25 - Number(bar.volume) / maximumVolume * 32, Math.max(1, step * .6), Number(bar.volume) / maximumVolume * 32); ctx.globalAlpha = 1;
      if (index === 0 || index === history.length - 1) {
        ctx.fillStyle = '#8b9bb2'; ctx.textAlign = index === 0 ? 'left' : 'right';
        ctx.fillText(bar.session_date, index === 0 ? left : right, height - 8); ctx.textAlign = 'left';
      }
    });
  }
  async function start() {
    try {
      // Local configuration only; this route never issues a provider request.
      const response = await fetch('/api/market-data', {cache: 'no-store', credentials: 'omit', mode: 'same-origin'});
      if (!response.ok) throw new Error('unavailable');
      const config = await response.json();
      ready = config.enabled === true && config.configured === true;
      $('market-ticker').replaceChildren(...config.watchlist.map((ticker) => {
        const option = node('option', ticker); option.value = ticker; return option;
      }));
      status('UNAVAILABLE', config.reason); controls();
    } catch { status('UNAVAILABLE', 'NETWORK_UNAVAILABLE'); controls(); }
  }
  $('market-load-previous').addEventListener('click', () => load('previous'));
  $('market-load-history').addEventListener('click', () => load('history'));
  $('market-ticker').addEventListener('change', () => {
    clearObservation(); status('UNAVAILABLE', ready ? 'USER_LOAD_REQUIRED' : 'PREVIEW_DISABLED');
  });
  new ResizeObserver(drawHistory).observe($('real-history-chart').parentElement);
  start();
})();
