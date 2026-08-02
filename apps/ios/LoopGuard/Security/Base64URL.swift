import Foundation

extension Data {
    init?(base64URLEncoded value: String) {
        guard value.range(of: "^[A-Za-z0-9_-]*={0,2}$", options: .regularExpression) != nil else {
            return nil
        }
        let base64 = value
            .replacingOccurrences(of: "-", with: "+")
            .replacingOccurrences(of: "_", with: "/")
            .padding(toLength: ((value.count + 3) / 4) * 4, withPad: "=", startingAt: 0)
        self.init(base64Encoded: base64)
    }

    var base64URLEncodedString: String {
        base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }
}
