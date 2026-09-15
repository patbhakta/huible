import type { APIRoute } from "astro";import fs from "node:fs/promises";
import path from "node:path";

export const GET: APIRoute = async ({ locals, url }) => {
  const tenant = locals.tenant;
  const sub = (url.searchParams.get("sub") ?? "").replaceAll("\\", "/");
  const root = path.resolve(tenant.vaultRoot, sub);
  if (!root.startsWith(path.resolve(tenant.vaultRoot))) {
    return new Response(JSON.stringify({ error: "path_outside_vault" }), { status: 400 });
  }
  try {
    const entries = await fs.readdir(root, { withFileTypes: true });
    const items = [];
    for (const entry of entries) {
      if (entry.name.startsWith(".")) continue;
      const full = path.join(root, entry.name);
      if (entry.isDirectory()) {
        items.push({ name: entry.name, type: "dir", rel: path.relative(tenant.vaultRoot, full) });
      } else if (entry.name.endsWith(".md")) {
        const stat = await fs.stat(full);
        items.push({ name: entry.name, type: "note", rel: path.relative(tenant.vaultRoot, full), bytes: stat.size, modified: stat.mtime.toISOString() });
      }
    }
    items.sort((a, b) => a.name.localeCompare(b.name));
    return new Response(
      JSON.stringify({ tenant: tenant.slug, label: tenant.label, root: sub || ".", count: items.length, items }),
      { headers: { "Content-Type": "application/json" } },
    );
  } catch (err) {
    const code = (err as { code?: string }).code;
    const status = code === "ENOENT" ? 404 : code === "EACCES" || code === "EPERM" ? 403 : 500;
    return new Response(JSON.stringify({ error: "vault_read_failed", code: code ?? null, message: err instanceof Error ? err.message : String(err) }), { status });
  }
};
