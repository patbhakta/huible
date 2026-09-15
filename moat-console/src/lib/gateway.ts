import { TENANTS, type TenantConfig } from "./tenants";

export const GATEWAY_BASE = process.env.HUIBLE_WM_GATEWAY ?? "http://127.0.0.1:8420";
export const GATEWAY_API_KEY = process.env.HUIBLE_WM_GATEWAY_KEY ?? "";

async function gatewayCall<T>(path: string, body: unknown, timeoutMs = 15_000): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(GATEWAY_BASE + path, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(GATEWAY_API_KEY ? { Authorization: `Bearer ${GATEWAY_API_KEY}` } : {}),
      },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    const text = await res.text();
    let parsed: unknown;
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = { raw: text.slice(0, 2000) };
    }
    if (!res.ok) {
      throw Object.assign(new Error(`gateway ${res.status}`), { status: res.status, payload: parsed });
    }
    return parsed as T;
  } finally {
    clearTimeout(timer);
  }
}

async function tenantGatewayCall<T>(tenant: TenantConfig, path: string, body: unknown, timeoutMs = 15_000): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(GATEWAY_BASE + path, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-tdai-service-id": tenant.serviceId,
        ...(GATEWAY_API_KEY ? { Authorization: `Bearer ${GATEWAY_API_KEY}` } : {}),
      },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    const text = await res.text();
    let parsed: unknown;
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = { raw: text.slice(0, 2000) };
    }
    if (!res.ok) {
      throw Object.assign(new Error(`gateway ${res.status}`), { status: res.status, payload: parsed });
    }
    return parsed as T;
  } finally {
    clearTimeout(timer);
  }
}

export interface RecallResult {
  context: string;
  prepend_context?: string;
  strategy?: string;
  memory_count?: number;
  digest_settled?: boolean;
  gist_blocks?: number;
  code?: number;
  message?: string;
}

export function recall(tenant: TenantConfig, query: string, sessionKey: string) {
  return tenantGatewayCall<RecallResult>(tenant, "/recall", {
    query: query.slice(0, 2000),
    session_key: sessionKey,
  });
}

export function searchConversations(tenant: TenantConfig, query: string, opts: { limit?: number; sessionKey?: string } = {}) {
  return tenantGatewayCall<{ results: string; total: number }>(tenant, "/search/conversations", {
    query: query.slice(0, 2000),
    limit: opts.limit ?? 20,
    ...(opts.sessionKey ? { session_key: opts.sessionKey } : {}),
  });
}

export function searchMemories(tenant: TenantConfig, query: string, opts: { limit?: number; type?: string; scene?: string } = {}) {
  return tenantGatewayCall<{ results: string; total: number; strategy?: string }>(tenant, "/search/memories", {
    query: query.slice(0, 2000),
    limit: opts.limit ?? 20,
    ...(opts.type ? { type: opts.type } : {}),
    ...(opts.scene ? { scene: opts.scene } : {}),
  });
}

export async function gatewayHealth() {
  const res = await fetch(GATEWAY_BASE + "/health", { signal: AbortSignal.timeout(5000) });
  if (!res.ok) throw new Error(`gateway health ${res.status}`);
  return (await res.json()) as Record<string, unknown>;
}

export const TENANT_LIST = Object.values(TENANTS);
