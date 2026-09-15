import type { APIRoute } from "astro";
import { chatTurn, consentCardFrom409 } from "../../../../lib/engine";

/**
 * Proxy one chat turn through the REAL engine — same path as production chat.
 * Passes the engine's own status verbatim:
 *   200 → reply + full trace (working-memory block, vault activations)
 *   409 → G6 consent card (rendered by the client, acknowledged via /consent)
 *   429 → G8 dosage cap (real gate, session paused)
 * workingMemoryEnabled:false = the W4 kill switch (lane off this turn).
 */
export const POST: APIRoute = async ({ locals, request }) => {
  const tenant = locals.tenant;
  let body: { message?: string; conversationId?: string; workingMemoryEnabled?: boolean };
  try {
    body = await request.json();
  } catch {
    return new Response(JSON.stringify({ error: "invalid_json" }), { status: 400 });
  }
  const message = (body.message ?? "").trim();
  const conversationId = (body.conversationId ?? "").trim();
  if (!message) {
    return new Response(JSON.stringify({ error: "message_required" }), { status: 400 });
  }
  if (!conversationId || !/^moat-[a-z0-9]{6,24}$/.test(conversationId)) {
    return new Response(JSON.stringify({ error: "conversationId_required", hint: "mint via POST chat/session" }), {
      status: 400,
    });
  }
  try {
    const { status, body: engineBody } = await chatTurn(tenant, {
      message,
      conversationId,
      workingMemoryEnabled: body.workingMemoryEnabled,
    });
    if (status === 409) {
      const { card, detail } = consentCardFrom409(engineBody);
      return new Response(JSON.stringify({ consent_card: card, detail }), {
        status: 409,
        headers: { "Content-Type": "application/json" },
      });
    }
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
