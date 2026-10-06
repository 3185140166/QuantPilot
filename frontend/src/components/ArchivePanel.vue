<script setup lang="ts">
import { ElAlert } from "element-plus/es/components/alert/index.mjs";
import { ElButton } from "element-plus/es/components/button/index.mjs";
import { ElForm } from "element-plus/es/components/form/index.mjs";
import { ElFormItem } from "element-plus/es/components/form/index.mjs";
import { ElInputNumber } from "element-plus/es/components/input-number/index.mjs";
import { computed, ref, watch } from "vue";
import { request } from "../api";

const props = defineProps<{ token: string }>();
const emit = defineEmits<{ archived: [] }>();
type Preview = {
  id: string;
  cutoff: string;
  counts: Record<string, number>;
  batch_limit: number;
};
const days = ref(90);
const plan = ref<Preview>();
const busy = ref(false);
const error = ref("");
const completed = ref("");
const total = computed(() =>
  Object.values(plan.value?.counts ?? {}).reduce(
    (sum, value) => sum + value,
    0,
  ),
);
const labels: Record<string, string> = {
  equity: "权益记录",
  events: "操作与异常记录",
  backtests: "回测明细",
  optimizations: "优化明细",
};
watch(days, () => {
  plan.value = undefined;
  completed.value = "";
});

async function preview() {
  busy.value = true;
  error.value = "";
  completed.value = "";
  plan.value = undefined;
  try {
    plan.value = await request<Preview>(
      "/maintenance/archive/preview",
      props.token,
      { retention_days: days.value },
    );
  } catch (err) {
    error.value = err instanceof Error ? err.message : "预览失败";
  } finally {
    busy.value = false;
  }
}
async function execute() {
  if (!plan.value || !total.value) return;
  busy.value = true;
  error.value = "";
  try {
    const result = await request<{ manifest: string }>(
      "/maintenance/archive/execute",
      props.token,
      { id: plan.value.id },
    );
    completed.value = `归档完成，校验清单保存在 ${result.manifest}`;
    plan.value = undefined;
    emit("archived");
  } catch (err) {
    error.value = err instanceof Error ? err.message : "归档失败";
    plan.value = undefined;
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <section class="panel">
    <div class="panel-heading"><h3>历史数据归档</h3></div>
    <p class="panel-note">
      把较早明细保存到本机归档文件，校验后释放数据库空间。策略、持仓、订单、全程最大回撤和行情样本保留；已归档回测与优化结果仍可查看。
    </p>
    <el-form inline>
      <el-form-item label="保留最近天数"
        ><el-input-number v-model="days" :min="30" :max="3650" :disabled="busy"
      /></el-form-item>
      <el-form-item
        ><el-button :loading="busy" @click="preview"
          >预览归档</el-button
        ></el-form-item
      >
    </el-form>
    <div v-if="plan">
      <p>
        归档 {{ new Date(plan.cutoff).toLocaleString("zh-CN") }} 之前的明细：
      </p>
      <p v-for="(count, kind) in plan.counts" :key="kind">
        {{ labels[kind] }}：{{ count }} 条
      </p>
      <p class="panel-note">
        保留每个策略的最新权益点；运行中任务不会归档。每次分批处理，预览一小时内有效。
      </p>
      <el-button
        type="primary"
        :disabled="!total || busy"
        :loading="busy"
        @click="execute"
        >确认归档 {{ total }} 条明细</el-button
      >
      <p v-if="!total">当前没有需要归档的明细。</p>
    </div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <el-alert
      v-if="completed"
      :title="completed"
      type="success"
      :closable="false"
    />
    <p class="panel-note">
      备份时请一起保存数据库、storage/archive 和 storage/market。
    </p>
  </section>
</template>
