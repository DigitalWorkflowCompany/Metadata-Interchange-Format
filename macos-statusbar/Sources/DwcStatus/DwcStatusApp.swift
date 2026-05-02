import SwiftUI

/// ``@main`` entry point — a SwiftUI ``App`` whose only scene is a
/// ``MenuBarExtra``. Plan §3.2: read-only ambient signal, no window.
/// ``AppState`` owns the polling loops — they're spun up in its init,
/// so they're running before the first view renders.
@main
struct DwcStatusApp: App {
    @StateObject private var state = AppState()

    var body: some Scene {
        MenuBarExtra {
            MenuContent(state: state)
        } label: {
            MenuBarIcon(state: state)
        }
        .menuBarExtraStyle(.window)
        .commands {
            // No Start/Stop/Re-sign commands — CLI operations only (§3.2).
        }
    }
}
