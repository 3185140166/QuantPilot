<script setup lang="ts">
import { ElAlert } from "element-plus/es/components/alert/index.mjs";
import { ElButton } from "element-plus/es/components/button/index.mjs";
import { ElCheckbox } from "element-plus/es/components/checkbox/index.mjs";
import { ElCollapse } from "element-plus/es/components/collapse/index.mjs";
import { ElCollapseItem } from "element-plus/es/components/collapse/index.mjs";
import { ElOption } from "element-plus/es/components/select/index.mjs";
import { ElRadio } from "element-plus/es/components/radio/index.mjs";
import { ElRadioGroup } from "element-plus/es/components/radio/index.mjs";
import { ElSelect } from "element-plus/es/components/select/index.mjs";
import { ElTable } from "element-plus/es/components/table/index.mjs";
import { ElTableColumn } from "element-plus/es/components/table/index.mjs";
import { computed, onMounted, onBeforeUnmount, watch, ref } from "vue";
import { ElMessage } from "element-plus/es/components/message/index.mjs";
import { ApiError, request } from "../api";
import StrategyEvidence, { type Qualification } from "./StrategyEvidence.vue";
const props = defineProps<{
  token: string;
  disabled: boolean;
  origin?: Record<string, unknown> | null;
  refreshVersion?: number;
}>();
const emit = defineEmits<{ applied: [] }>();
function sourceName(value: unknown) {
  return (value as { name?: string } | null)?.name ?? "未记录";
}
type Job = {
  id: number;
  symbol: string;
  interval: string;
  strategy_type: string;
  market_environment: string;
  source_strategy?: { id: number; name: string } | null;
};
type Preview = {
  qualification?: Qualification;
  job_id: number;
  fingerprint: string;
  configuration: {
    research_guard?: { enabled: boolean };
    symbols: string[];
    interval: string;
    budget: number;
    rules: Record<string, unknown>;
  };
  metrics: Record<string, number>;
  market_environment: string;
  warnings: string[];
  risk_sizing?: {
    budget: number;
    risk_per_trade_pct: number;
    portfolio_risk_pct: number;
    leverage: number;
    daily_loss_pct: number;
    max_drawdown_pct: number;
    constraints: Record<string, number>;
  } | null;
  symbols_mode?: "replace" | "keep";
  differences?: {
    field: string;
    before: unknown;
    after: unknown;
    research: unknown;
    changed: boolean;
  }[];
  unvalidated_symbols?: string[];
  risk_summary?: { policy: string } | null;
};
const jobs = ref<Job[]>([]);
const selected = ref<number>();
const candidate = ref<Preview>();
const acknowledged = ref(false);
const symbolsMode = ref<"replace" | "keep">("replace");
const unvalidatedAcknowledged = ref(false);
const riskFields = new Set([
  "budget",
  "risk_per_trade_pct",
  "portfolio_risk_pct",
  "daily_loss_pct",
  "max_drawdown_pct",
  "max_positions",
  "leverage",
  "max_correlation",
  "symbols",
  "interval",
  "fee_rate",
  "slippage_bps",
]);
const importantDifferences = computed(
  () =>
    candidate.value?.differences?.filter(
      (row) => row.changed || riskFields.has(row.field),
    ) ?? [],
);
const unvalidated = computed(() => candidate.value?.unvalidated_symbols ?? []);
const canApply = computed(
  () =>
    acknowledged.value &&
    (!unvalidated.value.length || unvalidatedAcknowledged.value) &&
    (!candidate.value?.configuration.research_guard?.enabled ||
      candidate.value?.qualification?.eligible === true),
);
const busy = ref(false);
const error = ref("");
const listError = ref("");
let timer: number | undefined;
let disposed = false;
let fetching = false;
const labels: Record<string, string> = {
  selection: "动态合约筛选",
  research_guard: "研究准入限制",
  monitoring: "策略运行监测",
  cash_reserve_pct: "现金保留（预算%）",
  hourly_trend_filter: "1小时方向过滤",
  hourly_min_gap_pct: "1小时均线间距下限（%）",
  strategy_type: "策略",
  fast: "快均线周期",
  slow: "慢均线周期",
  stop_loss_pct: "止损（%）",
  take_profit_pct: "止盈（%）",
  rsi_period: "RSI 周期",
  rsi_lower: "RSI 下限",
  rsi_upper: "RSI 上限",
  breakout_period: "突破周期",
  bollinger_period: "布林带周期",
  bollinger_std: "布林带标准差",
  volume_ratio: "成交量过滤",
  atr_pct_max: "ATR 上限（%）",
  adx_min: "ADX 下限",
  version: "规则版本",
  symbols: "扫描币种",
  interval: "信号周期",
  budget: "共享预算（USDT）",
  risk_per_trade_pct: "单笔风险（%）",
  portfolio_risk_pct: "组合风险（%）",
  daily_loss_pct: "日亏损限额（预算%）",
  max_drawdown_pct: "回撤限额（预算%）",
  max_positions: "最多持仓币种",
  leverage: "杠杆倍数",
  max_correlation: "持仓相关性上限",
  fee_rate: "手续费率",
  slippage_bps: "滑点（基点）",
};
function fieldName(field: string) {
  const key = field.replace(/^rules\./, "");
  return field === "rules.max_drawdown_pct"
    ? "模板回撤设置（%）"
    : (labels[key] ?? key);
}
function display(value: unknown, field: string) {
  if (value === undefined || value === null) return "—";
  if (Array.isArray(value)) return value.join("、");
  if (typeof value === "object") {
    const item = value as Record<string, unknown>;
    const state = item.enabled ? "已启用" : "未启用";
    if (field === "selection")
      return `${state} · ${item.ranking === "trend_quality" ? "趋势与波动比值" : "成交额优先"} · 前 ${item.top_n} 个 · 成交额下限 ${item.min_turnover} · ATR上限 ${item.max_atr_pct}%`;
    if (field === "research_guard")
      return `${state} · 每窗口至少 ${item.min_trades} 笔 · 至少 ${item.min_validation_windows ?? 1} 个窗口`;
    if (field === "monitoring")
      return `${state} · 连续亏损 ${item.consecutive_losses} 笔 · 不利成交偏差上限 ${item.max_adverse_slippage_bps} 基点`;
    return JSON.stringify(value);
  }
  if (field === "interval")
    return (
      (
        {
          "15": "15分钟",
          "30": "30分钟",
          "60": "1小时",
          "240": "4小时",
          D: "每天",
        } as Record<string, string>
      )[String(value)] ?? String(value)
    );
  if (field === "fee_rate") return `${(Number(value) * 100).toFixed(4)}%`;
  return String(value);
}
async function list() {
  if (fetching || disposed) return;
  fetching = true;
  try {
    const next = await request<Job[]>("/trading/research", props.token);
    if (disposed) return;
    jobs.value = next;
    listError.value = "";
  } catch (err) {
    if (!disposed)
      listError.value =
        err instanceof ApiError && err.status === 404
          ? "当前后端尚未加载验证回测功能，请停止原启动终端并重启平台。"
          : err instanceof Error
            ? err.message
            : "读取验证回测失败";
  } finally {
    fetching = false;
  }
}
function clear() {
  candidate.value = undefined;
  acknowledged.value = false;
  unvalidatedAcknowledged.value = false;
}
async function preview() {
  if (selected.value === undefined) return;
  busy.value = true;
  error.value = "";
  clear();
  try {
    candidate.value = await request<Preview>(
      `/trading/research/${selected.value}/preview?symbols_mode=${symbolsMode.value}`,
      props.token,
    );
  } catch (err) {
    error.value = err instanceof Error ? err.message : "预览失败";
  } finally {
    busy.value = false;
  }
}
async function apply() {
  if (!candidate.value || !canApply.value) return;
  busy.value = true;
  error.value = "";
  try {
    await request(
      `/trading/research/${candidate.value.job_id}/apply`,
      props.token,
      {
        fingerprint: candidate.value.fingerprint,
        acknowledge_model_difference: acknowledged.value,
        symbols_mode: candidate.value.symbols_mode ?? symbolsMode.value,
        acknowledge_unvalidated_symbols: unvalidatedAcknowledged.value,
      },
    );
    ElMessage.success("研究规则已应用，交易助手保持暂停");
    emit("applied");
    clear();
  } catch (err) {
    error.value = err instanceof Error ? err.message : "应用失败";
  } finally {
    busy.value = false;
  }
}
watch(
  () => props.refreshVersion,
  () => void list(),
);
onMounted(() => {
  void list();
  timer = window.setInterval(() => void list(), 10000);
});
onBeforeUnmount(() => {
  disposed = true;
  window.clearInterval(timer);
});
</script>
<template>
  <section class="panel research-apply-panel">
    <div class="panel-heading">
      <h3>从验证回测应用研究参数</h3>
      <el-button link @click="list">刷新回测列表</el-button>
    </div>
    <el-alert
      v-if="listError"
      :title="listError"
      type="error"
      :closable="false"
    />
    <p v-if="origin" class="panel-note">
      <template v-if="origin.source_strategy"
        >来源模拟策略：{{
          sourceName(origin.source_strategy)
        }}（规则快照，不随模拟策略自动同步）。</template
      >
      当前来源：验证回测 #{{ origin.job_id }}，应用时间
      {{ origin.applied_at }}。{{
        origin.modified
          ? "配置之后已修改，需要重新核对来源参数。"
          : "应用后保持暂停，启动操作需单独执行。"
      }}
    </p>
    <el-alert
      v-if="error"
      :title="error"
      type="error"
      :closable="false"
      class="mb"
    />
    <el-select
      v-model="selected"
      placeholder="选择已完成的 Bybit 验证回测"
      :disabled="busy"
      @change="clear"
      style="width: 100%"
    >
      <el-option
        v-for="job in jobs"
        :key="job.id"
        :value="job.id"
        :label="`#${job.id} ${job.source_strategy ? job.source_strategy.name + ' · ' : ''}${job.symbol} · ${job.interval} · ${job.strategy_type}`"
      />
    </el-select>
    <p v-if="!jobs.length && !listError" class="panel-note">
      暂无可用的已完成验证回测。上方默认创建的是“研究”任务，不会直接出现在这里；请在研究结果中为选定周期“创建后续验证”。验证完成且包含有效止损后，这里每10秒自动刷新。演示回测、失败任务及未通过约束的优化候选不可应用。
    </p>
    <p class="panel-note">
      应用时规则和周期会更新；预算、杠杆及账户风险限制保持当前设置。请选择扫描币种的处理方式，切换后需要重新预览。
    </p>
    <p class="panel-note">
      先选择已完成的验证回测，再选择币种范围，最后点击下方“预览研究参数”。预览只展示差异，不保存配置、不下单。
    </p>
    <el-radio-group v-model="symbolsMode" :disabled="busy" @change="clear">
      <el-radio value="keep">保留当前扫描币种</el-radio>
      <el-radio value="replace">仅使用回测币种</el-radio>
    </el-radio-group>
    <div class="research-preview-actions">
      <el-button
        type="primary"
        :disabled="selected === undefined || busy"
        :loading="busy"
        @click="preview"
        >预览研究参数</el-button
      >
      <span class="panel-note">{{
        selected === undefined ? "请先选择验证回测" : "下一步：查看将修改的参数"
      }}</span>
    </div>
    <p v-if="disabled" class="panel-note">
      当前暂不能应用：需要当前环境执行就绪、助手已暂停、空仓且没有待处理订单。仍可选择结果并预览。
    </p>
    <template v-if="candidate">
      <h4>应用前后对比</h4>
      <p class="panel-note">
        “回测设置”用于对照来源，不会自动覆盖保留的风险参数。发生变化的项目已标记；账户保护按共享预算计算日亏损和回撤金额。
      </p>
      <el-table :data="importantDifferences" class="configuration-differences">
        <el-table-column label="项目" min-width="180"
          ><template #default="{ row }"
            >{{ fieldName(row.field)
            }}{{ row.changed ? "（将修改）" : "（保留）" }}</template
          ></el-table-column
        >
        <el-table-column label="应用前" min-width="160"
          ><template #default="{ row }">{{
            display(row.before, row.field)
          }}</template></el-table-column
        >
        <el-table-column label="应用后" min-width="160"
          ><template #default="{ row }">{{
            display(row.after, row.field)
          }}</template></el-table-column
        >
        <el-table-column label="回测设置" min-width="160"
          ><template #default="{ row }">{{
            display(row.research, row.field)
          }}</template></el-table-column
        >
      </el-table>
      <el-collapse
        ><el-collapse-item title="查看全部参数对比">
          <el-table :data="candidate.differences ?? []">
            <el-table-column label="项目" min-width="180"
              ><template #default="{ row }">{{
                fieldName(row.field)
              }}</template></el-table-column
            >
            <el-table-column label="应用前" min-width="160"
              ><template #default="{ row }">{{
                display(row.before, row.field)
              }}</template></el-table-column
            >
            <el-table-column label="应用后" min-width="160"
              ><template #default="{ row }">{{
                display(row.after, row.field)
              }}</template></el-table-column
            >
          </el-table>
        </el-collapse-item></el-collapse
      >
      <p v-if="candidate.risk_sizing" class="panel-note">
        回测资金管理：预算 {{ candidate.risk_sizing.budget }} USDT，单笔风险
        {{ candidate.risk_sizing.risk_per_trade_pct }}%，组合风险
        {{ candidate.risk_sizing.portfolio_risk_pct }}%，杠杆
        {{ candidate.risk_sizing.leverage }} 倍。
        当前资金管理仍保留，请核对两者差异；合约限制快照：最小数量
        {{ candidate.risk_sizing.constraints.min_qty }}，数量步长
        {{ candidate.risk_sizing.constraints.qty_step }}，最小订单金额
        {{ candidate.risk_sizing.constraints.min_notional }} USDT。
      </p>
      <p class="panel-note">
        币种：{{ candidate.configuration.symbols.join("、") }}；周期：{{
          candidate.configuration.interval
        }}；保留共享预算：{{
          candidate.configuration.budget
        }}
        USDT；数据环境：{{ candidate.market_environment }}。
      </p>
      <el-table
        :data="
          Object.entries(candidate.configuration.rules).map(([key, value]) => ({
            name: labels[key] ?? key,
            value,
          }))
        "
        ><el-table-column prop="name" label="参数" /><el-table-column
          prop="value"
          label="值"
      /></el-table>
      <p class="panel-note">
        回测收益：{{
          ((candidate.metrics.total_return ?? 0) * 100).toFixed(2)
        }}%；最大回撤：{{
          (Math.abs(candidate.metrics.max_drawdown ?? 0) * 100).toFixed(2)
        }}%；交易次数：{{ candidate.metrics.trades ?? 0 }}。
      </p>
      <StrategyEvidence :qualification="candidate.qualification" />
      <p
        v-for="warning in candidate.warnings"
        :key="warning"
        class="panel-note"
      >
        {{ warning }}
      </p>
      <el-checkbox v-model="acknowledged"
        >我已核对应用前后差异和风险设置</el-checkbox
      >
      <div v-if="unvalidated.length">
        <p class="panel-note">
          此回测只验证了来源币种。{{ unvalidated.join("、") }}
          尚未通过这项回测验证，保留时也会使用相同规则。
        </p>
        <el-checkbox v-model="unvalidatedAcknowledged"
          >我确认保留这些尚未验证的币种</el-checkbox
        >
      </div>
      <p>
        <el-button
          type="primary"
          :disabled="disabled || !canApply || busy"
          :loading="busy"
          @click="apply"
          >应用参数并保持暂停</el-button
        >
      </p>
    </template>
  </section>
</template>
<style scoped>
.el-checkbox {
  height: auto;
  align-items: flex-start;
  max-width: 100%;
}
.research-preview-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  margin: 20px 0;
  padding-top: 16px;
  border-top: 1px solid var(--el-border-color);
}
:deep(.el-checkbox__label) {
  white-space: normal;
  line-height: 1.6;
}
</style>
