/* Explicit local PRE4C evaluation. Exact research decisions stay on the server. */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const node = (tag, text) => { const el = document.createElement(tag); el.textContent = text; return el; };
  const local = (path, action = false) => fetch(path, {cache: 'no-store', credentials: 'omit', mode: 'same-origin',
    headers: action ? {'X-Velocity-Market-Preview': '1'} : {}}).then(async (response) => {
    if (!response.ok) throw new Error('local'); return response.json();
  });
  const reasons = Object.freeze({USER_EVALUATION_REQUIRED: 'Explicit evaluation required. Load dates in discovery first.',
    REQUEST_IN_PROGRESS: 'Another collection or evaluation is in progress. Retry manually.',
    COLLECTION_TARGET_MISSING: 'The selected target is not retained. Reload dates manually after restart.',
    COLLECTION_CLOSED: 'This local session has closed.', INVALID_SCREENING_REQUEST: 'Invalid research settings. Use finite bounded decimal inputs.',
    SCREENING_SOURCE_INCOMPATIBLE: 'Retained sources failed PRE4A compatibility validation. No partial result is published.',
    SCREENING_MEMORY_LIMIT: 'Screening exceeded its bounded memory allocation. No partial result is published.',
    SCREENING_SUPERSEDED: 'Another evaluation replaced this result. Evaluate your target again.',
    SCREENING_RESPONSE_LIMIT: 'The bounded response could not be published.', SCREENING_UNAVAILABLE: 'Local screening unavailable.'});
  const outcomes = new Set(['PRELIMINARY_MATCH', 'PRELIMINARY_NOT_MET', 'INSUFFICIENT_HISTORY']);
  let evaluation = 0, page = 0, pages = 1, revision = 0, datesRevision = 0, busy = false, signature = null, collectionRevision = -1;
  const criteria = ['multi-price', 'multi-change', 'multi-average', 'multi-relative'];
  function controls() {
    $('multi-evaluate').disabled = busy || !$('multi-target').value;
    for (const id of ['multi-search', 'multi-sort', 'multi-outcome']) $(id).disabled = busy;
    $('multi-prev').disabled = busy || !evaluation || page === 0;
    $('multi-next').disabled = busy || !evaluation || page + 1 >= pages;
  }
  function empty(message) {
    evaluation = 0; page = 0; pages = 1;
    $('multi-rows').replaceChildren(); $('multi-sources').replaceChildren();
    for (const id of ['multi-total', 'multi-matches', 'multi-not-met', 'multi-insufficient', 'multi-visible']) $(id).textContent = '—';
    $('multi-empty').hidden = false; $('multi-empty').textContent = message;
    $('multi-applied').textContent = 'No result shown for the current draft. Explicit evaluation is required.';
    $('multi-page').textContent = '50 rows maximum per response'; controls();
  }
  function pending() {
    ++revision; empty('Target or research thresholds changed. Click Evaluate selected date; no automatic evaluation.');
    $('multi-message').textContent = 'Pending research settings. Original strategy, DEMO and PRE2 controls are unchanged.';
  }
  function collection(data) {
    if (!Number.isSafeInteger(data.collection_revision) || data.collection_revision < collectionRevision) return;
    collectionRevision = data.collection_revision;
    const entries = Array.isArray(data.dates) ? data.dates.slice(0, 11) : [];
    const next = entries.map((entry) => `${entry.requested_date}|${entry.retrieved_at}`).join(',');
    const selected = $('multi-target').value;
    const ordered = entries.map((entry) => entry.requested_date).sort();
    $('multi-target').replaceChildren(...(ordered.length ? ordered.map((day) => {
      const option = node('option', day); option.value = day; return option;
    }) : [node('option', 'No retained dates')]));
    if (!ordered.length) $('multi-target').firstChild.value = '';
    else $('multi-target').value = ordered.includes(selected) ? selected : ordered[ordered.length - 1];
    $('multi-target').disabled = !ordered.length;
    if (next !== signature) { signature = next; ++revision; empty('Retained sources changed. Explicitly evaluate the selected target.'); }
    controls();
  }
  async function refreshDates() {
    const current = ++datesRevision;
    try { const data = await local('/api/market-data/collection'); if (current === datesRevision) collection(data); }
    catch { $('multi-message').textContent = 'Retained date metadata unavailable. No provider request was made.'; }
  }
  function parameters() {
    let valid = true;
    for (const id of criteria) {
      const el = $(id), optional = id === 'multi-relative' && el.value === '' && !el.validity.badInput;
      const good = optional || (el.validity.valid && !el.validity.badInput && el.value.length <= 32
        && /^-?(?:0|[1-9]\d*)(?:\.\d{1,12})?$/.test(el.value));
      el.setAttribute('aria-invalid', String(!good)); valid = valid && good;
    }
    if (!valid || !$('multi-target').value) throw new Error('settings');
    return new URLSearchParams({target: $('multi-target').value, price: $('multi-price').value,
      change: $('multi-change').value, average: $('multi-average').value, relative: $('multi-relative').value});
  }
  function render(data) {
    if (data.state !== 'HISTORICAL_SCREENING_RESULT') {
      empty(reasons[data.reason] || 'Local screening unavailable.'); $('multi-message').textContent = reasons[data.reason] || 'Local screening unavailable.'; return;
    }
    if (data.target_date !== $('multi-target').value || data.filters.price !== $('multi-price').value
        || data.filters.change !== $('multi-change').value || data.filters.average !== $('multi-average').value
        || (data.filters.relative === null ? '' : data.filters.relative) !== $('multi-relative').value
        || data.search !== $('multi-search').value.trim().toUpperCase()
        || data.sort !== $('multi-sort').value || data.outcome_filter !== $('multi-outcome').value) {
      empty('Result belongs to different applied settings. Explicitly evaluate this draft.'); return;
    }
    evaluation = data.evaluation_id; page = data.page; pages = data.pages;
    for (const [id, value] of [['multi-total', data.target_tickers], ['multi-matches', data.counts.PRELIMINARY_MATCH],
      ['multi-not-met', data.counts.PRELIMINARY_NOT_MET], ['multi-insufficient', data.counts.INSUFFICIENT_HISTORY],
      ['multi-visible', data.matched_view_rows]]) $(id).textContent = String(value);
    const ratio = (value) => value === null ? 'UNAVAILABLE' : value.display;
    $('multi-rows').replaceChildren(...data.rows.slice(0, 50).map((row) => {
      const tr = document.createElement('tr'); tr.dataset.ticker = row.ticker;
      const price = node('td', row.source_close); price.append(node('small', `${row.target_date} ET`));
      tr.append(node('td', row.ticker), price, ...[row.change, row.average, row.relative, row.volume].map((value) => {
        const cell = node('td', ratio(value));
        if (value !== null) cell.title = `Exact source-derived ratio: ${value.numerator} / ${value.denominator}. Display is rounded.`;
        return cell;
      }));
      const dates = node('td', ''), details = document.createElement('details');
      details.append(node('summary', `${row.prior_observation_dates.length} prior observations`));
      details.append(node('p', `Previous close observed: ${row.previous_observation_date || 'UNAVAILABLE'}. Target source: ${row.source_at}.`));
      const list = document.createElement('ul'); list.append(...row.prior_observation_dates.slice(0, 10).map((day) => node('li', day)));
      details.append(list, node('p', `Ten-volume baseline: ${row.volume_baseline_dates.length === 10 ? row.volume_baseline_dates.join(', ') : 'UNAVAILABLE — ten prior observations required'}`));
      dates.append(details); tr.append(dates);
      const qualification = node('td', ''), badge = node('span', outcomes.has(row.outcome) ? row.outcome : 'UNAVAILABLE');
      badge.className = 'multi-outcome'; badge.dataset.outcome = row.outcome;
      qualification.append(badge, node('small', `Failed: ${row.failed.join(', ') || 'none'}. Missing: ${row.missing.join(', ') || 'none'}.`),
        node('small', `Unavailable: ${row.unavailable.join(', ') || 'none'}.`),
        node('small', 'Market cap UNVERIFIED · Session continuity / adjustment vintage UNVERIFIED'));
      tr.append(qualification); return tr;
    }));
    $('multi-empty').hidden = data.rows.length > 0;
    $('multi-empty').textContent = 'No rows in this search/outcome view. Missing data never passes a required criterion; review the whole-target counts above.';
    $('multi-page').textContent = `Page ${page + 1} / ${pages} · ${data.rows.length} shown / ${data.matched_view_rows} in view · 50 rows maximum`;
    $('multi-applied').textContent = `Applied target ${data.target_date} ET · Price < $${data.filters.price} · Observed gain ≥ ${data.filters.change}% · Prior 10-observation avg > ${data.filters.average} · EOD relative volume ${data.filters.relative === null ? 'not required' : `≥ ${data.filters.relative}`} · Session memory only`;
    $('multi-sources').replaceChildren(...data.sources.slice(0, 11).map((source) => node('li', `${source.requested_date} ET · ${source.validated_rows} validated / ${source.received_rows} received · Retrieved ${source.retrieved_at}`)));
    $('multi-message').textContent = 'Historical preliminary research. Eleven source dates do not prove ten prior observations for every ticker. No provider requests from evaluation.';
    controls();
  }
  async function view() {
    if (!evaluation || busy) return;
    const search = $('multi-search').value.trim().toUpperCase();
    const valid = /^[A-Z.]{0,7}$/.test(search); $('multi-search').setAttribute('aria-invalid', String(!valid));
    if (!valid) { $('multi-message').textContent = 'Invalid ticker search. Use at most seven letters/dots.'; return; }
    const current = ++revision;
    const query = new URLSearchParams({evaluation: String(evaluation), search, sort: $('multi-sort').value,
      outcome: $('multi-outcome').value, page: String(page)});
    try { const data = await local(`/api/market-data/multiday/page?${query}`); if (current === revision) render(data); }
    catch { if (current === revision) $('multi-message').textContent = 'Local result view unavailable.'; }
  }
  $('multi-evaluate').addEventListener('click', async () => {
    if (busy) return;
    let query;
    try { query = parameters(); } catch { $('multi-message').textContent = reasons.INVALID_SCREENING_REQUEST; return; }
    $('multi-search').value = ''; $('multi-sort').value = 'change'; $('multi-outcome').value = 'ALL';
    const current = ++revision; empty('Evaluating retained sources locally. No provider request.');
    busy = true; controls();
    try {
      const data = await local(`/api/market-data/multiday/evaluate?${query}`, true);
      if (current === revision) render(data);
    } catch { if (current === revision) empty('Local evaluation unavailable. No provider request was made.'); }
    finally { busy = false; controls(); }
  });
  for (const id of criteria) $(id).addEventListener('input', pending);
  $('multi-target').addEventListener('change', pending);
  for (const id of ['multi-search', 'multi-sort', 'multi-outcome']) $(id).addEventListener('input', () => { page = 0; view(); });
  $('multi-prev').addEventListener('click', () => { page = Math.max(0, page - 1); view(); });
  $('multi-next').addEventListener('click', () => { page = Math.min(pages - 1, page + 1); view(); });
  $('multi-reset').addEventListener('click', () => {
    $('multi-price').value = '5'; $('multi-change').value = '3'; $('multi-average').value = '1000000'; $('multi-relative').value = '';
    $('multi-search').value = ''; $('multi-sort').value = 'change'; $('multi-outcome').value = 'ALL'; pending();
    for (const id of [...criteria, 'multi-search']) $(id).setAttribute('aria-invalid', 'false');
  });
  $('multi-refresh-dates').addEventListener('click', refreshDates);
  document.addEventListener('velocity-collection-status', (event) => { ++datesRevision; collection(event.detail); });
  refreshDates(); // Metadata only. Never evaluates or requests provider data.
})();
