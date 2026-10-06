<script setup lang="ts">
import { ElTable } from "element-plus/es/components/table/index.mjs";
import { ElTableColumn } from "element-plus/es/components/table/index.mjs";
import { ElButton } from "element-plus/es/components/button/index.mjs";
import { ElDialog } from "element-plus/es/components/dialog/index.mjs";
import { ElAlert } from "element-plus/es/components/alert/index.mjs";
import { ref } from "vue";
import { request } from "../api";
const props = defineProps<{
  token: string;
  trades: Record<string, unknown>[];
  executions: Record<string, unknown>[];
  ledger: Record<string, unknown>[];
}>();
const replay = ref<{
  context: {
    bar_time?: string;
    configuration?: {
      budget: number;
      interval: string;
      rules: { strategy_type: string };
    };
    decision?: {
      signal?: string;
      reason?: string;
      signal_basis?: string;
      planned_loss?: string;
      quantity?: string;
      stop_loss?: string;
    };
  } | null;
  fills: Record<string, unknown>[];
  orders: Record<string, unknown>[];
}>();
const replayError = ref("");
const replayBusy = ref(false);
const replayVisible = ref(false);
async function openReplay(link: string) {
  replayVisible.value = true;
  replayBusy.value = true;
  replayError.value = "";
  replay.value = undefined;
  try {
    replay.value = await request(
      `/trading/replay/${encodeURIComponent(link)}`,
      props.token,
    );
  } catch (e) {
    replayError.value = (e as Error).message;
  } finally {
    replayBusy.value = false;
  }
}
const amount = (value: unknown) => Number(value ?? 0).toFixed(4);
const pnl = (row: Record<string, unknown>) =>
  row.net_pnl == null ? "待核查" : amount(row.net_pnl);
const stamp = (value: unknown) => new Date(Number(value)).toLocaleString();
</script>
<template>
  <section class="panel">
    <div class="panel-heading"><h3>交易周期与净盈亏</h3></div>
    <el-table :data="trades">
      <el-table-column prop="symbol" label="币种" />
      <el-table-column prop="entry_price" label="开仓均价" />
      <el-table-column prop="exit_price" label="平仓均价" />
      <el-table-column prop="closed_quantity" label="已平数量" />
      <el-table-column label="费用"
        ><template #default="{ row }">{{
          amount(row.fees)
        }}</template></el-table-column
      >
      <el-table-column label="资金费"
        ><template #default="{ row }">{{
          amount(row.funding)
        }}</template></el-table-column
      >
      <el-table-column label="净盈亏" min-width="110"
        ><template #default="{ row }">{{ pnl(row) }}</template></el-table-column
      >
      <el-table-column label="状态"
        ><template #default="{ row }">{{
          row.reconciled === false
            ? "待核查"
            : row.complete
              ? "已平仓"
              : "持仓／部分平仓"
        }}</template></el-table-column
      >
      <el-table-column prop="exit_reason" label="退出原因" min-width="170" />
      <el-table-column label="回放" width="110"
        ><template #default="{ row }"
          ><el-button
            :disabled="!row.opening_link_id || replayBusy"
            @click="openReplay(String(row.opening_link_id))"
            >查看回放</el-button
          ></template
        ></el-table-column
      >
    </el-table>
    <p class="panel-note">
      按实际成交顺序更新剩余持仓成本，并在交易所盈亏流水齐全时核对已实现盈亏。净盈亏扣除开平仓费并计入资金费；浮盈亏另见账户权益。成交顺序或仓位归属无法确认时显示待核查。
    </p>
  </section>
  <section class="panel">
    <div class="panel-heading"><h3>交易所逐笔成交</h3></div>
    <el-table :data="executions">
      <el-table-column prop="symbol" label="币种" />
      <el-table-column prop="side" label="方向" />
      <el-table-column prop="quantity" label="数量" />
      <el-table-column prop="price" label="成交价" />
      <el-table-column label="手续费"
        ><template #default="{ row }">{{
          amount(row.fee)
        }}</template></el-table-column
      >
      <el-table-column label="归属"
        ><template #default="{ row }">{{
          row.owned ? "本系统" : "外部／待核查"
        }}</template></el-table-column
      >
      <el-table-column prop="exit_reason" label="退出原因" min-width="150" />
      <el-table-column label="时间" min-width="180"
        ><template #default="{ row }">{{
          stamp(row.at_ms)
        }}</template></el-table-column
      >
    </el-table>
  </section>
  <section class="panel">
    <div class="panel-heading"><h3>资金流水（USDT）</h3></div>
    <el-table :data="ledger">
      <el-table-column prop="kind" label="类型" min-width="150" />
      <el-table-column prop="symbol" label="币种" />
      <el-table-column label="余额变化"
        ><template #default="{ row }">{{
          amount(row.change)
        }}</template></el-table-column
      >
      <el-table-column label="手续费"
        ><template #default="{ row }">{{
          amount(row.fee)
        }}</template></el-table-column
      >
      <el-table-column label="资金费"
        ><template #default="{ row }">{{
          amount(row.funding)
        }}</template></el-table-column
      >
      <el-table-column label="时间" min-width="180"
        ><template #default="{ row }">{{
          stamp(row.at_ms)
        }}</template></el-table-column
      >
    </el-table>
    <p class="panel-note">
      TRANSFER_IN／TRANSFER_OUT 是划转，SETTLEMENT
      是资金费结算；划转不计入交易收益。页面展示最近 100
      条，历史记录保留在数据库。
    </p>
  </section>
  <el-dialog v-model="replayVisible" title="交易决策与实际成交回放" width="90%">
    <p v-if="replayBusy">正在读取本地记录…</p>
    <el-alert
      v-if="replayError"
      :title="replayError"
      type="error"
      :closable="false"
    />
    <template v-if="replay">
      <p class="panel-note">
        按此开仓记录关联实际成交；开仓时的配置快照不会随后续配置修改。交易所原生保护退出的原因以成交记录为准。
      </p>
      <details open>
        <summary>开仓决策与风险参数</summary>
        <template v-if="replay.context">
          <p>
            信号时间：{{ replay.context.bar_time }} · 方向：{{
              replay.context.decision?.signal === "LONG"
                ? "做多"
                : replay.context.decision?.signal === "SHORT"
                  ? "做空"
                  : "未记录"
            }}
          </p>
          <p>
            开仓依据：{{
              replay.context.decision?.signal_basis ||
              replay.context.decision?.reason ||
              "未记录"
            }}
          </p>
          <p>
            共享预算：{{ replay.context.configuration?.budget }} USDT · 周期：{{
              replay.context.configuration?.interval
            }}
            · 计划风险：{{ replay.context.decision?.planned_loss ?? "未记录" }}
            USDT
          </p>
          <p>
            计划数量：{{ replay.context.decision?.quantity ?? "未记录" }} ·
            止损价格：{{ replay.context.decision?.stop_loss ?? "未记录" }}
          </p>
          <details>
            <summary>完整参数快照</summary>
            <pre>{{ JSON.stringify(replay.context, null, 2) }}</pre>
          </details>
        </template>
        <p v-else>旧订单未记录决策快照，无法还原当时的信号依据。</p>
      </details>
      <el-table :data="replay.fills"
        ><el-table-column prop="symbol" label="币种" /><el-table-column
          prop="side"
          label="方向"
        /><el-table-column prop="quantity" label="数量" /><el-table-column
          prop="price"
          label="成交价格"
        /><el-table-column prop="fee" label="手续费" /><el-table-column
          prop="exit_reason"
          label="退出原因"
        /><el-table-column label="时间"
          ><template #default="{ row }">{{
            stamp(row.at_ms)
          }}</template></el-table-column
        ></el-table
      >
      <details>
        <summary>本系统提交记录</summary>
        <pre>{{ JSON.stringify(replay.orders, null, 2) }}</pre>
      </details>
    </template>
  </el-dialog>
</template>
<style scoped>
pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
