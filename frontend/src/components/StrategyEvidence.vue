<script setup lang="ts">
export type Qualification = {
  enforced?: boolean;
  eligible: boolean;
  failed: string[];
  checks?: { name: string; passed: boolean; explanation: string }[];
  limits?: string;
};
export type Monitoring = {
  assumed_slippage_bps?: number;
  actual_entry_fee_rate?: number | null;
  assumed_fee_rate?: number;
  validation_return?: number | null;
  enabled: boolean;
  pause_new_entries: boolean;
  reasons: string[];
  completed_trades: number;
  consecutive_losses: number;
  observed_openings: number;
  adverse_slippage_bps: number | null;
  net_pnl: number;
  fees: number;
  funding: number;
  limitations: string[];
};
defineProps<{ qualification?: Qualification; monitor?: Monitoring }>();
</script>
<template>
  <section class="panel strategy-evidence">
    <h3>策略证据与运行监测</h3>
    <p v-if="qualification">
      研究准入（{{ qualification.enforced ? "已启用限制" : "仅展示诊断" }}）：{{
        qualification.eligible ? "已通过当前历史证据检查" : "尚未通过"
      }}。{{ qualification.failed.join("、") }}
    </p>
    <p v-else>尚未生成研究准入报告，请创建组合研究及后续验证。</p>
    <details v-if="qualification?.checks?.length">
      <summary>查看准入检查</summary>
      <p v-for="item in qualification.checks" :key="item.name">
        {{ item.passed ? "通过" : "未通过" }} · {{ item.name }}：{{
          item.explanation
        }}
      </p>
    </details>
    <p class="panel-note">
      历史证据不保证未来收益。启用“研究准入限制”后，未通过检查不能应用参数或启动新开仓。
    </p>
    <template v-if="monitor">
      <p>
        运行监测：{{ monitor.enabled ? "已启用暂停新开仓" : "仅展示诊断" }} ·
        已对账 {{ monitor.completed_trades }} 笔 · 连续亏损
        {{ monitor.consecutive_losses }} 笔
      </p>
      <p>
        本系统当前参数已结算净收益 {{ monitor.net_pnl.toFixed(4) }} USDT ·
        手续费 {{ monitor.fees.toFixed(4) }} · 资金费净收入
        {{ monitor.funding.toFixed(4) }}
      </p>
      <p>
        平均不利成交偏差
        {{
          monitor.adverse_slippage_bps === null
            ? "样本不足"
            : monitor.adverse_slippage_bps.toFixed(2) + " 基点"
        }}（{{ monitor.observed_openings }} 个有参考价的开仓）
      </p>
      <p v-if="monitor.actual_entry_fee_rate !== undefined">
        实际开仓费率
        {{
          monitor.actual_entry_fee_rate === null
            ? "样本不足"
            : (monitor.actual_entry_fee_rate * 100).toFixed(4) + "%"
        }}
        · 配置费率 {{ ((monitor.assumed_fee_rate ?? 0) * 100).toFixed(4) }}% ·
        配置滑点 {{ monitor.assumed_slippage_bps ?? "未记录" }} 基点。
      </p>
      <p v-if="monitor.validation_return != null" class="panel-note">
        关联验证区间历史收益
        {{
          (monitor.validation_return * 100).toFixed(2)
        }}%；运行期与验证期不同，不能直接按总收益比较，也不自动修改参数。
      </p>
      <p v-for="reason in monitor.reasons" :key="reason">{{ reason }}</p>
      <details>
        <summary>统计口径与限制</summary>
        <p v-for="item in monitor.limitations" :key="item">{{ item }}</p>
      </details>
    </template>
  </section>
</template>
