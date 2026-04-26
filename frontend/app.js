/**
 * app.js — Bảng điều khiển robot giao dịch BO
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
    const mode = status.engine_mode || 'KHÔNG_XÁC_ĐỊNH';
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
        <span class="badge bg-dark border border-secondary">Che do: ${mode}</span>
        <span class="badge bg-dark border border-secondary">Song: ${ctrl.wave_direction_filter || 'both'}</span>
        <span class="badge bg-dark border border-secondary">TP: ${ctrl.daily_take_profit_usd > 0 ? '+$' + ctrl.daily_take_profit_usd : 'tắt'}</span>
        <span class="badge bg-dark border border-secondary">SL: ${ctrl.daily_stop_loss_usd > 0 ? '-$' + ctrl.daily_stop_loss_usd : 'tắt'}</span>
        <span class="badge bg-dark border border-secondary">Ma: ${(status.active_symbols || []).join(', ')}</span>
      </div>`;
    setHtml('system-status-body', statusHtml);

    // Strategy status
    let stratHtml = `
      <div class="d-flex flex-wrap gap-2">
        <span class="badge bg-primary">${cap.strategy || '--'}</span>
        <span class="badge bg-dark border border-secondary">Moc: $${cap.base_stake || 1}</span>
        ${cap.row ? `<span class="badge bg-dark border border-secondary">Hàng ${cap.row} / Vị trí ${cap.pos}</span>` : ''}
        ${cap.stake ? `<span class="badge bg-warning text-dark">Tien lenh: $${cap.stake}</span>` : ''}
        <span class="badge bg-dark border border-secondary">Chuoi thang: ${cap.consecutive_win || 0}</span>
        <span class="badge bg-dark border border-secondary">Chuoi thua: ${cap.consecutive_loss || 0}</span>
      </div>`;
    setHtml('strategy-status-body', stratHtml);
    setHtml('strategy-current-status', stratHtml);

    // TP/SL displays
    setText('tp-display', ctrl.daily_take_profit_usd > 0 ? '+$' + ctrl.daily_take_profit_usd : 'tắt');
    setText('sl-display', ctrl.daily_stop_loss_usd > 0 ? '-$' + ctrl.daily_stop_loss_usd : 'tắt');
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
    showToast('Huấn luyện tổng hợp đã hoàn thành!', 'success');
  } else {
    body.innerHTML = `<div class="text-danger">Lỗi huấn luyện. Hãy kiểm tra nhật ký máy chủ.</div>`;
    showToast('Lỗi huấn luyện tổng hợp', 'danger');
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
        <span class="badge bg-dark border border-secondary ${wrClass}">Thang: ${result.win_rate_pct}%</span>
        <span class="badge bg-dark border border-secondary">Dac trung: ${result.n_features}</span>
        <span class="badge bg-success bg-opacity-25">Thang: ${result.n_wins}</span>
        <span class="badge bg-danger bg-opacity-25">Thua: ${result.n_losses}</span>
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
    showToast('Tiến hóa đã hoàn tất! Genome vô địch đã được lưu.', 'success');
    await loadEvolutionStatus();
  } else {
    showToast('Lỗi tiến hóa — hãy kiểm tra nhật ký máy chủ', 'danger');
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
      `Đã áp dụng genome vô địch! min_score=${result.min_signal_score?.toFixed(1)} ` +
      `rsi_os=${result.rsi_oversold?.toFixed(1)}`, 'success'
    );
    await loadStats();
  } else {
    showToast('Không có genome vô địch để áp dụng', 'warning');
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
      <span class="badge bg-success">Do phu hop: ${fitness}</span>
      <span class="badge bg-info">Ty le thang: ${wr}%</span>
      <span class="badge bg-primary">PF: ${pf}</span>
      <span class="badge bg-secondary">Lenh: ${c.n_trades || 0}</span>
      <span class="badge bg-dark border border-secondary">The he: ${c.generation || 0}</span>
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
          <div class="gene-label">rsi_qua_ban / qua_mua</div>
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

// ── Game Theory Engine ───────────────────────────────────────────

async function runGameTheory() {
  const regime    = document.getElementById('gt-regime').value;
  const rounds    = parseInt(document.getElementById('gt-rounds').value) || 100;
  const opponents = parseInt(document.getElementById('gt-opponents').value) || 4;
  const btn       = document.getElementById('gt-run-btn');
  const prog      = document.getElementById('gt-progress');

  btn.disabled       = true;
  prog.style.display = '';

  const result = await apiPost('/gametheory/simulate', {
    current_regime : regime || '',
    n_rounds       : rounds,
    n_opponents    : opponents,
    trade_outcomes : [],
  });
  prog.style.display = 'none';
  btn.disabled = false;

  if (result && result.status === 'ok') {
    renderGTRecommendation(result);
    renderGTNash(result.nash_solutions || []);
    renderGTMatrix(result);
    renderGTOpponent(result.opponent_beliefs || {});
    renderGTEXP3(result.exp3_weights || {});
    renderGTPressure(result.pressure_analysis || {});
    renderGTEcosystem(result.ecosystem_state || {});
    renderGTAgents((result.ecosystem_state || {}).agent_states || []);
    renderGTInsights(result.insights || []);

    ['gt-rec-card','gt-nash-card','gt-matrix-card','gt-opp-card','gt-exp3-card',
     'gt-pressure-card','gt-eco-card','gt-agents-card','gt-insights-card']
    .forEach(id => {
      const el = document.getElementById(id);
      if (el) el.style.display = '';
    });

    showToast(
      `Game Theory: action=${result.recommended_action} pressure=${(result.platform_pressure*100).toFixed(0)}%`,
      'warning'
    );
  } else {
    showToast('Game theory analysis thất bại', 'warning');
  }
}

async function loadGameTheoryReport() {
  const result = await apiGet('/gametheory/report');
  if (!result || result.status === 'no_report' || result.status === 'no_data') return;

  renderGTRecommendation(result);
  renderGTNash(result.nash_solutions || []);
  renderGTOpponent(result.opponent_beliefs || {});
  renderGTEXP3(result.exp3_weights || {});
  renderGTPressure(result.pressure_analysis || {});
  renderGTEcosystem(result.ecosystem_state || {});
  renderGTAgents((result.ecosystem_state || {}).agent_states || []);
  renderGTInsights(result.insights || []);

  ['gt-rec-card','gt-nash-card','gt-opp-card','gt-exp3-card',
   'gt-pressure-card','gt-eco-card','gt-agents-card','gt-insights-card']
  .forEach(id => {
    const el = document.getElementById(id);
    if (el) el.style.display = '';
  });
}

function renderGTRecommendation(result) {
  const body = document.getElementById('gt-rec-body');
  if (!body) return;

  const action   = result.recommended_action || '?';
  const pressure = (result.platform_pressure || 0) * 100;
  const crowding = (result.crowding_index || 0) * 100;
  const nashPayoff = result.nash_payoff || 0;
  const opp      = result.dominant_opponent || '?';
  const conf     = (result.opponent_concentration || 0) * 100;

  const actionColor = {CALL:'text-success', PUT:'text-danger', SKIP:'text-warning'}[action] || 'text-info';
  const pressBadge  = pressure > 50 ? 'bg-danger' : pressure > 20 ? 'bg-warning' : 'bg-success';
  const crowdBadge  = crowding > 50 ? 'bg-danger' : crowding > 30 ? 'bg-warning' : 'bg-success';

  body.innerHTML = `
    <div class="d-flex justify-content-around mb-2">
      <div class="text-center">
        <div class="fs-4 fw-bold ${actionColor}">${action}</div>
        <div class="text-muted small">Hành động Nash</div>
      </div>
      <div class="text-center">
        <div class="fs-5 fw-bold text-info">${nashPayoff.toFixed(4)}</div>
        <div class="text-muted small">Payoff Nash mỗi lệnh</div>
      </div>
    </div>
    <div class="d-flex gap-2 justify-content-center flex-wrap small">
      <span class="badge ${pressBadge} bg-opacity-25 text-light">
        Áp lực nền tảng: ${pressure.toFixed(0)}%
      </span>
      <span class="badge ${crowdBadge} bg-opacity-25 text-light">
        Mật độ cạnh tranh: ${crowding.toFixed(0)}%
      </span>
      <span class="badge bg-info bg-opacity-25 text-info">
        Đối thủ: ${opp} (${conf.toFixed(0)}%)
      </span>
    </div>`;
}

function renderGTNash(nashList) {
  const body = document.getElementById('gt-nash-body');
  if (!body) return;
  if (!nashList || !nashList.length) {
    body.innerHTML = '<div class="text-muted small">Không tìm thấy cân bằng Nash</div>';
    return;
  }

  body.innerHTML = nashList.slice(0,2).map((ne, idx) => {
    const s = ne.our_strategy || [0.33, 0.33, 0.34];
    const barFor = (label, val, color) =>
      `<div class="d-flex align-items-center gap-1 mb-1">
        <span class="small ${color}" style="width:40px">${label}</span>
        <div class="progress flex-grow-1 bg-dark" style="height:8px">
          <div class="progress-bar ${color.replace('text-','bg-')}"
               style="width:${Math.round(val*100)}%"></div>
        </div>
        <span class="small ${color}" style="width:35px">${(val*100).toFixed(0)}%</span>
      </div>`;

    return `<div class="${idx>0?'mt-3 border-top border-secondary pt-2':''}">
      <div class="small text-muted mb-1">${idx+1}. ${ne.type} | payoff=${ne.payoff}</div>
      ${barFor('CALL', s[0]||0, 'text-success')}
      ${barFor('PUT',  s[1]||0, 'text-danger')}
      ${barFor('SKIP', s[2]||0, 'text-warning')}
    </div>`;
  }).join('');
}

function renderGTMatrix(result) {
  const card = document.getElementById('gt-matrix-card');
  const body = document.getElementById('gt-matrix-body');
  if (!card || !body) return;
  card.style.display = '';

  const labels = ['CALL', 'PUT', 'SKIP'];
  const winProb = (1 - (result.platform_pressure||0)*0.5) * 0.55;
  const payout  = 0.85;
  const cd      = 0.10;

  const payoff = (a, o) => {
    if (a === 2) return 0;
    const crowded = a === o && o < 2;
    const eff_b   = payout * (crowded ? (1-cd) : 1.0);
    const win_p   = a === 0 ? winProb : 1-winProb;
    return win_p * eff_b - (1-win_p);
  };

  const cell = (v) => {
    const cls = v > 0 ? 'text-success' : v < 0 ? 'text-danger' : 'text-muted';
    return `<td class="text-center ${cls} small" style="width:70px">${v.toFixed(3)}</td>`;
  };

  body.innerHTML = `
    <table class="table table-dark table-sm table-bordered text-center mb-0" style="font-size:0.72rem">
      <thead>
        <tr>
          <th class="text-muted">Us \\ Opp</th>
          ${labels.map(l=>`<th class="text-muted">${l}</th>`).join('')}
        </tr>
      </thead>
      <tbody>
        ${labels.map((la,a) => `<tr>
          <th class="text-muted">${la}</th>
          ${labels.map((_,o) => cell(payoff(a,o))).join('')}
        </tr>`).join('')}
      </tbody>
    </table>
    <div class="text-muted small mt-1">Lợi ích trên mỗi đơn vị vốn | xanh=lãi, đỏ=lỗ</div>`;
}

function renderGTOpponent(beliefs) {
  const body = document.getElementById('gt-opp-body');
  if (!body) return;

  const typeBeliefs = beliefs.beliefs || {};
  const conc        = (beliefs.concentration || 0) * 100;
  const nObs        = beliefs.n_observations || 0;
  const predicted   = beliefs.predicted_next || {};

  const typeColors = {
    TREND_FOLLOWER:'text-success', MEAN_REVERTER:'text-danger',
    MOMENTUM:'text-warning', RANDOM_BOT:'text-secondary'
  };

  const typeBeliefRows = Object.entries(typeBeliefs).map(([t, p]) =>
    `<div class="d-flex align-items-center gap-1 mb-1">
      <span class="small ${typeColors[t]||'text-muted'}" style="min-width:110px">${t}</span>
      <div class="progress flex-grow-1 bg-dark" style="height:6px">
        <div class="progress-bar ${(typeColors[t]||'text-secondary').replace('text-','bg-')}"
             style="width:${Math.round(p*100)}%"></div>
      </div>
      <span class="small text-muted">${(p*100).toFixed(0)}%</span>
    </div>`
  ).join('');

  body.innerHTML = `
    <div class="mb-2">${typeBeliefRows}</div>
    <div class="d-flex justify-content-between small text-muted border-top border-secondary pt-2">
      <span>Độ tin cậy: <strong class="text-info">${conc.toFixed(0)}%</strong></span>
      <span>Số quan sát: ${nObs}</span>
    </div>
    <div class="mt-1 small text-muted">Dự báo tiếp theo:
      <span class="text-success">CALL=${((predicted.CALL||0)*100).toFixed(0)}%</span>
      <span class="text-danger ms-2">PUT=${((predicted.PUT||0)*100).toFixed(0)}%</span>
      <span class="text-warning ms-2">SKIP=${((predicted.SKIP||0)*100).toFixed(0)}%</span>
    </div>`;
}

function renderGTEXP3(exp3) {
  const body = document.getElementById('gt-exp3-body');
  if (!body) return;

  const ws     = exp3.weights || {};
  const ms     = exp3.mixed_strategy || {};
  const greedy = exp3.greedy_action || '?';
  const rounds = exp3.rounds || 0;
  const regret = exp3.estimated_regret || 0;
  const bound  = exp3.regret_bound || 0;
  const gamma  = exp3.gamma || 0.10;
  const eta    = exp3.eta   || 0.10;

  const actionColors = {CALL:'text-success', PUT:'text-danger', SKIP:'text-warning'};

  const barFor = (k) => {
    const w = ws[k] || 0;
    const p = (ms[k] || 0) * 100;
    const wMax = Math.max(...Object.values(ws), 1);
    const pct  = Math.round(w / wMax * 100);
    return `
      <div class="d-flex align-items-center gap-1 mb-1">
        <span class="small ${actionColors[k]||'text-muted'}" style="width:40px">${k}</span>
        <div class="progress flex-grow-1 bg-dark" style="height:8px">
          <div class="progress-bar ${(actionColors[k]||'text-secondary').replace('text-','bg-')}"
               style="width:${pct}%"></div>
        </div>
        <span class="small text-muted">${p.toFixed(0)}%</span>
      </div>`;
  };

  body.innerHTML = `
    ${['CALL','PUT','SKIP'].map(barFor).join('')}
    <div class="d-flex justify-content-between small text-muted border-top border-secondary pt-2 mt-1">
      <span>Hanh dong tham lam: <strong class="text-info">${greedy}</strong></span>
      <span>Vong: ${rounds}</span>
    </div>
    <div class="small text-muted mt-1">
      Regret: ${regret.toFixed(1)} / bound ${bound.toFixed(0)}
      &nbsp;|&nbsp; γ=${gamma} η=${eta}
    </div>`;
}

function renderGTPressure(pa) {
  const body = document.getElementById('gt-pressure-body');
  if (!body) return;

  const score  = (pa.pressure_score || 0) * 100;
  const pressCls = score > 50 ? 'bg-danger' : score > 20 ? 'bg-warning' : 'bg-success';
  const wr_r   = pa.win_rate_recent;
  const wr_h   = pa.win_rate_historical;
  const signals = pa.signals || [];

  body.innerHTML = `
    <div class="mb-2">
      <div class="d-flex justify-content-between small mb-1">
        <span class="text-muted">Diem ap luc</span>
        <span class="${score>50?'text-danger':score>20?'text-warning':'text-success'} fw-bold">${score.toFixed(0)}%</span>
      </div>
      <div class="progress bg-dark" style="height:10px">
        <div class="progress-bar ${pressCls}" style="width:${Math.round(score)}%"></div>
      </div>
    </div>
    ${wr_r != null ? `<div class="small text-muted mb-2">
      WR gần đây: <span class="${wr_r < wr_h ? 'text-danger':'text-success'}">${(wr_r*100).toFixed(1)}%</span>
      vs lịch sử: ${(wr_h*100).toFixed(1)}%
    </div>` : ''}
    ${signals.map(s=>`<div class="small text-muted mb-1">• ${s}</div>`).join('')}`;
}

function renderGTEcosystem(eco) {
  const body = document.getElementById('gt-eco-body');
  if (!body) return;

  const crowding  = (eco.crowding_avg   || 0) * 100;
  const pressure  = (eco.pressure_final || 0) * 100;
  const nashDist  = eco.nash_distance  || 0;
  const ours      = eco.our_state || {};

  const cwHist = eco.crowding_history || [];
  const prHist = eco.pressure_history || [];

  const sparkline = (data, color) => {
    if (!data.length) return '';
    const mx = Math.max(...data, 0.01);
    return `<div class="d-flex align-items-end gap-px" style="height:28px">
      ${data.map(v=>`<div style="flex:1;height:${Math.round(v/mx*100)}%;background:${color};opacity:0.7;min-height:1px"></div>`).join('')}
    </div>`;
  };

  body.innerHTML = `
    <div class="row g-2 text-center mb-2">
      <div class="col-3">
        <div class="fw-bold text-warning">${crowding.toFixed(0)}%</div>
        <div class="text-muted" style="font-size:0.7rem">Crowding</div>
      </div>
      <div class="col-3">
        <div class="fw-bold ${pressure>30?'text-danger':'text-success'}">${pressure.toFixed(0)}%</div>
        <div class="text-muted" style="font-size:0.7rem">Pressure</div>
      </div>
      <div class="col-3">
        <div class="fw-bold text-info">${nashDist.toFixed(3)}</div>
        <div class="text-muted" style="font-size:0.7rem">Khoảng cách Nash</div>
      </div>
      <div class="col-3">
        <div class="fw-bold text-light">${((ours.win_rate||0)*100).toFixed(1)}%</div>
        <div class="text-muted" style="font-size:0.7rem">Ty le thang cua ta</div>
      </div>
    </div>
    <div class="mb-1">
      <div class="text-muted" style="font-size:0.7rem">Xu huong mat do canh tranh</div>
      ${sparkline(cwHist, '#ffc107')}
    </div>
    <div>
      <div class="text-muted" style="font-size:0.7rem">Xu huong ap luc nen tang</div>
      ${sparkline(prHist, '#dc3545')}
    </div>`;
}

function renderGTAgents(agents) {
  const tbody = document.querySelector('#gt-agents-table tbody');
  if (!tbody) return;

  tbody.innerHTML = (agents || []).map(a => {
    const wr    = ((a.win_rate || 0) * 100).toFixed(1);
    const pnl   = (a.total_pnl || 0).toFixed(3);
    const isUs  = a.agent_id === 'us';
    const pnlCls = a.total_pnl > 0 ? 'text-success' : 'text-danger';
    return `<tr ${isUs?'class="table-active"':''}>
      <td class="small ${isUs?'text-info fw-bold':'text-muted'}">${a.agent_id}</td>
      <td class="small text-muted" style="font-size:0.7rem">${a.agent_type}</td>
      <td class="small">${wr}%</td>
      <td class="small ${pnlCls}">${pnl}</td>
    </tr>`;
  }).join('');
}

function renderGTInsights(insights) {
  const card = document.getElementById('gt-insights-card');
  const body = document.getElementById('gt-insights-body');
  if (!card || !body) return;
  card.style.display = '';
  body.innerHTML = (insights || []).map(i =>
    `<div class="d-flex gap-2 mb-1 small">
       <span class="text-warning flex-shrink-0">•</span>
       <span class="text-muted">${i}</span>
     </div>`
  ).join('');
}

// ── Utility AI Engine ────────────────────────────────────────────

const UTILITY_PRESETS = {
  balanced   : [35, 30, 20, 15],
  aggressive : [60, 10, 20, 10],
  conservative:[20, 50, 10, 20],
  speed      : [20, 10, 60, 10],
  stable     : [20, 30, 10, 40],
};

function applyUtilityPreset() {
  const preset = document.getElementById('utility-preset').value;
  if (preset === 'custom' || !UTILITY_PRESETS[preset]) return;
  const [g, t, sp, st] = UTILITY_PRESETS[preset];
  document.getElementById('slider-growth').value    = g;
  document.getElementById('slider-trust').value     = t;
  document.getElementById('slider-speed').value     = sp;
  document.getElementById('slider-stability').value = st;
  onUtilitySlider();
}

function onUtilitySlider() {
  const g  = parseInt(document.getElementById('slider-growth').value);
  const t  = parseInt(document.getElementById('slider-trust').value);
  const sp = parseInt(document.getElementById('slider-speed').value);
  const st = parseInt(document.getElementById('slider-stability').value);
  const total = g + t + sp + st;

  document.getElementById('val-growth').textContent    = `${g}%`;
  document.getElementById('val-trust').textContent     = `${t}%`;
  document.getElementById('val-speed').textContent     = `${sp}%`;
  document.getElementById('val-stability').textContent = `${st}%`;

  const sumEl = document.getElementById('utility-weights-sum');
  if (total === 0) {
    sumEl.textContent = '⚠️ Tổng = 0';
    sumEl.className = 'text-danger small text-center mt-1';
  } else {
    sumEl.textContent = `Tổng: ${total} → normalized ×${(100/total).toFixed(2)}`;
    sumEl.className = 'text-muted small text-center mt-1';
  }

  // Switch preset to "custom" when user drags manually
  const presetEl = document.getElementById('utility-preset');
  if (presetEl) {
    const presetVals = UTILITY_PRESETS[presetEl.value];
    if (!presetVals || presetVals[0] !== g || presetVals[1] !== t ||
        presetVals[2] !== sp || presetVals[3] !== st) {
      presetEl.value = 'custom';
    }
  }
}

async function runUtilityOptimize() {
  const preset = document.getElementById('utility-preset').value;
  const regime = document.getElementById('utility-regime').value;
  const btn    = document.getElementById('utility-run-btn');
  const prog   = document.getElementById('utility-progress');

  btn.disabled       = true;
  prog.style.display = '';

  const g  = parseInt(document.getElementById('slider-growth').value)    / 100;
  const t  = parseInt(document.getElementById('slider-trust').value)     / 100;
  const sp = parseInt(document.getElementById('slider-speed').value)     / 100;
  const st = parseInt(document.getElementById('slider-stability').value) / 100;

  const body = {
    preset         : preset === 'custom' ? 'custom' : preset,
    growth         : g,
    trust          : t,
    speed          : sp,
    stability      : st,
    current_regime : regime || '',
  };

  const result = await apiPost('/utility/optimize', body);
  prog.style.display = 'none';
  btn.disabled = false;

  if (result && result.status === 'ok') {
    renderUtilityOptimal(result);
    renderUtilityScores(result.top_scores || []);
    renderUtilityTemporal(result.temporal_analysis || {});
    renderUtilityCausal(result.causal_alignment || {});
    renderUtilityInsights(result.insights || []);
    renderUtilityKelly(result.utility_breakdown, result.top_scores);
    document.getElementById('utility-scores-card').style.display   = '';
    document.getElementById('utility-kelly-card').style.display    = '';
    document.getElementById('utility-temporal-card').style.display = '';
    document.getElementById('utility-causal-card').style.display   = '';
    showToast(
      `Utility: toi uu=${result.optimal_genome_id}  Kelly=${(result.kelly_stake*100).toFixed(1)}%`,
      'info'
    );
    await loadParetoAnalysis();
  } else {
    showToast('Tối ưu utility thất bại — hãy chạy Tiến hóa + Meta trước', 'warning');
  }
}

async function loadUtilityReport() {
  const result = await apiGet('/utility/report');
  if (!result || result.status === 'no_report') return;

  renderUtilityOptimal(result);
  renderUtilityScores((result.scores || []).slice(0, 10));
  renderUtilityTemporal(result.temporal_analysis || {});
  renderUtilityCausal(result.causal_alignment   || {});
  renderUtilityInsights(result.insights || []);
  renderUtilityKelly(result.utility_breakdown, result.scores);

  document.getElementById('utility-scores-card').style.display   = '';
  document.getElementById('utility-kelly-card').style.display    = '';
  document.getElementById('utility-temporal-card').style.display = '';
  document.getElementById('utility-causal-card').style.display   = '';

  await loadParetoAnalysis();
}

async function loadParetoAnalysis() {
  const result = await apiGet('/utility/pareto');
  if (result && result.status === 'ok') {
    renderParetoAnalysis(result.pareto_by_preset || {});
  }
}

function renderUtilityOptimal(result) {
  const card = document.getElementById('utility-optimal-card');
  const body = document.getElementById('utility-optimal-body');
  if (!card || !body) return;
  card.style.display = '';

  const gid    = result.optimal_genome_id || '?';
  const kelly  = (result.kelly_stake || 0) * 100;
  const front  = result.pareto_front_size || 0;
  const n_eval = result.n_evaluated || 0;
  const bd     = result.utility_breakdown || {};
  const w      = result.weights || {};

  const barHtml = (label, val, color) => `
    <div class="mb-1">
      <div class="d-flex justify-content-between small">
        <span class="${color}">${label}</span>
        <span class="${color}">${(val*100).toFixed(1)}%</span>
      </div>
      <div class="progress bg-dark" style="height:6px">
        <div class="progress-bar ${color.replace('text-','bg-')}" style="width:${Math.round(val*100)}%"></div>
      </div>
    </div>`;

  body.innerHTML = `
    <div class="d-flex justify-content-between mb-2">
      <div>
        <div class="text-info fw-bold">${gid}</div>
        <div class="text-muted small">
          Pareto front: <strong class="text-success">${front}</strong> genomes
          &nbsp;|&nbsp; Kho: ${n_eval} evaluated
        </div>
      </div>
      <div class="text-center">
        <div class="fs-5 text-warning fw-bold">${kelly.toFixed(1)}%</div>
        <div class="text-muted small">Vốn Kelly</div>
      </div>
    </div>
    ${barHtml('📈 Tang truong',    bd.growth    || 0, 'text-warning')}
    ${barHtml('🛡️ Tin cay',    bd.trust     || 0, 'text-success')}
    ${barHtml('⚡ Toc do',     bd.speed     || 0, 'text-info')}
    ${barHtml('🏔️ On dinh',bd.stability || 0, 'text-secondary')}
    <div class="mt-2 text-center small text-muted">
      Weighted score: <strong class="text-light">${((bd.weighted||0)*100).toFixed(2)}%</strong>
      &nbsp;|&nbsp; Weights: g=${(w.growth||0)*100|0}% t=${(w.trust||0)*100|0}%
      sp=${(w.speed||0)*100|0}% st=${(w.stability||0)*100|0}%
    </div>`;
}

function renderUtilityScores(scores) {
  const tbody = document.querySelector('#utility-scores-table tbody');
  if (!tbody) return;

  const bar = (v) => {
    const pct = Math.round((v || 0) * 100);
    const col = pct > 66 ? 'bg-success' : pct > 33 ? 'bg-warning' : 'bg-danger';
    return `<div class="progress bg-dark" style="height:4px;width:40px;display:inline-block;vertical-align:middle">
      <div class="progress-bar ${col}" style="width:${pct}%"></div></div>`;
  };

  tbody.innerHTML = (scores || []).map(s => {
    const star = s.is_pareto_optimal
      ? '<span class="text-info" title="Pareto optimal">★</span> '
      : '';
    return `
      <tr>
        <td class="small">${star}${s.genome_id}</td>
        <td>${bar(s.growth_utility)}</td>
        <td>${bar(s.trust_utility)}</td>
        <td>${bar(s.speed_utility)}</td>
        <td>${bar(s.stability_utility)}</td>
        <td class="small text-info fw-bold">${((s.weighted_utility||0)*100).toFixed(1)}</td>
        <td class="small text-warning">${((s.kelly_fraction||0)*100).toFixed(1)}%</td>
      </tr>`;
  }).join('');
}

function renderUtilityTemporal(ta) {
  const body = document.getElementById('utility-temporal-body');
  if (!body || !ta || !Object.keys(ta).length) return;

  const bar = (label, v, color) => `
    <div class="mb-2">
      <div class="d-flex justify-content-between small">
        <span class="${color}">${label}</span>
        <span class="${color}">${((v||0)*100).toFixed(1)}%</span>
      </div>
      <div class="progress bg-dark" style="height:8px">
        <div class="progress-bar ${color.replace('text-','bg-')}"
             style="width:${Math.round((v||0)*100)}%"></div>
      </div>
    </div>`;

  body.innerHTML = `
    ${bar('⚡ Short-term  (H=5 trades)',  ta.short_term_utility,  'text-warning')}
    ${bar('⚖️ Medium-term (H=20 trades)', ta.medium_term_utility, 'text-info')}
    ${bar('🔭 Long-term   (H=50 trades)', ta.long_term_utility,   'text-success')}
    ${bar('🎯 Temporal composite',         ta.temporal_utility,    'text-light')}
    <div class="text-muted small mt-2">
      ${ta.preference_type || ''} (λ=${ta.discount_rate || 0.2})
    </div>`;
}

function renderUtilityCausal(alignment) {
  const body = document.getElementById('utility-causal-body');
  if (!body || !alignment || !Object.keys(alignment).length) {
    if (body) body.innerHTML = '<div class="text-muted">Cần chạy phân tích nhân quả trước</div>';
    return;
  }

  const axisColors = {growth:'text-warning', trust:'text-success', speed:'text-info', stability:'text-muted'};
  const qualityBadge = (q) => ({
    good   : '<span class="badge bg-success bg-opacity-25 text-success">✅ Good</span>',
    partial: '<span class="badge bg-warning bg-opacity-25 text-warning">⚠️ Partial</span>',
    unknown: '<span class="badge bg-secondary bg-opacity-25 text-secondary">? Unknown</span>',
  }[q] || '');

  body.innerHTML = Object.entries(alignment).map(([axis, data]) => `
    <div class="mb-2">
      <div class="d-flex justify-content-between">
        <span class="${axisColors[axis] || 'text-muted'}">${axis}</span>
        ${qualityBadge(data.alignment_quality)}
      </div>
      ${data.causal_drivers?.length ? `<div class="text-success" style="font-size:0.7rem">Nhan qua: ${data.causal_drivers.join(', ')}</div>` : ''}
      ${data.spurious_present?.length ? `<div class="text-danger" style="font-size:0.7rem">Gia tuong quan: ${data.spurious_present.join(', ')}</div>` : ''}
    </div>`
  ).join('');
}

function renderUtilityInsights(insights) {
  const card = document.getElementById('utility-insights-card');
  const body = document.getElementById('utility-insights-body');
  if (!card || !body || !insights.length) return;
  card.style.display = '';
  body.innerHTML = insights.map(i =>
    `<div class="d-flex gap-2 mb-1 small">
       <span class="text-info flex-shrink-0">•</span>
       <span class="text-muted">${i}</span>
     </div>`
  ).join('');
}

function renderUtilityKelly(breakdown, scores) {
  const body = document.getElementById('utility-kelly-body');
  if (!body) return;

  // Find the best genome's win_rate for Kelly curve display
  const best = (scores || [])[0];
  if (!best) {
    body.innerHTML = '<div class="text-muted small">Khong co du lieu</div>';
    return;
  }
  const wr      = best.win_rate_pct || 55;
  const kelly   = best.kelly_fraction || 0;
  const breakev = 54.0;  // approx for 85% payout

  body.innerHTML = `
    <div class="row text-center mb-2 g-1">
      <div class="col">
        <div class="fw-bold text-warning">${wr.toFixed(1)}%</div>
        <div class="text-muted" style="font-size:0.72rem">Win Rate</div>
      </div>
      <div class="col">
        <div class="fw-bold text-success">${(kelly*100).toFixed(1)}%</div>
        <div class="text-muted" style="font-size:0.72rem">Vốn Kelly</div>
      </div>
      <div class="col">
        <div class="fw-bold text-info">${breakev.toFixed(1)}%</div>
        <div class="text-muted" style="font-size:0.72rem">Breakeven WR</div>
      </div>
    </div>
    <div class="text-muted small">
      <strong>f* = (p·b−q)/b</strong> &nbsp;|&nbsp;
      p=${(wr/100).toFixed(3)} b=0.85 &nbsp;→&nbsp;
      full Kelly = ${((wr/100*0.85-(1-wr/100))/0.85*100).toFixed(1)}%
      &nbsp;→&nbsp; quarter = ${(kelly*100).toFixed(1)}%
    </div>
    <div class="text-muted small mt-1">
      Kelly 1/4 bảo vệ tránh ruin trong chuỗi thua liên tiếp.
    </div>`;
}

function renderParetoAnalysis(byPreset) {
  const card = document.getElementById('utility-pareto-card');
  const body = document.getElementById('utility-pareto-body');
  if (!card || !body) return;
  card.style.display = '';

  const presetEmojis = {
    balanced:'⚖️', aggressive:'🚀', conservative:'🛡️', speed:'⚡', stable:'🏔️'
  };

  body.innerHTML = Object.entries(byPreset).map(([preset, res]) => `
    <div class="d-flex justify-content-between align-items-center mb-2 small">
      <div>
        <span class="text-muted">${presetEmojis[preset]||'?'} ${preset}</span>
        <div class="text-info" style="font-size:0.7rem">
          g=${((res.growth||0)*100).toFixed(0)}
          t=${((res.trust||0)*100).toFixed(0)}
          sp=${((res.speed||0)*100).toFixed(0)}
          st=${((res.stability||0)*100).toFixed(0)}
        </div>
      </div>
      <div class="text-right">
        <div class="text-light">${res.optimal_genome_id || '?'}</div>
        <div class="text-muted" style="font-size:0.7rem">
          score=${((res.weighted_utility||0)*100).toFixed(1)}%
          kelly=${((res.kelly_fraction||0)*100).toFixed(1)}%
        </div>
      </div>
    </div>`
  ).join('') || '<div class="text-muted small">Chưa có dữ liệu</div>';
}

// ── Causal AI Engine ─────────────────────────────────────────────

async function runCausalAnalyze() {
  const fastMode = document.getElementById('causal-fast-mode').checked;
  const btn      = document.getElementById('causal-run-btn');
  const prog     = document.getElementById('causal-progress');
  const label    = document.getElementById('causal-progress-label');

  btn.disabled       = true;
  prog.style.display = '';
  label.textContent  = fastMode
    ? 'Phân tích partial correlation…'
    : 'Chạy do-calculus interventions (có thể mất 30-60s)…';

  const result = await apiPost('/causal/analyze', { fast_mode: fastMode });

  prog.style.display = 'none';
  btn.disabled = false;

  if (result && result.status === 'ok') {
    renderCausalInsights(result.insights);
    renderCausalEffects(result.top_effects);
    renderCausalSummary({
      pool_size    : result.pool_size,
      causal_genes : result.causal_genes,
      spurious_genes: result.spurious_genes,
      neutral_genes : result.neutral_genes,
    });
    showToast(
      `Nhan qua: ${result.causal_genes.length} causal, ${result.spurious_genes.length} spurious genes`,
      'warning'
    );
    await loadCausalReport();
  } else {
    showToast('Causal analysis thất bại — chạy evolution trước để tích lũy dữ liệu', 'warning');
  }
}

async function loadCausalReport() {
  const result = await apiGet('/causal/report');
  if (!result || result.status === 'no_report') return;

  renderCausalInsights(result.insights || []);
  renderCausalEffects((result.effects || []).slice(0, 10));
  renderCausalRegimeMap(result.regime_fitness || []);
  renderCausalWorldModel(result.world_model || {});
  renderCausalSummary({
    pool_size    : result.pool_size,
    causal_genes : result.causal_genes || [],
    spurious_genes: result.spurious_genes || [],
    neutral_genes : result.neutral_genes || [],
  });
  renderCausalCF(result.counterfactuals || []);
}

async function queryCausalCounterfactual() {
  const from = document.getElementById('cf-regime-from').value;
  const to   = document.getElementById('cf-regime-to').value;
  const result = await apiGet(`/causal/counterfactual?regime_from=${from}&regime_to=${to}`);
  if (result && result.status === 'ok') {
    renderCausalCFResult(result.counterfactuals, result.n_survived, from, to);
  }
}

function renderCausalInsights(insights) {
  const card = document.getElementById('causal-insights-card');
  const body = document.getElementById('causal-insights-body');
  if (!card || !body || !insights.length) return;
  card.style.display = '';
  body.innerHTML = insights.map(i =>
    `<div class="d-flex gap-2 mb-1 small">
       <span class="text-warning flex-shrink-0">•</span>
       <span class="text-muted">${i}</span>
     </div>`
  ).join('');
}

function renderCausalEffects(effects) {
  const card  = document.getElementById('causal-effects-card');
  const tbody = document.querySelector('#causal-effects-table tbody');
  if (!card || !tbody) return;
  card.style.display = '';

  tbody.innerHTML = (effects || []).map(e => {
    let rowClass = '';
    let badge    = '';
    if (e.is_causal && !e.is_spurious) {
      rowClass = 'table-success bg-opacity-10';
      badge    = '<span class="badge bg-success bg-opacity-25 text-success" style="font-size:0.65rem">C</span>';
    } else if (e.is_spurious) {
      rowClass = 'table-danger bg-opacity-10';
      badge    = '<span class="badge bg-danger bg-opacity-25 text-danger" style="font-size:0.65rem">S</span>';
    } else {
      rowClass = '';
      badge    = '<span class="badge bg-secondary bg-opacity-25 text-muted" style="font-size:0.65rem">N</span>';
    }
    const opt = e.optimal_range || [0, 0];
    const aceStr = (e.causal_ace !== undefined)
      ? (e.causal_ace >= 0 ? '+' : '') + e.causal_ace.toFixed(4)
      : '—';
    return `
      <tr class="${rowClass}">
        <td class="small">${badge} ${e.gene}</td>
        <td class="small ${e.causal_ace > 0 ? 'text-success' : 'text-danger'}">${aceStr}</td>
        <td class="small text-muted">${(e.simple_rho||0).toFixed(3)}</td>
        <td class="small text-muted">${(e.partial_rho||0).toFixed(3)}</td>
        <td class="small ${e.spurious_score > 0.15 ? 'text-danger' : 'text-muted'}">${(e.spurious_score||0).toFixed(3)}</td>
        <td class="small text-info" style="font-size:0.7rem">${opt[0].toFixed(1)}–${opt[1].toFixed(1)}</td>
      </tr>`;
  }).join('');
}

function renderCausalRegimeMap(regimeMaps) {
  const card  = document.getElementById('causal-regime-card');
  const tbody = document.querySelector('#causal-regime-table tbody');
  if (!card || !tbody) return;
  card.style.display = '';

  const regimeEmojis = {
    trend_up: '📈', trend_down: '📉', choppy: '↔',
    high_vol_choppy: '🌊', crash: '💥', spike: '🚀', recovery: '🔄',
  };

  tbody.innerHTML = (regimeMaps || []).slice(0, 10).map(m => {
    const emoji = regimeEmojis[m.regime_champion] || '?';
    const survColor = m.survivability >= 0.7 ? 'text-success'
                    : m.survivability >= 0.4 ? 'text-warning' : 'text-danger';
    return `
      <tr>
        <td class="small text-muted">${m.genome_id}</td>
        <td class="small text-info fw-bold">${(m.robust_score||0).toFixed(3)}</td>
        <td class="small ${survColor}">${Math.round((m.survivability||0)*100)}%</td>
        <td class="small text-muted" style="font-size:0.75rem">${emoji} ${m.regime_champion||'?'}</td>
      </tr>`;
  }).join('');
}

function renderCausalWorldModel(wm) {
  const card = document.getElementById('causal-wm-card');
  const body = document.getElementById('causal-wm-body');
  if (!card || !body || !Object.keys(wm).length) return;
  card.style.display = '';

  const regimes = Object.keys(wm);
  const emojis  = {
    trend_up: '📈', trend_down: '📉', choppy: '↔',
    high_vol_choppy: '🌊', crash: '💥', spike: '🚀', recovery: '🔄',
  };

  // Show top transitions from each regime (top-2 most probable)
  let rows = '';
  for (const from of regimes.slice(0, 5)) {
    const probs = wm[from] || {};
    const sorted = Object.entries(probs).sort((a, b) => b[1] - a[1]).slice(0, 2);
    const toStr  = sorted.map(([r, p]) => `${emojis[r]||r} ${(p*100).toFixed(0)}%`).join('  ');
    rows += `
      <div class="d-flex justify-content-between small mb-1">
        <span class="text-muted">${emojis[from]||'?'} ${from.replace('_',' ')}</span>
        <span class="text-info">${toStr}</span>
      </div>`;
  }
  body.innerHTML = `
    <div class="text-muted small mb-2">2 che do kha nang cao nhat tiep theo:</div>
    ${rows}`;
}

function renderCausalSummary(stats) {
  const card = document.getElementById('causal-summary-card');
  const body = document.getElementById('causal-summary-body');
  if (!card || !body) return;
  card.style.display = '';

  body.innerHTML = `
    <div class="d-flex flex-wrap gap-2 small">
      <div class="text-center p-2 border border-secondary rounded flex-fill">
        <div class="fs-5 text-light fw-bold">${stats.pool_size || 0}</div>
        <div class="text-muted">Kho gene</div>
      </div>
      <div class="text-center p-2 border border-success rounded flex-fill">
        <div class="fs-5 text-success fw-bold">${(stats.causal_genes||[]).length}</div>
        <div class="text-muted">Nhân quả</div>
      </div>
      <div class="text-center p-2 border border-danger rounded flex-fill">
        <div class="fs-5 text-danger fw-bold">${(stats.spurious_genes||[]).length}</div>
        <div class="text-muted">Giả tương quan</div>
      </div>
      <div class="text-center p-2 border border-secondary rounded flex-fill">
        <div class="fs-5 text-muted fw-bold">${(stats.neutral_genes||[]).length}</div>
        <div class="text-muted">Trung tính</div>
      </div>
    </div>
    ${(stats.causal_genes||[]).length ? `<div class="mt-2 small text-success">
      Nhan qua: ${(stats.causal_genes||[]).join(', ')}
    </div>` : ''}
    ${(stats.spurious_genes||[]).length ? `<div class="mt-1 small text-danger">
      Gia tuong quan: ${(stats.spurious_genes||[]).join(', ')}
    </div>` : ''}`;
}

function renderCausalCF(cfs) {
  if (!cfs || !cfs.length) return;
  const card = document.getElementById('causal-cf-card');
  if (card) card.style.display = '';
  renderCausalCFResult(cfs,
    cfs.filter(cf => cf.survived).length,
    cfs[0]?.regime_from || '?',
    cfs[0]?.regime_to   || '?'
  );
}

function renderCausalCFResult(cfs, nSurvived, regimeFrom, regimeTo) {
  const cfCard = document.getElementById('causal-cf-card');
  const result = document.getElementById('causal-cf-result');
  if (cfCard) cfCard.style.display = '';
  if (!result) return;

  const survived    = cfs.filter(cf => cf.survived);
  const notSurvived = cfs.filter(cf => !cf.survived);
  const emojis = {
    trend_up: '📈', trend_down: '📉', choppy: '↔',
    high_vol_choppy: '🌊', crash: '💥', spike: '🚀', recovery: '🔄',
  };

  result.innerHTML = `
    <div class="mb-2 small text-muted">
      ${emojis[regimeFrom]||'?'} ${regimeFrom} → ${emojis[regimeTo]||'?'} ${regimeTo}:
      <strong class="${nSurvived > 0 ? 'text-success' : 'text-danger'}">${nSurvived}</strong>
      / ${cfs.length} sống sót
    </div>
    ${survived.slice(0, 3).map(cf => `
      <div class="d-flex justify-content-between small mb-1 text-success">
        <span>✅ ${cf.genome_id}</span>
        <span>${cf.fitness_before.toFixed(4)} → ${cf.fitness_after.toFixed(4)}</span>
      </div>`).join('')}
    ${notSurvived.slice(0, 2).map(cf => `
      <div class="d-flex justify-content-between small mb-1 text-danger">
        <span>❌ ${cf.genome_id}</span>
        <span>${cf.fitness_before.toFixed(4)} → ${cf.fitness_after.toFixed(4)}</span>
      </div>`).join('')}`;
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
    showToast(`Meta lai tạo: ${result.n_seeds} hạt giống từ kho ${result.pool_size} genome`, 'info');
    await loadMetaReport();
  } else {
    showToast('Meta-Learning chưa đủ dữ liệu — hãy chạy tiến hóa trước', 'warning');
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
    const hepBadge = p.hep_range
      ? '<span class="badge bg-success bg-opacity-25 text-success" style="font-size:0.65rem">hẹp</span>'
      : '<span class="badge bg-dark text-muted" style="font-size:0.65rem">rộng</span>';
    const uniMark = p.is_universal ? ' ★' : '';
    return `
      <tr>
        <td class="small ${p.hep_range ? 'text-warning' : 'text-muted'}">${gene}${uniMark}</td>
        <td class="small text-muted">${(p.low_pct||0).toFixed(2)}</td>
        <td class="small text-light">${(p.median||0).toFixed(2)}</td>
        <td class="small text-muted">${(p.high_pct||0).toFixed(2)}</td>
        <td>${hepBadge}</td>
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
          <span class="text-muted small">${arch.n_members} thành viên</span>
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
        <div class="text-muted">Bộ gene</div>
      </div>
      <div class="text-center">
        <div class="fs-5 text-primary fw-bold">${stats.n_archetypes || 0}</div>
        <div class="text-muted">Nguyên mẫu</div>
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

  // Load causal report when causal tab is clicked
  document.querySelectorAll('[href="#tab-causal"]').forEach(el => {
    el.addEventListener('click', () => loadCausalReport());
  });

  // Load utility report when utility tab is clicked
  document.querySelectorAll('[href="#tab-utility"]').forEach(el => {
    el.addEventListener('click', () => loadUtilityReport());
  });
  document.querySelectorAll('[href="#tab-gametheory"]').forEach(el => {
    el.addEventListener('click', () => loadGameTheoryReport());
  });
});
