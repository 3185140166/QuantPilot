<script setup lang="ts">
import { ElTable } from "element-plus/es/components/table/index.mjs";
import { ElTableColumn } from "element-plus/es/components/table/index.mjs";
import { computed } from "vue";
export type Exposure = {
  groups: Record<
    string,
    {
      positions: number;
      long_notional: string;
      short_notional: string;
      stop_risk: string;
    }
  >;
  available_balance: string;
  reserve_amount: string;
  spendable_balance: string;
  reserved_external_notional: string;
  known_stop_risk: string;
  risk_limit: string;
  risk_complete: boolean;
  unprotected_symbols: string[];
  invalid_symbols: string[];
  reasons: string[];
  semantics: string;
};
const props = defineProps<{ report?: Exposure; fresh?: boolean }>();
const names: Record<string, string> = {
  system: "本系统",
  external: "手工／其他程序",
  unknown: "来源未知",
};
const rows = computed(() =>
  Object.entries(props.report?.groups ?? {}).map(([source, value]) => ({
    source: names[source] ?? source,
    ...value,
  })),
);
</script>
<template>
  <section v-if="report" class="panel account-exposure-panel">
    <h3>账户整体风险与可用资金</h3>
    <p class="panel-note">
      {{
        fresh
          ? "当前账户快照"
          : "快照未更新或已过期，以下仅供查看，不能作为开仓确认"
      }}
    </p>
    <p>
      可用余额 {{ report.available_balance }} USDT · 保留
      {{ report.reserve_amount }} USDT · 新开仓可用上限
      {{ report.spendable_balance }} USDT。
    </p>
    <p>
      已知止损风险 {{ report.known_stop_risk }} / 组合风险额度
      {{ report.risk_limit }} USDT。{{
        report.risk_complete
          ? "当前持仓都有可估计止损。"
          : "存在未保护或数据不完整持仓，这个风险合计不完整。"
      }}
    </p>
    <el-table :data="rows">
      <el-table-column prop="source" label="来源" min-width="150" />
      <el-table-column prop="positions" label="持仓数" width="90" />
      <el-table-column
        prop="long_notional"
        label="多头名义金额（USDT）"
        min-width="160"
      />
      <el-table-column
        prop="short_notional"
        label="空头名义金额（USDT）"
        min-width="160"
      />
      <el-table-column
        prop="stop_risk"
        label="已知止损风险（USDT）"
        min-width="160"
      />
    </el-table>
    <p class="panel-note">
      外部开仓挂单名义金额：{{
        report.reserved_external_notional
      }}
      USDT；不能据此推算可撤销或可动用资金。
    </p>
    <p v-if="report.unprotected_symbols.length" class="panel-note">
      无止损持仓：{{
        report.unprotected_symbols.join("、")
      }}。系统不会替你操作这些外部仓位。
    </p>
    <p v-for="reason in report.reasons" :key="reason" class="panel-note">
      {{ reason }}
    </p>
    <p class="panel-note">{{ report.semantics }}</p>
  </section>
</template>
