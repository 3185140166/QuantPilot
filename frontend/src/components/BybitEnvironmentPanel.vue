<script setup lang="ts">
import { ElAlert } from "element-plus/es/components/alert/index.mjs";
import { ElButton } from "element-plus/es/components/button/index.mjs";
import { ElForm } from "element-plus/es/components/form/index.mjs";
import { ElFormItem } from "element-plus/es/components/form/index.mjs";
import { ElOption } from "element-plus/es/components/select/index.mjs";
import { ElSelect } from "element-plus/es/components/select/index.mjs";
import { ElTag } from "element-plus/es/components/tag/index.mjs";
import { onMounted, ref } from "vue";
import { request } from "../api";
import {
  changeEnvironment,
  type EnvironmentStatus,
} from "../environmentSwitch";

const props = defineProps<{ token: string }>();
const emit = defineEmits<{ pending: [] }>();
type Status = EnvironmentStatus;
const status = ref<Status>();
const selected = ref<"testnet" | "mainnet">("testnet");
const busy = ref(false);
const error = ref("");
const label = (value: string) => (value === "testnet" ? "测试网" : "主网");
async function load() {
  busy.value = true;
  error.value = "";
  try {
    status.value = await request<Status>("/bybit-environment", props.token);
    selected.value = status.value.selected;
  } catch (err) {
    error.value = err instanceof Error ? err.message : "环境读取失败";
  } finally {
    busy.value = false;
  }
}
async function save() {
  busy.value = true;
  error.value = "";
  try {
    status.value = await changeEnvironment(
      props.token,
      selected.value,
      (value) => (status.value = value),
      status.value?.switch_id,
    );
    emit("pending");
    window.dispatchEvent(new Event("bybit-environment-saved"));
  } catch (err) {
    error.value = err instanceof Error ? err.message : "环境保存失败";
    selected.value = status.value?.active ?? "testnet";
    window.dispatchEvent(new Event("bybit-environment-switch-failed"));
  } finally {
    busy.value = false;
  }
}
onMounted(load);
</script>

<template>
  <section class="panel bybit-environment-panel">
    <div class="panel-heading">
      <h3>Bybit 主网 / 测试网</h3>
      <el-tag
        v-if="status"
        :type="status.active === 'testnet' ? 'success' : 'warning'"
        >当前：{{ label(status.active) }}</el-tag
      >
    </div>
    <p class="panel-note">
      测试网与主网密钥分别保存，两个环境使用独立数据库。新版统一启动器会自动更新后台服务，完成后显示当前环境；自动交易保持暂停，外部仓位与订单不会被处理。
    </p>
    <details v-if="status?.recent_switches?.length" class="panel-note">
      <summary>最近切换结果与失败原因</summary>
      <p v-for="item in status.recent_switches" :key="item.id">
        {{
          item.finished_at ? new Date(item.finished_at).toLocaleString() : ""
        }}
        · {{ label(item.target) }}：{{ item.message }}
      </p>
    </details>
    <el-form inline>
      <el-form-item label="选择环境"
        ><el-select
          v-model="selected"
          aria-label="Bybit 环境"
          style="width: 180px"
          :disabled="
            busy ||
            !status?.switch_available ||
            status?.automatic_switch_available === false
          "
          ><el-option label="测试网（默认）" value="testnet" /><el-option
            label="主网"
            value="mainnet" /></el-select
      ></el-form-item>
      <el-form-item
        ><el-button
          type="primary"
          :loading="busy"
          :disabled="
            !status?.switch_available ||
            status?.automatic_switch_available === false
          "
          @click="save"
          >{{
            status?.automatic_switch_available
              ? "切换环境"
              : "保存环境，重启后生效"
          }}</el-button
        ></el-form-item
      >
    </el-form>
    <p v-if="status" class="panel-note">
      测试网密钥：{{
        status.testnet_credentials_configured ? "已填写" : "待填写"
      }}
      · 主网密钥：{{
        status.mainnet_credentials_configured ? "已填写" : "待填写"
      }}。这里只检查是否填写，不代表连接验证成功。
    </p>
    <el-alert
      v-if="status?.restart_required && !status.automatic_switch_available"
      :title="`已保存${label(status.selected)}，当前仍为${label(status.active)}。请停止并重新启动平台；新环境的历史记录单独保存。`"
      type="warning"
      :closable="false"
    />
    <el-alert
      v-if="
        status?.automatic_switch_available === false && status.switch_available
      "
      title="请通过新版统一启动器启动一次，以加载自动环境切换功能；之后无需手动重启切换。"
      type="info"
      :closable="false"
    />
    <el-alert
      v-if="status && !status.switch_available"
      title="当前部署请通过配置文件及独立数据库切换环境，网页切换仅支持本地 SQLite。"
      type="info"
      :closable="false"
    />
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <el-button v-if="error" link @click="load">重新读取配置</el-button>
  </section>
</template>
