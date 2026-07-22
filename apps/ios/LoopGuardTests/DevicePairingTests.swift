import CryptoKit
import Foundation
import Testing
@testable import LoopGuard

@Suite("Device pairing", .serialized)
struct DevicePairingTests {
    @Test("software fallback is encrypted, device-only, and truthfully reported")
    func softwareFallbackMetadata() throws {
        let service = "dev.loopguard.device-tests.\(UUID().uuidString)"
        let store = DeviceKeyStore(
            keychain: KeychainStore(service: service),
            secureEnclaveAvailable: { false }
        )
        defer { try? store.revoke() }

        let capability = try store.prepare()

        #expect(capability.algorithm == "Ed25519")
        #expect(capability.secureHardware == false)
        #expect(capability.exportability == .encryptedSoftwareFallback)
        #expect(capability.publicKey.count == 32)
        #expect(try store.sign(Data("challenge".utf8)).isEmpty == false)
        let attributes = try store.keychainAttributes()
        #expect(attributes.allSatisfy { $0.synchronizable != true })
    }

    @Test("pairing challenge can only be completed once")
    func oneTimePairing() async throws {
        let api = RecordingPairingAPI()
        let keyStore = DeviceKeyStore(
            keychain: KeychainStore(service: "dev.loopguard.device-tests.\(UUID().uuidString)"),
            secureEnclaveAvailable: { false }
        )
        defer { try? keyStore.revoke() }
        let coordinator = DevicePairingCoordinator(api: api, keyStore: keyStore)
        let challenge = PairingChallenge(
            id: "pairing-one-time-challenge",
            challenge: Data("server-challenge".utf8),
            expiresAt: Date().addingTimeInterval(300)
        )

        _ = try await coordinator.complete(challenge: challenge, name: "Test iPhone")
        await #expect(throws: DevicePairingError.challengeConsumed) {
            _ = try await coordinator.complete(challenge: challenge, name: "Test iPhone")
        }
        #expect(await api.completions == 1)
    }

    @Test("revocation removes signing material")
    func revocationRemovesKey() throws {
        let store = DeviceKeyStore(
            keychain: KeychainStore(service: "dev.loopguard.device-tests.\(UUID().uuidString)"),
            secureEnclaveAvailable: { false }
        )
        _ = try store.prepare()

        try store.revoke()

        #expect(store.capability() == nil)
        #expect(throws: DeviceKeyStoreError.keyNotFound) {
            _ = try store.sign(Data("action".utf8))
        }
    }
}

private actor RecordingPairingAPI: DevicePairingAPI {
    private(set) var completions = 0

    func start() async throws -> PairingChallenge {
        PairingChallenge(
            id: "pairing-one-time-challenge",
            challenge: Data("server-challenge".utf8),
            expiresAt: Date().addingTimeInterval(300)
        )
    }

    func complete(_ request: PairingCompletionRequest) async throws -> PairedDevice {
        completions += 1
        return PairedDevice(
            id: "device-1",
            keyID: request.keyID,
            algorithm: request.algorithm,
            name: request.name
        )
    }
}
