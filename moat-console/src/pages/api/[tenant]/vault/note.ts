import type { APIRoute } from "astro";
import fs from "node:fs/promises";
import path from "node:path";

export const GET: APIRoute = async ({ locals, url }) => {
  const tenant = locals.tenant;
  const rel = url.searchParams.get("rel") ?? "";
  if (!rel) {
    return new Response(JSON.stringify({ error: "rel_required" }), { status: 400 });
  }
  const normalized = path.normalize(rel).replaceAll("\\", "/");
  const full = path.resolve(tenant.vaultRoot, normalized);
  if (!full.startsWith(path.resolve(tenant.vaultRoot) + path.sep)) {
    return new Response(JSON.stringify({ error: "path_outside_vault" }), { status: 400 });
  }
  if (!normalized.endsWith(".md")) {
    return new Response(JSON.stringify({ error: "only_markdown_notes" }), { status: 415 });
  }
  try {
    const content = await fs.readFile(full, "utf8");
    const stat = await fs.stat(full);
    return new Response(
      JSON.stringify({
        tenant: tenant.slug,
        rel: normalized,
        bytes: stat.size,
        modified: stat.mtime.toISOString(),
        content,
      }),
      { headers: { "Content-Type": "application/json" } },
    );
  } catch (err) {
    const code = (err as { code?: string }).code;
    const status = code === "ENOENT" ? 404 : code === "EACCES" || code === "EPERM" ? 403 : 500;
    return new Response(JSON.stringify({ error: "vault_note_failed", code: code ?? null, message: err instanceof Error ? err.message : String(err) }), { status });
  }
};
