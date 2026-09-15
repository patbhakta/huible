import { defineMiddleware } from "astro:middleware";
import { resolveTenant, TENANTS } from "./lib/tenants";

export const onRequest = defineMiddleware((context, next) => {
  const { url } = context;
  if (url.pathname.startsWith("/api/")) {
    const segments = url.pathname.split("/").filter(Boolean);
    if (segments[0] === "api" && segments[1] !== "health") {
      const tenant = resolveTenant(segments[1]);
      if (!tenant) {
        return new Response(
          JSON.stringify({ error: "unknown_tenant", tenants: Object.keys(TENANTS) }),
          { status: 404, headers: { "Content-Type": "application/json" } },
        );
      }
      context.locals.tenant = tenant;
    }
  }
  return next();
});
