/* QuantAgent web UI — client.
   No framework, no build step. Talks to /api/analyze then follows the SSE
   stream, rendering agent status and reports as the graph advances. */

'use strict';

const $ = (id) => document.getElementById(id);

/* Report fields we render, in the order the tabs appear. */
const REPORTS = [
  { key: 'market_report',        label: '技术分析' },
  { key: 'news_report',          label: '新闻' },
  { key: 'fundamentals_report',  label: '基本面' },
  { key: 'sentiment_report',     label: '情绪' },
  { key: 'bull_history',         label: '多头论点' },
  { key: 'bear_history',         label: '空头论点' },
  { key: 'judge_decision',       label: '研究主管裁决' },
  { key: 'trader_investment_plan', label: '交易计划' },
  { key: 'aggressive_history',   label: '激进派' },
  { key: 'conservative_history', label: '保守派' },
  { key: 'neutral_history',      label: '中立派' },
  { key: 'final_trade_decision', label: '最终决策' },
];

const state = {
  teams: [],
  statusZh: { pending: '等待中', running: '分析中', done: '已完成' },
  status: {},      // agent key -> pending | running | done
  snapshot: {},    // state field -> text
  activeTab: null,
  source: null,    // EventSource
  runId: null,
  running: false,
  startedAt: null,
  tickTimer: null,
  cancelled: false,
};

/* ------------------------------------------------------------------ utils */

function escapeHtml(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

/* Minimal markdown -> HTML. Input is LLM output, so everything is escaped
   before any markup is reintroduced. */
function renderMarkdown(src) {
  if (!src) return '<p class="muted">（暂无内容）</p>';
  const lines = escapeHtml(src).split('\n');
  const out = [];
  let inTable = false, inList = false, inCode = false, codeBuf = [];

  const closeList  = () => { if (inList)  { out.push('</ul>'); inList = false; } };
  const closeTable = () => {
    if (!inTable) return;
    out.push('</tbody></table>');
    inTable = false;
  };

  for (const raw of lines) {
    const line = raw.trimEnd();

    if (/^```/.test(line)) {
      if (inCode) { out.push('<pre><code>' + codeBuf.join('\n') + '</code></pre>'); codeBuf = []; inCode = false; }
      else { closeList(); closeTable(); inCode = true; }
      continue;
    }
    if (inCode) { codeBuf.push(raw); continue; }

    if (!line.trim()) { closeList(); closeTable(); continue; }

    // table row
    if (/^\|.*\|$/.test(line)) {
      const cells = line.slice(1, -1).split('|').map((c) => c.trim());
      if (/^[\s|:-]+$/.test(line)) continue;               // separator row
      if (!inTable) {
        closeList();
        out.push('<table><thead><tr>' + cells.map((c) => `<th>${inline(c)}</th>`).join('') + '</tr></thead><tbody>');
        inTable = true;
      } else {
        out.push('<tr>' + cells.map((c) => `<td>${inline(c)}</td>`).join('') + '</tr>');
      }
      continue;
    }
    closeTable();

    let m;
    if ((m = line.match(/^(#{1,6})\s+(.*)$/))) {
      closeList();
      const lvl = Math.min(m[1].length, 4);
      out.push(`<h${lvl}>${inline(m[2])}</h${lvl}>`);
    } else if ((m = line.match(/^\s*[-*]\s+(.*)$/))) {
      if (!inList) { out.push('<ul>'); inList = true; }
      out.push(`<li>${inline(m[1])}</li>`);
    } else if ((m = line.match(/^\s*(\d+)\.\s+(.*)$/))) {
      if (!inList) { out.push('<ul>'); inList = true; }
      out.push(`<li>${inline(m[2])}</li>`);
    } else if ((m = line.match(/^>\s?(.*)$/))) {
      closeList();
      out.push(`<blockquote>${inline(m[1])}</blockquote>`);
    } else if (/^(-{3,}|\*{3,})$/.test(line)) {
      closeList();
      out.push('<hr>');
    } else {
      closeList();
      out.push(`<p>${inline(line)}</p>`);
    }
  }
  if (inCode && codeBuf.length) out.push('<pre><code>' + codeBuf.join('\n') + '</code></pre>');
  closeList();
  closeTable();
  return out.join('\n');

  /* inline: **bold**, *italic*, `code` — applied after escaping */
  function inline(s) {
    return s
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
  }
}

/* ------------------------------------------------------------- agent board */

function buildAgentBoard(teams) {
  const board = $('agentBoard');
  board.innerHTML = teams.map((team) => `
    <div class="team" data-team="${escapeHtml(team.key)}">
      <div class="team-head">
        <span>${escapeHtml(team.label_zh)}</span>
        <span class="muted" style="text-transform:none;letter-spacing:0">${escapeHtml(team.label)}</span>
        <span class="team-progress" data-progress="${escapeHtml(team.key)}">0/${team.agents.length}</span>
      </div>
      <div class="agent-grid">
        ${team.agents.map((a) => `
          <div class="agent is-pending" data-agent="${escapeHtml(a.key)}" title="${escapeHtml(state.statusZh.pending || 'pending')}">
            <span class="agent-dot"></span>
            <span class="agent-name">${escapeHtml(a.label_zh)}</span>
          </div>`).join('')}
      </div>
    </div>`).join('');
}

function applyStatus(status) {
  state.status = status;
  for (const [key, value] of Object.entries(status)) {
    const el = document.querySelector(`[data-agent="${CSS.escape(key)}"]`);
    if (!el) continue;
    el.classList.remove('is-pending', 'is-running', 'is-done');
    el.classList.add('is-' + value);
    el.title = state.statusZh[value] || value;
  }
  for (const team of state.teams) {
    const done = team.agents.filter((a) => state.status[a.key] === 'done').length;
    const p = document.querySelector(`[data-progress="${CSS.escape(team.key)}"]`);
    if (p) p.textContent = `${done}/${team.agents.length}`;
  }
}

/* ---------------------------------------------------------------- reports */

function buildReportTabs() {
  const tabs = $('reportTabs');
  const first = REPORTS.find((r) => (state.snapshot[r.key] || '').trim());
  state.activeTab = first ? first.key : 'market_report';
  renderTabs();
}

function renderTabs() {
  $('reportTabs').innerHTML = REPORTS.map((r) => {
    const has = Boolean((state.snapshot[r.key] || '').trim());
    const active = r.key === state.activeTab ? ' is-active' : '';
    const empty = has ? '' : ' is-empty';
    return `<button class="report-tab${active}${empty}" data-tab="${escapeHtml(r.key)}">${escapeHtml(r.label)}</button>`;
  }).join('');
  renderReport();
}

function renderReport() {
  const key = state.activeTab;
  const text = state.snapshot[key] || '';
  $('reportBody').innerHTML = renderMarkdown(text);
  $('reportMeta').textContent = text.trim() ? `${text.length.toLocaleString()} 字符` : '';
}

$('reportTabs').addEventListener('click', (e) => {
  const btn = e.target.closest('.report-tab');
  if (!btn) return;
  state.activeTab = btn.dataset.tab;
  renderTabs();
});

/* ------------------------------------------------------------------- run */

function setRunning(on) {
  state.running = on;
  $('runBtn').disabled = on;
  $('runBtn').querySelector('.btn-label').textContent = on ? '分析中…' : '开始分析';
  $('stopBtn').classList.toggle('is-hidden', !on);
  $('emptyState').classList.toggle('is-hidden', on);
  $('agentBoard').classList.toggle('is-hidden', !on);
  if (on) {
    $('runChip').classList.remove('is-hidden');
    $('runStats').classList.remove('is-hidden');
    startClock();
  } else {
    stopClock();
  }
}

function startClock() {
  stopClock();
  state.startedAt = Date.now();
  state.tickTimer = setInterval(() => {
    $('statElapsed').textContent = fmtDuration(Date.now() - state.startedAt);
  }, 1000);
}

function stopClock() {
  if (state.tickTimer) { clearInterval(state.tickTimer); state.tickTimer = null; }
}

function fmtDuration(ms) {
  const s = Math.floor(ms / 1000);
  if (s < 60) return s + 's';
  return `${Math.floor(s / 60)}m${String(s % 60).padStart(2, '0')}s`;
}

function applyStats(stats) {
  if (!stats) return;
  $('statCalls').textContent  = stats.llm_calls ?? 0;
  $('statTokens').textContent = ((stats.tokens_in || 0) + (stats.tokens_out || 0)).toLocaleString();
  $('statCost').textContent    = stats.cost_usd == null ? '—' : '$' + stats.cost_usd.toFixed(4);
  $('statElapsed').textContent = fmtDuration(Date.now() - (state.startedAt || Date.now()));
}

function setRunState(text, cls) {
  $('runState').textContent = text;
  $('runState').className = 'run-chip-state' + (cls ? ' ' + cls : '');
}

$('runBtn').addEventListener('click', async () => {
  const ticker = $('ticker').value.trim();
  const date = $('tradeDate').value;
  const analysts = [...document.querySelectorAll('#analystPicker input:checked')].map((i) => i.value);

  if (!ticker) { alert('请输入股票代码'); return; }
  if (!date)   { alert('请选择分析日期'); return; }
  if (!analysts.length) { alert('至少选择一个分析师'); return; }
  if (date > new Date().toISOString().slice(0, 10)) { alert('分析日期不能晚于今天'); return; }

  $('errorBox').classList.add('is-hidden');
  $('decisionCard').classList.add('is-hidden');
  $('reportPanel').classList.add('is-hidden');
  $('queueNote').classList.add('is-hidden');
  state.snapshot = {};
  state.status = {};
  state.cancelled = false;
  applyStatus({});
  applyStats({ llm_calls: 0, tokens_in: 0, tokens_out: 0, cost_usd: null });

  setRunning(true);
  $('runTicker').textContent = ticker.toUpperCase();
  $('runDate').textContent = date;
  setRunState('运行中', 'is-running');

  try {
    const res = await fetch('/api/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ticker,
        trade_date: date,
        analysts,
        max_debate_rounds: Number($('debateRounds').value),
        max_risk_rounds: Number($('riskRounds').value),
      }),
    });
    const body = await res.json();
    if (!res.ok) throw new Error(body.detail?.[0]?.msg || body.detail || `HTTP ${res.status}`);
    state.runId = body.run_id;
    follow(body.run_id);
  } catch (err) {
    showError(String(err));
    setRunning(false);
  }
});

/* Stop asks the server to stop. Closing the EventSource alone would leave the
   worker running and still paying for LLM calls. */
$('stopBtn').addEventListener('click', async () => {
  if (!state.runId) return;
  $('stopBtn').disabled = true;
  setRunState('正在中止…', 'is-cancelled');
  try {
    await fetch(`/api/cancel/${state.runId}`, { method: 'POST' });
  } catch { /* the run will still be stopped server-side or report its own error */ }
  $('stopBtn').disabled = false;
});

function follow(runId) {
  state.runId = runId;
  if (state.source) state.source.close();
  const src = new EventSource(`/api/stream/${runId}`);
  state.source = src;
  let sawTerminal = false;

  src.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }

    switch (msg.type) {
      case 'queued':
        $('queueNote').classList.remove('is-hidden');
        setRunState('排队中', 'is-running');
        break;

      case 'started':
        $('queueNote').classList.add('is-hidden');
        state.teams = msg.teams || state.teams;
        state.statusZh = msg.status_zh || state.statusZh;
        buildAgentBoard(state.teams);
        setRunState('运行中', 'is-running');
        break;

      case 'status':
        applyStatus(msg.status || {});
        break;

      case 'snapshot': {
        const prev = state.snapshot;
        state.snapshot = msg.state || {};
        applyStats(msg.stats);
        const changed = REPORTS.find((r) => state.snapshot[r.key] !== prev[r.key]);
        $('reportPanel').classList.remove('is-hidden');
        if (!state.activeTab || (!prev[state.activeTab] && state.snapshot[state.activeTab])) {
          state.activeTab = (changed || REPORTS.find((r) => (state.snapshot[r.key] || '').trim()))?.key
            || state.activeTab;
        }
        renderTabs();
        break;
      }

      case 'done':
        applyStats(msg.stats);
        $('decisionBody').textContent = msg.decision || '';
        $('ratingBadge').textContent = msg.rating || '—';
        $('ratingBadge').dataset.r = msg.rating || '';
        $('decisionCard').classList.remove('is-hidden');
        setRunState('完成', 'is-done');
        sawTerminal = true;
        loadHistory();
        break;

      case 'cancelled':
        applyStats(msg.stats);
        $('queueNote').classList.add('is-hidden');
        setRunState('已中止', 'is-cancelled');
        showError('分析已中止。' + (msg.reason ? `（${msg.reason}）` : '') +
                  '\n注意：中止在当前智能体完成后生效，该节点的模型调用费用已经产生。');
        sawTerminal = true;
        break;

      case 'error':
        $('queueNote').classList.add('is-hidden');
        showError(msg.error + (msg.traceback ? '\n\n' + msg.traceback : ''));
        setRunState('失败', 'is-error');
        sawTerminal = true;
        break;

      case 'closed':
        src.close();
        state.source = null;
        setRunning(false);
        break;
    }
  };

  /* A dropped connection must not strand the run: the server keeps it and can
     replay the event buffer, so reconnect and re-render. */
  src.onerror = () => {
    if (state.source !== src) return;
    src.close();
    state.source = null;
    if (sawTerminal) { setRunning(false); return; }
    setRunState('连接中断，重连中…', 'is-running');
    setTimeout(() => { if (state.runId === runId) follow(runId); }, 2000);
  };
}

function showError(msg) {
  const box = $('errorBox');
  box.textContent = msg;
  box.classList.remove('is-hidden');
}

/* ---------------------------------------------------------------- history */

async function loadHistory() {
  try {
    const res = await fetch('/api/runs');
    const rows = await res.json();
    $('historyMeta').textContent = rows.length ? `${rows.length} 条记录` : '';
    $('historyList').innerHTML = rows.length
      ? rows.map((r) => `
        <div class="history-item">
          <span class="history-ticker">${escapeHtml(r.ticker)}</span>
          <span class="history-date">${escapeHtml(r.trade_date)}</span>
          <span class="history-decision">${escapeHtml(r.decision)}</span>
          <span></span>
        </div>`).join('')
      : '<div class="history-empty">还没有决策记录。跑一次分析后会出现在这里。</div>';
  } catch { /* the tab is optional; stay quiet */ }
}

/* ------------------------------------------------------------------- init */

document.querySelectorAll('.tab').forEach((tab) => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach((t) => t.classList.toggle('is-active', t === tab));
    document.querySelectorAll('.view').forEach((v) => {
      v.classList.toggle('is-active', v.id === 'view-' + tab.dataset.view);
    });
    if (tab.dataset.view === 'history') loadHistory();
  });
});

(async function init() {
  // Default the date field to today, yyyy-mm-dd, in the browser's own locale.
  const now = new Date();
  $('tradeDate').value = now.toISOString().slice(0, 10);

  try {
    const [teamsBody, cfg] = await Promise.all([
      fetch('/api/teams').then((r) => r.json()),
      fetch('/api/config').then((r) => r.json()),
    ]);
    state.teams = teamsBody.teams || [];
    state.statusZh = teamsBody.status_zh || state.statusZh;
    $('debateRounds').value = cfg.max_debate_rounds;
    $('riskRounds').value = cfg.max_risk_discuss_rounds;

    const keyPill = cfg.api_key_configured
      ? '<span class="pill pill-ok">API Key 已配置</span>' : '<span class="pill pill-warn">未配置 API Key</span>';
    $('configMeta').innerHTML =
      keyPill +
      `<span class="pill">${escapeHtml(cfg.llm_provider)}</span>` +
      `<span class="pill">${escapeHtml(cfg.output_language)}</span>`;
  } catch {
    $('configMeta').innerHTML = '<span class="pill pill-warn">无法连接后端</span>';
  }

  loadHistory();
})();
