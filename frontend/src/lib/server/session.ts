import "server-only";

import { decodeJwt } from "jose";
import { cookies } from "next/headers";

import { AuthError, refreshSession, signInWithPassword, type Tokens } from "./cognito";
import { serverConfig } from "./config";
import { signInLocal } from "./local-auth";

export const SESSION_COOKIE = "sb_session";
const REFRESH_COOKIE = "sb_refresh";
const REFRESH_TTL_SECONDS = 60 * 60 * 24 * 7;
// Refresh slightly before expiry so a request never carries a token that expires mid-flight.
const EXPIRY_SKEW_SECONDS = 60;

function cookieOptions(maxAge: number) {
  return {
    httpOnly: true, // never readable by browser JavaScript
    secure: serverConfig().APP_ENV === "production",
    sameSite: "lax" as const,
    path: "/",
    maxAge,
  };
}

export async function signIn(email: string, password: string): Promise<void> {
  const tokens =
    serverConfig().AUTH_PROVIDER === "local"
      ? await signInLocal(email, password)
      : await signInWithPassword(email, password);
  await storeTokens(tokens);
}

async function storeTokens(tokens: Tokens): Promise<void> {
  const jar = await cookies();
  jar.set(SESSION_COOKIE, tokens.idToken, cookieOptions(tokens.expiresIn));
  if (tokens.refreshToken) {
    jar.set(REFRESH_COOKIE, tokens.refreshToken, cookieOptions(REFRESH_TTL_SECONDS));
  }
}

export async function signOut(): Promise<void> {
  const jar = await cookies();
  jar.delete(SESSION_COOKIE);
  jar.delete(REFRESH_COOKIE);
}

function isFresh(token: string): boolean {
  try {
    // Expiry only — the backend verifies the signature and every other claim.
    const { exp } = decodeJwt(token);
    return typeof exp === "number" && exp - EXPIRY_SKEW_SECONDS > Date.now() / 1000;
  } catch {
    return false;
  }
}

/** Current ID token, refreshed via Cognito when needed; null means "sign in again". */
export async function getIdToken(): Promise<string | null> {
  const jar = await cookies();
  const token = jar.get(SESSION_COOKIE)?.value;
  if (token && isFresh(token)) return token;

  const refreshToken = jar.get(REFRESH_COOKIE)?.value;
  if (!refreshToken || serverConfig().AUTH_PROVIDER !== "cognito") return null;
  try {
    const tokens = await refreshSession(refreshToken);
    await storeTokens(tokens);
    return tokens.idToken;
  } catch (error) {
    if (error instanceof AuthError) await signOut();
    return null;
  }
}
