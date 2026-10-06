<script setup lang="ts">
import ResearchDiagnostics from "./ResearchDiagnostics.vue";
import { ElAlert } from "element-plus/es/components/alert/index.mjs";
import { ElButton } from "element-plus/es/components/button/index.mjs";
import { ElCheckbox } from "element-plus/es/components/checkbox/index.mjs";
import { ElCollapse } from "element-plus/es/components/collapse/index.mjs";
import { ElCollapseItem } from "element-plus/es/components/collapse/index.mjs";
import { ElDatePicker } from "element-plus/es/components/date-picker/index.mjs";
import { ElForm } from "element-plus/es/components/form/index.mjs";
import { ElFormItem } from "element-plus/es/components/form/index.mjs";
import { ElInputNumber } from "element-plus/es/components/input-number/index.mjs";
import { ElOption } from "element-plus/es/components/select/index.mjs";
import { ElProgress } from "element-plus/es/components/progress/index.mjs";
import { ElSelect } from "element-plus/es/components/select/index.mjs";
import { ElTable } from "element-plus/es/components/table/index.mjs";
import { ElTableColumn } from "element-plus/es/components/table/index.mjs";
import { ElTag } from "element-plus/es/components/tag/index.mjs";
import {
  computed,
  onBeforeUnmount,
  onMounted,
  reactive,
  ref,
  watch,
} from "vue";
import { ElMessage } from "element-plus/es/components/message/index.mjs";
import { request } from "../api";
import type {
  Job,
  OptimizationCandidate,
  OptimizationJob,
  SearchDimension,
  StrategyRules,
} from "../types";

const props = defineProps<{
  token: string;
  base: {
    exchange: string;
    symbol: string;
    interval: string;
    rules: StrategyRules;
    quantity: number;
    initial_equity: number;
    max_notional: number;
    fee_rate: number;
    slippage_bps: number;
  };
}>();
const emit = defineEmits<{ inspect: [id: number]; promote: [job: Job] }>();
const dimensions = ref<SearchDimension[]>([]);
const dates = ref<[Date, Date]>();
const split = ref<Date>();
const settings = reactive({
  trials: 40,
  top_k: 5,
  seed: 42,
  objective: "return_drawdown",
  drawdown_penalty: 1,
  max_drawdown_pct: 10,
  min_trades: 5,
  validation_windows: 1,
  require_positive_validation: true,
});
const jobs = ref<OptimizationJob[]>([]);
const selected = ref<OptimizationJob>();
const busy = ref(false);
const loadingSpace = ref(false);
const error = ref("");
let timer: number | undefined;
let disposed = false;
let sequence = 0;
let refreshing = false;
const active = (status: string) =>
  ["pending", "running", "cancelling"].includes(status);
const statusName = (status: string) =>
  ({
    pending: "排队中",
    running: "搜索／验证中",
    cancelling: "等待取消",
    cancelled: "已取消",
    completed: "已完成",
    failed: "失败",
  })[status] ?? status;
const progress = computed(() =>
  selected.value
    ? Math.min(
        100,
        Math.round(
          (selected.value.completed_trials / selected.value.parameters.trials) *
            100,
        ),
      )
    : 0,
);
const percent = (value: number) => `${(value * 100).toFixed(3)}%`;
const stamp = (value: string) =>
  new Date(value).toLocaleString("zh-CN", { hour12: false });
const candidates = computed(() => selected.value?.result?.candidates ?? []);
const ruleSummary = (rules: StrategyRules) => {
  const template = rules.strategy_type;
  const main =
    template === "breakout"
      ? `突破 ${rules.breakout_period}`
      : template === "bollinger_reversion"
        ? `布林 ${rules.bollinger_period} / ${rules.bollinger_std}`
        : template === "rsi_reversion"
          ? `RSI ${rules.rsi_period} / ${rules.rsi_lower} / ${rules.rsi_upper}`
          : `EMA ${rules.fast} / ${rules.slow} · RSI ${rules.rsi_period}`;
  return `${main} · 量比 ${rules.volume_ratio} · ATR 上限 ${rules.atr_pct_max}% · ADX ${rules.adx_min} · 止损/止盈 ${rules.stop_loss_pct}%/${rules.take_profit_pct}%`;
};
watch(dates, (range) => {
  if (range) {
    const midpoint = new Date(
      range[0].getTime() + (range[1].getTime() - range[0].getTime()) * 0.7,
    );
    midpoint.setHours(0, 0, 0, 0);
    split.value = midpoint;
  }
});
watch(
  () => props.base.rules.strategy_type,
  async (template) => {
    const current = ++sequence;
    loadingSpace.value = true;
    try {
      const data = await request<SearchDimension[]>(
        `/optimization-space?template=${template}`,
        props.token,
      );
      if (!disposed && current === sequence) dimensions.value = data;
    } catch (err) {
      if (!disposed && current === sequence)
        error.value = err instanceof Error ? err.message : "参数范围加载失败";
    } finally {
      if (current === sequence) loadingSpace.value = false;
    }
  },
  { immediate: true },
);

async function refresh() {
  if (refreshing) return;
  refreshing = true;
  try {
    const list = await request<OptimizationJob[]>(
      "/optimizations",
      props.token,
    );
    if (disposed) return;
    jobs.value = list;
    const id = selected.value?.id ?? list[0]?.id;
    if (id !== undefined) {
      const detail = await request<OptimizationJob>(
        `/optimizations/${id}`,
        props.token,
      );
      if (!disposed && (!selected.value || selected.value.id === id))
        selected.value = detail;
    }
    error.value = "";
  } catch (err) {
    if (!disposed)
      error.value = err instanceof Error ? err.message : "优化状态获取失败";
  } finally {
    refreshing = false;
  }
}
async function choose(id: number) {
  selected.value = jobs.value.find((job) => job.id === id);
  await refresh();
}
async function start() {
  busy.value = true;
  try {
    if (!dates.value || !split.value)
      throw new Error("请选择历史区间和研究／验证切分日期");
    const ranges = Object.fromEntries(
      dimensions.value
        .filter((item) => item.enabled)
        .map((item) => [
          item.key,
          { low: item.low, high: item.high, step: item.step },
        ]),
    );
    if (!Object.keys(ranges).length || Object.keys(ranges).length > 10)
      throw new Error("请选择 1 至 10 个搜索参数");
    const job = await request<OptimizationJob>("/optimizations", props.token, {
      base: {
        ...props.base,
        evaluation_phase: "research",
        start_at: dates.value[0].toISOString(),
        end_at: dates.value[1].toISOString(),
      },
      split_at: split.value.toISOString(),
      ranges,
      ...settings,
    });
    selected.value = job;
    await refresh();
    ElMessage.success("自动优化已排队，研究与验证使用独立时间区间");
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : "创建优化任务失败");
  } finally {
    busy.value = false;
  }
}
async function cancel(job: OptimizationJob) {
  try {
    await request(`/optimizations/${job.id}/cancel`, props.token, {});
    await refresh();
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : "取消失败");
  }
}
function promote(candidate: OptimizationCandidate) {
  emit("promote", {
    id: candidate.backtest_id,
    status: "completed",
    created_at: "",
    parameters: {
      exchange: props.base.exchange,
      symbol: selected.value!.parameters.base.symbol,
      interval: props.base.interval,
    },
  });
}
onMounted(() => {
  void refresh();
  timer = window.setInterval(() => {
    if (!document.hidden) void refresh();
  }, 4000);
});
onBeforeUnmount(() => {
  disposed = true;
  sequence++;
  window.clearInterval(timer);
});
</script>

<template>
  <section class="panel optimization-panel">
    <div class="panel-heading">
      <div>
        <h3>自动选参 · 独立验证</h3>
        <span
          >{{ base.exchange.toUpperCase() }} / {{ base.symbol }} /
          {{ base.interval === "D" ? "1 天" : `${base.interval} 分钟` }} ·
          沿用下方策略模板、资金、费用和固定因子设置</span
        >
      </div>
      <el-button @click="refresh">刷新优化状态</el-button>
    </div>
    <el-alert
      v-if="error"
      :title="error"
      type="error"
      :closable="false"
      class="mb"
    />
    <el-collapse
      ><el-collapse-item title="配置搜索范围与验证条件" name="settings">
        <el-form label-position="top" class="optimization-form">
          <div class="form-grid">
            <el-form-item
              label="完整历史区间（本地时区，结束日期不包含）"
              class="full-span"
              ><el-date-picker
                v-model="dates"
                type="daterange"
                start-placeholder="优化开始日期"
                end-placeholder="优化结束日期"
                :disabled-date="(date: Date) => date.getTime() > Date.now()"
            /></el-form-item>
            <el-form-item label="研究／验证切分日期"
              ><el-date-picker
                v-model="split"
                type="date"
                placeholder="切分日期"
            /></el-form-item>
            <el-form-item label="搜索次数（前 10 次随机探索）"
              ><el-input-number v-model="settings.trials" :min="5" :max="200"
            /></el-form-item>
            <el-form-item label="优化目标"
              ><el-select v-model="settings.objective"
                ><el-option
                  label="收益减去回撤惩罚"
                  value="return_drawdown" /><el-option
                  label="收益（仍执行回撤／成交约束）"
                  value="return" /></el-select
            ></el-form-item>
            <el-form-item label="回撤惩罚权重"
              ><el-input-number
                v-model="settings.drawdown_penalty"
                :min="0"
                :max="10"
                :step="0.5"
                :disabled="settings.objective !== 'return_drawdown'"
            /></el-form-item>
            <el-form-item label="最大允许回撤（%）"
              ><el-input-number
                v-model="settings.max_drawdown_pct"
                :min="0.01"
                :max="100"
            /></el-form-item>
            <el-form-item label="最低平仓次数（每个区间）"
              ><el-input-number
                v-model="settings.min_trades"
                :min="1"
                :max="10000"
            /></el-form-item>
            <el-form-item label="独立验证窗口数"
              ><el-input-number
                v-model="settings.validation_windows"
                :min="1"
                :max="3"
            /></el-form-item>
            <el-form-item label="保留候选数"
              ><el-input-number v-model="settings.top_k" :min="1" :max="5"
            /></el-form-item>
            <el-form-item label="随机种子"
              ><el-input-number
                v-model="settings.seed"
                :min="0"
                :max="2147483647"
            /></el-form-item>
            <el-form-item label="验证收益要求"
              ><el-checkbox v-model="settings.require_positive_validation"
                >每个验证窗口收益必须为正</el-checkbox
              ></el-form-item
            >
          </div>
          <el-table
            :data="dimensions"
            class="search-dimensions"
            empty-text="正在获取模板参数"
            max-height="400"
          >
            <el-table-column label="搜索参数" min-width="190"
              ><template #default="{ row }"
                ><el-checkbox v-model="row.enabled">{{
                  row.label
                }}</el-checkbox></template
              ></el-table-column
            >
            <el-table-column label="下限" min-width="150"
              ><template #default="{ row }"
                ><el-input-number
                  v-model="row.low"
                  :min="row.min"
                  :max="row.max"
                  :step="row.step"
                  :precision="row.kind === 'int' ? 0 : 2"
                  :disabled="!row.enabled"
                  :aria-label="`${row.label}搜索下限`" /></template
            ></el-table-column>
            <el-table-column label="上限" min-width="150"
              ><template #default="{ row }"
                ><el-input-number
                  v-model="row.high"
                  :min="row.min"
                  :max="row.max"
                  :step="row.step"
                  :precision="row.kind === 'int' ? 0 : 2"
                  :disabled="!row.enabled"
                  :aria-label="`${row.label}搜索上限`" /></template
            ></el-table-column>
            <el-table-column label="步长" min-width="150"
              ><template #default="{ row }"
                ><el-input-number
                  v-model="row.step"
                  :min="row.kind === 'int' ? 1 : 0.01"
                  :precision="row.kind === 'int' ? 0 : 2"
                  :disabled="!row.enabled"
                  :aria-label="`${row.label}搜索步长`" /></template
            ></el-table-column>
          </el-table>
          <p class="panel-note">
            只用研究区间搜索与排序，固定候选后再验证。验证窗口连续且不重叠，每窗独立初始化资金和持仓；参数不再重选，属于固定参数的分窗验证。至少各
            100 根 K 线。资金费率与持仓量因子尚未接入。
          </p>
          <el-button
            type="primary"
            :loading="busy"
            :disabled="loadingSpace"
            @click="start"
            >开始自动优化</el-button
          >
        </el-form>
      </el-collapse-item></el-collapse
    >
    <div v-if="jobs.length" class="optimization-jobs">
      <el-button
        v-for="job in jobs"
        :key="job.id"
        :type="selected?.id === job.id ? 'primary' : 'default'"
        plain
        @click="choose(job.id)"
        >#{{ job.id }} {{ job.parameters.base.symbol }} ·
        {{ statusName(job.status) }} · {{ job.completed_trials }}/{{
          job.parameters.trials
        }}</el-button
      >
    </div>
    <div v-if="selected" class="optimization-result">
      <div class="panel-heading">
        <div>
          <h3>
            优化任务 #{{ selected.id }} · {{ statusName(selected.status) }}
          </h3>
          <span
            >搜索完成 {{ selected.completed_trials }} /
            {{ selected.parameters.trials }}
            次；搜索完成后继续验证和诊断候选</span
          >
        </div>
        <el-button
          v-if="active(selected.status)"
          :disabled="selected.status === 'cancelling'"
          @click="cancel(selected)"
          >取消优化</el-button
        >
      </div>
      <el-progress
        :percentage="progress"
        :status="selected.status === 'completed' ? 'success' : undefined"
      />
      <p class="panel-note">
        研究 {{ stamp(selected.parameters.base.start_at) }} 至
        {{ stamp(selected.parameters.split_at) }}；验证
        {{ stamp(selected.parameters.split_at) }} 至
        {{ stamp(selected.parameters.base.end_at) }}。
      </p>
      <el-alert
        v-if="selected.error"
        :title="selected.error"
        type="error"
        :closable="false"
      />
      <el-table
        :data="candidates"
        empty-text="搜索和验证完成后显示候选；未通过约束时会显示原因"
        max-height="560"
      >
        <el-table-column type="expand"
          ><template #default="{ row }"
            ><div class="candidate-details">
              <p>{{ ruleSummary(row.rules) }}</p>
              <ResearchDiagnostics
                :robustness="row.robustness"
                :factors="row.factor_analysis"
              />
              <p>
                研究：{{
                  row.train_pass ? "通过" : row.reasons.join("；")
                }}；验证：{{
                  row.validation_pass
                    ? "通过"
                    : row.validation_reasons.join("；")
                }}
              </p>
              <p v-for="(window, index) in row.windows" :key="window.start_at">
                窗口 {{ index + 1 }}：{{ stamp(window.start_at) }} 至
                {{ stamp(window.end_at) }} · 收益
                {{ percent(window.metrics.total_return) }} · 回撤
                {{ percent(window.metrics.max_drawdown) }} ·
                {{ window.metrics.trades }} 次 ·
                {{ window.passed ? "通过" : window.reasons.join("；") }}
              </p>
            </div></template
          ></el-table-column
        >
        <el-table-column label="候选 / 研究评分" min-width="150"
          ><template #default="{ row }"
            >试验 {{ row.number + 1 }}
            <div class="table-sub">{{ row.score.toFixed(5) }}</div></template
          ></el-table-column
        >
        <el-table-column label="研究收益 / 回撤" min-width="180"
          ><template #default="{ row }"
            >{{ percent(row.train_metrics.total_return) }} /
            {{ percent(row.train_metrics.max_drawdown) }}
            <div class="table-sub">
              {{ row.train_metrics.trades }} 次平仓
            </div></template
          ></el-table-column
        >
        <el-table-column label="验证收益 / 回撤" min-width="180"
          ><template #default="{ row }"
            >{{ percent(row.validation_metrics.total_return) }} /
            {{ percent(row.validation_metrics.max_drawdown) }}
            <div class="table-sub">
              {{ row.validation_metrics.trades }} 次平仓
            </div></template
          ></el-table-column
        >
        <el-table-column label="约束结果" min-width="150"
          ><template #default="{ row }"
            ><el-tag :type="row.eligible ? 'success' : 'warning'">{{
              row.eligible ? "研究与验证通过" : "未通过筛选"
            }}</el-tag></template
          ></el-table-column
        >
        <el-table-column label="操作" min-width="220"
          ><template #default="{ row }"
            ><el-button link @click="emit('inspect', row.backtest_id)"
              >查看验证回测</el-button
            ><el-button
              link
              type="primary"
              :disabled="!row.eligible"
              @click="promote(row as OptimizationCandidate)"
              >创建模拟策略</el-button
            ></template
          ></el-table-column
        >
      </el-table>
      <el-alert
        v-if="
          selected.status === 'completed' &&
          !candidates.some((item) => item.eligible)
        "
        title="本次没有候选通过全部条件。展开行查看原因；也可能交易数量过小／过大、因子过滤过严或样本不足。"
        type="info"
        :closable="false"
      />
      <p class="panel-note">
        候选按研究评分排序，验证结果不反馈给搜索器。反复根据验证结果调参，会使这段验证数据失去独立性；通过筛选后仍需模拟观察。
      </p>
    </div>
  </section>
</template>
