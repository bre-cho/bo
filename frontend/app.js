/**
 * app.js — BO Trading Robot Dashboard
 */

const API = '';   // Same origin — API server serves the frontend

let currentPage = 1;
let isPaused    = false;
let refreshTimer = null;

// ── Victor strategy definitions ──────────────────────────────────
const VICTOR_DATA = {
  victor2: {
    name: 'Victor 2',
    rows: [
      [1,1,2,2,3,4,5,7,10,13,18,24,32,44,59,80,108,146,197,271],
      [1,2,4,4,6,8,10,14,20,26,36,48,64,88,118,160,216,292,394,542],
    ],
    description: `Có 2 chuỗi\n- Di chuyển từ trái sang phải khi thua ở chuỗi 1, khi thắng sẽ di chuyển xuống chuỗi 2 cùng vị trí.\n- Nếu thắng ở chuỗi 2 thì quay trở về (1,1), nếu thua thì quay về vị trí tiếp theo ở chuỗi 1 (di chuyển chéo lên trên).`,
  },
  victor3: {
    name: 'Victor 3',
    rows: [
      [1,1,1,1,1,1,1.5,2,2,2,2.5,3,3,3.5,4,4,4.5,5.4,6,7,8,9.5,11],
      [1,2,2,2,2,2,3,3.9,3.9,3.9,4.875,5.85,6.825,7.8,8.775,10.53,11.7,13.65,15.6,18.525,21.45],
      [1,4,4,4,4,4,6,7.605,7.605,7.605,9.50625,11.4075,13.30875,15.21,17.11125,20.5335,22.815,26.6175,30.42,36.1],
    ],
    description: `Có 3 chuỗi\n- Di chuyển từ trái sang phải khi thua ở chuỗi 1, khi thắng sẽ di chuyển xuống chuỗi 2 cùng vị trí.\n- Nếu thắng ở chuỗi 2 thì tiếp tục di chuyển xuống chuỗi 3 cùng vị trí, nếu thua trở lại vị trí tiếp theo ở chuỗi 1.\n- Nếu thắng ở chuỗi 3 thì quay về vị trí (1,1), nếu thua trở lại vị trí tiếp theo ở chuỗi 1.\n- Kết thúc chuỗi 1 sẽ quay về (1,1)`,
  },
  victor4: {
    name: 'Victor 4',
    rows: [
      [1,1,1,1,1,1,1,1,1,1,1,1,1,1,1.23,1.25,1.28,1.3,1.47,1.6,1.74,1.88,2.04,2.22],
      [1.95,1.95,1.95,1.95,1.95,1.95,1.95,1.95,1.95,1.95,1.95,1.95,1.95,1.95,1.95,2.28,2.32,2.36,2.41,2.73,2.96,3.21,3.49,3.79],
      [3.8,3.8,3.8,3.8,3.8,3.8,3.8,3.8,3.8,3.8,3.8,3.8,3.8,3.8,4.22,4.29,4.37,4.45,5.04,5.47,5.94,6.44,6.99,7.59],
      [7.41,7.41,7.41,7.41,7.41,7.41,7.41,7.41,7.41,7.41,7.41,7.41,7.41,7.41,7.81,7.94,8.08,8.24,9.33,10.12,10.99,11.92,12.96,14.09],
    ],
    description: `Có 4 chuỗi\n- Di chuyển từ trái sang phải khi thua ở chuỗi 1, khi thắng sẽ di chuyển xuống chuỗi 2 cùng vị trí.\n- Nếu thắng ở chuỗi 2 thì tiếp tục di chuyển xuống chuỗi 3 cùng vị trí, nếu thua trở lại vị trí tiếp theo ở chuỗi 1.\n- Nếu thắng ở chuỗi 3 thì tiếp tục di chuyển xuống chuỗi 4 cùng vị trí, nếu thua trở lại vị trí tiếp theo ở chuỗi 1.\n- Nếu thắng ở chuỗi 4 thì quay về vị trí (1,1), nếu thua trở lại vị trí tiếp theo ở chuỗi 1.`,
  },
};

// ── API Helpers ───────────────────────────────────────────────────
async function apiGet(path) {
  try {
    const r = await fetch(API + path);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return await r.json();
  } catch(e) { console.error('GET', path, e); return null; }
}

async function apiPost(path, body) {
  try {
    const r = await fetch(API + path, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    if (!r.ok) { const t = await r.text(); throw new Error(t); }
    return await r.json();
  } catch(e) { console.error('POST', path, e); return null; }
}

// ── Format helpers ────────────────────────────────────────────────
function fmtPnl(v) {
  const n = parseFloat(v);
  const cls = n >= 0 ? 'text-success' : 'text-danger';
  const sgn = n >= 0 ? '+' : '';
  return `<span class="${cls}">${sgn}${n.toFixed(2)}</span>`;
}

function fmtDir(dir) {
  if (dir === 'CALL') return '<span class="badge bg-success">CALL ↑</span>';
  if (dir === 'PUT')  return '<span class="badge bg-danger">PUT ↓</span>';
  return dir;
}

function fmtTime(ts) {
  if (!ts) return '--';
  try { return new Date(ts).toLocaleTimeString('vi-VN'); }
  catch { return ts; }
}

// ── Load stats ────────────────────────────────────────────────────
async function loadStats() {
  const [stats, status, balance] = await Promise.all([
    apiGet('/stats'),
    apiGet('/status'),
    apiGet('/balance'),
  ]);

  if (stats) {
    setText('total-trades', stats.total_trades ?? '--');
    setText('wins', stats.wins ?? 0);
    setText('losses', stats.losses ?? 0);
    const wr = stats.win_rate_pct != null ? stats.win_rate_pct.toFixed(1) + '%' : '--';
    setText('win-rate', wr);
    setText('pf', stats.profit_factor ?? '--');
    setHtml('daily-pnl', fmtPnl(stats.total_pnl ?? 0));
  }

  if (balance) {
    setText('balance', '$' + parseFloat(balance.balance ?? 0).toFixed(2));
  }

  if (status) {
    // Engine badge
    const mode = status.engine_mode || 'UNKNOWN';
    const badge = document.getElementById('engine-badge');
    badge.textContent = mode;
    badge.className = `badge ${mode === 'LIVE' ? 'bg-success' : mode === 'PAPER' ? 'bg-warning text-dark' : mode === 'PAUSED' ? 'bg-secondary' : 'bg-info'}`;

    // Connection dot
    document.getElementById('conn-dot').className = 'status-dot dot-green';
    document.getElementById('conn-label').textContent = 'Kết nối';

    // System status card
    const ctrl = status.control || {};
    const cap  = status.capital_strategy || {};
    let statusHtml = '';
    if (ctrl.stopped_by_tpsl) {
      statusHtml += `<div class="alert alert-danger py-2 mb-2">🛑 ${ctrl.stop_reason}</div>`;
      document.getElementById('tpsl-stop-alert').style.display = '';
      document.getElementById('tpsl-stop-reason').textContent = ctrl.stop_reason;
      document.getElementById('restart-btn').style.display = '';
    } else {
      document.getElementById('tpsl-stop-alert').style.display = 'none';
      document.getElementById('restart-btn').style.display = 'none';
    }
    statusHtml += `
      <div class="d-flex flex-wrap gap-2">
        <span class="badge bg-dark border border-secondary">Mode: ${mode}</span>
        <span class="badge bg-dark border border-secondary">Wave: ${ctrl.wave_direction_filter || 'both'}</span>
        <span class="badge bg-dark border border-secondary">TP: ${ctrl.daily_take_profit_usd > 0 ? '+$' + ctrl.daily_take_profit_usd : 'off'}</span>
        <span class="badge bg-dark border border-secondary">SL: ${ctrl.daily_stop_loss_usd > 0 ? '-$' + ctrl.daily_stop_loss_usd : 'off'}</span>
        <span class="badge bg-dark border border-secondary">Symbols: ${(status.active_symbols || []).join(', ')}</span>
      </div>`;
    setHtml('system-status-body', statusHtml);

    // Strategy status
    let stratHtml = `
      <div class="d-flex flex-wrap gap-2">
        <span class="badge bg-primary">${cap.strategy || '--'}</span>
        <span class="badge bg-dark border border-secondary">Base: $${cap.base_stake || 1}</span>
        ${cap.row ? `<span class="badge bg-dark border border-secondary">Hàng ${cap.row} / Vị trí ${cap.pos}</span>` : ''}
        ${cap.stake ? `<span class="badge bg-warning text-dark">Stake: $${cap.stake}</span>` : ''}
        <span class="badge bg-dark border border-secondary">Win streak: ${cap.consecutive_win || 0}</span>
        <span class="badge bg-dark border border-secondary">Loss streak: ${cap.consecutive_loss || 0}</span>
      </div>`;
    setHtml('strategy-status-body', stratHtml);
    setHtml('strategy-current-status', stratHtml);

    // TP/SL displays
    setText('tp-display', ctrl.daily_take_profit_usd > 0 ? '+$' + ctrl.daily_take_profit_usd : 'off');
    setText('sl-display', ctrl.daily_stop_loss_usd > 0 ? '-$' + ctrl.daily_stop_loss_usd : 'off');
  }
}

// ── Load recent logs ──────────────────────────────────────────────
async function loadLogs(page) {
  page = page || currentPage;
  currentPage = page;
  const data = await apiGet(`/logs?page=${page}&size=20`);
  if (!data) return;

  setText('logs-total', `Tổng: ${data.total}`);

  const tbody = document.getElementById('logs-body');
  if (!data.records || data.records.length === 0) {
    tbody.innerHTML = '<tr><td colspan="8" class="text-center text-muted py-4">Không có dữ liệu</td></tr>';
    return;
  }

  tbody.innerHTML = data.records.map(r => `
    <tr class="${r.won ? 'table-success-subtle' : 'table-danger-subtle'}">
      <td>${fmtTime(r.timestamp)}</td>
      <td>${r.symbol}</td>
      <td>${fmtDir(r.direction)}</td>
      <td>${r.signal_score?.toFixed(0) ?? '--'}</td>
      <td>$${parseFloat(r.stake || 0).toFixed(2)}</td>
      <td>$${parseFloat(r.payout || 0).toFixed(2)}</td>
      <td>${fmtPnl(r.pnl)}</td>
      <td>${r.won ? '✅ THẮNG' : '❌ THUA'}</td>
    </tr>`).join('');

  // Pagination
  const totalPages = Math.ceil(data.total / 20);
  let pages = '';
  for (let i = 1; i <= Math.min(totalPages, 10); i++) {
    pages += `<button class="btn btn-sm ${i === page ? 'btn-primary' : 'btn-outline-secondary'} mx-1" onclick="loadLogs(${i})">${i}</button>`;
  }
  setHtml('logs-pagination', pages);

  // Also update recent trades on dashboard
  const recentTbody = document.getElementById('recent-trades-body');
  recentTbody.innerHTML = data.records.slice(0, 10).map(r => `
    <tr class="${r.won ? 'table-success-subtle' : 'table-danger-subtle'}">
      <td>${fmtTime(r.timestamp)}</td>
      <td>${r.symbol}</td>
      <td>${fmtDir(r.direction)}</td>
      <td>${r.signal_score?.toFixed(0) ?? '--'}</td>
      <td>$${parseFloat(r.stake || 0).toFixed(2)}</td>
      <td>${fmtPnl(r.pnl)}</td>
      <td>${r.won ? '✅' : '❌'}</td>
    </tr>`).join('');
}

// ── Engine pause/resume ───────────────────────────────────────────
async function togglePause() {
  const endpoint = isPaused ? '/engine/resume' : '/engine/pause';
  const result = await apiPost(endpoint, {});
  if (result) {
    isPaused = !isPaused;
    document.getElementById('pause-icon').className = isPaused ? 'bi bi-play-circle' : 'bi bi-pause-circle';
    document.getElementById('pause-label').textContent = isPaused ? 'Tiếp tục' : 'Tạm dừng';
    await loadStats();
  }
}

// ── Strategy ──────────────────────────────────────────────────────
function onStrategyChange() {
  const v = document.getElementById('strategy-select').value;
  const isVictor = v.startsWith('victor');
  document.getElementById('victor-info').style.display = isVictor ? '' : 'none';
  document.getElementById('victor-chains-card').style.display = isVictor ? '' : 'none';

  if (isVictor && VICTOR_DATA[v]) {
    const d = VICTOR_DATA[v];
    document.getElementById('victor-name-label').textContent = d.name;
    document.getElementById('victor-description').innerHTML =
      d.description.split('\n').map(l => `<div>${l}</div>`).join('');

    const base = parseFloat(document.getElementById('base-stake').value) || 1;
    let chainsHtml = '';
    d.rows.forEach((row, i) => {
      const cells = row.slice(0, 16).map(s => {
        const stake = (s * base).toFixed(s < 10 ? 2 : 0);
        return `<span class="stake-cell">${stake}</span>`;
      }).join('');
      chainsHtml += `<div class="mb-2">
        <div class="text-muted small mb-1">Cài đặt hàng ${i + 1}</div>
        <div class="stake-row">${cells}${row.length > 16 ? '<span class="stake-cell text-muted">...</span>' : ''}</div>
      </div>`;
    });
    setHtml('victor-chains-body', chainsHtml);
  }
}

async function applyStrategy() {
  const name  = document.getElementById('strategy-select').value;
  const stake = parseFloat(document.getElementById('base-stake').value) || 1;
  const result = await apiPost('/strategy', {name, base_stake: stake});
  if (result) {
    showToast('Đã áp dụng chiến lược: ' + name, 'success');
    await loadStats();
  } else {
    showToast('Lỗi áp dụng chiến lược', 'danger');
  }
}

async function resetStrategy() {
  const result = await apiPost('/strategy/reset', {});
  if (result) { showToast('Đã reset chiến lược', 'warning'); await loadStats(); }
}

// ── TP/SL Controls ────────────────────────────────────────────────
function onTPToggle() {
  document.getElementById('tp-amount').disabled = !document.getElementById('tp-enabled').checked;
}
function onSLToggle() {
  document.getElementById('sl-amount').disabled = !document.getElementById('sl-enabled').checked;
}

async function applyTPSL() {
  const tpOn  = document.getElementById('tp-enabled').checked;
  const slOn  = document.getElementById('sl-enabled').checked;
  const tpAmt = tpOn ? parseFloat(document.getElementById('tp-amount').value) : 0;
  const slAmt = slOn ? parseFloat(document.getElementById('sl-amount').value) : 0;

  await Promise.all([
    apiPost('/control/tp', {amount_usd: tpAmt}),
    apiPost('/control/sl', {amount_usd: slAmt}),
  ]);
  showToast('Đã lưu cài đặt TP/SL', 'success');
  await loadStats();
}

async function restartAfterStop() {
  const result = await apiPost('/control/restart', {});
  if (result) {
    showToast('Đã khởi động lại — tiếp tục giao dịch', 'success');
    await loadStats();
  }
}

async function applyWaveFilter() {
  const mode = document.querySelector('input[name="wave-filter"]:checked')?.value || 'both';
  const result = await apiPost('/control/wave', {mode});
  if (result) {
    showToast('Đã cập nhật bộ lọc sóng: ' + mode, 'success');
    await loadStats();
  }
}

// ── LLM ──────────────────────────────────────────────────────────
async function sendLLMQuestion() {
  const input = document.getElementById('llm-input');
  const q = input.value.trim();
  if (!q) return;
  input.value = '';
  appendChat('user', q);
  const btn = document.getElementById('llm-send-btn');
  btn.disabled = true;
  const result = await apiPost('/llm/ask', {question: q});
  btn.disabled = false;
  if (result) {
    appendChat('assistant', result.answer || '[Không có phản hồi]');
  } else {
    appendChat('system', 'Lỗi kết nối — hãy kiểm tra LLM_ENABLED và API key.');
  }
}

async function askSuggestions() {
  document.getElementById('llm-input').value = 'Đề xuất 3 cải thiện chiến lược dựa trên lịch sử giao dịch hiện tại.';
  await sendLLMQuestion();
}

function appendChat(role, text) {
  const box = document.getElementById('llm-chat-box');
  const div = document.createElement('div');
  div.className = `chat-bubble ${role}`;
  div.textContent = text;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
}

// ── Synthetic Engine ─────────────────────────────────────────────
async function runSyntheticTrain() {
  const n      = parseInt(document.getElementById('synth-n-per-regime').value) || 150;
  const blend  = document.getElementById('synth-blend-real').checked;
  const btn    = document.getElementById('synth-train-btn');
  const prog   = document.getElementById('synth-train-progress');
  const label  = document.getElementById('synth-progress-label');

  btn.disabled = true;
  prog.style.display = '';
  label.textContent  = 'Đang tạo dữ liệu tổng hợp...';

  const result = await apiPost('/synthetic/train', {
    n_per_regime: n,
    blend_real_data: blend,
  });

  prog.style.display = 'none';
  btn.disabled = false;

  const card = document.getElementById('synth-result-card');
  const body = document.getElementById('synth-result-body');
  card.style.display = '';

  if (result && result.status === 'ok') {
    const m = result.metrics || {};
    body.innerHTML = `
      <div class="d-flex flex-wrap gap-2 mb-2">
        <span class="badge bg-success">✅ Hoàn thành</span>
        <span class="badge bg-info">AUC: ${(m.win_clf_auc || 0).toFixed(4)}</span>
        <span class="badge bg-primary">Mẫu: ${m.n_samples || '--'}</span>
        <span class="badge ${m.lstm_trained ? 'bg-success' : 'bg-secondary'}">LSTM: ${m.lstm_trained ? 'OK' : 'Bỏ qua'}</span>
      </div>
      <div class="text-muted small">
        Model đã lưu → dùng cho inference ngay lệnh tiếp theo.<br>
        Bật <code>ML_ENABLED=True</code> trong config.py để kích hoạt.
      </div>`;
    showToast('Synthetic training hoàn thành!', 'success');
  } else {
    body.innerHTML = `<div class="text-danger">Lỗi training. Kiểm tra console server.</div>`;
    showToast('Lỗi synthetic training', 'danger');
  }
}

async function previewSyntheticData() {
  const n = parseInt(document.getElementById('synth-n-per-regime').value) || 50;
  const result = await apiGet(`/synthetic/demo?n_per_regime=${Math.min(n, 50)}`);
  const card = document.getElementById('synth-preview-card');
  const body = document.getElementById('synth-preview-body');
  card.style.display = '';

  if (result) {
    const wr = result.win_rate_pct || 0;
    const wrClass = wr >= 48 && wr <= 52 ? 'text-success' : wr > 55 ? 'text-warning' : 'text-info';
    body.innerHTML = `
      <div class="d-flex flex-wrap gap-2 mb-2">
        <span class="badge bg-dark border border-secondary">Tổng: ${result.n_samples}</span>
        <span class="badge bg-dark border border-secondary ${wrClass}">Win: ${result.win_rate_pct}%</span>
        <span class="badge bg-dark border border-secondary">Features: ${result.n_features}</span>
        <span class="badge bg-success bg-opacity-25">Win: ${result.n_wins}</span>
        <span class="badge bg-danger bg-opacity-25">Loss: ${result.n_losses}</span>
      </div>
      <div class="text-muted small">
        ${result.feature_names ? result.feature_names.slice(0, 10).join(', ') + '...' : ''}
      </div>`;
  } else {
    body.innerHTML = `<div class="text-muted small">Không lấy được preview — server chưa chạy?</div>`;
  }
}

// ── Evolution Engine ─────────────────────────────────────────────
async function runEvolution() {
  const gens    = parseInt(document.getElementById('evol-gens').value)    || 10;
  const pop     = parseInt(document.getElementById('evol-pop').value)     || 20;
  const envs    = parseInt(document.getElementById('evol-envs').value)    || 6;
  const candles = parseInt(document.getElementById('evol-candles').value) || 150;

  const btn     = document.getElementById('evol-run-btn');
  const prog    = document.getElementById('evol-progress');
  const label   = document.getElementById('evol-progress-label');

  btn.disabled      = true;
  prog.style.display = '';
  label.textContent  = `Đang tiến hóa… (${gens} thế hệ × pop=${pop})`;

  const result = await apiPost('/evolution/run', {
    generations : gens,
    pop_size    : pop,
    n_envs      : envs,
    env_candles : candles,
    seed        : 42,
  });

  prog.style.display = 'none';
  btn.disabled = false;

  if (result && result.status === 'ok') {
    renderEvolutionChampion(result.champion);
    showToast('Evolution hoàn thành! Champion đã được lưu.', 'success');
    await loadEvolutionStatus();
  } else {
    showToast('Lỗi Evolution — kiểm tra console server', 'danger');
  }
}

async function loadEvolutionStatus() {
  const result = await apiGet('/evolution/status');
  if (!result) return;

  if (result.champion) {
    renderEvolutionChampion(result.champion);
  }

  if (result.history && result.history.length > 0) {
    renderEvolutionHistory(result.history);
  }
}

async function promoteChampion() {
  const result = await apiPost('/evolution/promote', {});
  if (result && result.applied) {
    showToast(
      `Champion áp dụng! min_score=${result.min_signal_score?.toFixed(1)} ` +
      `rsi_os=${result.rsi_oversold?.toFixed(1)}`, 'success'
    );
    await loadStats();
  } else {
    showToast('Không có champion để áp dụng', 'warning');
  }
}

function renderEvolutionChampion(c) {
  const card = document.getElementById('evol-champion-card');
  const body = document.getElementById('evol-champion-body');
  if (!card || !body || !c) return;

  card.style.display = '';
  const fitness = (c.fitness || 0).toFixed(4);
  const wr      = (c.win_rate_pct || 0).toFixed(1);
  const pf      = (c.profit_factor || 0).toFixed(2);
  const genes   = c.genes || {};

  body.innerHTML = `
    <div class="d-flex flex-wrap gap-2 mb-3">
      <span class="badge bg-success">Fitness: ${fitness}</span>
      <span class="badge bg-info">Win Rate: ${wr}%</span>
      <span class="badge bg-primary">PF: ${pf}</span>
      <span class="badge bg-secondary">Trades: ${c.n_trades || 0}</span>
      <span class="badge bg-dark border border-secondary">Gen: ${c.generation || 0}</span>
      <span class="badge bg-dark border border-secondary">#${c.genome_id || '?'}</span>
    </div>
    <div class="row g-2 small text-muted">
      <div class="col-6 col-md-4">
        <div class="gene-card">
          <div class="gene-label">min_signal_score</div>
          <div class="gene-value text-warning">${(c.min_signal_score || 60).toFixed(1)}</div>
        </div>
      </div>
      <div class="col-6 col-md-4">
        <div class="gene-card">
          <div class="gene-label">lookahead_candles</div>
          <div class="gene-value text-info">${c.lookahead_candles || 5}</div>
        </div>
      </div>
      <div class="col-6 col-md-4">
        <div class="gene-card">
          <div class="gene-label">rsi_oversold / overbought</div>
          <div class="gene-value text-success">${(c.rsi_oversold||30).toFixed(1)} / ${(c.rsi_overbought||70).toFixed(1)}</div>
        </div>
      </div>
      <div class="col-6 col-md-4">
        <div class="gene-card">
          <div class="gene-label">wave_weight</div>
          <div class="gene-value text-primary">${(genes.wave_weight || c.wave_weight || 1).toFixed(3)}</div>
        </div>
      </div>
      <div class="col-6 col-md-4">
        <div class="gene-card">
          <div class="gene-label">rsi_weight</div>
          <div class="gene-value">${(genes.rsi_weight||1).toFixed(3)}</div>
        </div>
      </div>
      <div class="col-6 col-md-4">
        <div class="gene-card">
          <div class="gene-label">macd_weight</div>
          <div class="gene-value">${(genes.macd_weight||1).toFixed(3)}</div>
        </div>
      </div>
    </div>`;
}

function renderEvolutionHistory(history) {
  const card  = document.getElementById('evol-history-card');
  const tbody = document.querySelector('#evol-history-table tbody');
  if (!card || !tbody) return;

  card.style.display = '';
  tbody.innerHTML = history.slice(-20).reverse().map(row => `
    <tr>
      <td>${row.generation}</td>
      <td class="text-success">${(row.best_fitness||0).toFixed(4)}</td>
      <td>${(row.mean_fitness||0).toFixed(4)}</td>
      <td>${(row.best_win_rate||0).toFixed(1)}%</td>
      <td>${(row.best_pf||0).toFixed(2)}</td>
    </tr>`).join('');
}

// ── Meta-Genetics Engine ─────────────────────────────────────────

async function runMetaBreed() {
  const nSeeds = parseInt(document.getElementById('meta-n-seeds').value) || 12;
  const btn    = document.getElementById('meta-breed-btn');
  const prog   = document.getElementById('meta-progress');

  btn.disabled       = true;
  prog.style.display = '';

  const result = await apiPost('/meta/breed', { n_seeds: nSeeds });

  prog.style.display = 'none';
  btn.disabled = false;

  if (result && result.status === 'ok') {
    renderMetaInsights(result.insights);
    renderMetaSeeds(result.seeds);
    renderMetaPoolStats({ pool_size: result.pool_size, n_archetypes: result.n_archetypes });
    showToast(`Meta breed: ${result.n_seeds} seeds từ pool ${result.pool_size} genomes`, 'info');
    await loadMetaReport();
  } else {
    showToast('Meta-Learning chưa đủ dữ liệu — chạy evolution trước', 'warning');
  }
}

async function loadMetaReport() {
  const result = await apiGet('/meta/report');
  if (!result || result.status === 'no_report') return;

  renderMetaInsights(result.insights || []);
  renderMetaImportance(result.gene_importances || {}, result.top_genes || []);
  renderMetaPatterns(result.winner_patterns || {});
  renderMetaPoolStats({ pool_size: result.pool_size, n_archetypes: result.n_archetypes });

  // Load archetypes
  const archResult = await apiGet('/meta/archetypes');
  if (archResult && archResult.archetypes) {
    renderMetaArchetypes(archResult.archetypes);
  }
}

function renderMetaInsights(insights) {
  const card = document.getElementById('meta-insights-card');
  const body = document.getElementById('meta-insights-body');
  if (!card || !body || !insights.length) return;
  card.style.display = '';
  body.innerHTML = insights.map(i =>
    `<div class="d-flex gap-2 mb-1 small">
       <span class="text-info flex-shrink-0">•</span>
       <span class="text-muted">${i}</span>
     </div>`
  ).join('');
}

function renderMetaImportance(importances, topGenes) {
  const card = document.getElementById('meta-importance-card');
  const body = document.getElementById('meta-importance-body');
  if (!card || !body) return;
  card.style.display = '';

  const entries = Object.entries(importances)
    .sort((a, b) => b[1] - a[1]);
  const maxImp = entries.length ? entries[0][1] : 1;

  body.innerHTML = entries.map(([gene, imp]) => {
    const pct   = maxImp > 0 ? Math.round((imp / maxImp) * 100) : 0;
    const isTop = topGenes.includes(gene);
    const barColor = isTop ? 'bg-warning' : 'bg-secondary';
    return `
      <div class="mb-1">
        <div class="d-flex justify-content-between small">
          <span class="${isTop ? 'text-warning fw-bold' : 'text-muted'}">${gene}</span>
          <span class="text-muted">${imp.toFixed(4)}</span>
        </div>
        <div class="progress bg-dark" style="height:5px">
          <div class="progress-bar ${barColor}" style="width:${pct}%"></div>
        </div>
      </div>`;
  }).join('');
}

function renderMetaPatterns(patterns) {
  const card  = document.getElementById('meta-patterns-card');
  const tbody = document.querySelector('#meta-patterns-table tbody');
  if (!card || !tbody) return;
  card.style.display = '';

  const rows = Object.entries(patterns).map(([gene, p]) => {
    const tightBadge = p.tight_range
      ? '<span class="badge bg-success bg-opacity-25 text-success" style="font-size:0.65rem">tight</span>'
      : '<span class="badge bg-dark text-muted" style="font-size:0.65rem">loose</span>';
    const uniMark = p.is_universal ? ' ★' : '';
    return `
      <tr>
        <td class="small ${p.tight_range ? 'text-warning' : 'text-muted'}">${gene}${uniMark}</td>
        <td class="small text-muted">${(p.low_pct||0).toFixed(2)}</td>
        <td class="small text-light">${(p.median||0).toFixed(2)}</td>
        <td class="small text-muted">${(p.high_pct||0).toFixed(2)}</td>
        <td>${tightBadge}</td>
      </tr>`;
  });
  tbody.innerHTML = rows.join('');
}

function renderMetaArchetypes(archetypes) {
  const card = document.getElementById('meta-archetypes-card');
  const body = document.getElementById('meta-archetypes-body');
  if (!card || !body) return;
  card.style.display = '';

  const colors = ['bg-success', 'bg-info', 'bg-primary', 'bg-warning', 'bg-danger'];
  body.innerHTML = archetypes.map((arch, i) => {
    const color = colors[i % colors.length];
    return `
      <div class="archetype-card mb-2 border border-secondary rounded p-2">
        <div class="d-flex justify-content-between align-items-center mb-1">
          <span class="badge ${color} bg-opacity-25 text-light">${arch.label || arch.archetype_id}</span>
          <span class="text-muted small">${arch.n_members} members</span>
        </div>
        <div class="d-flex gap-2 small text-muted">
          <span>fit <strong class="text-light">${(arch.mean_fitness||0).toFixed(4)}</strong></span>
          <span>WR <strong class="text-light">${(arch.mean_win_rate||0).toFixed(1)}%</strong></span>
          <span>PF <strong class="text-light">${(arch.mean_pf||0).toFixed(2)}</strong></span>
        </div>
        <div class="small text-muted mt-1">
          wave_w=${((arch.centroid||{}).wave_weight||0).toFixed(2)}
          min_score=${((arch.centroid||{}).min_signal_score||0).toFixed(1)}
          lookahead=${Math.round((arch.centroid||{}).lookahead_candles||5)}
        </div>
      </div>`;
  }).join('');
}

function renderMetaSeeds(seeds) {
  const card  = document.getElementById('meta-seeds-card');
  const tbody = document.querySelector('#meta-seeds-table tbody');
  const badge = document.getElementById('meta-seeds-count');
  if (!card || !tbody) return;
  card.style.display = '';
  if (badge) badge.textContent = seeds.length;
  tbody.innerHTML = seeds.map((s, i) => `
    <tr>
      <td class="text-muted small">${i + 1}</td>
      <td class="small text-warning">${(s.min_signal_score||60).toFixed(1)}</td>
      <td class="small text-primary">${(s.wave_weight||1).toFixed(2)}</td>
      <td class="small text-info">${s.lookahead_candles||5}</td>
    </tr>`).join('');
}

function renderMetaPoolStats(stats) {
  const card = document.getElementById('meta-pool-card');
  const body = document.getElementById('meta-pool-body');
  if (!card || !body) return;
  card.style.display = '';
  body.innerHTML = `
    <div class="d-flex gap-3 small">
      <div class="text-center">
        <div class="fs-5 text-info fw-bold">${stats.pool_size || 0}</div>
        <div class="text-muted">Genomes</div>
      </div>
      <div class="text-center">
        <div class="fs-5 text-primary fw-bold">${stats.n_archetypes || 0}</div>
        <div class="text-muted">Archetypes</div>
      </div>
    </div>`;
}

// ── Utilities ─────────────────────────────────────────────────────
function setText(id, v) {
  const el = document.getElementById(id);
  if (el) el.textContent = v;
}
function setHtml(id, v) {
  const el = document.getElementById(id);
  if (el) el.innerHTML = v;
}

function showToast(msg, type) {
  const toast = document.createElement('div');
  toast.className = `alert alert-${type} position-fixed bottom-0 end-0 m-3`;
  toast.style.zIndex = 9999;
  toast.textContent = msg;
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 3000);
}

// ── Auto-refresh ──────────────────────────────────────────────────
function startRefresh() {
  loadStats();
  loadLogs(1);
  refreshTimer = setInterval(() => {
    loadStats();
    // Only reload logs if on logs tab
    if (document.querySelector('#tab-logs.active')) loadLogs(currentPage);
  }, 15000);
}

// ── Init ──────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  startRefresh();
  onStrategyChange();
  // Listen for base stake changes to update Victor display
  document.getElementById('base-stake').addEventListener('input', onStrategyChange);

  // Load evolution status when evolution tab is clicked
  document.querySelectorAll('[href="#tab-evolution"]').forEach(el => {
    el.addEventListener('click', () => loadEvolutionStatus());
  });

  // Load meta report when meta tab is clicked
  document.querySelectorAll('[href="#tab-meta"]').forEach(el => {
    el.addEventListener('click', () => loadMetaReport());
  });
});
