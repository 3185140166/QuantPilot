export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function request<T>(
  path: string,
  token: string,
  body?: unknown,
  method?: "GET" | "POST" | "DELETE",
): Promise<T> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 25000);
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
    if (error instanceof ApiError) throw error;
    if (error instanceof Error && error.name === "AbortError")
      throw new Error("请求超过 25 秒，请检查交易所网络连接后重试");
    throw new Error("连接失败，请确认接口服务已启动后重试");
  } finally {
    window.clearTimeout(timeout);
  }
}
