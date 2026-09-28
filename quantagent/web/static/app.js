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
  status: {},      // agent key -> pending | running | done
  snapshot: {},    // state field -> text
  activeTab: null,
  source: null,    // EventSource
  running: false,
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
          <div class="agent is-pending" data-agent="${escapeHtml(a.key)}">
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
  if (!on) $('runState').classList.remove('is-running');
}

$('runBtn').addEventListener('click', async () => {
  const ticker = $('ticker').value.trim();
  const date = $('tradeDate').value;
  const analysts = [...document.querySelectorAll('#analystPicker input:checked')].map((i) => i.value);

  if (!ticker) { alert('请输入股票代码'); return; }
  if (!date)   { alert('请选择分析日期'); return; }
  if (!analysts.length) { alert('至少选择一个分析师'); return; }

  $('errorBox').classList.add('is-hidden');
  $('decisionCard').classList.add('is-hidden');
  $('reportPanel').classList.add('is-hidden');
  state.snapshot = {};
  state.status = {};
  applyStatus({});

  setRunning(true);
  $('runTicker').textContent = ticker.toUpperCase();
  $('runDate').textContent = date;
  $('runState').textContent = '运行中';

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
    if (!res.ok) throw new Error(`启动失败: HTTP ${res.status}`);
    const run = await res.json();
    follow(run.run_id);
  } catch (err) {
    showError(String(err));
    setRunning(false);
  }
});

$('stopBtn').addEventListener('click', () => {
  if (state.source) { state.source.close(); state.source = null; }
  setRunning(false);
  $('runState').textContent = '已中止';
  $('runState').className = 'run-chip-state';
});

function follow(runId) {
  if (state.source) state.source.close();
  const src = new EventSource(`/api/stream/${runId}`);
  state.source = src;

  src.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }

    switch (msg.type) {
      case 'started':
        state.teams = msg.teams || state.teams;
        buildAgentBoard(state.teams);
        break;

      case 'status':
        applyStatus(msg.status || {});
        break;

      case 'snapshot': {
        const prev = state.snapshot;
        state.snapshot = msg.state || {};
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
        $('decisionBody').textContent = msg.decision || '';
        $('ratingBadge').textContent = msg.rating || '—';
        $('ratingBadge').dataset.r = msg.rating || '';
        $('decisionCard').classList.remove('is-hidden');
        $('runState').textContent = '完成';
        $('runState').className = 'run-chip-state is-done';
        loadHistory();
        break;

      case 'error':
        showError(msg.error + (msg.traceback ? '\n\n' + msg.traceback : ''));
        $('runState').textContent = '失败';
        $('runState').className = 'run-chip-state is-error';
        break;

      case 'closed':
        src.close();
        state.source = null;
        setRunning(false);
        break;
    }
  };

  src.onerror = () => {
    if (state.source === src) {
      src.close();
      state.source = null;
      setRunning(false);
    }
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
    const [teams, cfg] = await Promise.all([
      fetch('/api/teams').then((r) => r.json()),
      fetch('/api/config').then((r) => r.json()),
    ]);
    state.teams = teams;
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
