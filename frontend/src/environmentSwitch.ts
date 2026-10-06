import { ApiError, request } from "./api";

export type EnvironmentStatus = {
  active: "testnet" | "mainnet";
  selected: "testnet" | "mainnet";
  restart_required: boolean;
  switch_available: boolean;
  automatic_switch_available?: boolean;
  switch_state?: string;
  switch_message?: string;
  switch_id?: string;
  switch_phase?: string;
  switch_elapsed_seconds?: number;
  recent_switches?: {
    id: string;
    state: string;
    target: string;
    message: string;
    failed_phase?: string;
    finished_at?: string;
  }[];
  testnet_credentials_configured?: boolean;
  mainnet_credentials_configured?: boolean;
};

export async function waitForEnvironment(
  token: string,
  target: string,
  update: (status: EnvironmentStatus) => void,
  submissionFailed = false,
  expectedId?: string,
  previousId?: string,
) {
  window.dispatchEvent(
    new CustomEvent("bybit-environment-switching", { detail: target }),
  );
  const deadline = Date.now() + 180000;
  const started = Date.now();
  while (Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 750));
    let status: EnvironmentStatus;
    try {
      status = await request<EnvironmentStatus>(
        "/bybit-environment",
        token,
        undefined,
        "GET",
        3000,
      );
    } catch {
      window.dispatchEvent(
        new CustomEvent("bybit-environment-progress", {
          detail: {
            switch_message: "后台正在重新连接，正在确认切换结果",
            switch_elapsed_seconds: Math.floor((Date.now() - started) / 1000),
          },
        }),
      );
      continue;
    }
    if (expectedId && status.switch_id !== expectedId) continue;
    if (
      !expectedId &&
      previousId &&
      status.switch_id === previousId &&
      status.switch_state === "failed" &&
      !submissionFailed
    )
      continue;
    update(status);
    if (
      submissionFailed &&
      status.active !== target &&
      status.selected !== target &&
      !["pending", "switching"].includes(status.switch_state ?? "")
    )
      throw new Error("切换请求未生效，仍保留原环境，请重试或检查后台日志");
    window.dispatchEvent(
      new CustomEvent("bybit-environment-progress", { detail: status }),
    );
    if (status.switch_state === "failed")
      throw new Error(status.switch_message || "环境切换失败，已恢复原环境");
    if (
      status.active === target &&
      !status.restart_required &&
      !["pending", "switching"].includes(status.switch_state ?? "")
    ) {
      window.dispatchEvent(new Event("bybit-environment-changed"));
      return status;
    }
  }
  throw new Error("切换耗时较长，请刷新查看后台状态；不要启动交易");
}

export async function changeEnvironment(
  token: string,
  target: string,
  update: (status: EnvironmentStatus) => void,
  previousId?: string,
) {
  let status: EnvironmentStatus;
  window.dispatchEvent(
    new CustomEvent("bybit-environment-switching", { detail: target }),
  );
  try {
    status = await request<EnvironmentStatus>("/bybit-environment", token, {
      environment: target,
    });
  } catch (error) {
    if (error instanceof ApiError && error.status < 500) throw error;
    return waitForEnvironment(
      token,
      target,
      update,
      error instanceof ApiError,
      undefined,
      previousId,
    );
  }
  update(status);
  if (
    ["pending", "switching"].includes(status.switch_state ?? "") ||
    (status.automatic_switch_available && status.restart_required)
  )
    return waitForEnvironment(
      token,
      target,
      update,
      false,
      status.switch_id,
      previousId,
    );
  if (status.automatic_switch_available && !status.restart_required)
    window.dispatchEvent(new Event("bybit-environment-changed"));
  else {
    window.dispatchEvent(new Event("bybit-environment-switch-failed"));
    window.dispatchEvent(new Event("bybit-environment-saved"));
  }
  return status;
}
