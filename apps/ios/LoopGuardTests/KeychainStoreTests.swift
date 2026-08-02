import Foundation
import Security
import Testing
@testable import LoopGuard

@Suite("Keychain credentials", .serialized)
struct KeychainStoreTests {
    @Test("credentials are device-only and non-synchronizable")
    func deviceOnlyCredential() throws {
        let service = "dev.loopguard.tests.\(UUID().uuidString)"
        let store = KeychainStore(service: service)
        defer { try? store.remove("access-token") }

        try store.set(Data("secret".utf8), for: "access-token")
        let attributes = try store.attributes(for: "access-token")

        #expect(
            attributes[kSecAttrAccessible as String] as? String
                == (kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly as String)
        )
        #expect(attributes[kSecAttrSynchronizable as String] as? Bool != true)
        #expect(try store.data(for: "access-token") == Data("secret".utf8))
    }
}
