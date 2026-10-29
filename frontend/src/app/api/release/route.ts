import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

export function GET() {
  const commit = process.env.BUILD_COMMIT_ID;
  const configured = Boolean(commit && /^[a-f0-9]{40}$/.test(commit));
  return NextResponse.json(
    { commit: configured ? commit : null },
    { status: configured ? 200 : 503, headers: { "Cache-Control": "no-store" } },
  );
}
