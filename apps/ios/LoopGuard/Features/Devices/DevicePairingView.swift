import Foundation
import SwiftUI

struct PairingChallenge: Sendable, Equatable {
    let id: String
    let challenge: Data
    let expiresAt: Date
}

struct PairingCompletionRequest: Sendable, Equatable {
    let pairingID: String
    let keyID: String
    let algorithm: String
    let publicKey: String
    let signature: String
    let name: String
}

struct PairedDevice: Sendable, Equatable {
    let id: String
    let keyID: String
    let algorithm: String
    let name: String
}

protocol DevicePairingAPI: Sendable {
    func start() async throws -> PairingChallenge
    func complete(_ request: PairingCompletionRequest) async throws -> PairedDevice
}

enum DevicePairingError: Error, Equatable {
    case challengeConsumed
    case challengeExpired
    case invalidServerResponse
}

actor DevicePairingCoordinator {
    private let api: any DevicePairingAPI
    private let keyStore: DeviceKeyStore
    private var consumedChallengeIDs: Set<String> = []

    init(api: any DevicePairingAPI, keyStore: DeviceKeyStore) {
        self.api = api
        self.keyStore = keyStore
    }

    func pair(name: String) async throws -> PairedDevice {
        let challenge = try await api.start()
        return try await complete(challenge: challenge, name: name)
    }

    func complete(challenge: PairingChallenge, name: String) async throws -> PairedDevice {
        guard challenge.expiresAt > Date() else { throw DevicePairingError.challengeExpired }
        guard consumedChallengeIDs.insert(challenge.id).inserted else {
            throw DevicePairingError.challengeConsumed
        }
        let capability = try keyStore.prepare()
        let signature = try keyStore.sign(challenge.challenge)
        return try await api.complete(
            PairingCompletionRequest(
                pairingID: challenge.id,
                keyID: capability.keyID,
                algorithm: capability.algorithm,
                publicKey: capability.publicKey.base64URLEncodedString,
                signature: signature.base64URLEncodedString,
                name: String(name.prefix(256))
            )
        )
    }
}

struct ControlDevicePairingAPI: DevicePairingAPI {
    private let api: ControlAPI

    init(api: ControlAPI) {
        self.api = api
    }

    func start() async throws -> PairingChallenge {
        let response: PairingStartPayload = try await api.request(
            path: "v1/devices/pairing/start",
            method: "POST",
            body: Data("{}".utf8)
        )
        guard let challenge = Data(base64URLEncoded: response.challenge) else {
            throw DevicePairingError.invalidServerResponse
        }
        return PairingChallenge(
            id: response.pairingID,
            challenge: challenge,
            expiresAt: response.expiresAt
        )
    }

    func complete(_ request: PairingCompletionRequest) async throws -> PairedDevice {
        let body = try JSONEncoder().encode(PairingCompletionPayload(request))
        let response: PairedDevicePayload = try await api.request(
            path: "v1/devices/pairing/complete",
            method: "POST",
            body: body
        )
        return PairedDevice(
            id: response.id,
            keyID: response.keyID,
            algorithm: response.algorithm,
            name: response.name
        )
    }
}

@MainActor
struct DevicePairingView: View {
    let coordinator: DevicePairingCoordinator
    @State private var state: PairingViewState = .ready

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            Label("Pair this device", systemImage: "lock.shield")
                .font(.title2.weight(.semibold))
            Text("Pairing registers only this device’s public signing key. Private key material never leaves the device.")
                .foregroundStyle(SemanticColor.secondaryText)
            switch state {
            case .ready:
                Button("Pair this iPhone") { Task { await pair() } }
                    .buttonStyle(LoopGuardPrimaryButtonStyle())
            case .pairing:
                ProgressView("Creating device proof")
            case .paired(let device):
                Label("Paired as \(device.name)", systemImage: "checkmark.circle.fill")
                    .foregroundStyle(SemanticColor.success)
            case .failed:
                Button("Try pairing again") { Task { await pair() } }
                    .buttonStyle(LoopGuardPrimaryButtonStyle())
            }
        }
        .padding()
        .navigationTitle("Device security")
    }

    private func pair() async {
        state = .pairing
        do {
            state = .paired(try await coordinator.pair(name: UIDevice.current.name))
        } catch {
            state = .failed
        }
    }
}

private enum PairingViewState {
    case ready
    case pairing
    case paired(PairedDevice)
    case failed
}

private struct PairingStartPayload: Decodable {
    let pairingID: String
    let challenge: String
    let expiresAt: Date

    enum CodingKeys: String, CodingKey {
        case pairingID = "pairing_id"
        case challenge
        case expiresAt = "expires_at"
    }
}

private struct PairingCompletionPayload: Encodable {
    let pairingID: String
    let publicKeyAlgorithm: String
    let publicKey: String
    let signature: String
    let name: String

    init(_ request: PairingCompletionRequest) {
        pairingID = request.pairingID
        publicKeyAlgorithm = request.algorithm
        publicKey = request.publicKey
        signature = request.signature
        name = request.name
    }

    enum CodingKeys: String, CodingKey {
        case pairingID = "pairing_id"
        case publicKeyAlgorithm = "public_key_alg"
        case publicKey = "public_key"
        case signature
        case name
    }
}

private struct PairedDevicePayload: Decodable {
    let id: String
    let keyID: String
    let algorithm: String
    let name: String

    enum CodingKeys: String, CodingKey {
        case id
        case keyID = "key_id"
        case algorithm
        case name
    }
}
