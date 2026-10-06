<script setup lang="ts">
import type { FactorReport } from "../types";
defineProps<{ report: FactorReport }>();
const number = (value: number | null) =>
  value === null ? "样本不足／无变动" : value.toFixed(3);
const percent = (value: number | null) =>
  value === null ? "—" : `${(value * 100).toFixed(3)}%`;
</script>
<template>
  <details class="factor-diagnostics">
    <summary>因子诊断：历史关联与过滤条件对照</summary>
    <p class="panel-note">
      因子是可以从数据计算的市场特征。这里检验“因子大小与后续涨跌是否有历史关联”，不自动生成策略权重或修改交易参数。
    </p>
    <p v-if="report.split_at" class="panel-note">
      {{
        report.split_kind === "specified"
          ? "任务指定研究／验证切分"
          : "诊断内部70%研究／30%留出切分（不替代后续独立验证）"
      }}：{{
        report.split_at
      }}。每个观察期限隔同样根数取样，避免收益区间重叠；研究段剔除跨越切分的未来收益。
    </p>
    <p class="panel-note">
      秩相关（IC）接近0表示关联弱；正值表示因子越高，后续上涨倾向越强，负值表示方向相反。单币时间序列口径不等于多币横截面IC。方向和分组阈值只由研究段确定，不能因留出结果好看就改方向。
    </p>
    <p v-for="warning in report.warnings" :key="warning" class="panel-note">
      {{ warning }}
    </p>
    <div class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>因子</th>
            <th>持有K线数</th>
            <th>研究／留出样本</th>
            <th>研究IC</th>
            <th>留出IC</th>
            <th>尾部方向样本</th>
            <th>每次平均毛收益</th>
            <th>扣成本后均值</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in report.rows" :key="`${row.key}-${row.horizon}`">
            <td>{{ row.label }}</td>
            <td>{{ row.horizon }}</td>
            <td>{{ row.train_samples }} / {{ row.test_samples }}</td>
            <td>{{ number(row.train_ic) }}</td>
            <td>{{ number(row.test_ic) }}</td>
            <td>{{ row.tail_samples }}</td>
            <td>{{ percent(row.tail_gross_mean) }}</td>
            <td>{{ percent(row.tail_net_mean) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <p class="panel-note">
      尾部方向样本：用研究段最低20%、最高20%的固定阈值观察留出段多空收益；每次从信号之后的下一根开盘进入，持有指定根数。均值不是资金曲线或组合收益，尚未计资金费和强平。
    </p>
    <template v-if="report.redundancy.length"
      ><h4>可能重复的因子</h4>
      <p v-for="row in report.redundancy" :key="`${row.left}-${row.right}`">
        {{ row.left }} / {{ row.right }}：研究段秩相关
        {{
          number(row.correlation)
        }}。多个相似指标同时通过，不代表多份独立证据。
      </p></template
    >
    <template v-if="report.ablation?.length">
      <h4>逐项关闭过滤条件 · 同一留出段</h4>
      <p class="panel-note">
        相同成本和资金设置，每次只关闭一个已有过滤条件；比较收益、回撤和交易数。差异不能单独证明因子有效，也不会重新选择最优配置。
      </p>
      <div class="table-scroll">
        <table>
          <thead>
            <tr>
              <th>对照规则</th>
              <th>收益</th>
              <th>回撤</th>
              <th>交易数</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in report.ablation" :key="row.label">
              <td>{{ row.label }}</td>
              <td>{{ percent(row.metrics.total_return) }}</td>
              <td>{{ percent(row.metrics.max_drawdown) }}</td>
              <td>{{ row.metrics.trades }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
    <p v-else class="panel-note">
      此报告没有过滤条件对照：单币研究未开启成交量／ATR／ADX过滤时无需逐项关闭；组合报告目前只提供各币种因子诊断。
    </p>
    <p v-for="item in report.limitations" :key="item" class="panel-note">
      {{ item }}
    </p>
  </details>
</template>
<style scoped>
.factor-diagnostics {
  margin: 16px 0;
}
summary {
  cursor: pointer;
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
th,
td {
  padding: 8px;
  border-bottom: 1px solid var(--el-border-color);
  text-align: left;
  white-space: nowrap;
}
</style>
