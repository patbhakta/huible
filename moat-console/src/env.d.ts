import type { TenantConfig } from "./lib/tenants";

declare global {
  namespace App {
    interface Locals {
      tenant: TenantConfig;
    }
  }
}

export {};
