import CryptoKit
import Foundation
import Security

enum IDTokenValidationError: Error, Equatable {
    case malformed
    case unsupportedAlgorithm
    case keyNotFound
    case invalidSignature
    case invalidClaims
}

actor RemoteJWKSIDTokenValidator: IDTokenValidating {
    private let session: URLSession
    private var cache: [URL: JWKSet] = [:]

    init(session: URLSession = .shared) {
        self.session = session
    }

    func validate(
        _ token: String,
        configuration: OIDCConfiguration
    ) async throws -> IDTokenClaims {
        let segments = token.split(separator: ".", omittingEmptySubsequences: false)
        guard segments.count == 3,
              let headerData = Data(base64URLEncoded: String(segments[0])),
              let payloadData = Data(base64URLEncoded: String(segments[1])),
              let signature = Data(base64URLEncoded: String(segments[2]))
        else {
            throw IDTokenValidationError.malformed
        }
        let header = try JSONDecoder().decode(JWTHeader.self, from: headerData)
        let payload = try JSONDecoder().decode(JWTPayload.self, from: payloadData)
        guard ["RS256", "PS256", "ES256"].contains(header.algorithm), !header.keyID.isEmpty else {
            throw IDTokenValidationError.unsupportedAlgorithm
        }
        let set = try await keys(at: configuration.jwksEndpoint)
        guard let key = set.keys.first(where: {
            $0.keyID == header.keyID
                && ($0.algorithm == nil || $0.algorithm == header.algorithm)
                && ($0.use == nil || $0.use == "sig")
        }) else {
            throw IDTokenValidationError.keyNotFound
        }
        let signedData = Data("\(segments[0]).\(segments[1])".utf8)
        guard try Self.verify(
            algorithm: header.algorithm,
            key: key,
            signedData: signedData,
            signature: signature
        ) else {
            throw IDTokenValidationError.invalidSignature
        }
        let expectedIssuer = configuration.issuer.absoluteString
            .trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        guard payload.issuer == expectedIssuer,
              payload.audience.values.contains(configuration.clientID),
              payload.expiresAt > Date().timeIntervalSince1970,
              !payload.subject.isEmpty,
              !payload.nonce.isEmpty
        else {
            throw IDTokenValidationError.invalidClaims
        }
        return IDTokenClaims(
            issuer: payload.issuer,
            subject: payload.subject,
            audience: payload.audience.values,
            expiresAt: payload.expiresAt,
            nonce: payload.nonce
        )
    }

    private func keys(at url: URL) async throws -> JWKSet {
        if let cached = cache[url] { return cached }
        var request = URLRequest(url: url)
        request.cachePolicy = .reloadRevalidatingCacheData
        request.timeoutInterval = 15
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse,
              (200 ..< 300).contains(http.statusCode),
              data.count <= 1_048_576
        else {
            throw IDTokenValidationError.keyNotFound
        }
        let set = try JSONDecoder().decode(JWKSet.self, from: data)
        guard set.keys.count <= 100 else { throw IDTokenValidationError.keyNotFound }
        cache[url] = set
        return set
    }

    private static func verify(
        algorithm: String,
        key: JWK,
        signedData: Data,
        signature: Data
    ) throws -> Bool {
        switch algorithm {
        case "ES256":
            guard key.keyType == "EC", key.curve == "P-256",
                  let x = key.x.flatMap(Data.init(base64URLEncoded:)), x.count == 32,
                  let y = key.y.flatMap(Data.init(base64URLEncoded:)), y.count == 32,
                  signature.count == 64
            else {
                throw IDTokenValidationError.keyNotFound
            }
            var representation = Data([0x04])
            representation.append(x)
            representation.append(y)
            let publicKey = try P256.Signing.PublicKey(x963Representation: representation)
            let parsedSignature = try P256.Signing.ECDSASignature(rawRepresentation: signature)
            return publicKey.isValidSignature(parsedSignature, for: signedData)
        case "RS256", "PS256":
            guard key.keyType == "RSA",
                  let modulus = key.modulus.flatMap(Data.init(base64URLEncoded:)),
                  let exponent = key.exponent.flatMap(Data.init(base64URLEncoded:)),
                  let publicKey = rsaPublicKey(modulus: modulus, exponent: exponent)
            else {
                throw IDTokenValidationError.keyNotFound
            }
            let secAlgorithm: SecKeyAlgorithm = algorithm == "RS256"
                ? .rsaSignatureMessagePKCS1v15SHA256
                : .rsaSignatureMessagePSSSHA256
            guard SecKeyIsAlgorithmSupported(publicKey, .verify, secAlgorithm) else {
                throw IDTokenValidationError.unsupportedAlgorithm
            }
            var error: Unmanaged<CFError>?
            let valid = SecKeyVerifySignature(
                publicKey,
                secAlgorithm,
                signedData as CFData,
                signature as CFData,
                &error
            )
            return valid && error == nil
        default:
            throw IDTokenValidationError.unsupportedAlgorithm
        }
    }

    private static func rsaPublicKey(modulus: Data, exponent: Data) -> SecKey? {
        let encoded = asn1Sequence(asn1Integer(modulus) + asn1Integer(exponent))
        let attributes: [CFString: Any] = [
            kSecAttrKeyType: kSecAttrKeyTypeRSA,
            kSecAttrKeyClass: kSecAttrKeyClassPublic,
            kSecAttrKeySizeInBits: modulus.count * 8,
        ]
        var error: Unmanaged<CFError>?
        return SecKeyCreateWithData(encoded as CFData, attributes as CFDictionary, &error)
    }

    private static func asn1Integer(_ value: Data) -> Data {
        var payload = Data(value.drop(while: { $0 == 0 }))
        if payload.isEmpty { payload = Data([0]) }
        if let first = payload.first, first & 0x80 != 0 { payload.insert(0, at: 0) }
        return Data([0x02]) + asn1Length(payload.count) + payload
    }

    private static func asn1Sequence(_ value: Data) -> Data {
        Data([0x30]) + asn1Length(value.count) + value
    }

    private static func asn1Length(_ length: Int) -> Data {
        if length < 128 { return Data([UInt8(length)]) }
        var value = length
        var bytes: [UInt8] = []
        while value > 0 {
            bytes.insert(UInt8(value & 0xff), at: 0)
            value >>= 8
        }
        return Data([0x80 | UInt8(bytes.count)] + bytes)
    }
}

private struct JWTHeader: Decodable {
    let algorithm: String
    let keyID: String

    enum CodingKeys: String, CodingKey {
        case algorithm = "alg"
        case keyID = "kid"
    }
}

private struct JWTPayload: Decodable {
    let issuer: String
    let subject: String
    let audience: Audience
    let expiresAt: TimeInterval
    let nonce: String

    enum CodingKeys: String, CodingKey {
        case issuer = "iss"
        case subject = "sub"
        case audience = "aud"
        case expiresAt = "exp"
        case nonce
    }
}

private struct Audience: Decodable {
    let values: [String]

    init(from decoder: any Decoder) throws {
        let container = try decoder.singleValueContainer()
        if let value = try? container.decode(String.self) {
            values = [value]
        } else {
            values = try container.decode([String].self)
        }
    }
}

private struct JWKSet: Decodable {
    let keys: [JWK]
}

private struct JWK: Decodable {
    let keyID: String
    let keyType: String
    let algorithm: String?
    let use: String?
    let curve: String?
    let x: String?
    let y: String?
    let modulus: String?
    let exponent: String?

    enum CodingKeys: String, CodingKey {
        case keyID = "kid"
        case keyType = "kty"
        case algorithm = "alg"
        case use
        case curve = "crv"
        case x
        case y
        case modulus = "n"
        case exponent = "e"
    }
}
