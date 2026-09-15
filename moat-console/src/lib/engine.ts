import type { TenantConfig } from "./tenants";

/**
 * Client for the REAL persona engine (FastAPI core) — the exact path the
 * production chat uses. The moat console only proxies turns through it; it
 * never touches TencentDB or the vault except via the engine/gateway itself.
 *
 * Engine keys are persona-scoped (same table as .env API_KEYS), resolved per
 * tenant from env at request time.
 */

export const ENGINE_BASE = process.env.HUIBLE_API_BASE ?? "http://127.0.0.1:8000/api/v1";

export function engineKeyFor(tenant: TenantConfig): string {
  return process.env[tenant.engineKeyEnv] ?? "";
}

export function mintConversationId(): string {
  return `moat-${crypto.randomUUID().replace(/-/g, "").slice(0, 12)}`;
}

export interface EngineCallResult<T = Record<string, unknown>> {
  status: number;
  body: T;
}

async function engineCall<T = Record<string, unknown>>(
  tenant: TenantConfig,
  method: "GET" | "POST",
  path: string,
  body?: unknown,
  timeoutMs = 180_000,
): Promise<EngineCallResult<T>> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(`${ENGINE_BASE}${path}`, {
      method,
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${engineKeyFor(tenant)}`,
        // Sanctioned internal/probe lane (real_user_gate.py): skips only the
        // ramp-gate refusal; G1 crisis, G6 consent, G8 risk still fully fire.
        ...(path.startsWith("/chat") && !path.endsWith("/consent")
          ? { "X-Huible-Traffic-Class": "internal" }
          : {}),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    });
    const text = await res.text();
    let parsed: unknown;
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = { raw: text.slice(0, 2000) };
    }
    // Pass the engine's own status through verbatim (409 consent card,
    // 429 dosage cap, 5xx) — diagnostic honesty is the contract here.
    return { status: res.status, body: parsed as T };
  } finally {
    clearTimeout(timer);
  }
}

export interface EngineHealth {
  data?: {
    status?: string;
    checks?: Record<string, string>;
  };
}

export async function engineHealth(): Promise<EngineHealth & { up: boolean }> {
  try {
    const res = await fetch(`${ENGINE_BASE}/health`, {
      headers: { Authorization: `Bearer ${process.env.HUIBLE_DEMO_KEY ?? ""}` },
      signal: AbortSignal.timeout(5_000),
    });
    if (!res.ok) return { up: false };
    return { up: true, ...((await res.json()) as EngineHealth) };
  } catch {
    return { up: false };
  }
}

export interface ChatTurnTrace {
  working_memory?: {
    context?: string;
    chars?: number;
    strategy?: string;
    synced?: boolean;
    gist_blocks?: number;
    digest_settled?: boolean;
  };
  activated_memories?: Array<{
    id?: string;
    content_type?: string;
    disclosure_scope?: string;
    activation_score?: number;
    content?: string;
  }>;
  scoped_reads?: Array<{ section: string; lines: number }>;
  interest_tool?: { lines?: number };
  exclusion_counts?: Record<string, number>;
  provider?: string;
  [key: string]: unknown;
}

export interface ChatTurnOk {
  response?: string;
  conversation_id?: string;
  trace?: ChatTurnTrace;
  [key: string]: unknown;
}

export interface ConsentCard {
  title?: string;
  body?: string;
  acknowledge_instructions?: string;
  [key: string]: unknown;
}

/** Extract the G6 consent card from a 409 detail envelope, if present. */
export function consentCardFrom409(body: unknown): { card: ConsentCard; detail: string } {
  const detail = (body as { detail?: unknown })?.detail;
  const d = typeof detail === "object" && detail !== null ? (detail as Record<string, unknown>) : {};
  const e = typeof d.error === "object" && d.error !== null ? (d.error as Record<string, unknown>) : d;
  const card = (e.consent_card ?? e.card ?? {}) as ConsentCard;
  return { card, detail: typeof e.message === "string" ? e.message : "" };
}

export function chatTurn(
  tenant: TenantConfig,
  opts: { message: string; conversationId: string; workingMemoryEnabled?: boolean },
): Promise<EngineCallResult<ChatTurnOk | Record<string, unknown>>> {
  const chatBody: Record<string, unknown> = {
    message: opts.message.slice(0, 4000),
    relationship: "close_friend",
    conversation_id: opts.conversationId,
  };
  // Kill switch: only send the field when the client explicitly turned the
  // lane off; omit otherwise (engine default ON).
  if (opts.workingMemoryEnabled === false) {
    chatBody.working_memory_enabled = false;
  }
  return engineCall(tenant, "POST", `/chat/${tenant.personaId}`, chatBody);
}

export function chatConsent(tenant: TenantConfig, conversationId: string) {
  return engineCall(tenant, "POST", `/chat/${tenant.personaId}/consent`, { conversation_id: conversationId }, 30_000);
}
