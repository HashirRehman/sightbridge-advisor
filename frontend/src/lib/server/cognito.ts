import "server-only";

import { serverConfig } from "./config";

export type Tokens = { idToken: string; refreshToken?: string; expiresIn: number };

export class AuthError extends Error {
  constructor(
    message: string,
    readonly kind: "invalid_credentials" | "challenge" | "unavailable",
  ) {
    super(message);
  }
}

type InitiateAuthResponse = {
  AuthenticationResult?: { IdToken: string; RefreshToken?: string; ExpiresIn: number };
  ChallengeName?: string;
};

// Public app client (no secret), so the Cognito JSON API can be called directly.
async function initiateAuth(
  flow: string,
  parameters: Record<string, string>,
): Promise<InitiateAuthResponse> {
  const { COGNITO_REGION, COGNITO_APP_CLIENT_ID } = serverConfig();
  const response = await fetch(`https://cognito-idp.${COGNITO_REGION}.amazonaws.com/`, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-amz-json-1.1",
      "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth",
    },
    body: JSON.stringify({
      AuthFlow: flow,
      ClientId: COGNITO_APP_CLIENT_ID,
      AuthParameters: parameters,
    }),
    cache: "no-store",
    signal: AbortSignal.timeout(8000),
  });
  const body = (await response.json().catch(() => ({}))) as InitiateAuthResponse & {
    __type?: string;
  };
  if (!response.ok) {
    const type = body.__type ?? "";
    if (/NotAuthorized|UserNotFound|UserNotConfirmed|PasswordResetRequired/.test(type)) {
      throw new AuthError("Incorrect email or password.", "invalid_credentials");
    }
    throw new AuthError("Sign-in is temporarily unavailable.", "unavailable");
  }
  return body;
}

export async function signInWithPassword(email: string, password: string): Promise<Tokens> {
  const result = await initiateAuth("USER_PASSWORD_AUTH", { USERNAME: email, PASSWORD: password });
  if (!result.AuthenticationResult) {
    throw new AuthError("This account needs an administrator to finish setup.", "challenge");
  }
  const { IdToken, RefreshToken, ExpiresIn } = result.AuthenticationResult;
  return { idToken: IdToken, refreshToken: RefreshToken, expiresIn: ExpiresIn };
}

export async function refreshSession(refreshToken: string): Promise<Tokens> {
  const result = await initiateAuth("REFRESH_TOKEN_AUTH", { REFRESH_TOKEN: refreshToken });
  if (!result.AuthenticationResult) {
    throw new AuthError("Session expired.", "invalid_credentials");
  }
  const { IdToken, ExpiresIn } = result.AuthenticationResult;
  return { idToken: IdToken, refreshToken, expiresIn: ExpiresIn };
}
