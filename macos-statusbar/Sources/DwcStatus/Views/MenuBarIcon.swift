import SwiftUI

/// Filled circle tinted by overall status — the "ambient signal" the
/// DIT glances at.
///
/// Two non-obvious things this view has to get right:
///
/// 1. ``MenuBarExtra`` only renders SF Symbol images and text in its
///    label slot; arbitrary ``Circle()`` shapes allocate space but draw
///    nothing, leaving an invisible click target. We render the
///    ``circle.fill`` SF Symbol and tint with ``foregroundColor``.
/// 2. ``MenuBarExtra``'s ``label`` closure is evaluated once at scene
///    creation and is not re-evaluated when state changes. Reading
///    ``state.overallStatus`` from outside this view (e.g. as a
///    ``status:`` parameter) leaves the icon stuck at its initial value
///    even when the model updates correctly. So the view holds an
///    ``@ObservedObject`` reference to ``AppState`` and redraws itself
///    in response to its own ``@Published`` change events.
struct MenuBarIcon: View {
    @ObservedObject var state: AppState

    var body: some View {
        // `.symbolRenderingMode(.palette) + .foregroundStyle(...)` opts the
        // SF Symbol out of `NSStatusBarButton`'s default template tinting
        // and lets us render in our actual status colour. Without this,
        // the icon renders monochrome in whatever shade the system picks
        // for the menu bar — i.e. it looks grey regardless of state.
        Image(systemName: "circle.fill")
            .symbolRenderingMode(.palette)
            .foregroundStyle(state.overallStatus.color)
            .accessibilityLabel(state.overallStatus.accessibilityLabel)
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
