<script setup lang="ts">
import { ElAlert } from "element-plus/es/components/alert/index.mjs";
import { ElButton } from "element-plus/es/components/button/index.mjs";
import { ElDialog } from "element-plus/es/components/dialog/index.mjs";
import { ElSwitch } from "element-plus/es/components/switch/index.mjs";
import { ElTag } from "element-plus/es/components/tag/index.mjs";
import { ElTooltip } from "element-plus/es/components/tooltip/index.mjs";
import { onBeforeUnmount, onMounted, ref } from "vue";
import { ElMessage } from "element-plus/es/components/message/index.mjs";
import { request } from "../api";
import BybitEnvironmentPanel from "./BybitEnvironmentPanel.vue";
import {
  changeEnvironment,
  waitForEnvironment,
  type EnvironmentStatus,
} from "../environmentSwitch";
const props = defineProps<{ token: string }>();
const mainnet = ref(false);
const busy = ref(true);
const details = ref(false);
const status = ref<EnvironmentStatus>();
const error = ref("");
async function load() {
  try {
    status.value = await request(
      "/bybit-environment",
      props.token,
      undefined,
      "GET",
      3000,
    );
    mainnet.value = status.value!.selected === "mainnet";
    error.value = "";
    if (["pending", "switching"].includes(status.value!.switch_state ?? "")) {
      busy.value = true;
      await waitForEnvironment(
        props.token,
        status.value!.selected,
        (value) => (status.value = value),
        false,
        status.value!.switch_id,
      );
    }
  } catch (err) {
    mainnet.value = status.value?.active === "mainnet";
    error.value =
      err instanceof Error
        ? err.message
        : "环境切换或读取未完成，请刷新查看后台状态";
    window.dispatchEvent(new Event("bybit-environment-switch-failed"));
  } finally {
    busy.value = false;
  }
}
async function save() {
  busy.value = true;
  try {
    status.value = await changeEnvironment(
      props.token,
      mainnet.value ? "mainnet" : "testnet",
      (value) => (status.value = value),
      status.value?.switch_id,
    );
    mainnet.value = status.value.selected === "mainnet";
    ElMessage.success(
      status.value.restart_required
        ? "旧启动器尚不支持自动切换，请更新启动器"
        : "环境已切换，自动交易保持暂停",
    );
  } catch (err) {
    mainnet.value = status.value?.active === "mainnet";
    window.dispatchEvent(new Event("bybit-environment-switch-failed"));
    ElMessage.error(err instanceof Error ? err.message : "环境保存失败");
  } finally {
    busy.value = false;
  }
}
onMounted(() => {
  void load();
  window.addEventListener("bybit-environment-saved", load);
});
onBeforeUnmount(() =>
  window.removeEventListener("bybit-environment-saved", load),
);
</script>
<template>
  <div class="header-environment">
    <el-tag
      v-if="status"
      size="small"
      :type="status.active === 'testnet' ? 'success' : 'warning'"
      >当前：{{ status.active === "testnet" ? "测试网" : "主网" }}</el-tag
    >
    <el-tooltip
      content="切换由后台自动完成，成功后更新当前环境；自动交易保持暂停。首次更新需通过新版统一启动器启动。"
    >
      <el-switch
        v-model="mainnet"
        aria-label="测试网与主网环境切换"
        inactive-text="测试网"
        active-text="主网"
        inline-prompt
        :loading="busy"
        :disabled="
          busy ||
          !status?.switch_available ||
          status?.automatic_switch_available === false
        "
        @change="save"
      />
    </el-tooltip>
    <el-button link @click="details = true">{{
      busy
        ? "切换中"
        : status?.automatic_switch_available === false
          ? "更新启动器"
          : status?.restart_required
            ? "待重启"
            : error
              ? "查看状态"
              : "设置"
    }}</el-button>
    <el-dialog v-model="details" title="交易环境设置" width="min(620px, 95vw)">
      <el-alert v-if="error" :title="error" type="warning" :closable="false" />
      <BybitEnvironmentPanel v-if="details" :token="token" @pending="load" />
      <p class="panel-note">
        主网使用真实资金，测试网使用测试币。切换完成后仍需在交易助手中手动启动；外部仓位与挂单不会被处理。
      </p>
    </el-dialog>
  </div>
</template>
