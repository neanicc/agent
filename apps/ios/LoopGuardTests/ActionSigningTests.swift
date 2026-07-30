import CryptoKit
import Foundation
import Testing
@testable import LoopGuard

@Suite("Exact action payload signing", .serialized)
struct ActionSigningTests {
    @Test("device signature covers the server canonical payload exactly")
    func exactPayloadIsSigned() async throws {
        let keyStore = DeviceKeyStore(
            keychain: KeychainStore(service: "dev.loopguard.action-signing.\(UUID().uuidString)"),
            secureEnclaveAvailable: { false }
        )
        defer { try? keyStore.revoke() }
        let capability = try keyStore.prepare()
        let signer = DeviceActionSigner(
            deviceID: "00000000-0000-0000-0000-000000000001",
            keyStore: keyStore
        )
        let payload = ActionReviewRequest.fixture().canonicalPayload

        let proof = try await signer.sign(payload)

        let publicKey = try Curve25519.Signing.PublicKey(rawRepresentation: capability.publicKey)
        #expect(publicKey.isValidSignature(proof.signature, for: payload))
        #expect(!publicKey.isValidSignature(proof.signature, for: payload + Data([0])))
        #expect(proof.keyID == capability.keyID)
        #expect(proof.algorithm == "Ed25519")
    }

    @Test("canonical review mismatch fails before signing")
    @MainActor
    func mismatchedReviewFailsClosed() async {
        let api = RecordingActionAPI()
        let signer = RecordingActionSigner()
        let request = ActionReviewRequest.fixture(expectedStateVersion: 99)
        let model = ActionViewModel(
            request: request,
            api: api,
            signer: signer,
            now: { Date(timeIntervalSince1970: 100) }
        )

        await model.submit()

        #expect(model.state == .stale)
        #expect(await signer.payloads.isEmpty)
        #expect(await api.submittedActionIDs.isEmpty)
    }
}
