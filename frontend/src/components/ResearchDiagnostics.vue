<script setup lang="ts">
import { computed } from "vue";
import type { RobustnessReport } from "../types";
import type { FactorReport } from "../types";
import FactorDiagnostics from "./FactorDiagnostics.vue";
const props = defineProps<{
  robustness?: RobustnessReport;
  quality?: Record<string, unknown>;
  factors?: FactorReport;
}>();
const pct = (value: number) => `${(value * 100).toFixed(2)}%`;
const environmentNames: Record<string, string> = {
  testnet: "测试网",
  mainnet: "主网",
  demo: "演示行情",
};
const qualityRows = computed(() => {
  const q = props.quality;
  if (!q) return [];
  if (typeof q.bars === "number")
    return [
      {
        symbol: `研究行情（${environmentNames[String(q.market_environment)] ?? "环境未记录"}）`,
        bars: q.bars,
        basis: undefined,
        gap: undefined,
        fingerprint: q.fingerprint,
        warnings: [] as string[],
      },
    ];
  return Object.entries(q).map(([symbol, raw]) => {
    const value = raw as {
      price?: { bars: number; fingerprint: string };
      max_mark_basis_pct?: number;
      funding_largest_gap_minutes?: number;
      warnings?: string[];
      market_environment?: string;
    };
    return {
      symbol: `${symbol}${value.market_environment ? "（" + (environmentNames[value.market_environment] ?? value.market_environment) + "）" : ""}`,
      bars: value.price?.bars,
      basis: value.max_mark_basis_pct,
      gap: value.funding_largest_gap_minutes,
      fingerprint: value.price?.fingerprint,
      warnings: value.warnings ?? [],
    };
  });
});
</script>
<template>
  <div class="research-diagnostics">
    <FactorDiagnostics v-if="factors" :report="factors" />
    <details v-if="quality">
      <summary>数据一致性报告</summary>
      <div v-for="row in qualityRows" :key="row.symbol">
        <p>
          {{ row.symbol }} · {{ row.bars }} 根K线<span
            v-if="row.basis !== undefined"
          >
            · 标记价最大偏离 {{ row.basis.toFixed(3) }}%</span
          ><span v-if="row.gap !== undefined">
            · 资金费最大记录间隔 {{ row.gap }} 分钟</span
          >
        </p>
        <p
          v-for="warning in row.warnings"
          :key="warning"
          class="diagnostic-warning"
        >
          {{ warning }}
        </p>
        <p class="fingerprint">数据指纹：{{ row.fingerprint }}</p>
      </div>
    </details>
    <details v-if="robustness">
      <summary>稳健性诊断：成本、邻近参数与滚动验证</summary>
      <p class="panel-note">
        这些诊断不改变原搜索排序或应用资格；请结合样本数量判断。
      </p>
      <p
        v-for="warning in robustness.warnings"
        :key="warning"
        class="diagnostic-warning"
      >
        {{ warning }}
      </p>
      <h4>成本压力 · 独立验证区间</h4>
      <div class="table-scroll">
        <table>
          <thead>
            <tr>
              <th>成本倍数</th>
              <th>手续费</th>
              <th>滑点（基点）</th>
              <th>收益</th>
              <th>回撤</th>
              <th>交易数</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in robustness.cost_stress" :key="r.multiplier">
              <td>{{ r.multiplier }}倍</td>
              <td>{{ pct(r.fee_rate) }}</td>
              <td>{{ r.slippage_bps }}</td>
              <td>{{ pct(r.metrics.total_return) }}</td>
              <td>{{ pct(r.metrics.max_drawdown) }}</td>
              <td>{{ r.metrics.trades }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <h4>邻近参数 · 仅研究区间</h4>
      <div class="table-scroll">
        <table>
          <thead>
            <tr>
              <th>主要周期参数</th>
              <th>研究收益</th>
              <th>回撤</th>
              <th>交易数</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(r, i) in robustness.sensitivity" :key="i">
              <td>
                EMA {{ r.rules.fast }} / {{ r.rules.slow }} · RSI
                {{ r.rules.rsi_period }} · 突破 {{ r.rules.breakout_period }} ·
                布林 {{ r.rules.bollinger_period }}
              </td>
              <td>{{ pct(r.metrics.total_return) }}</td>
              <td>{{ pct(r.metrics.max_drawdown) }}</td>
              <td>{{ r.metrics.trades }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <h4>滚动样本外验证</h4>
      <p class="panel-note">
        每个窗口仅用之前的数据选择邻近参数，随后测试下一段；窗口资金独立初始化。
      </p>
      <div class="table-scroll">
        <table>
          <thead>
            <tr>
              <th>研究结束</th>
              <th>样本外区间</th>
              <th>研究约束</th>
              <th>样本外收益</th>
              <th>回撤</th>
              <th>交易数</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in robustness.rolling" :key="r.test_start">
              <td>{{ r.train_end.slice(0, 16) }}</td>
              <td>
                {{ r.test_start.slice(0, 16) }} 至 {{ r.test_end.slice(0, 16) }}
              </td>
              <td>{{ r.train_pass ? "通过" : "未通过" }}</td>
              <td>{{ pct(r.test_metrics.total_return) }}</td>
              <td>{{ pct(r.test_metrics.max_drawdown) }}</td>
              <td>{{ r.test_metrics.trades }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-for="item in robustness.limitations" :key="item" class="panel-note">
        {{ item }}
      </p>
    </details>
  </div>
</template>
<style scoped>
.research-diagnostics {
  margin: 16px 0;
}
summary {
  cursor: pointer;
  margin: 12px 0;
  font-weight: 600;
}
.table-scroll {
  max-width: 100%;
  overflow-x: auto;
}
table {
  border-collapse: collapse;
  width: 100%;
  font-size: 12px;
}
td,
th {
  padding: 8px;
  border-bottom: 1px solid #e5e7eb;
  text-align: left;
  white-space: nowrap;
}
.diagnostic-warning {
  color: #a15d16;
}
.fingerprint {
  overflow-wrap: anywhere;
  font-size: 12px;
}
</style>
