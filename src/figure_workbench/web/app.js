'use strict';
let board, selectedId, previewId, busy = false, toastTimer;
const $ = selector => document.querySelector(selector);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const current = task => task.submissions.find(s => s.id === task.current_submission);
const statusNames = {planned:'待开始',working:'制图中',review:'待人工验收',rework:'待返工',accepted:'已验收',blocked:'自动检查阻塞'};
const displayStatus = task => task.status === 'review' && current(task)?.checks.some(c => c.level === 'error') ? 'blocked' : task.status;
const pill = status => `<span class="pill ${status}">${statusNames[status] || esc(status)}</span>`;
const figure = submission => submission?.files.find(f => f.role === 'figure');
const image = submission => figure(submission) ? `<img src="/api/files/${figure(submission).id}" alt="${esc(submission.version)} 图件预览">` : '<span class="muted">尚未提交PNG图件</span>';
const stamp = value => new Date(value).toLocaleString();

function notify(message) {
  (document.querySelector('dialog[open]') || document.body).appendChild($('#toast'));
  $('#toast').textContent = message;
  $('#toast').hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $('#toast').hidden = true, 6000);
}
async function request(url, payload) {
  const response = await fetch(url, payload === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...payload,expected_revision:board.revision})});
  const result = await response.json();
  if (!response.ok) {
    if (response.status === 409) await load(false);
    throw new Error(result.error || `HTTP ${response.status}`);
  }
  return result;
}
async function action(url, payload, message) {
  if (busy) return;
  busy = true;
  try {
    const result = await request(url, payload);
    await load(false);
    if (selectedId && $('#detail').open) await showDetail(selectedId);
    notify(message);
    return result;
  } catch (e) {
    notify(e.message);
    return null;
  } finally {busy = false;}
}
async function load(announce = false) {
  board = await request('/api/state');
  renderBoard();
  if (announce) notify('已读取最新工作台状态');
}
function renderBoard() {
  const tasks = Object.values(board.tasks), accepted = tasks.filter(t => t.status === 'accepted').length;
  $('#total').textContent = tasks.length;
  $('#accepted').textContent = `${accepted} / ${tasks.length}`;
  $('#blocked').textContent = tasks.filter(t => displayStatus(t) === 'blocked').length;
  $('#release-count').textContent = board.releases.length;
  $('#revision').textContent = `工作台版本 ${board.revision}`;
  $('#freeze').disabled = !tasks.length || accepted !== tasks.length;
  $('#freeze').title = accepted === tasks.length ? '重新核验全部字节并冻结当前验收版本' : `还有 ${tasks.length-accepted} 张当前图件未验收通过`;
  const owners = [...new Set(tasks.map(t => t.owner))].sort(), chosen = $('#owner-filter').value;
  $('#owner-filter').innerHTML = '<option value="">全部制图者</option>' + owners.map(o => `<option value="${esc(o)}">${esc(o)}</option>`).join('');
  $('#owner-filter').value = chosen;
  $('#owners').innerHTML = owners.map(o => `<div class="owner-line"><span>${esc(o)}</span><b>${tasks.filter(t => t.owner === o && ['working','review','rework'].includes(t.status)).length} / ${board.max_active_per_owner}</b></div>`).join('');
  const query = $('#search').value.toLowerCase(), owner = $('#owner-filter').value;
  const shown = tasks.filter(t => (!owner || t.owner === owner) && `${t.title} ${t.id} ${t.owner}`.toLowerCase().includes(query));
  const columns = [{title:'待开始',statuses:['planned']},{title:'制图中',statuses:['working']},{title:'检查与验收',statuses:['review','rework']},{title:'已验收',statuses:['accepted']}];
  $('#board').innerHTML = columns.map(column => {
    const list = shown.filter(t => column.statuses.includes(t.status));
    return `<div class="column"><div class="column-head">${column.title}<span>${list.length}</span></div><div class="cards">${list.map(t => {
      const sub = current(t), state = displayStatus(t), errors = sub?.checks.filter(c => c.level === 'error').length || 0;
      return `<button class="card" data-task="${t.id}"><div class="card-top"><span>${esc(t.owner)}</span>${pill(state)}</div><h3>${esc(t.title)}</h3><p>${sub ? esc(sub.version) + ' · 第' + sub.number + '次提交' : '等待开始与首版提交'}</p><div class="thumbnail ${sub ? '' : 'empty'}">${sub ? image(sub) : 'FIGURE PENDING'}</div><div class="card-bottom"><span>${errors ? errors + '项自动检查错误' : sub ? '自动检查通过' : '每人最多两张在途'}</span><span>${t.reviews.length ? t.reviews.length + '次验收记录' : '查看要求 →'}</span></div></button>`;
    }).join('') || '<div class="empty-column">暂无图件</div>'}</div></div>`;
  }).join('');
  document.querySelectorAll('[data-task]').forEach(button => button.addEventListener('click', () => showDetail(button.dataset.task).catch(e => notify(e.message))));
}
async function showDetail(taskId, versionId) {
  selectedId = taskId;
  const task = board.tasks[taskId];
  if (!task) return;
  previewId = versionId || task.current_submission;
  const sub = task.submissions.find(s => s.id === previewId), latest = sub?.id === task.current_submission;
  const live = latest && sub ? await request(`/api/tasks/${taskId}/inspect`) : null;
  const checks = live?.checks || sub?.checks || [], errorCount = checks.filter(c => c.level === 'error').length;
  $('#detail-title').textContent = task.title;
  $('#detail-body').innerHTML = `<div class="detail-bar"><div>${pill(displayStatus(task))} <span class="muted">${esc(task.owner)} · ${task.id}</span></div><select id="version-select" aria-label="选择图件版本">${task.submissions.map(s => `<option value="${s.id}" ${s.id === previewId ? 'selected' : ''}>${esc(s.version)}${s.id === task.current_submission ? ' · 当前版本' : ' · 历史版本'}</option>`).join('') || '<option>尚无版本</option>'}</select></div>
  <div class="detail-grid"><div><div class="figure-preview">${image(sub)}</div><p class="brief">${esc(task.brief)}</p><div class="section-title">图注</div><div class="caption">${esc(sub?.caption || '提交时补充图注、单位与统计口径。')}</div>${sub && task.submissions.indexOf(sub) > 0 ? '<button class="button light" id="compare">对比上个版本</button><div id="comparison" hidden></div>' : ''}<div class="section-title">证据文件</div>${sub?.files.map(f => `<div class="file-row"><div><a href="/api/files/${f.id}" download="${esc(f.name)}">${esc(f.name)}</a><span class="hash">${esc(f.sha256)}</span></div><span>${(f.size/1024).toFixed(1)} KB</span></div>`).join('') || '<p class="muted">尚无文件</p>'}<div class="section-title">验收留痕</div>${task.reviews.slice().reverse().map(r => `<div class="history-row">${r.decision === 'accept' ? '通过' : '返工'} · ${esc(task.submissions.find(s => s.id === r.submission_id)?.version)}<p>${esc(r.note)}</p><span class="muted">${stamp(r.created_at)}</span></div>`).join('') || '<p class="muted">尚无人工验收决定。</p>'}</div>
  <div><div class="section-title">自动检查 · ${latest ? '当前字节复核' : '该版本提交时记录'}</div>${checks.map(c => `<div class="check"><span class="pill ${c.level}">${{pass:'通过',error:'错误',info:'说明'}[c.level]}</span><span>${esc(c.message)}</span></div>`).join('') || '<p class="muted">提交后检查PNG、CSV与文件哈希。</p>'}<p class="muted">自动检查不判断科学结论或图像与数据语义一致性。</p><div class="section-title">人工验收</div><form id="review-form"><div class="checklist"><label><input type="checkbox" name="visual">已检查视觉可读性、轴与单位</label><label><input type="checkbox" name="data">已核对数据来源、覆盖与统计口径</label><label><input type="checkbox" name="caption">已核对图注、版本与论文描述</label></div><label class="muted" for="review-note">验收或返工备注</label><textarea id="review-note" name="note" required maxlength="2000" placeholder="记录具体检查依据或需修订的位置"></textarea><div class="review-actions"><button class="button teal" data-decision="accept" type="submit" ${!latest || task.status !== 'review' || errorCount ? 'disabled' : ''}>确认通过当前版本</button><button class="button danger" data-decision="rework" type="submit" ${!latest || task.status !== 'review' ? 'disabled' : ''}>退回返工</button></div></form>${!latest && sub ? '<p class="muted">当前预览历史版本；签署须切换到当前待验收版本。</p>' : ''}${['planned','rework'].includes(task.status) ? '<p><button class="button primary" id="start">开始'+(task.status === 'rework' ? '返工' : '任务')+'</button></p>' : ''}
  <details><summary>提交新版本</summary><form id="submit-form" class="form"><label>新版本标签<input name="version" required maxlength="120" placeholder="如 v2 / 数据或代码版本标识"></label><label>图注与统计口径<textarea name="caption" required maxlength="2000"></textarea></label><label>PNG图件<input type="file" name="figure" accept=".png" required></label><label>来源CSV（series,x,value）<input type="file" name="data" accept=".csv" required></label><label>可选源码（归档，不执行）<input type="file" name="code" accept=".py,.m,.tex,.txt"></label><button class="button primary" type="submit" ${task.status === 'planned' ? 'disabled' : ''}>提交并自动检查</button><span class="muted">单个文件最多8MiB；新版本会撤销当前的通过状态，保留历史记录。</span></form></details></div></div>`;
  $('#version-select').addEventListener('change', e => showDetail(taskId, e.target.value).catch(e => notify(e.message)));
  $('#start')?.addEventListener('click', () => action(`/api/tasks/${taskId}/start`, {}, '任务已开始'));
  $('#compare')?.addEventListener('click', () => {
    const previous = task.submissions[task.submissions.indexOf(sub)-1];
    $('#comparison').hidden = false;
    $('#comparison').innerHTML = `<div class="comparison-preview"><div><label>${esc(previous.version)}</label>${image(previous)}</div><div><label>${esc(sub.version)}</label>${image(sub)}</div></div>`;
  });
  $('#review-form').addEventListener('submit', async e => {
    e.preventDefault();
    const decision = e.submitter.dataset.decision, form = e.currentTarget;
    const checklist = Object.fromEntries(['visual','data','caption'].map(k => [k,form.elements[k].checked]));
    if (decision === 'accept' && Object.values(checklist).some(v => !v)) return notify('请人工确认三个验收项目后再通过');
    await action(`/api/tasks/${taskId}/review`, {submission_id:sub.id,decision,note:form.elements.note.value,checklist}, decision === 'accept' ? '当前版本已验收通过' : '已记录返工要求');
  });
  $('#submit-form').addEventListener('submit', async e => {
    e.preventDefault();
    const form = e.currentTarget;
    try {
      const files = [];
      for (const role of ['figure','data','code']) {
        const file = form.elements[role].files[0];
        if (!file) continue;
        if (file.size > 8*1024*1024) throw new Error('单个文件超过8MiB');
        const encoded = await new Promise((resolve,reject) => {const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(new Error('文件读取失败'));reader.readAsDataURL(file);});
        files.push({role,name:file.name,data_base64:encoded});
      }
      await action(`/api/tasks/${taskId}/submit`, {version:form.elements.version.value,caption:form.elements.caption.value,files}, '新版本已提交，请查看自动检查结果');
    } catch(e) {notify(e.message);}
  });
  if (!$('#detail').open) $('#detail').showModal();
}
function showList(title, content) {
  $('#list-title').textContent = title;
  $('#list-body').innerHTML = content;
  $('#list-dialog').showModal();
}
document.querySelectorAll('[data-close]').forEach(button => button.addEventListener('click', () => document.getElementById(button.dataset.close).close()));
$('#refresh').addEventListener('click', () => load(true).catch(e => notify(e.message)));
$('#search').addEventListener('input', renderBoard);
$('#owner-filter').addEventListener('change', renderBoard);
$('#new-task').addEventListener('click', () => $('#create').showModal());
$('#create-form').addEventListener('submit', async e => {
  e.preventDefault();
  const form = e.currentTarget;
  const result = await action('/api/tasks', {title:form.elements.title.value,owner:form.elements.owner.value,brief:form.elements.brief.value}, '任务已创建');
  if (result) {$('#create').close();form.reset();await showDetail(result.result.task_id);}
});
$('#freeze').addEventListener('click', async () => {
  const result = await action('/api/releases', {}, '当前验收版本已冻结');
  if (result) showPackages();
});
function showPackages() {
  showList('冻结图包', board.releases.slice().reverse().map(r => `<div class="history-row"><a href="/api/releases/${r.id}.zip">下载图包 · ${r.figure_count}张图件</a><p>来源工作台版本 ${r.source_board_revision} · ${stamp(r.created_at)}</p><code>SHA-256: ${esc(r.sha256)}</code></div>`).join('') || '<p class="muted">全部当前版本人工验收通过后，可冻结固定图包。</p>');
}
$('#packages-nav').addEventListener('click', showPackages);
$('#history-nav').addEventListener('click', () => showList('操作留痕 · 最近80条', board.events.map(event => `<div class="history-row">#${event.seq} · ${esc({create:'创建任务',start:'开始任务',submit:'提交版本',review:'人工验收',freeze:'冻结图包'}[event.kind] || event.kind)} · 工作台版本 ${event.revision}<span class="muted"> · ${stamp(event.at)}</span><code>${esc(JSON.stringify(event.payload))}</code></div>`).join('') || '<p class="muted">暂无记录。</p>'));
$('#board-nav').addEventListener('click', () => load(true).catch(e => notify(e.message)));
load().catch(e => notify('无法读取工作台：'+e.message));
