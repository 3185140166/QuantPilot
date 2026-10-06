<script setup lang="ts">
import { ElAlert } from "element-plus/es/components/alert/index.mjs";
import { ElButton } from "element-plus/es/components/button/index.mjs";
import { ElInput } from "element-plus/es/components/input/index.mjs";
import { ElPagination } from "element-plus/es/components/pagination/index.mjs";
import { ElRadioButton } from "element-plus/es/components/radio/index.mjs";
import { ElRadioGroup } from "element-plus/es/components/radio/index.mjs";
import { ElTable } from "element-plus/es/components/table/index.mjs";
import { ElTableColumn } from "element-plus/es/components/table/index.mjs";
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { ElMessage } from "element-plus/es/components/message/index.mjs";
import { Refresh, Search, Star, StarFilled } from "@element-plus/icons-vue";
import { request } from "../api";
import type { Ticker, TickerSnapshot, WatchlistEntry } from "../types";

const props = defineProps<{
  exchange: string;
  token: string;
  selectedSymbol: string;
}>();
const emit = defineEmits<{ select: [symbol: string] }>();
const snapshot = ref<TickerSnapshot>();
const favorites = ref<Set<string>>(new Set());
const search = ref("");
const tab = ref("all");
const loading = ref(false);
const error = ref("");
const pending = ref<Set<string>>(new Set());
const page = ref(1);
const pageSize = ref(50);
const requestMs = ref<number>();
const sort = ref({ prop: "turnover_24h", order: "descending" });
let timer: number | undefined;
let sequence = 0;
let disposed = false;
const rowClass = ({ row }: { row: Ticker }) =>
  row.symbol === props.selectedSymbol ? "selected-market-row" : "";
const rows = computed(() =>
  (snapshot.value?.tickers ?? []).filter(
    (row) =>
      (tab.value !== "favorites" || favorites.value.has(row.symbol)) &&
      row.symbol.includes(search.value.trim().toUpperCase()),
  ),
);
const watchlistMissing = computed(() =>
  [...favorites.value].filter(
    (symbol) => !snapshot.value?.tickers.some((row) => row.symbol === symbol),
  ),
);
const sortedRows = computed(() => {
  const direction = sort.value.order === "ascending" ? 1 : -1;
  return [...rows.value].sort((a, b) => {
    const key = sort.value.prop as keyof Ticker;
    const left = a[key],
      right = b[key];
    return (
      direction *
      (typeof left === "number" && typeof right === "number"
        ? left - right
        : String(left).localeCompare(String(right)))
    );
  });
});
const visibleRows = computed(() =>
  sortedRows.value.slice(
    (page.value - 1) * pageSize.value,
    page.value * pageSize.value,
  ),
);
function changeSort(value: { prop: string | null; order: string | null }) {
  sort.value =
    value.order && value.prop
      ? { prop: value.prop, order: value.order }
      : { prop: "turnover_24h", order: "descending" };
  page.value = 1;
}
watch([search, tab, pageSize, () => props.exchange], () => {
  page.value = 1;
});
watch(
  () => rows.value.length,
  (count) => {
    page.value = Math.min(
      page.value,
      Math.max(1, Math.ceil(count / pageSize.value)),
    );
  },
);
const asOf = computed(() =>
  snapshot.value
    ? new Date(snapshot.value.fetched_at).toLocaleTimeString("zh-CN", {
        hour12: false,
      })
    : "",
);
const price = (value: number) =>
  value.toLocaleString("zh-CN", {
    minimumFractionDigits: value < 1 ? 4 : 2,
    maximumFractionDigits: value < 1 ? 8 : 4,
  });
const amount = (value: number) =>
  value >= 100000000
    ? `${(value / 100000000).toFixed(2)} 亿`
    : value >= 10000
      ? `${(value / 10000).toFixed(2)} 万`
      : value.toLocaleString("zh-CN", { maximumFractionDigits: 2 });

async function refresh(withWatchlist = false) {
  const current = ++sequence;
  const exchange = props.exchange;
  const token = props.token;
  loading.value = true;
  error.value = "";
  const started = performance.now();
  try {
    const [data, saved] = await Promise.all([
      request<TickerSnapshot>(`/tickers?exchange=${exchange}`, token),
      withWatchlist
        ? request<WatchlistEntry[]>(`/watchlist?exchange=${exchange}`, token)
        : Promise.resolve(undefined),
    ]);
    if (current !== sequence || disposed) return;
    snapshot.value = data;
    requestMs.value = Math.round(performance.now() - started);
    if (saved) favorites.value = new Set(saved.map((entry) => entry.symbol));
  } catch (err) {
    if (current === sequence && !disposed)
      error.value = err instanceof Error ? err.message : "行情获取失败，请重试";
  } finally {
    if (current === sequence && !disposed) loading.value = false;
  }
}

async function toggleFavorite(symbol: string) {
  const exchange = props.exchange;
  const token = props.token;
  const removing = favorites.value.has(symbol);
  pending.value = new Set([...pending.value, symbol]);
  try {
    if (removing)
      await request(
        `/watchlist/${exchange}/${symbol}`,
        token,
        undefined,
        "DELETE",
      );
    else await request("/watchlist", token, { exchange, symbol });
    if (exchange !== props.exchange || disposed) return;
    const next = new Set(favorites.value);
    if (removing) next.delete(symbol);
    else next.add(symbol);
    favorites.value = next;
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : "自选保存失败");
  } finally {
    const next = new Set(pending.value);
    next.delete(symbol);
    pending.value = next;
  }
}

watch(
  () => props.exchange,
  () => {
    sequence++;
    snapshot.value = undefined;
    favorites.value = new Set();
    pending.value = new Set();
    search.value = "";
    void refresh(true);
  },
);
onMounted(() => {
  void refresh(true);
  timer = window.setInterval(() => {
    if (!loading.value && !document.hidden) void refresh();
  }, 15000);
});
onBeforeUnmount(() => {
  disposed = true;
  sequence++;
  window.clearInterval(timer);
});
defineExpose({ refresh });
</script>

<template>
  <section class="panel market-board">
    <div class="panel-heading">
      <div>
        <h3>多币种行情</h3>
        <span
          >{{ exchange.toUpperCase() }} · USDT 永续合约 ·
          {{ snapshot?.tickers.length ?? 0 }} 个交易对</span
        >
      </div>
      <el-button :icon="Refresh" :loading="loading" @click="refresh(true)"
        >刷新行情</el-button
      >
    </div>
    <div class="market-list-toolbar">
      <el-radio-group v-model="tab"
        ><el-radio-button value="all">全部市场</el-radio-button
        ><el-radio-button value="favorites"
          >我的自选（{{ favorites.size }}）</el-radio-button
        ></el-radio-group
      ><el-input
        v-model="search"
        :prefix-icon="Search"
        clearable
        placeholder="搜索币种，例如 BTC、DOGE"
        aria-label="搜索币种"
      />
    </div>
    <el-alert
      v-if="error"
      :title="
        snapshot ? `${error}；下方保留上次快照，请以获取时间为准。` : error
      "
      type="error"
      :closable="false"
      class="mb"
    />
    <el-alert
      v-if="tab === 'favorites' && watchlistMissing.length"
      title="部分自选当前没有可用行情，可移除后重新选择。"
      type="info"
      :closable="false"
      class="mb"
    />
    <div
      v-if="tab === 'favorites' && watchlistMissing.length"
      class="missing-favorites"
    >
      <el-button
        v-for="symbol in watchlistMissing"
        :key="symbol"
        size="small"
        :loading="pending.has(symbol)"
        @click="toggleFavorite(symbol)"
        >移除 {{ symbol }}</el-button
      >
    </div>
    <el-table
      :data="visibleRows"
      row-key="symbol"
      max-height="620"
      @sort-change="changeSort"
      :default-sort="{ prop: 'turnover_24h', order: 'descending' }"
      :row-class-name="rowClass"
      :empty-text="
        loading
          ? '正在加载行情…'
          : tab === 'favorites'
            ? '还没有自选，点击星标添加币种'
            : '没有匹配的交易对'
      "
    >
      <el-table-column label="自选" width="64"
        ><template #default="{ row }"
          ><el-button
            text
            :icon="favorites.has(row.symbol) ? StarFilled : Star"
            :class="{ 'favorite-star': favorites.has(row.symbol) }"
            :disabled="pending.has(row.symbol)"
            :aria-label="`${favorites.has(row.symbol) ? '移除自选' : '加入自选'} ${row.symbol}`"
            @click.stop="toggleFavorite(row.symbol)" /></template
      ></el-table-column>
      <el-table-column
        label="交易对"
        min-width="155"
        prop="symbol"
        sortable="custom"
        ><template #default="{ row }"
          ><button class="symbol-link" @click="emit('select', row.symbol)">
            {{ row.symbol }}<small>{{ row.base_coin }} / USDT</small>
          </button></template
        ></el-table-column
      >
      <el-table-column
        label="最新价（USDT）"
        min-width="150"
        prop="last_price"
        sortable="custom"
        ><template #default="{ row }">{{
          price(row.last_price)
        }}</template></el-table-column
      >
      <el-table-column
        label="24h 涨跌"
        min-width="125"
        prop="change_24h_pct"
        sortable="custom"
        ><template #default="{ row }"
          ><span :class="row.change_24h_pct >= 0 ? 'positive' : 'negative'"
            >{{ row.change_24h_pct >= 0 ? "+" : ""
            }}{{ row.change_24h_pct.toFixed(2) }}%</span
          ></template
        ></el-table-column
      >
      <el-table-column label="24h 最高 / 最低" min-width="180"
        ><template #default="{ row }"
          >{{ price(row.high_24h) }} / {{ price(row.low_24h) }}</template
        ></el-table-column
      >
      <el-table-column
        label="24h 成交额（USDT）"
        min-width="175"
        prop="turnover_24h"
        sortable="custom"
        ><template #default="{ row }">{{
          amount(row.turnover_24h)
        }}</template></el-table-column
      >
      <el-table-column label="详情" width="80" fixed="right"
        ><template #default="{ row }"
          ><el-button
            link
            type="primary"
            :aria-label="`查看 ${row.symbol}`"
            @click="emit('select', row.symbol)"
            >查看</el-button
          ></template
        ></el-table-column
      >
    </el-table>
    <div class="market-pagination">
      <span
        >匹配 {{ rows.length }} / 全市场
        {{ snapshot?.tickers.length ?? 0 }} 个交易对</span
      >
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :page-sizes="[20, 50, 100]"
        :total="rows.length"
        layout="sizes, prev, pager, next"
        :pager-count="5"
      />
    </div>
    <p class="panel-note">
      {{
        snapshot?.source === "demo"
          ? "演示快照：合成价格与模拟 24 小时统计。"
          : "最新价格快照与已收盘 K 线价格可能不同。"
      }}
      获取时间 {{ asOf || "—" }} · 每 15 秒刷新 · 星标自选保存在数据库 ·
      请求耗时
      {{
        requestMs === undefined ? "—" : `${requestMs} ms`
      }}（含网络与服务端处理，非交易延迟）
    </p>
  </section>
</template>
