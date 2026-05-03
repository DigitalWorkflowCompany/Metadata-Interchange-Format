import Foundation
import AppKit

/// Side-effect-free command generation for the onboarding panel buttons
/// (plan §6.3, §6.8). The view layer composes these into actual
/// pasteboard writes and AppleScript executions; tests verify the
/// generated strings directly without launching Terminal or polluting
/// the user's pasteboard.
enum OnboardingActions {
    /// The single command we tell users to run when ``OnboardingState``
    /// is ``cliMissing``. Lives here as a constant so tests pin the
    /// exact string and the README, formula, and panel UI can't drift.
    static let brewInstallCommand =
        "brew install digitalworkflowcompany/tap/dwc-sidecar"

    /// Fallback for users without Homebrew (Linux, locked-down Macs).
    static let pipxFallbackCommand = "pipx install dwc-sidecar"

    /// AppleScript that opens Terminal and runs ``dwc init``. If the
    /// user has already configured a watch root in DWC Status, we
    /// ``cd`` there first so init writes ``signers.json`` next to the
    /// shoot's ``.watch-state.json`` (the convention from §5.3).
    ///
    /// Two escaping layers — the bash command embedded inside the
    /// AppleScript ``do script`` argument is escaped twice: once for
    /// bash (so a path with embedded ``"`` round-trips intact) and
    /// once for AppleScript's own string literal. Single-layer escaping
    /// silently corrupts paths that contain ``\`` or ``"``.
    static func dwcInitTerminalScript(watchRoot: String?) -> String {
        let bashCommand: String
        if let root = watchRoot, !root.isEmpty {
            let shellEscaped = root
                .replacingOccurrences(of: "\\", with: "\\\\")
                .replacingOccurrences(of: "\"", with: "\\\"")
            bashCommand = "cd \"\(shellEscaped)\" && dwc init"
        } else {
            bashCommand = "dwc init"
        }
        let scriptEscaped = bashCommand
            .replacingOccurrences(of: "\\", with: "\\\\")
            .replacingOccurrences(of: "\"", with: "\\\"")
        return """
        tell application "Terminal"
            do script "\(scriptEscaped)"
            activate
        end tell
        """
    }
}

/// Pasteboard write seam — protocol so tests can verify the recorded
/// string without touching the user's real pasteboard.
protocol PasteboardWriting {
    @discardableResult
    func setString(_ string: String) -> Bool
}

struct SystemPasteboard: PasteboardWriting {
    @discardableResult
    func setString(_ string: String) -> Bool {
        NSPasteboard.general.clearContents()
        return NSPasteboard.general.setString(string, forType: .string)
    }
}

/// AppleScript execution seam. Tests inject a recorder so the
/// "Open Terminal" button's effect can be asserted without a real
/// `osascript` invocation (plan §6.8).
protocol AppleScriptRunning {
    @discardableResult
    func run(source: String) -> Bool
}

struct SystemAppleScriptRunner: AppleScriptRunning {
    @discardableResult
    func run(source: String) -> Bool {
        guard let script = NSAppleScript(source: source) else { return false }
        var error: NSDictionary?
        _ = script.executeAndReturnError(&error)
        return error == nil
    }
}
