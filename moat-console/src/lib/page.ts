import type { TenantConfig } from "./tenants";
import { resolveTenant } from "./tenants";

export function tenantFromParams(slug: string | undefined): TenantConfig | null {
  return resolveTenant(slug);
}

export function notFound(message: string): Response {
  return new Response(
    `<!doctype html><html lang="en"><body style="font-family: Georgia, serif; max-width: 40rem; margin: 4rem auto; padding: 0 1rem;"><h1>404</h1><p>${message.replace(/[<>&]/g, "")}</p><p><a href="/">Back to the console</a></p></body></html>`,
    { status: 404, headers: { "Content-Type": "text/html; charset=utf-8" } },
  );
}

export function clampLimit(raw: string | null, fallback = 20, max = 100): number {
  const n = Number.parseInt(raw ?? "", 10);
  if (!Number.isFinite(n) || n <= 0) return fallback;
  return Math.min(n, max);
}

export function formatBytes(bytes: number | undefined): string {
  if (bytes === undefined) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} kB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
