import { NextResponse } from "next/server";

import { signOut } from "@/lib/server/session";

export async function POST() {
  await signOut();
  return NextResponse.json({ ok: true });
}
