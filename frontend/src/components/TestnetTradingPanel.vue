<script setup lang="ts">
import { ElAlert } from "element-plus/es/components/alert/index.mjs";
import { ElButton } from "element-plus/es/components/button/index.mjs";
import { ElDescriptions } from "element-plus/es/components/descriptions/index.mjs";
import { ElDescriptionsItem } from "element-plus/es/components/descriptions/index.mjs";
import { ElForm } from "element-plus/es/components/form/index.mjs";
import { ElFormItem } from "element-plus/es/components/form/index.mjs";
import { ElInput } from "element-plus/es/components/input/index.mjs";
import { ElInputNumber } from "element-plus/es/components/input-number/index.mjs";
import { ElOption } from "element-plus/es/components/select/index.mjs";
import { ElSelect } from "element-plus/es/components/select/index.mjs";
import { ElSwitch } from "element-plus/es/components/switch/index.mjs";
import { ElTable } from "element-plus/es/components/table/index.mjs";
import { ElTableColumn } from "element-plus/es/components/table/index.mjs";
import { ElTag } from "element-plus/es/components/tag/index.mjs";
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import { ElMessage } from "element-plus/es/components/message/index.mjs";
import { request } from "../api";
import TestnetJournalPanel from "./TestnetJournalPanel.vue";
import TestnetResearchPanel from "./TestnetResearchPanel.vue";
import AccountExposure, { type Exposure } from "./AccountExposure.vue";
import RuleFields from "./RuleFields.vue";
import type { StrategyRules } from "../types";
import PortfolioResearchPanel from "./PortfolioResearchPanel.vue";
import PeriodComparisonPanel from "./PeriodComparisonPanel.vue";
import StrategyEvidence, {
  type Qualification,
  type Monitoring,
} from "./StrategyEvidence.vue";
type Config = {
  selection?: {
    enabled: boolean;
    ranking: string;
    top_n: number;
    min_turnover: number;
    max_atr_pct: number;
  };
  research_guard?: {
    enabled: boolean;
    min_trades: number;
    min_validation_windows?: number;
  };
  monitoring?: {
    enabled: boolean;
    consecutive_losses: number;
    min_fills: number;
    max_adverse_slippage_bps: number;
  };
  budget: number;
  cash_reserve_pct?: number;
  risk_per_trade_pct: number;
  portfolio_risk_pct: number;
  daily_loss_pct: number;
  max_drawdown_pct: number;
  max_positions: number;
  leverage: number;
  max_correlation?: number;
  fee_rate: number;
  slippage_bps: number;
  interval: string;
  symbols: string[];
  rules: StrategyRules;
};
type Status = {
  account_exposure?: Exposure;
  research_qualification?: Qualification;
  strategy_monitor?: Monitoring;
  available: boolean;
  environment: string;
  enabled: boolean;
  risk_halted: boolean;
  flatten_requested: boolean;
  equity: string;
  peak_equity: string;
  configuration: Config;
  last_error: string | null;
  updated_at: string | null;
  net_flows?: string;
  ledger_warning?: string | null;
  execution_state?: string;
  strategy_status?: {
    configuration_fingerprint: string;
    acknowledged: boolean;
    confirmed_at: string | null;
    last_checked_at: string | null;
    source: string;
    source_job_id: number | null;
    source_modified: boolean;
    unvalidated_symbols: string[];
    last_scan_at: string | null;
    last_scan: {
      symbol: string;
      signal: string;
      reason: string;
      bar_time: string;
      actionable: boolean;
    }[];
  };
  account_risk?: {
    drawdown_pct: string;
    daily_loss_pct: string;
    drawdown_amount: string;
    daily_loss_amount: string;
    drawdown_limit: string;
    daily_loss_limit: string;
    budget: string;
    basis: string;
  };
  health?: {
    worker_online: boolean;
    snapshot_fresh: boolean;
    snapshot_age_seconds: number | null;
    unprotected_symbols: string[];
  };
  research_origin?: Record<string, unknown> | null;
  trades?: Record<string, unknown>[];
  executions?: Record<string, unknown>[];
  ledger?: Record<string, unknown>[];
  snapshot: {
    positions?: Record<string, unknown>[];
    order_margin?: string;
    ownership?: {
      orders: Record<string, unknown>[];
      positions: Record<string, unknown>[];
      blocked_symbols: string[];
      reserved_notional: string;
      uncertain: boolean;
    };
    wallet_equity?: string;
    available_balance?: string;
    margin_mode?: string;
    funding_balance?: string;
    funding_error?: string;
    protective_equity?: string;
    risk_basis?: string;
    event_stream?: { ready: boolean; reconciled: boolean; events: number };
    entry_check?: { symbol: string; reason: string };
  };
  positions: {
    symbol: string;
    side: string;
    quantity: string;
    entry_price: string;
    mark_price: string;
    stop_loss: string;
  }[];
  orders: {
    id: number;
    symbol: string;
    side: string;
    action: string;
    quantity: string;
    status: string;
    filled_quantity: string;
    fee: string;
    link_id: string;
    error: string | null;
    cancel_requested?: boolean;
  }[];
};
type Card = {
  validation_status?: string;
  selected?: boolean;
  selection_rank?: number;
  selection_score?: number | null;
  selection_reason?: string;
  budget_fit?: boolean;
  budget_reason?: string;
  symbol: string;
  signal: string;
  reason: string;
  actionable: boolean;
  entry?: string;
  quantity?: string;
  stop_loss?: string;
  notional?: string;
  planned_loss?: string;
  margin?: string;
  liquidity?: number;
  trend?: string;
  signal_basis?: string;
  higher_timeframe?: { direction: string; semantics: string } | null;
  factors?: {
    key: string;
    label: string;
    description: string;
    value: number | null;
    unit: string;
    enabled: boolean;
    passed: boolean | null;
    threshold: number | null;
    comparison: string | null;
  }[];
};
const props = defineProps<{ token: string; sourceStrategyId?: number }>();
function sourceName(value: unknown) {
  return (value as { name?: string } | null)?.name ?? "未记录";
}
const status = ref<Status>();
const config = ref<Config>();
const symbols = ref("BTCUSDT,ETHUSDT,SOLUSDT");
const cards = ref<Card[]>([]);
const researchVersion = ref(0);
const error = ref("");
const busy = ref(false);
function researchPreset() {
  if (!config.value || busy.value || configurationLocked.value) return;
  Object.assign(config.value, {
    interval: "15",
    cash_reserve_pct: 20,
    risk_per_trade_pct: 0.5,
    portfolio_risk_pct: 1,
    daily_loss_pct: 2,
    max_drawdown_pct: 5,
    max_positions: 2,
    leverage: 2,
    research_guard: {
      enabled: true,
      min_trades: 20,
      min_validation_windows: 3,
    },
    monitoring: {
      enabled: true,
      consecutive_losses: 3,
      min_fills: 10,
      max_adverse_slippage_bps: 20,
    },
  });
  Object.assign(config.value.rules, {
    strategy_type: "ema_cross",
    fast: 20,
    slow: 60,
    hourly_trend_filter: true,
    hourly_min_gap_pct: 0.05,
    stop_loss_pct: 2,
    take_profit_pct: 4,
  });
  ElMessage.info(
    "已填入研究起点，尚未保存；请核对原有过滤条件、币种和成本，再创建研究验证。不会启动交易。",
  );
}
const accountPositions = computed(
  () =>
    status.value?.snapshot.ownership?.positions ??
    status.value?.snapshot.positions?.map((row) => ({
      ...row,
      source: "unknown",
      managed: false,
    })) ??
    status.value?.positions.map((row) => ({
      symbol: row.symbol,
      side: row.side,
      size: row.quantity,
      avgPrice: row.entry_price,
      markPrice: row.mark_price,
      stopLoss: row.stop_loss,
      source: "system",
      managed: true,
    })) ??
    [],
);
const executionError = computed(() =>
  status.value?.last_error ===
  "检测到非本系统持仓或挂单，已阻止新开仓；不会接管手工仓位"
    ? "执行进程仍返回旧版拦截状态，请重启平台加载按币种隔离规则；当前尚不能恢复开仓。"
    : status.value?.last_error,
);
const configurationLocked = computed(() =>
  Boolean(
    status.value &&
    (status.value.enabled ||
      status.value.flatten_requested ||
      status.value.positions.length ||
      status.value.snapshot.positions?.length ||
      status.value.snapshot.ownership?.positions.length ||
      status.value.orders.some((order) =>
        [
          "intent",
          "submitted",
          "unknown",
          "New",
          "PartiallyFilled",
          "Untriggered",
        ].includes(order.status),
      )),
  ),
);
const environmentLabel = computed(() =>
  status.value?.environment === "mainnet" ? "主网" : "测试网",
);
const fundsLabel = computed(() =>
  status.value?.environment === "mainnet" ? "真实资金" : "测试币",
);
type AssistantView = "run" | "configure" | "research" | "records";
const view = ref<AssistantView>(props.sourceStrategyId ? "research" : "run");
const views: { key: AssistantView; label: string }[] = [
  { key: "run", label: "运行面板" },
  { key: "configure", label: "策略设置" },
  { key: "research", label: "研究验证" },
  { key: "records", label: "交易记录" },
];
async function goTo(target: AssistantView, id: string) {
  view.value = target;
  await nextTick();
  document
    .getElementById(id)
    ?.scrollIntoView({ behavior: "smooth", block: "start" });
  if (id === "shared-budget")
    document
      .querySelector<HTMLInputElement>("#shared-budget input")
      ?.focus({ preventScroll: true });
}
const parsedSymbols = computed(() =>
  symbols.value
    .split(/[,，\s]+/)
    .map((item) => item.trim().toUpperCase())
    .filter(Boolean),
);
function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object")
    return `{${Object.entries(value)
      .filter(([, item]) => item !== undefined)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, item]) => `${JSON.stringify(key)}:${canonical(item)}`)
      .join(",")}}`;
  return JSON.stringify(value) ?? "null";
}
const dirty = computed(() =>
  Boolean(
    config.value &&
    status.value &&
    canonical({ ...config.value, symbols: parsedSymbols.value }) !==
      canonical(status.value.configuration),
  ),
);
function useSavedConfiguration() {
  if (!status.value) return;
  config.value = JSON.parse(
    JSON.stringify(status.value.configuration),
  ) as Config;
  symbols.value = status.value.configuration.symbols.join(",");
  cards.value = [];
}
async function start() {
  if (dirty.value) {
    ElMessage.warning("请先保存配置或加载已保存配置，再启动");
    return;
  }
  await act("command", {
    action: "start",
    ...(status.value?.strategy_status
      ? {
          configuration_fingerprint:
            status.value.strategy_status.configuration_fingerprint,
        }
      : {}),
  });
}
const columns = ref(window.innerWidth < 640 ? 1 : 2);
const resize = () => {
  columns.value = window.innerWidth < 640 ? 1 : 2;
};
const orderLabels: Record<string, string> = {
  intent: "等待提交",
  submitted: "已提交，等待确认",
  unknown: "结果未确认，正在查询",
  New: "等待成交",
  PartiallyFilled: "部分成交",
  Filled: "已成交",
  Cancelled: "已取消",
  Rejected: "已拒绝",
  PartiallyFilledCanceled: "部分成交后取消",
  Deactivated: "已失效",
  Untriggered: "等待触发",
};
const signalNames: Record<string, string> = {
  LONG: "做多",
  SHORT: "做空",
  HOLD: "等待",
};
const orderStatus = (value: string) => orderLabels[value] ?? value;
const strategyNames: Record<string, string> = {
  ema_cross: "EMA双均线",
  ema_rsi: "EMA / RSI趋势",
  rsi_reversion: "RSI均值回归",
  breakout: "区间突破",
  bollinger_reversion: "布林带回归",
};
const strategyDescriptions: Record<string, string> = {
  ema_cross:
    "快线向上穿过慢线时做多、向下穿过时做空，无新交叉等待；快线一直在慢线上方不代表每根K线都重新做多。",
  ema_rsi: "均线向上交叉且RSI不过热时做多，向下交叉且RSI不过度超卖时做空。",
  rsi_reversion: "RSI从超卖区回升时做多，从超买区回落时做空。",
  breakout: "收盘价首次上破此前区间时做多，下破时做空。",
  bollinger_reversion: "价格从下轨外回到带内时做多，从上轨外回到带内时做空。",
};
let timer: number | undefined;
let fetching = false;
let requestVersion = 0;
let disposed = false;
const money = (value: unknown) => Number(value ?? 0).toFixed(2);
const states: Record<string, string> = {
  unavailable: "交易执行未就绪",
  offline: "交易执行进程离线",
  risk_halted: "账户风险暂停",
  flattening: "正在平仓并暂停",
  confirming: "订单待确认",
  blocked: "核查异常，禁止新开仓",
  entry_blocked: "开仓检查未通过，等待重新核查",
  paused: "已暂停新开仓",
  recovering: "恢复核对中，暂不开新仓",
  managing_positions: "持仓管理中",
  waiting_signal: "等待交易信号",
};
function reloadConfiguration() {
  view.value = "run";
  config.value = undefined;
  cards.value = [];
  void load(true);
}
const drawdown = computed(() =>
  status.value?.account_risk
    ? Number(status.value.account_risk.drawdown_pct).toFixed(2)
    : status.value
      ? Math.max(
          0,
          ((Number(status.value.peak_equity) - Number(status.value.equity)) /
            Number(status.value.configuration.budget)) *
            100,
        ).toFixed(2)
      : "0",
);
async function load(force = false) {
  if ((!force && fetching) || disposed) return false;
  const version = ++requestVersion;
  fetching = true;
  try {
    const data = await request<Status>("/trading/status", props.token);
    if (disposed || version !== requestVersion) return false;
    status.value = data;
    if (!config.value) {
      config.value = structuredClone(data.configuration);
      symbols.value = data.configuration.symbols.join(",");
    }
    error.value = "";
    return true;
  } catch (err) {
    if (!disposed && version === requestVersion)
      error.value =
        err instanceof Error ? err.message : "交易助手接口未就绪，请重启平台";
    return false;
  } finally {
    if (version === requestVersion) fetching = false;
  }
}
async function act(path: string, body: unknown = {}) {
  busy.value = true;
  error.value = "";
  try {
    await request(
      `/trading/${path}?environment=${status.value?.environment}`,
      props.token,
      path === "command"
        ? {
            ...(body as Record<string, unknown>),
            environment: status.value?.environment,
          }
        : body,
    );
    ElMessage.success(`${environmentLabel.value}操作已提交`);
    return await load(true);
  } catch (err) {
    error.value = err instanceof Error ? err.message : "操作失败";
    return false;
  } finally {
    busy.value = false;
  }
}
async function save() {
  if (!config.value) return;
  const payload = {
    ...config.value,
    symbols: symbols.value
      .split(/[,，\s]+/)
      .map((item) => item.trim().toUpperCase())
      .filter(Boolean),
  };
  if (await act("configuration", payload)) {
    useSavedConfiguration();
    view.value = "run";
  }
}
async function scan() {
  busy.value = true;
  error.value = "";
  try {
    cards.value = (
      await request<{ candidates: Card[] }>("/trading/scan", props.token, {})
    ).candidates;
  } catch (err) {
    error.value = err instanceof Error ? err.message : "扫描失败";
  } finally {
    busy.value = false;
  }
}
onMounted(() => {
  window.addEventListener("resize", resize);
  void load();
  timer = window.setInterval(() => void load(), 5000);
});
onBeforeUnmount(() => {
  window.removeEventListener("resize", resize);
  disposed = true;
  window.clearInterval(timer);
});
</script>
<template>
  <details class="panel quantitative-workflow">
    <summary>
      量化策略五步：筛选 → 因子 → 组合回放 → 验证准入 → 运行监测
    </summary>
    <ol>
      <li>
        在策略设置选择观察池，保存动态筛选门槛；扫描查看候选排名与预算适配。
      </li>
      <li>
        创建多币组合研究，在详情查看横截面因子诊断；方向和评分规则保持固定。
      </li>
      <li>
        查看历史逐时筛选记录及共享资金回放，确认手续费、资金费和成交假设。
      </li>
      <li>
        创建后续独立验证，查看成本压力与参数稳定性。需要强制约束时先启用研究准入限制，再研究验证；未通过项需要新的研究，不能反复调验证结果。
      </li>
      <li>
        核对并应用参数后仍暂停；自行启动测试网，查看已对账收益、费用和成交偏差。监测只暂停新开仓，保留交易所保护。
      </li>
    </ol>
  </details>
  <details class="panel trading-workflow">
    <summary>操作帮助：首次使用按什么顺序？</summary>
    <div class="panel-heading"><h3>交易助手操作顺序</h3></div>
    <ol>
      <li>
        <a
          href="#trading-config"
          @click.prevent="goTo('configure', 'trading-config')"
          >保存交易配置</a
        >
        <p>
          设置币种、预算、规则和风险限制，再保存。后续研究与扫描使用已保存的配置。
        </p>
      </li>
      <li>
        <a
          href="#trading-research"
          @click.prevent="goTo('research', 'trading-research')"
          >研究周期并独立验证</a
        >
        <p>
          创建三个周期回测，等待完成；选定一个研究结果创建后续验证，再提交独立验证回测。这里只计算历史结果，不下单。
        </p>
      </li>
      <li>
        <a
          href="#trading-apply"
          @click.prevent="goTo('research', 'trading-apply')"
          >预览并应用参数</a
        >
        <p>
          验证完成后选择结果和币种范围，预览差异、核对后应用。应用完成仍保持暂停。
        </p>
      </li>
      <li>
        <a href="#trading-scan" @click.prevent="goTo('run', 'trading-scan')"
          >扫描当前信号</a
        >
        <p>
          查看做多、做空或等待，以及建议仓位和跳过原因。扫描本身不下单；没有信号时等待。
        </p>
      </li>
      <li>
        <a
          href="#trading-execution"
          @click.prevent="goTo('run', 'trading-execution')"
          >启动{{ environmentLabel }}交易</a
        >
        <p>
          核对余额、逐仓／单向模式和同步状态，手动启动；此后才会按信号自动下单。通过下方持仓、订单及流水查看结果。
        </p>
      </li>
    </ol>
    <p class="panel-note">
      首次使用可按以上顺序操作；已有参数可以直接扫描。多币组合回测用于补充检查共享资金下的表现，不会自动应用参数或启动交易。
    </p>
  </details>
  <nav class="assistant-views" aria-label="交易助手功能">
    <button
      v-for="item in views"
      :key="item.key"
      type="button"
      :aria-pressed="view === item.key"
      @click="view = item.key"
    >
      {{ item.label }}{{ item.key === "configure" && dirty ? " · 未保存" : "" }}
    </button>
  </nav>
  <el-alert
    :title="`当前执行环境：Bybit ${environmentLabel}，使用${fundsLabel}；所有币种共享一份预算。原策略管理仍为本地模拟。`"
    type="info"
    :closable="false"
    class="mb"
  />
  <el-alert
    v-if="error"
    :title="error"
    type="error"
    :closable="false"
    class="mb"
  />
  <template v-if="status && config">
    <StrategyEvidence
      :qualification="status.research_qualification"
      :monitor="status.strategy_monitor"
    />
    <section class="panel budget-summary">
      <div class="panel-heading">
        <h3>共享预算 · {{ environmentLabel }}</h3>
        <el-button type="primary" @click="goTo('configure', 'shared-budget')"
          >修改预算</el-button
        >
      </div>
      <p>
        已保存预算：<strong
          >{{ money(status.configuration.budget) }} USDT</strong
        >
        · 当前可用余额：{{ status.snapshot.available_balance ?? "待同步" }} USDT
      </p>
      <p class="panel-note">
        预算首次默认200，可自行设置。点击“修改预算”进入设置，修改后点击“保存扫描与风险配置”；实际下单还受剩余余额和风险限制。
      </p>
      <p v-if="configurationLocked" class="panel-note">
        目前有运行、持仓或待处理状态：可以查看预算，修改前需暂停并满足空仓与订单确认条件。
      </p>
    </section>
    <section class="panel current-strategy-panel">
      <div class="panel-heading">
        <h3>当前执行参数与确认</h3>
        <el-button link @click="load()">刷新执行确认</el-button>
        <el-button link @click="view = 'configure'">修改策略</el-button>
      </div>
      <p>
        已保存策略：{{
          strategyNames[status.configuration.rules.strategy_type]
        }}
        · 周期 {{ status.configuration.interval }} · 币种
        {{ status.configuration.symbols.join("、") }} · 预算
        {{ status.configuration.budget }} USDT。
      </p>
      <p class="panel-note" v-if="status.research_origin">
        参数来源：验证回测 #{{ status.research_origin.job_id }}
        <template v-if="status.research_origin.source_strategy">
          · 模拟策略
          {{ sourceName(status.research_origin.source_strategy) }}
          的规则快照</template
        >。
        {{
          status.research_origin.modified
            ? "应用后已修改，当前参数不再完整对应原验证结果，请重新验证。"
            : "使用已应用的参数快照，来源策略后续修改不会自动同步。"
        }}
      </p>
      <p class="panel-note" v-else>
        参数来源：助手手动配置。尚未应用验证回测；策略管理中的启动或修改不会同步到这里。
      </p>
      <details class="saved-rule-details">
        <summary>查看已保存的均线、止损与过滤设置</summary>
        <p
          v-if="
            ['ema_cross', 'ema_rsi'].includes(
              status.configuration.rules.strategy_type,
            )
          "
          class="panel-note"
        >
          已保存均线：快 {{ status.configuration.rules.fast }} / 慢
          {{ status.configuration.rules.slow }}；止损
          {{ status.configuration.rules.stop_loss_pct }}%，止盈
          {{ status.configuration.rules.take_profit_pct ?? 0 }}%。
        </p>
        <p class="panel-note">
          已保存过滤：成交量至少
          {{ status.configuration.rules.volume_ratio ?? 0 }} 倍，ATR上限
          {{ status.configuration.rules.atr_pct_max ?? 0 }}%，ADX至少
          {{ status.configuration.rules.adx_min ?? 0 }}（0表示对应过滤关闭）。
        </p>
      </details>
      <p
        v-if="status.configuration.rules.hourly_trend_filter"
        class="panel-note"
      >
        已启用1小时方向过滤＋15分钟入场；已收盘小时趋势不明时不开仓。均线间距下限
        {{ status.configuration.rules.hourly_min_gap_pct }}%。
      </p>
      <p>
        参数读取确认：{{
          status.strategy_status?.acknowledged
            ? "执行进程已按当前已保存参数完成一轮检查"
            : status.enabled
              ? "已请求启动，等待执行进程确认当前参数"
              : "当前已暂停；尚无本轮执行确认"
        }}。
      </p>
      <p v-if="status.strategy_status?.confirmed_at" class="panel-note">
        最近确认：{{
          new Date(status.strategy_status.confirmed_at).toLocaleString()
        }}；参数版本
        {{ status.strategy_status.configuration_fingerprint.slice(0, 10) }}。
      </p>
      <p class="panel-note">
        当前状态：{{
          status.execution_state
            ? states[status.execution_state]
            : status.enabled
              ? "已请求启动，等待执行确认"
              : "已暂停新开仓"
        }}。已确认参数不代表一定有订单；没有入场信号时等待。
      </p>
      <p v-if="!status.strategy_status" class="panel-note">
        后端尚未提供参数读取确认，请重启平台后查看。
      </p>
      <el-alert
        v-if="status.last_error || status.ledger_warning || status.risk_halted"
        :title="
          executionError ||
          status.ledger_warning ||
          '账户风险已暂停，请先核查持仓与风险限额'
        "
        type="warning"
        :closable="false"
      />
      <el-button v-if="status.orders.length" link @click="view = 'records'"
        >查看订单与交易记录</el-button
      >
      <el-alert
        v-if="dirty"
        title="表单与已保存配置不一致：修改尚未应用，启动将使用已保存配置。请先保存，或加载已保存配置。"
        type="warning"
        :closable="false"
      />
      <el-button v-if="dirty" @click="useSavedConfiguration"
        >加载已保存配置</el-button
      >
      <details>
        <summary>研究结果怎样影响交易？</summary>
        <p class="panel-note">
          研究回测、因子诊断和组合验证不会自动改动执行规则。只有保存配置或“应用参数并保持暂停”才会更新已保存参数；上方流程是操作指引，不代表每一步已完成。
        </p>
      </details>
      <p
        v-if="status.strategy_status?.unvalidated_symbols.length"
        class="panel-note"
      >
        来源回测未覆盖的币种：{{
          status.strategy_status.unvalidated_symbols.join("、")
        }}。
      </p>
      <details v-if="status.strategy_status?.last_scan.length">
        <summary>执行进程最近一轮信号检查</summary>
        <p class="panel-note">
          {{
            status.strategy_status.last_scan_at
          }}；以下是执行进程记录，手动扫描结果在下方单独显示。
        </p>
        <p v-for="row in status.strategy_status.last_scan" :key="row.symbol">
          {{ row.symbol }}：{{ signalNames[row.signal] ?? row.signal }} ·
          {{ row.reason }} · 已收盘K线 {{ row.bar_time }}
        </p>
      </details>
    </section>
    <el-alert
      v-if="!status.available"
      :title="`${environmentLabel}执行未就绪：请检查交易所行情、执行配置与环境切换状态，并重启平台。`"
      type="warning"
      :closable="false"
      class="mb"
    />
    <el-alert
      v-if="status.snapshot.entry_check"
      :title="`${status.snapshot.entry_check.symbol} 开仓检查：${status.snapshot.entry_check.reason}`"
      type="warning"
      :closable="false"
      class="mb"
    />
    <section
      v-show="view === 'run' || view === 'records'"
      class="panel account-positions"
    >
      <div class="panel-heading">
        <h3>账户全部持仓 · {{ environmentLabel }}</h3>
        <el-button link @click="load()">刷新持仓</el-button>
      </div>
      <p v-if="!status.snapshot.ownership" class="panel-note">
        当前执行接口尚未提供来源识别：已读取到的合约持仓先标记为“归属待确认”，不会自动接管。重启平台后加载完整来源识别与按币种隔离规则。
      </p>
      <p class="panel-note">
        展示当前环境、当前 API 账户的 USDT
        合约持仓，包含系统和外部持仓；现货、其他结算币种及其他子账户不在此列表。
      </p>
      <el-table :data="accountPositions" empty-text="当前账户没有持仓">
        <el-table-column prop="symbol" label="币种" min-width="110" />
        <el-table-column label="来源" min-width="150"
          ><template #default="{ row }">{{
            row.source === "system"
              ? "本系统"
              : row.source === "external"
                ? "外部／手工"
                : status.snapshot.ownership
                  ? "归属未确认／混合"
                  : "归属待确认"
          }}</template></el-table-column
        >
        <el-table-column label="方向"
          ><template #default="{ row }">{{
            row.side === "Buy"
              ? "做多"
              : row.side === "Sell"
                ? "做空"
                : row.side
          }}</template></el-table-column
        >
        <el-table-column
          prop="size"
          label="数量"
          min-width="100"
        /><el-table-column
          prop="avgPrice"
          label="入场价"
          min-width="110"
        /><el-table-column prop="markPrice" label="标记价" min-width="110" />
        <el-table-column
          prop="unrealisedPnl"
          label="浮动盈亏（USDT）"
          min-width="160"
        />
        <el-table-column label="交易所止损" min-width="110"
          ><template #default="{ row }">{{
            Number(row.stopLoss) > 0 ? row.stopLoss : "未设置"
          }}</template></el-table-column
        >
        <el-table-column label="系统处理" min-width="210"
          ><template #default="{ row }">{{
            !row.managed
              ? "仅展示，不接管"
              : status.snapshot.ownership?.blocked_symbols.includes(row.symbol)
                ? "自动管理暂停：外部订单冲突"
                : "本系统管理"
          }}</template></el-table-column
        >
      </el-table>
      <p class="panel-note">
        外部可能来自手工或其他程序；同币种混合操作、记录无法匹配时不强行认领。系统只管理已确认归属的持仓，外部持仓仍计入账户风险。手机上可左右滑动表格查看数量、价格与止损。
      </p>
    </section>
    <section v-show="view === 'run'" class="panel execution-panel">
      <div class="panel-heading">
        <h3>启动与停止{{ environmentLabel }}交易</h3>
      </div>
      <p class="panel-note">
        启动后使用已保存的参数自动提交{{
          environmentLabel
        }}订单；没有入场信号时等待。暂停停止新开仓，已有持仓继续保护；“平仓并暂停”会提交平仓订单。逐仓／单向设置需要账户空仓。
      </p>
      <p v-if="!status.enabled" class="panel-note">
        本系统当前没有运行自动交易。暂停只停止本系统新开仓，不会平仓或撤销你的外部手工订单。
      </p>
      <div id="trading-execution" class="trading-actions">
        <el-button
          type="primary"
          :disabled="
            busy ||
            dirty ||
            !status.available ||
            status.enabled ||
            status.risk_halted ||
            Boolean(status.last_error) ||
            Boolean(status.ledger_warning)
          "
          @click="start"
          >启动{{ environmentLabel }}自动交易</el-button
        >
        <el-button :disabled="busy" @click="act('command', { action: 'pause' })"
          >暂停新开仓</el-button
        >
        <el-button
          type="warning"
          :disabled="busy || !status.available"
          @click="act('command', { action: 'flatten' })"
          >平仓并暂停（{{ fundsLabel }}）</el-button
        >
      </div>
    </section>
    <details v-show="view === 'run'" class="panel account-details">
      <summary>账户余额、风险与同步详情</summary>
      <div class="panel-heading">
        <h3>共享资金与账户保护</h3>
        <el-tag :type="status.enabled ? 'success' : 'info'">{{
          status.execution_state
            ? states[status.execution_state]
            : status.enabled
              ? "已请求启动，等待执行确认"
              : "已暂停新开仓"
        }}</el-tag>
      </div>
      <p v-if="status.health" class="panel-note">
        执行服务：{{
          status.health.worker_online ? "在线" : "离线"
        }}；账户快照：{{
          status.health.snapshot_fresh ? "有效" : "过期或未读取"
        }}； 无止损仓位：{{
          status.health.unprotected_symbols.join("、") || "无"
        }}。
      </p>
      <p v-if="status.snapshot.event_stream" class="panel-note">
        订单实时同步：{{
          status.snapshot.event_stream.ready
            ? "已连接"
            : "断线恢复中，暂停新开仓"
        }}； 对账：{{
          status.snapshot.event_stream.reconciled ? "已核对" : "等待交易所确认"
        }}。
      </p>
      <el-descriptions :column="columns" label-width="120px" border>
        <el-descriptions-item label="共享预算"
          >{{ money(status.configuration.budget) }} USDT</el-descriptions-item
        >
        <el-descriptions-item label="预算权益"
          >{{ money(status.equity) }} USDT</el-descriptions-item
        >
        <el-descriptions-item label="测试钱包权益"
          >{{ money(status.snapshot.wallet_equity) }} USDT</el-descriptions-item
        >
        <el-descriptions-item label="测试可用余额"
          >{{
            money(status.snapshot.available_balance)
          }}
          USDT</el-descriptions-item
        >
        <el-descriptions-item label="预算高点回撤"
          >{{ drawdown }}%（按初始预算）</el-descriptions-item
        >
        <el-descriptions-item v-if="status.account_risk" label="日亏损／限额"
          >{{ money(status.account_risk.daily_loss_amount) }} /
          {{ money(status.account_risk.daily_loss_limit) }}
          USDT</el-descriptions-item
        >
        <el-descriptions-item v-if="status.account_risk" label="回撤／限额"
          >{{ money(status.account_risk.drawdown_amount) }} /
          {{ money(status.account_risk.drawdown_limit) }}
          USDT</el-descriptions-item
        >
        <el-descriptions-item label="资金账户 USDT">{{
          status.snapshot.funding_balance === undefined
            ? "未读取"
            : money(status.snapshot.funding_balance) + " USDT"
        }}</el-descriptions-item>
        <el-descriptions-item label="基线后净划转"
          >{{ money(status.net_flows) }} USDT（不计收益）</el-descriptions-item
        >
        <el-descriptions-item label="基线后净收益"
          >{{
            money(Number(status.equity) - status.configuration.budget)
          }}
          USDT（含浮盈亏）</el-descriptions-item
        >
        <el-descriptions-item label="保证金模式">{{
          status.snapshot.margin_mode ?? "等待同步"
        }}</el-descriptions-item>
      </el-descriptions>
      <p class="panel-note">
        账户风控以共享预算为固定基准：预算200 USDT、回撤限额5%对应10
        USDT。盈利不扩大该限额；日亏损按上海日期重置基准。回测绩效中的标准最大回撤按权益高点计算，与此风控回撤不同。
      </p>
      <p
        v-if="status.snapshot.risk_basis === 'floating_only'"
        class="panel-note"
      >
        资金流水尚在核查：仍按已确认浮盈亏变化执行账户保护。保护参考权益
        {{ money(status.snapshot.protective_equity) }}
        USDT；未知现金变化暂未计入。
      </p>
      <p class="panel-note">
        最近同步：{{
          status.updated_at
            ? new Date(status.updated_at).toLocaleString()
            : "等待执行进程同步"
        }}
      </p>
      <p class="panel-note">
        预算权益按初始预算加钱包权益变化，再扣除净划转计算，包含浮盈亏与费用；资金账户中的
        USDT需在 Bybit
        划转到统一交易账户。手工交易仍影响账户收益，请使用专门测试账户。计划风险可能因滑点而超出。
      </p>
      <el-alert
        v-if="status.ledger_warning"
        :title="status.ledger_warning"
        type="warning"
        :closable="false"
        class="mb"
      />
      <el-alert
        v-if="status.snapshot.funding_error"
        :title="status.snapshot.funding_error"
        type="info"
        :closable="false"
        class="mb"
      />
      <el-alert
        v-if="status.last_error"
        :title="executionError ?? ''"
        type="warning"
        :closable="false"
        class="mb"
      />
      <el-alert
        v-if="status.risk_halted"
        title="账户风险已暂停：先确认平仓，再重置风险基线。"
        type="warning"
        :closable="false"
        class="mb"
      />
    </details>
    <details v-show="view === 'configure'" class="panel account-setup">
      <summary>首次使用：账户初始化与风险重置</summary>
      <p class="panel-note">
        逐仓／单向设置需要账户空仓；风险重置需要暂停、空仓并确认所有订单。普通保存配置无需重置风险。
      </p>
      <div class="trading-actions">
        <el-button
          :disabled="busy || !status.available"
          @click="act('prepare-isolated')"
          >设置空仓交易账户为逐仓／单向</el-button
        >
        <el-button
          v-if="!status.enabled && status.positions.length === 0"
          :disabled="busy || !status.available"
          @click="act('command', { action: 'reset' })"
          >空仓后重置风险基线</el-button
        >
      </div>
    </details>
    <AccountExposure
      v-show="view === 'run' || view === 'records'"
      :report="status.account_exposure"
      :fresh="status.health?.snapshot_fresh"
    />
    <section v-show="view === 'configure'" id="trading-config" class="panel">
      <div class="panel-heading"><h3>多币种筛选与风险仓位</h3></div>
      <p class="panel-note">
        方向选择：自动多空，不固定做多。当前模板为{{
          strategyNames[config.rules.strategy_type] ??
          config.rules.strategy_type
        }}。{{
          strategyDescriptions[config.rules.strategy_type]
        }}仅使用已收盘K线，并检查当前过滤条件；反向信号用于退出原仓位，新开仓仍须通过风险检查。修改模板需要保存后生效。
      </p>
      <el-form label-position="top" :disabled="busy || configurationLocked">
        <el-button
          :disabled="busy || configurationLocked"
          @click="researchPreset"
          >填入15分钟研究起点</el-button
        >
        <p class="panel-note">
          填入草稿：1小时方向＋15分钟入场、EMA20/60、止损2%、止盈4%、单笔风险0.5%、组合1%、2倍杠杆、最多2仓、现金保留20%、启用三窗口准入及运行监测。预算、币种、费用和原有其他过滤保留；需要核对并保存，再研究验证。它不是已证明盈利的策略。
        </p>
        <RuleFields v-model="config.rules" account-risk-mode simple-mode />
        <div class="form-grid">
          <el-form-item id="shared-budget" label="共享预算（USDT）">
            <el-input-number
              v-model="config.budget"
              :min="10"
              :max="100000"
              :step="10"
            />
            <p class="panel-note">
              首次默认 200，可自行修改为 10～100000
              USDT，保存后生效；主网与测试网分别保存。实际下单按剩余可用余额和风险限制计算，预算不是固定下单金额。
            </p>
          </el-form-item>
          <el-form-item label="单笔计划风险（%）"
            ><el-input-number
              v-model="config.risk_per_trade_pct"
              :min="0.1"
              :max="1"
              :step="0.1"
          /></el-form-item>
          <el-form-item label="组合计划风险上限（%）"
            ><el-input-number
              v-model="config.portfolio_risk_pct"
              :min="0.1"
              :max="2"
              :step="0.1"
          /></el-form-item>
          <el-form-item label="每日亏损暂停（预算%）"
            ><el-input-number
              v-model="config.daily_loss_pct"
              :min="0.1"
              :max="5"
              :step="0.1"
          /></el-form-item>
          <el-form-item label="高点回撤暂停（预算%）"
            ><el-input-number
              v-model="config.max_drawdown_pct"
              :min="0.1"
              :max="10"
              :step="0.1"
          /></el-form-item>
          <el-form-item label="最多持仓币种"
            ><el-input-number v-model="config.max_positions" :min="1" :max="2"
          /></el-form-item>
          <el-form-item label="杠杆"
            ><el-input-number v-model="config.leverage" :min="1" :max="2"
          /></el-form-item>
          <el-form-item label="持仓相关性上限（1 为关闭）"
            ><el-input-number
              v-model="config.max_correlation"
              :min="0"
              :max="1"
              :step="0.05"
          /></el-form-item>

          <el-form-item
            v-if="config.cash_reserve_pct !== undefined"
            label="现金保留（预算%，0关闭）"
            ><el-input-number
              v-model="config.cash_reserve_pct"
              :min="0"
              :max="90"
              :step="5"
          /></el-form-item>
          <el-form-item label="信号周期"
            ><el-select v-model="config.interval"
              ><el-option label="15 分钟" value="15" /><el-option
                label="1 小时"
                value="60" /><el-option label="4 小时" value="240" /><el-option
                label="每天"
                value="D" /></el-select
          ></el-form-item>
        </div>
        <el-form-item label="扫描币种（最多 10 个，逗号分隔）"
          ><el-input v-model="symbols" placeholder="BTCUSDT,ETHUSDT,SOLUSDT"
        /></el-form-item>
        <fieldset v-if="config.selection" class="quant-policy-settings">
          <legend>动态合约筛选（只影响新开仓）</legend>
          <el-form-item label="启用动态筛选"
            ><el-switch v-model="config.selection.enabled"
          /></el-form-item>
          <el-form-item label="固定排序规则"
            ><el-select v-model="config.selection.ranking"
              ><el-option label="成交额优先" value="liquidity" /><el-option
                label="趋势与波动比值"
                value="trend_quality" /></el-select
          ></el-form-item>
          <el-form-item label="候选前 N 个"
            ><el-input-number
              v-model="config.selection.top_n"
              :min="1"
              :max="10"
          /></el-form-item>
          <el-form-item label="每根K线中位成交额下限（USDT）"
            ><el-input-number
              v-model="config.selection.min_turnover"
              :min="0"
              :step="10000"
          /></el-form-item>
          <el-form-item label="ATR波动率上限（%，0关闭）"
            ><el-input-number
              v-model="config.selection.max_atr_pct"
              :min="0"
              :max="100"
              :step="0.1"
          /></el-form-item>
          <p class="panel-note">
            观察池最多10个合约。筛选使用已收盘数据，并排除预算无法满足最小订单的合约；排名退出不平仓。趋势比值只是固定筛选指标，不是预期收益或胜率；入选仍可能等待信号。
          </p>
        </fieldset>
        <fieldset v-if="config.research_guard" class="quant-policy-settings">
          <legend>研究准入限制</legend>
          <el-form-item label="未通过验证禁止应用和启动"
            ><el-switch v-model="config.research_guard.enabled"
          /></el-form-item>
          <el-form-item label="最低验证成交笔数"
            ><el-input-number
              v-model="config.research_guard.min_trades"
              :min="5"
              :max="1000"
          /></el-form-item>
          <p class="panel-note">
            需要同一观察池、筛选与资金配置的后续组合验证，包含资金费及市价成交；数据、收益、回撤、双倍成本和研究段邻近参数均需通过。没有报告或旧回测缺少检查时视为未通过。
          </p>
          <el-form-item
            v-if="config.research_guard.min_validation_windows !== undefined"
            label="最低独立验证窗口数"
            ><el-input-number
              v-model="config.research_guard.min_validation_windows"
              :min="1"
              :max="5"
          /></el-form-item>
          <p class="panel-note">
            设置大于1并启用准入后，需要多窗口组合验证全部完成并逐项通过；仅单个窗口赚钱不能应用或启动。现金保留按预算计算，回测与执行均扣除，新开仓仍受风险和余额限制。
          </p>
        </fieldset>
        <fieldset v-if="config.monitoring" class="quant-policy-settings">
          <legend>运行监测（只暂停本系统新开仓）</legend>
          <el-form-item label="启用监测暂停"
            ><el-switch v-model="config.monitoring.enabled"
          /></el-form-item>
          <el-form-item label="连续亏损暂停笔数"
            ><el-input-number
              v-model="config.monitoring.consecutive_losses"
              :min="2"
              :max="20"
          /></el-form-item>
          <el-form-item label="成交偏差最少开仓样本"
            ><el-input-number
              v-model="config.monitoring.min_fills"
              :min="5"
              :max="100"
          /></el-form-item>
          <el-form-item label="平均不利成交偏差上限（基点）"
            ><el-input-number
              v-model="config.monitoring.max_adverse_slippage_bps"
              :min="1"
              :max="500"
          /></el-form-item>
        </fieldset>
        <el-button :loading="busy" @click="save">保存扫描与风险配置</el-button>
      </el-form>
      <p v-if="configurationLocked" class="panel-note">
        修改前需暂停自动交易、账户空仓，并确认本系统所有待处理订单。外部手工挂单本身不要求撤销；有持仓时暂不能修改风险基准。
      </p>
    </section>
    <div v-show="view === 'research'">
      <PeriodComparisonPanel
        id="trading-research"
        :token="token"
        :disabled="!status.available"
        :symbols="status.configuration.symbols"
        :hourly-trend-filter="status.configuration.rules.hourly_trend_filter"
        :source-strategy-id="sourceStrategyId"
        @completed="researchVersion++"
      />
    </div>

    <div v-show="view === 'research'">
      <TestnetResearchPanel
        id="trading-apply"
        :token="token"
        :origin="status.research_origin"
        :refresh-version="researchVersion"
        :disabled="
          !status.available ||
          status.enabled ||
          status.positions.length > 0 ||
          status.orders.some((order) =>
            [
              'intent',
              'submitted',
              'unknown',
              'New',
              'PartiallyFilled',
              'Untriggered',
            ].includes(order.status),
          ) ||
          status.flatten_requested
        "
        @applied="reloadConfiguration"
      />
    </div>
    <section v-show="view === 'run'" id="trading-scan" class="panel">
      <div class="panel-heading"><h3>扫描当前信号（不下单）</h3></div>
      <p class="panel-note">
        使用已保存的策略模板，仅在已收盘信号出现时入场。仓位包含手续费／滑点余量，并向下取整；最小订单超过预算时跳过。总名义金额不超过共享预算，最多
        2 个仓位。未启用动态筛选时，有多个可交易信号优先最近 24 根 K
        线的中位成交额较高者；相关性使用最近 100 根已收盘收益率，过高或不足 30
        个共同样本则跳过加仓。该排序不代表收益预测。
      </p>
      <p class="panel-note">
        启用动态筛选时，先按已保存门槛选出前N个观察候选，再判断入场信号；排名高也可能等待。成交额以收盘价乘成交量估算；下单前还会核查新鲜盘口与价差。此处扫描和组合回测使用同一筛选公式。
      </p>
      <el-button :loading="busy" :disabled="!status.available" @click="scan"
        >扫描已保存的币种</el-button
      >
      <el-table v-if="cards.length" :data="cards">
        <el-table-column label="合约适配与候选" min-width="240"
          ><template #default="{ row }"
            ><p>
              排名 {{ row.selection_rank ?? "待诊断" }} ·
              {{ row.selected === false ? "本次排除" : "观察候选" }}
            </p>
            <p>{{ row.selection_reason ?? "请更新后端查看筛选诊断" }}</p>
            <p>{{ row.validation_status ?? "验证覆盖状态待核对" }}</p>
            <p v-if="row.budget_reason">{{ row.budget_reason }}</p></template
          ></el-table-column
        >
        <el-table-column label="因子与过滤" min-width="210"
          ><template #default="{ row }"
            ><details>
              <summary>查看已收盘因子</summary>
              <p v-for="factor in row.factors ?? []" :key="factor.key">
                {{ factor.label }}：{{
                  factor.value === null
                    ? "数据不足"
                    : factor.value.toFixed(3) + factor.unit
                }}
                ·
                {{
                  factor.enabled
                    ? factor.passed
                      ? "过滤通过"
                      : "过滤未通过"
                    : "参考值"
                }}<span v-if="factor.enabled"
                  >（{{ factor.comparison === "min" ? "至少" : "不超过"
                  }}{{ factor.threshold }}{{ factor.unit }}）</span
                >
              </p>
              <p v-if="!row.factors?.length">
                此结果尚未提供因子数值，请重新扫描。
              </p>
            </details></template
          ></el-table-column
        >
        <el-table-column prop="symbol" label="币种" /><el-table-column
          label="信号"
          ><template #default="{ row }">{{
            signalNames[row.signal] ?? row.signal
          }}</template></el-table-column
        ><el-table-column prop="quantity" label="建议数量" /><el-table-column
          prop="notional"
          label="名义金额"
        /><el-table-column prop="stop_loss" label="止损价" /><el-table-column
          prop="planned_loss"
          label="计划亏损含成本"
        /><el-table-column
          prop="trend"
          label="均线位置"
          min-width="150"
        /><el-table-column label="信号依据（已收盘）" min-width="240"
          ><template #default="{ row }"
            >{{ row.signal_basis }}
            <p v-if="row.higher_timeframe">
              1小时方向：{{
                signalNames[row.higher_timeframe.direction] ??
                row.higher_timeframe.direction
              }}
              · {{ row.higher_timeframe.semantics }}
            </p></template
          ></el-table-column
        ><el-table-column
          prop="reason"
          label="依据／跳过原因"
          min-width="220"
        />
      </el-table>
    </section>

    <section
      v-show="view === 'run' || view === 'records'"
      v-if="status.snapshot.ownership"
      class="panel external-activity"
    >
      <div class="panel-heading"><h3>订单来源与剩余可用资金</h3></div>
      <p>
        扣除持仓与挂单占用后的可用余额：{{
          status.snapshot.available_balance ?? "待同步"
        }}
        USDT；挂单保证金占用：{{ status.snapshot.order_margin ?? "待同步" }}
        USDT。
      </p>
      <p v-if="status.snapshot.ownership.blocked_symbols.length">
        系统跳过的币种：{{
          status.snapshot.ownership.blocked_symbols.join("、")
        }}。其他币种按剩余余额和预算检查；外部开仓挂单预留名义金额
        {{ status.snapshot.ownership.reserved_notional }} USDT。
      </p>
      <p class="panel-note">
        外部表示手工或其他程序创建；未知表示无法确认来源。本系统不会撤销外部订单，也不会接管其持仓。同一币种出现外部订单时暂停系统自动退出，原有交易所止损仍由交易所执行。
      </p>
      <el-table :data="status.snapshot.ownership.orders">
        <el-table-column prop="symbol" label="币种" />
        <el-table-column
          prop="orderId"
          label="交易所订单编号"
          min-width="200"
        />
        <el-table-column label="来源" min-width="130"
          ><template #default="{ row }">{{
            row.source === "system"
              ? "本系统"
              : row.source === "external"
                ? "外部／手工"
                : "未知来源"
          }}</template></el-table-column
        >
        <el-table-column prop="side" label="方向" /><el-table-column
          prop="qty"
          label="数量"
        /><el-table-column prop="price" label="价格" /><el-table-column
          prop="orderStatus"
          label="状态"
        />
        <el-table-column label="处理方式" min-width="140"
          ><template #default="{ row }">{{
            row.managed ? "系统管理" : "仅展示，不自动操作"
          }}</template></el-table-column
        >
      </el-table>
    </section>
    <section v-show="view === 'records'" class="panel">
      <div class="panel-heading">
        <h3>{{ environmentLabel }}订单与提交状态</h3>
      </div>
      <el-table :data="status.orders"
        ><el-table-column prop="symbol" label="币种" /><el-table-column
          prop="action"
          label="动作"
        /><el-table-column prop="side" label="方向" /><el-table-column
          prop="quantity"
          label="提交数量"
        /><el-table-column
          prop="filled_quantity"
          label="成交数量"
        /><el-table-column label="状态" min-width="160"
          ><template #default="{ row }">{{
            orderStatus(row.status)
          }}</template></el-table-column
        ><el-table-column prop="error" label="异常" min-width="220" />
        <el-table-column label="恢复操作" min-width="150"
          ><template #default="{ row }"
            ><el-button
              v-if="
                [
                  'intent',
                  'submitted',
                  'unknown',
                  'New',
                  'PartiallyFilled',
                  'Untriggered',
                ].includes(row.status)
              "
              size="small"
              :disabled="busy || !status.available || row.cancel_requested"
              @click="act(`orders/${row.id}/cancel`)"
              >{{
                row.cancel_requested ? "撤单待确认" : "请求撤单并暂停"
              }}</el-button
            ></template
          ></el-table-column
        >
      </el-table>
      <p class="panel-note">
        intent／submitted／unknown
        等未确认状态会占用执行通道。执行进程同时查询订单和逐笔成交，不自动重复提交。撤单只在交易所确认存在该订单后执行；查询不到时保留未确认状态，不直接清除记录。
      </p>
    </section>
    <div v-show="view === 'research'">
      <PortfolioResearchPanel
        :token="token"
        :disabled="!status.available"
        :symbols="status.configuration.symbols"
        :interval="status.configuration.interval"
        @completed="researchVersion++"
      />
    </div>
    <div v-show="view === 'records'">
      <TestnetJournalPanel
        :token="token"
        :trades="status.trades ?? []"
        :executions="status.executions ?? []"
        :ledger="status.ledger ?? []"
      />
    </div>
  </template>
</template>
<style scoped>
.trading-workflow ol {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
  gap: 18px 28px;
  padding-left: 22px;
}
.assistant-views {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
  margin: 16px 0;
}
.assistant-views button {
  min-width: 0;
  padding: 12px 8px;
  border: 1px solid var(--el-border-color);
  border-radius: 8px;
  background: var(--el-fill-color-blank);
  color: var(--el-text-color-primary);
  cursor: pointer;
}
.assistant-views button[aria-pressed="true"] {
  border-color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
  font-weight: 600;
}
.trading-workflow summary,
.account-details > summary {
  cursor: pointer;
  font-weight: 600;
}
.current-strategy-panel .panel-heading {
  flex-wrap: wrap;
}
@media (max-width: 600px) {
  .assistant-views {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
.trading-workflow li {
  padding-left: 4px;
}
.trading-workflow a {
  color: var(--el-color-primary);
  font-weight: 600;
}
.trading-workflow li p {
  font-size: 13px;
  line-height: 1.7;
  color: var(--el-text-color-secondary);
}
:deep([id^="trading-"]) {
  scroll-margin-top: 24px;
}
.quant-policy-settings {
  min-width: 0;
  margin: 16px 0;
  border: 1px solid var(--el-border-color);
  border-radius: 8px;
  padding: 16px;
}
</style>
