import type { APIRoute } from "astro";
import { gatewayHealth, TENANT_LIST } from "../../lib/gateway";

export const GET: APIRoute = async () => {
  const out: Record<string, unknown> = { status: "ok", service: "moat-console", tenants: TENANT_LIST.map((t) => t.slug) };
  try {
    const gw = await gatewayHealth();
    out.gateway = { status: "ok", version: gw.version ?? null, uptime: gw.uptime ?? null };
  } catch (err) {
    out.gateway = { status: "error", message: err instanceof Error ? err.message : String(err) };
  }
  return new Response(JSON.stringify(out), {
    headers: { "Content-Type": "application/json" },
  });
};
