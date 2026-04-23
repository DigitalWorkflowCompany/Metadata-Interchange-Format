import Foundation

/// Decoded shape of ``dwc doctor --json``.
/// See ``src/dwc_sidecar/doctor.py::format_json`` — the top-level
/// status rolls up fail > warn > pass across every ``Check``.
struct DoctorReport: Codable, Equatable {
    let status: Status
    let checks: [Check]

    struct Check: Codable, Equatable {
        let status: Status
        let title:  String
        let detail: String
        let remedy: String
    }

    enum Status: String, Codable, Equatable {
        case pass
        case warn
        case fail
    }
}

extension DoctorReport {
    /// Convenience — count of checks at each level, for menu-bar summary text.
    var counts: (pass: Int, warn: Int, fail: Int) {
        checks.reduce(into: (0, 0, 0)) { acc, c in
            switch c.status {
            case .pass: acc.0 += 1
            case .warn: acc.1 += 1
            case .fail: acc.2 += 1
            }
        }
    }

    static func decode(from data: Data) throws -> DoctorReport {
        try JSONDecoder().decode(DoctorReport.self, from: data)
    }
}
