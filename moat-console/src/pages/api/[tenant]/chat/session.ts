import type { APIRoute } from "astro";
import { engineHealth, mintConversationId } from "../../../../lib/engine";

/** Mint a moat conversation id after confirming the real engine is up. */
export const POST: APIRoute = async ({ locals }) => {
  const tenant = locals.tenant;
  const health = await engineHealth();
  const generator = health.data?.checks?.generator ?? "unknown";
  if (!health.up) {
    return new Response(
      JSON.stringify({ error: "engine_unreachable", engine: health.data ?? null }),
      { status: 502, headers: { "Content-Type": "application/json" } },
    );
  }
  return new Response(
    JSON.stringify({
      conversation_id: mintConversationId(),
      persona: tenant.personaId,
      tenant: tenant.slug,
      generator,
      engine_status: health.data?.status ?? "ok",
    }),
    { headers: { "Content-Type": "application/json" } },
  );
};
