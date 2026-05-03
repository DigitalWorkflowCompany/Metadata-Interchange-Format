import Foundation

/// Onboarding state surfaced by ``MenuContent`` per plan §6.3. DWC Status
/// stays read-only — this detector never writes config, never invokes
/// ``dwc init``; it only classifies the host into one of three buckets so
/// the UI can show a copy-pasteable next step.
enum OnboardingState: Equatable {
    /// No ``dwc`` CLI on PATH or any of the known install paths. Panel
    /// shows the brew install command.
    case cliMissing

    /// CLI present but no signer config discoverable. Panel shows
    /// "Open Terminal at `dwc init`".
    case signersUnconfigured

    /// CLI present and a ``signers.json`` is discoverable (env var,
    /// watch-root file, or `~/.config` location). Normal panel content.
    case ready
}

/// Probes the filesystem for path existence. Injected so tests can
/// substitute a deterministic mock and exercise every branch without
/// touching real disk. Mirrors the platform-injection seam used in
/// `init.py` (plan §5.4).
protocol FileSystemProbing {
    func fileExists(atPath path: String) -> Bool
    func isExecutableFile(atPath path: String) -> Bool
}

/// Probes process environment. Injected for the same reason as
/// ``FileSystemProbing`` — and additionally because GUI apps inherit a
/// stripped-down PATH from launchd, so a real `getenv("DWC_SIGNERS")`
/// frequently misses an export the user did add to `~/.zshrc`. The
/// detector treats env-var presence as a hint, not a contract.
protocol EnvironmentProbing {
    func value(forName name: String) -> String?
}

struct RealFileSystem: FileSystemProbing {
    func fileExists(atPath path: String) -> Bool {
        FileManager.default.fileExists(atPath: path)
    }
    func isExecutableFile(atPath path: String) -> Bool {
        FileManager.default.isExecutableFile(atPath: path)
    }
}

struct RealEnvironment: EnvironmentProbing {
    func value(forName name: String) -> String? {
        ProcessInfo.processInfo.environment[name]
    }
}

/// Classify the host into one of three onboarding states.
///
/// - ``OnboardingState/cliMissing``: ``config.dwcBinary`` is not set
///   (or not executable) AND none of the candidate install paths exist
///   in the filesystem.
/// - ``OnboardingState/signersUnconfigured``: a CLI is reachable but no
///   ``signers.json`` is discoverable via:
///     1. ``DWC_SIGNERS`` env var pointing at an existing file
///     2. ``signers.json`` at the watch root (where ``dwc init`` writes
///        it by default)
///     3. ``~/.config/dwc/signers.json`` (XDG-style fallback location)
/// - ``OnboardingState/ready``: both checks pass.
///
/// Detection is pure given the injected probes — no side effects, no
/// caching. The "Recheck" button in ``MenuContent`` re-runs this; it is
/// the only refresh path so a misconfigured setup stays visible.
func detectOnboardingState(
    config: Config,
    fs: FileSystemProbing = RealFileSystem(),
    env: EnvironmentProbing = RealEnvironment()
) -> OnboardingState {
    if !cliReachable(config: config, fs: fs) {
        return .cliMissing
    }
    if !signersDiscoverable(config: config, fs: fs, env: env) {
        return .signersUnconfigured
    }
    return .ready
}

/// True when a ``dwc`` binary is either configured-and-executable or
/// resolvable via the candidate install paths in ``Config``.
private func cliReachable(config: Config, fs: FileSystemProbing) -> Bool {
    if let bin = config.dwcBinary, fs.isExecutableFile(atPath: bin) {
        return true
    }
    for path in cliCandidatePaths() where fs.isExecutableFile(atPath: path) {
        return true
    }
    return false
}

/// Path candidates to try when ``Config/dwcBinary`` is unset. Mirrors
/// ``Config/discoverDwcBinary`` — kept in a separate function so the
/// detector can probe with mocks without invoking the real
/// ``Process``-based fallback.
func cliCandidatePaths() -> [String] {
    let home = NSHomeDirectory()
    return [
        "/opt/homebrew/bin/dwc",
        "/usr/local/bin/dwc",
        "/opt/local/bin/dwc",
        "\(home)/.local/bin/dwc",
    ]
}

/// True when at least one signer config is discoverable. We trust env-var
/// presence only when the file it points at exists; a stale export
/// shouldn't paper over a missing config.
private func signersDiscoverable(
    config: Config,
    fs: FileSystemProbing,
    env: EnvironmentProbing
) -> Bool {
    if let envPath = env.value(forName: "DWC_SIGNERS"),
       !envPath.isEmpty,
       fs.fileExists(atPath: envPath) {
        return true
    }
    if let root = config.watchRoot {
        let signersAtRoot = (root as NSString)
            .appendingPathComponent("signers.json")
        if fs.fileExists(atPath: signersAtRoot) {
            return true
        }
    }
    let home = NSHomeDirectory()
    let xdgConfig = "\(home)/.config/dwc/signers.json"
    if fs.fileExists(atPath: xdgConfig) {
        return true
    }
    return false
}
