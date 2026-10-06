export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

let switching = false;
const resumed = new Set<() => void>();
window.addEventListener("bybit-environment-switching", () => {
  switching = true;
});
function resume() {
  switching = false;
  for (const resolve of resumed) resolve();
  resumed.clear();
}
window.addEventListener("bybit-environment-switch-failed", resume);
window.addEventListener("bybit-environment-saved", resume);
function waitUntilResumed() {
  return new Promise<void>((resolve) => resumed.add(resolve));
}

export async function request<T>(
  path: string,
  token: string,
  body?: unknown,
  method?: "GET" | "POST" | "DELETE",
  timeoutMs = 25000,
): Promise<T> {
  const readOnly = (method ?? (body === undefined ? "GET" : "POST")) === "GET";
  const environmentRequest = path === "/bybit-environment";
  if (switching && !environmentRequest) {
    if (!readOnly) throw new ApiError(409, "环境正在切换，请等待完成后再操作");
    await waitUntilResumed();
  }
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`/api${path}`, {
      method: method ?? (body === undefined ? "GET" : "POST"),
      signal: controller.signal,
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new ApiError(
        response.status,
        typeof data.detail === "string"
          ? data.detail
          : Array.isArray(data.detail)
            ? data.detail
                .map((item: { msg?: string }) =>
                  item.msg?.replace(/^Value error, /, ""),
                )
                .filter(Boolean)
                .slice(0, 3)
                .join("；") || "输入参数无效"
            : `请求失败（${response.status}），请检查输入参数`,
      );
    }
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  } catch (error) {
    if (switching && !environmentRequest && readOnly) {
      window.clearTimeout(timeout);
      await waitUntilResumed();
      return request<T>(path, token, body, method, timeoutMs);
    }
    if (error instanceof ApiError) throw error;
    if (error instanceof Error && error.name === "AbortError")
      throw new Error(
        `请求超过 ${Math.ceil(timeoutMs / 1000)} 秒，请检查服务或网络连接后重试`,
      );
    throw new Error("连接失败，请确认接口服务已启动后重试");
  } finally {
    window.clearTimeout(timeout);
  }
}
