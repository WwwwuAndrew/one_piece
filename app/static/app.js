/* app/static/app.js —— app 外壳：三个界面（首页 / 看板 / 个股）+ 后台任务的弹框。
   表格本身的渲染和交互来自 table.js（= ui/factor_table.py 里那一份，和命令行完全同源）。 */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const DAY_CHOICES = [5, 10, 15, 20, 30];

const state = {
  view: 'home',        // home | boards | stocks
  days: 10,
  good: false,
  selected: null,      // 看板里选中的板块 {code, name}
  board: null,         // 个股视图所属板块 {code, name}
  jobs: false,         // 是否有任务在跑（跑的时候按钮都禁用）
};

/* ------------------------------------------------------------------ 小工具 */

async function api(path, opts) {
  try {
    const r = await fetch(path, opts);
    const j = await r.json();
    return j;
  } catch (e) {
    return { ok: false, error: '连不上本地服务：' + e.message };
  }
}

const esc = (s) => String(s == null ? '' : s)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

function fmtDay(d) {                    // 20260924 -> 09-24
  const s = String(d || '');
  return s.length === 8 ? s.slice(4, 6) + '-' + s.slice(6) : s;
}

function setNav(active) {
  $$('.top nav button').forEach(b => b.classList.toggle('active', b.dataset.go === active));
}

function hideBar() { $('#bottombar').classList.add('hidden'); }

/* --------------------------------------------------------------- 弹框 */

function openModal(title, bodyHtml, logText, spinning) {
  $('#modal-title').textContent = title;
  $('#modal-body').innerHTML = bodyHtml || '';
  const log = $('#modal-log');
  if (logText === null || logText === undefined) {
    log.classList.add('hidden');
  } else {
    log.classList.remove('hidden');
    log.textContent = logText;
    log.scrollTop = log.scrollHeight;
  }
  $('#modal-spin').classList.toggle('hidden', !spinning);
  $('#modal-mask').classList.remove('hidden');
}

function closeModal() { $('#modal-mask').classList.add('hidden'); }

/* --------------------------------------------------- 后台任务（拉数据） */

async function startJob(name) {
  const r = await api('/api/job', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  });
  if (!r.ok) { openModal('没启动起来', `<span class="bad">${esc(r.error)}</span>`, null, false); return; }
  state.jobs = true;
  setJobButtons(true);
  openModal(r.job.title, '正在运行…（下面的日志实时刷新）', '', true);
  pollJob();
}

function setJobButtons(disabled) {
  $$('button[data-job]').forEach(b => { b.disabled = disabled; });
}

function jobBody(j) {
  if (j.running) return '正在运行…（下面的日志实时刷新）';
  if (j.ok) {
    const w = j.warnings || 0;
    return `<span class="ok">✅ 成功</span>（用了 ${j.seconds} 秒）`
      + (w ? `<br><span class="warn">有 ${w} 条警告</span>，见下面的日志`
           : '');
  }
  const why = j.error || '命令返回了非 0';
  return `<span class="bad">❌ 失败</span>（用了 ${j.seconds} 秒）<br>原因：${esc(why)}`;
}

async function pollJob() {
  const j = await api('/api/job');
  if (j.idle) { state.jobs = false; setJobButtons(false); return; }
  openModal((j.running ? '⏳ ' : (j.ok ? '✅ ' : '❌ ')) + j.title,
            jobBody(j), (j.lines || []).join('\n'), j.running);
  state.jobs = j.running;
  setJobButtons(j.running);
  if (j.running) {
    setTimeout(pollJob, 1000);
  } else {
    refreshStatus();
    if (state.view === 'home') renderHome();
  }
}

/* --------------------------------------------------------------- 顶栏状态 */

async function refreshStatus() {
  const s = await api('/api/state');
  const el = $('#status');
  if (!s.ok) { el.textContent = '读不到本地库'; return; }
  el.innerHTML = s.has_market
    ? `行情到 <b>${s.stock_to || '—'}</b> · ${s.stock_days} 个交易日 · 板块 ${s.boards_l2} 二级 / ${s.boards_l1} 一级`
    : '本地还没有行情数据';
}

/* --------------------------------------------------------------- 首页 */

async function renderHome() {
  state.view = 'home';
  state.board = null; state.selected = null;
  hideBar(); setNav('home');
  const s = await api('/api/state');
  const kv = !s.ok ? '<span class="warn">读不到本地库</span>' : `
    <div>个股日线：<b>${s.stock_rows.toLocaleString()}</b> 行 / <b>${s.stock_days}</b> 个交易日
      <span class="kv-sub">（${s.stock_from || '—'} ~ ${s.stock_to || '—'}）</span></div>
    <div>板块日线：<b>${s.board_rows.toLocaleString()}</b> 行 / <b>${s.board_concepts}</b> 个板块
      <span class="kv-sub">${s.board_from ? '（最新 ' + s.board_from + '）' : ''}</span></div>
    <div>板块层级：一级 <b>${s.boards_l1}</b> / 二级 <b>${s.boards_l2}</b></div>
    <div>自选：<b>${s.watch}</b> 个</div>`;

  $('#view').innerHTML = `
    <div class="cards">
      <div class="card">
        <h2>① 拉取数据</h2>
        <p class="muted">每天收盘后点这两下就够。点完会弹框，成功失败和原因都写在里面。</p>
        <button class="primary big" data-job="fetch_market">拉取行情（fetch market）</button>
        <button class="primary big" data-job="fetch_board">计算板块（fetch board）</button>
        <button data-job="update_board">更新板块定义（update board · 约 27 分钟）</button>
      </div>
      <div class="card">
        <h2>② 看看板</h2>
        <p class="muted">二级板块的因子数字表 → 点某个板块 → 进它内部的个股。</p>
        <button class="primary big" data-go="boards">打开看板 →</button>
      </div>
      <div class="card">
        <h2>库里现在有什么</h2>
        <div class="kv">${kv}</div>
      </div>
    </div>`;
  $$('#view button[data-job]').forEach(b => { b.disabled = state.jobs; });
}

/* --------------------------------------------------------------- 看板 */

function toolbar(backLabel, backView, title, extra) {
  const opts = DAY_CHOICES.map(d =>
    `<option value="${d}"${d === state.days ? ' selected' : ''}>${d}</option>`).join('');
  return `
    <div class="toolbar">
      <button class="ghost" data-go="${backView}">← ${backLabel}</button>
      <span class="title">${esc(title)}</span>
      <label>最近 <select id="days">${opts}</select> 天</label>
      <label class="switch"><input type="checkbox" id="good"${state.good ? ' checked' : ''}> good 筛选</label>
      <button class="ghost" id="reload">刷新</button>
      ${extra || ''}
      <span class="note" id="tb-note"></span>
    </div>
    <details class="legend-box"><summary>这张表怎么读</summary><div id="legend"></div></details>
    <div class="wrap">
      <div class="left" id="grid"><div class="loading">加载中…</div></div>
      <div class="right" id="detail"></div>
    </div>`;
}

function bindToolbar(reload) {
  $('#days').addEventListener('change', e => { state.days = +e.target.value; reload(); });
  $('#good').addEventListener('change', e => { state.good = e.target.checked; reload(); });
  $('#reload').addEventListener('click', () => reload());
}

async function renderBoards() {
  state.view = 'boards';
  state.board = null; state.selected = null;
  hideBar(); setNav('boards');
  $('#view').innerHTML = toolbar('首页', 'home', '板块因子表');
  bindToolbar(loadBoards);
  await loadBoards();
}

async function loadBoards() {
  const grid = $('#grid');
  grid.innerHTML = '<div class="loading">加载中…（首次点开要算 100 多个板块，几秒）</div>';
  const v = await api(`/api/boards?days=${state.days}&good=${state.good ? 1 : 0}`);
  if (!v.ok) {
    grid.innerHTML = `<div class="hint" style="padding:20px">${esc(v.reason || v.error || '没有数据')}</div>`;
    $('#detail').innerHTML = ''; $('#legend').innerHTML = ''; $('#tb-note').textContent = '';
    return;
  }
  grid.innerHTML = v.grid;
  $('#detail').innerHTML = v.right;
  $('#legend').innerHTML = v.legend;
  $('#tb-note').textContent = `${v.stats.span} · ${v.stats.kept} 个板块`;
  initFactorTable({ detail: v.detail, parentOf: v.parent_of, names: v.names, mode: 'boards' });
}

/* --------------------------------------------------------------- 个股 */

async function renderStocks(code, name) {
  state.view = 'stocks';
  state.board = { code, name };
  state.selected = null;
  hideBar(); setNav('boards');
  $('#view').innerHTML = toolbar('返回看板', 'boards', `${name || code} 的成分股`);
  bindToolbar(() => loadStocks(code));
  await loadStocks(code);
}

async function loadStocks(code) {
  const grid = $('#grid');
  grid.innerHTML = '<div class="loading">加载中…（这个板块的每只票都要算 RS 和成本，几秒）</div>';
  const v = await api(`/api/stocks?code=${encodeURIComponent(code)}&days=${state.days}&good=${state.good ? 1 : 0}`);
  if (!v.ok) {
    grid.innerHTML = `<div class="hint" style="padding:20px">${esc(v.reason || v.error || '没有数据')}</div>`;
    $('#detail').innerHTML = ''; $('#legend').innerHTML = ''; $('#tb-note').textContent = '';
    return;
  }
  grid.innerHTML = v.grid;
  $('#detail').innerHTML = v.right;
  $('#legend').innerHTML = v.legend;
  $('#tb-note').textContent = `${v.stats.span} · ${v.stats.kept} 只`;
  initFactorTable({ detail: {}, parentOf: null, names: v.names, mode: 'stocks' });
}

/* ------------------------------------------------- 表格里点了某个格子 */

window.onFactorPick = function (code, date, name) {
  if (state.view !== 'boards' || !code) { hideBar(); state.selected = null; return; }
  state.selected = { code, name: name || code };
  $('#sel-text').innerHTML =
    `已选中 <b>${esc(name || code)}</b><span class="code">${esc(code)}</span> · ${fmtDay(date)}`;
  $('#bottombar').classList.remove('hidden');
};

/* --------------------------------------------------------------- 路由 */

function go(view, arg) {
  if (view === 'home') return renderHome();
  if (view === 'boards') return renderBoards();
  if (view === 'stocks') {
    const b = arg || state.board || state.selected;
    if (!b) return renderBoards();
    return renderStocks(b.code, b.name);
  }
}

document.addEventListener('click', (e) => {
  const nav = e.target.closest('[data-go]');
  if (nav) { go(nav.dataset.go); return; }
  const job = e.target.closest('button[data-job]');
  if (job) { startJob(job.dataset.job); return; }
});

$('#btn-detail').addEventListener('click', () => {
  if (state.selected) go('stocks', state.selected);
});
$('#btn-clear').addEventListener('click', () => {
  state.selected = null;
  hideBar();
  // 同时把表格里的高亮也清掉：再点一次同一格就是取消
  const on = document.querySelector('.grid td.cell.sel');
  if (on) on.click();
});
$('#modal-close').addEventListener('click', closeModal);
$('#modal-mask').addEventListener('click', (e) => {
  if (e.target === $('#modal-mask') && !state.jobs) closeModal();
});

/* 启动：先看有没有任务在跑（比如刷新了页面），再去首页 */
(async function boot() {
  await refreshStatus();
  const j = await api('/api/job');
  if (j && !j.idle && j.running) { state.jobs = true; await renderHome(); pollJob(); return; }
  await renderHome();
})();
