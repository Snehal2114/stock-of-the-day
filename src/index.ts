import { Container, getContainer } from "@cloudflare/containers";

const AI_MODEL = "@cf/google/gemma-4-26b-a4b-it";

export interface AiMessage {
  role: "system" | "user" | "assistant";
  content: string;
}

export interface AiRunOptions {
  messages?: AiMessage[];
  prompt?: string;
  stream?: boolean;
  max_tokens?: number;
}

export interface AiRunResponse {
  response: string;
}

export interface Env {
  STOCK_CONTAINER: DurableObjectNamespace<StockContainer>;
  AI: {
    run: (model: string, options: AiRunOptions) => Promise<AiRunResponse>;
  };
}

/**
 * Durable Object that fronts the Python container running the
 * "stock of the day" service. The container listens on port 8080.
 */
export class StockContainer extends Container {
  defaultPort = 8080;
  // Keep the container warm for a while between requests to avoid
  // a cold start on every call.
  sleepAfter = "10m";
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    const startedAt = Date.now();
    const requestId =
      request.headers.get("cf-ray") ?? crypto.randomUUID();

    // Observability: log every incoming request. These show up in
    // Workers Logs (Observability) because `observability.enabled = true`
    // in wrangler.jsonc.
    log("info", "request.received", {
      requestId,
      method: request.method,
      path: url.pathname,
      query: url.search || undefined,
      country: url.searchParams.get("country") ?? undefined,
      refresh: url.searchParams.get("refresh") ?? undefined,
      ua: request.headers.get("user-agent") ?? undefined,
    });

    // CORS preflight support (useful for browser clients / dashboards).
    if (request.method === "OPTIONS") {
      return finalize(
        new Response(null, {
          status: 204,
          headers: corsHeaders({
            "access-control-max-age": "86400",
          }),
        }),
        { requestId, startedAt, kind: "cors_preflight" },
      );
    }

    // Simple health endpoint that doesn't need the container.
    if (url.pathname === "/healthz") {
      return finalize(
        new Response("ok", { status: 200, headers: corsHeaders() }),
        { requestId, startedAt, kind: "healthz" },
      );
    }

    if (url.pathname !== "/" && url.pathname !== "/stock-of-the-day") {
      log("info", "request.not_found", { requestId, path: url.pathname });
      return finalize(
        json({ error: "not_found", message: "Try GET / for API details or GET /stock-of-the-day?country=US" }, 404),
        { requestId, startedAt, kind: "not_found" },
      );
    }

    // Forward the request to the container. We use a single instance
    // (id "default") so the container can reuse any in-memory caches.
    const container = getContainer(env.STOCK_CONTAINER, "default");

    const upstreamUrl = new URL(request.url);

    // Treat `/?country=...` as a convenience alias for `/stock-of-the-day?country=...`
    // so that callers landing on the root with a country param get data back
    // instead of just the API description.
    const isStockRequest =
      url.pathname === "/stock-of-the-day" ||
      (url.pathname === "/" && url.searchParams.has("country"));

    // `refresh=true` bypasses both the edge cache and the container's
    // deterministic daily pick. Accept the common truthy spellings.
    const refresh = parseBool(url.searchParams.get("refresh"));

    if (!isStockRequest) {
      // Root path with no query: forward to the container's API-details endpoint.
      upstreamUrl.pathname = "/";
      upstreamUrl.search = "";
    } else {
      // /stock-of-the-day (or /?country=...): validate the `country` query parameter first.
      const rawCountry = (url.searchParams.get("country") ?? "US").trim();
      if (!/^[A-Za-z]{2}$/.test(rawCountry)) {
        log("info", "request.invalid_country", { requestId, rawCountry });
        return finalize(
          json(
            {
              error: "invalid_country",
              message: "country must be a 2-letter ISO code (e.g. US, IN, GB, JP).",
            },
            400,
          ),
          { requestId, startedAt, kind: "invalid_country" },
        );
      }
      const country = rawCountry.toUpperCase();
      upstreamUrl.pathname = "/stock-of-the-day";
      upstreamUrl.searchParams.set("country", country);
      if (refresh) {
        upstreamUrl.searchParams.set("refresh", "true");
      } else {
        upstreamUrl.searchParams.delete("refresh");
      }
    }

    const upstreamStarted = Date.now();
    log("info", "upstream.dispatch", {
      requestId,
      upstream: upstreamUrl.pathname + upstreamUrl.search,
      refresh,
    });

    try {
      const resp = await container.fetch(
        new Request(upstreamUrl.toString(), { method: "GET" }),
      );

      const upstreamMs = Date.now() - upstreamStarted;

      // Re-wrap so we can set caching + CORS headers cleanly.
      let body = await resp.text();

      if (resp.ok && isStockRequest) {
        try {
          const payload = JSON.parse(body);
          if (payload?.company?.summary && typeof payload.company.summary === "string") {
            const originalSummary = payload.company.summary;
            const wordCount = originalSummary.trim().split(/\s+/).length;
            if (wordCount > 75) {
              const aiResponse = await env.AI.run(AI_MODEL, {
                messages: [
                  {
                    role: "system",
                    content: "You are a financial analyst. Summarize the following company business description in under 75 words. Do not include any preamble, introduction, markdown formatting, meta-commentary, or surrounding text. Return ONLY the plain text summary.",
                  },
                  {
                    role: "user",
                    content: originalSummary,
                  },
                ],
                // Cap generation so the summary stays short and the call
                // returns reliably (avoids truncated/empty responses that
                // would leave the original long summary unchanged).
                max_tokens: 256,
              });

              if (aiResponse && typeof aiResponse.response === "string" && aiResponse.response.trim().length > 0) {
                payload.company.summary = aiResponse.response.trim();
                body = JSON.stringify(payload);
                log("info", "summary.summarized", {
                  requestId,
                  originalLength: originalSummary.length,
                  newLength: payload.company.summary.length,
                });
              }
            }
          }
        } catch (summaryErr) {
          log("warn", "summary.summarization_failed", {
            requestId,
            error: summaryErr instanceof Error ? summaryErr.message : String(summaryErr),
          });
        }
      }

      // When `refresh=true`, never cache — the whole point is to bypass
      // the day's pick. Otherwise the pick is stable for the UTC day, so
      // cache for an hour at the edge to reduce load on the container.
      const cacheControl = refresh
        ? "no-store"
        : resp.ok
          ? "public, max-age=3600"
          : "no-store";
      const headers = new Headers({
        "content-type": resp.headers.get("content-type") ?? "application/json",
        ...corsHeaders(),
        "cache-control": cacheControl,
      });
      log("info", "upstream.response", {
        requestId,
        status: resp.status,
        ok: resp.ok,
        upstreamMs,
        bodyBytes: body.length,
        refresh,
      });
      return finalize(new Response(body, { status: resp.status, headers }), {
        requestId,
        startedAt,
        kind: "upstream",
      });
    } catch (err) {
      const upstreamMs = Date.now() - upstreamStarted;
      // Log the underlying error for operators but do not leak it to the
      // client (avoids stack-trace / internal-detail exposure).
      log("error", "upstream.failed", {
        requestId,
        upstreamMs,
        error: err instanceof Error ? err.message : String(err),
      });
      return finalize(
        json(
          {
            error: "upstream_error",
            message: "Failed to reach the stock-of-the-day container.",
          },
          502,
        ),
        { requestId, startedAt, kind: "upstream_error" },
      );
    }
  },
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body, null, 2), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", ...corsHeaders() },
  });
}

function corsHeaders(extra: Record<string, string> = {}): Record<string, string> {
  return {
    "access-control-allow-origin": "*",
    "access-control-allow-methods": "GET, OPTIONS",
    "access-control-allow-headers": "content-type",
    ...extra,
  };
}

function parseBool(value: string | null): boolean {
  if (!value) return false;
  return ["1", "true", "yes", "on"].includes(value.trim().toLowerCase());
}

/**
 * Emit a single-line JSON log so Workers Logs (Observability) shows
 * structured fields rather than a stringified object.
 */
function log(
  level: "info" | "warn" | "error",
  event: string,
  fields: Record<string, unknown> = {},
): void {
  const entry = { level, event, ts: new Date().toISOString(), ...fields };
  const line = JSON.stringify(entry);
  if (level === "error") {
    console.error(line);
  } else if (level === "warn") {
    console.warn(line);
  } else {
    console.log(line);
  }
}

/**
 * Attach a request-id header and emit a final ``request.completed`` log
 * for every response so operators can correlate latency and status codes.
 */
function finalize(
  response: Response,
  ctx: { requestId: string; startedAt: number; kind: string },
): Response {
  const headers = new Headers(response.headers);
  if (!headers.has("x-request-id")) {
    headers.set("x-request-id", ctx.requestId);
  }
  const out = new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
  log("info", "request.completed", {
    requestId: ctx.requestId,
    kind: ctx.kind,
    status: response.status,
    durationMs: Date.now() - ctx.startedAt,
  });
  return out;
}
