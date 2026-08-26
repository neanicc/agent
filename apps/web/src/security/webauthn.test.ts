import { describe, expect, test, vi } from "vitest";

import { verifyActionAssertion, type ActionPasskey } from "./webauthn";

const credential: ActionPasskey = {
  credentialId: "credential-1",
  publicKey: new Uint8Array([1, 2, 3]),
  counter: 8,
  userId: "user-1",
  deviceType: "singleDevice",
  backedUp: false,
};

describe("WebAuthn action proof", () => {
  test("binds user verification, origin, RP ID, credential, and exact action challenge", async () => {
    const verifier = vi.fn(async () => ({
      verified: true,
      authenticationInfo: {
        credentialID: "credential-1",
        newCounter: 9,
        userVerified: true,
        credentialDeviceType: "singleDevice" as const,
        credentialBackedUp: false,
        origin: "https://loopguard.test",
        rpID: "loopguard.test",
      },
    }));

    const result = await verifyActionAssertion(
      {
        actionChallenge: "canonical-action-challenge",
        expectedOrigin: "https://loopguard.test",
        expectedRPID: "loopguard.test",
        expectedUserId: "user-1",
        credential,
        response: fixtureResponse("canonical-action-challenge"),
      },
      verifier,
    );

    expect(result).toMatchObject({ verified: true, userVerified: true, newCounter: 9 });
    expect(verifier).toHaveBeenCalledWith(
      expect.objectContaining({
        expectedChallenge: "canonical-action-challenge",
        expectedOrigin: "https://loopguard.test",
        expectedRPID: "loopguard.test",
        requireUserVerification: true,
        credential: expect.objectContaining({ id: "credential-1", counter: 8 }),
      }),
    );
  });

  test("rejects a credential bound to another user before crypto verification", async () => {
    const verifier = vi.fn();
    await expect(
      verifyActionAssertion(
        {
          actionChallenge: "canonical-action-challenge",
          expectedOrigin: "https://loopguard.test",
          expectedRPID: "loopguard.test",
          expectedUserId: "other-user",
          credential,
          response: fixtureResponse("canonical-action-challenge"),
        },
        verifier,
      ),
    ).rejects.toThrow("credential user binding");
    expect(verifier).not.toHaveBeenCalled();
  });

  test("accepts an authenticator that never implements a signature counter", async () => {
    const verifier = vi.fn(async () => ({
      verified: true,
      authenticationInfo: {
        credentialID: "credential-1",
        newCounter: 0,
        userVerified: true,
        credentialDeviceType: "singleDevice" as const,
        credentialBackedUp: false,
        origin: "https://loopguard.test",
        rpID: "loopguard.test",
      },
    }));

    const result = await verifyActionAssertion(
      {
        actionChallenge: "canonical-action-challenge",
        expectedOrigin: "https://loopguard.test",
        expectedRPID: "loopguard.test",
        expectedUserId: "user-1",
        credential: { ...credential, counter: 0 },
        response: fixtureResponse("canonical-action-challenge"),
      },
      verifier,
    );

    expect(result).toMatchObject({ verified: true, userVerified: true, newCounter: 0 });
  });

  test("still rejects a counter regression on a counter-bearing authenticator", async () => {
    const verifier = vi.fn(async () => ({
      verified: true,
      authenticationInfo: {
        credentialID: "credential-1",
        newCounter: 0,
        userVerified: true,
        credentialDeviceType: "singleDevice" as const,
        credentialBackedUp: false,
        origin: "https://loopguard.test",
        rpID: "loopguard.test",
      },
    }));

    await expect(
      verifyActionAssertion(
        {
          actionChallenge: "canonical-action-challenge",
          expectedOrigin: "https://loopguard.test",
          expectedRPID: "loopguard.test",
          expectedUserId: "user-1",
          credential,
          response: fixtureResponse("canonical-action-challenge"),
        },
        verifier,
      ),
    ).rejects.toThrow("signature counter did not advance");
  });
});

function fixtureResponse(challenge: string) {
  const clientDataJSON = Buffer.from(
    JSON.stringify({ type: "webauthn.get", challenge, origin: "https://loopguard.test" }),
  ).toString("base64url");
  return {
    id: "credential-1",
    rawId: "credential-1",
    type: "public-key" as const,
    clientExtensionResults: {},
    response: {
      authenticatorData: "AA",
      clientDataJSON,
      signature: "AA",
      userHandle: "dXNlci0x",
    },
  };
}
