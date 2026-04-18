# ============================================================
# Deriv Binary Options Robot - Cấu hình
# ============================================================
# Hướng dẫn lấy API Token:
# 1. Đăng nhập tài khoản Deriv tại https://app.deriv.com
# 2. Vào Settings > API Token
# 3. Tạo token với quyền "Trade" và "Read"
# 4. Dán token vào biến DERIV_API_TOKEN bên dưới
# ============================================================

# --- Deriv API ---
DERIV_API_TOKEN = "YOUR_DERIV_API_TOKEN"   # Thay bằng API token thực
DERIV_APP_ID    = 1089                     # App ID mặc định (demo). Tạo app tại https://api.deriv.com/app-registration
DERIV_WS_URL    = f"wss://ws.binaryws.com/websockets/v3?app_id={DERIV_APP_ID}"

# --- Thị trường giao dịch ---
# Các symbol phổ biến trên Deriv:
#   Volatility Index : R_10, R_25, R_50, R_75, R_100
#   Crash/Boom      : CRASH1000, BOOM1000, CRASH500, BOOM500
#   Forex (Binary)  : frxEURUSD, frxGBPUSD, frxUSDJPY
SYMBOL = "R_100"  # Volatility 100 Index

# --- Tham số chiến lược ---
CANDLE_COUNT       = 100    # Số nến lịch sử dùng để tính chỉ báo
GRANULARITY        = 60     # Khung thời gian nến (giây): 60=1m, 300=5m, 900=15m, 3600=1h
RSI_PERIOD         = 14     # Chu kỳ RSI
RSI_OVERSOLD       = 30     # RSI < ngưỡng này → tín hiệu MUA
RSI_OVERBOUGHT     = 70     # RSI > ngưỡng này → tín hiệu BÁN
MOMENTUM_PERIOD    = 10     # Chu kỳ Momentum

# --- Tham số lệnh (Binary Options) ---
TRADE_AMOUNT       = 10     # Số tiền đặt cược (USD)
TRADE_CURRENCY     = "USD"
CONTRACT_DURATION  = 5      # Thời hạn hợp đồng
CONTRACT_DURATION_UNIT = "m"  # Đơn vị: t=giây, m=phút, h=giờ, d=ngày

# --- Redis ---
REDIS_HOST    = "localhost"
REDIS_PORT    = 6379
REDIS_DB      = 0
REDIS_HASH_KEY = "Deriv_Binary_Signal"

# --- Scheduler ---
SCAN_INTERVAL_SECONDS = 60  # Kiểm tra tín hiệu mỗi N giây

# ============================================================
# Hệ thống TỰ VẬN HÀNH
# ============================================================

# --- Danh sách thị trường tự quét ---
# Robot sẽ tự chọn thị trường tốt nhất trong danh sách này
SCAN_SYMBOLS = ["R_10", "R_25", "R_50", "R_75", "R_100"]

# --- Ngưỡng chất lượng tín hiệu ---
# Robot chỉ đặt lệnh khi điểm tín hiệu >= ngưỡng này (0-100)
MIN_SIGNAL_SCORE = 60

# --- Quản lý rủi ro tự động ---
RISK_MAX_DAILY_LOSS_PCT  = 0.20   # Dừng giao dịch khi lỗ >= 20% số dư ban đầu trong ngày
RISK_MAX_CONSECUTIVE_LOSS = 5     # Dừng tạm thời sau N lần thua liên tiếp
RISK_COOLDOWN_MINUTES     = 30    # Nghỉ bao nhiêu phút sau chuỗi thua

# --- Quản lý kích thước lệnh tự động ---
# Dựa trên điểm tín hiệu (0-100) và số dư tài khoản
STAKE_PCT_HIGH   = 0.05   # score >= 80 → 5% số dư
STAKE_PCT_MEDIUM = 0.03   # score 60-79 → 3% số dư
STAKE_PCT_LOW    = 0.02   # score < 60  → 2% số dư (fallback)
STAKE_MIN_USD    = 1.0    # Lệnh tối thiểu (USD)
STAKE_MAX_USD    = 50.0   # Lệnh tối đa (USD)

# --- Redis keys cho trạng thái tự vận hành ---
REDIS_STATE_KEY   = "Deriv_Robot_State"    # Hash: trạng thái rủi ro
REDIS_LOG_KEY     = "Deriv_Trade_Log"      # List: lịch sử lệnh (JSON)

# --- File log giao dịch ---
TRADE_LOG_FILE = "trade_log.csv"

# ============================================================
# Hệ thống PHÂN TÍCH SÓNG (Wave Analyzer — Operator System)
# ============================================================

# Cửa sổ rolling để phát hiện đỉnh/đáy (Swing High/Low)
WAVE_SWING_ORDER = 5

# Kích thước tối thiểu của sóng chính (% so với giá hiện tại)
# Sóng nhỏ hơn ngưỡng này bị bỏ qua
WAVE_MIN_SIZE_PCT = 0.005       # 0.5% giá

# Biên sóng hồi hợp lệ: [min%, max%] của sóng chính
# < 20%  → chưa đủ sâu để tính là sóng hồi
# > 80%  → có thể là đảo chiều, không phải hồi
WAVE_CORRECTION_MIN = 0.20      # 20%
WAVE_CORRECTION_MAX = 0.80      # 80%

# Dung sai xác nhận "tại vùng Fibonacci" (±% khoảng cách sóng)
WAVE_FIB_TOLERANCE = 0.015      # ±1.5%

# ============================================================
# SIMULATOR — Tự mô phỏng (Self-Simulate)
# ============================================================

# Số nến tải về cho backtest (nhiều hơn CANDLE_COUNT để đủ walk-forward)
SIM_CANDLE_COUNT      = 200

# Số nến sau điểm vào để xác định kết quả thắng/thua
# (5 nến × GRANULARITY 60s = 5 phút ~ CONTRACT_DURATION)
SIM_LOOKAHEAD_CANDLES = 5

# Tỉ lệ payout binary options (85% → thắng nhận 85%, thua mất 100%)
SIM_PAYOUT_RATIO      = 0.85

# Stake giả dùng khi mô phỏng
SIM_STAKE_USD         = 10.0

# ============================================================
# LEARNER — Tự học (Self-Learn)
# ============================================================

# Cần ít nhất N lệnh trong lịch sử mới học
LEARNER_MIN_HISTORY     = 20

# Học lại sau mỗi N chu kỳ vận hành
LEARNER_INTERVAL_CYCLES = 10

# Win rate < ngưỡng này → điều kiện tín hiệu bị đánh dấu "yếu"
LEARNER_WEAK_WIN_RATE   = 0.45   # 45%

# ============================================================
# PREDICTOR — Tự dự đoán (Self-Predict)
# ============================================================

# Xác suất thắng tối thiểu để predictor cho phép vào lệnh
PREDICT_MIN_WIN_PROB      = 0.54

# Mức tự tin tối thiểu để vào lệnh
PREDICT_MIN_CONFIDENCE    = 0.30

# Ngưỡng ATR tương đối (ATR / price) xác định biến động cao/thấp
PREDICT_HIGH_VOLATILITY_ATR = 0.005   # > 0.5% → biến động cao
PREDICT_LOW_VOLATILITY_ATR  = 0.001   # < 0.1% → biến động thấp

# ============================================================
# DECISION ENGINE — Điều khiển nhịp vận hành
# ============================================================

# Chạy backtest simulation cho tất cả markets khi khởi động
ENGINE_RUN_SIM_ON_START = True

# Thời gian nghỉ (giây) khi self-heal phát hiện lỗi liên tiếp
HEAL_COOLDOWN_SECONDS   = 60

# ============================================================
# SCALER — Tự scale (Self-Scale)
# ============================================================

# Cần ít nhất N lệnh để đủ cơ sở scale
SCALE_MIN_TRADES      = 15

# Win rate >= ngưỡng này → mở rộng pool thị trường
SCALE_HIGH_WIN_RATE   = 65.0

# Win rate < ngưỡng này → thu hẹp pool thị trường
SCALE_LOW_WIN_RATE    = 45.0

# Kiểm tra scale mỗi N chu kỳ
SCALE_INTERVAL_CYCLES = 20

# ============================================================
# PIPELINE — Dây chuyền điều phối vận hành
# ============================================================

# Số lệnh tối đa trong hàng đợi cùng lúc
PIPELINE_MAX_QUEUE_DEPTH     = 3

# Số lệnh tối đa đang chờ xử lý trong 1 cửa sổ thời gian
# (giới hạn tải — rate limiting)
PIPELINE_RATE_WINDOW_SECONDS = 300    # 5 phút
PIPELINE_RATE_MAX_TRADES     = 3      # Tối đa 3 lệnh / 5 phút

# Khoảng cách tối thiểu giữa 2 lệnh liên tiếp (giây)
# Ngăn "đặt lệnh liên tục" — load spacing
PIPELINE_MIN_TRADE_GAP_SECONDS = 30

# Điểm quyền hạn tối thiểu để lệnh vượt qua cổng xác nhận
# Tổng điểm quyền hạn = signal_score_gate + predictor_gate + risk_gate
# Mỗi cổng đóng góp True/False → tổng tối đa 3
PIPELINE_MIN_AUTHORITY_GATES  = 2     # Cần ít nhất 2/3 cổng thông qua

# Kích thước cửa sổ đo lường (giây)
PIPELINE_METRICS_WINDOW_SECONDS = 3600   # Tính metrics trên 1 giờ gần nhất

# ============================================================
# MEMORY BRAIN — Redis là bộ não trung tâm ghi nhớ Win/Loss
# ============================================================

# Số lệnh tối thiểu trên một mẫu (fingerprint) để xét luật cứng
MEMORY_MIN_SAMPLES_FOR_RULE  = 3

# Tỉ lệ thua tối thiểu để đưa fingerprint vào danh sách chặn cứng
# Fingerprint có loss_rate >= ngưỡng này → luật cứng: BLOCK
MEMORY_HARD_BLOCK_LOSS_RATE  = 0.20      # >= 20% thua → chặn cứng (Hard Rule bắt buộc theo pipeline)

# Tỉ lệ thắng tốt để tăng ưu tiên (priority boost) cho fingerprint
MEMORY_STRONG_WIN_RATE       = 0.65      # >= 65% thắng → bonus ưu tiên

# Số fingerprint tối đa lưu trong Redis (FIFO — cũ nhất bị xóa)
MEMORY_MAX_PATTERNS          = 500

# Redis key prefix cho từng pattern
REDIS_MEMORY_PREFIX          = "Deriv_Mem:"          # + fingerprint

# Redis key lưu danh sách luật cứng (JSON list)
REDIS_MEMORY_RULES_KEY       = "Deriv_Mem_Rules"

# Redis key lưu tổng hợp thống kê memory (JSON)
REDIS_MEMORY_STATS_KEY       = "Deriv_Mem_Stats"

# ============================================================
# CANDLE LIBRARY — Thư viện nến 10.000 mẫu
# ============================================================
CANDLE_LIBRARY_COUNT     = 10000  # Số nến tải về cho thư viện học
CANDLE_LIBRARY_DIR       = "candle_data"  # Thư mục lưu file parquet
CANDLE_LIBRARY_REDIS_KEY = "Deriv_CandleLib:{symbol}"
CANDLE_LIBRARY_REALTIME_KEY = "Deriv_CandleRT:{symbol}"
CANDLE_REALTIME_MAX_CACHE   = 200   # Số nến realtime cache trong Redis

# ============================================================
# ML MODEL STACK — XGBoost / LR / Q-Learning / LSTM
# ============================================================
ML_MODELS_DIR            = "models"
ML_FEATURE_WINDOW        = 60     # Cửa sổ nến cho LSTM sequence
ML_RETRAIN_INTERVAL      = 50     # Retrain sau N lệnh mới
ML_MIN_TRAIN_SAMPLES     = 100    # Cần ít nhất N mẫu để train
ML_ENSEMBLE_WEIGHT_WIN   = 0.40   # Trọng số WinClassifier
ML_ENSEMBLE_WEIGHT_QLEARN= 0.20   # Trọng số Q-Learning
ML_ENSEMBLE_WEIGHT_LSTM  = 0.40   # Trọng số LSTM
ML_ENABLED               = False  # Tắt theo mặc định cho đến khi train xong

# ============================================================
# CAPITAL STRATEGY — Chiến lược vốn
# ============================================================
# Loại: "fixed_fractional" | "martingale" | "anti_martingale"
#       "victor2" | "victor3" | "victor4" | "custom"
CAPITAL_STRATEGY         = "fixed_fractional"
CAPITAL_STRATEGY_REDIS   = "Deriv_CapStrat_State"  # Lưu state Victor

# Victor strategies stake sequences (from UI screenshots)
VICTOR2_ROWS = [
    [1,1,2,2,3,4,5,7,10,13,18,24,32,44,59,80,108,146,197,271],
    [1,2,4,4,6,8,10,14,20,26,36,48,64,88,118,160,216,292,394,542],
]
VICTOR3_ROWS = [
    [1,1,1,1,1,1,1.5,2,2,2,2.5,3,3,3.5,4,4,4.5,5.4,6,7,8,9.5,11],
    [1,2,2,2,2,2,3,3.9,3.9,3.9,4.875,5.85,6.825,7.8,8.775,10.53,11.7,13.65,15.6,18.525,21.45],
    [1,4,4,4,4,4,6,7.605,7.605,7.605,9.50625,11.4075,13.30875,15.21,17.11125,20.5335,22.815,26.6175,30.42,36.1],
]
VICTOR4_ROWS = [
    [1,1,1,1,1,1,1,1,1,1,1,1,1,1,1.23,1.25,1.28,1.3,1.47,1.6,1.74,1.88,2.04,2.22],
    [1.95,1.95,1.95,1.95,1.95,1.95,1.95,1.95,1.95,1.95,1.95,1.95,1.95,1.95,1.95,2.28,2.32,2.36,2.41,2.73,2.96,3.21,3.49,3.79],
    [3.8,3.8,3.8,3.8,3.8,3.8,3.8,3.8,3.8,3.8,3.8,3.8,3.8,3.8,4.22,4.29,4.37,4.45,5.04,5.47,5.94,6.44,6.99,7.59],
    [7.41,7.41,7.41,7.41,7.41,7.41,7.41,7.41,7.41,7.41,7.41,7.41,7.41,7.41,7.81,7.94,8.08,8.24,9.33,10.12,10.99,11.92,12.96,14.09],
]

# ============================================================
# CONTROL SYSTEM — Daily TP/SL + Wave Direction Filter
# ============================================================
# Daily Take-Profit: dừng khi lãi >= ngưỡng này trong ngày (0 = tắt)
DAILY_TAKE_PROFIT_USD    = 0.0
# Daily Stop-Loss: dừng khi lỗ >= ngưỡng này trong ngày (0 = dùng RISK_MAX_DAILY_LOSS_PCT)
DAILY_STOP_LOSS_USD      = 0.0
# Redis key đánh dấu "đã dừng bởi TP/SL hôm nay"
DAILY_TPSL_STOPPED_KEY   = "Deriv_DailyTPSL_Stopped"
DAILY_TPSL_REASON_KEY    = "Deriv_DailyTPSL_Reason"

# Wave direction filter: "both" | "up_only" | "down_only"
WAVE_DIRECTION_FILTER    = "both"

# ============================================================
# LLM AGENT — RAG + Function Calling (tắt mặc định)
# ============================================================
LLM_ENABLED              = False   # Bật khi có API key
LLM_BASE_URL             = "https://api.openai.com/v1"
LLM_MODEL                = "gpt-4o-mini"
LLM_API_KEY              = ""      # Đặt trong .env
LLM_MAX_TOKENS           = 512
LLM_ADVICE_ONLY          = True    # LLM chỉ tư vấn, không tự thực thi lệnh
LLM_RAG_TOP_K            = 5       # Số kết quả tìm kiếm ngữ nghĩa
VECTOR_STORE_FILE        = "vector_store.json"

# ============================================================
# API SERVER
# ============================================================
API_HOST                 = "0.0.0.0"
API_PORT                 = 8000
API_SECRET_KEY           = "changeme_in_env"   # JWT secret
API_CORS_ORIGINS         = ["http://localhost:3000", "http://localhost:8000"]

# ============================================================
# SYNTHETIC SIGNAL ENGINE — Data Generation + Augmentation
# ============================================================
# Số synthetic samples sinh ra mỗi loại regime (trend/chop/crash/...)
SYNTH_N_PER_REGIME       = 150
# Tỉ lệ synthetic trong tập train khi blend với real data
# 0.5 = 50% real + 50% synthetic; 1.0 = 100% synthetic (cold start)
SYNTH_BLEND_RATIO        = 0.50
# Bật tự động synthetic boost khi real samples < ML_MIN_TRAIN_SAMPLES
SYNTH_AUTO_BOOST         = True
# Chạy synthetic training khi khởi động lần đầu (cold start)
SYNTH_COLD_START         = True

# ============================================================
# EVOLUTION ENGINE — Self-Play + Simulation Environment
# ============================================================
# Kích thước quần thể (số genomes mỗi thế hệ)
EVOL_POP_SIZE            = 30
# Số thế hệ tiến hóa mỗi lần chạy
EVOL_GENERATIONS         = 10
# Số môi trường thị trường (regime environments) mỗi genome phải vượt qua
EVOL_N_ENVIRONMENTS      = 8
# Số nến trong mỗi môi trường (nhiều hơn = chính xác hơn, chậm hơn)
EVOL_ENV_CANDLES         = 200
# Số elites được bảo toàn qua mỗi thế hệ (không đột biến)
EVOL_N_ELITES            = 4
# Tỉ lệ đột biến mỗi gene (0.15 = 15% gene bị thay đổi)
EVOL_MUTATION_RATE       = 0.15
# Độ lớn đột biến (tỉ lệ range của gene)
EVOL_MUTATION_SIGMA      = 0.12
# Tỉ lệ lai ghép (xác suất thực hiện crossover thay vì copy)
EVOL_CROSSOVER_RATE      = 0.70
# Kích thước tournament (k candidate) trong tournament selection
EVOL_TOURNAMENT_K        = 4
# Tự động chạy evolution sau mỗi N learning cycles
EVOL_AUTO_INTERVAL       = 100  # chu kỳ (0 = tắt)
# Tự áp dụng champion genome lên config khi evolution xong
EVOL_AUTO_PROMOTE        = True

# ============================================================
# META-LEARNING — Strategy Genome Engine
# ============================================================
# Kích thước tối đa gene pool (số genome tích lũy qua các runs)
META_POOL_MAX_SIZE       = 500
# Số winners dùng để phân tích pattern + archetype clustering
META_TOP_K_WINNERS       = 30
# Số archetype cluster (chiến lược nền khác nhau)
META_N_ARCHETYPES        = 4
# Số seeds meta-guided sinh ra cho evolution tiếp theo
META_N_SEEDS             = 12
