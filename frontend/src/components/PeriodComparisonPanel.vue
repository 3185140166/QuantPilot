<script setup lang="ts">
import { ElAlert } from "element-plus/es/components/alert/index.mjs";
import { ElButton } from "element-plus/es/components/button/index.mjs";
import { ElDatePicker } from "element-plus/es/components/date-picker/index.mjs";
import { ElOption } from "element-plus/es/components/select/index.mjs";
import { ElSelect } from "element-plus/es/components/select/index.mjs";
import { ElTable } from "element-plus/es/components/table/index.mjs";
import { ElTableColumn } from "element-plus/es/components/table/index.mjs";
import { computed, onMounted, onBeforeUnmount, ref, watch } from "vue";
import { ElMessage } from "element-plus/es/components/message/index.mjs";
import { ApiError, request } from "../api";
import type { FactorReport, Strategy } from "../types";
import FactorDiagnostics from "./FactorDiagnostics.vue";
const props = defineProps<{
  token: string;
  disabled: boolean;
  symbols: string[];
  sourceStrategyId?: number;
  hourlyTrendFilter?: boolean;
}>();
const emit = defineEmits<{ completed: [] }>();
type Job = {
  id: number;
  status: string;
  error?: string;
  research_worker_online?: boolean;
  risk_summary?: { max_drawdown_pct: number; policy: string } | null;
  parameters: {
    comparison_id: string;
    interval: string;
    symbol: string;
    start_at: string;
    end_at: string;
    evaluation_phase?: string;
  };
  metrics?: Record<string, number>;
  rejected_entries: number;
};
type Receipt = { comparison_id: string; jobs?: Job[] };
const boundary = (stamp: number) => Math.floor(stamp / 14400000) * 14400000;
const symbol = ref(props.symbols[0] ?? "");
const strategies = ref<Strategy[]>([]);
const sourceId = ref<number | undefined>(props.sourceStrategyId);
const sourceError = ref("");
const source = computed(() =>
  strategies.value.find((item) => item.id === sourceId.value),
);
const researchCount = computed(() =>
  (source.value?.rules.hourly_trend_filter ?? props.hourlyTrendFilter) ? 1 : 3,
);
const researchSymbols = computed(() =>
  source.value ? [source.value.symbol] : props.symbols,
);
function changeSource() {
  symbol.value = source.value?.symbol ?? props.symbols[0] ?? "";
  phase.value = "research";
}
watch(
  () => props.sourceStrategyId,
  (id) => {
    sourceId.value = id;
    changeSource();
  },
);
async function loadSources() {
  try {
    const next = await request<Strategy[]>("/strategies", props.token);
    if (disposed) return;
    strategies.value = next.filter((item) => item.exchange === "bybit");
    sourceError.value = "";
    if (sourceId.value !== undefined) changeSource();
  } catch (err) {
    if (!disposed)
      sourceError.value =
        err instanceof Error ? err.message : "读取模拟策略失败";
  }
}
const end = boundary(Date.now()) - 30 * 86400000;
const dates = ref<[Date, Date]>([new Date(end - 90 * 86400000), new Date(end)]);
const phase = ref("research");
const jobs = ref<Job[]>([]);
const chosen = ref("");
const groups = computed(() => [
  ...new Map(jobs.value.map((j) => [j.parameters.comparison_id, j])).values(),
]);
const visible = computed(() =>
  jobs.value.filter(
    (j) =>
      j.parameters.comparison_id ===
      (chosen.value || jobs.value[0]?.parameters.comparison_id),
  ),
);
const validationJob = ref<Job>();
const validationDates = ref<[Date, Date]>();
const error = ref("");
const diagnostic = ref<{ id: number; report?: FactorReport }>();
const diagnosticBusy = ref(false);
const diagnosticError = ref("");
async function showDiagnostics(job: Job) {
  diagnosticBusy.value = true;
  diagnosticError.value = "";
  diagnostic.value = undefined;
  try {
    const detail = await request<{
      result?: { factor_analysis?: FactorReport };
    }>(`/backtests/${job.id}`, props.token);
    if (!disposed)
      diagnostic.value = { id: job.id, report: detail.result?.factor_analysis };
  } catch (err) {
    if (!disposed)
      diagnosticError.value =
        err instanceof Error ? err.message : "读取因子诊断失败";
  } finally {
    diagnosticBusy.value = false;
  }
}
const message = ref("");
const busy = ref(false);
let timer: number | undefined;
let fetching = false;
let disposed = false;
let completed = "";
const readableError = (err: unknown) =>
  err instanceof ApiError && err.status === 404
    ? "当前后端尚未加载新功能。请停止原启动终端，再重新启动平台并刷新页面。"
    : err instanceof Error
      ? err.message
      : "读取周期对比失败";
async function load() {
  if (fetching || disposed) return;
  fetching = true;
  try {
    const next = await request<Job[]>("/trading/comparisons", props.token);
    if (disposed) return;
    if (!Array.isArray(next))
      throw new Error("周期对比接口格式不匹配，请重启平台");
    jobs.value = next;
    const signature = next
      .filter((j) => j.status === "completed")
      .map((j) => j.id)
      .join(",");
    if (signature !== completed) {
      completed = signature;
      emit("completed");
    }
    error.value = "";
  } catch (err) {
    if (!disposed) error.value = readableError(err);
  } finally {
    fetching = false;
  }
}
function range(value?: [Date, Date]) {
  if (!value?.[0] || !value?.[1]) throw new Error("请先选择开始和结束时间");
  return {
    start_at: new Date(boundary(value[0].getTime())).toISOString(),
    end_at: new Date(boundary(value[1].getTime())).toISOString(),
  };
}
async function submit(path: string, payload: unknown, count: number) {
  busy.value = true;
  error.value = "";
  message.value = "";
  try {
    const receipt = await request<Receipt>(path, props.token, payload);
    chosen.value = receipt.comparison_id;
    if (receipt.jobs) jobs.value = [...receipt.jobs, ...jobs.value];
    const submitted = receipt.jobs?.length || count;
    message.value = `已提交 ${submitted} 项回测，后台处理，结果每10秒刷新；不会提交交易订单。`;
    ElMessage.success(`已提交 ${submitted} 项回测`);
    await load();
  } catch (err) {
    error.value = readableError(err);
  } finally {
    busy.value = false;
  }
}
async function queue() {
  try {
    if (sourceId.value !== undefined && !source.value)
      throw new Error("来源策略尚未加载或已不存在，请刷新策略列表");
    await submit(
      "/trading/comparisons",
      {
        symbol: symbol.value || props.symbols[0],
        ...range(dates.value),
        evaluation_phase: phase.value,
        ...(source.value ? { source_strategy_id: source.value.id } : {}),
      },
      researchCount.value,
    );
  } catch (err) {
    error.value = readableError(err);
  }
}
function prepareValidation(job: Job) {
  validationJob.value = job;
  validationDates.value = [
    new Date(job.parameters.end_at),
    new Date(boundary(Date.now())),
  ];
}
async function validate() {
  if (!validationJob.value) return;
  try {
    await submit(
      `/trading/comparisons/${validationJob.value.id}/validation`,
      range(validationDates.value),
      1,
    );
  } catch (err) {
    error.value = readableError(err);
  }
}
const periodNames: Record<string, string> = {
  "15": "15分钟",
  "60": "1小时",
  "240": "4小时",
};
const jobStateNames: Record<string, string> = {
  pending: "排队中",
  running: "运行中",
  completed: "已完成",
  failed: "失败",
};
const percent = (value?: number) =>
  value === undefined ? "待完成" : `${(value * 100).toFixed(2)}%`;
onMounted(() => {
  void loadSources();
  void load();
  timer = window.setInterval(() => void load(), 10000);
});
onBeforeUnmount(() => {
  disposed = true;
  window.clearInterval(timer);
});
</script>
<template>
  <section class="panel period-comparison-panel">
    <div class="panel-heading">
      <h3>
        {{
          researchCount === 1
            ? "1小时方向过滤＋15分钟入场研究"
            : "15分钟 / 1小时 / 4小时对比"
        }}
      </h3>
      <el-button link @click="load">刷新结果</el-button>
    </div>
    <p class="panel-note">
      选择规则来源 → 创建研究 → 创建后续独立验证 →
      预览并应用到助手。回测只重放历史，不会下单。
    </p>
    <el-select
      v-model="sourceId"
      clearable
      placeholder="规则来源：已保存的交易助手配置"
      @change="changeSource"
      :disabled="busy"
    >
      <el-option
        v-for="item in strategies"
        :key="item.id"
        :value="item.id"
        :label="`模拟策略 #${item.id} ${item.name} · ${item.symbol}`"
      />
    </el-select>
    <el-button link @click="loadSources">刷新模拟策略</el-button>
    <el-alert
      v-if="sourceError"
      :title="sourceError"
      type="error"
      :closable="false"
    />
    <p class="panel-note" v-if="source">
      已选：{{ source.name }} · 原周期 {{ source.interval }} ·
      {{ source.rules.strategy_type }}。 提交时保存规则快照；{{
        researchCount === 1
          ? "启用小时过滤时只研究15分钟入场"
          : "分别比较三个周期"
      }}。模拟持仓、模拟收益和固定下单数量不会导入。
      回测预算、杠杆和成本来自助手已保存配置，助手当前执行参数不会因此改变。
    </p>
    <p class="panel-note" v-else>
      当前使用助手已保存的规则和风险配置；可选择 Bybit
      模拟策略作为研究规则来源。
    </p>
    <p class="panel-note">
      默认研究90天，留出最近30天供后续验证。未启用小时过滤时分别比较三个周期；启用时研究同一套1小时方向＋15分钟入场规则。小时方向使用同一组快慢EMA及完整已收盘小时数据。
    </p>
    <el-select v-model="symbol" placeholder="选择配置中的币种"
      ><el-option v-for="s in researchSymbols" :key="s" :label="s" :value="s"
    /></el-select>
    <el-date-picker
      v-model="dates"
      type="datetimerange"
      start-placeholder="开始时间"
      end-placeholder="结束时间"
      style="width: 100%; margin: 12px 0"
    />
    <p class="panel-note">
      时间按本地时区显示，提交时向下对齐四小时边界。每个周期至少100根K线。
    </p>
    <p class="panel-note">
      绩效回撤按权益高点计算；风控回撤按固定共享预算计算。旧记录未保存风控版本时显示“未记录”，建议重新回测后比较。
    </p>
    <el-select v-model="phase" :disabled="sourceId !== undefined"
      ><el-option label="研究区间" value="research" /><el-option
        label="独立验证区间"
        value="validation"
    /></el-select>
    <el-button
      :disabled="
        disabled ||
        busy ||
        !researchSymbols.length ||
        (sourceId !== undefined && !source) ||
        (!!source && source.rules.stop_loss_pct <= 0)
      "
      :loading="busy"
      @click="queue"
      >{{
        researchCount === 1 ? "创建多周期策略回测" : "创建三个周期回测"
      }}</el-button
    >
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <el-alert
      v-if="source && source.rules.stop_loss_pct <= 0"
      title="这套模拟策略没有设置止损，暂不能用于助手仓位研究。请先准备一套设置了正数止损的策略。"
      type="warning"
      :closable="false"
    />
    <el-alert
      v-if="message"
      :title="message"
      type="success"
      :closable="false"
    />
    <p v-if="disabled" class="panel-note">
      需要当前环境执行就绪及交易所行情；切换环境后请重启平台。
    </p>
    <p class="panel-note">
      当前合约限制会保存为快照，并非历史限制。单币结果不等于多币组合收益；尚未模拟历史资金费、保证金强平和真实订单簿成交。同根K线触及止损及止盈时按止损先发生计算。
    </p>
    <template v-if="jobs.length">
      <el-alert
        v-if="
          visible.some(
            (j) =>
              ['pending', 'running'].includes(j.status) &&
              j.research_worker_online === false,
          )
        "
        title="回测进程未在线，任务已保留。请确认统一启动器中的研究服务正常运行。"
        type="warning"
        :closable="false"
      />
      <el-select
        v-model="chosen"
        placeholder="选择历史对比组"
        style="width: 100%"
        ><el-option
          v-for="group in groups"
          :key="group.parameters.comparison_id"
          :value="group.parameters.comparison_id"
          :label="`${group.parameters.symbol} · ${group.parameters.evaluation_phase === 'validation' ? '验证' : '研究'} · ${group.parameters.start_at} 至 ${group.parameters.end_at}`"
      /></el-select>
      <p class="panel-note">
        最近一组：{{ visible[0]?.parameters.symbol }}，{{
          visible[0]?.parameters.start_at
        }}
        至
        {{
          visible[0]?.parameters.end_at
        }}。排队和运行表示尚未完成，失败会显示原因；完成不代表盈利。
      </p>
      <el-table :data="visible">
        <el-table-column label="任务" width="90"
          ><template #default="{ row }"
            >#{{ row.id }}</template
          ></el-table-column
        >
        <el-table-column label="周期" width="90"
          ><template #default="{ row }">{{
            periodNames[row.parameters.interval]
          }}</template></el-table-column
        >
        <el-table-column label="状态" width="100"
          ><template #default="{ row }">{{
            jobStateNames[row.status] ?? row.status
          }}</template></el-table-column
        >
        <el-table-column label="净收益" width="100"
          ><template #default="{ row }">{{
            percent(row.metrics?.total_return)
          }}</template></el-table-column
        >
        <el-table-column label="绩效回撤" width="100"
          ><template #default="{ row }">{{
            percent(row.metrics?.max_drawdown)
          }}</template></el-table-column
        >
        <el-table-column label="风控回撤（预算%）" width="160"
          ><template #default="{ row }">{{
            row.risk_summary
              ? row.risk_summary.max_drawdown_pct.toFixed(2) + "%"
              : "未记录"
          }}</template></el-table-column
        >
        <el-table-column label="交易次数" width="100"
          ><template #default="{ row }">{{
            row.metrics?.trades ?? "—"
          }}</template></el-table-column
        >
        <el-table-column label="平均每笔净收益(U)" width="150"
          ><template #default="{ row }">{{
            row.metrics?.average_trade_pnl?.toFixed(4) ?? "—"
          }}</template></el-table-column
        >
        <el-table-column prop="rejected_entries" label="跳过开仓" width="100" />
        <el-table-column prop="error" label="失败原因" min-width="180" />
        <el-table-column label="策略诊断" width="140"
          ><template #default="{ row }"
            ><el-button
              :disabled="row.status !== 'completed' || diagnosticBusy"
              @click="showDiagnostics(row as Job)"
              >查看因子诊断</el-button
            ></template
          ></el-table-column
        >
        <el-table-column label="下一步" width="160"
          ><template #default="{ row }"
            ><el-button
              v-if="row.parameters.evaluation_phase !== 'validation'"
              :disabled="row.status !== 'completed' || busy"
              @click="prepareValidation(row as Job)"
              >创建后续验证</el-button
            ><span v-else>完成后在下方应用</span></template
          ></el-table-column
        >
      </el-table>
    </template>
    <p v-else-if="!error" class="panel-note">
      尚未创建周期对比。点击上方按钮后，这里会显示三个任务及执行状态。
    </p>
    <el-alert
      v-if="diagnosticError"
      :title="diagnosticError"
      type="error"
      :closable="false"
    />
    <div v-if="diagnostic" class="period-factor-report">
      <h4>回测 #{{ diagnostic.id }} 因子诊断</h4>
      <FactorDiagnostics v-if="diagnostic.report" :report="diagnostic.report" />
      <p v-else class="panel-note">
        此历史结果尚未记录因子诊断，请创建新的回测生成报告；旧结果不会自动改写。
      </p>
    </div>
    <div v-if="validationJob" class="followup-validation">
      <h4>验证研究回测 #{{ validationJob.id }}</h4>
      <p class="panel-note">
        保留这项研究的规则、预算、风险和合约限制，在研究结束之后的数据上验证。区间不得重叠，且至少100根K线；研究结束太近时需等待更多数据或改用更早的研究区间。
      </p>
      <el-date-picker
        v-model="validationDates"
        type="datetimerange"
        start-placeholder="验证开始时间"
        end-placeholder="验证结束时间"
        style="width: 100%"
      />
      <el-button :loading="busy" :disabled="disabled || busy" @click="validate"
        >提交独立验证回测</el-button
      >
    </div>
  </section>
</template>
