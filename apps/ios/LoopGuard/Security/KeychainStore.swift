import Foundation
import Security

struct KeychainStore: Sendable {
    let service: String

    init(service: String) {
        precondition(!service.isEmpty)
        self.service = service
    }

    func set(_ data: Data, for account: String) throws {
        guard !account.isEmpty else { throw KeychainStoreError.invalidAccount }
        try? remove(account)
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
            kSecAttrSynchronizable as String: kCFBooleanFalse as Any,
            kSecValueData as String: data,
        ]
        try check(SecItemAdd(query as CFDictionary, nil))
    }

    func data(for account: String) throws -> Data? {
        var result: CFTypeRef?
        var query = baseQuery(account: account)
        query[kSecReturnData as String] = kCFBooleanTrue
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        if status == errSecItemNotFound { return nil }
        try check(status)
        guard let data = result as? Data else { throw KeychainStoreError.invalidResult }
        return data
    }

    func attributes(for account: String) throws -> [String: Any] {
        var result: CFTypeRef?
        var query = baseQuery(account: account)
        query[kSecReturnAttributes as String] = kCFBooleanTrue
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        try check(status)
        guard let attributes = result as? [String: Any] else { throw KeychainStoreError.invalidResult }
        return attributes
    }

    func remove(_ account: String) throws {
        let status = SecItemDelete(baseQuery(account: account) as CFDictionary)
        if status != errSecItemNotFound { try check(status) }
    }

    private func baseQuery(account: String) -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecAttrSynchronizable as String: kSecAttrSynchronizableAny,
        ]
    }

    private func check(_ status: OSStatus) throws {
        guard status == errSecSuccess else { throw KeychainStoreError.status(status) }
    }
}

enum KeychainStoreError: Error, Equatable {
    case invalidAccount
    case invalidResult
    case status(OSStatus)
}
