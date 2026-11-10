import { clearToken, getToken } from "./auth";

export class ApiError extends Error {
  status: number;
  code: string;
  details: Record<string, unknown>;

  constructor(status: number, code: string, message: string, details: Record<string, unknown>) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

const BASE = "";

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(`${BASE}${path}`, { ...init, headers });

  if (response.status === 401) {
    clearToken();
    if (!window.location.pathname.startsWith("/login")) {
      window.location.href = "/login";
    }
    throw new ApiError(401, "UNAUTHORIZED", "登录已失效，请重新登录", {});
  }

  if (response.status === 204) {
    return undefined as T;
  }

  const text = await response.text();
  const payload = text ? JSON.parse(text) : {};

  if (!response.ok) {
    const error = payload?.error ?? {};
    throw new ApiError(
      response.status,
      error.code ?? "UNKNOWN",
      error.message ?? "请求失败，请稍后重试",
      error.details ?? {},
    );
  }

  return payload as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) }),
  patch: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "PATCH", body: JSON.stringify(body) }),
  put: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "PUT", body: JSON.stringify(body) }),
  del: (path: string) => request<void>(path, { method: "DELETE" }),
  upload: <T>(path: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<T>(path, { method: "POST", body: form });
  },
};

/** 带鉴权地下载文件（直接 window.open 不会带 Authorization 头）。 */
export async function downloadFile(path: string, fallbackName: string): Promise<void> {
  const headers = new Headers();
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const response = await fetch(`${BASE}${path}`, { headers });
  if (!response.ok) throw new ApiError(response.status, "DOWNLOAD_FAILED", "导出失败", {});

  const disposition = response.headers.get("content-disposition") ?? "";
  const match = /filename\*=UTF-8''([^;]+)/i.exec(disposition);
  const filename = match ? decodeURIComponent(match[1]) : fallbackName;

  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}
