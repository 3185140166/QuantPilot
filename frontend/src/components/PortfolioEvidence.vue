<script setup lang="ts">
import StrategyEvidence, { type Qualification } from "./StrategyEvidence.vue";
export type CrossReport = {
  symbols: string[];
  horizon: number;
  rows: {
    key: string;
    label: string;
    periods: number;
    mean_rank_ic: number | null;
    positive_fraction: number | null;
    high_minus_low_gross: number | null;
  }[];
  limitations: string[];
};
defineProps<{
  qualification?: Qualification;
  cross?: CrossReport;
  review?: {
    stress: { multiplier: number; metrics: Record<string, number> }[];
    neighbors: { metrics: Record<string, number> }[];
  };
  selection?: {
    history: { timestamp: string; selected: string[] }[];
    history_truncated: boolean;
    rejected_signals: number;
    semantics: string;
  };
}>();
const fmt = (value: number | null) =>
  value === null ? "样本不足" : value.toFixed(4);
</script>
<template>
  <StrategyEvidence :qualification="qualification" />
  <section v-if="cross" class="panel cross-factor-report">
    <h3>多币横截面因子诊断</h3>
    <p>
      比较同一时刻 {{ cross.symbols.join("、") }} 的排名，观察后续
      {{ cross.horizon }} 根K线；不自动调整方向或权重。
    </p>
    <div style="overflow-x: auto">
      <table class="factor-table">
        <thead>
          <tr>
            <th>因子</th>
            <th>观察期</th>
            <th>平均排名相关</th>
            <th>正相关比例</th>
            <th>高组减低组毛收益</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in cross.rows" :key="row.key">
            <td>{{ row.label }}</td>
            <td>{{ row.periods }}</td>
            <td>{{ fmt(row.mean_rank_ic) }}</td>
            <td>{{ fmt(row.positive_fraction) }}</td>
            <td>{{ fmt(row.high_minus_low_gross) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-for="item in cross.limitations" :key="item" class="panel-note">
      {{ item }}
    </p>
  </section>
  <section v-if="review" class="panel">
    <h3>参数稳定性与成本压力</h3>
    <p>
      研究段邻近参数检查
      {{ review.neighbors.length }} 项；结果只用于诊断，不自动挑选参数。
    </p>
    <p v-for="item in review.stress" :key="item.multiplier">
      {{ item.multiplier }} 倍手续费与滑点：收益
      {{ (item.metrics.total_return * 100).toFixed(2) }}%，最大回撤
      {{ (Math.abs(item.metrics.max_drawdown) * 100).toFixed(2) }}%。
    </p>
  </section>
  <details v-if="selection" class="panel">
    <summary>历史逐时合约筛选记录</summary>
    <p>
      {{ selection.semantics }}；筛选跳过信号
      {{ selection.rejected_signals }} 次。{{
        selection.history_truncated
          ? "仅展示前500次记录，收益统计仍覆盖完整回放。"
          : ""
      }}
    </p>
    <p v-for="row in selection.history" :key="row.timestamp">
      {{ row.timestamp }}：{{
        row.selected.join("、") || "没有适合的新开仓候选"
      }}
    </p>
  </details>
</template>
<style scoped>
.factor-table {
  width: 100%;
  border-collapse: collapse;
}
th,
td {
  padding: 10px;
  border-bottom: 1px solid #ddd;
  text-align: left;
  white-space: nowrap;
}
</style>
