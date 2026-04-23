import SwiftUI

/// ``@main`` entry point — a SwiftUI ``App`` whose only scene is a
/// ``MenuBarExtra``. Plan §3.2: read-only ambient signal, no window.
@main
struct DwcStatusApp: App {
    @StateObject private var state  = AppState()
    @State       private var poller: Poller?

    var body: some Scene {
        MenuBarExtra {
            MenuContent(state: state)
        } label: {
            MenuBarIcon(status: state.overallStatus)
        }
        .menuBarExtraStyle(.window)
        .onChange(of: state.config) { _ in
            // Config mutated via the menu — restart polling so the new
            // intervals / binary / watch root take effect immediately.
            poller?.start()
        }
        .commands {
            // No Start/Stop/Re-sign commands — CLI operations only (§3.2).
        }
    }

    init() {
        // Construct the poller lazily so @StateObject's state is available.
        // Actually kicked off after the first view render below via .task.
    }
}

extension DwcStatusApp {
    /// Bootstraps the poller exactly once on first appearance. SwiftUI
    /// runs .task bodies on the main actor, which matches ``Poller``'s
    /// ``@MainActor`` constraint.
    func bootstrap() {
        let p = Poller(state: state)
        p.start()
    }
}
