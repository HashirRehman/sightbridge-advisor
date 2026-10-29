import "server-only";

import { createHash, timingSafeEqual } from "node:crypto";

import { SignJWT } from "jose";
import { z } from "zod";

import { AuthError, type Tokens } from "./cognito";
import { serverConfig } from "./config";

// Local development only (refused in production by config): mimics a Cognito ID token so the
// backend's LocalTokenVerifier can check it with the shared secret.
const LOCAL_ISSUER = "sightbridge-local";
const TOKEN_TTL_SECONDS = 3600;

const users = z.array(
  z.object({ email: z.email(), password: z.string().min(8), role: z.enum(["advisor", "admin"]) }),
);

function sameSecret(a: string, b: string): boolean {
  const digest = (s: string) => createHash("sha256").update(s).digest();
  return timingSafeEqual(digest(a), digest(b));
}

export async function signInLocal(email: string, password: string): Promise<Tokens> {
  const { LOCAL_DEMO_USERS, LOCAL_AUTH_SECRET } = serverConfig();
  const user = users
    .parse(JSON.parse(LOCAL_DEMO_USERS))
    .find((u) => u.email.toLowerCase() === email.toLowerCase());
  // Compare even when the user is unknown, so timing doesn't reveal which emails exist.
  const ok = sameSecret(user?.password ?? "", password) && user !== undefined;
  if (!ok || !user) throw new AuthError("Incorrect email or password.", "invalid_credentials");

  const idToken = await new SignJWT({
    email: user.email.toLowerCase(),
    token_use: "id",
    "cognito:groups": user.role === "admin" ? ["admin"] : [],
  })
    .setProtectedHeader({ alg: "HS256" })
    .setSubject(`local-${createHash("sha256").update(user.email).digest("hex").slice(0, 16)}`)
    .setIssuer(LOCAL_ISSUER)
    .setAudience(LOCAL_ISSUER)
    .setIssuedAt()
    .setExpirationTime(`${TOKEN_TTL_SECONDS}s`)
    .sign(new TextEncoder().encode(LOCAL_AUTH_SECRET));
  return { idToken, expiresIn: TOKEN_TTL_SECONDS };
}
