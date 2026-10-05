export interface Bar {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}
export interface Instrument {
  symbol: string;
  base_coin: string;
  quote_coin: string;
}
export interface Ticker extends Instrument {
  last_price: number;
  change_24h_pct: number;
  high_24h: number;
  low_24h: number;
  volume_24h: number;
  turnover_24h: number;
}
export interface TickerSnapshot {
  source: string;
  exchange: string;
  fetched_at: string;
  tickers: Ticker[];
}
export interface WatchlistEntry {
  exchange: string;
  symbol: string;
  created_at: string;
}
export interface Overview {
  source: string;
  mode: string;
  strategies: number;
  running: number;
  positions: number;
  cash: string;
  equity: string;
  initial_equity: string;
  unrealized_pnl: string;
  orders: number;
  workers: { name: string; online: boolean; heartbeat_at: string }[];
}
export interface StrategyRules {
  version: 1;
  strategy_type:
    | "ema_rsi"
    | "ema_cross"
    | "rsi_reversion"
    | "breakout"
    | "bollinger_reversion";
  fast: number;
  slow: number;
  rsi_period: number;
  rsi_lower: number;
  rsi_upper: number;
  stop_loss_pct: number;
  take_profit_pct: number;
  max_drawdown_pct: number;
  breakout_period: number;
  bollinger_period: number;
  bollinger_std: number;
  volume_period: number;
  volume_ratio: number;
  atr_period: number;
  atr_pct_max: number;
  adx_period: number;
  adx_min: number;
}
export interface Strategy {
  id: number;
  name: string;
  exchange: string;
  symbol: string;
  interval: string;
  status: string;
  signal: string;
  quantity: string;
  cash: string;
  initial_equity: string;
  max_notional: string;
  last_error: string | null;
  last_bar: string | null;
  rules: StrategyRules;
  origin_job_id: number | null;
  risk_halted: boolean;
  max_drawdown: string;
  equity: string;
  total_return: string;
  position: Position | null;
  curve: { timestamp: string; equity: string }[];
}
export interface Position {
  strategy_id: number;
  strategy_name: string;
  symbol: string;
  side: string;
  quantity: string;
  entry_price: string;
  mark_price: string;
  unrealized_pnl: string;
}
export interface Order {
  id: number;
  strategy_id: number;
  symbol: string;
  side: string;
  action: string;
  quantity: string;
  price: string;
  fee: string;
  realized_pnl: string;
  source: string;
  created_at: string;
}
export interface AuditEvent {
  id: number;
  level: string;
  message: string;
  created_at: string;
}
export interface BacktestResult {
  source: string;
  bars: number;
  equity_kind: string;
  risk_halted: boolean;
  rejected_entries: number;
  dataset?: {
    start_at: string;
    end_at: string;
    sha256: string;
    warmup_bars: number;
  };
  metrics: {
    final_equity: number;
    total_return: number;
    max_drawdown: number;
    trades: number;
    win_rate: number;
  };
  curve: { timestamp: string; equity: number }[];
  trades: {
    side: string;
    entry_time: string;
    exit_time: string;
    entry_price: number;
    exit_price: number;
    quantity: number;
    pnl: number;
  }[];
}
export interface Job {
  id: number;
  status: string;
  parameters: {
    exchange: string;
    symbol: string;
    interval: string;
    rules?: StrategyRules;
    evaluation_phase?: string;
    batch_id?: string;
    start_at?: string;
    end_at?: string;
    initial_equity?: number;
    quantity?: number;
    fee_rate?: number;
    slippage_bps?: number;
    max_notional?: number;
  };
  summary?: BacktestResult["metrics"] | null;
  created_at: string;
  result?: BacktestResult | null;
  error?: string | null;
}
export interface Account {
  source: string;
  connected: boolean;
  exchange?: string;
  testnet?: boolean;
  message?: string;
  balances?: { coin: string; balance: string }[];
}

export interface SearchDimension {
  key: string;
  label: string;
  min: number;
  max: number;
  kind: "int" | "float";
  low: number;
  high: number;
  step: number;
  enabled: boolean;
}
export interface OptimizationCandidate {
  number: number;
  score: number;
  rules: StrategyRules;
  train_metrics: BacktestResult["metrics"];
  validation_metrics: BacktestResult["metrics"];
  train_pass: boolean;
  validation_pass: boolean;
  eligible: boolean;
  reasons: string[];
  validation_reasons: string[];
  backtest_id: number;
  windows: {
    start_at: string;
    end_at: string;
    metrics: BacktestResult["metrics"];
    passed: boolean;
    reasons: string[];
  }[];
}
export interface OptimizationJob {
  id: number;
  status: string;
  completed_trials: number;
  error?: string | null;
  parameters: {
    trials: number;
    base: {
      symbol: string;
      rules: StrategyRules;
      start_at: string;
      end_at: string;
    };
    split_at: string;
    validation_windows: number;
  };
  result?: {
    candidates: OptimizationCandidate[];
    trials: unknown[];
    ranking: string;
    dataset?: { sha256: string };
    split_at: string;
  } | null;
}
