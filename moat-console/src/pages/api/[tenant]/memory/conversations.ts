import type { APIRoute } from "astro";
import { searchConversations } from "../../../../lib/gateway";
import { sessionKeyFor } from "../../../../lib/tenants";

const SESSION_ROW = /\*\*\[(user|assistant)\]\*\* Session: (huible-p([0-9a-f-]{36})-c\S+?)(?:\s|\])/;

export const POST: APIRoute = async ({ locals, request }) => {
  const tenant = locals.tenant;
  let body: { query?: string; limit?: number; conversationId?: string; sessionKey?: string };
  try {
    body = await request.json();
  } catch {
    return new Response(JSON.stringify({ error: "invalid_json" }), { status: 400 });
  }
  const query = (body.query ?? "").trim();
  if (!query) {
    return new Response(JSON.stringify({ error: "query_required" }), { status: 400 });
  }
  const sessionKey = body.sessionKey ?? (body.conversationId ? sessionKeyFor(tenant, body.conversationId) : undefined);
  try {
    const result = await searchConversations(tenant, query, {
      limit: Math.min(Math.max(body.limit ?? 20, 1), 100),
      sessionKey,
    });
    const personaPrefix = `huible-p${tenant.personaId}-c`;
    const rows = result.results
      .split(/\n---\n/)
      .map((row) => row.trim())
      .filter(Boolean)
      .map((row) => {
        const m = SESSION_ROW.exec(row);
        return m ? { role: m[1], sessionKey: m[2], personaId: m[3], text: row } : null;
      })
      .filter((row): row is { role: string; sessionKey: string; personaId: string; text: string } => row !== null);
    const scoped = rows.filter((row) => row.sessionKey.startsWith(personaPrefix));
    const dropped = rows.length - scoped.length;
    return new Response(
      JSON.stringify({
        tenant: tenant.slug,
        service: tenant.serviceId,
        query,
        ...(sessionKey ? { session_key: sessionKey } : {}),
        total: result.total,
        returned: scoped.length,
        cross_tenant_rows_dropped: dropped,
        rows: scoped.map((row) => ({ role: row.role, sessionKey: row.sessionKey, text: row.text })),      }),
      { headers: { "Content-Type": "application/json" } },
    );
  } catch (err) {
    const status = (err as { status?: number }).status ?? 502;
    return new Response(
      JSON.stringify({ error: "search_failed", message: err instanceof Error ? err.message : String(err) }),
      { status },
    );
  }
};
