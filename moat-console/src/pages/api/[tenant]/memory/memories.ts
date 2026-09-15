import type { APIRoute } from "astro";
import { searchMemories } from "../../../../lib/gateway";

export const POST: APIRoute = async ({ locals, request }) => {
  const tenant = locals.tenant;
  let body: { query?: string; limit?: number; type?: string; scene?: string };
  try {
    body = await request.json();
  } catch {
    return new Response(JSON.stringify({ error: "invalid_json" }), { status: 400 });
  }
  const query = (body.query ?? "").trim();
  if (!query) {
    return new Response(JSON.stringify({ error: "query_required" }), { status: 400 });
  }
  try {
    const result = await searchMemories(tenant, query, {
      limit: Math.min(Math.max(body.limit ?? 20, 1), 100),
      type: body.type,
      scene: body.scene,
    });
    return new Response(
      JSON.stringify({
        tenant: tenant.slug,
        service: tenant.serviceId,
        query,
        total: result.total,
        strategy: result.strategy,
        results: result.results,
        note: "L1 memories are user-profile scoped on this gateway instance and are shared across personas of the same user; L0 conversation rows are tenant-scoped via /memory/conversations.",
      }),
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
