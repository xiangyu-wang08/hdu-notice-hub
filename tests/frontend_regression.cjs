const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const root = require('node:path').join(__dirname, '..', 'web') + '/';
class Element {
  constructor(tag = 'div') {this.tagName = tag.toUpperCase(); this.children = []; this.textContent = ''; this.value = ''; this.hidden = false; this.attributes = {}; this.dataset = {}; this.style = {}; this.classList = {toggle() {}, remove() {}};}
  append(...children) {this.children.push(...children);}
  prepend(...children) {this.children.unshift(...children);}
  replaceChildren(...children) {this.children = children; this.textContent = '';}
  setAttribute(name, value) {this.attributes[name] = value;}
  removeAttribute(name) {delete this.attributes[name];}
  addEventListener(name, callback) {(this.events ||= {})[name] = callback;}
  querySelectorAll() {return [];}
  focus() {}
  showModal() {this.open = true;}
  close() {this.open = false;}
}
const nodes = new Map([...fs.readFileSync(root + 'index.html', 'utf8').matchAll(/id="([^"]+)"/g)].map(match => [match[1], new Element()]));
nodes.get('days').value = '30';
const requests = [];
let noticeState = null;
let saved = {base_url: '', model: '', key_saved: false, configured: false};
let taskStatus = 200, taskBody = {};
let deferTaskPolls = false, deferredTaskRequests = [];
const context = vm.createContext({URL, Intl, Date, Map, Set, Number, String, Boolean, Object, Array, Math, JSON, Error, encodeURIComponent,
  setTimeout: () => 1, clearTimeout() {},
  document: {getElementById: id => nodes.get(id), createElement: tag => new Element(tag), addEventListener() {}, activeElement: new Element('body')},
  fetch: async (url, options = {}) => {
    const payload = options.body ? JSON.parse(options.body) : undefined;
    requests.push({url, payload});
    let result;
    if (url === '/api/state') result = noticeState || {today: '2026-10-19', sources: [], notices: []};
    else if (url === '/api/ai/test') result = {ok: true, message: '连接测试成功'};
    else if (url === '/api/ai/settings' && !payload) result = saved;
    else if (url === '/api/ai/settings') {
      saved = {base_url: payload.base_url, model: payload.model, key_saved: Boolean(payload.api_key) || saved.key_saved};
      saved.configured = Boolean(saved.base_url && saved.model && saved.key_saved);
      result = saved;
    } else if (url.startsWith('/api/ai/tasks/')) {
      if (deferTaskPolls) return new Promise(resolve => deferredTaskRequests.push({url, resolve}));
      return {ok: taskStatus < 400, status: taskStatus, json: async () => taskBody};
    }
    else throw new Error('unexpected request');
    return {ok: true, status: 200, json: async () => result};
  }});
vm.runInContext(fs.readFileSync(root + 'app.js', 'utf8'), context);
const run = expression => vm.runInContext(expression, context);
const textOf = node => node.textContent + node.children.map(textOf).join('');
async function keyDraftTest() {
  await new Promise(resolve => setImmediate(resolve));
  for (const keySaved of [false, true]) {
    saved = {base_url: 'https://ai.example.test/v1', model: 'qa-model', key_saved: keySaved, configured: keySaved};
    context.currentSettings = saved; run('applyAISettings(currentSettings)');
    nodes.get('ai-base-url').value = saved.base_url;
    nodes.get('ai-model').value = saved.model;
    nodes.get('ai-api-key').value = 'qa-dummy-never-real';
    await run("changeAISettings('test')");
    assert.equal(nodes.get('ai-api-key').value, 'qa-dummy-never-real', 'Test must retain the unsaved Key for Save');
    await run("changeAISettings('save')");
    assert.equal(requests.at(-1).payload.api_key, 'qa-dummy-never-real', 'Save must send the exact newly tested draft Key');
    assert.equal(nodes.get('ai-api-key').value, '', 'Successful Save clears the Key input');
    assert.equal(nodes.get('ai-config-status').textContent, 'AI 已配置');
  }
}
async function referenceTest() {
  context.snapshots = ['a', 'b', 'c'].map(id => ({id, title: '通知' + id, url: 'https://example.test/' + id, date: '2026-10-19', source_name: '官方来源'}));
  context.taskSources = context.snapshots.map((source, index) => ({...source, number: index + 1, used_chars: 0}));
  run("aiTaskSnapshot = snapshots; aiTask = {status: 'queued', progress: {done: 0,total: 3}, sources: taskSources, failures: []}; renderAITask()");
  assert.equal(nodes.get('ai-references').children.length, 3);
  assert(!textOf(nodes.get('ai-references')).includes('已读取正文'), 'Queued sources must not claim their bodies are read');
  assert(textOf(nodes.get('ai-references')).includes('待读取'), 'Queued sources show pending state');
  run("aiTask = {status: 'failed', progress: {done: 3,total: 3}, sources: [{...taskSources[0],used_chars: 123},taskSources[1],taskSources[2]], failures: [{id:'b',title:'通知b',reason:'正文不可用'}]}; renderAITask()");
  const rows = nodes.get('ai-references').children;
  assert.equal(rows.length, 3, 'A failed source is represented once, without an extra success row');
  assert(textOf(rows[0]).includes('已读取正文'));
  assert(textOf(rows[1]).includes('正文不可用') && !textOf(rows[1]).includes('已读取正文'));
  assert(textOf(rows[2]).includes('未读取正文') && !textOf(rows[2]).includes('已读取正文'));
}
async function missingKeySaveTest() {
  saved = {base_url: 'https://ai.example.test/v1', model: 'qa-model', key_saved: false, configured: false};
  context.currentSettings = saved; run('applyAISettings(currentSettings)');
  nodes.get('ai-api-key').value = '';
  const before = requests.length;
  await run("changeAISettings('save')");
  assert.equal(requests.length, before, 'Missing first Key must be rejected before saving');
  assert(nodes.get('ai-settings-message').textContent.includes('API Key'), 'The missing field is explained');
}
async function expiredTaskTest() {
  taskStatus = 404; taskBody = {error: '任务不存在或已过期'};
  run("aiTask = {task_id:'expired',status:'reading',progress:{done:0,total:3},sources:[],failures:[]}");
  await run('pollAITask()');
  assert.equal(run('aiTaskRunning()'), false, 'Expired task must release the single-operation lock');
  assert.equal(nodes.get('ai-settings-save').disabled, false, 'Settings can be used after task expiry');
  assert.equal(nodes.get('ai-task-check').hidden, true, 'Expired task must not offer endless polling');
  assert.equal(nodes.get('ai-task-progress').hidden, true, 'Expired task hides active progress');
  taskStatus = 502; taskBody = {error: '暂时无法读取'};
  run("aiTask = {task_id:'temporary',status:'reading',progress:{done:0,total:3},sources:[],failures:[]}");
  const postsBefore = requests.filter(request => request.url === '/api/ai/tasks').length;
  await run('pollAITask()');
  assert.equal(run('aiTaskRunning()'), true, 'Temporary failure keeps task pending for manual resume');
  assert.equal(nodes.get('ai-task-check').hidden, false);
  assert.equal(requests.filter(request => request.url === '/api/ai/tasks').length, postsBefore, 'Polling recovery never resubmits the task');
}
function taskResponse(id, status = 200) {
  return {ok: status < 400, status, json: async () => status >= 400 ? {error: '任务已过期'} : {task_id: id, status: 'succeeded', sources: [], failures: [], answer: id + '回答'}};
}
async function duplicatePollTest() {
  deferTaskPolls = true; deferredTaskRequests = [];
  run("aiTask = {task_id:'double',status:'reading',sources:[],failures:[]}");
  const first = run('pollAITask()'), second = run('pollAITask()');
  try {assert.equal(deferredTaskRequests.length, 1, 'Double Continue must send a single GET for the current task');}
  finally {
    for (const request of deferredTaskRequests) request.resolve(taskResponse('double'));
    await Promise.all([first, second]); deferTaskPolls = false;
  }
}
async function stalePollTest(status) {
  deferTaskPolls = true; deferredTaskRequests = [];
  run("aiTask = {task_id:'old',status:'reading',sources:[],failures:[]}; aiTaskSnapshot = [{id:'old-notice',title:'旧通知'}]");
  const oldPoll = run('pollAITask()');
  run("aiTask = {task_id:'new',status:'reading',sources:[],failures:[]}; aiTaskSnapshot = [{id:'new-notice',title:'新通知'}]");
  const newPoll = run('pollAITask()');
  let thirdPoll;
  try {
    deferredTaskRequests[0].resolve(taskResponse('old', status));
    await oldPoll;
    assert.equal(run('aiTask.task_id'), 'new', 'A stale success or 404 must never replace the new task');
    assert.equal(run('aiTask.status'), 'reading', 'A stale 404 must never fail the new task');
    assert.equal(run('aiTaskSnapshot[0].id'), 'new-notice');
    thirdPoll = run('pollAITask()');
    assert.equal(deferredTaskRequests.length, 2, 'Old finally cleanup must preserve the new task in-flight guard');
  } finally {
    for (const request of deferredTaskRequests) request.resolve(taskResponse(request.url.endsWith('/new') ? 'new' : 'old'));
    await Promise.all([oldPoll, newPoll, thirdPoll]); deferTaskPolls = false;
  }
}
async function defaultSelectionTest() {
  await new Promise(resolve => setImmediate(resolve));
  const notices = Array.from({length: 8}, (_, i) => ({id: 'n' + i, source_id: 'jwc', title: '通知' + i, date: '2026-10-19', url: 'https://jwc.hdu.edu.cn/' + i}));
  context.fullNotices = notices;
  run('aiMode=false; selectionInitialized=false; aiSelected.clear()');
  noticeState = {today:'2026-10-19',sources:[],notices:notices.slice(0,1)};
  await run('load()');
  noticeState = {...noticeState,notices};
  await run('load()');
  nodes.get('mode-ai').events.click();
  assert.equal(run('aiSelected.size'),5,'First AI entry uses the latest results, not an early partial response');
  run('aiSelected.clear(); selectionInitialized=true; render()');
  assert.equal(run('aiSelected.size'),0,'Explicit clear remains empty across polling renders');
  run('aiMode=false; data.notices=[]; filtersChanged()');
  run('data.notices=fullNotices; render()');
  nodes.get('mode-ai').events.click();
  assert.equal(run('aiSelected.size'),5,'An empty normal-search filter update cannot freeze default selection');
}
(async () => {
  const results = [];
  for (const [name, test] of [['Default selection after incremental data arrival', defaultSelectionTest], ['Key draft survives Test → Save for fresh and existing config', keyDraftTest], ['Save without a first Key explains the missing field', missingKeySaveTest], ['Reference states truthful and failure rows unique', referenceTest], ['Expired task unlocks UI while 5xx keeps manual resume', expiredTaskTest], ['Double Continue creates only one task GET', duplicatePollTest], ['Late old success cannot overwrite a new task or clear its polling guard', () => stalePollTest(200)], ['Late old 404 cannot fail a new task or clear its polling guard', () => stalePollTest(404)]]) {
    try {await test(); results.push({name, pass: true});}
    catch (error) {results.push({name, pass: false, error: error.message}); process.exitCode = 1;}
  }
  process.stdout.write(JSON.stringify(results, null, 2));
})();
