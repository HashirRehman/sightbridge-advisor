import { NextResponse } from "next/server";
import { z } from "zod";

import { AuthError } from "@/lib/server/cognito";
import { signIn } from "@/lib/server/session";

const credentials = z.object({
  email: z.email().max(254),
  password: z.string().min(1).max(256),
});

export async function POST(request: Request) {
  const parsed = credentials.safeParse(await request.json().catch(() => null));
  if (!parsed.success) {
    return NextResponse.json({ error: "Enter a valid email and password." }, { status: 400 });
  }
  try {
    await signIn(parsed.data.email, parsed.data.password);
    return NextResponse.json({ ok: true });
  } catch (error) {
    if (error instanceof AuthError) {
      const status = error.kind === "unavailable" ? 503 : 401;
      return NextResponse.json({ error: error.message }, { status });
    }
    console.error("login failed", error instanceof Error ? error.name : "unknown");
    return NextResponse.json({ error: "Sign-in is temporarily unavailable." }, { status: 503 });
  }
}
