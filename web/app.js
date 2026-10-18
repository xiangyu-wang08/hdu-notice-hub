'use strict';
const $ = (id) => document.getElementById(id);
let data = null, sourceFilter = 'all', groupFilter = 'all', visibleCount = 20, polling = null, pending = false;
const statusNames = {never: '待获取', unknown: '待验证', cached: '本地缓存', loading: '获取中', ok: '已更新', partial: '部分获取', error: '获取失败'};

/* 来源卡片、页签与列表标记的配色。
   jwc / com 沿用原版配色；其余来源按调色板轮转，同一来源各处颜色一致。 */
const FIXED_TINTS = {
  jwc: {symbol: '#edf2ff', symbolFg: '#4167c8', dot: '#d3def9', badge: '#f2f5fc', badgeFg: '#405e95'},
  com: {symbol: '#eaf5f2', symbolFg: '#3f8c7e', dot: '#bcdcd4', badge: '#eff7f4', badgeFg: '#356b5c'}
};
const PALETTE = [[220, 58], [168, 32], [32, 52], [276, 36], [342, 44], [196, 46], [92, 34], [256, 34], [12, 46], [150, 30]];
const tintCache = new Map();
function tintOf(id) {
  if (FIXED_TINTS[id]) return FIXED_TINTS[id];
  if (tintCache.has(id)) return tintCache.get(id);
  const index = sourceIds.indexOf(id);
  const [h, s] = PALETTE[Math.max(0, index) % PALETTE.length];
  const hsl = (sat, light) => `hsl(${h},${Math.min(85, s + sat)}%,${light}%)`;
  const tint = {symbol: hsl(26, 95), symbolFg: hsl(14, 35), dot: hsl(18, 84), badge: hsl(28, 96), badgeFg: hsl(16, 30)};
  tintCache.set(id, tint);
  return tint;
}
/* 页签用短名：去掉「（软件学院）」这类括注 */
const shortName = (name) => name.replace(/（[^）]*）/g, '').replace(/\([^)]*\)/g, '').trim() || name;
const symbolOf = (name) => shortName(name).trim().charAt(0) || '讯';

let sourceIds = [], cardSignature = null;
const nameOf = new Map();

function message(text, warning = false) {
  $('status-message').hidden = !text;
  $('status-message').textContent = text;
  $('status-message').classList.toggle('warning', warning);
}
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function formatTime(value) {
  return new Intl.DateTimeFormat('zh-CN', {timeZone: 'Asia/Shanghai', year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', hour12:false}).format(new Date(value));
}
function detailText(source) {
  const lines = [];
  if (source.status === 'loading') lines.push('正在读取官网，已有内容仍可查看');
  else if (source.error) lines.push(source.error + '；保留此前可用内容');
  else if (source.last_result === 'unknown' && source.from_cache) lines.push('历史缓存结果不明，请重新获取验证');
  else if (source.status === 'never') lines.push('尚未获取 · 点击上方按钮开始');
  if (source.last_attempt) lines.push('最近尝试：' + formatTime(source.last_attempt));
  if (source.last_success) lines.push('获得数据：' + formatTime(source.last_success));
  if (source.last_complete_success) lines.push('完整读取：' + formatTime(source.last_complete_success));
  if (source.last_success) lines.push(`本次读取 ${source.pages} 页${source.limited ? ' · 已达5页上限' : ''} · 保留 ${source.count} 条`);
  if (source.latest_date) lines.push(`已获取日期：${source.earliest_date} 至 ${source.latest_date}`);
  if (source.latest_date && Date.parse(data.today) - Date.parse(source.latest_date) > 90 * 86400000)
    lines.push('最新条目超过90天；栏目可能更新较少，请核对官网');
  if (source.status === 'ok' && !source.count) lines.push('官网栏目当前为空');
  return lines.join('\n');
}
/* 来源数量或顺序变化时重建卡片与页签；色板依赖顺序，所以顺序也算签名 */
function buildSourceUI() {
  const ids = data.sources.map(s => s.id);
  const signature = ids.join(',');
  if (signature === cardSignature) return;
  cardSignature = signature;
  sourceIds = ids;
  tintCache.clear();
  nameOf.clear();
  for (const s of data.sources) nameOf.set(s.id, shortName(s.name));

  const grid = $('sources');
  grid.replaceChildren();
  const select = $('source-select');
  select.replaceChildren();
  const all = el('option', '', '全部来源'); all.value = 'all'; select.append(all);
  const groups = {};
  for (const [id, name] of [['school', '校级平台'], ['college', '学院／教学单位']]) {
    const optionGroup = el('optgroup'); optionGroup.label = name; select.append(optionGroup); groups[id] = optionGroup;
    const heading = el('h3', 'source-group-heading', name); grid.append(heading);
    const section = el('div', 'source-group-grid'); section.id = 'group-' + id; grid.append(section);
  }
  data.sources.forEach((s) => {
    const tint = tintOf(s.id);
    const card = el('article', 'source-card');
    card.dataset.sourceCard = s.id;
    card.title = s.name + (s.url ? '\n' + s.url : '');
    const symbol = el('span', 'source-symbol', symbolOf(s.name));
    symbol.setAttribute('aria-hidden', 'true');
    symbol.style.background = tint.symbol;
    symbol.style.color = tint.symbolFg;
    const info = el('div', 'source-info');
    const heading = el('h4', '', s.name);
    const state = el('span', 'source-state', statusNames[s.status] || '待获取');
    state.id = 'state-' + s.id;
    heading.append(state);
    info.append(heading);
    const latest = el('p', 'source-latest');
    latest.id = 'latest-' + s.id;
    info.append(latest);
    const disclosure = el('details', 'source-details');
    const summary = el('summary', '', '查看详情');
    summary.setAttribute('aria-label', s.name + '：查看抓取详情');
    disclosure.append(summary);
    const links = el('div', 'source-links');
    let first = true;
    for (const [href, label, title] of [[s.url, '通知公告 ↗', s.name + ' · 通知公告'], [s.home, '官网 ↗', s.name + ' · 官网']]) {
      if (!href || (label.startsWith('官网') && href === s.url)) continue;
      const link = el('a', '', label);
      link.href = href; link.target = '_blank'; link.rel = 'noopener noreferrer'; link.title = title;
      if (!first) link.style.marginLeft = '10px';
      first = false;
      links.append(link);
    }
    const detail = el('p', '');
    detail.id = 'detail-' + s.id;
    disclosure.append(links, detail);
    info.append(disclosure);
    const count = el('strong', '', '—');
    count.id = 'count-' + s.id;
    card.append(symbol, info, count);
    $('group-' + s.group).append(card);

    const option = el('option', '', s.name); option.value = s.id; groups[s.group].append(option);

  });
}
function render() {
  if (!data) return;
  const busy = data.refreshing || pending;
  $('refresh').disabled = busy;
  $('refresh-text').textContent = busy ? '正在获取通知…' : '一键获取通知';
  $('refresh-icon').classList.toggle('spinning', busy);
  buildSourceUI();
  for (const source of data.sources) {
    const badge = $('state-' + source.id);
    if (badge) {badge.textContent = (statusNames[source.status] || '待获取') + (source.from_cache ? ' · 缓存' : ''); badge.className = 'source-state ' + source.status;}
    const count = $('count-' + source.id);
    if (count) count.textContent = source.count || (source.last_success ? '0' : '—');
    if (count) count.setAttribute('aria-label', `已获取 ${source.count || 0} 条通知`);
    const latest = $('latest-' + source.id);
    if (latest) latest.textContent = source.latest_date ? '最新通知 ' + source.latest_date : source.last_success && !source.count ? '栏目暂无通知' : '暂无可用通知';
    const detail = $('detail-' + source.id);
    if (detail) detail.textContent = detailText(source);
  }
  const times = data.sources.map(s => s.last_success).filter(Boolean).sort();
  const counts = {ok:0, partial:0, error:0, unknown:0, never:0, loading:0};
  for (const source of data.sources) counts[source.status] = (counts[source.status] || 0) + 1;
  $('source-total').textContent = data.sources.length;
  const summary = $('sources-summary');
  summary.replaceChildren();
  for (const [status, label] of [['ok','成功'], ['partial','部分成功'], ['error','失败'], ['unknown','待验证'], ['never','待获取'], ['loading','获取中']]) {
    if (counts[status] || status === 'ok') summary.append(el('span', 'status-chip ' + status, `${label} ${counts[status]}`));
  }
  $('retry-failed').hidden = !(counts.partial + counts.error);
  $('retry-failed').disabled = busy;
  $('source-select').value = sourceFilter;
  for (const b of $('source-tabs').querySelectorAll('[data-source]')) {
    b.classList.toggle('active', b.dataset.source === groupFilter);
    b.setAttribute('aria-pressed', String(b.dataset.source === groupFilter));
  }
  $('last-updated').textContent = busy ? '正在分别读取各官方通知栏目' : times.length ? '最近获取于 ' + formatTime(times[times.length - 1]) : '点击获取，读取校级平台与各学院官网的近期通知';
  const term = $('search').value.trim().toLocaleLowerCase();
  const days = Number($('days').value);
  const today = Date.parse(data.today + 'T00:00:00+08:00');
  const cutoff = today - (days - 1) * 86400000;
  const notices = data.notices.filter(n => (sourceFilter === 'all' || n.source_id === sourceFilter)
    && (groupFilter === 'all' || data.sources.find(s => s.id === n.source_id)?.group === groupFilter)
    && (!days || (Date.parse(n.date + 'T00:00:00+08:00') >= cutoff && Date.parse(n.date + 'T00:00:00+08:00') <= today))
    && n.title.toLocaleLowerCase().includes(term));
  $('result-count').textContent = notices.length;
  $('range-note').textContent = days ? `近 ${days} 天（含今天） · 按官网发布日期倒序` : '全部已获取记录 · 按官网发布日期倒序';
  const list = $('notice-list');
  list.replaceChildren();
  if (!notices.length) {
    const empty = el('div', 'empty');
    empty.append(el('div', 'empty-symbol', busy ? '↻' : '≡'));
    const never = !data.notices.length && data.sources.every(s => !s.last_attempt);
    const failed = !data.notices.length && data.sources.some(s => s.status === 'error');
    empty.append(el('h3', '', busy ? '正在为你汇集校园通知' : never ? '让近期通知，在这里相遇' : failed ? '暂时无法获取通知' : '没有符合条件的通知'));
    empty.append(el('p', '', busy ? '各来源分别读取，成功后会显示在这里。' : never ? '点击「一键获取通知」，读取校级平台与各学院的官方来源。' : failed ? '请查看上方来源提示，检查网络后重新获取。' : '试试扩大时间范围、切换来源，或更换搜索关键词。'));
    list.append(empty);
  }
  for (const n of notices.slice(0, visibleCount)) {
    const tint = tintOf(n.source_id);
    const row = el('article', 'notice-row');
    const dot = el('span', 'notice-dot');
    dot.style.background = tint.dot;
    row.append(dot);
    const main = el('div', 'notice-main');
    const link = el('a', 'notice-title', n.title);
    link.href = n.url; link.target = '_blank'; link.rel = 'noopener noreferrer';
    main.append(link);
    const meta = el('div', 'notice-meta');
    const badge = el('span', 'source-badge', nameOf.get(n.source_id) || n.source_id);
    badge.style.color = tint.badgeFg;
    badge.style.background = tint.badge;
    badge.title = n.source_id;
    const mobileDate = el('time', 'notice-date-mobile', n.date); mobileDate.dateTime = n.date;
    meta.append(badge, mobileDate, el('span', 'original-link-hint', '官方原文 ↗'));
    main.append(meta); row.append(main);
    const date = el('time', 'notice-date', n.date); date.dateTime = n.date;
    row.append(date, el('span', 'row-arrow', '↗'));
    list.append(row);
  }
  $('list-summary').textContent = `显示 ${Math.min(visibleCount, notices.length)} / ${notices.length} 条符合条件的通知 · 本机已获取 ${data.notices.length} 条`;
  $('show-more').hidden = notices.length <= visibleCount;
}
async function load() {
  const response = await fetch('/api/state');
  if (!response.ok) throw new Error('本机服务暂时不可用，请重新启动网页。');
  data = await response.json();
  render();
  if (data.refreshing) {
    message('正在获取各来源的通知，已有内容仍可查看。');
    schedulePoll();
  } else {
    clearTimeout(polling);
    const failures = data.sources.filter(s => ['error', 'partial'].includes(s.status));
    if (data.warning) message(data.warning, true);
    else if (failures.length) message(failures.map(s => s.name + '：' + s.error).join('；') + '。已获取内容仍保留，可稍后重试。', true);
    else message('');
  }
}
function schedulePoll() {
  clearTimeout(polling);
  polling = setTimeout(() => load().catch(handleError), 1000);
}
function handleError(error) {
  clearTimeout(polling);
  pending = false;
  if (data) data.refreshing = false;
  render();
  $('refresh').disabled = false;
  $('refresh-text').textContent = '重试获取通知';
  $('refresh-icon').classList.remove('spinning');
  message(error.message || '无法连接本机服务，请重新启动网页。', true);
}
async function refreshSources(sourceIds) {
  if (pending || data?.refreshing) return;
  pending = true; render(); message('正在连接官网…');
  try {
    const response = await fetch('/api/refresh', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(sourceIds ? {source_ids:sourceIds} : {})});
    pending = false;
    if (response.status === 429) {render(); message('刚刚已经获取过通知，请间隔 30 秒后再试。'); return;}
    if (!response.ok) throw new Error('获取请求失败，请检查本机服务后重试。');
    await load();
  } catch (error) {handleError(error);}
}
$('refresh').addEventListener('click', () => refreshSources());
$('retry-failed').addEventListener('click', () => refreshSources(data.sources.filter(s => ['error','partial'].includes(s.status)).map(s => s.id)));
$('source-select').addEventListener('change', () => {sourceFilter = $('source-select').value; groupFilter = 'all'; visibleCount = 20; render();});
$('source-tabs').addEventListener('click', event => {
  const button = event.target.closest('button[data-source]');
  if (!button) return;
  groupFilter = button.dataset.source; sourceFilter = 'all';
  for (const b of $('source-tabs').querySelectorAll('[data-source]')) {
    b.classList.toggle('active', b === button);
    b.setAttribute('aria-pressed', String(b === button));
  }
  visibleCount = 20; render();
});
$('toggle-sources').addEventListener('click', () => {
  const grid = $('sources');
  const open = grid.hidden;
  grid.hidden = !open;
  $('toggle-sources').textContent = open ? '收起' : '展开';
  $('toggle-sources').setAttribute('aria-expanded', String(open));
});
$('days').addEventListener('change', () => {visibleCount = 20; render();});
$('search').addEventListener('input', () => {visibleCount = 20; render();});
$('show-more').addEventListener('click', () => {visibleCount += 20; render();});
document.addEventListener('keydown', event => {
  if (event.key === '/' && !['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName)) {event.preventDefault(); $('search').focus();}
});
load().catch(handleError);
