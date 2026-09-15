import type { APIRoute } from "astro";
import { recall } from "../../../../lib/gateway";
import { sessionKeyFor } from "../../../../lib/tenants";

export const POST: APIRoute = async ({ locals, request }) => {
  const tenant = locals.tenant;
  let body: { query?: string; conversationId?: string; sessionKey?: string };
  try {
    body = await request.json();
  } catch {
    return new Response(JSON.stringify({ error: "invalid_json" }), { status: 400 });
  }
  const query = (body.query ?? "").trim();
  if (!query) {
    return new Response(JSON.stringify({ error: "query_required" }), { status: 400 });
  }
  const sessionKey = body.sessionKey ?? (body.conversationId ? sessionKeyFor(tenant, body.conversationId) : "");
  if (!sessionKey) {
    return new Response(JSON.stringify({ error: "conversationId_or_sessionKey_required" }), { status: 400 });
  }
  try {
    const result = await recall(tenant, query, sessionKey);
    return new Response(
      JSON.stringify({ tenant: tenant.slug, service: tenant.serviceId, session_key: sessionKey, ...result }),
      { headers: { "Content-Type": "application/json" } },
    );
  } catch (err) {
    const status = (err as { status?: number }).status ?? 502;
    return new Response(
      JSON.stringify({ error: "recall_failed", message: err instanceof Error ? err.message : String(err) }),
      { status },
    );
  }
};
