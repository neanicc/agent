import "server-only";

import {
  generateAuthenticationOptions,
  verifyAuthenticationResponse,
  type AuthenticationResponseJSON,
  type VerifiedAuthenticationResponse,
  type WebAuthnCredential,
} from "@simplewebauthn/server";

export type ActionPasskey = {
  credentialId: string;
  publicKey: Uint8Array<ArrayBuffer>;
  counter: number;
  userId: string;
  deviceType: "singleDevice" | "multiDevice";
  backedUp: boolean;
  transports?: WebAuthnCredential["transports"];
};

type AssertionVerifier = (options: Parameters<typeof verifyAuthenticationResponse>[0]) => Promise<VerifiedAuthenticationResponse>;

export async function actionAuthenticationOptions(input: {
  actionChallenge: string;
  credential: ActionPasskey;
  rpID: string;
}) {
  return generateAuthenticationOptions({
    rpID: input.rpID,
    challenge: input.actionChallenge,
    userVerification: "required",
    allowCredentials: [
      {
        id: input.credential.credentialId,
        transports: input.credential.transports,
      },
    ],
  });
}

export async function verifyActionAssertion(
  input: {
    actionChallenge: string;
    expectedOrigin: string;
    expectedRPID: string;
    expectedUserId: string;
    credential: ActionPasskey;
    response: AuthenticationResponseJSON;
  },
  verifier: AssertionVerifier = verifyAuthenticationResponse,
): Promise<{ verified: true; userVerified: true; newCounter: number; backedUp: boolean }> {
  if (input.credential.userId !== input.expectedUserId) {
    throw new TypeError("WebAuthn credential user binding does not match");
  }
  if (input.response.id !== input.credential.credentialId) {
    throw new TypeError("WebAuthn credential ID does not match");
  }
  const result = await verifier({
    response: input.response,
    expectedChallenge: input.actionChallenge,
    expectedOrigin: input.expectedOrigin,
    expectedRPID: input.expectedRPID,
    credential: {
      id: input.credential.credentialId,
      publicKey: input.credential.publicKey,
      counter: input.credential.counter,
      transports: input.credential.transports,
    },
    expectedType: "webauthn.get",
    requireUserVerification: true,
    advancedFIDOConfig: { userVerification: "required" },
  });
  if (!result.verified || !result.authenticationInfo.userVerified) {
    throw new TypeError("WebAuthn action proof was not user verified");
  }
  if (
    result.authenticationInfo.origin !== input.expectedOrigin ||
    result.authenticationInfo.rpID !== input.expectedRPID
  ) {
    throw new TypeError("WebAuthn relying-party binding does not match");
  }
  // WebAuthn L3 §6.1.1 step 17: authenticators without a counter always report 0,
  // so only enforce advancement once either side is nonzero.
  const counterInUse =
    result.authenticationInfo.newCounter !== 0 || input.credential.counter !== 0;
  if (
    counterInUse &&
    result.authenticationInfo.newCounter <= input.credential.counter &&
    !input.credential.backedUp
  ) {
    throw new TypeError("WebAuthn signature counter did not advance");
  }
  return {
    verified: true,
    userVerified: true,
    newCounter: result.authenticationInfo.newCounter,
    backedUp: result.authenticationInfo.credentialBackedUp,
  };
}
