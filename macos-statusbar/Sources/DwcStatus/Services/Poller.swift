import Foundation
import Combine

/// ``AppState`` is the single ``ObservableObject`` the UI reads from.
/// Holds the latest doctor report, watch state, quarantine listing, and
/// any polling error. ``Poller`` pushes updates into it on the main actor.
@MainActor
final class AppState: ObservableObject {
    @Published var config: Config = .load() {
        didSet {
            // Mutating the watch root, dwc binary, or poll intervals must
            // restart the loops so new values take effect immediately.
            if oldValue != config { poller?.start() }
        }
    }
    @Published var doctorReport:  DoctorReport? = nil
    @Published var watchState:    WatchState?   = nil
    @Published var quarantineCount: Int         = 0
    @Published var lastError:     String?       = nil
    @Published var lastDoctorAt:  Date?         = nil
    @Published var lastWatchAt:   Date?         = nil

    /// IUO so we can take ``self`` after stored-property init completes;
    /// assigned exactly once at the bottom of ``init`` and never reset.
    private var poller: Poller!

    init() {
        if config.dwcBinary == nil {
            config.dwcBinary = Config.discoverDwcBinary()
            try? config.save()
        }
        self.poller = Poller(state: self)
        self.poller.start()
    }

    /// Menu-bar icon state, per plan §3.4.
    var overallStatus: OverallStatus {
        guard config.dwcBinary != nil else { return .grey }
        if let report = doctorReport {
            switch report.status {
            case .fail: return .red
            case .warn: return .amber
            case .pass: break
            }
        }
        if quarantineCount > 0 { return .red }
        if let watch = watchState,
           watch.emitted.contains(where: { $0.status == "quarantined" }) {
            return .amber
        }
        if doctorReport == nil && watchState == nil { return .grey }
        return .green
    }
}

/// Four-state status tint for the menu-bar icon.
enum OverallStatus {
    case green
    case amber
    case red
    case grey
}

/// Long-lived polling service. Two tasks:
///   - Doctor loop: polls ``dwc doctor --quick --json`` every ``pollDoctorSeconds``.
///   - Watch loop:  re-reads ``.watch-state.json`` and the quarantine dir
///                  every ``pollWatchStateSeconds``.
///
/// Tasks cooperate with ``stop()`` via ``Task.isCancelled`` checks — no
/// orphaned timers after the app reloads config mid-run.
@MainActor
final class Poller {
    let state: AppState
    private var doctorTask: Task<Void, Never>?
    private var watchTask:  Task<Void, Never>?

    init(state: AppState) { self.state = state }

    func start() {
        stop()
        doctorTask = Task { [state] in await Self.doctorLoop(state) }
        watchTask  = Task { [state] in await Self.watchLoop(state)  }
    }

    func stop() {
        doctorTask?.cancel(); doctorTask = nil
        watchTask?.cancel();  watchTask  = nil
    }

    /// `@MainActor` so the @Published mutations (``state.doctorReport``,
    /// ``state.lastError``) fire SwiftUI updates on the main thread —
    /// without this annotation, static methods on a @MainActor class are
    /// non-isolated, the Combine publisher fires off-main, and the UI
    /// silently misses the update.
    @MainActor
    private static func doctorLoop(_ state: AppState) async {
        while !Task.isCancelled {
            let cfg = state.config
            do {
                let report = try DwcCLI.runDoctor(
                    binary: cfg.dwcBinary,
                    workingDirectory: cfg.watchRoot
                )
                state.doctorReport = report
                state.lastDoctorAt = Date()
                state.lastError    = nil
            } catch DwcCLI.Error.binaryNotConfigured {
                state.lastError = "dwc CLI not configured"
            } catch {
                state.lastError = "dwc doctor: \(error)"
            }
            try? await Task.sleep(nanoseconds:
                UInt64(cfg.pollDoctorSeconds) * 1_000_000_000)
        }
    }

    @MainActor
    private static func watchLoop(_ state: AppState) async {
        while !Task.isCancelled {
            let cfg = state.config
            if let root = cfg.watchRoot {
                let rootURL    = URL(fileURLWithPath: root)
                let stateURL   = rootURL.appendingPathComponent(".watch-state.json")
                let quarantine = rootURL.appendingPathComponent("quarantine",
                                                                isDirectory: true)
                if let data = try? Data(contentsOf: stateURL) {
                    state.watchState = try? WatchState.decode(from: data)
                }
                let listing = try? FileManager.default.contentsOfDirectory(
                    at: quarantine, includingPropertiesForKeys: nil,
                    options: [.skipsHiddenFiles])
                state.quarantineCount = (listing ?? []).count
                state.lastWatchAt     = Date()
            }
            try? await Task.sleep(nanoseconds:
                UInt64(cfg.pollWatchStateSeconds) * 1_000_000_000)
        }
    }
}
