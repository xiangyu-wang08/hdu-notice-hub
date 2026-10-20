'use strict';
const $ = (id) => document.getElementById(id);
let data = null, sourceFilter = 'all', groupFilter = 'all', visibleCount = 20, polling = null, pending = false;
const statusNames = {never: '待获取', unknown: '待验证', cached: '本地缓存', loading: '获取中', ok: '已更新', partial: '部分获取', error: '获取失败'};
let aiMode = false, selectionInitialized = false;
const aiSelected = new Map();
let aiSettings = {base_url: '', model: '', key_saved: false, configured: false}, aiSettingsLoaded = false;
let settingsAction = '', settingsOpener = null, aiSubmitting = false, aiTask = null, aiTaskSnapshot = [], aiTaskPolling = null, aiTaskPollInFlight = null;
const aiStatusNames = {queued: '等待处理', reading: '正在读取正文', summarizing: '正在生成回答', succeeded: '已完成', failed: '未能完成'};
const aiTaskRunning = () => aiSubmitting || Boolean(aiTask && !['succeeded', 'failed'].includes(aiTask.status));

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
function safeUrl(value) {
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : null;
  } catch {return null;}
}
function externalLink(title, value, className = '') {
  const href = safeUrl(value);
  const node = el(href ? 'a' : 'span', className, title);
  if (href) {node.href = href; node.target = '_blank'; node.rel = 'noopener noreferrer';}
  return node;
}
function filteredNotices() {
  if (!data) return [];
  const term = $('search').value.trim().toLocaleLowerCase();
  const days = Number($('days').value);
  const today = Date.parse(data.today + 'T00:00:00+08:00');
  const cutoff = today - (days - 1) * 86400000;
  return data.notices.filter(n => (sourceFilter === 'all' || n.source_id === sourceFilter)
    && (groupFilter === 'all' || data.sources.find(s => s.id === n.source_id)?.group === groupFilter)
    && (!days || (Date.parse(n.date + 'T00:00:00+08:00') >= cutoff && Date.parse(n.date + 'T00:00:00+08:00') <= today))
    && n.title.toLocaleLowerCase().includes(term)).sort((a, b) => b.date.localeCompare(a.date));
}
function noticeSnapshot(notice) {
  return {id: notice.id, title: notice.title, date: notice.date, url: notice.url,
    source_name: data?.sources.find(s => s.id === notice.source_id)?.name || nameOf.get(notice.source_id) || notice.source_id};
}
function selectDefaultNotices() {
  aiSelected.clear();
  for (const notice of filteredNotices().slice(0, 5)) aiSelected.set(notice.id, noticeSnapshot(notice));
  selectionInitialized = aiSelected.size > 0;
}
function filtersChanged() {
  visibleCount = 20;
  if (aiMode) selectDefaultNotices();
  else {aiSelected.clear(); selectionInitialized = false;}
  $('ai-form-message').textContent = '';
  render();
}
function renderAIControls() {
  $('mode-normal').classList.toggle('active', !aiMode);
  $('mode-ai').classList.toggle('active', aiMode);
  $('mode-normal').setAttribute('aria-pressed', String(!aiMode));
  $('mode-ai').setAttribute('aria-pressed', String(aiMode));
  $('ai-selection-bar').hidden = !aiMode;
  $('ai-workspace').hidden = !aiMode;
  $('ai-selection-count').textContent = `已选 ${aiSelected.size} / 10 条`;
  $('ai-select-default').disabled = !data || !filteredNotices().length;
  $('ai-clear-selection').disabled = !aiSelected.size;
  $('ai-ask').disabled = !aiSelected.size || aiTaskRunning() || Boolean(settingsAction);
  $('ai-ask').textContent = aiTaskRunning() ? '正在处理…' : '问问 AI';
  $('ai-config-status').textContent = !aiSettingsLoaded ? '配置状态暂不可用' : aiSettings.configured ? 'AI 已配置' : '请先配置 AI';
  $('ai-config-status').classList.toggle('configured', aiSettingsLoaded && aiSettings.configured);
  $('ai-settings-save').disabled = Boolean(settingsAction) || aiTaskRunning();
  $('ai-settings-test').disabled = Boolean(settingsAction) || aiTaskRunning();
  $('ai-clear-key').disabled = !aiSettings.key_saved || Boolean(settingsAction) || aiTaskRunning();
  $('ai-settings-save').textContent = settingsAction === 'save' ? '正在保存…' : '保存设置';
  $('ai-settings-test').textContent = settingsAction === 'test' ? '正在测试…' : '测试连接';
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
      if (!safeUrl(href) || (label.startsWith('官网') && href === s.url)) continue;
      const link = externalLink(label, href); link.title = title;
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
  renderAIControls();
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
  const days = Number($('days').value);
  const notices = filteredNotices();
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
    row.classList.toggle('selectable', aiMode);
    row.classList.toggle('selected', aiMode && aiSelected.has(n.id));
    row.dataset.noticeId = n.id;
    if (aiMode) {
      const pick = el('label', 'notice-pick');
      const checkbox = el('input'); checkbox.type = 'checkbox'; checkbox.checked = aiSelected.has(n.id);
      checkbox.dataset.noticeSelect = n.id;
      checkbox.setAttribute('aria-label', '选择通知：' + n.title);
      checkbox.addEventListener('change', () => {
        if (checkbox.checked && aiSelected.size >= 10) {
          checkbox.checked = false;
          $('ai-form-message').textContent = '最多选择 10 条通知，请先取消一条。';
          return;
        }
        if (checkbox.checked) aiSelected.set(n.id, noticeSnapshot(n));
        else aiSelected.delete(n.id);
        row.classList.toggle('selected', checkbox.checked);
        $('ai-form-message').textContent = '';
        renderAIControls();
      });
      pick.append(checkbox); row.append(pick);
    }
    const dot = el('span', 'notice-dot');
    dot.style.background = tint.dot;
    row.append(dot);
    const main = el('div', 'notice-main');
    const link = externalLink(n.title, n.url, 'notice-title');
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
  if (aiMode && !selectionInitialized && data.notices.length) selectDefaultNotices();
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
async function aiRequest(path, payload) {
  const options = payload === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)};
  let response;
  try {response = await fetch(path, options);}
  catch {throw new Error('无法连接本机服务，请确认程序仍在运行。');}
  let result;
  try {result = await response.json();}
  catch {
    const error = new Error('本机服务返回了无法读取的结果，请稍后重试。');
    error.status = response.status;
    throw error;
  }
  if (!response.ok) {
    const error = new Error(typeof result.error === 'string' ? result.error : '操作未能完成，请稍后重试。');
    error.status = response.status;
    throw error;
  }
  return result;
}
let settingsGeneration = 0;
function applyAISettings(result) {
  aiSettings = {base_url: typeof result.base_url === 'string' ? result.base_url : '', model: typeof result.model === 'string' ? result.model : '',
    key_saved: result.key_saved === true, configured: result.configured === true, warning: typeof result.warning === 'string' ? result.warning : ''};
  aiSettingsLoaded = true;
  $('ai-api-key').placeholder = aiSettings.key_saved ? '已保存，留空沿用；输入可替换' : '输入 API Key';
  $('ai-key-state').textContent = aiSettings.key_saved ? 'API Key 已加密保存在本机，不会回显。' : '尚未保存 API Key';
  renderAIControls();
}
async function loadAISettings() {
  const generation = settingsGeneration;
  const result = await aiRequest('/api/ai/settings');
  if (generation === settingsGeneration) applyAISettings(result);
}
function settingsMessage(text, warning = false) {
  $('ai-settings-message').textContent = text;
  $('ai-settings-message').classList.toggle('warning', warning);
}
function openAISettings(note = '') {
  const dialog = $('ai-settings-dialog');
  if (dialog.open) return;
  settingsOpener = document.activeElement;
  $('ai-base-url').value = aiSettings.base_url;
  $('ai-model').value = aiSettings.model;
  $('ai-api-key').value = '';
  settingsMessage(note || aiSettings.warning || '');
  renderAIControls();
  dialog.showModal();
  $('ai-base-url').focus();
}
function settingsPayload() {
  const baseUrl = $('ai-base-url').value.trim();
  let url;
  try {url = new URL(baseUrl);} catch {throw new Error('请填写有效的 HTTPS Base URL。');}
  if (url.protocol !== 'https:' || url.username || url.password) throw new Error('Base URL 须使用 HTTPS，且不能包含用户名或密码。');
  const model = $('ai-model').value.trim();
  if (!model) throw new Error('请手动填写模型名。');
  const payload = {base_url: baseUrl, model};
  const key = $('ai-api-key').value.trim();
  if (key) payload.api_key = key;
  return payload;
}
async function changeAISettings(action) {
  if (settingsAction || aiTaskRunning()) return;
  let payload;
  try {
    payload = action === 'clear' ? {base_url: aiSettings.base_url, model: aiSettings.model, clear_key: true} : settingsPayload();
    if (action === 'save' && !payload.api_key && !aiSettings.key_saved) throw new Error('尚未保存 API Key，请填写后再保存设置。');
  }
  catch (error) {settingsMessage(error.message, true); return;}
  settingsAction = action;
  renderAIControls();
  settingsMessage(action === 'test' ? '正在测试当前表单，可能需要片刻…' : action === 'clear' ? '正在清除已保存的 Key…' : '正在保存设置…');
  try {
    if (action === 'test') {
      const result = await aiRequest('/api/ai/test', payload);
      settingsMessage((typeof result.message === 'string' ? result.message : '连接测试成功。') + ' 本次测试未保存配置。');
    } else {
      settingsGeneration += 1;
      applyAISettings(await aiRequest('/api/ai/settings', payload));
      settingsMessage(action === 'clear' ? '已清除保存在本机的 API Key。' : (aiSettings.configured ? '设置已保存。' : '设置已保存，但 AI 配置尚未完整，请检查各项。') + (aiSettings.warning ? ' ' + aiSettings.warning : ''), action !== 'clear' && !aiSettings.configured);
      $('ai-api-key').value = '';
    }
  } catch (error) {settingsMessage(error.message, true);}
  finally {settingsAction = ''; renderAIControls();}
}
function taskScopeLine(notice, className = '') {
  const item = el('li', className);
  item.append(externalLink(notice.title || '未命名通知', notice.url));
  item.append(el('span', 'ai-reference-meta', [notice.source_name, notice.date].filter(Boolean).join(' · ')));
  return item;
}
function renderAITask() {
  if (!aiTask) return;
  $('ai-task').hidden = false;
  $('ai-task-status').textContent = aiStatusNames[aiTask.status] || '正在处理';
  $('ai-task-status').className = aiTask.status;
  $('ai-task-question').textContent = aiTask.question ? '问题：' + aiTask.question : '问题：简要概括通知、重要日期和个人需完成的事项。';
  const progress = $('ai-task-progress');
  progress.hidden = aiTask.status === 'failed';
  const done = Math.max(0, Number(aiTask.progress?.done) || 0), total = Math.max(0, Number(aiTask.progress?.total) || aiTaskSnapshot.length);
  if (aiTask.status === 'succeeded') {progress.max = 1; progress.value = 1;}
  else if (['queued', 'summarizing'].includes(aiTask.status)) progress.removeAttribute('value');
  else {progress.max = Math.max(1, total); progress.value = Math.min(done, total);}
  $('ai-task-message').textContent = aiTask.status === 'reading' ? `正在读取所选通知正文：${done} / ${total} 条`
    : aiTask.status === 'queued' ? '任务已创建，正在准备读取所选通知。'
    : aiTask.status === 'summarizing' ? '正文读取完成，正在等待 AI 服务返回回答，请稍候。'
    : aiTask.status === 'failed' ? aiTask.error || '本次任务未能完成，请查看下方正文读取情况。' : '回答已生成，可对照下方官方来源查看。';
  const notices = $('ai-task-notices'); notices.replaceChildren();
  const serverSources = new Map((aiTask.sources || []).map(source => [source.id, source]));
  for (const snapshot of aiTaskSnapshot) notices.append(taskScopeLine({...snapshot, ...(serverSources.get(snapshot.id) || {})}));
  $('ai-task-scope-summary').textContent = `本次选中的 ${aiTaskSnapshot.length} 条通知`;
  const answer = typeof aiTask.answer === 'string' ? aiTask.answer : '';
  $('ai-answer').hidden = !answer;
  $('ai-answer').textContent = answer;
  const references = $('ai-references'); references.replaceChildren();
  const failures = new Map((aiTask.failures || []).map(failure => [failure.id, failure]));
  const referenceSources = new Map(serverSources);
  for (const failure of aiTask.failures || []) {
    if (!referenceSources.has(failure.id)) referenceSources.set(failure.id, aiTaskSnapshot.find(notice => notice.id === failure.id) || failure);
  }
  for (const [id, source] of referenceSources) {
    const failure = failures.get(id);
    const read = Number(source.used_chars) > 0;
    const waiting = !read && !failure && ['queued', 'reading'].includes(aiTask.status);
    const item = taskScopeLine({...source, ...(failure || {})}, failure ? 'ai-reference-failed' : '');
    if (source.number) item.prepend(el('span', 'ai-reference-number', '[' + source.number + '] '));
    const state = failure ? '未读取：' + (failure.reason || '正文读取失败')
      : read ? source.truncated ? '已读取 · 正文过长，已截断' : '已读取正文'
      : waiting ? '待读取正文' : '未读取正文';
    item.append(el('span', 'ai-read-state' + (!read && !failure ? ' pending' : ''), state));
    references.append(item);
  }
  $('ai-reference-section').hidden = !references.children.length;
  renderAIControls();
}
async function pollAITask() {
  const taskId = aiTask?.task_id;
  if (!taskId || aiTaskPollInFlight?.task_id === taskId) return;
  const token = {task_id: taskId};
  aiTaskPollInFlight = token;
  const isCurrent = () => aiTask?.task_id === taskId && aiTaskPollInFlight === token;
  clearTimeout(aiTaskPolling);
  aiTaskPolling = null;
  $('ai-task-check').disabled = true;
  try {
    const task = await aiRequest('/api/ai/tasks/' + encodeURIComponent(taskId));
    if (!isCurrent()) return;
    aiTask = task;
    $('ai-task-warning').hidden = true;
    $('ai-task-check').hidden = true;
    renderAITask();
    if (aiTaskRunning()) aiTaskPolling = setTimeout(() => {if (aiTask?.task_id === taskId) pollAITask();}, 1000);
  } catch (error) {
    if (!isCurrent()) return;
    if (error.status === 404) {
      aiTask = {...aiTask, status: 'failed', answer: null, error: '本次任务已过期或本机服务已重启，请重新发起提问。'};
      aiTaskPolling = null;
      $('ai-task-warning').hidden = true;
      $('ai-task-check').hidden = true;
      renderAITask();
      return;
    }
    $('ai-task-warning').textContent = error.message + ' 点击「继续查看」读取现有任务，不会重复发送问题。';
    $('ai-task-warning').hidden = false;
    $('ai-task-check').hidden = false;
  } finally {
    if (aiTaskPollInFlight === token) {
      aiTaskPollInFlight = null;
      if (aiTask?.task_id === taskId) $('ai-task-check').disabled = false;
    }
  }
}
async function askAI(event) {
  event.preventDefault();
  if (aiTaskRunning() || settingsAction || !aiSelected.size) return;
  if (!aiSettingsLoaded || !aiSettings.configured) {openAISettings('请先填写并保存完整的 AI 配置，再发起提问。'); return;}
  const question = $('ai-question').value.trim();
  if (question.length > 1000) {$('ai-form-message').textContent = '问题最多 1000 字，请缩短后再试。'; return;}
  aiSubmitting = true;
  $('ai-form-message').textContent = '正在创建任务…';
  renderAIControls();
  const snapshot = Array.from(aiSelected.values(), notice => ({...notice}));
  try {
    const result = await aiRequest('/api/ai/tasks', {notice_ids: snapshot.map(notice => notice.id), question});
    aiTaskSnapshot = snapshot;
    aiTask = {task_id: result.task_id, status: result.status || 'queued', question, progress: {done: 0, total: snapshot.length}, sources: [], failures: []};
    $('ai-task-check').hidden = true;
    $('ai-task-warning').hidden = true;
    $('ai-form-message').textContent = '';
    aiSubmitting = false;
    renderAITask();
    pollAITask();
  } catch (error) {$('ai-form-message').textContent = error.message;}
  finally {aiSubmitting = false; renderAIControls();}
}
$('refresh').addEventListener('click', () => refreshSources());
$('retry-failed').addEventListener('click', () => refreshSources(data.sources.filter(s => ['error','partial'].includes(s.status)).map(s => s.id)));
$('source-select').addEventListener('change', () => {sourceFilter = $('source-select').value; groupFilter = 'all'; filtersChanged();});
$('source-tabs').addEventListener('click', event => {
  const button = event.target.closest('button[data-source]');
  if (!button) return;
  groupFilter = button.dataset.source; sourceFilter = 'all';
  for (const b of $('source-tabs').querySelectorAll('[data-source]')) {
    b.classList.toggle('active', b === button);
    b.setAttribute('aria-pressed', String(b === button));
  }
  filtersChanged();
});
$('toggle-sources').addEventListener('click', () => {
  const grid = $('sources');
  const open = grid.hidden;
  grid.hidden = !open;
  $('toggle-sources').textContent = open ? '收起' : '展开';
  $('toggle-sources').setAttribute('aria-expanded', String(open));
});
$('days').addEventListener('change', filtersChanged);
$('search').addEventListener('input', filtersChanged);
$('show-more').addEventListener('click', () => {visibleCount += 20; render();});
$('mode-normal').addEventListener('click', () => {aiMode = false; render();});
$('mode-ai').addEventListener('click', () => {
  if (!aiMode) selectDefaultNotices();
  aiMode = true;
  if (!selectionInitialized && data?.notices.length) selectDefaultNotices();
  render();
});
$('ai-select-default').addEventListener('click', () => {selectDefaultNotices(); $('ai-form-message').textContent = ''; render();});
$('ai-clear-selection').addEventListener('click', () => {aiSelected.clear(); selectionInitialized = true; $('ai-form-message').textContent = ''; render();});
$('ai-question').addEventListener('input', () => {$('ai-question-length').textContent = `${$('ai-question').value.length} / 1000`;});
$('ai-question-form').addEventListener('submit', askAI);
$('ai-task-check').addEventListener('click', pollAITask);
$('ai-settings-open').addEventListener('click', () => openAISettings());
$('ai-settings-close').addEventListener('click', () => $('ai-settings-dialog').close());
$('ai-settings-dialog').addEventListener('close', () => {
  $('ai-api-key').value = '';
  if (settingsOpener?.isConnected) settingsOpener.focus();
});
$('ai-settings-dialog').addEventListener('cancel', () => {$('ai-api-key').value = '';});
$('ai-settings-form').addEventListener('submit', event => {event.preventDefault(); changeAISettings('save');});
$('ai-settings-test').addEventListener('click', () => changeAISettings('test'));
$('ai-clear-key').addEventListener('click', () => changeAISettings('clear'));
document.addEventListener('keydown', event => {
  if (event.key === '/' && !$('ai-settings-dialog').open && !['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName)) {event.preventDefault(); $('search').focus();}
});
load().catch(handleError);
loadAISettings().catch(() => {aiSettingsLoaded = false; renderAIControls();});
