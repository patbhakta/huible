export interface TenantConfig {
  slug: string;
  label: string;
  personaId: string;
  serviceId: string;
  vaultRoot: string;
  /** Env var holding the persona-scoped engine API key (same table as .env API_KEYS). */
  engineKeyEnv: string;
}

export const TENANTS: Record<string, TenantConfig> = {
  "huible-demo": {
    slug: "huible-demo",
    label: "Huible Demo — Chandler",
    personaId: "fdc3a44b-4c0f-565d-b671-4ed0e3bc7894",
    serviceId: "huible-chandler",
    vaultRoot: "/root/repos/personas/chandler-bing/vault",
    engineKeyEnv: "HUIBLE_DEMO_KEY",
  },
  "huible-monica": {
    slug: "huible-monica",
    label: "Huible Demo — Monica",
    personaId: "3ef60bec-79d2-5e31-8d9e-e856bb1ebfea",
    serviceId: "huible-monica",
    vaultRoot: "/root/repos/personas/monica/vault",
    engineKeyEnv: "HUIBLE_DEMO_KEY_MONICA",
  },
};

export const DEMO_TENANT = "huible-demo";

export function resolveTenant(slug: string | undefined): TenantConfig | null {
  if (!slug) return null;
  return TENANTS[slug] ?? null;
}

export function sessionKeyFor(tenant: TenantConfig, conversationId: string): string {
  return `huible-p${tenant.personaId}-c${conversationId}`;
}
