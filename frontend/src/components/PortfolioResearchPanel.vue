<script setup lang="ts">
import ResearchDiagnostics from "./ResearchDiagnostics.vue";
import FactorDiagnostics from "./FactorDiagnostics.vue";
import PortfolioEvidence, { type CrossReport } from "./PortfolioEvidence.vue";
import type { Qualification } from "./StrategyEvidence.vue";
import type { FactorReport } from "../types";
import { ElAlert } from "element-plus/es/components/alert/index.mjs";
import { ElButton } from "element-plus/es/components/button/index.mjs";
import { ElDatePicker } from "element-plus/es/components/date-picker/index.mjs";
import { ElDialog } from "element-plus/es/components/dialog/index.mjs";
import { ElForm } from "element-plus/es/components/form/index.mjs";
import { ElFormItem } from "element-plus/es/components/form/index.mjs";
import { ElInputNumber } from "element-plus/es/components/input-number/index.mjs";
import { ElOption } from "element-plus/es/components/select/index.mjs";
import { ElSelect } from "element-plus/es/components/select/index.mjs";
import { ElSwitch } from "element-plus/es/components/switch/index.mjs";
import { ElTable } from "element-plus/es/components/table/index.mjs";
import { ElTableColumn } from "element-plus/es/components/table/index.mjs";
import { defineAsyncComponent, onMounted, onBeforeUnmount, ref } from "vue";
import { request } from "../api";
const ChartPanel = defineAsyncComponent(() => import("./ChartPanel.vue"));
const props = defineProps<{
  token: string;
  symbols: string[];
  interval: string;
  disabled: boolean;
}>();
const emit = defineEmits<{ completed: [] }>();
const statuses: Record<string, string> = {
  pending: "等待研究进程",
  running: "回测中",
  completed: "已完成",
  failed: "失败",
};
type Job = {
  id: number;
  status: string;
  error?: string;
  parameters: {
    evaluation_phase: string;
    start_at: string;
    end_at: string;
    rolling_group_id?: string;
    rolling_index?: number;
    rolling_windows?: number;
    portfolio_config: { symbols: string[]; interval: string };
  };
  metrics?: Record<string, number>;
  funding_total?: number;
  partial_fills?: number;
  liquidations?: number;
};
type Result = {
  cross_sectional?: CrossReport;
  qualification?: Qualification;
  strategy_review?: {
    stress: { multiplier: number; metrics: Record<string, number> }[];
    neighbors: { metrics: Record<string, number> }[];
  };
  selection?: {
    history: { timestamp: string; selected: string[] }[];
    history_truncated: boolean;
    rejected_signals: number;
    semantics: string;
  };
  factor_analysis?: Record<string, FactorReport>;
  data_quality?: Record<string, unknown>;
  metrics: Record<string, number>;
  curve: { timestamp: string; equity: number }[];
  funding_total: number;
  fees_total: number;
  partial_fills: number;
  expired_orders: number;
  liquidations: number;
  limitations: string[];
  trades: Record<string, unknown>[];
  coverage: Record<string, unknown>;
  events: Record<string, unknown>[];
};
const jobs = ref<Job[]>([]);
const detail = ref<Result>();
const selected = ref<Job>();
type RollingReport = {
  complete: boolean;
  passed: boolean;
  expected: number;
  semantics: string;
  windows: {
    id: number;
    index: number;
    status: string;
    start_at: string;
    end_at: string;
    metrics: Record<string, number>;
    passed: boolean;
    failed: string[];
  }[];
};
const rollingReport = ref<RollingReport>();
const rollingSource = ref<Job>();
const rollingDates = ref<[Date, Date]>();
const rollingWindows = ref(3);
function prepareRolling(job: Job) {
  rollingSource.value = job;
  rollingDates.value = [new Date(job.parameters.end_at), new Date()];
}
async function createRolling() {
  if (
    !rollingSource.value ||
    !rollingDates.value?.[0] ||
    !rollingDates.value?.[1]
  )
    return;
  busy.value = true;
  error.value = "";
  try {
    const interval = rollingSource.value.parameters.portfolio_config.interval;
    const step = (interval === "D" ? 1440 : Number(interval)) * 60000;
    const align = (value: Date) =>
      new Date(Math.floor(value.getTime() / step) * step).toISOString();
    await request(
      `/trading/portfolio/${rollingSource.value.id}/rolling-validation`,
      props.token,
      {
        start_at: align(rollingDates.value[0]),
        end_at: align(rollingDates.value[1]),
        windows: rollingWindows.value,
      },
    );
    notice.value = `已提交${rollingWindows.value}个独立窗口，规则与资金配置保持研究快照；每个窗口独立开始，不自动调参或下单。`;
    rollingSource.value = undefined;
    await refresh();
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    busy.value = false;
  }
}
const error = ref("");
const busy = ref(false);
const notice = ref("");
const orderType = ref("Market");
const funding = ref(true);
const participation = ref(1);
const offset = ref(5);
const expiry = ref(2);
const makerFee = ref(0.02);
const end = new Date(Date.now() - 30 * 86400000);
const start = new Date(end.getTime() - 90 * 86400000);
const dates = ref<[Date, Date]>([start, end]);
let timer: number | undefined;
let disposed = false;
let fetching = false;
let initialized = false;
async function refresh() {
  if (fetching) return;
  fetching = true;
  try {
    const value = await request<Job[]>("/trading/portfolio", props.token);
    if (disposed) return;
    if (!Array.isArray(value))
      throw new Error("组合回测列表格式异常，请重启后端服务");
    if (
      initialized &&
      value.some(
        (j) =>
          j.status === "completed" &&
          jobs.value.find((old) => old.id === j.id)?.status !== "completed",
      )
    )
      emit("completed");
    jobs.value = value;
    initialized = true;
  } catch (e) {
    if (!disposed) error.value = (e as Error).message;
  } finally {
    fetching = false;
  }
}
async function create() {
  busy.value = true;
  error.value = "";
  try {
    if (!dates.value?.[0] || !dates.value?.[1])
      throw new Error("请选择研究区间");
    const step =
      (props.interval === "D" ? 1440 : Number(props.interval)) * 60000;
    const align = (d: Date) =>
      new Date(Math.floor(d.getTime() / step) * step).toISOString();
    await request("/trading/portfolio", props.token, {
      start_at: align(dates.value[0]),
      end_at: align(dates.value[1]),
      evaluation_phase: "research",
      execution: {
        order_type: orderType.value,
        include_funding: funding.value,
        participation_rate: participation.value / 100,
        limit_offset_bps: offset.value,
        limit_expiry_bars: expiry.value,
        maker_fee_rate: makerFee.value / 100,
      },
    });
    notice.value = "组合研究已排队，完成后可查看详情并创建验证回测";
    await refresh();
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    busy.value = false;
  }
}
async function open(job: Job) {
  busy.value = true;
  error.value = "";
  rollingReport.value = undefined;
  try {
    const value = await request<{ result: Result }>(
      `/backtests/${job.id}`,
      props.token,
    );
    selected.value = job;
    detail.value = value.result;
    if (job.parameters.rolling_group_id)
      rollingReport.value = await request<RollingReport>(
        `/trading/portfolio/${job.id}/rolling-validation`,
        props.token,
      );
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    busy.value = false;
  }
}
async function validate(job: Job) {
  busy.value = true;
  error.value = "";
  try {
    const interval = job.parameters.portfolio_config.interval;
    const step = (interval === "D" ? 1440 : Number(interval)) * 60000;
    await request(`/trading/portfolio/${job.id}/validation`, props.token, {
      start_at: job.parameters.end_at,
      end_at: new Date(Math.floor(Date.now() / step) * step).toISOString(),
    });
    notice.value =
      "验证回测已排队，沿用研究时的币种、预算与参数；完成后在验证参数列表中查看";
    await refresh();
  } catch (e) {
    error.value = (e as Error).message;
  } finally {
    busy.value = false;
  }
}
onMounted(() => {
  void refresh();
  timer = window.setInterval(refresh, 10000);
});
onBeforeUnmount(() => {
  disposed = true;
  window.clearInterval(timer);
});
</script>
<template>
  <section class="panel portfolio-research-panel">
    <div class="panel-heading"><h3>多币种共享预算回测</h3></div>
    <p class="panel-note">
      使用已保存的配置：{{ symbols.join("、") }}，周期{{
        interval
      }}。所有币种共享一份预算、持仓上限和风险额度。研究期间不会向交易所提交订单。
    </p>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <el-form label-position="top" :disabled="busy || disabled"
      ><div class="form-grid">
        <el-form-item label="研究区间"
          ><el-date-picker
            v-model="dates"
            type="datetimerange"
            start-placeholder="开始"
            end-placeholder="结束"
        /></el-form-item>
        <el-form-item label="成交模型"
          ><el-select v-model="orderType"
            ><el-option label="市价单" value="Market" /><el-option
              label="限价挂单（仅回测）"
              value="Limit" /></el-select
        ></el-form-item>
        <el-form-item label="每根K线最大成交量占比（%）"
          ><el-input-number
            v-model="participation"
            :min="0.01"
            :max="100"
            :step="0.1"
        /></el-form-item>
        <el-form-item label="计入历史资金费"
          ><el-switch v-model="funding"
        /></el-form-item>
        <template v-if="orderType === 'Limit'"
          ><el-form-item label="挂单偏离（基点）"
            ><el-input-number
              v-model="offset"
              :min="0"
              :max="100" /></el-form-item
          ><el-form-item label="挂单有效K线数"
            ><el-input-number
              v-model="expiry"
              :min="1"
              :max="10" /></el-form-item
          ><el-form-item label="挂单手续费（%）"
            ><el-input-number
              v-model="makerFee"
              :min="0"
              :max="1"
              :step="0.01" /></el-form-item
        ></template></div
    ></el-form>
    <el-button
      type="primary"
      :disabled="disabled || symbols.length < 2"
      :loading="busy"
      @click="create"
      >创建组合研究回测</el-button
    >
    <p class="panel-note">
      {{
        notice ||
        "至少保存两个币种。手续费使用配置值；缺少标记价格或所需资金费数据时任务会失败。资金费可用记录与强平近似模型的限制将在结果中列明。"
      }}
    </p>
    <el-table :data="jobs"
      ><el-table-column prop="id" label="编号" width="70" /><el-table-column
        label="币种"
        ><template #default="{ row }">{{
          row.parameters.portfolio_config.symbols.join(" / ")
        }}</template></el-table-column
      ><el-table-column label="阶段"
        ><template #default="{ row }">{{
          row.parameters.evaluation_phase === "validation"
            ? `验证${row.parameters.rolling_index ? " · 窗口 " + row.parameters.rolling_index + "/" + row.parameters.rolling_windows : ""}`
            : "研究"
        }}</template></el-table-column
      ><el-table-column label="状态"
        ><template #default="{ row }">{{
          statuses[row.status] ?? row.status
        }}</template></el-table-column
      ><el-table-column prop="error" label="失败原因" /><el-table-column
        label="操作"
        width="340"
        ><template #default="{ row }"
          ><el-button
            :disabled="busy || row.status !== 'completed'"
            @click="open(row as Job)"
            >详情</el-button
          ><el-button
            :disabled="
              busy ||
              row.status !== 'completed' ||
              row.parameters.evaluation_phase === 'validation'
            "
            @click="validate(row as Job)"
            >验证</el-button
          ><el-button
            :disabled="
              busy ||
              disabled ||
              row.status !== 'completed' ||
              row.parameters.evaluation_phase !== 'research'
            "
            @click="prepareRolling(row as Job)"
            >多窗口验证</el-button
          ></template
        ></el-table-column
      ></el-table
    >
    <el-dialog
      :model-value="!!rollingSource"
      title="创建后续多窗口独立验证"
      width="90%"
      @close="rollingSource = undefined"
    >
      <p>
        研究 #{{
          rollingSource?.id
        }}。将后续时间切分为连续、不重叠的窗口；每个窗口至少100根K线，沿用研究参数、币种和风险设置。不会根据验证结果重新选参数。
      </p>
      <el-date-picker
        v-model="rollingDates"
        type="datetimerange"
        start-placeholder="验证开始"
        end-placeholder="验证结束"
        style="width: 100%"
      />
      <el-form-item label="窗口数"
        ><el-input-number v-model="rollingWindows" :min="2" :max="5"
      /></el-form-item>
      <el-button
        :disabled="busy || disabled || !rollingDates"
        :loading="busy"
        @click="createRolling"
        >提交全部独立窗口</el-button
      >
      <el-alert v-if="error" :title="error" type="error" :closable="false" />
    </el-dialog>
    <el-dialog
      :model-value="!!detail"
      title="组合回测详情"
      width="90%"
      @close="detail = undefined"
    >
      <template v-if="detail"
        ><section v-if="rollingReport" class="rolling-validation-report">
          <h3>多窗口独立验证</h3>
          <p>
            {{
              rollingReport.complete ? "所有窗口已完成" : "窗口尚未全部完成"
            }}
            ·
            {{
              rollingReport.passed
                ? "各窗口证据检查通过"
                : "尚未全部通过，请查看失败项"
            }}。
          </p>
          <el-button :disabled="busy" @click="selected && open(selected)"
            >刷新窗口报告</el-button
          >
          <el-table :data="rollingReport.windows">
            <el-table-column prop="id" label="编号" width="70" />
            <el-table-column prop="index" label="窗口" width="70" />
            <el-table-column label="状态"
              ><template #default="{ row }">{{
                statuses[row.status] ?? row.status
              }}</template></el-table-column
            >
            <el-table-column label="净收益"
              ><template #default="{ row }">{{
                row.metrics.total_return === undefined
                  ? "待完成"
                  : (row.metrics.total_return * 100).toFixed(2) + "%"
              }}</template></el-table-column
            >
            <el-table-column label="失败项" min-width="180"
              ><template #default="{ row }">{{
                row.failed.join("、") || "已通过"
              }}</template></el-table-column
            >
          </el-table>
          <p class="panel-note">{{ rollingReport.semantics }}</p>
        </section>
        <p>
          任务 {{ selected?.id }} · 资金费支出 {{ detail.funding_total }} ·
          手续费 {{ detail.fees_total }} · 部分成交 {{ detail.partial_fills }} ·
          过期订单 {{ detail.expired_orders }} · 强平 {{ detail.liquidations }}
        </p>
        <p>
          总收益 {{ (detail.metrics.total_return * 100).toFixed(2) }}% ·
          标准最大回撤 {{ (detail.metrics.max_drawdown * 100).toFixed(2) }}% ·
          已结算交易 {{ detail.metrics.trades }} 笔
        </p>
        <p class="panel-note">
          资金费正值表示支付，负值表示收入；强平结果为研究近似值。
        </p>
        <ChartPanel
          :option="{
            xAxis: {
              type: 'category',
              data: detail.curve.map((p) => p.timestamp),
            },
            yAxis: { type: 'value', scale: true },
            tooltip: { trigger: 'axis' },
            series: [
              {
                type: 'line',
                data: detail.curve.map((p) => p.equity),
                showSymbol: false,
              },
            ],
          }" />
        <p v-for="(item, i) in detail.limitations" :key="i">{{ item }}</p>
        <ResearchDiagnostics :quality="detail.data_quality" />
        <PortfolioEvidence
          :cross="detail.cross_sectional"
          :qualification="detail.qualification"
          :review="detail.strategy_review"
          :selection="detail.selection" />
        <div v-for="(report, symbol) in detail.factor_analysis" :key="symbol">
          <h4>{{ symbol }} 因子研究</h4>
          <FactorDiagnostics :report="report" />
        </div>
        <details>
          <summary>数据覆盖与交易事件</summary>
          <pre>{{ JSON.stringify(detail.coverage, null, 2) }}</pre>
          <pre>{{ JSON.stringify(detail.events, null, 2) }}</pre>
        </details>
        <el-table :data="detail.trades"
          ><el-table-column prop="symbol" label="币种" /><el-table-column
            prop="side"
            label="方向" /><el-table-column
            prop="entry_time"
            label="开仓时间" /><el-table-column
            prop="exit_time"
            label="平仓时间" /><el-table-column
            prop="pnl"
            label="净收益" /><el-table-column
            prop="exit_reason"
            label="原因" /></el-table
      ></template>
    </el-dialog>
  </section>
</template>
<style scoped>
.portfolio-research-panel :deep(.el-date-editor) {
  width: 100%;
  max-width: 100%;
  min-width: 0;
}
pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
@media (max-width: 640px) {
  .form-grid {
    grid-template-columns: 1fr;
  }
}
</style>
