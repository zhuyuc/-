const state = {
  user: null,
  holdings: [],
  trades: [],
  expandedFees: new Set(),
  auditPage: 1,
  auditMenu: '',
  auditQuery: '',
  auditMenuSignature: '',
  reviewScope: 'all',
  selectedSecurity: null,
  market: 'all',
  side: 'buy',
  feeRules: { a: [], hk: [] },
  feeDisplay: { a: [], hk: [] },
  range: 'month',
  board: null,
  boardRange: '6m',
  boardCache: {},
  boardCardRange: {},
  boardGeom: {},
  watch: null,
  watchMarket: 'all',
  watchStatus: 'all',
  watchRange: '6m',
  watchCache: {},
  watchCardRange: {},
  watchDetail: null,
  watchSecurity: null,
  watchMode: 'create',
  watchEditId: null,
  watchNoteMode: 'create',
  watchNoteId: null,
  watchNoteCurrent: null,
  watchNotes: [],
  tradesPage: { limit: 200, offset: 0, total: 0 },
  historyTrades: [],
  historyTotal: 0,
  historyPage: 0,
  historyPageSize: 12,
  historySide: '',
  notesPage: { limit: 20, offset: 0, total: 0 },
  chartSecurity: null,
  chartPrices: [],
  hover: null,
  reviews: [],
  reviewSecurity: null,
  reviewPeriod: '',
  reviewEditId: null,
  reviewMode: 'create',
  tplanSecurity: null,
  tplanDirection: 'buy_first',
  tplanLastBody: null,
  tplanEditId: null,
  tplans: [],
  tplanStatus: 'all',
  tplanSearch: '',
  tfeedbackId: null,
  planCandidates: [],
  planLinkPick: 0,
  cash: { CNY: 0, HKD: 0 },
  cashTab: 'CNY',
  cashInitialized: false,
  cashEntries: [],
  tradeFeeTotal: 0
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

function beijingParts(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return new Date(date.getTime() + 8 * 3600 * 1000);
}

function beijingToday() {
  const date = beijingParts(Date.now());
  const pad = n => String(n).padStart(2, '0');
  return `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}`;
}

const today = beijingToday();

function unwrap(payload) {
  if (payload && Object.prototype.hasOwnProperty.call(payload, 'data')) return payload.data;
  return payload;
}

async function request(path, options = {}) {
  const { authRedirect = true, ...rest } = options;
  const config = { credentials: 'same-origin', headers: { 'Content-Type': 'application/json', ...(rest.headers || {}) }, ...rest };
  if (config.body && typeof config.body !== 'string') config.body = JSON.stringify(config.body);
  const response = await fetch(path, config);
  if (response.status === 401 && authRedirect) {
    window.location.reload();
    return new Promise(() => {});
  }
  let payload = null;
  try { payload = await response.json(); } catch { payload = null; }
  if (!response.ok) throw new Error(payload?.detail || payload?.message || payload?.error || `请求失败（${response.status}）`);
  return unwrap(payload);
}

function toast(message, danger = false) {
  const node = $('#toast');
  node.textContent = message;
  node.style.background = danger ? 'var(--red)' : 'var(--toast)';
  node.classList.add('show');
  window.clearTimeout(toast.timer);
  toast.timer = window.setTimeout(() => node.classList.remove('show'), 2800);
}

const modalReturnFocus = [];

let modalZTop = 30;

function openModal(selector) {
  const el = $(selector);
  if (!el || !el.classList.contains('hidden')) return el;
  el.style.zIndex = String(++modalZTop);
  modalReturnFocus.push(document.activeElement);
  el.classList.remove('hidden');
  if (!el.contains(document.activeElement)) { const first = focusablesIn(el)[0]; if (first) first.focus(); }
  return el;
}

function closeModal(selector) {
  const el = $(selector);
  if (!el || el.classList.contains('hidden')) return false;
  el.classList.add('hidden');
  if (!$$('.modal').some(node => !node.classList.contains('hidden'))) modalZTop = 30;
  const trigger = modalReturnFocus.pop();
  if (trigger && trigger.isConnected) trigger.focus();
  return true;
}

function focusablesIn(root) {
  return $$('a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[contenteditable="true"],[tabindex]:not([tabindex="-1"])', root)
    .filter(el => el === document.activeElement || el.offsetWidth > 0 || el.offsetHeight > 0);
}

function bindModalFocusTrap() {
  document.addEventListener('keydown', event => {
    if (event.key !== 'Tab') return;
    const open = $$('.modal').filter(modal => !modal.classList.contains('hidden'));
    if (!open.length) return;
    const modal = open[open.length - 1];
    const items = focusablesIn(modal);
    if (!items.length) { event.preventDefault(); return; }
    const active = document.activeElement;
    if (!modal.contains(active)) { event.preventDefault(); items[0].focus(); return; }
    if (event.shiftKey && active === items[0]) { event.preventDefault(); items[items.length - 1].focus(); }
    else if (!event.shiftKey && active === items[items.length - 1]) { event.preventDefault(); items[0].focus(); }
  });
}

function setFieldError(el, message) {
  if (!el) return;
  const holder = el.closest('label') || el.closest('.field-wrap') || el.parentElement;
  const slot = holder ? holder.querySelector(':scope > .field-error') : null;
  if (message) {
    let node = slot;
    if (!node && holder) {
      node = document.createElement('span');
      node.className = 'field-error';
      node.setAttribute('role', 'alert');
      holder.appendChild(node);
    }
    if (node) node.textContent = message;
    el.classList.add('invalid');
    el.setAttribute('aria-invalid', 'true');
  } else {
    if (slot) slot.remove();
    el.classList.remove('invalid');
    el.removeAttribute('aria-invalid');
  }
}

function applyValidation(checks) {
  let firstBad = null;
  checks.forEach(([el, message]) => { setFieldError(el, message || ''); if (message && !firstBad) firstBad = el; });
  if (firstBad) firstBad.focus();
  return !firstBad;
}

function positiveError(value, label) {
  const text = String(value ?? '').trim();
  if (!text) return `请填写${label}`;
  const parsed = Number(text);
  if (!Number.isFinite(parsed) || parsed <= 0) return `${label}必须大于 0`;
  return '';
}

function formatMarketCap(value, currency) {
  if (value == null || value === '') return '--';
  const yi = Number(value);
  if (!Number.isFinite(yi) || yi <= 0) return '--';
  const prefix = currency === 'HKD' ? 'HK$' : '¥';
  return yi >= 10000 ? `${prefix}${(yi / 10000).toFixed(2)}万亿` : `${prefix}${yi.toFixed(2)}亿`;
}

function editorError(editor, label) {
  const empty = !(editor.textContent || '').trim();
  setFieldError(editor, empty ? `${label}不能为空` : '');
  if (empty) editor.focus();
  return !empty;
}

function setRefreshing(selector, on) {
  const el = $(selector);
  if (!el) return;
  el.classList.toggle('refreshing', on);
  if (on) el.setAttribute('aria-busy', 'true');
  else el.removeAttribute('aria-busy');
}

function roundHalfUp3(value) { const n = Math.abs(Number(value)); if (n < 1e-6) return 0; const mag = Number(`${n}e3`); return Number(`${Math.round(mag)}e-3`); }

function money(value, currency = 'CNY') {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '--';
  const n = Number(value); const abs = roundHalfUp3(n);
  return `${n < 0 && abs > 0 ? '-' : ''}${absMoney(abs, currency)}`;
}

function absMoney(abs, currency = 'CNY') {
  return `${currency === 'HKD' ? 'HK$' : '¥'}${abs.toLocaleString('zh-CN', { minimumFractionDigits: 3, maximumFractionDigits: 3 })}`;
}

function priceMoney(value, currency = 'CNY') {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '--';
  const digits = currency === 'HKD' ? 3 : 2;
  const n = Number(value);
  return `${n < 0 ? '-' : ''}${currency === 'HKD' ? 'HK$' : '¥'}${Math.abs(n).toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

function tplanPriceValue(value, currency = 'CNY') {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '';
  return Number(value).toFixed(currency === 'HKD' ? 3 : 2);
}

function number(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '--';
  return Number(value).toLocaleString('zh-CN', { maximumFractionDigits: digits });
}

function plainNumber(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value ?? 0);
  if (n === 0) return '0';
  return n.toFixed(10).replace(/0+$/, '').replace(/\.$/, '');
}

function pnlClass(value) { return Number(value) >= 0 ? 'positive' : 'negative'; }
function signedMoney(value, currency = 'CNY') { const n = Number(value); const abs = roundHalfUp3(n); return `${n < 0 && abs > 0 ? '-' : '+'}${absMoney(abs, currency)}`; }
function asArray(value, keys = []) {
  if (Array.isArray(value)) return value;
  for (const key of keys) if (Array.isArray(value?.[key])) return value[key];
  return [];
}

function showAuth() {
  $('#auth-view').classList.remove('hidden');
  $('#app-view').classList.add('hidden');
}

function showApp(user) {
  state.user = user || {};
  $('#auth-view').classList.add('hidden');
  $('#app-view').classList.remove('hidden');
  const name = state.user.username || state.user.name || '本地账号';
  $('#account-name').textContent = name;
  $('#account-avatar').textContent = name.slice(0, 1).toUpperCase();
  loadDashboard();
  syncPrices({ quiet: true }).then(() => loadDashboard(), () => loadDashboard());
}

function syncStamp() { return new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }); }

function markSyncState(text, degraded) {
  const el = $('#last-sync');
  if (!el) return;
  el.textContent = text;
  el.classList.toggle('sync-degraded', !!degraded);
}

async function syncPrices(options = {}) {
  const quiet = !!options.quiet;
  let result;
  try {
    result = await request('/api/prices/sync', { method: 'POST' });
  } catch (error) {
    markSyncState(`行情更新失败 ${syncStamp()}`, true);
    if (!quiet) toast(`行情更新失败：${error.message}`, true);
    throw error;
  }
  const stale = (result?.stale || []).length;
  const skipped = result?.skipped_markets || [];
  if (result?.degraded) {
    markSyncState(`${syncStamp()} 行情不完整，${stale} 只沿用旧价`, true);
    if (!quiet) toast(`行情获取不完整，${stale} 只股票仍显示最近一次保存的价格`, true);
  } else {
    markSyncState(`${result.updated > 0 ? '已更新' : '已检查'} ${syncStamp()}`, false);
  }
  if (skipped.length && !result?.degraded && !quiet) toast(`${skipped.map(m => m === 'hk' ? '港股' : 'A股').join('、')}当前时段暂无交易，沿用最近交易价`);
  return result;
}

async function loadDashboard() {
  setRefreshing('#holdings-container', true);
  setRefreshing('.summary-grid', true);
  try {
    const [portfolio, trades, cash] = await Promise.all([request('/api/portfolio'), request(tradesUrl(0)), request('/api/cash')]);
    state.holdings = asArray(portfolio, ['holdings', 'positions', 'items']);
    syncSellAvailability();
    state.trades = asArray(trades, ['trades', 'items']);
    state.tradesPage.total = Number(trades.total ?? state.trades.length);
    state.tradesPage.offset = state.trades.length;
    applyCash(cash);
    renderOverview(portfolio);
    state.costMethod = portfolio?.cost_method || state.costMethod || 'moving_average';
    if (portfolio?.cost_method) $('#cost-method').value = portfolio.cost_method;
  } catch (error) {
    toast(error.message, true);
    $('#holdings-container').innerHTML = `<div class="empty-state">暂时无法加载持仓数据。${escapeHtml(error.message)}</div>`;
  } finally {
    setRefreshing('#holdings-container', false);
    setRefreshing('.summary-grid', false);
  }
  try {
    const [a, hk] = await Promise.all(['A', 'HK'].map(market => request(`/api/fees?market=${market}`)));
    state.feeRules.a = asArray(a, ['rules', 'items']).map(rule => ({ ...rule, market: rule.market || 'SH' }));
    state.feeRules.hk = asArray(hk, ['rules', 'items']).map(rule => ({ ...rule, market: rule.market || 'HK' }));
    state.feeDisplay.a = mergeFeeSides(state.feeRules.a);
    state.feeDisplay.hk = mergeFeeSides(state.feeRules.hk);
    renderFeeRules();
    renderFeePreview();
  } catch (error) { renderFeeRules(); renderFeePreview(); }
  try {
    const reviewData = await request('/api/reviews');
    state.reviews = asArray(reviewData, ['reviews', 'items']);
    renderReviewScopeMenu();
  } catch { /* 侧边栏二级菜单允许静默失败 */ }
}

function applyCash(data) {
  state.cash = {};
  asArray(data, ['accounts']).forEach(item => { state.cash[item.currency] = Number(item.balance); });
  state.cashInitialized = !!data?.initialized;
  state.cashEntries = asArray(data, ['entries']);
  state.cashOpening = data?.opening || {};
  state.cashStartedAt = data?.started_at || null;
  renderCashCard();
  renderCashEntries();
  if ($('#trade-held-hint')) renderFeePreview();
  if (!$('#cash-modal').classList.contains('hidden')) syncCashModalUI();
}

async function loadCash() {
  try { applyCash(await request('/api/cash')); } catch { /* 资金卡保持旧值 */ }
}

function renderCashCard() {
  const el = $('#cash-balances');
  if (!el) return;
  el.innerHTML = [['CNY', '人民币账户'], ['HKD', '港币账户']].map(([currency, label]) => {
    if (!state.cashInitialized) return `<span>${label} --</span>`;
    const value = Number(state.cash?.[currency] || 0);
    return `<span class="${value < 0 ? 'cash-negative' : ''}">${label} ${money(value, currency)}</span>`;
  }).join('');
}

function renderCashEntries() {
  const el = $('#cash-history-list');
  if (!el) return;
  const entries = state.cashEntries || [];
  const currencyOf = entry => entry.currency === 'HKD' ? 'HKD' : 'CNY';
  const countOf = currency => entries.filter(entry => currencyOf(entry) === currency).length + (Number(state.cashOpening?.[currency] || 0) !== 0 ? 1 : 0);
  const tab = state.cashTab === 'HKD' ? 'HKD' : 'CNY';
  $$('#cash-tabs .cash-tab').forEach(button => button.classList.toggle('active', button.dataset.cashTab === tab));
  $('#cash-tab-cny').textContent = state.cashInitialized ? money(Number(state.cash?.CNY || 0), 'CNY') : '--';
  $('#cash-tab-hkd').textContent = state.cashInitialized ? money(Number(state.cash?.HKD || 0), 'HKD') : '--';
  $('#cash-tab-cny-count').textContent = `${countOf('CNY')} 条记录`;
  $('#cash-tab-hkd-count').textContent = `${countOf('HKD')} 条记录`;
  const head = '<div class="cash-row head"><small>时间</small><span>类型</span><span>备注</span><span>金额</span><span>操作</span></div>';
  const rows = [];
  const opening = Number(state.cashOpening?.[tab] || 0);
  if (opening !== 0) rows.push(`<div class="cash-row opening"><small>${state.cashStartedAt ? formatDate(state.cashStartedAt) : '-'}</small><span class="cash-kind">期初结转</span><span class="cash-note">起算日前已有持仓</span><strong class="cash-amt">${money(opening, tab)}</strong><span class="cash-action"></span></div>`);
  entries.filter(entry => currencyOf(entry) === tab).forEach(entry => {
    const deposit = entry.kind === 'deposit';
    rows.push(`<div class="cash-row"><small>${escapeHtml(formatDateTime(entry.created_at))}</small><span class="cash-kind${deposit ? ' dep' : ''}">${deposit ? '入金' : '出金'}</span><span class="cash-note">${entry.note ? escapeHtml(entry.note) : '—'}</span><strong class="cash-amt">${deposit ? '+' : '-'}${absMoney(Number(entry.amount), tab)}</strong><span class="cash-action"><button class="text-action danger cash-delete" type="button" data-id="${escapeAttr(entry.id)}">删除</button></span></div>`);
  });
  el.innerHTML = rows.length ? head + rows.join('') : `<div class="cash-history-empty">还没有${tab === 'HKD' ? '港币' : '人民币'}资金记录，点「记一笔」开始。</div>`;
  $$('.cash-delete').forEach(button => button.addEventListener('click', () => deleteCashEntry(button.dataset.id)));
}

function cashModalValues() {
  return { currency: $('#cash-currency').value === 'HKD' ? 'HKD' : 'CNY', kind: $('#cash-kind').value === 'withdraw' ? 'withdraw' : 'deposit' };
}

function syncCashModalUI() {
  if (!$('#cash-modal')) return;
  const { currency, kind } = cashModalValues();
  const amount = Number($('#cash-amount')?.value || 0);
  const available = Number(state.cash?.[currency] || 0);
  $$('#cash-currency-tabs button').forEach(button => button.classList.toggle('active', button.dataset.cashCurrency === currency));
  $$('#cash-kind-tabs button').forEach(button => button.classList.toggle('active', button.dataset.cashKind === kind));
  $('#cash-currency-label').textContent = currencyLabel(currency);
  const previewLabel = $('#cash-preview-label');
  const previewValue = $('#cash-preview-value');
  const preview = $('#cash-preview');
  previewLabel.textContent = kind === 'deposit' ? '入金后可用' : '出金后可用';
  const bad = kind === 'withdraw' && amount > available;
  const showAfter = state.cashInitialized || kind === 'deposit';
  previewValue.textContent = amount > 0 && showAfter ? `${money(kind === 'deposit' ? available + amount : available - amount, currency)}${bad ? ' · 可用金额不足' : ''}` : '--';
  preview.classList.toggle('bad', amount > 0 && bad);
  $('#cash-save').disabled = !(amount > 0 && (kind === 'deposit' || (state.cashInitialized && amount <= available)));
}

function openCashModal(currency) {
  const target = currency === 'HKD' ? 'HKD' : 'CNY';
  $('#cash-currency').value = target;
  $('#cash-kind').value = 'deposit';
  $('#cash-amount').value = '';
  $('#cash-note').value = '';
  syncCashModalUI();
  openModal('#cash-modal');
  loadCash().then(syncCashModalUI);
}

function closeCashModal() { closeModal('#cash-modal'); }

function openCashHistory() {
  renderCashEntries();
  openModal('#cash-history-modal');
  loadCash();
}

function closeCashHistory() { closeModal('#cash-history-modal'); }

async function saveCashEntry() {
  const { currency, kind } = cashModalValues();
  const amount = Number($('#cash-amount').value);
  if (!applyValidation([[$('#cash-amount'), positiveError($('#cash-amount').value, '金额')]])) return;
  const available = Number(state.cash?.[currency] || 0);
  if (kind === 'withdraw' && amount > available) {
    applyValidation([[$('#cash-amount'), `出金金额不能超过可用 ${money(available, currency)}`]]);
    return;
  }
  try {
    await request('/api/cash/entries', { method: 'POST', body: { currency, kind, amount, note: $('#cash-note').value.trim() } });
    toast(kind === 'deposit' ? '入金已记录' : '出金已记录');
    $('#cash-amount').value = '';
    $('#cash-note').value = '';
    await loadCash();
    syncCashModalUI();
    if (state.selectedSecurity) renderFeePreview();
  } catch (error) { toast(error.message, true); }
}

async function deleteCashEntry(id) {
  if (!await confirmDialog('删除后不可恢复，确认删除这条资金记录？', '删除资金记录')) return;
  try {
    await request(`/api/cash/entries/${encodeURIComponent(id)}`, { method: 'DELETE' });
    toast('资金记录已删除');
    await loadCash();
    if (state.selectedSecurity) renderFeePreview();
  } catch (error) { toast(error.message, true); }
}

function renderOverview(portfolio) {
  const summary = portfolio?.summary || portfolio || {};
  const holdings = state.holdings;
  const total = Number(summary.total_value ?? holdings.reduce((sum, item) => sum + Number(item.market_value || item.value || 0), 0));
  const realizedMap = portfolio?.realized || {};
  const realized = Object.values(realizedMap).reduce((sum, value) => sum + Number(value || 0), 0);
  const unrealized = Number(summary.unrealized_pnl ?? holdings.reduce((sum, item) => sum + Number(item.unrealized_pnl || item.floating_pnl || 0), 0));
  const byCurrency = {};
  holdings.forEach(item => { const currency = item.currency || (item.market === 'hk' ? 'HKD' : 'CNY'); byCurrency[currency] = (byCurrency[currency] || 0) + Number(item.market_value ?? item.value ?? 0); });
  const currencyEntries = Object.entries(byCurrency);
  const totalEl = $('#total-value');
  if (currencyEntries.length > 1) { totalEl.classList.add('multi'); totalEl.innerHTML = currencyEntries.map(([currency, value]) => `<span>${escapeHtml(currencyLabel(currency))} ${money(value, currency)}</span>`).join(''); }
  else { totalEl.classList.remove('multi'); totalEl.textContent = money(total, currencyEntries[0]?.[0] || 'CNY'); }
  const pnlByCurrency = {};
  holdings.forEach(item => { const currency = item.currency || (item.market === 'hk' ? 'HKD' : 'CNY'); const entry = pnlByCurrency[currency] ||= { realized: 0, unrealized: 0 }; entry.unrealized += Number(item.unrealized_pnl ?? item.floating_pnl ?? 0); });
  Object.entries(realizedMap).forEach(([currency, value]) => { (pnlByCurrency[currency] ||= { realized: 0, unrealized: 0 }).realized = Number(value || 0); });
  const pnlEntries = Object.entries(pnlByCurrency);
  const setPnl = (el, key, totalValue) => { if (pnlEntries.length > 1) { el.className = 'multi'; el.innerHTML = pnlEntries.map(([currency, entry]) => `<span class="${pnlClass(entry[key])}">${signedMoney(entry[key], currency)}</span>`).join(''); } else { el.className = pnlClass(totalValue); el.textContent = signedMoney(totalValue, pnlEntries[0]?.[0] || 'CNY'); } };
  setPnl($('#realized-pnl'), 'realized', realized);
  setPnl($('#unrealized-pnl'), 'unrealized', unrealized);
  $('#realized-note').textContent = realized !== 0 || state.trades.some(trade => trade.side === 'sell') ? '已扣除交易费用' : '暂无卖出记录，减仓成交后结算';
  $('#holding-count').textContent = number(holdings.length, 0);
  const groups = { CNY: [], HKD: [] };
  holdings.forEach(item => { const currency = item.currency || (item.market === 'hk' ? 'HKD' : 'CNY'); (groups[currency] ||= []).push(item); });
  const html = Object.entries(groups).filter(([, items]) => items.length).map(([currency, items]) => {
    const value = items.reduce((sum, item) => sum + Number(item.market_value ?? item.value ?? 0), 0);
    return `<div class="currency-group"><div class="currency-head"><strong>${currency === 'CNY' ? '人民币持仓' : '港币持仓'}</strong><span>持仓金额 ${money(value, currency)}</span></div><div class="table-scroll"><table class="holding-table"><thead><tr><th>股票</th><th>数量</th><th>持仓成本</th><th>最后价格</th><th>持仓金额</th><th>浮动盈亏</th><th>已实现盈亏</th><th>累计收益率</th><th>操作</th></tr></thead><tbody>${items.map(item => holdingRow(item, currency)).join('')}</tbody></table></div></div>`;
  }).join('');
  $('#holdings-container').innerHTML = html || '<div class="empty-state">还没有持仓，记录第一笔交易后会显示在这里。</div>';
  syncHoldingSelection();
  $$('.holding-link').forEach(button => button.addEventListener('click', () => selectChartSecurity(button.dataset.id, button.dataset.name, button.dataset.code, button.dataset.currency)));
  $$('.row-tplan').forEach(button => button.addEventListener('click', () => openTplanModal({ direction: 'buy_first', holding: { security_id: button.dataset.tid, name: button.dataset.name, code: button.dataset.code, exchange: button.dataset.exchange, currency: button.dataset.currency, quantity: button.dataset.qty, average_cost: button.dataset.cost } })));
  $$('.row-trade').forEach(button => button.addEventListener('click', () => gotoTradeWithSecurity(button.dataset)));
  const held = holdings.map(item => ({ id: item.security_id ?? item.id, name: item.name || item.security_name || item.symbol || '-', code: item.code || item.symbol_code || item.ticker || '-', currency: item.currency || (item.market === 'hk' ? 'HKD' : 'CNY'), value: Number(item.market_value ?? item.value ?? 0) }));
  if (!held.length) { state.chartSecurity = null; state.chartPrices = []; $('#chart-title').textContent = ''; $('#chart-cost').textContent = ''; $('#chart-latest').textContent = ''; drawChart(); return; }
  const active = state.chartSecurity && held.find(item => item.id === state.chartSecurity.id);
  if (!active) { const pick = held.reduce((a, b) => (b.value > a.value ? b : a)); selectChartSecurity(pick.id, pick.name, pick.code, pick.currency); }
  if (state.board && $('#page-board').classList.contains('active-page')) loadBoard();
}

function holdingRow(item, currency) {
  const id = item.security_id ?? item.id;
  const name = item.name || item.security_name || item.symbol || '-';
  const code = item.code || item.symbol_code || item.ticker || '-';
  const qty = item.quantity ?? item.remaining_quantity ?? 0;
  const cost = item.cost ?? item.cost_price ?? item.average_cost ?? 0;
  const price = item.last_price ?? item.price ?? null;
  const value = item.market_value ?? item.value ?? Number(qty) * Number(price || 0);
  const pnl = item.unrealized_pnl ?? item.floating_pnl ?? (Number(value) - Number(qty) * Number(cost));
  const costTotal = Number(qty) * Number(cost);
  const invested = Number(item.invested_cost ?? costTotal);
  const totalPnl = Number(item.total_pnl ?? 0);
  const realized = Number(item.realized_pnl ?? 0);
  const pct = invested > 0 ? (totalPnl / invested) * 100 : 0;
  const pctText = invested > 0 ? `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%` : '--';
  const selected = state.chartSecurity && String(state.chartSecurity.id) === String(id);
  return `<tr data-sid="${escapeAttr(id)}"${selected ? ' class="sel"' : ''}><td><button class="holding-link" data-id="${escapeAttr(id)}" data-name="${escapeAttr(name)}" data-code="${escapeAttr(code)}" data-currency="${currency}"><span class="stock-name">${escapeHtml(name)}</span><span class="stock-code">${escapeHtml(code)} · ${escapeHtml(item.exchange ? exchangeLabel(item.exchange) : (currency === 'HKD' ? '港交所' : 'A 股'))}</span></button></td><td>${number(qty, 4)}</td><td>${money(cost, currency)}</td><td>${priceMoney(price, currency)}</td><td>${money(value, currency)}</td><td class="${pnlClass(pnl)}">${signedMoney(pnl, currency)}</td><td class="${pnlClass(realized)}">${signedMoney(realized, currency)}</td><td class="${invested > 0 ? pnlClass(totalPnl) : 'muted'}">${pctText}</td><td><div class="holding-ops"><button class="text-action row-trade" data-trade-id="${escapeAttr(id)}" data-name="${escapeAttr(name)}" data-code="${escapeAttr(code)}" data-currency="${currency}" data-exchange="${escapeAttr(item.exchange || '')}" data-market="${escapeAttr(item.market || '')}">买卖</button><button class="text-action row-tplan" data-tid="${escapeAttr(id)}" data-qty="${escapeAttr(qty)}" data-cost="${escapeAttr(cost)}" data-name="${escapeAttr(name)}" data-code="${escapeAttr(code)}" data-currency="${currency}" data-exchange="${escapeAttr(item.exchange || '')}">做T</button></div></td></tr>`;
}

function syncHoldingSelection() {
  const id = state.chartSecurity?.id;
  $$('#holdings-container tr[data-sid]').forEach(row => row.classList.toggle('sel', String(row.dataset.sid) === String(id)));
}

function searchSecurities(query) {
  return searchSecuritiesInto({ listSel: '#security-results', market: () => state.market, heldOnly: () => state.side === 'sell', onPick: selectSecurity }, query);
}

function heldQuantity(securityId) {
  const holding = (state.holdings || []).find(item => (item.security_id ?? item.id) === securityId);
  return holding ? Number(holding.quantity ?? 0) : 0;
}

function syncSellQtyLimit() {
  const input = $('#trade-quantity');
  const held = state.side === 'sell' && state.selectedSecurity ? heldQuantity(state.selectedSecurity.security_id ?? state.selectedSecurity.id) : 0;
  if (state.side === 'sell' && held > 0) input.setAttribute('max', String(held)); else input.removeAttribute('max');
}

function setTradeSide(side) {
  state.side = side;
  $('#trade-side').value = side;
  $$('#trade-form .direction').forEach(item => {
    item.classList.remove('active', 'buy', 'sell');
    if (item.dataset.side === side) item.classList.add('active', side);
  });
  syncSellQtyLimit();
  renderFeePreview();
}

function syncSellAvailability() {
  const sellButton = $('#trade-form .direction[data-side="sell"]');
  if (!sellButton) return;
  const security = state.selectedSecurity;
  const held = security ? heldQuantity(security.security_id ?? security.id) : 0;
  const disabled = Boolean(security) && held <= 0;
  sellButton.disabled = disabled;
  sellButton.setAttribute('aria-disabled', disabled ? 'true' : 'false');
  sellButton.title = disabled ? '该股票当前没有持仓，不能卖出' : '';
  if (disabled && state.side === 'sell') setTradeSide('buy');
}

function selectSecurity(security) {
  const securityId = security.id ?? security.security_id ?? '';
  if (state.side === 'sell' && heldQuantity(securityId) <= 0) { toast('该股票当前没有持仓，不能卖出', true); return; }
  state.selectedSecurity = security;
  $('#security-id').value = security.id ?? security.security_id ?? '';
  $('#trade-currency').textContent = currencyLabel(renderSecurityChip('#selected-security', security, clearSecurity));
  $('#security-search').classList.add('hidden');
  $('#security-search').value = '';
  const held = heldQuantity(securityId);
  const chipMeta = $('#selected-security small');
  if (chipMeta && held > 0) chipMeta.textContent = `${chipMeta.textContent} · 可卖 ${number(held, 4)} 股`;
  hideSecurityList('#security-results');
  syncSellQtyLimit();
  syncSellAvailability();
  renderFeePreview();
}
function clearSecurity() {
  state.selectedSecurity = null;
  $('#security-id').value = '';
  $('#selected-security').classList.add('hidden');
  $('#selected-security').innerHTML = '';
  $('#trade-currency').textContent = '人民币';
  $('#security-search').classList.remove('hidden');
  $('#security-search').value = '';
  hideSecurityList('#security-results');
  $('#trade-quantity').removeAttribute('max');
  $('#trade-held-hint').textContent = '股';
  syncSellAvailability();
  renderFeePreview();
}
function gotoTradeWithSecurity(dataset) {
  navigate('trade');
  selectSecurity({ security_id: dataset.tradeId, id: dataset.tradeId, name: dataset.name, code: dataset.code, exchange: dataset.exchange, currency: dataset.currency, market: dataset.market });
}

const comboControllers = new Map();

function securityCurrency(security) { return security.currency || ((security.market || '').toUpperCase() === 'HK' ? 'HKD' : 'CNY'); }

function securityChipMeta(security) {
  const raw = security.last_price;
  const price = raw == null || raw === '' ? null : Number(raw);
  const currency = securityCurrency(security);
  return `<div><strong>${escapeHtml(security.name || security.security_name || security.symbol || '')}</strong><small>${escapeHtml(security.code || security.symbol_code || security.ticker || '')} · ${escapeHtml(exchangeLabel(security.exchange))}${price ? ` · 最新 ${priceMoney(price, currency)}` : ''}</small></div>`;
}

function renderSecurityChip(chipSel, security, onClear) {
  const chip = $(chipSel);
  chip.classList.remove('hidden');
  chip.innerHTML = `${securityChipMeta(security)}<button type="button" class="text-action" data-clear-security>更换</button>`;
  $('[data-clear-security]', chip).addEventListener('click', () => { onClear(); openComboByChip(chipSel); });
  return securityCurrency(security);
}

function hideSecurityList(listSel) { const list = $(listSel); list.classList.add('hidden'); list.innerHTML = ''; }

async function searchSecuritiesInto(cfg, query) {
  const keyword = (query || '').trim();
  const list = $(cfg.listSel);
  if (!keyword) { hideSecurityList(cfg.listSel); return; }
  comboControllers.get(cfg.listSel)?.abort();
  const controller = new AbortController();
  comboControllers.set(cfg.listSel, controller);
  const { signal } = controller;
  list.classList.remove('hidden');
  list.innerHTML = '<div class="muted center">搜索中…</div>';
  try {
    const market = cfg.market();
    let data = await request(`/api/securities?q=${encodeURIComponent(keyword)}&market=${encodeURIComponent(market)}`, { signal });
    let rows = asArray(data, ['securities', 'items', 'results']);
    if (!rows.length) data = await request('/api/securities/refresh', { method: 'POST', body: { q: keyword, market }, signal });
    if (signal.aborted) return;
    rows = asArray(data, ['securities', 'items', 'results']);
    const visible = cfg.heldOnly() ? rows.filter(security => heldQuantity(security.security_id ?? security.id) > 0) : rows;
    list.innerHTML = visible.length ? visible.map(securityRowHtml).join('') : `<div class="muted center">${rows.length ? '当前持仓中没有匹配的股票' : '没有匹配的股票'}</div>`;
    bindSecurityResults(list, cfg.onPick);
  } catch (error) {
    if (signal.aborted || error.name === 'AbortError') return;
    list.innerHTML = `<div class="muted center">${escapeHtml(error.message)}</div>`;
  }
}

function securityRowHtml(security) {
  const currency = security.currency || (security.market === 'hk' ? 'HKD' : 'CNY');
  const price = security.last_price == null || security.last_price === '' ? null : Number(security.last_price);
  return `<button class="security-result" data-security='${escapeAttr(JSON.stringify(security))}'><span><strong>${escapeHtml(security.name || security.security_name || security.symbol)}</strong><small>${escapeHtml(security.code || security.symbol_code || security.ticker || '')}</small></span><em>${escapeHtml(exchangeLabel(security.exchange))} · ${escapeHtml(currencyLabel(currency))}${price ? ` · 最新 ${money(price, currency)}` : ''}</em></button>`;
}

function bindSecurityResults(list, onPick) { $$('.security-result[data-security]', list).forEach(button => button.addEventListener('click', () => onPick(JSON.parse(button.dataset.security)))); }

const comboOpeners = new Map();

function securityHoldingRowHtml(item) {
  return `<button class="security-result" data-holding="${escapeAttr(JSON.stringify(item))}"><span><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.code)}</small></span><em>持仓 ${number(Number(item.quantity), 4)}</em></button>`;
}

async function renderSecurityDropdown(list, heldOnly) {
  list.classList.remove('hidden');
  const holdings = state.holdings || [];
  let known = [];
  if (!heldOnly) {
    try {
      const data = await request('/api/trades');
      const seen = new Set();
      known = asArray(data, ['trades', 'items']).filter(trade => {
        const id = trade.security_id;
        if (!id || seen.has(id)) return false;
        seen.add(id);
        return true;
      }).map(trade => ({ security_id: trade.security_id, name: trade.name, code: trade.code, exchange: trade.exchange, currency: trade.currency, market: trade.market }));
    } catch { known = []; }
  }
  const heldIds = new Set(holdings.map(item => item.security_id ?? item.id));
  const html = holdings.map(securityHoldingRowHtml).join('') + known.filter(item => !heldIds.has(item.security_id ?? item.id)).map(securityRowHtml).join('');
  list.innerHTML = html || '<div class="muted center">没有可选股票，输入代码或名称搜索</div>';
}

function bindSecurityDropdown(list, onHolding, onSecurity) {
  list.querySelectorAll('[data-holding]').forEach(button => button.addEventListener('click', () => onHolding(JSON.parse(button.dataset.holding))));
  bindSecurityResults(list, onSecurity);
}

function bindSecurityCombo(cfg) {
  const input = $(cfg.inputSel), list = $(cfg.listSel);
  const open = () => { renderSecurityDropdown(list, cfg.heldOnly()).then(() => bindSecurityDropdown(list, cfg.onHolding, cfg.onSecurity)); };
  comboOpeners.set(cfg.chipSel, open);
  const maybeOpen = () => { if (!cfg.getState() && !input.value.trim()) open(); };
  input.addEventListener('focus', maybeOpen);
  input.addEventListener('click', maybeOpen);
  input.addEventListener('input', () => { window.clearTimeout(open.timer); const value = input.value.trim(); open.timer = window.setTimeout(() => { if (!value) open(); else cfg.search(value); }, 220); });
  input.addEventListener('keydown', event => { if (event.key === 'Escape' && !list.classList.contains('hidden')) { event.stopPropagation(); list.classList.add('hidden'); } });
}

function openComboByChip(chipSel) { const open = comboOpeners.get(chipSel); if (open) open(); }

let historyDropdownRequestId = 0;

function pickHistoryFilter(item) {
  $('#history-filter').value = item.name || '';
  $('#history-results').classList.add('hidden');
  state.historyPage = 0;
  renderTrades();
}

function openHistoryDropdown(keyword) {
  const requestId = ++historyDropdownRequestId;
  const list = $('#history-results');
  renderSecurityDropdown(list, false).then(() => {
    if (requestId !== historyDropdownRequestId) return;
    bindSecurityDropdown(list, pickHistoryFilter, pickHistoryFilter);
    const q = (keyword || '').trim().toLowerCase();
    if (!q) return;
    let visible = 0;
    list.querySelectorAll('.security-result').forEach(row => { const match = row.textContent.toLowerCase().includes(q); row.classList.toggle('hidden', !match); if (match) visible++; });
    if (!visible) list.innerHTML = '<div class="muted center">没有匹配的股票</div>';
  });
}

const PERIOD_LABELS = { short: '短期 · 1月左右', medium: '中期 · 3月左右', long: '长期 · 2年左右', forever: '终身' };

function searchReviewSecurities(query) {
  return searchSecuritiesInto({ listSel: '#review-security-results', market: () => 'all', heldOnly: () => false, onPick: selectReviewSecurity }, query);
}

function selectReviewSecurity(security) {
  state.reviewSecurity = security;
  renderSecurityChip('#review-selected-security', security, clearReviewSecurity);
  $('#review-search-block').classList.add('hidden');
  $('#review-security-search').value = '';
  hideSecurityList('#review-security-results');
}

function clearReviewSecurity() {
  state.reviewSecurity = null;
  const el = $('#review-selected-security');
  el.classList.add('hidden');
  el.innerHTML = '';
  $('#review-search-block').classList.remove('hidden');
  $('#review-security-search').value = '';
  hideSecurityList('#review-security-results');
}

async function loadReviews() {
  setRefreshing('#review-container', true);
  setRefreshing('#review-scope-menu', true);
  try {
    const data = await request('/api/reviews');
    state.reviews = asArray(data, ['reviews', 'items']);
    renderReviews();
  } catch (error) {
    toast(error.message, true);
    $('#review-container').innerHTML = `<div class="empty-state">暂时无法加载复盘。${escapeHtml(error.message)}</div>`;
  } finally {
    setRefreshing('#review-container', false);
    setRefreshing('#review-scope-menu', false);
  }
}

function formatDateTime(value) {
  if (!value) return '-';
  const date = beijingParts(value);
  if (!date) return String(value).slice(0, 10);
  const pad = n => String(n).padStart(2, '0');
  return `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())} ${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}`;
}

function formatDate(value) {
  const text = formatDateTime(value);
  return text.length > 10 ? text.slice(0, 10) : text;
}

function reviewCard(item) {
  const period = PERIOD_LABELS[item.holding_period] || item.holding_period || '-';
  const excerpt = item.content_text || '';
  return `<article class="review-card"><div class="review-head"><div><strong>${escapeHtml(item.name || '-')}</strong><small>${escapeHtml(item.code || '')} · ${escapeHtml(item.exchange ? exchangeLabel(item.exchange) : '')}</small></div><span class="period-tag${item.holding_period === 'forever' ? ' forever' : ''}"><em>持仓周期</em>${escapeHtml(period)}</span></div><p class="review-excerpt">${escapeHtml(excerpt)}</p><div class="review-foot"><small>更新于 ${formatDateTime(item.updated_at)}</small><div class="review-actions"><button class="text-action review-view" data-id="${escapeAttr(item.id)}">查看</button><button class="text-action review-edit" data-id="${escapeAttr(item.id)}">编辑</button><button class="text-action danger review-delete" data-id="${escapeAttr(item.id)}">删除</button></div></div></article>`;
}

function reviewScopeGroups() {
  const latest = new Map();
  for (const item of state.reviews) {
    const existing = latest.get(item.security_id);
    if (!existing || (item.updated_at || '') > (existing.updated_at || '')) latest.set(item.security_id, item);
  }
  return [...latest.values()].sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || ''));
}

function renderReviewScopeMenu() {
  const groups = reviewScopeGroups();
  const ids = groups.map(item => item.security_id);
  if (state.reviewScope !== 'all' && !ids.includes(state.reviewScope)) state.reviewScope = 'all';
  const menu = $('#review-scope-menu');
  if (!menu) return;
  const countOf = id => state.reviews.filter(item => item.security_id === id).length;
  const rows = [`<button type="button" class="scope-item${state.reviewScope === 'all' ? ' active' : ''}" data-scope="all"><span>全部</span><small>${state.reviews.length}</small></button>`].concat(
    groups.map(item => `<button type="button" class="scope-item${state.reviewScope === item.security_id ? ' active' : ''}" data-scope="${escapeAttr(item.security_id)}"><span>${escapeHtml(item.name || '-')}</span><small>${countOf(item.security_id)}</small></button>`)
  ).join('');
  menu.innerHTML = rows;
  $$('#review-scope-menu .scope-item').forEach(button => button.addEventListener('click', () => { state.reviewScope = button.dataset.scope; renderReviews(); }));
}

function renderReviews() {
  renderReviewScopeMenu();
  const keyword = ($('#review-filter')?.value || '').trim().toLowerCase();
  const period = $('#review-period')?.value || '';
  const scope = state.reviewScope;
  const rows = state.reviews.filter(item => {
    const text = `${item.name || ''} ${item.code || ''} ${item.content_text || ''}`.toLowerCase();
    return (!keyword || text.includes(keyword)) && (!period || item.holding_period === period) && (scope === 'all' || item.security_id === scope);
  });
  const scoped = scope !== 'all';
  $('#review-container').innerHTML = rows.length ? rows.map(reviewCard).join('') : `<div class="empty-state">${keyword || period || scoped ? '没有匹配的复盘' : '还没有复盘记录，点「新增复盘」写下第一份。'}</div>`;
  const find = id => state.reviews.find(item => String(item.id) === String(id));
  $$('.review-view').forEach(button => button.addEventListener('click', () => openReviewModal('view', find(button.dataset.id))));
  $$('.review-edit').forEach(button => button.addEventListener('click', () => openReviewModal('edit', find(button.dataset.id))));
  $$('.review-delete').forEach(button => button.addEventListener('click', () => deleteReview(button.dataset.id)));
}

function openReviewModal(mode, review = null, quick = null) {
  state.reviewMode = quick ? 'create' : mode;
  state.reviewEditId = review ? review.id : null;
  const viewing = mode === 'view';
  $('#review-modal-title').textContent = viewing ? '查看复盘' : mode === 'edit' ? '编辑复盘' : quick ? `写复盘 · ${quick.name || '-'}` : '新增复盘';
  const editor = $('#review-editor');
  editor.contentEditable = viewing ? 'false' : 'true';
  editor.classList.toggle('readonly', viewing);
  $('#review-toolbar').classList.toggle('hidden', viewing);
  $('#review-save').classList.toggle('hidden', viewing);
  $('#review-edit').classList.toggle('hidden', !viewing);
  $('#review-cancel').textContent = viewing ? '关闭' : '取消';
  $('#review-periods').classList.toggle('locked', viewing);
  $('#review-modal .modal-body').classList.toggle('viewing', viewing);
  $('#review-modal .modal-body').classList.toggle('quick', !!quick);
  $('#review-modal .modal-body').classList.toggle('editing', mode === 'edit');
  $('#review-steps').classList.toggle('hidden', !!quick);
  $('#review-step-1').classList.toggle('hidden', !!quick);
  $('#review-search-block').classList.toggle('hidden', !!quick || mode === 'edit');
  const lockedBar = $('#review-locked-bar');
  if (quick) {
    lockedBar.classList.remove('hidden');
    lockedBar.innerHTML = `<div><strong>${escapeHtml(quick.name || '-')}</strong><small>${escapeHtml(quick.code || '')} · ${escapeHtml(exchangeLabel(quick.exchange))}</small></div><span class="period-tag${quick.period === 'forever' ? ' forever' : ''}"><em>持仓周期</em>${escapeHtml(PERIOD_LABELS[quick.period] || quick.period || '-')}</span>`;
  } else {
    lockedBar.classList.add('hidden');
    lockedBar.innerHTML = '';
  }
  if (viewing && review) {
    $('#review-view-name').textContent = review.name || '-';
    $('#review-view-meta').textContent = `${review.code || ''} · ${exchangeLabel(review.exchange)} · 更新于 ${formatDateTime(review.updated_at)}`;
    const tag = $('#review-view-period');
    tag.className = `period-tag${review.holding_period === 'forever' ? ' forever' : ''}`;
    tag.innerHTML = `<em>持仓周期</em>${escapeHtml(PERIOD_LABELS[review.holding_period] || review.holding_period || '-')}`;
    $('#review-view-content').innerHTML = review.content_html || '';
  }
  if (quick) {
    state.reviewSecurity = { security_id: quick.security_id, name: quick.name, code: quick.code, exchange: quick.exchange, currency: quick.currency };
    state.reviewPeriod = quick.period;
    editor.innerHTML = '';
  } else if (review) {
    state.reviewSecurity = { security_id: review.security_id, name: review.name, code: review.code, exchange: review.exchange, currency: review.currency };
    const el = $('#review-selected-security');
    el.classList.remove('hidden');
    el.innerHTML = `<div><strong>${escapeHtml(review.name || '-')}</strong><small>${escapeHtml(review.code || '')} · ${escapeHtml(exchangeLabel(review.exchange))}</small></div>${mode === 'create' ? '<button type="button" class="text-action" id="review-clear-security">更换</button>' : ''}`;
    if (mode === 'create') $('#review-clear-security').addEventListener('click', clearReviewSecurity);
    state.reviewPeriod = review.holding_period;
    editor.innerHTML = review.content_html || '';
  } else {
    clearReviewSecurity();
    state.reviewPeriod = '';
    editor.innerHTML = '';
  }
  $$('#review-periods button').forEach(button => button.classList.toggle('active', button.dataset.period === state.reviewPeriod));
  openModal('#review-modal');
  if (!viewing) (quick || mode === 'edit' ? editor : $('#review-security-search')).focus();
}

function closeReviewModal() { closeModal('#review-modal'); }

let confirmResolver = null;

function confirmDialog(message, title = '确认操作') {
  $('#confirm-modal-title').textContent = title;
  $('#confirm-message').textContent = message;
  openModal('#confirm-modal');
  $('#confirm-ok').focus();
  return new Promise(resolve => { confirmResolver = resolve; });
}

function closeConfirm(result) {
  closeModal('#confirm-modal');
  if (confirmResolver) { const resolve = confirmResolver; confirmResolver = null; resolve(result); }
}

async function saveReview() {
  if (!state.reviewSecurity?.security_id) { toast('请先选择企业', true); return; }
  if (!state.reviewPeriod) { toast('请选择持仓周期', true); return; }
  const editor = $('#review-editor');
  if (!editorError(editor, '复盘内容')) return;
  const body = { security_id: state.reviewSecurity.security_id, holding_period: state.reviewPeriod, content_html: editor.innerHTML };
  try {
    if (state.reviewMode === 'edit' && state.reviewEditId) await request(`/api/reviews/${state.reviewEditId}`, { method: 'PUT', body });
    else await request('/api/reviews', { method: 'POST', body });
    toast(state.reviewMode === 'edit' ? '复盘已更新' : '复盘已保存');
    closeReviewModal();
    await loadReviews();
  } catch (error) { toast(error.message, true); }
}

async function deleteReview(id) {
  if (!await confirmDialog('删除后不可恢复，确认删除这份复盘？', '删除复盘')) return;
  try {
    await request(`/api/reviews/${id}`, { method: 'DELETE' });
    toast('复盘已删除');
    await loadReviews();
  } catch (error) { toast(error.message, true); }
}

function bindRteToolbar(toolbarSelector, editorSelector) {
  $$(`${toolbarSelector} button`).forEach(button => {
    button.addEventListener('mousedown', event => event.preventDefault());
    button.addEventListener('click', () => {
      const cmd = button.dataset.cmd;
      $(editorSelector).focus();
      if (cmd === 'quote' || cmd === 'h3') {
        const tag = cmd === 'quote' ? 'blockquote' : 'h3';
        const current = String(document.queryCommandValue('formatBlock') || '').toLowerCase();
        document.execCommand('formatBlock', false, current === tag ? 'p' : tag);
      } else {
        document.execCommand(cmd, false, null);
      }
    });
  });
}

function bindReviewToolbar() {
  bindRteToolbar('#review-toolbar', '#review-editor');
  bindRteToolbar('#watch-toolbar', '#watch-editor');
  bindRteToolbar('#watchnote-toolbar', '#watchnote-editor');
  bindReviewEditorKeys();
}

const LIST_MAX_DEPTH = 5;

function currentListItem() {
  const selection = window.getSelection();
  if (!selection || !selection.rangeCount) return null;
  let node = selection.getRangeAt(0).startContainer;
  if (node.nodeType === 3) node = node.parentElement;
  return node && node.closest ? node.closest('li') : null;
}

function listDepthOf(li) {
  let depth = 0;
  let parent = li ? li.parentElement : null;
  while (parent && !parent.classList.contains('rte-editor')) {
    if (parent.tagName === 'UL' || parent.tagName === 'OL') depth += 1;
    parent = parent.parentElement;
  }
  return depth;
}

function moveCaretToOwnTextEnd(el) {
  const own = [...el.childNodes].filter(node => !(node.nodeType === 1 && (node.tagName === 'UL' || node.tagName === 'OL')));
  const textNodes = own.filter(node => node.nodeType === 3 && node.textContent.length);
  const range = document.createRange();
  if (textNodes.length) {
    const last = textNodes[textNodes.length - 1];
    range.setStart(last, last.textContent.length);
  } else {
    range.setStart(el, 0);
  }
  range.collapse(true);
  const selection = window.getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
}

function subtreeMaxDepth(li) {
  let max = listDepthOf(li);
  li.querySelectorAll('li').forEach(child => { max = Math.max(max, listDepthOf(child)); });
  return max;
}

function indentListItem(li) {
  if (subtreeMaxDepth(li) >= LIST_MAX_DEPTH) {
    const now = Date.now();
    if (now - (indentListItem.lastTip || 0) > 1500) { indentListItem.lastTip = now; toast(`列表最多支持 ${LIST_MAX_DEPTH} 层`); }
    return;
  }
  const prev = li.previousElementSibling;
  if (!prev || prev.tagName !== 'LI') return;
  let sub = [...prev.children].find(el => el.tagName === 'UL' || el.tagName === 'OL');
  if (!sub) { sub = document.createElement(li.parentElement.tagName); prev.appendChild(sub); }
  sub.appendChild(li);
  moveCaretToOwnTextEnd(li);
}

function outdentListItem(li) {
  const list = li.parentElement;
  const grandLi = list && list.parentElement ? list.parentElement.closest('li') : null;
  if (!grandLi) {
    const paragraph = document.createElement('p');
    while (li.firstChild) paragraph.appendChild(li.firstChild);
    li.replaceWith(paragraph);
    moveCaretToOwnTextEnd(paragraph);
    return;
  }
  grandLi.after(li);
  if (list && !list.children.length) list.remove();
  moveCaretToOwnTextEnd(li);
}

function pruneEmptyLists(event) {
  const editor = event && event.currentTarget ? event.currentTarget : $('#review-editor');
  const selection = window.getSelection();
  const caretNode = selection && selection.rangeCount ? selection.getRangeAt(0).startContainer : null;
  const caretEl = caretNode ? (caretNode.nodeType === 3 ? caretNode.parentElement : caretNode) : null;
  for (const list of [...editor.querySelectorAll('ul,ol')].reverse()) {
    if (caretEl && list.contains(caretEl)) continue;
    const hasContent = [...list.querySelectorAll('li')].some(li => (li.textContent || '').trim() || [...li.children].some(child => child.tagName === 'UL' || child.tagName === 'OL'));
    if (!hasContent) list.remove();
  }
  for (const li of [...editor.querySelectorAll('li')].reverse()) {
    if (caretEl && li.contains(caretEl)) continue;
    const hasChildList = [...li.children].some(child => child.tagName === 'UL' || child.tagName === 'OL');
    if (!(li.textContent || '').trim() && !hasChildList) li.remove();
  }
}

function bindReviewEditorKeys() {
  ['#review-editor', '#watch-editor', '#watchnote-editor'].forEach(selector => bindRteKeys(selector));
}

function bindRteKeys(editorSelector) {
  const editor = $(editorSelector);
  editor.addEventListener('input', pruneEmptyLists);
  editor.addEventListener('keydown', event => {
    if ((event.key === 'Backspace' || event.key === 'Delete') && window.getSelection()?.isCollapsed) {
      const li = currentListItem();
      if (li && !(li.textContent || '').trim() && !li.querySelector('ul,ol')) {
        event.preventDefault();
        outdentListItem(li);
        pruneEmptyLists(event);
        return;
      }
    }
    if (event.key === 'Tab') {
      event.preventDefault();
      const li = currentListItem();
      if (!li) return;
      if (event.shiftKey) { if (listDepthOf(li) > 1) outdentListItem(li); }
      else indentListItem(li);
      return;
    }
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      const li = currentListItem();
      if (!li) return;
      const empty = !(li.textContent || '').trim() && !li.querySelector('ul,ol');
      if (!empty) return;
      event.preventDefault();
      outdentListItem(li);
    }
  });
}

function renderFeePreview() {
  const security = state.selectedSecurity;
  const side = state.side;
  const sideBadge = $('#preview-side');
  if (sideBadge) { sideBadge.textContent = side === 'buy' ? '买入' : '卖出'; sideBadge.className = `preview-badge ${side}`; }
  const stockBox = $('#preview-stock');
  const funds = $('#fee-preview');
  const payEl = $('#preview-pay');
  const afterEl = $('#preview-after');
  const costEl = $('#preview-cost');
  const hint = $('#trade-held-hint');
  if (hint) {
    if (side === 'sell' && security) hint.textContent = `可卖 ${number(heldQuantity(security.security_id ?? security.id), 4)} 股`;
    else if (side === 'buy' && security) {
      const cashCurrency = security.currency || ((security.market || '').toUpperCase() === 'HK' ? 'HKD' : 'CNY');
      hint.textContent = `可用 ${money(Number(state.cash?.[cashCurrency] || 0), cashCurrency)}`;
    } else hint.textContent = '股';
  }
  if (!security) {
    state.tradeFeeTotal = 0;
    if (stockBox) stockBox.innerHTML = '<strong class="muted">尚未选择股票</strong><small>选择后显示成交金额与费用</small>';
    if (funds) funds.innerHTML = '<div class="fee-line"><span>成交金额</span><span>--</span></div><div class="fee-line total"><span>费用合计</span><span>--</span></div>';
    if (payEl) payEl.textContent = '--';
    if (afterEl) { afterEl.textContent = '--'; afterEl.className = ''; }
    if (costEl) costEl.textContent = '--';
    return;
  }
  const price = Number($('#trade-price')?.value || 0);
  const qty = Number($('#trade-quantity')?.value || 0);
  const amount = price > 0 && qty > 0 ? Math.round(price * qty * 100) / 100 : 0;
  const market = security.market === 'hk' || security.market === 'HK' || security.currency === 'HKD' ? 'hk' : 'a';
  const currency = security.currency || (market === 'hk' ? 'HKD' : 'CNY');
  const qtyText = qty > 0 ? `${number(qty, 4)} 股` : null;
  const priceText = price > 0 ? `@ ${priceMoney(price, currency)}` : null;
  const meta = [security.exchange ? exchangeLabel(security.exchange) : null, qtyText && priceText ? `${qtyText} ${priceText}` : (qtyText || priceText)].filter(Boolean).join(' · ');
  if (stockBox) stockBox.innerHTML = `<strong>${escapeHtml(security.name || security.security_name || '')} ${escapeHtml(security.code || '')}</strong><small>${escapeHtml(meta || '待填写数量与单价')}</small>`;
  if (!amount) {
    state.tradeFeeTotal = 0;
    if (funds) funds.innerHTML = '<div class="fee-line"><span>成交金额</span><span>--</span></div><div class="fee-line total"><span>费用合计</span><span>--</span></div>';
    if (payEl) payEl.textContent = '--';
    if (afterEl) { afterEl.textContent = '--'; afterEl.className = ''; }
    if (costEl) costEl.textContent = '--';
    return;
  }
  const feeMarket = market === 'hk' ? 'HK' : ({ SSE: 'SH', SZSE: 'SZ', BSE: 'BSE' }[security.exchange] || security.fee_market);
  const loadedRules = state.feeRules[market] || [];
  const wantMarket = feeMarket && loadedRules.some(rule => rule.market === feeMarket) ? feeMarket : (market === 'a' ? 'SH' : feeMarket);
  const rules = loadedRules.filter(rule => rule.enabled !== 0 && (!wantMarket || rule.market === wantMarket) && (!rule.side || rule.side === side));
  let total = 0;
  const lines = rules.map(rule => { const fee = calculateFee(rule, amount); total += fee; return { name: rule.name || rule.fee_name || '费用', fee }; });
  state.tradeFeeTotal = total;
  if (funds) funds.innerHTML = `<div class="fee-line amount"><span>成交金额</span><span>${money(amount, currency)}</span></div>${lines.map(line => `<div class="fee-line"><span>${escapeHtml(line.name)}</span><span>${money(line.fee, currency)}</span></div>`).join('')}<div class="fee-line total"><span>费用合计</span><span>${money(total, currency)}</span></div>`;
  if (payEl) payEl.textContent = side === 'buy' ? `-${absMoney(amount + total, currency)}` : `+${absMoney(amount - total, currency)}`;
  const available = Number(state.cash?.[currency] || 0);
  const afterCash = side === 'buy' ? available - (amount + total) : available + (amount - total);
  const insufficient = side === 'buy' && afterCash < 0;
  if (afterEl) { afterEl.textContent = `${money(afterCash, currency)}${insufficient ? ' · 可用金额不足' : ''}`; afterEl.className = insufficient ? 'cash-negative' : ''; }
  const holding = (state.holdings || []).find(item => String(item.security_id ?? item.id) === String(security.security_id ?? security.id));
  const heldQty = Number(holding?.quantity || 0);
  const heldCost = Number(holding?.average_cost || 0);
  let afterCost = null;
  if (side === 'buy') {
    const nextQty = heldQty + qty;
    if (nextQty > 0) afterCost = (heldQty * heldCost + amount + total) / nextQty;
  } else {
    const nextQty = heldQty - qty;
    if (nextQty > 0) afterCost = state.costMethod === 'diluted' ? (heldQty * heldCost - (amount - total)) / nextQty : heldCost;
  }
  if (costEl) costEl.textContent = afterCost == null ? '--' : money(afterCost, currency);
}

function calculateFee(rule, amount) {
  if (rule.waived) return 0;
  const type = rule.calc_type || rule.type || (Number(rule.rate) ? 'ratio' : 'fixed');
  if (rule.included || type === 'included' || type === 'zero' || type === 'none') return 0;
  let fee;
  if (type === 'fixed') {
    fee = Number(rule.fixed_amount ?? rule.amount ?? rule.value ?? 0);
  } else {
    fee = amount * Number(rule.rate ?? rule.value ?? 0);
    const minimum = Number(rule.min_fee ?? 0);
    const maximum = Number(rule.max_fee ?? 0);
    if ((type === 'ratio_min' || type === 'ratio_min_max') && minimum > fee) fee = minimum;
    if ((type === 'ratio_max' || type === 'ratio_min_max') && maximum > 0 && fee > maximum) fee = maximum;
    fee = roundFeeClient(fee, rule.rounding);
  }
  return Math.max(0, fee);
}

function roundFeeClient(fee, rounding) {
  if (rounding === 'none' || rounding === 'raw') return fee;
  if (rounding === 'ceil_unit' || rounding === 'ceil_dollar') return Math.ceil(fee);
  if (rounding === 'floor' || rounding === 'floor_cent') return Math.floor(fee * 100) / 100;
  if (rounding === 'ceil' || rounding === 'ceil_cent' || rounding === 'integer_up') return Math.ceil(fee * 100) / 100;
  return Math.round(fee * 100) / 100;
}

function feeLineDetail(line) {
  if (line.status === 'included') return '已包含在佣金中';
  const legacy = /^(ratio|fixed|zero|included)\(/.exec(line.formula || '');
  if (legacy) {
    if (legacy[1] === 'zero' || line.calc_type === 'zero') return '未收取';
    return `按保存时费率计算 · 基数 ${number(Number(line.base_amount ?? 0), 3)}`;
  }
  if (line.status === 'not_charged' || line.calc_type === 'zero') return '未收取';
  return line.formula || '';
}

function feeSnapshotHtml(trade, currency) {
  const lines = trade.fee_details || [];
  if (!lines.length) return '<div class="muted">这笔交易保存时没有费用明细</div>';
  const rows = lines.map(line => {
    const amount = Number(line.amount ?? 0);
    const detail = amount === 0 ? '未收取' : feeLineDetail(line);
    return `<div class="fee-line"><span>${escapeHtml(line.name)}<small>${escapeHtml(detail)}</small></span><span>${money(amount, currency)}</span></div>`;
  }).join('');
  const total = lines.reduce((sum, line) => sum + Number(line.amount ?? 0), 0);
  return `${rows}<div class="fee-line total"><span>合计</span><span>${money(total, currency)}</span></div>`;
}

function syncRangeTabs() { $$('.range').forEach(button => button.classList.toggle('active', button.dataset.range === state.range)); }

function syncWatchPills() {
  $$('#watch-markets .market-pill').forEach(button => button.classList.toggle('active', button.dataset.wmarket === state.watchMarket));
  $$('#watch-status .market-pill').forEach(button => button.classList.toggle('active', button.dataset.wstatus === state.watchStatus));
}

function tradesUrl(offset, limit = state.tradesPage.limit) { return `/api/trades?limit=${limit}&offset=${offset}`; }

async function loadHistory() {
  const data = await request('/api/trades?limit=500&offset=0');
  state.historyTrades = asArray(data, ['trades', 'items']);
  state.historyTotal = Number(data.total ?? state.historyTrades.length);
  renderTrades();
}

function historyNetText(trade, currency) {
  const amount = Number(trade.gross_amount ?? trade.amount ?? 0);
  const fee = Number(trade.total_fee ?? trade.fee_total ?? trade.fee ?? 0);
  return trade.side === 'sell' ? `+${absMoney(amount - fee, currency)}` : `-${absMoney(amount + fee, currency)}`;
}

function renderTrades() {
  const filter = ($('#history-filter')?.value || '').trim().toLowerCase();
  const side = state.historySide || '';
  const all = state.historyTrades || [];
  const rows = all.filter(trade => {
    const text = `${trade.name || trade.security_name || ''} ${trade.code || trade.symbol_code || ''}`.toLowerCase();
    return (!filter || text.includes(filter)) && (!side || trade.side === side);
  });
  const size = state.historyPageSize;
  const pages = Math.max(1, Math.ceil(rows.length / size));
  state.historyPage = Math.min(Math.max(state.historyPage, 0), pages - 1);
  const pageRows = rows.slice(state.historyPage * size, state.historyPage * size + size);
  const filtered = !!(filter || side);
  const emptyText = filtered ? '没有符合当前筛选的成交。' : '还没有交易记录，先在记录交易页录入第一笔。';
  $('#trades-body').innerHTML = pageRows.length ? pageRows.map(trade => {
    const currency = trade.currency || (trade.market === 'hk' ? 'HKD' : 'CNY');
    const amount = Number(trade.gross_amount ?? trade.amount ?? 0);
    const fee = Number(trade.total_fee ?? trade.fee_total ?? trade.fee ?? 0);
    const unit = trade.unit_price ? Number(trade.unit_price) : (Number(trade.quantity) ? amount / Number(trade.quantity) : 0);
    const details = trade.fee_details || [];
    const expanded = state.expandedFees.has(String(trade.id));
    const toggle = details.length ? `<button type="button" class="text-action compact history-fee-toggle" data-fee-id="${escapeAttr(trade.id)}" aria-expanded="${expanded}">${expanded ? '收起' : '明细'}</button>` : '';
    const exchange = trade.exchange ? ` · ${escapeHtml(exchangeLabel(trade.exchange))}` : '';
    const recorded = trade.created_at ? formatDateTime(trade.created_at) : (trade.trade_date || trade.date || '-');
    const tradeDate = trade.trade_date || trade.date || '';
    const dateCell = `${escapeHtml(recorded)}${tradeDate && tradeDate !== recorded.slice(0, 10) ? `<small class="history-date-sub">成交日 ${escapeHtml(tradeDate)}</small>` : ''}`;
    const row = `<tr class="history-row"><td>${dateCell}</td><td class="history-stock"><strong>${escapeHtml(trade.name || trade.security_name || '-')}</strong><small>${escapeHtml(trade.code || trade.symbol_code || '')}${exchange}</small></td><td><span class="history-side ${trade.side === 'sell' ? 'sell' : 'buy'}">${trade.side === 'sell' ? '卖出' : '买入'}</span></td><td>${number(trade.quantity, 4)}</td><td>${priceMoney(unit, currency)}</td><td>${money(amount, currency)}</td><td>${money(fee, currency)}${toggle}</td><td>${historyNetText(trade, currency)}</td><td>${formatMarketCap(trade.market_cap, currency)}</td><td><button class="text-action danger history-void" type="button" data-id="${escapeAttr(trade.id)}" aria-label="作废这笔交易">作废</button></td></tr>`;
    if (!expanded) return row;
    const total = details.reduce((sum, line) => sum + Number(line.amount ?? 0), 0);
    const cells = details.map(line => {
      const feeAmount = Number(line.amount ?? 0);
      const detail = feeAmount === 0 ? '未收取' : feeLineDetail(line);
      return `<div class="history-fee-cell"><span>${escapeHtml(line.name)}</span><small>${escapeHtml(detail)}</small><strong>${money(feeAmount, currency)}</strong></div>`;
    }).join('');
    return row + `<tr class="history-fee-row"><td colspan="10"><div class="history-fee-box"><div class="history-fee-grid">${cells}<div class="history-fee-cell total"><span>合计</span><small>${details.length} 项费用</small><strong>${money(total, currency)}</strong></div></div></div></td></tr>`;
  }).join('') : `<tr><td colspan="10" class="history-empty">${emptyText}</td></tr>`;
  $('#history-count').textContent = filtered ? `筛选出 ${rows.length} 笔 / 共 ${all.length} 笔` : `共 ${all.length} 笔成交`;
  $('#history-page-info').textContent = pageRows.length ? `第 ${state.historyPage + 1} / ${pages} 页` : '';
  $('#history-prev').disabled = state.historyPage <= 0 || !pageRows.length;
  $('#history-next').disabled = state.historyPage >= pages - 1 || !pageRows.length;
  $$('#trades-body .history-void').forEach(button => button.addEventListener('click', () => deleteTrade(button.dataset.id)));
  $$('#trades-body .history-fee-toggle').forEach(button => button.addEventListener('click', () => {
    const id = String(button.dataset.feeId);
    if (state.expandedFees.has(id)) state.expandedFees.delete(id); else state.expandedFees.add(id);
    renderTrades();
  }));
}

function feeConfigKey(rule) {
  const isRate = ['rate', 'ratio', 'ratio_min', 'ratio_min_max'].includes(rule.calc_type) || rule.type === 'rate' || rule.type === 'ratio';
  return JSON.stringify([rule.calc_type, rule.type, isRate ? rule.rate : (rule.fixed_amount ?? rule.amount), rule.min_fee ?? rule.minimum, rule.max_fee ?? rule.maximum, rule.rounding, !!rule.included, !!rule.waived]);
}

function mergeFeeSides(rows) {
  const byName = new Map();
  for (const rule of rows) {
    const name = rule.name || rule.fee_name || '未命名费用';
    if (!byName.has(name)) byName.set(name, { name, configs: {} });
    byName.get(name).configs[rule.side === 'sell' ? 'sell' : 'buy'] = rule;
  }
  const out = [];
  for (const entry of byName.values()) {
    const { buy, sell } = entry.configs;
    if (buy && sell && feeConfigKey(buy) === feeConfigKey(sell)) out.push({ name: entry.name, sides: ['buy', 'sell'], both: true, rep: buy, configs: { buy, sell } });
    else { if (buy) out.push({ name: entry.name, sides: ['buy'], both: false, rep: buy, configs: { buy } }); if (sell) out.push({ name: entry.name, sides: ['sell'], both: false, rep: sell, configs: { sell } }); }
  }
  return out;
}

function renderFeeRules() {
  const markets = [{ key: 'a', title: 'A 股', subtitle: '上交所 · 深交所 · 北交所 · CNY' }, { key: 'hk', title: '港股', subtitle: '港交所 · HKD' }];
  $('#fee-rules-container').innerHTML = markets.map(market => `<article class="fee-market-card"><div class="fee-market-head"><div><h3>${market.title}</h3><small>${market.subtitle}</small></div><small>生效日期由服务器管理</small></div><div class="table-scroll"><table class="fee-table"><thead><tr><th>费用项目</th><th>方向</th><th>收费方式</th><th>费率 / 金额</th><th>最低收费</th><th>最高收费</th><th>取整</th><th>不收费</th></tr></thead><tbody>${(state.feeDisplay[market.key] || []).map((entry, index) => { const rule = entry.rep || {}; const isRate = ['rate', 'ratio', 'ratio_min', 'ratio_min_max'].includes(rule.calc_type) || rule.type === 'rate' || rule.type === 'ratio'; const dir = entry.both ? '双向' : entry.sides[0] === 'sell' ? '卖出' : '买入'; return `<tr data-market="${market.key}" data-index="${index}"><td data-label="费用项目">${escapeHtml(entry.name)}</td><td data-label="方向">${dir}</td><td data-label="收费方式"><select class="fee-select fee-type"><option value="zero" ${rule.calc_type === 'zero' || rule.type === 'none' ? 'selected' : ''}>0 / 不收取</option><option value="fixed" ${rule.calc_type === 'fixed' || rule.type === 'fixed' ? 'selected' : ''}>固定金额</option><option value="rate" ${isRate ? 'selected' : ''}>按比例</option><option value="included" ${rule.included || rule.type === 'included' ? 'selected' : ''}>已包含</option></select></td><td data-label="费率 / 金额"><input class="fee-input fee-value" type="number" step="any" min="0" value="${escapeAttr(plainNumber(isRate ? rule.rate ?? 0 : rule.fixed_amount ?? rule.amount ?? rule.value ?? 0))}"></td><td data-label="最低收费"><input class="fee-input fee-min" type="number" step="any" min="0" value="${escapeAttr(plainNumber(rule.min_fee ?? rule.minimum ?? 0))}"></td><td data-label="最高收费"><input class="fee-input fee-max" type="number" step="any" min="0" value="${escapeAttr(plainNumber(rule.max_fee ?? rule.maximum ?? 0))}"></td><td data-label="取整"><select class="fee-select fee-round"><option value="cent">按分</option><option value="integer_up" ${rule.rounding === 'integer_up' || rule.rounding === 'ceil' ? 'selected' : ''}>向上取整</option><option value="floor" ${rule.rounding === 'floor' ? 'selected' : ''}>向下取整</option><option value="ceil_unit" ${rule.rounding === 'ceil_unit' || rule.rounding === 'ceil_dollar' ? 'selected' : ''}>向上取整到元</option></select></td><td data-label="不收费"><label class="fee-waive"><input class="fee-waived" type="checkbox" ${rule.waived ? 'checked' : ''}><span>不收费</span></label></td></tr>`; }).join('') || '<tr><td colspan="8" class="muted center">暂无规则</td></tr>'}</tbody></table></div></article>`).join('');
}

async function saveSettings() {
  try {
    for (const market of ['a', 'hk']) {
      const rows = [];
      const display = state.feeDisplay[market] || [];
      $$(`tr[data-market="${market}"]`).forEach(row => { const entry = display[Number(row.dataset.index)] || { name: '', sides: ['buy'], configs: {} }; const type = $('.fee-type', row).value; const value = Math.max(0, Number($('.fee-value', row).value || 0)); const minimum = Math.max(0, Number($('.fee-min', row).value || 0)); const maximum = Math.max(0, Number($('.fee-max', row).value || 0)); $('.fee-value', row).value = value; $('.fee-min', row).value = minimum; $('.fee-max', row).value = maximum; const rounding = $('.fee-round', row).value; const waived = !!$('.fee-waived', row)?.checked; const rateCalc = minimum > 0 && maximum > 0 ? 'ratio_min_max' : minimum > 0 ? 'ratio_min' : maximum > 0 ? 'ratio_max' : 'ratio'; for (const side of entry.sides) { const old = entry.configs[side] || entry.rep || {}; rows.push({ ...old, calc_type: type === 'rate' ? rateCalc : type, rate: type === 'rate' ? plainNumber(value) : 0, fixed_amount: type === 'fixed' ? plainNumber(value) : 0, min_fee: plainNumber(minimum), max_fee: plainNumber(maximum), rounding, included: type === 'included', waived, side, name: entry.name }); } });
      if (!rows.length) continue;
      await request('/api/fees', { method: 'PUT', body: { market: market === 'a' ? 'A' : 'HK', rules: rows } });
      state.feeRules[market] = rows;
      state.feeDisplay[market] = mergeFeeSides(rows);
    }
    await request('/api/settings', { method: 'PUT', body: { cost_method: $('#cost-method').value } });
    toast('设置已保存');
    renderFeeRules();
    renderFeePreview();
    await loadDashboard();
  } catch (error) { toast(error.message, true); }
}

function rangeCutoff(range) {
  const now = new Date(); const pad = n => String(n).padStart(2, '0'); const local = d => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  if (range === 'month') return local(new Date(now.getFullYear(), now.getMonth(), 1));
  const months = { '3m': 3, '6m': 6, '1y': 12 }[range];
  if (!months) return null;
  const head = new Date(now.getFullYear(), now.getMonth() - months, 1);
  const lastDay = new Date(head.getFullYear(), head.getMonth() + 1, 0).getDate();
  return local(new Date(head.getFullYear(), head.getMonth(), Math.min(now.getDate(), lastDay)));
}

async function selectChartSecurity(id, name, code, currency) {
  state.chartSecurity = { id, name, code, currency };
  $('#chart-tip').classList.add('hidden');
  $('#chart-title').textContent = `${name} ${code}`;
  const holding = (state.holdings || []).find(item => String(item.security_id ?? item.id) === String(id));
  $('#chart-cost').textContent = holding ? `成本 ${money(Number(holding.average_cost), currency)}` : '';
  $('#chart-latest').textContent = holding ? `最新 ${priceMoney(Number(holding.last_price), currency)}` : '';
  syncHoldingSelection();
  try {
    const data = await request(`/api/prices?security_id=${encodeURIComponent(id)}&range=${encodeURIComponent(state.range)}`);
    const cutoff = rangeCutoff(state.range);
    state.chartPrices = asArray(data, ['prices', 'items', 'data']).filter(point => !cutoff || (point.price_date || '') >= cutoff);
    drawChart();
  } catch (error) { state.chartPrices = []; drawChart(); toast(error.message, true); }
}

function monotoneTangents(xs, ys) {
  const n = ys.length;
  const slopes = [];
  for (let i = 0; i < n - 1; i++) slopes[i] = (ys[i + 1] - ys[i]) / ((xs[i + 1] - xs[i]) || 1);
  const tangents = new Array(n);
  tangents[0] = slopes[0];
  tangents[n - 1] = slopes[n - 2];
  for (let i = 1; i < n - 1; i++) tangents[i] = slopes[i - 1] * slopes[i] <= 0 ? 0 : (slopes[i - 1] + slopes[i]) / 2;
  for (let i = 0; i < n - 1; i++) {
    if (slopes[i] === 0) { tangents[i] = 0; tangents[i + 1] = 0; continue; }
    const a = tangents[i] / slopes[i]; const b = tangents[i + 1] / slopes[i];
    const radius = a * a + b * b;
    if (radius > 9) { const scale = 3 / Math.sqrt(radius); tangents[i] = scale * a * slopes[i]; tangents[i + 1] = scale * b * slopes[i]; }
  }
  return tangents;
}

function traceMonotone(ctx, pts) {
  const xs = pts.map(point => point[0]); const ys = pts.map(point => point[1]);
  const tangents = monotoneTangents(xs, ys);
  ctx.beginPath();
  ctx.moveTo(xs[0], ys[0]);
  for (let i = 0; i < pts.length - 1; i++) {
    const step = xs[i + 1] - xs[i];
    ctx.bezierCurveTo(xs[i] + step / 3, ys[i] + tangents[i] * step / 3, xs[i + 1] - step / 3, ys[i + 1] - tangents[i + 1] * step / 3, xs[i + 1], ys[i + 1]);
  }
}

function updateChartLabel() {
  const canvas = $('#price-chart');
  if (!canvas) return;
  const range = BOARD_RANGE_LABELS[state.range] || '';
  canvas.setAttribute('role', 'img');
  canvas.setAttribute('aria-label', state.chartSecurity ? `${state.chartSecurity.name} ${range}收盘价走势折线图` : '暂无持仓走势折线图');
}

function overviewChartCard() {
  const security = state.chartSecurity;
  const currency = security?.currency || 'CNY';
  const series = (state.chartPrices || [])
    .map(point => ({ d: String(point.price_date || ''), p: Number(point.price ?? point.close) }))
    .filter(point => Number.isFinite(point.p));
  const holding = (state.holdings || []).find(item => String(item.security_id ?? item.id) === String(security?.id));
  const cost = Number(holding?.average_cost);
  const marks = (state.trades || [])
    .filter(trade => String(trade.security_id) === String(security?.id))
    .map(trade => ({
      date: trade.trade_date,
      side: trade.side,
      unit_price: trade.unit_price ?? (Number(trade.quantity) ? Number(trade.gross_amount) / Number(trade.quantity) : null),
      quantity: trade.quantity,
      fee: trade.fee_total,
    }))
    .filter(mark => mark.date && Number.isFinite(Number(mark.unit_price)));
  return { security_id: `overview-${security?.id ?? 'none'}`, currency, series, marks, cost_line: Number.isFinite(cost) ? cost : null, cost_label: '成本' };
}

function overviewChartOptions() {
  const wrap = $('#price-chart').parentElement;
  const style = getComputedStyle(wrap);
  return { height: Math.max(160, Math.round(wrap.clientHeight - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom))), costWithinRange: true };
}

function drawChart() {
  updateChartLabel();
  const canvas = $('#price-chart');
  const empty = $('#chart-empty');
  const card = overviewChartCard();
  if (card.series.length < 2) {
    state.boardGeom[card.security_id] = null;
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    empty.textContent = state.chartSecurity ? (card.series.length ? '该区间价格点不足' : '该区间暂无价格数据') : '当前没有持仓';
    empty.classList.remove('hidden');
    return;
  }
  empty.classList.add('hidden');
  drawBoardChart(canvas, card, overviewChartOptions());
}

async function loadBoard() {
  const wrap = $('#board-cards');
  if (!state.board) wrap.innerHTML = '<div class="empty-state">正在加载看板…</div>';
  try {
    const base = await request(`/api/board?range=${encodeURIComponent(state.boardRange)}`);
    state.boardCache = { [state.boardRange]: base };
    state.board = base;
    renderBoard();
  } catch (error) {
    wrap.innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`;
  }
}

async function ensureBoardRange(range) {
  if (!state.boardCache[range]) state.boardCache[range] = await request(`/api/board?range=${encodeURIComponent(range)}`);
  return state.boardCache[range];
}

function mergedBoardCard(baseCard) {
  const range = state.boardCardRange[baseCard.security_id] || state.boardRange;
  const payload = state.boardCache[range];
  const ranged = payload ? (payload.cards || []).find(item => item.security_id === baseCard.security_id) : null;
  return Object.assign({}, baseCard, ranged ? { series: ranged.series, marks: ranged.marks, range_change: ranged.range_change } : {}, { range });
}

function renderBoard() {
  const data = state.board;
  if (!data) return;
  const totals = data.totals || {};
  $('#board-totals').innerHTML = Object.entries(totals).map(([currency, bucket]) => {
    const floating = Number(bucket.unrealized ?? 0);
    return `<div class="board-sum-card"><div class="board-sum-cur">${currencyLabel(currency)} <em>${escapeHtml(currency)}</em></div><div class="board-sum-grid">` + [
      `<div class="board-sum-cell"><span>累计投入</span><strong>${money(bucket.invested ?? 0, currency)}</strong></div>`,
      `<div class="board-sum-cell"><span>浮动盈亏</span><strong class="${pnlClass(floating)}">${signedMoney(floating, currency)}</strong></div>`,
      `<div class="board-sum-cell"><span>总费用</span><strong>${money(bucket.fees ?? 0, currency)}</strong></div>`,
    ].join('') + '</div></div>';
  }).join('');
  const cards = data.cards || [];
  $('#board-cards').innerHTML = cards.length ? cards.map(base => boardCardHtml(mergedBoardCard(base))).join('') : '<div class="empty-state">还没有成交记录，记录第一笔交易后这里会显示每只股票的盈亏与趋势。</div>';
  state.boardGeom = {};
  $$('#board-cards .board-card').forEach(article => {
    const card = cards.find(item => item.security_id === article.dataset.sid);
    if (card) bindBoardCard(article, mergedBoardCard(card));
  });
}

function bindBoardCard(article, card) {
  const canvas = article.querySelector('.board-chart');
  if (!canvas) return;
  drawBoardChart(canvas, card);
  canvas.addEventListener('mousemove', event => boardChartHover(canvas, card, event));
  canvas.addEventListener('mouseleave', () => { const geom = state.boardGeom[card.security_id]; if (geom) { geom.hover = null; geom.hoverMark = null; } canvas.parentElement.querySelector('.chart-tip').classList.add('hidden'); drawBoardChart(canvas, card); });
  article.querySelectorAll('.board-card-ranges .board-range').forEach(button => button.addEventListener('click', async () => {
    const range = button.dataset.brange;
    if (state.boardCardRange[card.security_id] === range) return;
    state.boardCardRange[card.security_id] = range;
    try {
      await ensureBoardRange(range);
      const merged = mergedBoardCard(card);
      const temp = document.createElement('div');
      temp.innerHTML = boardCardHtml(merged);
      const next = temp.firstElementChild;
      article.replaceWith(next);
      bindBoardCard(next, merged);
    } catch (error) { toast(error.message, true); }
  }));
}

function renderBoardCharts() {
  const cards = state.board?.cards || [];
  $$('#board-cards .board-chart').forEach(canvas => {
    const base = cards.find(item => item.security_id === canvas.dataset.sid);
    if (base) drawBoardChart(canvas, mergedBoardCard(base));
  });
}

function signedPct(value) { return `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`; }

const BOARD_RANGE_LABELS = { month: '本月', '3m': '3 个月', '6m': '半年', '1y': '1 年', all: '全部' };
const EXCHANGE_LABELS = { SSE: '沪市', SZSE: '深市', BSE: '北交所', HKEX: '港交所' };
function exchangeLabel(value) { return EXCHANGE_LABELS[value] || value || ''; }
const CURRENCY_LABELS = { CNY: '人民币', HKD: '港币' };
function currencyLabel(value) { return CURRENCY_LABELS[value] || value || ''; }
const WATCH_STATUS_LABELS = { watching: '关注中', bought: '已建仓', reached: '已到目标价' };
const COST_METHOD_LABELS = { moving_average: '移动加权平均', fifo: '先进先出（FIFO）', diluted: '摊薄成本' };

function boardCardHtml(card) {
  const currency = card.currency || 'CNY';
  const floating = Number(card.unrealized ?? 0);
  const invested = Number(card.invested ?? 0);
  const ratio = invested > 0 ? floating / invested * 100 : null;
  const cardRange = card.range || state.boardRange;
  const rangeLabel = BOARD_RANGE_LABELS[cardRange] || '区间';
  const metrics = [
    ['市值', formatMarketCap(card.market_cap, currency), ''],
    ['累计投入', money(Number(card.invested ?? 0), currency), ''],
    ['已实现盈亏', signedMoney(Number(card.realized ?? 0), currency), pnlClass(Number(card.realized ?? 0))],
    ['累计费用', money(Number(card.fees ?? 0), currency), ''],
    [`${rangeLabel}涨跌`, card.range_change == null ? '--' : signedPct(Number(card.range_change)), card.range_change == null ? '' : pnlClass(Number(card.range_change))],
  ];
  const ranges = Object.entries(BOARD_RANGE_LABELS).map(([value, label]) => `<button class="board-range${value === cardRange ? ' active' : ''}" data-brange="${value}">${label}</button>`).join('');
  return `<article class="board-card" data-sid="${escapeAttr(card.security_id)}"><div class="board-card-head"><div class="board-card-id"><div class="board-name-row"><strong class="board-name">${escapeHtml(card.name)}</strong><span class="board-badge ${card.status}">${card.status === 'held' ? '持仓' : '已清仓'}</span></div><small class="board-code">${escapeHtml(card.code)}${card.exchange ? ` · ${escapeHtml(exchangeLabel(card.exchange))}` : ''}</small></div><div class="board-card-pnl ${pnlClass(floating)}"><strong>${signedMoney(floating, currency)}</strong>${ratio == null ? '' : `<small>${signedPct(ratio)}</small>`}</div></div><div class="board-metrics">${metrics.map(([label, value, klass]) => `<div><span>${label}</span><strong class="${klass}">${value}</strong></div>`).join('')}</div><div class="board-chart-head"><div class="range-tabs board-card-ranges">${ranges}</div></div><div class="board-chart-wrap"><canvas class="board-chart" role="img" aria-label="${escapeAttr(card.name)} ${escapeAttr(BOARD_RANGE_LABELS[card.range || state.boardRange] || '')}盈亏走势折线图，含买卖标记" data-sid="${escapeAttr(card.security_id)}"></canvas><div class="chart-tip hidden"></div>${(card.series || []).length < 2 ? '<div class="chart-empty">区间内无价格数据</div>' : ''}</div></article>`;
}

function drawBoardChart(canvas, card, options = {}) {
  const wrap = canvas.parentElement;
  const rect = wrap.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  const width = Math.max(280, Math.round(canvas.getBoundingClientRect().width || (rect.width - 16)));
  const height = options.height || 190;
  const costLabel = options.costLabel || card.cost_label || '成本';
  const costWithinRange = !!options.costWithinRange;
  canvas.width = width * ratio; canvas.height = height * ratio;
  canvas.style.height = `${height}px`;
  const ctx = canvas.getContext('2d');
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, width, height);
  const rawSeries = card.series || [];
  const marks = card.marks || [];
  if (rawSeries.length < 2) { state.boardGeom[card.security_id] = null; return; }
  const maxPoints = Math.max(80, Math.floor((width - 60) / 2));
  let series = rawSeries;
  if (rawSeries.length > maxPoints) {
    const step = (rawSeries.length - 1) / (maxPoints - 1);
    series = [];
    for (let i = 0; i < maxPoints - 1; i++) series.push(rawSeries[Math.round(i * step)]);
    series.push(rawSeries[rawSeries.length - 1]);
  }
  const rootStyle = getComputedStyle(document.documentElement);
  const token = name => rootStyle.getPropertyValue(name).trim();
  const prices = series.map(point => Number(point.p));
  const cost = card.cost_line == null ? null : Number(card.cost_line);
  const domain = [...prices, ...marks.map(mark => Number(mark.unit_price)), ...(cost == null || costWithinRange ? [] : [cost])].filter(Number.isFinite);
  const min = Math.min(...domain); const max = Math.max(...domain);
  const pad = Math.max((max - min) * 0.12, max * 0.01, 0.01);
  const low = min - pad; const high = max + pad;
  const costVisible = cost != null && (!costWithinRange || (cost > low && cost < high));
  const top = 22; const right = 12; const bottom = 26;
  const plotBottom = height - bottom;
  ctx.font = `10px ${token('font-family')}`;
  const ticks = [0, 1, 2].map(i => money(high - (high - low) * i / 2, card.currency));
  const left = Math.max(40, Math.ceil(Math.max(...ticks.map(label => ctx.measureText(label).width))) + 12);
  const x = index => left + (width - left - right) * (index / (series.length - 1));
  const y = value => top + (plotBottom - top) * (1 - (value - low) / (high - low));
  const xByDate = dateValue => {
    let index = series.findIndex(point => point.d >= dateValue);
    if (index < 0) index = series.length - 1;
    return x(index);
  };
  const geom = state.boardGeom[card.security_id] || (state.boardGeom[card.security_id] = {});
  geom.series = series; geom.marks = marks; geom.currency = card.currency; geom.width = width; geom.height = height; geom.left = left; geom.right = right;
  geom.points = prices.map((value, index) => [x(index), y(value)]);
  geom.markPoints = marks.map(mark => ({ mark, px: xByDate(mark.date), py: y(Number(mark.unit_price)) }));
  ctx.strokeStyle = token('--line'); ctx.lineWidth = 1; ctx.fillStyle = token('--muted-2'); ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
  ctx.setLineDash([2, 4]);
  for (let i = 0; i < 3; i++) { const yy = top + (plotBottom - top) * i / 2; ctx.beginPath(); ctx.moveTo(left, yy); ctx.lineTo(width - right, yy); ctx.stroke(); ctx.fillText(ticks[i], left - 6, yy); }
  ctx.setLineDash([]);
  const up = prices[prices.length - 1] >= prices[0];
  const lineColor = up ? token('--up') : token('--down');
  traceMonotone(ctx, geom.points);
  ctx.lineTo(x(series.length - 1), plotBottom); ctx.lineTo(x(0), plotBottom); ctx.closePath();
  ctx.globalAlpha = 0.12; ctx.fillStyle = lineColor; ctx.fill(); ctx.globalAlpha = 1;
  if (costVisible) {
    ctx.save(); ctx.strokeStyle = token('--muted-2'); ctx.setLineDash([4, 4]); ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(left, y(cost)); ctx.lineTo(width - right, y(cost)); ctx.stroke(); ctx.restore();
  }
  traceMonotone(ctx, geom.points); ctx.strokeStyle = lineColor; ctx.lineWidth = 2; ctx.lineJoin = 'round'; ctx.stroke();
  geom.markPoints.forEach(({ mark, px, py }) => {
    const color = mark.side === 'buy' ? token('--up') : token('--down');
    ctx.beginPath(); ctx.arc(px, py, 3.4, 0, Math.PI * 2);
    ctx.fillStyle = color; ctx.fill(); ctx.lineWidth = 1.5; ctx.strokeStyle = token('--panel'); ctx.stroke();
  });
  if (costVisible) {
    const costText = `${money(cost, card.currency)} ${costLabel}`;
    const costY = Math.min(Math.max(y(cost) - 9, top + 8), plotBottom - 8);
    ctx.fillStyle = token('--panel'); ctx.fillRect(left + 3, costY - 8, ctx.measureText(costText).width + 10, 16);
    ctx.fillStyle = token('--muted-2'); ctx.textAlign = 'left'; ctx.textBaseline = 'middle'; ctx.fillText(costText, left + 8, costY);
  }
  ctx.fillStyle = token('--muted-2'); ctx.textBaseline = 'top';
  [0, Math.floor((series.length - 1) / 2), series.length - 1].forEach((index, slot) => {
    const label = String(series[index].d || '').slice(5);
    if (!label) return;
    ctx.textAlign = slot === 0 ? 'left' : (slot === 2 ? 'right' : 'center');
    ctx.fillText(label, x(index), plotBottom + 6);
  });
  if (geom.hover != null && geom.points[geom.hover]) {
    const [hx, hy] = geom.points[geom.hover];
    ctx.save(); ctx.strokeStyle = token('--border-strong'); ctx.setLineDash([4, 4]); ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(hx, top); ctx.lineTo(hx, height - bottom); ctx.stroke(); ctx.restore();
    ctx.beginPath(); ctx.arc(hx, hy, 3.5, 0, Math.PI * 2); ctx.fillStyle = lineColor; ctx.fill();
  }
  if (geom.hoverMark != null) {
    const point = geom.markPoints[geom.hoverMark];
    if (point) { ctx.beginPath(); ctx.arc(point.px, point.py, 5, 0, Math.PI * 2); ctx.strokeStyle = token('--panel'); ctx.lineWidth = 2; ctx.stroke(); }
  }
}

function boardChartHover(canvas, card, event, options = {}) {
  const geom = state.boardGeom[card.security_id];
  if (!geom || !geom.points || geom.points.length < 2) return;
  const rect = canvas.getBoundingClientRect();
  const mx = event.clientX - rect.left; const my = event.clientY - rect.top;
  let markIndex = null; let best = 12;
  geom.markPoints.forEach((point, index) => { const distance = Math.hypot(point.px - mx, point.py - my); if (distance < best) { best = distance; markIndex = index; } });
  const plot = Math.max(geom.width - geom.left - geom.right, 1);
  const seriesIndex = Math.min(geom.series.length - 1, Math.max(0, Math.round((mx - geom.left) / plot * (geom.series.length - 1))));
  const changed = geom.hover !== (markIndex == null ? seriesIndex : null) || geom.hoverMark !== markIndex;
  geom.hover = markIndex == null ? seriesIndex : null;
  geom.hoverMark = markIndex;
  const tip = canvas.parentElement.querySelector('.chart-tip');
  const offX = canvas.offsetLeft; const offY = canvas.offsetTop;
  if (markIndex != null) {
    const { mark, px, py } = geom.markPoints[markIndex];
    tip.textContent = `${mark.date}　${mark.side === 'buy' ? '买入' : '卖出'} ${number(mark.quantity, 4)} 股 @ ${priceMoney(Number(mark.unit_price), geom.currency)} · 费用 ${money(Number(mark.fee ?? 0), geom.currency)}`;
    tip.style.left = `${Math.min(Math.max(px, 70), geom.width - 70) + offX}px`; tip.style.top = `${py + offY}px`;
  } else {
    const point = geom.series[seriesIndex];
    tip.textContent = `${point.d}　${money(Number(point.p), geom.currency)}`;
    tip.style.left = `${Math.min(Math.max(geom.points[seriesIndex][0], 52), geom.width - 52) + offX}px`; tip.style.top = `${geom.points[seriesIndex][1] + offY}px`;
  }
  tip.classList.remove('hidden');
  if (changed) drawBoardChart(canvas, card, options);
}

async function loadWatch() {
  const wrap = $('#watch-cards');
  if (!state.watch) wrap.innerHTML = '<div class="empty-state">正在加载关注…</div>';
  try {
    const base = await request(`/api/watch?range=${encodeURIComponent(state.watchRange)}`);
    state.watchCache = { [state.watchRange]: base };
    state.watch = base;
    renderWatch();
  } catch (error) {
    wrap.innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`;
  }
}

async function ensureWatchRange(range) {
  if (!state.watchCache[range]) state.watchCache[range] = await request(`/api/watch?range=${encodeURIComponent(range)}`);
  return state.watchCache[range];
}

function mergedWatchItem(baseItem) {
  const range = state.watchCardRange[baseItem.id] || state.watchRange;
  const payload = state.watchCache[range];
  const ranged = payload ? (payload.items || []).find(item => item.id === baseItem.id) : null;
  return Object.assign({}, baseItem, ranged ? { series: ranged.series } : {}, { range });
}

function watchCardHtml(item) {
  const currency = item.currency || 'CNY';
  const last = item.last_price == null ? null : Number(item.last_price);
  const target = item.target_price == null ? null : Number(item.target_price);
  const gap = item.gap_pct == null ? null : Number(item.gap_pct);
  const reached = item.status === 'watching' && item.reached === true;
  const bought = item.status === 'bought';
  const badge = `<span class="board-badge${reached && !bought ? ' held' : ''}">${WATCH_STATUS_LABELS[bought ? 'bought' : reached ? 'reached' : 'watching']}</span>`;
  const metrics = [
    ['最新价', last == null ? '--' : priceMoney(last, currency), ''],
    ['市值', formatMarketCap(item.market_cap, currency), ''],
    ['目标买入价', target == null ? '未设置' : priceMoney(target, currency), ''],
    ['距目标价', gap == null ? '--' : `${gap > 0 ? '高于目标' : gap < 0 ? '低于目标' : '等于目标'}${gap === 0 ? '' : ` ${Math.abs(gap).toFixed(2)}%`}`, ''],
  ];
  const note = item.latest_note;
  return `<article class="board-card watch-card${reached ? ' watch-reached' : ''}" data-wid="${escapeAttr(item.id)}"><div class="board-card-head"><div><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.code)} · ${escapeHtml(item.exchange || '')}</small>${badge}</div><button class="text-action watch-detail" data-id="${escapeAttr(item.id)}">跟进记录</button></div><div class="board-metrics">${metrics.map(([label, value, klass]) => `<div><span>${label}</span><strong class="${klass}">${value}</strong></div>`).join('')}</div><div class="watch-meta-row"><button type="button" class="text-action muted watch-notes-open" data-id="${escapeAttr(item.id)}">${note ? `跟进 ${number(item.notes_count ?? 0, 0)} 条 · 最近 ${formatDateTime(note.updated_at)}` : '还没有跟进分析'}</button><div class="range-tabs board-card-ranges">${Object.entries(BOARD_RANGE_LABELS).map(([value, label]) => `<button class="board-range${value === (item.range || state.watchRange) ? ' active' : ''}" data-brange="${value}">${label}</button>`).join('')}</div></div><div class="board-chart-wrap"><canvas class="board-chart" role="img" aria-label="${escapeAttr(item.name)} ${escapeAttr(BOARD_RANGE_LABELS[item.range || state.watchRange] || '')}价格与目标价走势折线图" data-wid="${escapeAttr(item.id)}"></canvas><div class="chart-tip hidden"></div>${(item.series || []).length < 2 ? '<div class="chart-empty">暂无价格数据</div>' : ''}</div><div class="watch-actions"><button class="secondary watch-note-add" data-id="${escapeAttr(item.id)}">写跟进</button><button class="secondary watch-edit" data-id="${escapeAttr(item.id)}">编辑目标价</button>${bought ? '' : `<button class="primary watch-buy" data-id="${escapeAttr(item.id)}">买入建仓</button>`}<button class="text-action danger watch-remove" data-id="${escapeAttr(item.id)}">移除</button></div></article>`;
}

function renderWatch() {
  const data = state.watch;
  if (!data) return;
  syncWatchPills();
  const totals = data.totals || {};
  $('#watch-totals').innerHTML = [
    `<div class="board-total"><span>关注中</span><strong>${number(totals.watching ?? 0, 0)}</strong></div>`,
    `<div class="board-total"><span>已到目标价</span><strong class="${Number(totals.reached ?? 0) > 0 ? 'positive' : ''}">${number(totals.reached ?? 0, 0)}</strong></div>`,
    `<div class="board-total"><span>已建仓</span><strong>${number(totals.bought ?? 0, 0)}</strong></div>`,
  ].join('');
  const all = data.items || [];
  const items = all.filter(item => state.watchMarket === 'all' || (state.watchMarket === 'hk' ? item.market === 'HK' : item.market === 'A')).filter(item => state.watchStatus === 'all' || item.status === state.watchStatus).map(mergedWatchItem);
  const emptyText = all.length ? '没有符合当前筛选的股票。' : state.watchStatus === 'bought' ? '还没有已建仓的股票，从关注列表买入建仓后会自动归档到这里。' : state.watchStatus === 'watching' ? '当前没有关注中的股票。' : '还没有关注的股票，点「添加关注」开始跟踪。';
  $('#watch-cards').innerHTML = items.length ? items.map(watchCardHtml).join('') : `<div class="empty-state">${emptyText}</div>`;
  $$('#watch-cards .board-chart').forEach(canvas => {
    const item = items.find(card => String(card.id) === canvas.dataset.wid);
    if (!item) return;
    const chartCard = { security_id: `watch-${item.id}`, currency: item.currency, series: item.series || [], marks: [], cost_line: item.target_price == null ? null : Number(item.target_price), cost_label: '目标价' };
    drawBoardChart(canvas, chartCard);
    canvas.addEventListener('mousemove', event => boardChartHover(canvas, chartCard, event));
    canvas.addEventListener('mouseleave', () => { const geom = state.boardGeom[chartCard.security_id]; if (geom) { geom.hover = null; geom.hoverMark = null; } canvas.parentElement.querySelector('.chart-tip').classList.add('hidden'); drawBoardChart(canvas, chartCard); });
  });
  $$('#watch-cards .board-card-ranges .board-range').forEach(button => button.addEventListener('click', async () => {
    const cardEl = button.closest('.board-card');
    const id = Number(cardEl.dataset.wid);
    const range = button.dataset.brange;
    if ((state.watchCardRange[id] || state.watchRange) === range) return;
    state.watchCardRange[id] = range;
    try { await ensureWatchRange(range); renderWatch(); } catch (error) { toast(error.message, true); }
  }));
  $$('#watch-cards .watch-detail').forEach(button => button.addEventListener('click', () => openWatchDetail(items.find(item => String(item.id) === button.dataset.id))));
  $$('#watch-cards .watch-notes-open').forEach(button => button.addEventListener('click', () => openWatchDetail(items.find(item => String(item.id) === button.dataset.id))));
  $$('#watch-cards .watch-note-add').forEach(button => button.addEventListener('click', () => { state.watchDetail = items.find(item => String(item.id) === button.dataset.id); openWatchNoteModal('create'); }));
  $$('#watch-cards .watch-edit').forEach(button => button.addEventListener('click', () => openWatchModal('edit', items.find(item => String(item.id) === button.dataset.id))));
  $$('#watch-cards .watch-buy').forEach(button => button.addEventListener('click', () => { const item = items.find(card => String(card.id) === button.dataset.id); gotoTradeWithSecurity({ tradeId: item.security_id, name: item.name, code: item.code, currency: item.currency, exchange: item.exchange, market: item.market }); }));
  $$('#watch-cards .watch-remove').forEach(button => button.addEventListener('click', () => removeWatch(button.dataset.id)));
}

async function removeWatch(id) {
  if (!await confirmDialog('移除后不再跟踪该股票，跟进记录一并隐藏。确认移除？', '移除关注')) return;
  try { await request(`/api/watch/${id}`, { method: 'DELETE' }); toast('已移除关注'); await loadWatch(); } catch (error) { toast(error.message, true); }
}

function searchWatchSecurities(query) {
  return searchSecuritiesInto({ listSel: '#watch-security-results', market: () => 'all', heldOnly: () => false, onPick: selectWatchSecurity }, query);
}

function selectWatchSecurity(security) {
  state.watchSecurity = security;
  renderSecurityChip('#watch-selected-security', security, clearWatchSecurity);
  $('#watch-search-block').classList.add('hidden');
  $('#watch-security-search').value = '';
  hideSecurityList('#watch-security-results');
  $('#watch-currency').textContent = currencyLabel(securityCurrency(security));
  syncWatchTargetHint();
  syncWatchSaveState();
}

function clearWatchSecurity() {
  state.watchSecurity = null;
  const chip = $('#watch-selected-security');
  chip.classList.add('hidden');
  chip.innerHTML = '';
  $('#watch-search-block').classList.remove('hidden');
  $('#watch-security-search').value = '';
  hideSecurityList('#watch-security-results');
  $('#watch-currency').textContent = '人民币';
  syncWatchTargetHint();
  syncWatchSaveState();
}

function syncWatchSaveState() {
  const save = $('#watch-save');
  if (save) save.disabled = !state.watchSecurity?.security_id;
}

function syncWatchTargetHint() {
  const el = $('#watch-target-hint');
  if (!el) return;
  const target = Number($('#watch-target').value || 0);
  if (!(target > 0)) { el.textContent = '留空则只跟踪、不设目标'; return; }
  const latest = state.watchSecurity?.last_price == null ? null : Number(state.watchSecurity.last_price);
  if (!(latest > 0)) { el.textContent = '已设目标价，暂无可比最新价'; return; }
  const gap = (latest - target) / target * 100;
  const currency = state.watchSecurity?.currency || 'CNY';
  el.textContent = `当前 ${priceMoney(latest, currency)} · ${gap > 0 ? '高于' : gap < 0 ? '低于' : '等于'}目标${gap === 0 ? '' : ` ${Math.abs(gap).toFixed(2)}%`}`;
}

function openWatchModal(mode, item = null) {
  state.watchMode = mode;
  state.watchEditId = item ? item.id : null;
  state.watchSecurity = item ? { security_id: item.security_id, id: item.security_id, name: item.name, code: item.code, exchange: item.exchange, currency: item.currency, market: item.market, last_price: item.last_price } : null;
  $('#watch-modal-title').textContent = mode === 'edit' ? '编辑关注' : '添加关注';
  $('#watch-step-security').classList.remove('hidden');
  $('#watch-search-block').classList.toggle('hidden', mode === 'edit' || !!state.watchSecurity);
  $('#watch-step-note').classList.toggle('hidden', mode === 'edit');
  const chip = $('#watch-selected-security');
  if (state.watchSecurity) {
    chip.classList.remove('hidden');
    chip.innerHTML = `${securityChipMeta(state.watchSecurity)}${mode === 'edit' ? '' : '<button type="button" class="text-action" id="watch-clear-security">更换</button>'}`;
    if (mode !== 'edit') $('#watch-clear-security').addEventListener('click', clearWatchSecurity);
  } else {
    chip.classList.add('hidden');
    chip.innerHTML = '';
  }
  $('#watch-target').value = item && item.target_price != null ? Number(item.target_price) : '';
  $('#watch-currency').textContent = currencyLabel(state.watchSecurity?.currency || 'CNY');
  $('#watch-editor').innerHTML = '';
  $('#watch-note-body').classList.add('hidden');
  $('#watch-note-toggle').textContent = '＋ 展开';
  $('#watch-security-search').value = '';
  $('#watch-security-results').classList.add('hidden');
  $('#watch-security-results').innerHTML = '';
  syncWatchTargetHint();
  syncWatchSaveState();
  openModal('#watch-modal');
  if (mode !== 'edit' && !state.watchSecurity) $('#watch-security-search').focus();
}

function closeWatchModal() { closeModal('#watch-modal'); }

async function saveWatch() {
  const targetRaw = $('#watch-target').value;
  const targetError = targetRaw.trim() === '' ? '' : positiveError(targetRaw, '目标股价');
  if (!applyValidation([[ $('#watch-target'), targetError]])) return;
  if (!state.watchSecurity?.security_id) { toast('请先选择股票', true); $('#watch-security-search').focus(); return; }
  const body = { security_id: state.watchSecurity.security_id, target_price: targetRaw.trim() === '' ? null : Number(targetRaw) };
  if (state.watchMode === 'create') body.content_html = $('#watch-editor').innerHTML;
  try {
    if (state.watchMode === 'edit') await request(`/api/watch/${state.watchEditId}`, { method: 'PUT', body });
    else await request('/api/watch', { method: 'POST', body });
    toast(state.watchMode === 'edit' ? '关注已更新' : '已添加关注');
    closeWatchModal();
    await loadWatch();
  } catch (error) { toast(error.message, true); }
}

async function openWatchDetail(item) {
  if (!item) return;
  state.watchDetail = item;
  $('#watch-detail-title').textContent = `跟进记录 · ${item.name}`;
  $('#watch-detail-meta').textContent = `${item.code} · ${exchangeLabel(item.exchange)} · 最新 ${item.last_price == null ? '--' : money(Number(item.last_price), item.currency)} · 目标 ${item.target_price == null ? '未设置' : money(Number(item.target_price), item.currency)}`;
  $('#watch-notes').innerHTML = '<div class="muted">加载中…</div>';
  openModal('#watch-detail-modal');
  try {
    await loadWatchNotes(false);
  } catch (error) {
    $('#watch-notes').innerHTML = `<div class="muted">${escapeHtml(error.message)}</div>`;
  }
}

async function loadWatchNotes(append = false) {
  const item = state.watchDetail;
  if (!item) return;
  if (!append) { state.watchNotes = []; state.notesPage.offset = 0; }
  const data = await request(`/api/watch/${item.id}/notes?limit=${state.notesPage.limit}&offset=${state.notesPage.offset}`);
  state.watchNotes = state.watchNotes.concat(asArray(data, ['notes', 'items']));
  state.notesPage.total = Number(data.total ?? state.watchNotes.length);
  state.notesPage.offset = state.watchNotes.length;
  renderWatchNotes(state.watchNotes);
}

function closeWatchDetail() { closeModal('#watch-detail-modal'); }

function renderWatchNotes(notes) {
  state.watchNotes = notes;
  $('#watch-notes').innerHTML = notes.length ? notes.map(note => `<article class="watch-note"><div class="watch-note-head"><small>更新于 ${formatDateTime(note.updated_at)}</small><div class="review-actions"><button class="text-action watch-note-view" data-id="${escapeAttr(note.id)}">查看</button><button class="text-action watch-note-edit" data-id="${escapeAttr(note.id)}">编辑</button><button class="text-action danger watch-note-delete" data-id="${escapeAttr(note.id)}">删除</button></div></div><p class="review-excerpt">${escapeHtml(note.content_text || '')}</p></article>`).join('') : '<div class="empty-state">还没有跟进记录，点「写跟进」写下第一条。</div>';
  $$('#watch-notes .watch-note-view').forEach(button => button.addEventListener('click', () => openWatchNoteModal('view', state.watchNotes.find(note => String(note.id) === button.dataset.id))));
  $$('#watch-notes .watch-note-edit').forEach(button => button.addEventListener('click', () => openWatchNoteModal('edit', state.watchNotes.find(note => String(note.id) === button.dataset.id))));
  if (state.watchNotes.length < state.notesPage.total) {
    $('#watch-notes').insertAdjacentHTML('beforeend', `<div class="list-footer"><button type="button" class="secondary" id="watch-notes-more">加载更多（已加载 ${state.watchNotes.length} / ${state.notesPage.total} 条）</button></div>`);
    $('#watch-notes-more').addEventListener('click', () => { loadWatchNotes(true).catch(error => toast(error.message, true)); });
  } else if (state.watchNotes.length) {
    $('#watch-notes').insertAdjacentHTML('beforeend', `<div class="list-footer muted center">共 ${state.notesPage.total} 条</div>`);
  }
  $$('#watch-notes .watch-note-delete').forEach(button => button.addEventListener('click', async () => {
    if (!await confirmDialog('删除后不可恢复，确认删除这条跟进？', '删除跟进')) return;
    try { await request(`/api/watch/${state.watchDetail.id}/notes/${button.dataset.id}`, { method: 'DELETE' }); toast('跟进已删除'); openWatchDetail(state.watchDetail); loadWatch(); } catch (error) { toast(error.message, true); }
  }));
}

function openWatchNoteModal(mode, note = null) {
  state.watchNoteMode = mode;
  state.watchNoteId = note ? note.id : null;
  state.watchNoteCurrent = note;
  const item = state.watchDetail;
  const viewing = mode === 'view';
  $('#watch-note-title').textContent = viewing ? '查看跟进' : mode === 'edit' ? '编辑跟进' : '写跟进';
  const latest = item.last_price == null ? null : Number(item.last_price);
  const gap = item.gap_pct == null ? null : Number(item.gap_pct);
  const meta = [exchangeLabel(item.exchange), latest ? `最新 ${priceMoney(latest, item.currency)}` : null, gap != null && latest ? `${gap > 0 ? '高于' : gap < 0 ? '低于' : '等于'}目标${gap === 0 ? '' : ` ${Math.abs(gap).toFixed(2)}%`}` : null].filter(Boolean).join(' · ');
  $('#watch-note-locked').innerHTML = `<div><strong>${escapeHtml(item.name)} ${escapeHtml(item.code)}</strong><small>${escapeHtml(meta)}</small></div><span class="watch-note-target">${item.target_price == null ? '未设目标价' : `目标 ${money(Number(item.target_price), item.currency)}`}</span>`;
  const editor = $('#watchnote-editor');
  editor.contentEditable = viewing ? 'false' : 'true';
  editor.classList.toggle('readonly', viewing);
  $('#watchnote-toolbar').classList.toggle('hidden', viewing);
  $('#watch-note-save').classList.toggle('hidden', viewing);
  $('#watch-note-edit').classList.toggle('hidden', !viewing);
  $('#watch-note-cancel').textContent = viewing ? '关闭' : '取消';
  editor.innerHTML = note ? note.content_html : '';
  syncWatchNoteSaveState();
  openModal('#watch-note-modal');
  if (!viewing) editor.focus();
}

function syncWatchNoteSaveState() {
  const editor = $('#watchnote-editor');
  const save = $('#watch-note-save');
  if (!editor || !save) return;
  save.disabled = !(editor.innerText || '').trim();
}

function closeWatchNoteModal() { closeModal('#watch-note-modal'); }

async function saveWatchNote() {
  const editor = $('#watchnote-editor');
  if (!editorError(editor, '跟进内容')) return;
  const item = state.watchDetail;
  try {
    if (state.watchNoteMode === 'edit') await request(`/api/watch/${item.id}/notes/${state.watchNoteId}`, { method: 'PUT', body: { content_html: editor.innerHTML } });
    else await request(`/api/watch/${item.id}/notes`, { method: 'POST', body: { content_html: editor.innerHTML } });
    toast(state.watchNoteMode === 'edit' ? '跟进已更新' : '跟进已保存');
    closeWatchNoteModal();
    openWatchDetail(item);
    loadWatch();
  } catch (error) { toast(error.message, true); }
}

async function submitTrade(event) {
  event.preventDefault();
  const formEl = event.currentTarget;
  const form = new FormData(formEl);
  const quantityRaw = form.get('quantity'); const priceRaw = form.get('unit_price');
  const price = Number(priceRaw); const quantity = Number(quantityRaw);
  const body = { security_id: form.get('security_id'), side: form.get('side'), quantity, unit_price: price, gross_amount: roundHalfUp3(price * quantity), trade_date: form.get('trade_date') };
  const held = state.selectedSecurity ? heldQuantity(state.selectedSecurity.security_id ?? state.selectedSecurity.id) : 0;
  const quantityError = positiveError(quantityRaw, '成交数量') || (body.side === 'sell' && quantity > held ? `卖出数量不能超过可卖 ${number(held, 4)}` : '');
  if (!applyValidation([
    [$('#trade-quantity'), quantityError],
    [$('#trade-price'), positiveError(priceRaw, '成交单价')],
    [$('#trade-date'), form.get('trade_date') ? '' : '请选择成交日期'],
  ])) return;
  if (!state.selectedSecurity || !body.security_id) { toast('请先选择股票', true); $('#security-search').focus(); return; }
  if (body.side === 'sell' && held <= 0) { toast('该股票当前没有持仓，不能卖出', true); return; }
  if (body.side === 'buy') {
    const currency = state.selectedSecurity.currency === 'HKD' ? 'HKD' : 'CNY';
    if (body.gross_amount + Number(state.tradeFeeTotal || 0) > Number(state.cash?.[currency] || 0)) { toast('可用金额不足，请先入金', true); return; }
  }
  try {
    const result = await request('/api/trades', { method: 'POST', body });
    toast('交易已保存');
    formEl.reset(); $('#trade-date').value = today; $('#trade-side').value = 'buy'; state.side = 'buy'; $$('.direction').forEach(button => button.classList.toggle('active', button.dataset.side === 'buy')); clearSecurity();
    await loadDashboard();
    navigate('overview');
    if (result?.plan_candidates?.length) openPlanLinkModal(result.plan_candidates);
  } catch (error) { toast(error.message, true); }
}

async function deleteTrade(id) { if (!await confirmDialog('作废后这笔交易不再计入持仓与盈亏，原始记录保留。确认作废？', '作废交易')) return; try { await request(`/api/trades/${encodeURIComponent(id)}`, { method: 'DELETE' }); toast('交易已作废'); await loadDashboard(); if (!$('#history-modal').classList.contains('hidden')) await loadHistory(); } catch (error) { toast(error.message, true); } }

const TDIR_LABELS = { buy_first: '先买后卖', sell_first: '先卖后买' };

function searchTplanSecurities(query) {
  return searchSecuritiesInto({ listSel: '#tplan-security-results', market: () => 'all', heldOnly: () => true, onPick: selectTplanSecurity }, query);
}

function pickTplanHolding(item) {
  selectTplanSecurity({ security_id: item.security_id, name: item.name, code: item.code, exchange: item.exchange, currency: item.currency, market: item.market });
  $('#tplan-quantity').value = item.quantity;
  $('#tplan-known-price').value = item.average_cost;
  updateTplanAmount();
}

function selectTplanSecurity(security) {
  if (heldQuantity(security.security_id ?? security.id) <= 0) { toast('该股票当前没有持仓，不能做T', true); return; }
  state.tplanSecurity = security;
  $('#tplan-currency').textContent = currencyLabel(renderSecurityChip('#tplan-selected-security', security, clearTplanSecurity));
  $('#tplan-security-search').classList.add('hidden');
  $('#tplan-security-search').value = '';
  const holding = (state.holdings || []).find(item => String(item.security_id ?? item.id) === String(security.security_id ?? security.id));
  const meta = $('#tplan-selected-security small');
  if (meta && holding) meta.textContent = `${meta.textContent} · 持仓 ${number(Number(holding.quantity), 4)} 股 · 成本 ${money(Number(holding.average_cost), securityCurrency(security))}`;
  hideSecurityList('#tplan-security-results');
  syncTplanTargetUI();
  updateTplanAmount();
  markTplanStale();
}

function clearTplanSecurity() {
  state.tplanSecurity = null;
  const el = $('#tplan-selected-security');
  el.classList.add('hidden');
  el.innerHTML = '';
  $('#tplan-security-search').classList.remove('hidden');
  $('#tplan-security-search').value = '';
  $('#tplan-security-results').classList.add('hidden');
  $('#tplan-security-results').innerHTML = '';
  $('#tplan-currency').textContent = '人民币';
  syncTplanTargetUI();
  updateTplanAmount();
  markTplanStale();
}

let tplanCalcToken = 0;

function tplanCurrency() { return state.tplanSecurity?.currency || 'CNY'; }

function tplanResultEmpty(message = '填写左侧参数后点「计算目标交易」') {
  return `<div class="tplan-rhead"><h3>测算结果</h3><span class="tplan-badge">待计算</span></div><div class="tplan-empty">${escapeHtml(message)}</div>`;
}

function updateTplanAmount() {
  const el = $('#tplan-amount');
  if (!el) return;
  const qty = Number($('#tplan-quantity')?.value || 0);
  const price = Number($('#tplan-known-price')?.value || 0);
  el.innerHTML = qty > 0 && price > 0 ? `成交金额 <strong>${money(roundHalfUp3(qty * price), tplanCurrency())}</strong>（不含费用）` : '成交金额 --（不含费用）';
}

function markTplanStale() {
  tplanCalcToken += 1;
  const save = $('#tplan-save');
  if (!save || save.disabled) return;
  state.tplanLastBody = null;
  save.disabled = true;
  const result = $('#tplan-result');
  result.classList.add('stale');
  if (!$('#tplan-stale')) result.insertAdjacentHTML('afterbegin', '<div id="tplan-stale" class="tplan-stale">参数已修改，请重新计算</div>');
}

function openTplanModal(prefill) {
  state.tplanEditId = null;
  state.tplanDirection = prefill?.direction || 'buy_first';
  state.tplanLastBody = null;
  $('#tplan-modal .modal-body').classList.remove('editing');
  $('#tplan-modal-title').textContent = '做T测算';
  $('#tplan-edit-context').classList.add('hidden');
  $$('#tplan-directions button').forEach(button => { button.classList.toggle('active', button.dataset.tdir === state.tplanDirection); button.classList.toggle('buy', button.dataset.tdir === 'buy_first'); button.classList.toggle('sell', button.dataset.tdir === 'sell_first'); });
  clearTplanSecurity();
  $('#tplan-quantity').value = '';
  $('#tplan-known-price').value = '';
  $('#tplan-target-type').value = 'percent';
  $('#tplan-target-value').value = '';
  syncTplanTargetUI();
  updateTplanAmount();
  $('#tplan-result').classList.remove('stale');
  $('#tplan-result').innerHTML = tplanResultEmpty();
  if (prefill?.holding) {
    const item = prefill.holding;
    selectTplanSecurity({ security_id: item.security_id, name: item.name, code: item.code, exchange: item.exchange, currency: item.currency, market: item.market });
    $('#tplan-quantity').value = item.quantity;
    $('#tplan-known-price').value = item.average_cost;
    updateTplanAmount();
  }
  openModal('#tplan-modal');
}

function closeTplanModal() { closeModal('#tplan-modal'); }

function openTplanEdit(plan) {
  if (!plan || plan.status !== 'open') return;
  const qty = Number(plan.quantity);
  state.tplanEditId = plan.id;
  state.tplanDirection = plan.direction;
  state.tplanLastBody = null;
  state.tplanSecurity = { security_id: plan.security_id, name: plan.name, code: plan.code, exchange: plan.exchange, currency: plan.currency, market: plan.market };
  $('#tplan-modal .modal-body').classList.add('editing');
  $('#tplan-modal-title').textContent = '修改做T测算';
  const ctx = $('#tplan-edit-context');
  ctx.innerHTML = `<strong>${escapeHtml(plan.name || '-')}</strong><span>${escapeHtml(plan.code || '')} · ${escapeHtml(TDIR_LABELS[plan.direction] || plan.direction)}</span><small>股票与方向不可修改</small>`;
  ctx.classList.remove('hidden');
  $('#tplan-quantity').value = qty;
  $('#tplan-known-price').value = qty ? tplanPriceValue(Number(plan.known_gross) / qty, plan.currency || 'CNY') : '';
  $('#tplan-target-type').value = plan.target_type === 'amount' ? 'amount' : 'percent';
  $('#tplan-target-value').value = Number(plan.target_value);
  $('#tplan-currency').textContent = currencyLabel(plan.currency || 'CNY');
  syncTplanTargetUI();
  updateTplanAmount();
  $('#tplan-result').classList.remove('stale');
  $('#tplan-result').innerHTML = tplanResultEmpty('修改参数后点「计算目标交易」');
  openModal('#tplan-modal');
}

function percentError(value) {
  const text = String(value ?? '').trim();
  if (!text) return '请填写比率';
  const parsed = Number(text);
  if (!Number.isFinite(parsed) || parsed <= -100) return '比率必须大于 -100%';
  return '';
}

function tplanOtherLabel() { return state.tplanDirection === 'buy_first' ? '卖出价' : '回补价'; }

function syncTplanTargetUI() {
  const other = tplanOtherLabel();
  const type = $('#tplan-target-type').value;
  $('#tplan-target-title').textContent = other;
  $$('#tplan-target-modes button').forEach(button => button.classList.toggle('active', button.dataset.tt === type));
  const suffix = $('#tplan-target-suffix');
  if (type === 'percent') {
    $('#tplan-target-label').textContent = '比率 %';
    $('#tplan-target-value').placeholder = '例如 15';
    if (suffix) suffix.textContent = '%';
    $('#tplan-target-hint').textContent = state.tplanDirection === 'buy_first'
      ? '卖出价 = 已知买价 × (1 + 比率%)，比率即想赚的价差点数；实际按最小价位取整后计算。'
      : '回补价 = 已知卖价 × (1 − 比率%)，比率即想赚的价差点数；实际按最小价位取整后计算。';
  } else {
    $('#tplan-target-label').textContent = `${other}（每股）`;
    $('#tplan-target-value').placeholder = '例如 24.95';
    if (suffix) suffix.textContent = currencyLabel(tplanCurrency());
    $('#tplan-target-hint').textContent = `按你输入的${other}计算费用、净收益与成本变化。`;
  }
}

async function calcTplan() {
  const quantity = Number($('#tplan-quantity').value);
  const knownPrice = Number($('#tplan-known-price').value);
  const targetValue = Number($('#tplan-target-value').value);
  if (!applyValidation([
    [$('#tplan-quantity'), positiveError($('#tplan-quantity').value, '数量')],
    [$('#tplan-known-price'), positiveError($('#tplan-known-price').value, '成交单价')],
    [$('#tplan-target-value'), $('#tplan-target-type').value === 'percent' ? percentError($('#tplan-target-value').value) : positiveError($('#tplan-target-value').value, `${tplanOtherLabel()}（每股）`)],
  ])) return;
  if (!state.tplanSecurity?.security_id) { toast('请先选择股票', true); $('#tplan-security-search').focus(); return; }
  const knownGross = roundHalfUp3(quantity * knownPrice);
  const body = { security_id: state.tplanSecurity.security_id, direction: state.tplanDirection, quantity, known_gross: knownGross, target_type: $('#tplan-target-type').value, target_value: targetValue, ...(state.tplanEditId ? { plan_id: state.tplanEditId } : {}) };
  const button = $('#tplan-calc');
  const token = ++tplanCalcToken;
  button.disabled = true;
  button.textContent = '计算中…';
  try {
    const data = await request('/api/t-plans/solve', { method: 'POST', body: { ...body, preview: true } });
    if (token !== tplanCalcToken) return;
    state.tplanLastBody = body;
    renderTplanResult(data.plan);
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = '计算目标交易';
  }
}

function renderTplanResult(p) {
  const currency = tplanCurrency();
  const otherLabel = p.direction === 'buy_first' ? '卖出' : '回补';
  const otherPriceLabel = p.direction === 'buy_first' ? '卖出价' : '回补价';
  const costMoney = value => {
    const n = Number(value);
    return `${n < 0 ? '<span class="cost-sign">−</span>' : ''}${absMoney(roundHalfUp3(Math.abs(n)), currency)}`;
  };
  const feeLines = (lines, title) => `<div class="fee-detail"><h4>${title}</h4>${lines.map(l => `<div class="fee-line"><span>${escapeHtml(l.name)}</span><span>${money(Number(l.amount), currency)}</span></div>`).join('') || '<div class="fee-line"><span>无</span><span>0</span></div>'}</div>`;
  const spread = p.direction === 'buy_first' ? Number(p.solved_gross) - Number(p.known_gross) : Number(p.known_gross) - Number(p.solved_gross);
  const costBlock = p.cost_after && p.cost_after.unit_after != null
    ? `<div class="tplan-costcompare"><div class="tplan-cell"><span>当前每股成本</span><strong class="cost-value">${p.cost_after.unit_before == null ? '--' : costMoney(p.cost_after.unit_before)}</strong></div><span class="cost-arrow" aria-hidden="true">→</span><div class="tplan-cell"><span>完成做T后</span><strong class="cost-value">${costMoney(p.cost_after.unit_after)}</strong></div></div>`
    : '';
  const result = $('#tplan-result');
  result.classList.remove('stale');
  result.innerHTML = `<div class="tplan-rhead"><h3>测算结果</h3><span class="tplan-badge ${p.direction === 'buy_first' ? 'buy' : 'sell'}">${TDIR_LABELS[p.direction] || ''}</span></div><div class="tplan-rprice"><span>${otherPriceLabel}（每股）</span><strong>${priceMoney(Number(p.solved_unit_price), currency)}</strong><small>${otherLabel}成交金额 ${money(Number(p.solved_gross), currency)} · 已叠加全部费用</small></div><div class="tplan-rgrid"><div><span>净收益</span><strong class="${pnlClass(Number(p.target_amount))}">${signedMoney(Number(p.target_amount), currency)}</strong></div><div><span>净收益率</span><strong class="${pnlClass(Number(p.target_amount))}">${Number(p.base) ? signedPct(Number(p.target_amount) / Number(p.base) * 100) : '--'}</strong></div><div><span>已知交易费用</span><strong>${money(Number(p.known_fees), currency)}</strong></div><div><span>${otherLabel}交易费用</span><strong>${money(Number(p.solved_fees), currency)}</strong></div><div><span>总交易费用</span><strong>${money(Number(p.known_fees) + Number(p.solved_fees), currency)}</strong></div><div><span>往返费用率</span><strong>${(Number(p.round_trip_fee_rate) * 100).toFixed(2)}%</strong></div></div><p class="tplan-bridge">卖出价差 ${money(spread, currency)} = 净收益 ${money(Number(p.target_amount), currency)} + 往返费用 ${money(Number(p.known_fees) + Number(p.solved_fees), currency)}</p>${costBlock}<details class="tplan-fee-details"><summary>费用明细</summary>${feeLines(p.known_lines, `${p.direction === 'buy_first' ? '买入' : '卖出'}交易费用`)}${feeLines(p.other_lines, `${otherLabel}交易费用`)}</details><button class="primary tplan-save" id="tplan-save" type="button">${state.tplanEditId ? '保存修改' : '记录做T测算'}</button>`;
  $('#tplan-save').addEventListener('click', saveTplan);
}

async function saveTplan() {
  if (!state.tplanLastBody) return;
  try {
    if (state.tplanEditId) {
      const data = await request(`/api/t-plans/${encodeURIComponent(state.tplanEditId)}/edit`, { method: 'POST', body: state.tplanLastBody });
      toast(data?.unlinked ? '已保存修改，数量变化，关联的成交已解除' : '做T测算已修改');
    } else {
      await request('/api/t-plans/solve', { method: 'POST', body: state.tplanLastBody });
      toast('做T测算已记录');
    }
    closeTplanModal();
    if (!$('#tplans-modal').classList.contains('hidden')) loadTplans();
  } catch (error) { toast(error.message, true); }
}

async function loadTplans() {
  try {
    const data = await request('/api/t-plans');
    state.tplans = asArray(data, ['plans', 'items']);
    renderTplans();
  } catch (error) {
    toast(error.message, true);
    $('#tplan-list').innerHTML = `<div class="empty-state">暂时无法加载做T记录。${escapeHtml(error.message)}</div>`;
  }
}

function renderTplans() {
  const list = state.tplans || [];
  const open = list.filter(p => p.status === 'open');
  const closed = list.filter(p => p.status === 'closed');
  const executed = closed.filter(p => Number(p.executed) === 1);
  const hit = executed.filter(p => Number(p.actual_net) >= Number(p.plan_net));
  const pct = (a, b) => (b ? `${Math.round(a / b * 100)}%` : '--');
  const sums = { CNY: { actual: 0, plan: 0 }, HKD: { actual: 0, plan: 0 } };
  executed.forEach(p => { const currency = p.currency === 'HKD' ? 'HKD' : 'CNY'; sums[currency].actual += Number(p.actual_net || 0); sums[currency].plan += Number(p.plan_net || 0); });
  const curCell = currency => {
    const deltaText = signedMoney(sums[currency].actual - sums[currency].plan, currency);
    return `<div class="tplan-cur"><span>${currency === 'HKD' ? '港币' : '人民币'} <em>${currency}</em></span><strong class="${pnlClass(sums[currency].actual)}">${signedMoney(sums[currency].actual, currency)}</strong><small>GAP值：<span class="${pnlClass(sums[currency].actual - sums[currency].plan)}">${deltaText}</span></small></div>`;
  };
  $('#tplan-stats').innerHTML = `<div class="tplan-stat"><span>计划总数</span><strong>${list.length}</strong></div><div class="tplan-stat"><span>待回填</span><strong>${open.length}</strong></div><div class="tplan-stat"><span>已回填</span><strong>${closed.length}</strong></div><div class="tplan-stat"><span>执行率</span><strong>${pct(executed.length, list.length)}</strong></div><div class="tplan-stat"><span>达标率</span><strong>${pct(hit.length, executed.length)}</strong></div>${curCell('CNY')}${curCell('HKD')}`;
  const counts = { all: list.length, open: open.length, closed: closed.length };
  const labels = { all: '全部', open: '待回填', closed: '已回填' };
  $$('#tplan-status-tabs button').forEach(button => {
    const status = button.dataset.tstatus;
    button.classList.toggle('active', status === state.tplanStatus);
    button.textContent = `${labels[status]} ${counts[status] ?? 0}`;
  });
  const keyword = (state.tplanSearch || '').toLowerCase();
  const rows = list.filter(plan => {
    if (state.tplanStatus === 'open' && plan.status !== 'open') return false;
    if (state.tplanStatus === 'closed' && plan.status !== 'closed') return false;
    if (keyword) {
      const text = `${plan.name || ''} ${plan.code || ''}`.toLowerCase();
      if (!text.includes(keyword)) return false;
    }
    return true;
  });
  const filtered = !!keyword || state.tplanStatus !== 'all';
  $('#tplan-list').innerHTML = rows.length ? rows.map(planCard).join('') : `<div class="empty-state">${filtered ? '没有符合筛选的做T记录。' : '还没有做T记录，测算后点「记录做T测算」留档。'}</div>`;
  $$('#tplan-list .tplan-rec').forEach(card => {
    const plan = rows.find(item => String(item.id) === card.dataset.id);
    if (!plan) return;
    card.addEventListener('click', event => { if (event.target.closest('button')) return; openTplanView(plan); });
  });
  $$('.plan-view').forEach(button => button.addEventListener('click', () => openTplanView(state.tplans.find(p => String(p.id) === String(button.dataset.id)))));
  $$('.plan-edit').forEach(button => button.addEventListener('click', () => openTplanEdit(state.tplans.find(p => String(p.id) === String(button.dataset.id)))));
  $$('.plan-feedback').forEach(button => button.addEventListener('click', () => openFeedback(state.tplans.find(p => String(p.id) === String(button.dataset.id)))));
  $$('.plan-delete').forEach(button => button.addEventListener('click', () => deleteTplan(button.dataset.id)));
}

async function deleteTplan(id) {
  if (!await confirmDialog('删除后不可恢复，确认删除这条做T记录？', '删除做T记录')) return;
  try { await request(`/api/t-plans/${encodeURIComponent(id)}`, { method: 'DELETE' }); toast('做T记录已删除'); loadTplans(); } catch (error) { toast(error.message, true); }
}

function planBadge(p) {
  const links = p.links || [];
  if (p.status === 'open') return links.length ? '<span class="badge open">进行中</span>' : '<span class="badge open">待回填</span>';
  if (Number(p.executed) !== 1) return '<span class="badge">未执行</span>';
  const isHit = Number(p.actual_net) >= Number(p.plan_net);
  return `<span class="badge ${isHit ? 'hit' : 'miss'}">${isHit ? '达标' : '未达标'}</span>`;
}

function planCard(p) {
  const currency = p.currency || 'CNY';
  const buyFirst = p.direction === 'buy_first';
  const executed = Number(p.executed) === 1;
  const qty = Number(p.quantity);
  const knownUnit = qty ? Number(p.known_gross) / qty : 0;
  const net = Number(p.target_amount);
  const base = Number(p.base);
  const knownLabel = buyFirst ? '已知买价' : '已知卖价';
  const otherLabel = buyFirst ? '卖出价' : '回补价';
  const amountLabel = buyFirst ? '卖出成交金额' : '回补成交金额';
  const cost = p.live_unit_cost == null ? null : Number(p.live_unit_cost);
  const costAfter = p.live_cost_after == null ? null : Number(p.live_cost_after);
  const actual = p.status === 'closed' && executed;
  const priceLine = `<div class="tplan-rec-line"><span>${knownLabel}</span><strong>${priceMoney(knownUnit, currency)}</strong><span class="arrow">→</span><span>${otherLabel}</span><strong>${priceMoney(Number(p.solved_unit_price), currency)}</strong><span class="sep">·</span><span>${amountLabel}</span><strong>${money(Number(p.solved_gross), currency)}</strong>${actual ? `<span class="sep">·</span><span>实际成交价</span><strong>${priceMoney(Number(p.actual_unit_price), currency)}</strong>` : ''}</div>`;
  const metricLine = actual
    ? `<div class="tplan-rec-line"><span>实际净收益</span><strong class="${pnlClass(Number(p.actual_net))}">${signedMoney(Number(p.actual_net), currency)}</strong><span class="sep">/</span><span>目标</span><strong class="${pnlClass(Number(p.plan_net))}">${money(Number(p.plan_net), currency)}</strong><span class="sep">·</span><span>净收益率</span><strong class="${pnlClass(Number(p.actual_net))}">${base ? signedPct(Number(p.actual_net) / base * 100) : '--'}</strong><span class="sep">·</span><span>往返费用率</span><strong>${(Number(p.round_trip_fee_rate) * 100).toFixed(2)}%</strong>${cost != null ? `<span class="sep">·</span><span>当前每股成本</span><strong>${money(cost, currency)}</strong>` : ''}</div>`
    : `<div class="tplan-rec-line"><span>净收益</span><strong class="${pnlClass(net)}">${signedMoney(net, currency)}</strong><span class="sub ${pnlClass(net)}">${base ? signedPct(net / base * 100) : '--'}</span><span class="sep">·</span><span>往返费用率</span><strong>${(Number(p.round_trip_fee_rate) * 100).toFixed(2)}%</strong>${cost != null ? `<span class="sep">·</span><span>成本</span><strong>${money(cost, currency)}</strong>${costAfter != null ? `<span class="arrow">→</span><strong>${money(costAfter, currency)}</strong>` : ''}` : ''}</div>`;
  return `<article class="tplan-rec" data-id="${escapeAttr(p.id)}"><div class="tplan-rec-head"><div><span class="tplan-rec-name">${escapeHtml(p.name || '-')}</span><span class="tplan-rec-meta">${escapeHtml(p.code || '')} · ${escapeHtml(TDIR_LABELS[p.direction] || p.direction)} · 数量 ${number(qty, 4)}</span></div>${planBadge(p)}</div>${priceLine}${metricLine}<div class="tplan-rec-foot"><small>创建于 ${formatDateTime(p.created_at)}</small><div class="review-actions"><button class="text-action plan-view" data-id="${escapeAttr(p.id)}" type="button">查看</button>${p.status === 'open' ? `<button class="text-action plan-edit" data-id="${escapeAttr(p.id)}" type="button">编辑</button><button class="text-action plan-feedback" data-id="${escapeAttr(p.id)}" type="button">回填执行</button>` : ''}<button class="text-action danger plan-delete" data-id="${escapeAttr(p.id)}" type="button">删除</button></div></div></article>`;
}

function openTplansModal() { state.tplanStatus = 'all'; state.tplanSearch = ''; $('#tplan-search').value = ''; openModal('#tplans-modal'); loadTplans(); }
function closeTplansModal() { closeModal('#tplans-modal'); }

function openTplanView(plan) {
  if (!plan) return;
  const currency = plan.currency || 'CNY';
  const qty = Number(plan.quantity);
  const knownUnit = qty ? Number(plan.known_gross) / qty : 0;
  const buyFirst = plan.direction === 'buy_first';
  const knownSide = buyFirst ? '买入' : '卖出';
  const otherSide = buyFirst ? '卖出' : '回补';
  const net = Number(plan.target_amount);
  const base = Number(plan.base);
  const targetUnit = Number(plan.solved_unit_price);
  const effectivePct = knownUnit ? (buyFirst ? (targetUnit / knownUnit - 1) : (1 - targetUnit / knownUnit)) * 100 : null;
  const effectiveText = effectivePct == null ? '--' : `${effectivePct >= 0 ? '+' : ''}${effectivePct.toFixed(2)}%`;
  const executed = Number(plan.executed) === 1;
  const links = plan.links || [];
  const totalFees = Number(plan.known_fees) + Number(plan.solved_fees);
  const spread = buyFirst ? Number(plan.solved_gross) - Number(plan.known_gross) : Number(plan.known_gross) - Number(plan.solved_gross);
  const feeLines = lines => (lines && lines.length)
    ? lines.map(line => `<div class="tplan-fee-line"><span>${escapeHtml(line.name)}<small>${Number(line.amount) === 0 ? '未收取' : escapeHtml(line.formula || '')}</small></span><span>${money(Number(line.amount), currency)}</span></div>`).join('')
    : '<div class="tplan-fee-line"><span>无费用</span><span>--</span></div>';
  const legRow = (side, unit, quantity, gross, fees, key, lines) => `<tr><td><span class="tplan-side">${side}</span></td><td>${priceMoney(unit, currency)}</td><td>${number(quantity, 4)}</td><td>${money(gross, currency)}</td><td>${money(fees, currency)}<button class="text-action compact tplan-fee-toggle" type="button" data-fee="${key}">明细</button></td></tr><tr class="tplan-fee-row hidden" data-fee-row="${key}"><td colspan="5"><div class="tplan-fee-lines">${feeLines(lines)}</div></td></tr>`;
  const legsTable = `<table class="tplan-legs"><thead><tr><th>方向</th><th>单价</th><th>数量</th><th>成交金额</th><th>费用</th></tr></thead><tbody>${legRow(knownSide, knownUnit, qty, Number(plan.known_gross), Number(plan.known_fees), 'known', plan.known_fee_lines)}${legRow(otherSide, targetUnit, qty, Number(plan.solved_gross), Number(plan.solved_fees), 'target', plan.solved_fee_lines)}</tbody><tfoot><tr><td colspan="4">合计费用</td><td>${money(totalFees, currency)}</td></tr></tfoot></table>`;
  const costHtml = (() => {
    if (plan.status === 'closed' && plan.executed_cost_before != null && plan.executed_cost_after != null) {
      return `<div class="tplan-costcompare"><div class="tplan-cell"><span>执行前每股成本</span><strong class="cost-value">${money(Number(plan.executed_cost_before), currency)}</strong></div><span class="cost-arrow" aria-hidden="true">→</span><div class="tplan-cell"><span>执行后每股成本</span><strong class="cost-value">${money(Number(plan.executed_cost_after), currency)}</strong></div></div>`;
    }
    if (plan.live_unit_cost != null && plan.live_cost_after != null) {
      return `<div class="tplan-costcompare"><div class="tplan-cell"><span>当前每股成本</span><strong class="cost-value">${money(Number(plan.live_unit_cost), currency)}</strong></div><span class="cost-arrow" aria-hidden="true">→</span><div class="tplan-cell"><span>完成做T后每股成本</span><strong class="cost-value">${money(Number(plan.live_cost_after), currency)}</strong></div></div>`;
    }
    if (plan.live_unit_cost != null) return `<p class="tplan-bridge">当前每股成本 ${money(Number(plan.live_unit_cost), currency)}</p>`;
    return '';
  })();
  const compareHtml = (plan.status === 'closed' && executed) ? (() => {
    const plannedPrice = targetUnit;
    const actualPrice = Number(plan.actual_unit_price);
    const priceDelta = actualPrice - plannedPrice;
    const pricePct = plannedPrice ? priceDelta / plannedPrice * 100 : 0;
    const priceDeltaText = `${priceDelta >= 0 ? '+' : '-'}${priceMoney(Math.abs(priceDelta), currency)}（${pricePct >= 0 ? '+' : ''}${pricePct.toFixed(2)}%）`;
    const plannedTargetFee = Number(plan.solved_fees);
    const actualTargetFee = Number(plan.actual_fees ?? 0);
    const plannedKnownFee = Number(plan.known_fees);
    const knownLink = links.find(item => item.leg === 'known');
    const actualKnownFee = knownLink ? Number(knownLink.fee_total ?? 0) : plannedKnownFee;
    const plannedTotalFee = plannedKnownFee + plannedTargetFee;
    const actualTotalFee = actualKnownFee + actualTargetFee;
    const row = (label, planned, actual, delta, cls = '') => `<tr><td>${label}</td><td>${planned}</td><td>${actual}</td><td class="${cls}">${delta}</td></tr>`;
    return `<section class="tplan-view-section"><h3>计划 vs 实际</h3><div class="tplan-table-scroll"><table class="tplan-legs"><thead><tr><th>项目</th><th>计划</th><th>实际</th><th>差额</th></tr></thead><tbody>${row('成交价', priceMoney(plannedPrice, currency), priceMoney(actualPrice, currency), priceDeltaText)}${row(`${knownSide}费用`, money(plannedKnownFee, currency), money(actualKnownFee, currency), signedMoney(actualKnownFee - plannedKnownFee, currency))}${row(`${otherSide}费用`, money(plannedTargetFee, currency), money(actualTargetFee, currency), signedMoney(actualTargetFee - plannedTargetFee, currency))}${row('合计费用', money(plannedTotalFee, currency), money(actualTotalFee, currency), signedMoney(actualTotalFee - plannedTotalFee, currency))}${row('净收益', money(Number(plan.plan_net), currency), money(Number(plan.actual_net), currency), signedMoney(Number(plan.actual_net) - Number(plan.plan_net), currency), pnlClass(Number(plan.actual_net) - Number(plan.plan_net)))}</tbody></table></div></section>`;
  })() : '';
  const linkChip = leg => {
    const link = links.find(item => item.leg === leg);
    const side = leg === 'known' ? knownSide : (buyFirst ? '卖出' : '买入');
    return `<div class="tplan-link-chip"><span>${side}${link ? ` · ${escapeHtml(link.trade_date || '-')}` : ''}</span><strong>${link ? `${number(Number(link.quantity), 4)} 股 @ ${priceMoney(Number(link.unit_price), currency)}` : '未关联成交'}</strong></div>`;
  };
  $('#tplan-view-meta').textContent = `${plan.name || '-'} ${plan.code || ''} · ${TDIR_LABELS[plan.direction] || plan.direction} · 数量 ${number(qty, 4)}`;
  $('#tplan-view-body').innerHTML = `<div class="tplan-view-top"><small class="muted">创建于 ${formatDateTime(plan.created_at)}${plan.closed_at ? ` · 完成于 ${formatDateTime(plan.closed_at)}` : ''}</small>${planBadge(plan)}</div><div class="tplan-kpis"><div class="tplan-grid"><div class="tplan-cell"><span>净收益</span><strong class="tplan-kpi-main ${pnlClass(net)}">${money(net, currency)}</strong></div><div class="tplan-cell"><span>净收益率</span><strong class="${pnlClass(net)}">${base ? signedPct(net / base * 100) : '--'}</strong></div><div class="tplan-cell"><span>目标价差</span><strong>${effectiveText}</strong></div><div class="tplan-cell"><span>往返费用率</span><strong>${(Number(plan.round_trip_fee_rate) * 100).toFixed(2)}%</strong></div></div></div><p class="tplan-bridge">目标价差 ${effectiveText} · 价差 ${money(spread, currency)} = 净收益 ${money(net, currency)} + 往返费用 ${money(totalFees, currency)}</p>${costHtml}<section class="tplan-view-section"><h3>两笔交易<em>费用已计入，点「明细」展开</em></h3><div class="tplan-table-scroll">${legsTable}</div></section>${compareHtml}<section class="tplan-view-section"><h3>关联成交</h3><div class="tplan-link-chips">${linkChip('known')}${linkChip('target')}</div></section>`;
  $$('#tplan-view-body .tplan-fee-toggle').forEach(button => button.addEventListener('click', () => {
    const row = $(`#tplan-view-body [data-fee-row="${button.dataset.fee}"]`);
    const isHidden = row.classList.contains('hidden');
    row.classList.toggle('hidden', !isHidden);
    button.textContent = isHidden ? '收起' : '明细';
  }));
  openModal('#tplan-view-modal');
}
function closeTplanView() { closeModal('#tplan-view-modal'); }

function openPlanLinkModal(candidates) {
  state.planCandidates = candidates || [];
  state.planLinkPick = 0;
  $('#plan-link-summary').textContent = '这笔成交匹配到做T计划，关联后仍需在「回填执行」确认实际成交。';
  renderPlanLinkList();
  openModal('#plan-link-modal');
}

function renderPlanLinkList() {
  $('#plan-link-list').innerHTML = state.planCandidates.map((item, index) => {
    const legSide = item.leg === 'known'
      ? (item.direction === 'buy_first' ? '买入' : '卖出')
      : (item.direction === 'buy_first' ? '卖出' : '买入');
    return `<button type="button" class="plan-link-option${index === state.planLinkPick ? ' active' : ''}" data-index="${index}"><span class="plan-link-name">${escapeHtml(item.name || '')} ${escapeHtml(item.code || '')}</span><span class="plan-link-meta">${escapeHtml(TDIR_LABELS[item.direction] || item.direction)} · 数量 ${number(Number(item.quantity), 4)} · ${legSide} 计划价 ${money(Number(item.plan_unit_price), item.currency || 'CNY')}</span></button>`;
  }).join('');
  $$('#plan-link-list .plan-link-option').forEach(button => button.addEventListener('click', () => {
    state.planLinkPick = Number(button.dataset.index);
    renderPlanLinkList();
  }));
}

function closePlanLinkModal() { closeModal('#plan-link-modal'); }

async function confirmPlanLink() {
  const item = state.planCandidates[state.planLinkPick];
  if (!item) { closePlanLinkModal(); return; }
  try {
    await request(`/api/t-plans/${item.plan_id}/link`, { method: 'POST', body: { trade_id: item.trade_id } });
    toast('已关联做T计划');
    closePlanLinkModal();
    loadTplans();
  } catch (error) { toast(error.message, true); }
}

function renderTfeedbackLinks(plan) {
  const currency = plan.currency || 'CNY';
  const qty = Number(plan.quantity);
  $('#tfeedback-known-side').textContent = plan.direction === 'buy_first' ? '买入' : '卖出';
  $('#tfeedback-target-side').textContent = plan.direction === 'buy_first' ? '卖出' : '买入';
  $('#tfeedback-known-leg-price').textContent = priceMoney(qty ? Number(plan.known_gross) / qty : 0, currency);
  $('#tfeedback-target-leg-price').textContent = priceMoney(Number(plan.solved_unit_price), currency);
  renderTfeedbackLegActions(plan, 'known');
  renderTfeedbackLegActions(plan, 'target');
}

async function renderTfeedbackLegActions(plan, leg) {
  const box = $(`#tfeedback-${leg}-actions`);
  const status = $(`#tfeedback-${leg}-status`);
  if (!box) return;
  const currency = plan.currency || 'CNY';
  const link = (plan.links || []).find(item => item.leg === leg);
  if (status) { status.textContent = link ? '已关联' : '未关联'; status.className = `tfeedback-leg-status${link ? '' : ' wait'}`; }
  if (link) {
    const unlinkBtn = plan.status === 'open' ? '<button class="text-action danger tfeedback-unlink" type="button">解除</button>' : '';
    box.innerHTML = `<div class="tfeedback-linked"><span>${escapeHtml((link.trade_date || '').slice(5, 10))} · ${link.side === 'buy' ? '买入' : '卖出'} ${number(Number(link.quantity), 4)} 股 @ ${priceMoney(Number(link.unit_price), currency)}</span>${unlinkBtn}</div>`;
    const unlink = box.querySelector('.tfeedback-unlink');
    if (unlink) unlink.addEventListener('click', () => unlinkTplanLeg(plan, leg));
    return;
  }
  if (plan.status !== 'open') { box.innerHTML = '<span class="tfeedback-loading muted">未关联成交</span>'; return; }
  box.innerHTML = '<span class="tfeedback-loading muted">读取成交记录…</span>';
  try {
    const data = await request(`/api/t-plans/${plan.id}/candidates?leg=${leg}`);
    const trades = asArray(data, ['trades']);
    const options = trades.map(trade => `<option value="trade:${trade.id}">${escapeHtml((trade.trade_date || '').slice(5, 10))} ${trade.side === 'buy' ? '买入' : '卖出'} ${number(Number(trade.quantity), 4)} @ ${priceMoney(Number(trade.unit_price), currency)}</option>`);
    box.innerHTML = `<div class="tfeedback-leg-controls"><select class="tfeedback-link-select"><option value="">选择可关联成交…</option>${options.join('')}</select><button class="tfeedback-link-go" type="button" disabled>关联</button></div>`;
    const select = box.querySelector('select');
    const go = box.querySelector('.tfeedback-link-go');
    select.addEventListener('change', () => { go.disabled = !select.value; });
    go.addEventListener('click', () => { if (select.value) linkTplanTrade(plan, select.value); });
  } catch (error) {
    box.innerHTML = `<div class="tfeedback-leg-error"><span class="muted">可关联成交读取失败：${escapeHtml(error.message)}</span></div>`;
  }
}

async function linkTplanTrade(plan, value) {
  const tradeId = Number(String(value || '').replace('trade:', ''));
  if (!tradeId) return;
  try {
    const data = await request(`/api/t-plans/${plan.id}/link`, { method: 'POST', body: { trade_id: tradeId } });
    await handleLinkedPlan(data.plan);
  } catch (error) { toast(error.message, true); }
}

async function unlinkTplanLeg(plan, leg) {
  if (!await confirmDialog('解除后这笔成交不再计入计划，计划会退回进行中，成交记录保留。确认解除？', '解除关联')) return;
  try {
    const data = await request(`/api/t-plans/${plan.id}/link?leg=${encodeURIComponent(leg)}`, { method: 'DELETE' });
    await handleLinkedPlan(data.plan);
  } catch (error) { toast(error.message, true); }
}

async function handleLinkedPlan(plan) {
  await loadTplans();
  if (!plan) return;
  if (plan.status === 'closed') toast('两笔成交已关联，计划已完成');
  openFeedback(plan);
}

function openFeedback(plan) {
  if (!plan) return;
  state.tfeedbackId = plan.id;
  state.tfeedbackPlan = plan;
  const currency = plan.currency || 'CNY';
  const otherLabel = plan.direction === 'buy_first' ? '卖出' : '回补';
  const qty = Number(plan.quantity);
  const net = Number(plan.plan_net);
  $('#tfeedback-summary').textContent = `${plan.name} ${plan.code}`;
  $('#tfeedback-summary-meta').textContent = TDIR_LABELS[plan.direction] || '';
  $('#tfeedback-known-label').textContent = plan.direction === 'buy_first' ? '已知买价' : '已知卖价';
  $('#tfeedback-predict-price-label').textContent = `${otherLabel}价`;
  const links = plan.links || [];
  const knownLink = links.find(link => link.leg === 'known');
  const targetLink = links.find(link => link.leg === 'target');
  state.tfeedbackKnown = knownLink
    ? { gross: Number(knownLink.gross_amount), fees: Number(knownLink.fee_total) }
    : { gross: Number(plan.known_gross), fees: Number(plan.known_fees) };
  $('#tfeedback-known-price').textContent = priceMoney(qty ? state.tfeedbackKnown.gross / qty : 0, currency);
  $('#tfeedback-qty').textContent = number(qty, 4);
  $('#tfeedback-predict-price').textContent = priceMoney(Number(plan.solved_unit_price), currency);
  $('#tfeedback-target').textContent = signedMoney(net, currency);
  $('#tfeedback-target').className = pnlClass(net);
  state.tfeedbackTarget = targetLink ? Number(targetLink.unit_price) : null;
  state.tfeedbackTargetTrade = targetLink ? { gross: Number(targetLink.gross_amount), fees: Number(targetLink.fee_total) } : null;
  state.tfeedbackReady = Boolean(knownLink && targetLink);
  renderTfeedbackLinks(plan);
  updateFeedbackCalc();
  openModal('#tfeedback-modal');
}

function updateFeedbackCalc() {
  const plan = state.tfeedbackPlan;
  if (!plan) return;
  const currency = plan.currency || 'CNY';
  const price = state.tfeedbackTarget;
  const qty = Number(plan.quantity);
  const base = Number(plan.base);
  const hint = $('#tfeedback-link-hint');
  const badge = $('#tfeedback-badge');
  const set = (selector, text, className) => {
    const el = $(selector);
    if (!el) return;
    el.textContent = text;
    if (className !== undefined) el.className = className;
  };
  if (hint) hint.classList.toggle('hidden', Boolean(state.tfeedbackReady));
  if (badge) {
    badge.textContent = state.tfeedbackReady ? (Number(plan.actual_net ?? 0) >= Number(plan.plan_net) ? '达标' : '未达标') : '待回填';
    badge.className = `tfeedback-badge${state.tfeedbackReady ? '' : ' wait'}`;
  }
  if (!(price > 0) || !(qty > 0) || !state.tfeedbackReady) {
    set('#tfeedback-actual-net', '--', '');
    set('#tfeedback-net-rate', '--', '');
    set('#tfeedback-delta', '--', '');
    set('#tfeedback-price-delta', '--', '');
    set('#tfeedback-actual-gross', '--', '');
    set('#tfeedback-fee-rate', '--', '');
    return;
  }
  const gross = roundHalfUp3(state.tfeedbackTargetTrade.gross);
  const fees = state.tfeedbackTargetTrade.fees;
  const knownGross = state.tfeedbackKnown ? state.tfeedbackKnown.gross : Number(plan.known_gross);
  const knownFees = state.tfeedbackKnown ? state.tfeedbackKnown.fees : Number(plan.known_fees);
  const net = plan.direction === 'buy_first' ? (gross - fees) - (knownGross + knownFees) : (knownGross - knownFees) - (gross + fees);
  const rawDelta = net - Number(plan.plan_net);
  const delta = (rawDelta < 0 ? -1 : 1) * roundHalfUp3(rawDelta);
  const solvedUnit = Number(plan.solved_unit_price);
  const priceDeltaPct = solvedUnit ? (price - solvedUnit) / solvedUnit * 100 : 0;
  set('#tfeedback-actual-gross', money(gross, currency), '');
  set('#tfeedback-actual-net', signedMoney(net, currency), pnlClass(net));
  set('#tfeedback-net-rate', base ? signedPct(net / base * 100) : '--', pnlClass(net));
  set('#tfeedback-delta', signedMoney(delta, currency), pnlClass(delta));
  set('#tfeedback-price-delta', price ? `${price >= solvedUnit ? '高于' : '低于'} ${Math.abs(priceDeltaPct).toFixed(2)}%` : '--', '');
  set('#tfeedback-fee-rate', base ? `${((knownFees + fees) / base * 100).toFixed(2)}%` : '--', '');
}

function closeFeedbackModal() { closeModal('#tfeedback-modal'); }

const ROUTE_PAGES = { '/': 'overview', '/overview': 'overview', '/board': 'board', '/watch': 'watch', '/trade': 'trade', '/review': 'review' };
function routePage() { return ROUTE_PAGES[location.pathname] || 'overview'; }
function syncRoute(page) {
  const url = `/${page}`;
  if (location.pathname !== url) history.replaceState(null, '', url);
  if (location.search) history.replaceState(null, '', url);
}

function navigate(page) { $$('.nav-item').forEach(item => item.classList.toggle('active', item.dataset.page === page)); $$('.page').forEach(section => section.classList.toggle('active-page', section.id === `page-${page}`)); const titles = { overview: '持仓总览', board: '盈亏看板', watch: '建仓跟踪', trade: '记录交易', review: '复盘总结' }; $('#page-title').textContent = titles[page] || '持仓总览'; syncRoute(page); $('.top-actions').classList.toggle('hidden', page === 'trade' || page === 'review'); $('#review-tools-bar').classList.toggle('hidden', page !== 'review'); $('#trade-tools-bar').classList.toggle('hidden', page !== 'trade'); if (page === 'overview') drawChart(); if (page === 'review') loadReviews(); if (page === 'board') loadBoard(); if (page === 'watch') loadWatch(); }

function openSettings() { openModal('#settings-modal'); renderFeeRules(); }

const AUDIT_PAGE_SIZE = 20;
const AUDIT_WEEKDAYS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];

function auditMenuChips(menus) {
  const total = menus.reduce((sum, item) => sum + Number(item.count || 0), 0);
  const list = [{ menu: '', menu_label: '全部', count: total }, ...menus];
  return list.map(item => `<button class="audit-chip${item.menu === state.auditMenu ? ' active' : ''}" type="button" data-menu="${escapeAttr(item.menu)}">${escapeHtml(item.menu_label)} <i>${Number(item.count || 0)}</i></button>`).join('');
}

async function loadAuditLogs(page = 1) {
  const box = $('#audit-log-list');
  if (!box) return;
  const hasRows = box.dataset.loaded === '1';
  if (hasRows) {
    box.classList.add('audit-loading');
    $('#audit-prev').disabled = true;
    $('#audit-next').disabled = true;
  } else {
    box.innerHTML = '<div class="muted" style="padding:18px">加载中…</div>';
  }
  try {
    const current = Math.max(1, page);
    const params = new URLSearchParams({ limit: String(AUDIT_PAGE_SIZE), offset: String((current - 1) * AUDIT_PAGE_SIZE) });
    if (state.auditMenu) params.set('menu', state.auditMenu);
    if (state.auditQuery) params.set('q', state.auditQuery);
    const data = await request(`/api/audit-logs?${params}`);
    const logs = asArray(data, ['logs', 'items']);
    const menus = asArray(data, ['menus']);
    const total = Math.max(0, Number(data?.total) || 0);
    const pages = Math.max(1, Math.ceil(total / AUDIT_PAGE_SIZE));
    state.auditPage = Math.min(current, pages);
    const signature = JSON.stringify([state.auditMenu, menus]);
    if (signature !== state.auditMenuSignature) {
      state.auditMenuSignature = signature;
      $('#audit-menus').innerHTML = auditMenuChips(menus);
    }
    if (!logs.length) {
      const filtered = Boolean(state.auditQuery || state.auditMenu);
      box.innerHTML = `<div class="empty-state">${filtered ? '没有符合条件的操作记录。' : '还没有操作记录。'}</div>`;
    } else {
      const groups = [];
      logs.forEach(item => {
        const [date, time] = formatDateTime(item.created_at).split(' ');
        let group = groups.find(entry => entry.date === date);
        if (!group) { group = { date, items: [] }; groups.push(group); }
        group.items.push({ ...item, time });
      });
      box.innerHTML = groups.map(group => {
        const weekday = AUDIT_WEEKDAYS[new Date(`${group.date}T00:00:00`).getDay()] || '';
        return `<div class="audit-day"><b>${escapeHtml(group.date)} ${weekday}</b><span>${group.items.length} 条</span></div>` + group.items.map(item => {
          const unknown = item.menu === 'other';
          const actionLabel = item.action_label || item.action;
          const desc = item.summary ? `<b>${escapeHtml(actionLabel)}</b> · ${escapeHtml(item.summary)}` : `<b>${escapeHtml(actionLabel)}</b>`;
          return `<div class="audit-simple-row"><span class="audit-time">${escapeHtml(item.time || '')}</span><span><span class="audit-menu-tag${unknown ? ' unknown' : ''}">${escapeHtml(item.menu_label || '其他')}</span></span><span class="audit-desc">${desc}</span></div>`;
        }).join('');
      }).join('');
    }
    $('#audit-page-info').textContent = `第 ${state.auditPage} / ${pages} 页 · 共 ${total} 条`;
    $('#audit-prev').disabled = state.auditPage <= 1;
    $('#audit-next').disabled = state.auditPage >= pages || total === 0;
    box.dataset.loaded = '1';
    box.classList.remove('audit-loading');
    const scroller = $('#audit-modal .audit-scroll');
    if (scroller) scroller.scrollTop = 0;
  } catch (error) {
    box.classList.remove('audit-loading');
    $('#audit-prev').disabled = state.auditPage <= 1;
    $('#audit-next').disabled = false;
    box.innerHTML = `<div class="muted" style="padding:18px">${escapeHtml(error.message)}</div>`;
  }
}
function closeSettings() { closeModal('#settings-modal'); }
function openAuditLog() {
  state.auditMenu = '';
  state.auditQuery = '';
  state.auditPage = 1;
  state.auditMenuSignature = '';
  const box = $('#audit-log-list');
  if (box) { delete box.dataset.loaded; box.classList.remove('audit-loading'); }
  $('#audit-search').value = '';
  const actor = ($('#account-name')?.textContent || '').trim() || '-';
  $('#audit-actor').textContent = `操作人：${actor}`;
  openModal('#audit-modal');
  loadAuditLogs(1);
}
function closeAuditLog() { closeModal('#audit-modal'); }
async function openHistory() {
  state.historyPage = 0;
  state.historySide = '';
  state.expandedFees = new Set();
  $('#history-filter').value = '';
  $('#history-results').classList.add('hidden');
  $$('#history-side-tabs button').forEach(button => button.classList.toggle('active', button.dataset.hside === ''));
  $('#trades-body').innerHTML = '<tr><td colspan="10" class="history-empty">正在加载交易…</td></tr>';
  $('#history-count').textContent = '正在加载…';
  $('#history-page-info').textContent = '';
  $('#history-prev').disabled = true;
  $('#history-next').disabled = true;
  openModal('#history-modal');
  try { await loadHistory(); } catch (error) { toast(error.message, true); $('#trades-body').innerHTML = `<tr><td colspan="10" class="history-empty">${escapeHtml(error.message)}</td></tr>`; $('#history-count').textContent = '加载失败'; }
}
function closeHistory() { closeModal('#history-modal'); }

async function doAuth(path, form, isRegister = false) {
  const data = Object.fromEntries(new FormData(form));
  const checks = [
    [form.querySelector('[name="username"]'), (data.username || '').trim() ? '' : '请填写用户名'],
    [form.querySelector('[name="password"]'), (data.password || '').length >= 6 ? '' : '密码至少 6 位'],
  ];
  if (isRegister) checks.push([form.querySelector('[name="confirm"]'), data.confirm === data.password ? '' : '两次输入的密码不一致']);
  if (!applyValidation(checks)) return;
  try {
    const user = await request(path, { method: 'POST', body: { username: data.username, password: data.password }, authRedirect: false });
    showApp(user?.account || user?.user || user); navigate(routePage()); toast(isRegister ? '账号创建成功' : '登录成功');
  } catch (error) { toast(error.message, true); }
}

function escapeHtml(value) { return String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char])); }
function escapeAttr(value) { return escapeHtml(value); }

document.addEventListener('DOMContentLoaded', async () => {
  if (location.search) history.replaceState(null, '', location.pathname || '/');
  $('#trade-date').value = today;
  const authForms = { login: $('#login-form'), register: $('#register-form'), reset: $('#reset-form') };
  const setAuthMode = mode => {
    Object.entries(authForms).forEach(([key, form]) => {
      form.classList.toggle('hidden', key !== mode);
      form.querySelectorAll('.field-error').forEach(node => node.remove());
      form.querySelectorAll('.invalid').forEach(node => node.classList.remove('invalid'));
    });
    $('#auth-aux-default').classList.toggle('hidden', mode !== 'login');
    $('#auth-aux-back').classList.toggle('hidden', mode === 'login');
    const first = authForms[mode]?.querySelector('input');
    if (first) first.focus();
  };
  $$('#auth-view [data-auth-mode]').forEach(button => button.addEventListener('click', () => setAuthMode(button.dataset.authMode)));
  $('#reset-form').addEventListener('submit', event => {
    event.preventDefault();
    const form = event.currentTarget;
    const data = Object.fromEntries(new FormData(form));
    const checks = [
      [form.querySelector('[name="username"]'), (data.username || '').trim() ? '' : '请填写用户名'],
      [form.querySelector('[name="password"]'), (data.password || '').length >= 6 ? '' : '密码至少 6 位'],
      [form.querySelector('[name="confirm"]'), data.confirm === data.password ? '' : '两次输入的密码不一致'],
    ];
    if (!applyValidation(checks)) return;
    request('/api/password/reset', { method: 'POST', body: { username: data.username, password: data.password }, authRedirect: false })
      .then(() => { toast('密码已重置，请用新密码登录'); form.reset(); setAuthMode('login'); const input = $('#login-form [name="username"]'); if (input) input.value = data.username || ''; })
      .catch(error => toast(error.message, true));
  });
  $('#login-form').addEventListener('submit', event => { event.preventDefault(); doAuth('/api/login', event.currentTarget); });
  $$('#auth-view .password-toggle').forEach(button => button.addEventListener('click', () => {
    const input = button.closest('.field-wrap').querySelector('input');
    const show = input.type === 'password';
    input.type = show ? 'text' : 'password';
    button.classList.toggle('visible', show);
    button.setAttribute('aria-label', show ? '隐藏密码' : '显示密码');
    button.setAttribute('aria-pressed', show ? 'true' : 'false');
  }));
  $('#register-form').addEventListener('submit', event => { event.preventDefault(); doAuth('/api/register', event.currentTarget, true); });
  $('#logout-btn').addEventListener('click', async () => { try { await request('/api/logout', { method: 'POST' }); } catch {} state.user = null; showAuth(); });
  $('#settings-btn').addEventListener('click', openSettings);
  $('#settings-close').addEventListener('click', closeSettings);
  $('#audit-btn').addEventListener('click', openAuditLog);
  $('#audit-close').addEventListener('click', closeAuditLog);
  $('#audit-prev').addEventListener('click', () => loadAuditLogs(state.auditPage - 1));
  $('#audit-next').addEventListener('click', () => loadAuditLogs(state.auditPage + 1));
  $('#audit-search').addEventListener('input', event => {
    state.auditQuery = event.target.value.trim();
    window.clearTimeout(loadAuditLogs.timer);
    loadAuditLogs.timer = window.setTimeout(() => loadAuditLogs(1), 240);
  });
  $('#audit-menus').addEventListener('click', event => {
    const chip = event.target.closest('.audit-chip');
    if (!chip) return;
    state.auditMenu = chip.dataset.menu || '';
    $$('#audit-menus .audit-chip').forEach(item => item.classList.toggle('active', (item.dataset.menu || '') === state.auditMenu));
    loadAuditLogs(1);
  });
  $('#open-history-btn').addEventListener('click', openHistory);
  $('#history-close').addEventListener('click', closeHistory);
  $('#open-review-btn').addEventListener('click', () => {
    if (state.reviewScope !== 'all') {
      const latest = reviewScopeGroups().find(item => item.security_id === state.reviewScope);
      if (latest) { openReviewModal('create', null, { security_id: latest.security_id, name: latest.name, code: latest.code, exchange: latest.exchange, currency: latest.currency, period: latest.holding_period }); return; }
    }
    openReviewModal('create');
  });
  $('#review-close').addEventListener('click', closeReviewModal);
  $('#review-cancel').addEventListener('click', closeReviewModal);
  $('#review-save').addEventListener('click', saveReview);
  $('#review-edit').addEventListener('click', () => { const current = state.reviews.find(item => String(item.id) === String(state.reviewEditId)); if (current) openReviewModal('edit', current); });
  $('#review-filter').addEventListener('input', renderReviews);
  $('#review-period').addEventListener('change', renderReviews);
  bindSecurityCombo({ inputSel: '#review-security-search', listSel: '#review-security-results', chipSel: '#review-selected-security', getState: () => state.reviewSecurity, search: searchReviewSecurities, heldOnly: () => false, onHolding: selectReviewSecurity, onSecurity: selectReviewSecurity });
  $$('#review-periods button').forEach(button => button.addEventListener('click', () => { if ($('#review-periods').classList.contains('locked')) return; state.reviewPeriod = button.dataset.period; $$('#review-periods button').forEach(item => item.classList.toggle('active', item === button)); }));
  bindReviewToolbar();
  $('#open-tplan-btn').addEventListener('click', () => openTplanModal());
  $('#open-tplans-btn').addEventListener('click', openTplansModal);
  $('#tplan-close').addEventListener('click', closeTplanModal);
  $('#tplans-close').addEventListener('click', closeTplansModal);
  $('#tplan-view-close').addEventListener('click', closeTplanView);
  $('#tplan-view-done').addEventListener('click', closeTplanView);
  $('#tfeedback-close').addEventListener('click', closeFeedbackModal);
  $('#tfeedback-cancel').addEventListener('click', closeFeedbackModal);
  syncTplanTargetUI();
  $('#tplan-target-type').addEventListener('change', () => { syncTplanTargetUI(); markTplanStale(); });
  $$('#tplan-target-modes button').forEach(button => button.addEventListener('click', () => { $('#tplan-target-type').value = button.dataset.tt; syncTplanTargetUI(); markTplanStale(); }));
  $$('#tplan-directions button').forEach(button => button.addEventListener('click', () => { state.tplanDirection = button.dataset.tdir; syncTplanTargetUI(); $$('#tplan-directions button').forEach(item => { item.classList.toggle('active', item === button); item.classList.toggle('buy', item.dataset.tdir === 'buy_first'); item.classList.toggle('sell', item.dataset.tdir === 'sell_first'); }); markTplanStale(); }));
  $('#tplan-quantity').addEventListener('input', () => { updateTplanAmount(); markTplanStale(); });
  $('#tplan-known-price').addEventListener('input', () => { updateTplanAmount(); markTplanStale(); });
  $('#tplan-target-value').addEventListener('input', markTplanStale);
  bindSecurityCombo({ inputSel: '#tplan-security-search', listSel: '#tplan-security-results', chipSel: '#tplan-selected-security', getState: () => state.tplanSecurity, search: searchTplanSecurities, heldOnly: () => true, onHolding: pickTplanHolding, onSecurity: selectTplanSecurity });
  $('#tplan-calc').addEventListener('click', calcTplan);
  $('#tplan-search').addEventListener('input', event => { state.tplanSearch = event.target.value.trim(); renderTplans(); });
  $$('#tplan-status-tabs button').forEach(button => button.addEventListener('click', () => { state.tplanStatus = button.dataset.tstatus; renderTplans(); }));
  $('#cash-manage').addEventListener('click', () => openCashModal());
  $$('#cash-currency-tabs button').forEach(button => button.addEventListener('click', () => { $('#cash-currency').value = button.dataset.cashCurrency; syncCashModalUI(); }));
  $$('#cash-kind-tabs button').forEach(button => button.addEventListener('click', () => { $('#cash-kind').value = button.dataset.cashKind; syncCashModalUI(); }));
  $('#cash-amount').addEventListener('input', syncCashModalUI);
  $$('#cash-tabs .cash-tab').forEach(button => button.addEventListener('click', () => { state.cashTab = button.dataset.cashTab === 'HKD' ? 'HKD' : 'CNY'; renderCashEntries(); }));
  $('#cash-history-add').addEventListener('click', () => { const tab = state.cashTab; closeCashHistory(); openCashModal(tab); });
  $('#cash-history').addEventListener('click', openCashHistory);
  $('#cash-history-close').addEventListener('click', closeCashHistory);
  $('#cash-history-done').addEventListener('click', closeCashHistory);
  $('#cash-close').addEventListener('click', closeCashModal);
  $('#cash-cancel').addEventListener('click', closeCashModal);
  $('#cash-save').addEventListener('click', saveCashEntry);
  $('#plan-link-close').addEventListener('click', closePlanLinkModal);
  $('#plan-link-skip').addEventListener('click', closePlanLinkModal);
  $('#plan-link-confirm').addEventListener('click', confirmPlanLink);
  document.addEventListener('keydown', event => { if (event.key === 'Escape') { if (confirmResolver) closeConfirm(false); closeFeedbackModal(); closeTplanModal(); closeTplanView(); closeTplansModal(); closeSettings(); closeHistory(); closeReviewModal(); closeWatchNoteModal(); closeWatchDetail(); closeWatchModal(); closeCashModal(); closeCashHistory(); closePlanLinkModal(); } });
  bindModalFocusTrap();
  $('#confirm-ok').addEventListener('click', () => closeConfirm(true));
  $('#confirm-cancel').addEventListener('click', () => closeConfirm(false));
  $('#confirm-close').addEventListener('click', () => closeConfirm(false));
  $$('.nav-item').forEach(item => item.addEventListener('click', () => navigate(item.dataset.page)));
  $$('[data-go]').forEach(button => button.addEventListener('click', () => navigate(button.dataset.go)));
  bindSecurityCombo({ inputSel: '#security-search', listSel: '#security-results', chipSel: '#selected-security', getState: () => state.selectedSecurity, search: searchSecurities, heldOnly: () => state.side === 'sell', onHolding: selectSecurity, onSecurity: selectSecurity });
  document.addEventListener('mousedown', event => { if (event.target.closest('.security-results') || event.target.closest('.selected-security') || event.target.classList.contains('combo-input')) return; $$('.security-results').forEach(item => item.classList.add('hidden')); });
  $$('#trade-form .direction').forEach(button => button.addEventListener('click', () => { if (button.disabled) return; setTradeSide(button.dataset.side); if ($('#security-search').value.trim()) searchSecurities($('#security-search').value); else if (!$('#security-results').classList.contains('hidden')) openComboByChip('#selected-security'); }));
  $('#trade-price').addEventListener('input', renderFeePreview); $('#trade-quantity').addEventListener('input', renderFeePreview); $('#trade-form').addEventListener('submit', submitTrade); $('#history-filter').addEventListener('input', event => { state.historyPage = 0; renderTrades(); const value = event.target.value.trim(); window.clearTimeout(openHistoryDropdown.timer); openHistoryDropdown.timer = window.setTimeout(() => openHistoryDropdown(value), 220); });
  $('#history-filter').addEventListener('focus', () => { if (!$('#history-filter').value.trim()) openHistoryDropdown(''); });
  $('#history-filter').addEventListener('click', () => { if (!$('#history-filter').value.trim()) openHistoryDropdown(''); });
  $('#history-filter').addEventListener('keydown', event => { if (event.key === 'Escape' && !$('#history-results').classList.contains('hidden')) { event.stopPropagation(); $('#history-results').classList.add('hidden'); } }); $$('#history-side-tabs button').forEach(button => button.addEventListener('click', () => { state.historySide = button.dataset.hside || ''; state.historyPage = 0; $$('#history-side-tabs button').forEach(item => item.classList.toggle('active', item === button)); renderTrades(); })); $('#history-prev').addEventListener('click', () => { state.historyPage = Math.max(0, state.historyPage - 1); renderTrades(); }); $('#history-next').addEventListener('click', () => { state.historyPage += 1; renderTrades(); }); $('#save-settings').addEventListener('click', saveSettings);
  syncRangeTabs();
  $$('.range').forEach(button => button.addEventListener('click', async () => { state.range = button.dataset.range; syncRangeTabs(); if (state.chartSecurity) await selectChartSecurity(state.chartSecurity.id, state.chartSecurity.name, state.chartSecurity.code, state.chartSecurity.currency); }));
  $$('#watch-markets .market-pill').forEach(button => button.addEventListener('click', () => { state.watchMarket = button.dataset.wmarket; renderWatch(); }));
  $$('#watch-status .market-pill').forEach(button => button.addEventListener('click', () => { state.watchStatus = button.dataset.wstatus; renderWatch(); }));
  $('#open-watch-btn').addEventListener('click', () => openWatchModal('create'));
  $('#watch-close').addEventListener('click', closeWatchModal);
  $('#watch-cancel').addEventListener('click', closeWatchModal);
  $('#watch-save').addEventListener('click', saveWatch);
  $('#watch-target').addEventListener('input', syncWatchTargetHint);
  $('#watch-note-toggle').addEventListener('click', () => { const body = $('#watch-note-body'); const closed = body.classList.toggle('hidden'); $('#watch-note-toggle').textContent = closed ? '＋ 展开' : '收起'; if (!closed) $('#watch-editor').focus(); });
  $('#watch-security-search').addEventListener('input', event => { window.clearTimeout(searchWatchSecurities.timer); searchWatchSecurities.timer = window.setTimeout(() => searchWatchSecurities(event.target.value), 220); });
  $('#watch-detail-close').addEventListener('click', closeWatchDetail);
  $('#watch-detail-cancel').addEventListener('click', closeWatchDetail);
  $('#watch-note-add').addEventListener('click', () => openWatchNoteModal('create'));
  $('#watch-note-close').addEventListener('click', closeWatchNoteModal);
  $('#watch-note-cancel').addEventListener('click', closeWatchNoteModal);
  $('#watch-note-save').addEventListener('click', saveWatchNote);
  $('#watchnote-editor').addEventListener('input', syncWatchNoteSaveState);
  $('#watch-note-edit').addEventListener('click', () => openWatchNoteModal('edit', state.watchNoteCurrent));
  const chartCanvas = $('#price-chart'); const chartTip = $('#chart-tip');
  chartCanvas.addEventListener('mousemove', event => {
    if (!state.chartSecurity) return;
    boardChartHover(chartCanvas, overviewChartCard(), event, overviewChartOptions());
  });
  chartCanvas.addEventListener('mouseleave', () => {
    const geom = state.boardGeom[`overview-${state.chartSecurity?.id ?? 'none'}`];
    if (geom) { geom.hover = null; geom.hoverMark = null; }
    chartTip.classList.add('hidden');
    drawChart();
  });
  $('#refresh-btn').addEventListener('click', async () => {
    const button = $('#refresh-btn');
    button.disabled = true;
    markSyncState('正在更新…', false);
    try { await syncPrices(); } catch { /* syncPrices 已给出失败提示 */ }
    state.board = null; state.boardCache = {}; state.watch = null; state.watchCache = {};
    await loadDashboard();
    if (state.chartSecurity) await selectChartSecurity(state.chartSecurity.id, state.chartSecurity.name, state.chartSecurity.code, state.chartSecurity.currency);
    if ($('#page-board').classList.contains('active-page')) await loadBoard();
    if ($('#page-watch').classList.contains('active-page')) await loadWatch();
    button.disabled = false;
  });
  window.addEventListener('resize', () => { drawChart(); renderBoardCharts(); });
  window.addEventListener('popstate', () => { if (!document.querySelector('#app-view').classList.contains('hidden')) navigate(routePage()); });
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => drawChart());
  try { const user = await request('/api/me', { authRedirect: false }); showApp(user?.account || user?.user || user); navigate(routePage()); } catch { if (location.search) history.replaceState(null, '', location.pathname); showAuth(); }
});
