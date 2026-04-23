import Foundation

/// Decoded shape of ``.watch-state.json`` produced by ``dwc watch`` (plan §1.8).
///
/// The ``emitted`` array was added in phase 02 §1 — older state files
/// without it must still decode cleanly, hence the optional with a
/// default-to-empty accessor below.
struct WatchState: Codable, Equatable {
    let processedMhlSha256: [String]
    let emittedRaw:         [Emission]?
    let savedAt:            String?

    var emitted: [Emission] { emittedRaw ?? [] }

    struct Emission: Codable, Equatable, Identifiable {
        let clipName: String
        let omcPath:  String
        let signedAt: String
        let status:   String

        /// Derived — used as a SwiftUI ``ForEach`` identity so ordering
        /// changes don't reshuffle list rows.
        var id: String { omcPath + "@" + signedAt }
    }

    enum CodingKeys: String, CodingKey {
        case processedMhlSha256 = "processed_mhl_sha256"
        case emittedRaw         = "emitted"
        case savedAt            = "savedAt"
    }
}

extension WatchState {
    static func decode(from data: Data) throws -> WatchState {
        try JSONDecoder().decode(WatchState.self, from: data)
    }

    /// Count of quarantined emissions in the ring buffer.
    var quarantinedCount: Int {
        emitted.filter { $0.status == "quarantined" }.count
    }

    /// Most recent emissions, newest first, limited to ``limit`` entries.
    func recent(limit: Int = 5) -> [Emission] {
        Array(emitted.suffix(limit).reversed())
    }
}
