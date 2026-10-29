import { randomUUID } from "node:crypto";

import { NextResponse, type NextRequest } from "next/server";

import { serverConfig } from "@/lib/server/config";
import { getIdToken } from "@/lib/server/session";

// Only these backend routes are reachable through the proxy.
const ALLOWED: Array<{ method: string; pattern: RegExp }> = [
  { method: "GET", pattern: /^me$/ },
  { method: "POST", pattern: /^chat$/ },
  { method: "GET", pattern: /^chat\/activity\/[0-9a-f-]{36}(?:\/result)?$/ },
  { method: "GET", pattern: /^conversations$/ },
  { method: "GET", pattern: /^conversations\/[0-9a-f-]{36}$/ },
  { method: "GET", pattern: /^admin\/exchanges$/ },
  { method: "GET", pattern: /^admin\/knowledge-base$/ },
  { method: "GET", pattern: /^admin\/conversations\/[0-9a-f-]{36}$/ },
  {
    method: "GET",
    pattern: /^admin\/documents\/[a-zA-Z0-9_-]{1,100}\/chunks$/,
  },
];

type Context = { params: Promise<{ path: string[] }> };

async function forward(request: NextRequest, { params }: Context) {
  const path = (await params).path.join("/");
  if (
    !ALLOWED.some((r) => r.method === request.method && r.pattern.test(path))
  ) {
    return NextResponse.json({ detail: "Not found." }, { status: 404 });
  }

  // CSRF: state-changing requests must come from this origin and be JSON (forms can't be).
  if (request.method !== "GET") {
    const origin = request.headers.get("origin");
    if (!origin || new URL(origin).host !== request.headers.get("host")) {
      return NextResponse.json(
        { detail: "Cross-origin request rejected." },
        { status: 403 },
      );
    }
    if (!request.headers.get("content-type")?.startsWith("application/json")) {
      return NextResponse.json({ detail: "Expected JSON." }, { status: 415 });
    }
  }

  const token = await getIdToken();
  if (!token)
    return NextResponse.json({ detail: "Session expired." }, { status: 401 });

  const requestId = randomUUID().replaceAll("-", "");
  const target = new URL(
    `/api/${path}${request.nextUrl.search}`,
    serverConfig().API_BASE_URL,
  );
  try {
    const upstream = await fetch(target, {
      method: request.method,
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
        "X-Request-ID": requestId,
      },
      body: request.method === "GET" ? undefined : await request.text(),
      cache: "no-store",
      signal: AbortSignal.timeout(29_000),
    });
    const headers = new Headers({
      "Content-Type": "application/json",
      "X-Request-ID": requestId,
      "Cache-Control": "private, no-store",
    });
    const retryAfter = upstream.headers.get("retry-after");
    if (retryAfter) headers.set("Retry-After", retryAfter);
    return new NextResponse(await upstream.text(), {
      status: upstream.status,
      headers,
    });
  } catch {
    return NextResponse.json(
      {
        detail: "The assistant service is unavailable. Please try again.",
        request_id: requestId,
      },
      { status: 502 },
    );
  }
}

export { forward as GET, forward as POST };
