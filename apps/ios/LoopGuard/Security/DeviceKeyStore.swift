import CryptoKit
import Foundation
import Security

enum DeviceKeyExportability: String, Sendable, Equatable {
    case secureEnclaveBound
    case encryptedSoftwareFallback
}

struct DeviceKeyCapability: Sendable, Equatable {
    let keyID: String
    let algorithm: String
    let publicKey: Data
    let secureHardware: Bool
    let exportability: DeviceKeyExportability
}

struct KeychainAttributeSummary: Sendable, Equatable {
    let account: String
    let synchronizable: Bool?
    let accessibility: String?
}

enum DeviceKeyStoreError: Error, Equatable {
    case keyNotFound
    case invalidKeyMaterial
    case secureRandomFailed
}

struct DeviceKeyStore: Sendable {
    private static let secureKeyAccount = "device-signing-secure-enclave"
    private static let softwareKeyAccount = "device-signing-ed25519-encrypted"
    private static let wrappingKeyAccount = "device-signing-wrapping-key"
    private static let deviceIDAccount = "device-signing-registration-id"

    let keychain: KeychainStore
    private let secureEnclaveAvailable: @Sendable () -> Bool

    init(
        keychain: KeychainStore,
        secureEnclaveAvailable: @escaping @Sendable () -> Bool = { SecureEnclave.isAvailable }
    ) {
        self.keychain = keychain
        self.secureEnclaveAvailable = secureEnclaveAvailable
    }

    func prepare() throws -> DeviceKeyCapability {
        if let existing = capability() { return existing }
        if secureEnclaveAvailable() {
            do {
                let key = try SecureEnclave.P256.Signing.PrivateKey()
                try keychain.set(key.dataRepresentation, for: Self.secureKeyAccount)
                return Self.secureCapability(key)
            } catch {
                try? keychain.remove(Self.secureKeyAccount)
            }
        }
        let privateKey = Curve25519.Signing.PrivateKey()
        let wrappingKey = SymmetricKey(size: .bits256)
        let encrypted = try ChaChaPoly.seal(privateKey.rawRepresentation, using: wrappingKey)
        let combined = encrypted.combined
        let wrappingData = wrappingKey.withUnsafeBytes { Data($0) }
        try keychain.set(wrappingData, for: Self.wrappingKeyAccount)
        try keychain.set(combined, for: Self.softwareKeyAccount)
        return Self.softwareCapability(privateKey)
    }

    func capability() -> DeviceKeyCapability? {
        if let representation = try? keychain.data(for: Self.secureKeyAccount),
           let key = try? SecureEnclave.P256.Signing.PrivateKey(dataRepresentation: representation)
        {
            return Self.secureCapability(key)
        }
        if let key = try? softwarePrivateKey() {
            return Self.softwareCapability(key)
        }
        return nil
    }

    func sign(_ payload: Data) throws -> Data {
        if let representation = try keychain.data(for: Self.secureKeyAccount) {
            let key = try SecureEnclave.P256.Signing.PrivateKey(dataRepresentation: representation)
            return try key.signature(for: payload).derRepresentation
        }
        guard try keychain.data(for: Self.softwareKeyAccount) != nil else {
            throw DeviceKeyStoreError.keyNotFound
        }
        return try softwarePrivateKey().signature(for: payload)
    }

    func register(deviceID: String) throws {
        guard UUID(uuidString: deviceID) != nil else {
            throw DeviceKeyStoreError.invalidKeyMaterial
        }
        try keychain.set(Data(deviceID.lowercased().utf8), for: Self.deviceIDAccount)
    }

    func pairedDeviceID() throws -> String {
        guard let data = try keychain.data(for: Self.deviceIDAccount),
              let value = String(data: data, encoding: .utf8),
              UUID(uuidString: value) != nil
        else {
            throw DeviceKeyStoreError.keyNotFound
        }
        return value
    }

    func revoke() throws {
        for account in [
            Self.secureKeyAccount,
            Self.softwareKeyAccount,
            Self.wrappingKeyAccount,
            Self.deviceIDAccount,
        ] {
            try keychain.remove(account)
        }
    }

    func keychainAttributes() throws -> [KeychainAttributeSummary] {
        try [
            Self.secureKeyAccount,
            Self.softwareKeyAccount,
            Self.wrappingKeyAccount,
            Self.deviceIDAccount,
        ].compactMap { account in
            guard (try keychain.data(for: account)) != nil else { return nil }
            let attributes = try keychain.attributes(for: account)
            return KeychainAttributeSummary(
                account: account,
                synchronizable: attributes[kSecAttrSynchronizable as String] as? Bool,
                accessibility: attributes[kSecAttrAccessible as String] as? String
            )
        }
    }

    private func softwarePrivateKey() throws -> Curve25519.Signing.PrivateKey {
        guard let combined = try keychain.data(for: Self.softwareKeyAccount),
              let wrappingData = try keychain.data(for: Self.wrappingKeyAccount)
        else {
            throw DeviceKeyStoreError.keyNotFound
        }
        let box = try ChaChaPoly.SealedBox(combined: combined)
        let raw = try ChaChaPoly.open(box, using: SymmetricKey(data: wrappingData))
        return try Curve25519.Signing.PrivateKey(rawRepresentation: raw)
    }

    private static func secureCapability(
        _ key: SecureEnclave.P256.Signing.PrivateKey
    ) -> DeviceKeyCapability {
        capability(
            algorithm: "P-256",
            publicKey: key.publicKey.x963Representation,
            secureHardware: true,
            exportability: .secureEnclaveBound
        )
    }

    private static func softwareCapability(
        _ key: Curve25519.Signing.PrivateKey
    ) -> DeviceKeyCapability {
        capability(
            algorithm: "Ed25519",
            publicKey: key.publicKey.rawRepresentation,
            secureHardware: false,
            exportability: .encryptedSoftwareFallback
        )
    }

    private static func capability(
        algorithm: String,
        publicKey: Data,
        secureHardware: Bool,
        exportability: DeviceKeyExportability
    ) -> DeviceKeyCapability {
        let digest = Data(SHA256.hash(data: publicKey)).prefix(16).map { String(format: "%02x", $0) }.joined()
        return DeviceKeyCapability(
            keyID: "dk_\(digest)",
            algorithm: algorithm,
            publicKey: publicKey,
            secureHardware: secureHardware,
            exportability: exportability
        )
    }
}
