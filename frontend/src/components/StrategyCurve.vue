<script setup lang="ts">
import { ElAlert } from "element-plus/es/components/alert/index.mjs";
import { ElButton } from "element-plus/es/components/button/index.mjs";
import { ElEmpty } from "element-plus/es/components/empty/index.mjs";
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { request } from "../api";
import ChartPanel from "./ChartPanel.vue";

const props = defineProps<{ strategyId: number; token: string }>();
type Point = { id: number; timestamp: string; equity: string };
const points = ref<Point[]>([]);
const loading = ref(false);
const error = ref("");
let sequence = 0;
let pending = false;
let timer: number | undefined;

async function load(reset = false) {
  if (pending && !reset) return;
  const current = ++sequence;
  pending = true;
  if (reset) points.value = [];
  loading.value = points.value.length === 0;
  const cursor = reset ? 0 : (points.value.at(-1)?.id ?? 0);
  try {
    const data = await request<{ curve: Point[] }>(
      `/strategies/${props.strategyId}/curve?since_id=${cursor}`,
      props.token,
    );
    if (current !== sequence) return;
    const merged = new Map(points.value.map((point) => [point.id, point]));
    data.curve.forEach((point) => merged.set(point.id, point));
    points.value = [...merged.values()]
      .sort((a, b) => a.id - b.id)
      .slice(-1000);
    error.value = "";
  } catch (err) {
    if (current === sequence)
      error.value = err instanceof Error ? err.message : "权益曲线加载失败";
  } finally {
    if (current === sequence) {
      pending = false;
      loading.value = false;
    }
  }
}

const option = computed(() => ({
  tooltip: { trigger: "axis" },
  grid: { left: 65, right: 25, top: 30, bottom: 50 },
  xAxis: {
    type: "category",
    data: points.value.map((point) =>
      new Date(point.timestamp).toLocaleString("zh-CN", { hour12: false }),
    ),
  },
  yAxis: { type: "value", scale: true },
  series: [
    {
      type: "line",
      showSymbol: points.value.length <= 1,
      symbolSize: 8,
      sampling: "lttb",
      data: points.value.map((point) => Number(point.equity)),
      lineStyle: { color: "#138b78" },
    },
  ],
}));
watch(
  () => props.strategyId,
  () => void load(true),
);
onMounted(() => {
  void load(true);
  timer = window.setInterval(() => void load(), 5000);
});
onBeforeUnmount(() => {
  ++sequence;
  window.clearInterval(timer);
});
</script>

<template>
  <div v-loading="loading">
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <ChartPanel v-if="points.length" :option="option" :height="300" />
    <el-empty
      v-else-if="!loading && !error"
      description="启动后记录含浮动盈亏的模拟权益曲线"
      :image-size="60"
    />
    <el-button v-if="error" link @click="load(true)">重新加载曲线</el-button>
  </div>
</template>
