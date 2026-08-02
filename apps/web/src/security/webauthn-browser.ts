"use client";

import {
  startAuthentication,
  type AuthenticationResponseJSON,
  type PublicKeyCredentialRequestOptionsJSON,
} from "@simplewebauthn/browser";

export async function signActionChallenge(
  optionsJSON: PublicKeyCredentialRequestOptionsJSON,
): Promise<AuthenticationResponseJSON> {
  if (optionsJSON.userVerification !== "required") {
    throw new TypeError("Action approval must require WebAuthn user verification");
  }
  return startAuthentication({ optionsJSON, useBrowserAutofill: false });
}
