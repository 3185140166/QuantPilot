<script setup lang="ts">
import { ElAlert } from "element-plus/es/components/alert/index.mjs";
import { ElButton } from "element-plus/es/components/button/index.mjs";
import { onMounted, onBeforeUnmount, ref } from "vue";
import { request } from "../api";
const props = defineProps<{ token: string }>();
const workerNames: Record<string, string> = {
  engine: "模拟引擎",
  research: "研究进程",
  testnet: "测试网执行",
};
const serviceNames: Record<string, string> = {
  ...workerNames,
  api: "接口服务",
  frontend: "网页服务",
};
const serviceStates: Record<string, string> = {
  running: "运行中",
  starting: "启动中",
  waiting: "等待重启",
  failed: "需要检查",
};
type Health = {
  testnet_acceptance?: {
    passed: boolean;
    legacy?: boolean;
    untested: string[];
    checks: { name: string; passed: boolean }[];
  } | null;
  alerts: { code: string; severity: string; message: string }[];
  workers: { name: string; online: boolean }[];
  backup: {
    created_at?: string;
    verified?: boolean;
    message?: string;
    directory?: string;
  };
  supervisor: {
    services?: { name: string; state: string; restart_count: number }[];
  };
};
const health = ref<Health>();
const error = ref("");
const checking = ref(false);
const feedback = ref("");
let timer: number | undefined;
let disposed = false;
let loading = false;
async function load() {
  if (loading) return;
  loading = true;
  try {
    const value = await request<Health>("/system/status", props.token);
    if (!disposed) {
      health.value = value;
      error.value = "";
    }
  } catch (e) {
    if (!disposed) error.value = (e as Error).message;
  } finally {
    loading = false;
  }
}
async function verify() {
  checking.value = true;
  feedback.value = "";
  try {
    await request("/system/backups/verify", props.token, {});
    feedback.value = "备份文件和数据库完整性校验通过";
  } catch (e) {
    feedback.value = (e as Error).message;
  } finally {
    checking.value = false;
  }
}
onMounted(() => {
  void load();
  timer = window.setInterval(load, 15000);
});
onBeforeUnmount(() => {
  disposed = true;
  window.clearInterval(timer);
});
</script>
<template>
  <section class="panel">
    <details>
      <summary>
        运行状态与备份 ·
        {{
          health
            ? health.alerts.length
              ? `${health.alerts.length} 项提醒`
              : "检查正常"
            : "正在检查"
        }}
      </summary>
      <p v-if="error" class="panel-note">
        {{ error }}；新增状态入口需要重启后端服务。
      </p>
      <template v-if="health">
        <details>
          <summary>测试网交易验收记录</summary>
          <template v-if="health.testnet_acceptance"
            ><p>
              {{
                health.testnet_acceptance.passed
                  ? "基础小额验收通过"
                  : "验收未完成或存在失败项"
              }}；这不代表全部交易场景已验证。
            </p>
            <p>
              通过
              {{
                health.testnet_acceptance.checks.filter((c) => c.passed).length
              }}
              / {{ health.testnet_acceptance.checks.length }} 项基础检查。
            </p>
            <p>
              仍未验证：{{ health.testnet_acceptance.untested.join("、") }}
            </p></template
          >
          <p v-else>
            尚无新版测试网验收报告。验收工具需要助手暂停、账户空仓且没有未确认订单；页面查看记录不会提交订单。
          </p>
        </details>
        <el-alert
          v-for="item in health.alerts"
          :key="item.code"
          :title="item.message"
          :type="item.severity === 'error' ? 'error' : 'warning'"
          :closable="false"
          show-icon
        />
        <p class="panel-note">
          {{
            health.workers
              .map(
                (w) =>
                  `${workerNames[w.name] ?? w.name}：${w.online ? "在线" : "离线"}`,
              )
              .join("；")
          }}
        </p>
        <p class="panel-note">
          最近备份：{{
            health.backup.created_at
              ? new Date(health.backup.created_at).toLocaleString()
              : "尚无记录，请通过统一启动器运行"
          }}。{{ health.backup.message }}
        </p>
        <p class="panel-note">
          {{
            health.supervisor.services
              ?.map(
                (s) =>
                  `${serviceNames[s.name] ?? s.name}：${serviceStates[s.state] ?? s.state}，重启${s.restart_count}次`,
              )
              .join("；")
          }}
        </p>
        <el-button
          :disabled="!health.backup.directory"
          :loading="checking"
          @click="verify"
          >校验最近备份</el-button
        >
        <p class="panel-note">
          {{
            feedback ||
            "校验只检查备份完整性，不覆盖当前数据库。提醒显示在本页面。"
          }}
        </p>
      </template>
    </details>
  </section>
</template>
