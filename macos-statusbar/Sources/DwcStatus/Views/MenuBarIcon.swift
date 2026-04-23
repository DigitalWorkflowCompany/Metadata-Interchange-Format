import SwiftUI

/// Filled circle tinted by overall status — the "ambient signal" the
/// DIT glances at. macOS renders a monochrome version on the menu bar
/// automatically when the content is plain text or SF Symbol; we use
/// a plain circle with explicit colour so tinting is predictable
/// across light/dark mode and auto-hiding menu bars.
struct MenuBarIcon: View {
    let status: OverallStatus

    var body: some View {
        Circle()
            .fill(status.color)
            .frame(width: 12, height: 12)
            .accessibilityLabel(status.accessibilityLabel)
    }
}

extension OverallStatus {
    var color: Color {
        switch self {
        case .green: return .green
        case .amber: return .orange
        case .red:   return .red
        case .grey:  return .gray
        }
    }

    var accessibilityLabel: String {
        switch self {
        case .green: return "DWC watcher healthy"
        case .amber: return "DWC watcher warning"
        case .red:   return "DWC watcher failing"
        case .grey:  return "DWC CLI not available"
        }
    }
}
