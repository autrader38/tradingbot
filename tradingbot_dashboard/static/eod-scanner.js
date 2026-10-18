/* Independent EOD research view. All filters compare exact rational values. */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const node = (tag, text) => { const n = document.createElement(tag); n.textContent = text; return n; };
  const defaults = Object.freeze({'eod-max-price': '5', 'eod-min-change': '3', 'eod-min-cap': '50',
    'eod-min-average': '1000000', 'eod-min-rvol': ''});
  const bounds = Object.freeze({'eod-max-price': ['0.000000000001', '1000000'],
    'eod-min-change': ['-100', '100000'], 'eod-min-cap': ['0', '1000000000'],
    'eod-min-average': ['0', '1000000000000000'], 'eod-min-rvol': ['0', '1000000']});
  const names = Object.freeze({price: 'EOD price', change_pct: 'close-to-close change',
    average_volume_10: 'previous ten-bar average volume', relative_volume: 'EOD relative volume', market_cap: 'dated reference market cap'});
  const messages = Object.freeze({PREVIEW_DISABLED: 'Preview disabled. Default DEMO mode makes no provider requests.',
    KEY_NOT_CONFIGURED: 'Provider key unavailable. Configure only in the server environment; never enter it here.',
    RATE_LIMITED: 'Shared request budget or cooldown reached. Wait before another manual load.',
    REQUEST_IN_PROGRESS: 'Another provider request is in progress.', ELIGIBLE_HISTORY_REQUIRED: 'Load fresh eligible daily history first; no reference request was made.',
    INVALID_RESPONSE: 'Source response failed validation. No invalid values or provider response details are displayed.',
    AUTHENTICATION_FAILED: 'Provider authentication failed.', ACCESS_DENIED: 'Provider denied access; fields remain unavailable.',
    NOT_FOUND: 'No source record found.', TIMEOUT: 'Provider request timed out.', NETWORK_UNAVAILABLE: 'Provider network unavailable.',
    PROVIDER_UNAVAILABLE: 'Provider unavailable.', REDIRECT_REJECTED: 'Provider redirect rejected.',
    MISSING_REFERENCE_FIELDS: 'Reference source fields are incomplete.', REFERENCE_DATE_STALE: 'Reference date is stale.',
    NO_BAR_AVAILABLE: 'No daily bars returned.', MISSING_SOURCE_FIELDS: 'Daily source fields are incomplete.',
    ADJUSTMENT_MISMATCH: 'Split-adjustment policy is not established.', OLDER_THAN_FOUR_CALENDAR_DAYS: 'History is stale.',
    END_OF_DAY_NOT_ESTABLISHED: 'Completed EOD data is not established.',
    VALIDATED_DAILY_SOURCE: 'Validated daily history loaded.', DATE_SCOPED_REFERENCE: 'Date-scoped reference loaded; synchronization is not proven.'});
  let rows = [], ready = false, busy = false, revision = 0;
  const compare = (a, b) => { const d = a.n * b.d - b.n * a.d; return d < 0n ? -1 : d > 0n ? 1 : 0; };
  const ratio = (value) => ({n: BigInt(value.numerator), d: BigInt(value.denominator)});
  function decimal(text) {
    if (!/^-?(?:0|[1-9]\d*)(?:\.\d{1,12})?$/.test(text) || text.length > 32) throw new Error('invalid');
    const [whole, part = ''] = text.split('.'), negative = text.startsWith('-');
    return {n: BigInt(whole.replace('-', '') + part) * (negative ? -1n : 1n), d: 10n ** BigInt(part.length)};
  }
  function inputText(value) {
    const negative = value.n < 0n, n = negative ? -value.n : value.n;
    const digits = (n * 1000000000000n / value.d).toString().padStart(13, '0');
    return `${negative ? '-' : ''}${digits.slice(0, -12)}.${digits.slice(-12)}`.replace(/\.?0+$/, '');
  }
  function readFilters() {
    const values = {}; let invalid = false;
    for (const id of Object.keys(defaults)) {
      const el = $(id);
      try {
        if (el.validity.badInput || !el.validity.valid) throw new Error('invalid');
        const value = id === 'eod-min-rvol' && el.value === '' ? null : decimal(el.value);
        if (value && (compare(value, decimal(bounds[id][0])) < 0 || compare(value, decimal(bounds[id][1])) > 0)) throw new Error('range');
        values[id] = value; el.setAttribute('aria-invalid', 'false');
      } catch { el.setAttribute('aria-invalid', 'true'); invalid = true; }
    }
    if (invalid) { $('eod-filter-feedback').textContent = 'Invalid research filter. Use finite decimal values within the displayed limits; no assessment is published.'; return null; }
    $('eod-filter-feedback').textContent = 'Browser-only research filters. Comparisons use exact values; displayed numbers may be rounded.';
    values['eod-min-cap'].n *= 1000000n;
    return values;
  }
  function assess(row, filters) {
    if (!filters) return {outcome: 'INSUFFICIENT DATA', failed: [], missing: ['valid filters']};
    const checks = [['price', filters['eod-max-price'], 'lt'], ['change_pct', filters['eod-min-change'], 'ge'],
      ['average_volume_10', filters['eod-min-average'], 'gt']];
    if ($('eod-require-cap').checked) checks.push(['market_cap', filters['eod-min-cap'], 'ge']);
    if (filters['eod-min-rvol']) checks.push(['relative_volume', filters['eod-min-rvol'], 'ge']);
    const failed = [], missing = [];
    for (const [name, threshold, op] of checks) {
      const value = row.metrics[name];
      if (value === null) { missing.push(names[name]); continue; }
      const cmp = compare(ratio(value), threshold);
      if (!(op === 'lt' ? cmp < 0 : op === 'gt' ? cmp > 0 : cmp >= 0)) failed.push(names[name]);
    }
    return {outcome: failed.length ? 'EOD CRITERIA NOT MET' : missing.length ? 'INSUFFICIENT DATA' : 'EOD CRITERIA MET', failed, missing};
  }
  function controls() {
    $('eod-ticker').disabled = !ready;
    $('eod-load-history').disabled = !ready || busy;
    $('eod-load-reference').disabled = !ready || busy || !rows.some((r) => r.ticker === $('eod-ticker').value && r.history_eligible);
  }
  function draw() {
    const filters = readFilters(), assessed = rows.map((row) => ({row, ...assess(row, filters)}));
    for (const [id, count] of [['eod-total', rows.length], ['eod-history-count', rows.filter((r) => r.history_eligible).length],
      ['eod-reference-count', rows.filter((r) => r.reference_eligible).length],
      ['eod-match-count', assessed.filter((r) => r.outcome === 'EOD CRITERIA MET').length],
      ['eod-fail-count', assessed.filter((r) => r.outcome === 'EOD CRITERIA NOT MET').length],
      ['eod-incomplete-count', assessed.filter((r) => r.outcome === 'INSUFFICIENT DATA').length]]) $(id).textContent = String(count);
    const search = $('eod-search').value.trim().toUpperCase();
    const mode = ['change_pct', 'price', 'price_desc', 'relative_volume', 'volume', 'market_cap'].includes($('eod-sort').value) ? $('eod-sort').value : 'change_pct';
    const key = mode === 'price_desc' ? 'price' : mode;
    const visible = assessed.filter((r) => r.row.ticker.includes(search) && (!$('eod-matches-only').checked || r.outcome === 'EOD CRITERIA MET'));
    visible.sort((a, b) => {
      const left = a.row.metrics[key], right = b.row.metrics[key];
      if (left === null && right !== null) return 1;
      if (left !== null && right === null) return -1;
      const cmp = left !== null && right !== null ? compare(ratio(left), ratio(right)) : 0;
      return (mode === 'price' ? cmp : -cmp) || (a.row.ticker < b.row.ticker ? -1 : a.row.ticker > b.row.ticker ? 1 : 0);
    });
    $('eod-rows').replaceChildren(...visible.map(({row, outcome, failed, missing}) => {
      const tr = document.createElement('tr'); tr.dataset.ticker = row.ticker;
      tr.append(node('td', row.ticker), ...['price', 'change_pct', 'volume', 'average_volume_10', 'relative_volume', 'market_cap']
        .map((key) => node('td', row.metrics[key] === null ? 'UNAVAILABLE' : row.metrics[key].display)));
      tr.append(node('td', `Market: ${row.market_date || 'UNAVAILABLE'} ET. Reference request: ${row.reference?.requested_date || 'UNAVAILABLE'}. Reference: ${row.reference?.state || 'UNLOADED'}. Cap as-of: UNAVAILABLE.`), node('td', row.history_state));
      const coverage = document.createElement('td'), badge = node('span', outcome); badge.className = 'eod-outcome'; badge.dataset.state = outcome;
      coverage.append(badge, node('div', failed.length ? `Failed: ${failed.join(', ')}` : missing.length ? `Missing EOD: ${missing.join(', ')}` : 'Supported EOD research subset only.'));
      if (failed.length && missing.length) coverage.append(node('div', `Also missing: ${missing.join(', ')}`));
      const details = document.createElement('details'); details.className = 'eod-detail';
      details.append(node('summary', 'Source dates & limitations'),
        node('p', `History retrieved: ${row.history_retrieved_at || 'UNAVAILABLE'}. Split adjusted: ${row.adjusted_for_splits === null ? 'UNAVAILABLE' : row.adjusted_for_splits}.`),
        node('p', `Close dates: ${row.close_dates.join(', ') || 'UNAVAILABLE'}. Previous ten available bar dates: ${row.baseline_dates.join(', ') || 'INSUFFICIENT LOOKBACK'}.`),
        node('p', `Reference: ${row.reference?.state || 'UNLOADED'}. Retrieved: ${row.reference?.retrieved_at || 'UNAVAILABLE'}. ${row.reference_alignment.replaceAll('_', ' ')}. Market-cap source timestamp unavailable.`),
        node('p', 'FULL STRATEGY QUALIFICATION: NOT AVAILABLE. Volatility categories and ABCD setup remain unverified. Available bars do not prove calendar continuity, a synchronized snapshot or current momentum.'));
      coverage.append(details); tr.append(coverage); return tr;
    }));
    $('eod-empty').hidden = visible.length !== 0;
    $('eod-visible-count').textContent = `${visible.length} shown / ${rows.length} configured · Coverage counts cover the whole watchlist`;
    controls();
  }
  async function refresh() {
    const current = ++revision;
    const response = await fetch('/api/market-data/research', {cache: 'no-store', credentials: 'omit', mode: 'same-origin'});
    if (!response.ok) throw new Error('unavailable');
    const data = await response.json();
    if (current !== revision) return;
    rows = data.rows;
    $('eod-evaluated').textContent = `Freshness evaluated at ${data.evaluated_at}. No polling. Re-evaluate loaded data updates local freshness only; source retrieval dates are retained. Failed loads retain the last validated evidence.`;
    draw();
  }
  async function load(view) {
    if (!ready || busy) return;
    const ticker = $('eod-ticker').value; busy = true; controls();
    $('eod-message').textContent = `${ticker}: loading one ${view === 'history' ? 'daily-history' : 'dated-reference'} source. No other provider request will follow automatically.`;
    try {
      const response = await fetch(`/api/market-data/observation?ticker=${encodeURIComponent(ticker)}&view=${view}`,
        {cache: 'no-store', credentials: 'omit', mode: 'same-origin', headers: {'X-Velocity-Market-Preview': '1'}});
      if (!response.ok) throw new Error('unavailable');
      const result = await response.json();
      $('eod-message').textContent = `${ticker}: ${messages[result.reason] || 'Observation unavailable.'}${result.cached ? ' Cached source; no provider request.' : ''}${result.retry_after_seconds > 0 ? ` Retry after at least ${result.retry_after_seconds} seconds; manual retry only.` : ''}`;
      await refresh();
    } catch { $('eod-message').textContent = `${ticker}: load unavailable. No provider response details are displayed.`; }
    finally { busy = false; controls(); }
  }
  for (const id of Object.keys(defaults)) $(id).addEventListener('input', draw);
  for (const id of ['eod-require-cap', 'eod-matches-only', 'eod-search', 'eod-sort']) $(id).addEventListener('input', draw);
  $('eod-ticker').addEventListener('change', controls);
  $('eod-reset').addEventListener('click', () => {
    for (const [id, value] of Object.entries(defaults)) $(id).value = value;
    $('eod-require-cap').checked = true; $('eod-search').value = ''; $('eod-sort').value = 'change_pct'; $('eod-matches-only').checked = false; draw();
  });
  $('eod-loosen').addEventListener('click', () => {
    if (!readFilters()) return;
    for (const id of Object.keys(defaults)) {
      if ($(id).value === '') continue;
      const v = decimal($(id).value);
      const next = id === 'eod-max-price' ? {n: v.n * 2n, d: v.d} : id === 'eod-min-change' ? {n: v.n - v.d, d: v.d} : {n: v.n, d: v.d * 2n};
      const low = decimal(bounds[id][0]), high = decimal(bounds[id][1]);
      $(id).value = inputText(compare(next, low) < 0 ? low : compare(next, high) > 0 ? high : next);
    }
    draw(); $('eod-filter-feedback').textContent += ' Loosened values are shown in the controls; no source reload.';
  });
  $('eod-load-history').addEventListener('click', () => load('history'));
  $('eod-load-reference').addEventListener('click', () => load('reference'));
  const localFailure = () => { $('eod-message').textContent = 'Local research evidence unavailable.'; };
  $('eod-recheck').addEventListener('click', () => refresh().catch(localFailure));
  document.addEventListener('velocity-market-data-loaded', () => refresh().catch(localFailure));
  async function start() {
    try {
      const response = await fetch('/api/market-data', {cache: 'no-store', credentials: 'omit', mode: 'same-origin'});
      if (!response.ok) throw new Error('unavailable');
      const config = await response.json(); ready = config.enabled === true && config.configured === true;
      $('eod-ticker').replaceChildren(...config.watchlist.map((ticker) => { const o = node('option', ticker); o.value = ticker; return o; }));
      $('eod-message').textContent = ready ? 'Manual loads only. Load one ticker’s history, then request its dated reference separately. The demo scanner is unchanged.' : messages[config.reason];
      await refresh();
    } catch { localFailure(); }
    controls();
  }
  start();
})();
