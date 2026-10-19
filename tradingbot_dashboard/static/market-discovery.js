/* Personal grouped EOD discovery. Fetches only fixed loopback routes. */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const node = (tag, text) => { const n = document.createElement(tag); n.textContent = text; return n; };
  const reasons = Object.freeze({PREVIEW_DISABLED: 'Preview disabled. Default DEMO stays offline.',
    KEY_NOT_CONFIGURED: 'Provider key unavailable. Configure only in the server environment.',
    USER_LOAD_REQUIRED: 'Choose a past Eastern weekday and explicitly load one summary. Weekdays are not a holiday/session calendar.',
    RATE_LIMITED: 'Shared request budget/cooldown reached.', REQUEST_IN_PROGRESS: 'Another provider request is in progress.',
    INVALID_REQUEST: 'Invalid date. Use a past Eastern weekday within the last 730 calendar days.',
    INVALID_DISCOVERY_FILTERS: 'Invalid filter. Use finite bounded decimals and a valid ticker search.',
    INVALID_RESPONSE: 'Provider response failed strict validation; no partial results are published.',
    RESPONSE_LIMIT_EXCEEDED: 'Market response exceeds fixed byte/row limits. No partial coverage is published.',
    AUTHENTICATION_FAILED: 'Provider authentication failed.', ACCESS_DENIED: 'Provider access denied; check your own plan entitlement.',
    NOT_FOUND: 'No provider record available.', NETWORK_UNAVAILABLE: 'Provider network unavailable.',
    TIMEOUT: 'Provider request timed out.', REDIRECT_REJECTED: 'Provider redirect rejected.',
    PROVIDER_UNAVAILABLE: 'Provider unavailable.', NO_SUPPORTED_DAILY_OBSERVATIONS: 'No supported daily observations returned. This is not proof of a market session or absence of trading.',
    VALIDATED_GROUPED_DAILY_SOURCE: 'Validated dated EOD summary. Candidates remain preliminary.',
    INVALID_SHORTLIST: 'Selection rejected. Use at most five distinct validated discovered tickers.',
    DISCOVERY_REQUIRED: 'Load validated discovery evidence before selecting symbols.'});
  // Only fixed stage names may reach the page. Never display arbitrary server
  // strings, provider fields, exception messages or request identifiers.
  const diagnostics = new Set(['HTTP_CONTENT_TYPE_INVALID', 'HTTP_CONTENT_ENCODING_INVALID',
    'HTTP_CONTENT_LENGTH_INVALID', 'JSON_SYNTAX_INVALID', 'JSON_STRUCTURE_INVALID',
    'ROOT_FIELDS_UNEXPECTED', 'ROOT_STATUS_INVALID', 'ROOT_ADJUSTMENT_INVALID',
    'ROOT_METADATA_INVALID', 'RESULT_COUNTS_INVALID', 'ROW_FIELDS_UNEXPECTED',
    'ROW_IDENTITY_INVALID', 'ROW_IDENTITY_DUPLICATE', 'ROW_OHLCV_INVALID',
    'ROW_TIMESTAMP_INVALID', 'ROW_METADATA_INVALID', 'VALIDATION_FAILED']);
  const local = (path, action = false) => fetch(path, {cache: 'no-store', credentials: 'omit', mode: 'same-origin',
    headers: action ? {'X-Velocity-Market-Preview': '1'} : {}}).then(async (r) => { if (!r.ok) throw new Error('local'); return r.json(); });
  let ready = false, busy = false, selecting = false, shortlist = [], page = 0, pages = 1, revision = 0;
  function controls() {
    $('discovery-date').disabled = !ready || busy;
    $('discovery-load').disabled = !ready || busy;
    $('discovery-configured').disabled = selecting || !shortlist.length;
    for (const button of $('discovery-rows').querySelectorAll('button')) {
      button.disabled = busy || selecting || (!shortlist.includes(button.dataset.ticker) && shortlist.length >= 5);
    }
  }
  function selection(config) {
    shortlist = config.session_shortlist;
    $('discovery-shortlist-count').textContent = `${shortlist.length} / 5`;
    $('discovery-selection-source').textContent = shortlist.length
      ? `PRE2 now researches this session-only shortlist. Configured watchlist unchanged: ${config.watchlist.join(', ')}. Separate manual history/reference loads are required.`
      : `PRE2 uses your original configured watchlist: ${config.watchlist.join(', ')}. No provider requests from selection.`;
    $('discovery-shortlist').replaceChildren(...config.shortlist_sources.map(({ticker, discovery_date}) => {
      const button = node('button', `${ticker} · ${discovery_date} · Remove`);
      button.setAttribute('aria-label', `Remove ${ticker} from research shortlist`);
      button.disabled = selecting;
      button.addEventListener('click', () => choose(shortlist.filter((value) => value !== ticker)));
      return button;
    }));
    for (const button of $('discovery-rows').querySelectorAll('button')) {
      const selected = shortlist.includes(button.dataset.ticker);
      button.textContent = selected ? 'Selected · Remove' : 'Select for research';
      button.setAttribute('aria-pressed', String(selected));
    }
    controls();
  }
  async function choose(next) {
    if (selecting || busy || next.length > 5) return;
    selecting = true; controls();
    try {
      const config = await local(`/api/market-data/shortlist?tickers=${encodeURIComponent(next.join(','))}`, true);
      if (config.state !== 'RESEARCH_SELECTION_UPDATED') { $('discovery-message').textContent = reasons[config.reason] || 'Selection unavailable.'; return; }
      selection(config);
      document.dispatchEvent(new CustomEvent('velocity-research-selection', {detail: config}));
      $('discovery-message').textContent = 'Session research selection updated. No history or reference was requested; use the separate PRE2 manual buttons below.';
    } catch { $('discovery-message').textContent = 'Local selection unavailable. No provider details are displayed.'; }
    finally {
      selecting = false;
      for (const button of $('discovery-shortlist').querySelectorAll('button')) button.disabled = false;
      controls();
    }
  }
  function query() {
    let valid = true;
    for (const id of ['discovery-price', 'discovery-volume']) {
      const el = $(id), good = el.validity.valid && !el.validity.badInput && /^(?:0|[1-9]\d*)(?:\.\d{1,12})?$/.test(el.value) && el.value.length <= 32;
      el.setAttribute('aria-invalid', String(!good)); valid = valid && good;
    }
    const search = $('discovery-search').value.trim().toUpperCase();
    if (!/^[A-Z.]{0,7}$/.test(search)) valid = false;
    $('discovery-search').setAttribute('aria-invalid', String(!/^[A-Z.]{0,7}$/.test(search)));
    if (!valid) throw new Error('filters');
    return new URLSearchParams({price: $('discovery-price').value, volume: $('discovery-volume').value,
      search, sort: $('discovery-sort').value, page: String(page)});
  }
  function render(data) {
    const loaded = Number.isInteger(data.received_rows);
    for (const [id, value] of [['discovery-received', loaded ? `${data.provider_results_count} / ${data.received_rows}` : '—'],
      ['discovery-validated', data.validated_rows], ['discovery-excluded', loaded ? `${data.excluded_otc} / ${data.excluded_unsupported}` : '—'],
      ['discovery-candidates', data.eligible_candidates], ['discovery-awaiting', data.awaiting_deeper_research],
      ['discovery-displayed', data.displayed_count || 0]]) $(id).textContent = value === undefined ? '—' : String(value);
    $('discovery-source').textContent = loaded
      ? `Requested: ${data.requested_date} ET · Massive · ${data.state} · Retrieved: ${data.retrieved_at} · ${data.timestamp_semantics}. All declared response rows received ≠ complete U.S. market. Malformed rows invalidate the whole response; excluded format/OTC rows are counted.`
      : 'No qualified grouped response loaded. Market dates and coverage remain unavailable.';
    $('discovery-rows').replaceChildren(...(data.rows || []).slice(0, 50).map((row) => {
      const tr = document.createElement('tr'); tr.dataset.ticker = row.ticker;
      tr.append(...[row.ticker, row.close, row.volume, row.daily_range_pct.display, row.source_at].map((value) => node('td', value)));
      const td = document.createElement('td'), button = node('button', shortlist.includes(row.ticker) ? 'Selected · Remove' : 'Select for research');
      button.dataset.ticker = row.ticker; button.setAttribute('aria-pressed', String(shortlist.includes(row.ticker)));
      button.setAttribute('aria-label', `Select or remove ${row.ticker} for session research`);
      button.addEventListener('click', () => choose(shortlist.includes(row.ticker) ? shortlist.filter((value) => value !== row.ticker) : [...shortlist, row.ticker]));
      td.append(button); tr.append(td); return tr;
    }));
    page = data.page || 0; pages = data.pages || 1;
    $('discovery-page').textContent = loaded ? `Page ${page + 1} / ${pages} · ${data.displayed_count} displayed / ${data.matched_candidates} search matches · 50 rows maximum per page` : '0 displayed · Pages contain at most 50 rows';
    $('discovery-prev').disabled = page === 0; $('discovery-next').disabled = page + 1 >= pages;
    $('discovery-empty').hidden = !!data.rows?.length;
    $('discovery-empty').textContent = loaded ? 'No candidates match this view. This is not absence-of-trading or complete-market proof. Review dates, coverage and filters.' : 'No candidates loaded. No full-market or strategy qualification is established.';
    controls();
  }
  async function refresh() {
    const current = ++revision;
    let params;
    try { params = query(); }
    catch {
      render({rows: []});
      $('discovery-message').textContent = reasons.INVALID_DISCOVERY_FILTERS;
      $('discovery-empty').textContent = 'Correct invalid filters before viewing candidates.';
      return;
    }
    try {
      const data = await local(`/api/market-data/discovery?${params}`);
      if (current !== revision) return;
      render(data);
      if (!data.received_rows && data.reason) $('discovery-message').textContent = reasons[data.reason] || 'Discovery unavailable.';
      else if ($('discovery-message').textContent === reasons.INVALID_DISCOVERY_FILTERS) $('discovery-message').textContent = reasons.VALIDATED_GROUPED_DAILY_SOURCE;
      if (!$('discovery-date').value && data.suggested_date) {
        $('discovery-date').value = data.suggested_date;
        $('discovery-date').min = data.earliest_date; $('discovery-date').max = data.latest_date;
      }
    } catch { if (current === revision) $('discovery-message').textContent = 'Local discovery evidence unavailable.'; }
  }
  $('discovery-load').addEventListener('click', async () => {
    if (!ready || busy) return;
    const requested = $('discovery-date').value;
    if (!$('discovery-date').validity.valid || !requested) { $('discovery-message').textContent = reasons.INVALID_REQUEST; return; }
    busy = true; controls(); $('discovery-message').textContent = `Loading one market summary for ${requested}. No chained ticker requests.`;
    try {
      const data = await local(`/api/market-data/discovery/load?date=${encodeURIComponent(requested)}`, true);
      await refresh();
      const diagnostic = data.reason === 'INVALID_RESPONSE' && diagnostics.has(data.diagnostic_code)
        ? ` Diagnostic: ${data.diagnostic_code}.` : '';
      $('discovery-message').textContent = `${requested}: ${reasons[data.reason] || 'Discovery unavailable.'}${diagnostic}${data.cached ? ' Five-minute cached response; no provider call.' : ''}${data.retry_after_seconds ? ` Retry after at least ${data.retry_after_seconds} seconds; manual retry only.` : ''}`;
    } catch { $('discovery-message').textContent = 'Local load unavailable. No raw provider details are displayed.'; }
    finally { busy = false; controls(); }
  });
  for (const id of ['discovery-price', 'discovery-volume', 'discovery-search', 'discovery-sort']) $(id).addEventListener('input', () => { page = 0; refresh(); });
  $('discovery-prev').addEventListener('click', () => { page = Math.max(0, page - 1); refresh(); });
  $('discovery-next').addEventListener('click', () => { page = Math.min(pages - 1, page + 1); refresh(); });
  $('discovery-reset').addEventListener('click', () => {
    $('discovery-price').value = '5'; $('discovery-volume').value = '0'; $('discovery-search').value = ''; $('discovery-sort').value = 'volume'; page = 0; refresh();
  });
  $('discovery-configured').addEventListener('click', () => choose([]));
  async function start() {
    try {
      const config = await local('/api/market-data');
      ready = config.enabled === true && config.configured === true;
      selection(config); await refresh();
    } catch { $('discovery-message').textContent = 'Local discovery configuration unavailable.'; }
    controls();
  }
  start();
})();
