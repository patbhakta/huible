import type { APIRoute } from "astro";
import { chatConsent } from "../../../../lib/engine";

/** Acknowledge the real G6 consent gate for a moat conversation. */
export const POST: APIRoute = async ({ locals, request }) => {
  const tenant = locals.tenant;
  let body: { conversationId?: string };
  try {
    body = await request.json();
  } catch {
    return new Response(JSON.stringify({ error: "invalid_json" }), { status: 400 });
  }
  const conversationId = (body.conversationId ?? "").trim();
  if (!conversationId) {
    return new Response(JSON.stringify({ error: "conversationId_required" }), { status: 400 });
  }
  try {
    const { status, body: engineBody } = await chatConsent(tenant, conversationId);
    return new Response(JSON.stringify(engineBody), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  } catch (err) {
    return new Response(
      JSON.stringify({ error: "engine_call_failed", message: err instanceof Error ? err.message : String(err) }),
      { status: 502, headers: { "Content-Type": "application/json" } },
    );
  }
};
