<script setup lang="ts">
import ResearchDiagnostics from "./components/ResearchDiagnostics.vue";
import { ElAlert } from "element-plus/es/components/alert/index.mjs";
import { ElButton } from "element-plus/es/components/button/index.mjs";
import { ElCheckbox } from "element-plus/es/components/checkbox/index.mjs";
import { ElDatePicker } from "element-plus/es/components/date-picker/index.mjs";
import { ElDescriptions } from "element-plus/es/components/descriptions/index.mjs";
import { ElDescriptionsItem } from "element-plus/es/components/descriptions/index.mjs";
import { ElDialog } from "element-plus/es/components/dialog/index.mjs";
import { ElEmpty } from "element-plus/es/components/empty/index.mjs";
import { ElForm } from "element-plus/es/components/form/index.mjs";
import { ElFormItem } from "element-plus/es/components/form/index.mjs";
import { ElIcon } from "element-plus/es/components/icon/index.mjs";
import { ElInput } from "element-plus/es/components/input/index.mjs";
import { ElInputNumber } from "element-plus/es/components/input-number/index.mjs";
import { ElOption } from "element-plus/es/components/select/index.mjs";
import { ElSelect } from "element-plus/es/components/select/index.mjs";
import { ElTable } from "element-plus/es/components/table/index.mjs";
import { ElTableColumn } from "element-plus/es/components/table/index.mjs";
import { ElTag } from "element-plus/es/components/tag/index.mjs";
import {
  defineAsyncComponent,
  computed,
  onBeforeUnmount,
  onMounted,
  reactive,
  ref,
} from "vue";
import { ElMessage } from "element-plus/es/components/message/index.mjs";
import { ElMessageBox } from "element-plus/es/components/message-box/index.mjs";
import {
  DataAnalysis,
  TrendCharts,
  SetUp,
  Document,
  Wallet,
  Refresh,
  Plus,
  SwitchButton,
  ArrowRight,
  CircleCheck,
} from "@element-plus/icons-vue";
const ChartPanel = defineAsyncComponent(
  () => import("./components/ChartPanel.vue"),
);
const MarketBoard = defineAsyncComponent(
  () => import("./components/MarketBoard.vue"),
);
import RuleFields from "./components/RuleFields.vue";
const OptimizationPanel = defineAsyncComponent(
  () => import("./components/OptimizationPanel.vue"),
);
const StrategyCurve = defineAsyncComponent(
  () => import("./components/StrategyCurve.vue"),
);
const ArchivePanel = defineAsyncComponent(
  () => import("./components/ArchivePanel.vue"),
);
const BybitEnvironmentPanel = defineAsyncComponent(
  () => import("./components/BybitEnvironmentPanel.vue"),
);
import HeaderEnvironmentSwitch from "./components/HeaderEnvironmentSwitch.vue";
const SystemHealthPanel = defineAsyncComponent(
  () => import("./components/SystemHealthPanel.vue"),
);
const TestnetTradingPanel = defineAsyncComponent(
  () => import("./components/TestnetTradingPanel.vue"),
);
import { ApiError, request } from "./api";
import type {
  Account,
  AuditEvent,
  Bar,
  Job,
  Order,
  Overview,
  Position,
  Strategy,
  Instrument,
  StrategyRules,
} from "./types";

type Page =
  | "overview"
  | "market"
  | "strategies"
  | "research"
  | "orders"
  | "account"
  | "trading";
const page = ref<Page>("overview");
const researchStrategyId = ref<number>();
function researchStrategy(strategy: Strategy) {
  researchStrategyId.value = strategy.id;
  page.value = "trading";
}
const environmentSwitching = ref(false);
const environmentSwitchTarget = ref("");
const environmentSwitchMessage = ref("");
const environmentSwitchElapsed = ref(0);
function onEnvironmentProgress(event: Event) {
  const value = (event as CustomEvent).detail;
  environmentSwitchMessage.value =
    value.switch_message || "后台正在更新账户与数据环境";
  environmentSwitchElapsed.value = value.switch_elapsed_seconds || 0;
}
const savedEnvironmentPage = sessionStorage.getItem(
  "quantpilot-environment-page",
);
sessionStorage.removeItem("quantpilot-environment-page");
const navigation = [
  { key: "overview" as Page, title: "总览", icon: DataAnalysis },
  { key: "market" as Page, title: "行情中心", icon: TrendCharts },
  { key: "strategies" as Page, title: "策略管理", icon: SetUp },
  { key: "research" as Page, title: "研究与回测", icon: DataAnalysis },
  { key: "orders" as Page, title: "持仓与订单", icon: Document },
  { key: "account" as Page, title: "交易所账户", icon: Wallet },
  { key: "trading" as Page, title: "交易助手", icon: SetUp },
];
const token = ref(sessionStorage.getItem("quantpilot-token") ?? "");
if (navigation.some((item) => item.key === savedEnvironmentPage))
  page.value = savedEnvironmentPage as Page;
function onEnvironmentSwitching(event: Event) {
  environmentSwitching.value = true;
  environmentSwitchMessage.value = "正在提交环境切换请求";
  environmentSwitchElapsed.value = 0;
  environmentSwitchTarget.value =
    (event as CustomEvent).detail === "mainnet" ? "主网" : "测试网";
}
function onEnvironmentChanged() {
  sessionStorage.setItem("quantpilot-environment-page", page.value);
  window.location.reload();
}
function onEnvironmentSwitchFailed() {
  environmentSwitching.value = false;
}
const tokenInput = ref("");
const authenticated = ref(false);
const initializing = ref(true);
const authRequired = ref(false);
const busy = ref(false);
const loginBusy = ref(false);
const error = ref("");
const marketError = ref("");
const overview = ref<Overview>();
const strategies = ref<Strategy[]>([]);
const positions = ref<Position[]>([]);
const orders = ref<Order[]>([]);
const events = ref<AuditEvent[]>([]);
const jobs = ref<Job[]>([]);
const selectedJob = ref<Job>();
const account = ref<Account>();
const accountBusy = ref(false);
const accountExchange = ref("bybit");
const bars = ref<Bar[]>([]);
const symbolOptions = ref<Record<string, Instrument[]>>({});
const marketBoard = ref<InstanceType<typeof MarketBoard>>();
const marketDetail = ref<HTMLElement>();
const marketSignal = ref("HOLD");
const fetchedAt = ref("");
const marketForm = reactive({
  exchange: "bybit",
  symbol: "BTCUSDT",
  interval: "15",
});
const strategyDialog = ref(false);
const submitting = ref(false);
const defaultRules = (): StrategyRules => ({
  version: 1,
  strategy_type: "ema_rsi",
  fast: 20,
  slow: 60,
  rsi_period: 14,
  rsi_lower: 30,
  rsi_upper: 70,
  stop_loss_pct: 0,
  take_profit_pct: 0,
  max_drawdown_pct: 0,
  breakout_period: 20,
  bollinger_period: 20,
  bollinger_std: 2,
  volume_period: 20,
  volume_ratio: 0,
  atr_period: 14,
  atr_pct_max: 0,
  adx_period: 14,
  adx_min: 0,
});
const templateName = (type: string) =>
  ({
    ema_rsi: "EMA / RSI 趋势",
    ema_cross: "EMA 双均线",
    rsi_reversion: "RSI 均值回归",
    breakout: "区间突破",
    bollinger_reversion: "布林带回归",
  })[type] ?? type;
const researchDates = ref<[Date, Date]>();
const researchRange = ref("recent");
const batchEnabled = ref(false);
const fastVariants = ref("10,20,30");
const slowVariants = ref("40,60,90");
const comparisonBatch = ref("");
const comparisonJobs = computed(() =>
  jobs.value.filter(
    (job) =>
      job.summary &&
      (!comparisonBatch.value ||
        job.parameters.batch_id === comparisonBatch.value),
  ),
);
const strategyDetailId = ref<number>();
const strategyDetail = computed(() =>
  strategies.value.find((strategy) => strategy.id === strategyDetailId.value),
);
const strategyForm = reactive({
  rules: defaultRules(),
  fee_rate: 0.00055,
  slippage_bps: 2,
  name: "",
  exchange: "bybit",
  symbol: "BTCUSDT",
  interval: "15",
  quantity: 0.001,
  initial_equity: 10000,
  max_notional: 1000,
});
const researchForm = reactive({
  rules: defaultRules(),
  max_notional: 1000,
  evaluation_phase: "research",
  exchange: "bybit",
  symbol: "BTCUSDT",
  interval: "15",
  limit: 500,
  initial_equity: 10000,
  quantity: 0.001,
  fee_rate: 0.00055,
  slippage_bps: 2,
});
const intervals = [
  { value: "1", label: "1 分钟" },
  { value: "5", label: "5 分钟" },
  { value: "15", label: "15 分钟" },
  { value: "30", label: "30 分钟" },
  { value: "60", label: "1 小时" },
  { value: "240", label: "4 小时" },
  { value: "D", label: "1 天" },
];
let timer: number | undefined;
let marketSequence = 0;
let refreshSequence = 0;
const money = (value: unknown, digits = 2) =>
  Number(value ?? 0).toLocaleString("zh-CN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
const time = (value: string) =>
  new Date(
    /[Zz]|[+-]\d\d:\d\d$/.test(value) ? value : `${value}Z`,
  ).toLocaleString("zh-CN", { hour12: false });
const signed = (value: unknown) =>
  `${Number(value) >= 0 ? "+" : ""}${money(value)}`;
const statusLabel = (status: string) =>
  ({
    running: "运行中",
    paused: "已暂停",
    flattening: "等待平仓",
    pending: "排队中",
    completed: "已完成",
    failed: "失败",
  })[status] ?? status;
const signalLabel = (value: string) =>
  ({ LONG: "做多信号", SHORT: "做空信号", HOLD: "等待信号" })[value] ?? value;
const pnl = computed(
  () =>
    Number(overview.value?.equity ?? 0) -
    Number(overview.value?.initial_equity ?? 0),
);
const latest = computed(() => bars.value.at(-1));
const priceChange = computed(() =>
  bars.value.length > 1
    ? (latest.value!.close / bars.value.at(-2)!.close - 1) * 100
    : 0,
);
const result = computed(() => selectedJob.value?.result);
const demo = computed(() => overview.value?.source === "demo");
const heading = computed(
  () => navigation.find((item) => item.key === page.value)?.title,
);
const descriptions: Record<Page, string> = {
  overview: "行情、策略与资金状态，在一个工作台掌握。",
  market: "查看已收盘行情，跟踪你的交易标的。",
  strategies: "从研究配置创建模拟实例，观察收益、回撤与运行状态。",
  research: "使用历史样本评估策略，所有回测保留记录。",
  orders: "查看模拟持仓、成交费用与订单明细。",
  account: "检查交易所连接与余额，凭据保留在服务端。",
  trading: "按当前主网／测试网环境执行多币种策略，共享预算可自行设置。",
};

async function api<T>(path: string, body?: unknown) {
  try {
    return await request<T>(path, token.value, body);
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) {
      authenticated.value = false;
      sessionStorage.removeItem("quantpilot-token");
    }
    throw err;
  }
}
function message(err: unknown) {
  return err instanceof Error ? err.message : "请求失败，请重试";
}
async function initializeWorkspace() {
  initializing.value = true;
  error.value = "";
  try {
    const health = await request<{ auth_required: boolean }>("/health", "");
    authRequired.value = health.auth_required ?? true;
    if (!authRequired.value) {
      token.value = "";
      tokenInput.value = "";
      sessionStorage.removeItem("quantpilot-token");
      authenticated.value = true;
      await Promise.all([
        refresh(),
        loadMarket(),
        loadSymbols(marketForm.exchange),
      ]);
    } else if (token.value) {
      tokenInput.value = token.value;
      await unlock();
    }
  } catch (err) {
    error.value = message(err);
  } finally {
    initializing.value = false;
  }
}
async function unlock() {
  loginBusy.value = true;
  error.value = "";
  token.value = tokenInput.value.trim();
  try {
    overview.value = await api<Overview>("/overview");
    authenticated.value = true;
    sessionStorage.setItem("quantpilot-token", token.value);
    await Promise.all([
      refresh(),
      loadMarket(),
      loadSymbols(marketForm.exchange),
    ]);
  } catch (err) {
    error.value = message(err);
  } finally {
    loginBusy.value = false;
  }
}
function lock() {
  authenticated.value = false;
  token.value = "";
  tokenInput.value = "";
  error.value = "";
  sessionStorage.removeItem("quantpilot-token");
  refreshSequence++;
  marketSequence++;
  overview.value = undefined;
  strategies.value = [];
  positions.value = [];
  orders.value = [];
  events.value = [];
  jobs.value = [];
  selectedJob.value = undefined;
  account.value = undefined;
  bars.value = [];
}
async function refresh(silent = false) {
  if (!authenticated.value || busy.value) return;
  const sequence = ++refreshSequence;
  busy.value = true;
  try {
    const data = await Promise.all([
      api<Overview>("/overview"),
      api<Strategy[]>("/strategies"),
      api<Position[]>("/positions"),
      api<Order[]>("/orders"),
      api<AuditEvent[]>("/events"),
      api<Job[]>("/backtests"),
    ]);
    if (sequence !== refreshSequence || !authenticated.value) return;
    [
      overview.value,
      strategies.value,
      positions.value,
      orders.value,
      events.value,
      jobs.value,
    ] = data;
    fetchedAt.value = new Date().toLocaleTimeString("zh-CN", { hour12: false });
    error.value = "";
    if (
      selectedJob.value &&
      ["pending", "running"].includes(selectedJob.value.status)
    )
      await openJob(selectedJob.value.id);
  } catch (err) {
    if (!silent) ElMessage.error(message(err));
    error.value = message(err);
  } finally {
    busy.value = false;
  }
}
async function loadMarket() {
  if (!authenticated.value) return;
  const sequence = ++marketSequence;
  marketError.value = "";
  bars.value = [];
  try {
    const data = await api<{ bars: Bar[]; signal: string }>(
      `/market?${new URLSearchParams(marketForm)}`,
    );
    if (sequence === marketSequence && authenticated.value) {
      bars.value = data.bars;
      marketSignal.value = data.signal;
    }
  } catch (err) {
    if (sequence === marketSequence) {
      bars.value = [];
      marketError.value = message(err);
    }
  }
}
function symbolsFor(exchange: string) {
  return symbolOptions.value[exchange] ?? [];
}
async function loadSymbols(exchange: string) {
  if (!authenticated.value) return [];
  try {
    const data = await api<{ instruments: Instrument[] }>(
      `/instruments?exchange=${exchange}`,
    );
    symbolOptions.value = {
      ...symbolOptions.value,
      [exchange]: data.instruments,
    };
    return data.instruments;
  } catch (err) {
    ElMessage.error(message(err));
    return [];
  }
}
async function changeExchange(form: { exchange: string; symbol: string }) {
  const exchange = form.exchange;
  const symbols = await loadSymbols(exchange);
  if (form.exchange !== exchange) return;
  if (symbols.length && !symbols.some((item) => item.symbol === form.symbol))
    form.symbol = symbols[0]!.symbol;
  if (form === marketForm) await loadMarket();
}
function showSymbol(symbol: string) {
  marketForm.symbol = symbol;
  void loadMarket();
  marketDetail.value?.scrollIntoView({ behavior: "smooth", block: "start" });
}
async function createStrategy() {
  if (!strategyForm.name.trim()) {
    ElMessage.warning("请填写策略名称");
    return;
  }
  submitting.value = true;
  try {
    await api("/strategies", strategyForm);
    strategyDialog.value = false;
    strategyForm.name = "";
    ElMessage.success("策略已创建，当前为暂停状态");
    await refresh();
  } catch (err) {
    ElMessage.error(message(err));
  } finally {
    submitting.value = false;
  }
}
async function command(strategy: Strategy, action: string) {
  if (action === "flatten") {
    try {
      await ElMessageBox.confirm(
        "引擎将按下一次获取的价格平掉模拟持仓，并暂停策略。",
        "平仓并暂停",
        { confirmButtonText: "确认平仓", cancelButtonText: "取消" },
      );
    } catch {
      return;
    }
  }
  try {
    await api(`/strategies/${strategy.id}/command`, { action });
    ElMessage.success(
      action === "pause"
        ? "已暂停新交易，已有持仓继续执行风控保护"
        : "指令已提交",
    );
    await refresh();
  } catch (err) {
    ElMessage.error(message(err));
  }
}
async function queueBacktest() {
  submitting.value = true;
  try {
    if (researchRange.value === "dates" && !researchDates.value)
      throw new Error("请选择回测起止日期");
    const payload = {
      ...researchForm,
      ...(researchRange.value === "dates" && researchDates.value
        ? {
            start_at: researchDates.value[0].toISOString(),
            end_at: researchDates.value[1].toISOString(),
          }
        : {}),
    };
    if (batchEnabled.value) {
      const parse = (text: string) => {
        const values = [
          ...new Set(
            text
              .split(/[,，\s]+/)
              .filter(Boolean)
              .map(Number),
          ),
        ];
        if (!values.length || values.some((value) => !Number.isInteger(value)))
          throw new Error("参数组请填写逗号分隔的整数");
        return values;
      };
      const variants = parse(fastVariants.value).flatMap((fast) =>
        parse(slowVariants.value)
          .filter((slow) => fast < slow)
          .map((slow) => ({ ...researchForm.rules, fast, slow })),
      );
      if (!variants.length || variants.length > 9)
        throw new Error("有效参数组合必须为 1 至 9 组（快 EMA 小于慢 EMA）");
      if (!["ema_rsi", "ema_cross"].includes(researchForm.rules.strategy_type))
        throw new Error("RSI 模板请使用单次回测；EMA 参数扫描适用于趋势模板");
      const batch = await api<{ batch_id: string; jobs: Job[] }>(
        "/backtests/batch",
        { base: payload, variants },
      );
      comparisonBatch.value = batch.batch_id;
      selectedJob.value = batch.jobs[0];
    } else selectedJob.value = await api<Job>("/backtests", payload);
    await refresh();
    ElMessage.success("回测已加入队列");
  } catch (err) {
    ElMessage.error(message(err));
  } finally {
    submitting.value = false;
  }
}
async function promoteJob(job: Job) {
  try {
    const { value } = await ElMessageBox.prompt(
      "沿用这次回测的策略参数、数量、费用和风控，创建后保持暂停。",
      "创建模拟策略",
      {
        inputValue: `${job.parameters.symbol} 回测 #${job.id}`,
        inputValidator: (value) => !!value?.trim() && value.length <= 80,
        confirmButtonText: "创建",
        cancelButtonText: "取消",
      },
    );
    const strategy = await api<Strategy>(`/backtests/${job.id}/promote`, {
      name: value.trim(),
    });
    await refresh();
    strategyDetailId.value = strategy.id;
    page.value = "strategies";
    ElMessage.success("回测配置已创建为暂停的模拟策略");
  } catch (err) {
    if (err !== "cancel" && err !== "close") ElMessage.error(message(err));
  }
}
async function openJob(id: number) {
  try {
    selectedJob.value = await api<Job>(`/backtests/${id}`);
  } catch (err) {
    ElMessage.error(message(err));
  }
}
async function readAccount() {
  accountBusy.value = true;
  account.value = undefined;
  try {
    account.value = await api<Account>(
      `/account?exchange=${accountExchange.value}`,
    );
  } catch (err) {
    ElMessage.error(message(err));
  } finally {
    accountBusy.value = false;
  }
}
function downloadOrders() {
  const rows = [
    [
      "id",
      "symbol",
      "side",
      "action",
      "quantity",
      "price",
      "fee",
      "realized_pnl",
      "source",
      "created_at",
    ],
    ...orders.value.map((o) => [
      o.id,
      o.symbol,
      o.side,
      o.action,
      o.quantity,
      o.price,
      o.fee,
      o.realized_pnl,
      o.source,
      o.created_at,
    ]),
  ];
  const blob = new Blob(
    [
      "\uFEFF" +
        rows
          .map((row) =>
            row.map((v) => `"${String(v).replaceAll('"', '""')}"`).join(","),
          )
          .join("\n"),
    ],
    { type: "text/csv;charset=utf-8" },
  );
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "quantpilot-paper-orders.csv";
  link.click();
  URL.revokeObjectURL(url);
}

const candleOption = computed(() => ({
  animation: false,
  tooltip: { trigger: "axis" },
  grid: [
    { left: 68, right: 22, top: 18, bottom: 96 },
    { left: 68, right: 22, height: 44, bottom: 42 },
  ],
  xAxis: [0, 1].map((gridIndex) => ({
    gridIndex,
    type: "category",
    data: bars.value.map((b) => b.timestamp.slice(5, 16).replace("T", " ")),
    axisLine: { lineStyle: { color: "#dbe3ed" } },
    axisLabel: { color: "#8794a6", show: gridIndex === 1 },
    boundaryGap: true,
  })),
  yAxis: [
    {
      scale: true,
      splitLine: { lineStyle: { color: "#eff3f8" } },
      axisLabel: { color: "#8794a6" },
    },
    {
      gridIndex: 1,
      scale: true,
      splitNumber: 1,
      axisLabel: { show: false },
      splitLine: { show: false },
    },
  ],
  dataZoom: [{ type: "inside", xAxisIndex: [0, 1], start: 45, end: 100 }],
  series: [
    {
      type: "candlestick",
      data: bars.value.map((b) => [b.open, b.close, b.low, b.high]),
      itemStyle: {
        color: "#159f87",
        color0: "#e26974",
        borderColor: "#159f87",
        borderColor0: "#e26974",
      },
    },
    {
      type: "bar",
      xAxisIndex: 1,
      yAxisIndex: 1,
      data: bars.value.map((b) => ({
        value: b.volume,
        itemStyle: { color: b.close >= b.open ? "#a3dacf" : "#f0c1c5" },
      })),
    },
  ],
}));
const equityOption = computed(() => ({
  animation: false,
  tooltip: { trigger: "axis" },
  grid: { left: 78, right: 24, top: 24, bottom: 42 },
  xAxis: {
    type: "category",
    data: result.value?.curve.map((p) => p.timestamp.slice(5, 16)) ?? [],
    axisLabel: { color: "#8794a6" },
    axisLine: { lineStyle: { color: "#dbe3ed" } },
  },
  yAxis: {
    type: "value",
    scale: true,
    axisLabel: { color: "#8794a6" },
    splitLine: { lineStyle: { color: "#eff3f8" } },
  },
  series: [
    {
      type: "line",
      showSymbol: false,
      data: result.value?.curve.map((p) => p.equity) ?? [],
      lineStyle: { color: "#159f87", width: 2 },
      areaStyle: { color: "#159f87", opacity: 0.08 },
    },
  ],
}));
onMounted(async () => {
  window.addEventListener(
    "bybit-environment-switching",
    onEnvironmentSwitching,
  );
  window.addEventListener("bybit-environment-changed", onEnvironmentChanged);
  window.addEventListener("bybit-environment-progress", onEnvironmentProgress);
  window.addEventListener(
    "bybit-environment-switch-failed",
    onEnvironmentSwitchFailed,
  );
  await initializeWorkspace();
  timer = window.setInterval(() => {
    if (authenticated.value && !environmentSwitching.value) void refresh(true);
  }, 5000);
});
onBeforeUnmount(() => {
  window.removeEventListener(
    "bybit-environment-switching",
    onEnvironmentSwitching,
  );
  window.removeEventListener("bybit-environment-changed", onEnvironmentChanged);
  window.removeEventListener(
    "bybit-environment-progress",
    onEnvironmentProgress,
  );
  window.removeEventListener(
    "bybit-environment-switch-failed",
    onEnvironmentSwitchFailed,
  );
  window.clearInterval(timer);
});
</script>

<template>
  <div
    v-if="environmentSwitching"
    role="status"
    class="environment-switch-overlay"
    style="
      position: fixed;
      inset: 0;
      z-index: 9999;
      background: rgba(15, 23, 42, 0.85);
      color: white;
      display: grid;
      place-items: center;
      text-align: center;
    "
  >
    <div>
      <h2>正在切换到{{ environmentSwitchTarget }}</h2>
      <p>
        {{ environmentSwitchMessage }}（已等待
        {{ environmentSwitchElapsed }} 秒）
      </p>
      <p>完成后自动刷新，自动交易保持暂停。</p>
    </div>
  </div>
  <div
    v-if="initializing || (!authenticated && !authRequired)"
    class="startup-screen"
  >
    <div class="startup-card">
      <h2>QuantPilot</h2>
      <p>{{ initializing ? "正在加载工作台…" : error || "工作台连接中断" }}</p>
      <el-button
        v-if="!initializing"
        type="primary"
        @click="initializeWorkspace"
        >重新连接</el-button
      >
    </div>
  </div>
  <div v-else-if="!authenticated" class="login-shell">
    <div class="login-art">
      <div class="brand large">
        <span class="brand-mark">Q</span> QuantPilot
      </div>
      <div class="login-copy">
        <span class="eyebrow">YOUR PRIVATE QUANT WORKSPACE</span>
        <h1>让每一次交易，<br />有据可循。</h1>
        <p>从行情与研究，到策略与执行。<br />你的私人量化工作台。</p>
        <div class="login-line"></div>
        <span class="login-foot">研究 · 回测 · 模拟交易</span>
      </div>
    </div>
    <div class="login-form">
      <div class="login-card">
        <span class="eyebrow">PRIVATE ACCESS</span>
        <h2>进入你的工作台</h2>
        <p>使用平台访问凭证解锁本次会话。</p>
        <el-form @submit.prevent="unlock"
          ><el-form-item label="访问凭证" label-position="top"
            ><el-input
              v-model="tokenInput"
              type="password"
              show-password
              placeholder="输入平台访问凭证"
              size="large"
              autocomplete="off" /></el-form-item
          ><el-alert
            v-if="error"
            :title="error"
            type="error"
            :closable="false"
            class="mb" /><el-button
            type="primary"
            size="large"
            native-type="submit"
            :loading="loginBusy"
            class="login-button"
            >进入工作台 <el-icon><ArrowRight /></el-icon></el-button
        ></el-form>
        <p class="login-hint">
          初次使用：凭证位于本机 .env.platform 文件。<br />这是平台凭证，与交易所
          API 密钥不同。
        </p>
      </div>
    </div>
  </div>

  <div v-else class="app-shell">
    <aside class="sidebar">
      <div class="brand"><span class="brand-mark">Q</span> QuantPilot</div>
      <div class="workspace">
        <span class="avatar">P</span>
        <div>
          <strong>私人工作空间</strong><small>PERSONAL WORKSPACE</small>
        </div>
      </div>
      <span class="nav-label">工作台</span>
      <nav>
        <button
          v-for="item in navigation"
          :key="item.key"
          :class="{ active: page === item.key }"
          @click="page = item.key"
        >
          <el-icon><component :is="item.icon" /></el-icon>{{ item.title
          }}<span
            v-if="item.key === 'strategies' && overview?.running"
            class="nav-count"
            >{{ overview.running }}</span
          >
        </button>
      </nav>
      <div class="sidebar-bottom">
        <span class="status-dot"></span>模拟研究 / Bybit 交易
        <p>执行进程独立运行<br />测试币交易在交易助手中启用</p>
        <button v-if="authRequired" @click="lock">
          <el-icon><SwitchButton /></el-icon>锁定工作台
        </button>
      </div>
    </aside>

    <main class="main">
      <header class="topbar">
        <div class="breadcrumb">
          工作空间 <span>/</span> <strong>{{ heading }}</strong>
        </div>
        <div class="topbar-right">
          <HeaderEnvironmentSwitch :token="token" />
          <span class="source-badge" :class="{ exchange: !demo }">{{
            demo ? "演示行情" : "交易所行情"
          }}</span
          ><span class="owner-avatar">P</span>
        </div>
      </header>
      <div class="content">
        <SystemHealthPanel :token="token" />
        <div class="page-title">
          <div>
            <span class="eyebrow">{{
              page === "overview"
                ? "WORKSPACE OVERVIEW"
                : "QUANTITATIVE WORKSPACE"
            }}</span>
            <h1>{{ heading }}</h1>
            <p>{{ descriptions[page] }}</p>
          </div>
          <el-button
            :icon="Refresh"
            :loading="busy"
            @click="
              refresh();
              loadMarket();
              loadSymbols(marketForm.exchange);
              marketBoard?.refresh(true);
            "
            >刷新数据</el-button
          >
        </div>
        <el-alert
          v-if="error"
          :title="error"
          type="error"
          :closable="false"
          class="mb"
        />
        <div class="mode-banner">
          <el-icon><CircleCheck /></el-icon
          ><span>{{
            demo
              ? "演示环境：使用合成行情，资金、持仓与订单均为模拟数据。"
              : "交易所行情：原策略管理为本地模拟；交易助手按当前生效的 Bybit 环境执行订单。"
          }}</span
          ><small>SIMULATION</small>
        </div>

        <template v-if="page === 'overview'">
          <div class="metrics-grid">
            <div class="metric-card">
              <span>本地模拟总权益 <small>USDT</small></span
              ><strong>{{ money(overview?.equity) }}</strong>
              <div :class="pnl >= 0 ? 'positive' : 'negative'">
                {{ signed(pnl) }} <span>累计净盈亏</span>
              </div>
            </div>
            <div class="metric-card">
              <span>持仓浮动盈亏 <small>USDT</small></span
              ><strong
                :class="
                  Number(overview?.unrealized_pnl) >= 0
                    ? 'positive'
                    : 'negative'
                "
                >{{ signed(overview?.unrealized_pnl) }}</strong
              >
              <div>{{ overview?.positions ?? 0 }} <span>个模拟持仓</span></div>
            </div>
            <div class="metric-card">
              <span>运行策略</span
              ><strong
                >{{ overview?.running ?? 0
                }}<small>/ {{ overview?.strategies ?? 0 }}</small></strong
              >
              <div>
                <span class="status-dot"></span><span>独立交易引擎执行</span>
              </div>
            </div>
            <div class="metric-card">
              <span>模拟成交</span
              ><strong>{{ overview?.orders ?? 0 }}<small>笔</small></strong>
              <div><span>开仓、平仓均单独记录</span></div>
            </div>
          </div>
          <div class="overview-grid">
            <section class="panel">
              <div class="panel-heading">
                <div>
                  <h3>市场观察</h3>
                  <span
                    >{{ marketForm.symbol }} ·
                    {{
                      intervals.find((i) => i.value === marketForm.interval)
                        ?.label
                    }}
                    · 已收盘 K 线</span
                  >
                </div>
                <el-button text @click="page = 'market'"
                  >行情中心 <el-icon><ArrowRight /></el-icon
                ></el-button>
              </div>
              <div v-if="latest" class="market-price">
                {{ money(latest.close, latest.close < 1 ? 6 : 2)
                }}<span :class="priceChange >= 0 ? 'positive' : 'negative'"
                  >{{ priceChange >= 0 ? "+" : ""
                  }}{{ priceChange.toFixed(2) }}%</span
                ><small>较上一根</small>
              </div>
              <el-empty
                v-if="marketError || !bars.length"
                :description="marketError || '暂无行情'"
              /><ChartPanel v-else :option="candleOption" :height="320" />
            </section>
            <section class="panel activity-panel">
              <div class="panel-heading">
                <h3>运行状态</h3>
                <span class="subtle">{{ fetchedAt }}</span>
              </div>
              <div
                v-for="worker in overview?.workers"
                :key="worker.name"
                class="worker"
              >
                <span class="worker-icon"
                  ><el-icon><SetUp /></el-icon
                ></span>
                <div>
                  <strong>{{
                    worker.name === "testnet"
                      ? "Bybit 执行与对账"
                      : worker.name === "engine"
                        ? "模拟交易引擎"
                        : "回测计算进程"
                  }}</strong
                  ><small>{{
                    worker.online ? "服务持续运行中" : "等待服务启动"
                  }}</small>
                </div>
                <el-tag
                  :type="worker.online ? 'success' : 'info'"
                  size="small"
                  >{{ worker.online ? "在线" : "离线" }}</el-tag
                >
              </div>
              <div class="panel-divider"></div>
              <h3 class="activity-title">最近活动</h3>
              <el-empty
                v-if="!events.length"
                description="创建策略后，活动会显示在这里"
                :image-size="60"
              />
              <div
                v-for="event in events.slice(0, 5)"
                :key="event.id"
                class="event"
              >
                <span class="event-dot" :class="event.level"></span>
                <div>
                  <p>{{ event.message }}</p>
                  <small>{{ time(event.created_at) }}</small>
                </div>
              </div>
            </section>
          </div>
          <section class="panel">
            <div class="panel-heading">
              <div>
                <h3>我的策略</h3>
                <span>EMA 20 / 60 交叉 + RSI 14 过滤</span>
              </div>
              <el-button
                type="primary"
                :icon="Plus"
                @click="strategyDialog = true"
                >创建策略</el-button
              >
            </div>
            <el-empty
              v-if="!strategies.length"
              description="创建第一个策略，开始模拟运行"
              :image-size="80"
            />
            <div v-else class="strategy-preview">
              <div
                v-for="strategy in strategies.slice(0, 4)"
                :key="strategy.id"
                class="strategy-mini"
                @click="page = 'strategies'"
              >
                <div>
                  <span class="symbol-icon">{{
                    strategy.symbol.slice(0, 1)
                  }}</span
                  ><strong>{{ strategy.name }}</strong
                  ><el-tag
                    :type="strategy.status === 'running' ? 'success' : 'info'"
                    size="small"
                    >{{ statusLabel(strategy.status) }}</el-tag
                  >
                </div>
                <p>
                  {{ strategy.symbol }} · {{ strategy.exchange.toUpperCase() }}
                </p>
                <span
                  >模拟资金 <strong>{{ money(strategy.cash) }}</strong></span
                >
              </div>
            </div>
          </section>
        </template>

        <ArchivePanel
          v-if="page === 'overview'"
          :token="token"
          @archived="refresh()"
        />

        <template v-if="page === 'market'">
          <section class="panel market-selector-panel">
            <div class="market-controls">
              <el-select
                v-model="marketForm.exchange"
                @change="changeExchange(marketForm)"
                ><el-option label="Bybit" value="bybit" /><el-option
                  label="Binance"
                  value="binance" /></el-select
              ><el-select
                v-model="marketForm.symbol"
                filterable
                @change="loadMarket"
                ><el-option
                  v-for="instrument in symbolsFor(marketForm.exchange)"
                  :key="instrument.symbol"
                  :label="instrument.symbol"
                  :value="instrument.symbol" /></el-select
              ><el-select v-model="marketForm.interval" @change="loadMarket"
                ><el-option
                  v-for="interval in intervals"
                  :key="interval.value"
                  :label="interval.label"
                  :value="interval.value" /></el-select
              ><el-tag>{{ signalLabel(marketSignal) }}</el-tag>
            </div>
          </section>
          <MarketBoard
            ref="marketBoard"
            :exchange="marketForm.exchange"
            :token="token"
            :selected-symbol="marketForm.symbol"
            @select="showSymbol"
          />
          <section ref="marketDetail" class="panel market-detail">
            <div class="panel-heading">
              <h3>{{ marketForm.symbol }} · 单币详情</h3>
              <span>已收盘 K 线 · {{ signalLabel(marketSignal) }}</span>
            </div>
            <div v-if="latest" class="market-price">
              {{ money(latest.close, latest.close < 1 ? 6 : 2)
              }}<small>最新收盘价 · {{ bars.length }} 根样本 · UTC</small>
            </div>
            <el-empty
              v-if="marketError || !bars.length"
              :description="marketError || '暂无行情'"
            /><ChartPanel v-else :option="candleOption" :height="480" />
          </section>
          <section class="panel">
            <div class="panel-heading">
              <h3>已收盘行情</h3>
              <span>时间按本地时区显示</span>
            </div>
            <el-table :data="[...bars].reverse().slice(0, 50)" max-height="360"
              ><el-table-column label="时间" min-width="190"
                ><template #default="{ row }">{{
                  time(row.timestamp)
                }}</template></el-table-column
              ><el-table-column
                v-for="field in ['open', 'high', 'low', 'close', 'volume']"
                :key="field"
                :prop="field"
                :label="
                  (
                    {
                      open: '开盘',
                      high: '最高',
                      low: '最低',
                      close: '收盘',
                      volume: '成交量',
                    } as Record<string, string>
                  )[field]
                "
                ><template #default="{ row }">{{
                  money(row[field])
                }}</template></el-table-column
              ></el-table
            >
          </section>
        </template>

        <template v-if="page === 'strategies'">
          <section class="panel">
            <div class="panel-heading">
              <div>
                <h3>策略实例</h3>
                <span>每个实例拥有独立模拟资金与持仓</span>
              </div>
              <el-button
                type="primary"
                :icon="Plus"
                @click="strategyDialog = true"
                >创建策略</el-button
              >
            </div>
            <el-table :data="strategies" empty-text="尚未创建策略"
              ><el-table-column label="策略" min-width="210"
                ><template #default="{ row }"
                  ><strong>{{ row.name }}</strong>
                  <div class="table-sub">
                    {{ row.exchange.toUpperCase() }} / {{ row.symbol }} /
                    {{ row.interval === "D" ? "1 天" : `${row.interval} 分钟` }}
                  </div></template
                ></el-table-column
              ><el-table-column label="状态" width="120"
                ><template #default="{ row }"
                  ><el-tag
                    :type="
                      row.status === 'running'
                        ? 'success'
                        : row.status === 'flattening'
                          ? 'warning'
                          : 'info'
                    "
                    >{{ statusLabel(row.status) }}</el-tag
                  ></template
                ></el-table-column
              ><el-table-column label="收益 / 最大回撤" min-width="180"
                ><template #default="{ row }"
                  >{{ (Number(row.total_return) * 100).toFixed(2) }}% /
                  {{ (Number(row.max_drawdown) * 100).toFixed(2) }}%
                  <div class="table-sub">
                    {{ templateName(row.rules.strategy_type) }} · v{{
                      row.rules.version
                    }}
                  </div></template
                ></el-table-column
              ><el-table-column label="最新信号" width="130"
                ><template #default="{ row }">{{
                  signalLabel(row.signal)
                }}</template></el-table-column
              ><el-table-column label="模拟现金" min-width="140"
                ><template #default="{ row }">{{
                  money(row.cash)
                }}</template></el-table-column
              ><el-table-column
                label="每次数量"
                prop="quantity"
                width="120"
              /><el-table-column label="操作" min-width="220"
                ><template #default="{ row }"
                  ><el-button link @click="strategyDetailId = row.id"
                    >详情</el-button
                  ><el-button
                    link
                    :disabled="row.exchange !== 'bybit'"
                    @click="researchStrategy(row as Strategy)"
                    >研究并用于交易助手</el-button
                  ><el-button
                    v-if="row.status === 'paused'"
                    link
                    type="primary"
                    @click="command(row as Strategy, 'start')"
                    >启动</el-button
                  ><el-button
                    v-if="row.status === 'running'"
                    link
                    @click="command(row as Strategy, 'pause')"
                    >暂停</el-button
                  ><el-button
                    link
                    type="danger"
                    :disabled="row.status === 'flattening'"
                    @click="command(row as Strategy, 'flatten')"
                    >平仓并暂停</el-button
                  ></template
                ></el-table-column
              ></el-table
            >
            <p class="panel-note">
              暂停会停止信号处理并保留模拟持仓。需要退出持仓时，使用“平仓并暂停”。
              “研究并用于交易助手”仅支持 Bybit
              策略，先研究并独立验证，不会直接应用或启动交易。
            </p>
          </section>
          <section v-if="strategyDetail" class="panel">
            <div class="panel-heading">
              <div>
                <h3>{{ strategyDetail.name }} · 运行详情</h3>
                <span
                  >{{ templateName(strategyDetail.rules.strategy_type) }} · 配置
                  v{{ strategyDetail.rules.version }} ·
                  {{
                    strategyDetail.origin_job_id
                      ? `来自回测 #${strategyDetail.origin_job_id}`
                      : "手动创建"
                  }}</span
                >
              </div>
              <el-button @click="strategyDetailId = undefined">收起</el-button>
            </div>
            <el-descriptions :column="2" border>
              <el-descriptions-item label="模拟权益"
                >{{ money(strategyDetail.equity) }} USDT</el-descriptions-item
              >
              <el-descriptions-item label="最大回撤"
                >{{
                  (Number(strategyDetail.max_drawdown) * 100).toFixed(2)
                }}%</el-descriptions-item
              >
              <el-descriptions-item label="EMA / RSI"
                >{{ strategyDetail.rules.fast }} /
                {{ strategyDetail.rules.slow }} · RSI
                {{ strategyDetail.rules.rsi_period }}（{{
                  strategyDetail.rules.rsi_lower
                }}
                / {{ strategyDetail.rules.rsi_upper }}）</el-descriptions-item
              >
              <el-descriptions-item label="持仓">{{
                strategyDetail.position
                  ? `${strategyDetail.position.side} · ${strategyDetail.position.quantity}`
                  : "无持仓"
              }}</el-descriptions-item>
              <el-descriptions-item label="止损 / 止盈"
                >{{ strategyDetail.rules.stop_loss_pct }}% /
                {{
                  strategyDetail.rules.take_profit_pct
                }}%</el-descriptions-item
              >
              <el-descriptions-item label="回撤暂停上限"
                >{{ strategyDetail.rules.max_drawdown_pct }}%（0
                为关闭）</el-descriptions-item
              >
              <el-descriptions-item label="最近处理 K 线">{{
                strategyDetail.last_bar
                  ? time(strategyDetail.last_bar)
                  : "尚未处理"
              }}</el-descriptions-item>
              <el-descriptions-item label="当前异常">{{
                strategyDetail.last_error || "无"
              }}</el-descriptions-item>
              <el-descriptions-item label="突破 / 布林带"
                >{{ strategyDetail.rules.breakout_period }} /
                {{ strategyDetail.rules.bollinger_period }}（{{
                  strategyDetail.rules.bollinger_std
                }}
                倍）</el-descriptions-item
              >
              <el-descriptions-item label="过滤因子"
                >成交量 {{ strategyDetail.rules.volume_ratio }} 倍 · ATR 上限
                {{ strategyDetail.rules.atr_pct_max }}% · ADX 下限
                {{ strategyDetail.rules.adx_min }}（0
                为关闭）</el-descriptions-item
              >
            </el-descriptions>
            <el-alert
              v-if="strategyDetail.risk_halted"
              title="已触发最大回撤限制并暂停，请回到研究页面评估新配置。"
              type="warning"
              :closable="false"
              class="mb"
            />
            <StrategyCurve :strategy-id="strategyDetail.id" :token="token" />
            <p class="panel-note">
              展示最近 1000 个 K
              线周期的权益记录；最大回撤按引擎每次轮询计算并保留全程统计，同一根
              K
              线内的回撤可能不显示在曲线上。暂停后停止新开仓和信号反手，已有持仓仍执行止损、止盈及最大回撤保护。
            </p>
          </section>
          <section v-if="strategies.some((s) => s.last_error)" class="panel">
            <div class="panel-heading"><h3>策略异常</h3></div>
            <el-alert
              v-for="strategy in strategies.filter((s) => s.last_error)"
              :key="strategy.id"
              :title="`${strategy.name}：${strategy.last_error}`"
              type="error"
              :closable="false"
              class="mb"
            />
          </section>
          <section class="panel">
            <div class="panel-heading"><h3>操作与运行记录</h3></div>
            <el-table :data="events"
              ><el-table-column label="时间" width="200"
                ><template #default="{ row }">{{
                  time(row.created_at)
                }}</template></el-table-column
              ><el-table-column label="级别" width="110"
                ><template #default="{ row }"
                  ><el-tag
                    :type="
                      row.level === 'error'
                        ? 'danger'
                        : row.level === 'warning'
                          ? 'warning'
                          : 'info'
                    "
                    >{{ row.level }}</el-tag
                  ></template
                ></el-table-column
              ><el-table-column label="记录" prop="message"
            /></el-table>
          </section>
        </template>

        <template v-if="page === 'research'">
          <OptimizationPanel
            :base="researchForm"
            :token="token"
            @inspect="(id) => openJob(id)"
            @promote="(job) => promoteJob(job)"
          />
          <div class="research-grid">
            <section class="panel">
              <div class="panel-heading">
                <div>
                  <h3>新建回测</h3>
                  <span>策略模板 · 固定数量 · 下一根开盘成交</span>
                </div>
              </div>
              <el-form label-position="top" class="research-form"
                ><RuleFields v-model="researchForm.rules" />
                <div class="form-grid">
                  <el-form-item label="数据范围"
                    ><el-select v-model="researchRange"
                      ><el-option label="最近若干根" value="recent" /><el-option
                        label="历史起止日期"
                        value="dates" /></el-select
                  ></el-form-item>
                  <el-form-item label="评估用途"
                    ><el-select v-model="researchForm.evaluation_phase"
                      ><el-option
                        label="研究：选择参数"
                        value="research" /><el-option
                        label="验证：未参与选参的新区间"
                        value="validation" /></el-select
                  ></el-form-item>
                  <el-form-item
                    v-if="researchRange === 'dates'"
                    label="历史区间（本地时区，结束日期不包含）"
                    class="full-span"
                    ><el-date-picker
                      v-model="researchDates"
                      type="daterange"
                      start-placeholder="开始日期"
                      end-placeholder="结束日期"
                      :disabled-date="
                        (date: Date) => date.getTime() > Date.now()
                      "
                  /></el-form-item>
                  <el-form-item label="交易所"
                    ><el-select
                      v-model="researchForm.exchange"
                      @change="changeExchange(researchForm)"
                      ><el-option label="Bybit" value="bybit" /><el-option
                        label="Binance"
                        value="binance" /></el-select></el-form-item
                  ><el-form-item label="交易对"
                    ><el-select v-model="researchForm.symbol" filterable
                      ><el-option
                        v-for="instrument in symbolsFor(researchForm.exchange)"
                        :key="instrument.symbol"
                        :label="instrument.symbol"
                        :value="instrument.symbol" /></el-select></el-form-item
                  ><el-form-item label="K 线周期"
                    ><el-select v-model="researchForm.interval"
                      ><el-option
                        v-for="i in intervals"
                        :key="i.value"
                        :label="i.label"
                        :value="i.value" /></el-select></el-form-item
                  ><el-form-item label="样本根数"
                    ><el-input-number
                      v-model="researchForm.limit"
                      :min="100"
                      :max="1000"
                      :disabled="researchRange === 'dates'"
                      :step="100" /></el-form-item
                  ><el-form-item label="初始资金（USDT）"
                    ><el-input-number
                      v-model="researchForm.initial_equity"
                      :min="1"
                      :max="100000000"
                      :step="1000" /></el-form-item
                  ><el-form-item label="每次交易数量"
                    ><el-input-number
                      v-model="researchForm.quantity"
                      :min="0.000001"
                      :max="1000"
                      :precision="6"
                      :step="0.001" /></el-form-item
                  ><el-form-item label="最大持仓名义金额（USDT）"
                    ><el-input-number
                      v-model="researchForm.max_notional"
                      :min="1"
                      :max="100000000" /></el-form-item
                  ><el-form-item label="单边费率（0.00055 = 0.055%）"
                    ><el-input-number
                      v-model="researchForm.fee_rate"
                      :min="0"
                      :max="0.1"
                      :precision="5"
                      :step="0.0001" /></el-form-item
                  ><el-form-item label="单边滑点（基点）"
                    ><el-input-number
                      v-model="researchForm.slippage_bps"
                      :min="0"
                      :max="1000"
                  /></el-form-item>
                </div>
                <el-checkbox
                  v-model="batchEnabled"
                  :disabled="
                    !['ema_rsi', 'ema_cross'].includes(
                      researchForm.rules.strategy_type,
                    )
                  "
                  >多组 EMA 参数对比（最多 9 组）</el-checkbox
                >
                <div v-if="batchEnabled" class="form-grid">
                  <el-form-item label="快 EMA 候选（逗号分隔）"
                    ><el-input
                      v-model="fastVariants"
                      placeholder="10,20,30" /></el-form-item
                  ><el-form-item label="慢 EMA 候选（逗号分隔）"
                    ><el-input v-model="slowVariants" placeholder="40,60,90"
                  /></el-form-item>
                </div>
                <p class="panel-note">
                  日期区间最多 100000 根、730
                  天，自动补齐指标预热。参数对比使用相同时间区间；“验证”标签只是记录用途，需自行选择未参与选参的新区间。
                </p>
                <el-button
                  type="primary"
                  :loading="submitting"
                  @click="queueBacktest"
                  >开始回测 <el-icon><ArrowRight /></el-icon></el-button
              ></el-form>
            </section>
            <section class="panel">
              <div class="panel-heading">
                <h3>历史回测</h3>
                <span>最近 50 次</span>
              </div>
              <el-empty
                v-if="!jobs.length"
                description="提交回测后在这里查看"
                :image-size="70"
              /><button
                v-for="job in jobs"
                :key="job.id"
                class="job-row"
                :class="{ selected: selectedJob?.id === job.id }"
                @click="openJob(job.id)"
              >
                <div>
                  <strong>#{{ job.id }} · {{ job.parameters.symbol }}</strong
                  ><small>{{ time(job.created_at) }}</small>
                </div>
                <el-tag
                  size="small"
                  :type="
                    job.status === 'completed'
                      ? 'success'
                      : job.status === 'failed'
                        ? 'danger'
                        : 'info'
                  "
                  >{{ statusLabel(job.status) }}</el-tag
                >
              </button>
            </section>
          </div>
          <section class="panel">
            <div class="panel-heading">
              <div>
                <h3>回测结果对比</h3>
                <span
                  >查看收益、含浮亏回撤和成交次数，再到新时间区间验证；不同币种与资金配置需分别比较。</span
                >
              </div>
              <el-button v-if="comparisonBatch" @click="comparisonBatch = ''"
                >显示全部结果</el-button
              >
            </div>
            <el-table
              :data="comparisonJobs"
              empty-text="回测完成后显示结果"
              max-height="460"
            >
              <el-table-column label="回测" width="90"
                ><template #default="{ row }"
                  ><el-button link @click="openJob(row.id)"
                    >#{{ row.id }}</el-button
                  ></template
                ></el-table-column
              >
              <el-table-column label="币种 / 模板 / 参数" min-width="240"
                ><template #default="{ row }"
                  >{{ row.parameters.symbol }} ·
                  {{
                    templateName(
                      row.parameters.rules?.strategy_type ?? "ema_rsi",
                    )
                  }}
                  <div class="table-sub">
                    EMA {{ row.parameters.rules?.fast ?? 20 }}/{{
                      row.parameters.rules?.slow ?? 60
                    }}
                    · RSI {{ row.parameters.rules?.rsi_period ?? 14 }} ·
                    {{
                      row.parameters.evaluation_phase === "validation"
                        ? "验证"
                        : "研究"
                    }}
                  </div></template
                ></el-table-column
              >
              <el-table-column
                label="收益率"
                prop="summary.total_return"
                sortable
                width="130"
                ><template #default="{ row }"
                  >{{ (row.summary.total_return * 100).toFixed(3) }}%</template
                ></el-table-column
              >
              <el-table-column
                label="最大回撤"
                prop="summary.max_drawdown"
                sortable
                width="130"
                ><template #default="{ row }"
                  >{{ (row.summary.max_drawdown * 100).toFixed(3) }}%</template
                ></el-table-column
              >
              <el-table-column label="成交 / 胜率" min-width="150"
                ><template #default="{ row }"
                  >{{ row.summary.trades }} /
                  {{ (row.summary.win_rate * 100).toFixed(1) }}%</template
                ></el-table-column
              >
              <el-table-column label="操作" min-width="190"
                ><template #default="{ row }"
                  ><el-button link @click="openJob(row.id)">查看</el-button
                  ><el-button
                    link
                    type="primary"
                    @click="promoteJob(row as Job)"
                    >创建模拟策略</el-button
                  ></template
                ></el-table-column
              >
            </el-table>
          </section>
          <section v-if="selectedJob && !result" class="panel">
            <el-empty
              :description="
                selectedJob.error ||
                (selectedJob.status === 'running'
                  ? '回测正在计算中…'
                  : '回测排队中，请确认回测进程在线')
              "
            />
          </section>
          <template v-if="result"
            ><section class="panel">
              <div class="panel-heading">
                <div>
                  <h3>回测配置 · #{{ selectedJob?.id }}</h3>
                  <span
                    >{{
                      templateName(
                        selectedJob?.parameters.rules?.strategy_type ??
                          "ema_rsi",
                      )
                    }}
                    ·
                    {{
                      selectedJob?.parameters.evaluation_phase === "validation"
                        ? "验证区间"
                        : "研究区间"
                    }}</span
                  >
                </div>
                <el-button type="primary" @click="promoteJob(selectedJob!)"
                  >用这次回测创建模拟策略</el-button
                >
              </div>
              <el-descriptions :column="2" border>
                <el-descriptions-item label="交易所 / 币种 / 周期"
                  >{{ selectedJob?.parameters.exchange }} /
                  {{ selectedJob?.parameters.symbol }} /
                  {{
                    selectedJob?.parameters.interval === "D"
                      ? "1 天"
                      : `${selectedJob?.parameters.interval} 分钟`
                  }}</el-descriptions-item
                >
                <el-descriptions-item label="EMA / RSI"
                  >{{ selectedJob?.parameters.rules?.fast ?? 20 }} /
                  {{ selectedJob?.parameters.rules?.slow ?? 60 }} · RSI
                  {{
                    selectedJob?.parameters.rules?.rsi_period ?? 14
                  }}</el-descriptions-item
                >
                <el-descriptions-item label="初始资金 / 数量"
                  >{{ money(selectedJob?.parameters.initial_equity) }} USDT /
                  {{ selectedJob?.parameters.quantity }}</el-descriptions-item
                >
                <el-descriptions-item label="费率 / 滑点"
                  >{{
                    ((selectedJob?.parameters.fee_rate ?? 0) * 100).toFixed(3)
                  }}% /
                  {{ selectedJob?.parameters.slippage_bps }}
                  基点</el-descriptions-item
                >
                <el-descriptions-item label="止损 / 止盈 / 回撤暂停"
                  >{{ selectedJob?.parameters.rules?.stop_loss_pct ?? 0 }}% /
                  {{ selectedJob?.parameters.rules?.take_profit_pct ?? 0 }}% /
                  {{
                    selectedJob?.parameters.rules?.max_drawdown_pct ?? 0
                  }}%</el-descriptions-item
                >
                <el-descriptions-item label="名义金额上限"
                  >{{
                    money(selectedJob?.parameters.max_notional ?? 1000)
                  }}
                  USDT</el-descriptions-item
                >
              </el-descriptions>
              <p class="panel-note">
                {{
                  result.dataset
                    ? `数据 ${time(result.dataset.start_at)} 至 ${time(result.dataset.end_at)}，包含指标预热；指纹 ${result.dataset.sha256.slice(0, 12)}`
                    : "旧版回测记录"
                }}
                · 风控拒绝开仓 {{ result.rejected_entries ?? 0 }} 次 ·
                {{
                  result.risk_halted
                    ? "达到回撤限制，已停止开仓"
                    : "未触发回撤暂停"
                }}
              </p>
            </section>
            <div class="metrics-grid">
              <div class="metric-card">
                <span>净收益率</span
                ><strong
                  :class="
                    result.metrics.total_return >= 0 ? 'positive' : 'negative'
                  "
                  >{{ (result.metrics.total_return * 100).toFixed(3) }}%</strong
                >
                <div>
                  {{ result.source === "demo" ? "演示行情" : "交易所行情"
                  }}<span> · {{ result.bars }} 根样本</span>
                </div>
              </div>
              <div class="metric-card">
                <span>{{
                  result.equity_kind === "marked"
                    ? "含浮盈亏权益最大回撤"
                    : "已实现权益最大回撤"
                }}</span
                ><strong
                  >{{ (result.metrics.max_drawdown * 100).toFixed(3) }}%</strong
                >
                <div>
                  <span>{{
                    result.equity_kind === "marked"
                      ? "按每根收盘价估值"
                      : "旧版记录：未包含持仓浮动盈亏"
                  }}</span>
                </div>
              </div>
              <div class="metric-card">
                <span>成交 / 胜率</span
                ><strong
                  >{{ result.metrics.trades
                  }}<small
                    >/ {{ (result.metrics.win_rate * 100).toFixed(0) }}%</small
                  ></strong
                >
              </div>
              <div class="metric-card">
                <span>期末权益 <small>USDT</small></span
                ><strong>{{ money(result.metrics.final_equity) }}</strong>
              </div>
            </div>
            <section class="panel">
              <div class="panel-heading">
                <h3>
                  {{
                    result.equity_kind === "marked"
                      ? "回测权益（含浮动盈亏）"
                      : "回测已实现权益"
                  }}
                  · #{{ selectedJob?.id }}
                </h3>
              </div>
              <ChartPanel :option="equityOption" />
              <p class="panel-note">
                {{
                  result.equity_kind === "marked"
                    ? "每根收盘估值，风控按收盘检查并在下一根开盘成交。"
                    : "旧版已实现权益：平仓时更新，未计入持仓浮动盈亏。"
                }}末尾强制平仓，包含手续费与滑点，未模拟盘中触价、资金费率、杠杆保证金与强平。
              </p>
            </section>
            <section class="panel">
              <div class="panel-heading"><h3>回测成交</h3></div>
              <ResearchDiagnostics
                :robustness="result.robustness"
                :quality="result.data_quality"
                :factors="result.factor_analysis"
              />
              <el-table :data="result.trades" max-height="420"
                ><el-table-column
                  label="方向"
                  prop="side"
                  width="100"
                /><el-table-column label="入场时间" min-width="200"
                  ><template #default="{ row }">{{
                    time(row.entry_time.replace(" ", "T"))
                  }}</template></el-table-column
                ><el-table-column label="入场价"
                  ><template #default="{ row }">{{
                    money(row.entry_price)
                  }}</template></el-table-column
                ><el-table-column label="出场价"
                  ><template #default="{ row }">{{
                    money(row.exit_price)
                  }}</template></el-table-column
                ><el-table-column
                  label="数量"
                  prop="quantity"
                /><el-table-column label="净盈亏"
                  ><template #default="{ row }"
                    ><span :class="row.pnl >= 0 ? 'positive' : 'negative'">{{
                      signed(row.pnl)
                    }}</span></template
                  ></el-table-column
                ></el-table
              >
            </section></template
          >
        </template>

        <template v-if="page === 'orders'">
          <section class="panel">
            <div class="panel-heading">
              <h3>模拟持仓</h3>
              <el-tag type="info">本地模拟账户</el-tag>
            </div>
            <el-table :data="positions" empty-text="当前没有模拟持仓"
              ><el-table-column
                prop="strategy_name"
                label="策略"
                min-width="170"
              /><el-table-column prop="symbol" label="交易对" /><el-table-column
                prop="side"
                label="方向"
              /><el-table-column prop="quantity" label="数量" /><el-table-column
                label="入场价"
                ><template #default="{ row }">{{
                  money(row.entry_price)
                }}</template></el-table-column
              ><el-table-column label="标记价"
                ><template #default="{ row }">{{
                  money(row.mark_price)
                }}</template></el-table-column
              ><el-table-column label="浮动盈亏"
                ><template #default="{ row }"
                  ><span
                    :class="
                      Number(row.unrealized_pnl) >= 0 ? 'positive' : 'negative'
                    "
                    >{{ signed(row.unrealized_pnl) }}</span
                  ></template
                ></el-table-column
              ></el-table
            >
          </section>
          <section class="panel">
            <div class="panel-heading">
              <div>
                <h3>模拟成交记录</h3>
                <span>最近 100 笔 · 开仓费用立即扣除</span>
              </div>
              <el-button :disabled="!orders.length" @click="downloadOrders"
                >导出 CSV</el-button
              >
            </div>
            <el-table
              :data="orders"
              empty-text="策略触发成交后将在这里显示"
              max-height="600"
              ><el-table-column label="时间" min-width="190"
                ><template #default="{ row }">{{
                  time(row.created_at)
                }}</template></el-table-column
              ><el-table-column
                label="策略 ID"
                prop="strategy_id"
                width="90"
              /><el-table-column
                prop="symbol"
                label="交易对"
                width="120"
              /><el-table-column label="动作" width="110"
                ><template #default="{ row }"
                  >{{ row.action === "open" ? "开仓" : "平仓" }} /
                  {{ row.side }}</template
                ></el-table-column
              ><el-table-column prop="quantity" label="数量" /><el-table-column
                label="成交价"
                ><template #default="{ row }">{{
                  money(row.price)
                }}</template></el-table-column
              ><el-table-column label="手续费"
                ><template #default="{ row }">{{
                  money(row.fee, 5)
                }}</template></el-table-column
              ><el-table-column label="本笔净现金变动" min-width="150"
                ><template #default="{ row }"
                  ><span
                    :class="
                      Number(row.realized_pnl) >= 0 ? 'positive' : 'negative'
                    "
                    >{{ signed(row.realized_pnl) }}</span
                  ></template
                ></el-table-column
              ></el-table
            >
          </section>
        </template>

        <template v-if="page === 'account'">
          <BybitEnvironmentPanel
            :token="token"
            @pending="account = undefined"
          />
          <section class="panel account-panel">
            <div class="panel-heading">
              <div>
                <h3>交易所连接</h3>
                <span>余额仅在点击查询后读取</span>
              </div>
              <el-tag type="info">账户查询</el-tag>
            </div>
            <div class="connection-card">
              <span class="exchange-logo">{{
                accountExchange === "bybit" ? "B" : "BN"
              }}</span>
              <div>
                <h3>
                  {{ accountExchange === "bybit" ? "Bybit" : "Binance USD-M" }}
                </h3>
                <p>API 凭据与主网 / 测试网环境由服务端配置</p>
              </div>
              <el-select v-model="accountExchange" @change="account = undefined"
                ><el-option label="Bybit" value="bybit" /><el-option
                  label="Binance"
                  value="binance" /></el-select
              ><el-button
                type="primary"
                :loading="accountBusy"
                @click="readAccount"
                >查询余额</el-button
              >
            </div>
            <el-alert
              v-if="account?.message"
              :title="account.message"
              type="info"
              :closable="false"
            /><template v-if="account?.connected"
              ><el-alert
                :title="`余额查询成功 · ${account.testnet ? '测试网' : '主网'}`"
                type="success"
                :closable="false"
                class="mb" /><el-table :data="account.balances"
                ><el-table-column prop="coin" label="资产" /><el-table-column
                  prop="balance"
                  label="钱包余额" /></el-table
            ></template>
            <div class="account-note">
              <h3>账户与模拟资金分别管理</h3>
              <p>
                这里显示交易所钱包余额。策略页的资金、持仓和订单属于本地模拟账户，互相独立。
              </p>
              <p>
                主网密钥必须配合主网环境配置，测试网密钥必须配合测试网环境配置。
              </p>
            </div>
          </section>
        </template>
        <TestnetTradingPanel
          v-if="page === 'trading'"
          :token="token"
          :source-strategy-id="researchStrategyId"
        />
        <footer>
          QuantPilot
          <span
            >私人量化工作台 · {{ demo ? "演示行情" : "交易所行情" }} / 本地模拟
            / 交易助手</span
          ><span>状态每 5 秒更新 · 行情手动刷新</span>
        </footer>
      </div>
    </main>
    <el-dialog
      v-model="strategyDialog"
      title="创建模拟策略"
      width="560px"
      :close-on-click-modal="false"
      ><el-form label-position="top"
        ><el-form-item label="策略名称"
          ><el-input
            v-model="strategyForm.name"
            maxlength="80"
            placeholder="例如：BTC 15 分钟趋势策略"
        /></el-form-item>
        <div class="form-grid">
          <el-form-item label="交易所"
            ><el-select
              v-model="strategyForm.exchange"
              @change="changeExchange(strategyForm)"
              ><el-option label="Bybit" value="bybit" /><el-option
                label="Binance"
                value="binance" /></el-select></el-form-item
          ><el-form-item label="交易对"
            ><el-select v-model="strategyForm.symbol" filterable
              ><el-option
                v-for="instrument in symbolsFor(strategyForm.exchange)"
                :key="instrument.symbol"
                :label="instrument.symbol"
                :value="instrument.symbol" /></el-select></el-form-item
          ><el-form-item label="周期"
            ><el-select v-model="strategyForm.interval"
              ><el-option
                v-for="i in intervals"
                :key="i.value"
                :label="i.label"
                :value="i.value" /></el-select></el-form-item
          ><el-form-item label="每次交易数量"
            ><el-input-number
              v-model="strategyForm.quantity"
              :min="0.000001"
              :max="1000"
              :precision="6"
              :step="0.001" /></el-form-item
          ><el-form-item label="初始模拟资金（USDT）"
            ><el-input-number
              v-model="strategyForm.initial_equity"
              :min="1"
              :max="100000000"
              :step="1000" /></el-form-item
          ><el-form-item label="最大持仓名义金额（USDT）"
            ><el-input-number
              v-model="strategyForm.max_notional"
              :min="1"
              :max="100000000"
              :step="100"
          /></el-form-item>
        </div>
        <RuleFields v-model="strategyForm.rules" />
        <div class="form-grid">
          <el-form-item label="单边费率"
            ><el-input-number
              v-model="strategyForm.fee_rate"
              :min="0"
              :max="0.1"
              :precision="5"
              :step="0.0001" /></el-form-item
          ><el-form-item label="单边滑点（基点）"
            ><el-input-number
              v-model="strategyForm.slippage_bps"
              :min="0"
              :max="1000"
          /></el-form-item>
        </div>
        <el-alert
          title="参数和风控保存为独立配置快照，创建后为暂停状态，仅执行模拟交易。"
          type="info"
          :closable="false" /></el-form
      ><template #footer
        ><el-button @click="strategyDialog = false">取消</el-button
        ><el-button type="primary" :loading="submitting" @click="createStrategy"
          >创建策略</el-button
        ></template
      ></el-dialog
    >
  </div>
</template>
