import XCTest
@testable import DwcStatus

/// Plan §6.8 (Track A) test plan: three state branches, recorded
/// AppleScript, recorded pasteboard contents, idempotent recheck.
final class OnboardingTests: XCTestCase {

    // MARK: - State detection

    func testCliMissingWhenNoBinaryAndNoCandidates() {
        let fs  = MockFileSystem()             // nothing exists
        let env = MockEnvironment()            // no DWC_SIGNERS
        var cfg = Config.default
        cfg.dwcBinary = nil
        XCTAssertEqual(
            detectOnboardingState(config: cfg, fs: fs, env: env),
            .cliMissing
        )
    }

    func testCliMissingWhenConfiguredBinaryGoneFromDisk() {
        let fs  = MockFileSystem()             // configured path removed
        let env = MockEnvironment()
        var cfg = Config.default
        cfg.dwcBinary = "/nonexistent/dwc"
        XCTAssertEqual(
            detectOnboardingState(config: cfg, fs: fs, env: env),
            .cliMissing
        )
    }

    func testSignersUnconfiguredWhenCliPresentButNoSigners() {
        let fs = MockFileSystem(
            executables: ["/opt/homebrew/bin/dwc"]
        )
        let env = MockEnvironment()            // no DWC_SIGNERS
        var cfg = Config.default
        cfg.dwcBinary = "/opt/homebrew/bin/dwc"
        cfg.watchRoot = "/Volumes/SHOOT"
        XCTAssertEqual(
            detectOnboardingState(config: cfg, fs: fs, env: env),
            .signersUnconfigured
        )
    }

    func testReadyWhenSignersAtWatchRoot() {
        let fs = MockFileSystem(
            executables: ["/opt/homebrew/bin/dwc"],
            files:       ["/Volumes/SHOOT/signers.json"]
        )
        let env = MockEnvironment()
        var cfg = Config.default
        cfg.dwcBinary = "/opt/homebrew/bin/dwc"
        cfg.watchRoot = "/Volumes/SHOOT"
        XCTAssertEqual(
            detectOnboardingState(config: cfg, fs: fs, env: env),
            .ready
        )
    }

    func testReadyWhenDwcSignersEnvVarPointsAtExistingFile() {
        let fs = MockFileSystem(
            executables: ["/opt/homebrew/bin/dwc"],
            files:       ["/etc/dwc/signers.json"]
        )
        let env = MockEnvironment(values: ["DWC_SIGNERS": "/etc/dwc/signers.json"])
        var cfg = Config.default
        cfg.dwcBinary = "/opt/homebrew/bin/dwc"
        XCTAssertEqual(
            detectOnboardingState(config: cfg, fs: fs, env: env),
            .ready
        )
    }

    func testStaleEnvVarToMissingFileFallsThroughToUnconfigured() {
        // DWC_SIGNERS exported but the file it points at no longer exists —
        // an export from a previous shoot. Should not paper over the gap.
        let fs = MockFileSystem(
            executables: ["/opt/homebrew/bin/dwc"]
            // no files exist
        )
        let env = MockEnvironment(values: ["DWC_SIGNERS": "/old/path/signers.json"])
        var cfg = Config.default
        cfg.dwcBinary = "/opt/homebrew/bin/dwc"
        XCTAssertEqual(
            detectOnboardingState(config: cfg, fs: fs, env: env),
            .signersUnconfigured
        )
    }

    func testReadyViaXdgFallback() {
        let home = NSHomeDirectory()
        let fs = MockFileSystem(
            executables: ["/opt/homebrew/bin/dwc"],
            files:       ["\(home)/.config/dwc/signers.json"]
        )
        let env = MockEnvironment()
        var cfg = Config.default
        cfg.dwcBinary = "/opt/homebrew/bin/dwc"
        XCTAssertEqual(
            detectOnboardingState(config: cfg, fs: fs, env: env),
            .ready
        )
    }

    func testCliReachableViaCandidatePathEvenIfBinaryUnset() {
        // dwcBinary is nil but /opt/homebrew/bin/dwc exists — should
        // pass the CLI gate and progress to the signers check.
        let fs = MockFileSystem(
            executables: ["/opt/homebrew/bin/dwc"]
        )
        let env = MockEnvironment()
        var cfg = Config.default
        cfg.dwcBinary = nil
        cfg.watchRoot = "/Volumes/SHOOT"
        XCTAssertEqual(
            detectOnboardingState(config: cfg, fs: fs, env: env),
            .signersUnconfigured
        )
    }

    func testDetectionIsIdempotent() {
        // Repeated calls with unchanged inputs return the same result —
        // no caching, no hidden state.
        let fs = MockFileSystem(
            executables: ["/opt/homebrew/bin/dwc"],
            files:       ["/Volumes/SHOOT/signers.json"]
        )
        let env = MockEnvironment()
        var cfg = Config.default
        cfg.dwcBinary = "/opt/homebrew/bin/dwc"
        cfg.watchRoot = "/Volumes/SHOOT"
        let first  = detectOnboardingState(config: cfg, fs: fs, env: env)
        let second = detectOnboardingState(config: cfg, fs: fs, env: env)
        XCTAssertEqual(first, second)
        XCTAssertEqual(first, .ready)
    }

    // MARK: - Action strings

    func testBrewInstallCommandIsTheTapInvocation() {
        // Pin the exact command — the README, panel, formula, and
        // marketing copy must agree.
        XCTAssertEqual(
            OnboardingActions.brewInstallCommand,
            "brew install digitalworkflowcompany/tap/dwc-sidecar"
        )
    }

    func testPipxFallbackCommandIsStable() {
        XCTAssertEqual(
            OnboardingActions.pipxFallbackCommand,
            "pipx install dwc-sidecar"
        )
    }

    func testTerminalScriptWithoutWatchRootRunsBareDwcInit() {
        let source = OnboardingActions.dwcInitTerminalScript(watchRoot: nil)
        XCTAssertTrue(source.contains("do script \"dwc init\""))
        XCTAssertFalse(source.contains("cd "))
        XCTAssertTrue(source.contains("activate"))
    }

    func testTerminalScriptWithWatchRootCdsFirst() {
        let source = OnboardingActions.dwcInitTerminalScript(
            watchRoot: "/Volumes/SHOOT"
        )
        XCTAssertTrue(source.contains("cd \\\"/Volumes/SHOOT\\\""))
        XCTAssertTrue(source.contains("&& dwc init"))
    }

    func testTerminalScriptEscapesQuotesInWatchPath() {
        // A path with a literal " in it must produce a parseable script
        // (real-world DIT paths sometimes contain unusual characters).
        // Each embedded quote takes two escape passes:
        //   bash:        "  → \"
        //   AppleScript: \" → \\\"  (\ → \\, " → \")
        // so the final string contains the four-char sequence \\\" at
        // each path-quote site.
        let source = OnboardingActions.dwcInitTerminalScript(
            watchRoot: "/Volumes/Foo \"Bar\""
        )
        XCTAssertTrue(
            source.contains("\\\\\\\""),
            "expected source to contain escaped sequence \\\\\\\", got: \(source)"
        )
    }

    func testTerminalScriptEscapesBackslashesInWatchPath() {
        // A literal \ in the path is doubled by the bash layer (\\) and
        // doubled again by the AppleScript layer (\\\\), so the final
        // source contains the four-backslash sequence \\\\.
        let source = OnboardingActions.dwcInitTerminalScript(
            watchRoot: "/Volumes/Has\\Backslash"
        )
        XCTAssertTrue(
            source.contains("\\\\\\\\"),
            "expected source to contain escaped sequence \\\\\\\\, got: \(source)"
        )
    }

    // MARK: - Action plumbing

    func testPasteboardWritingProtocolRecordsTheString() {
        // The view's "copy to clipboard" goes through a PasteboardWriting;
        // tests inject a recording mock so we can assert the value
        // without touching NSPasteboard.general.
        let recorder = RecordingPasteboard()
        recorder.setString(OnboardingActions.brewInstallCommand)
        XCTAssertEqual(recorder.recorded, [OnboardingActions.brewInstallCommand])
    }

    func testAppleScriptRunningProtocolRecordsTheSource() {
        let recorder = RecordingAppleScriptRunner()
        let source = OnboardingActions.dwcInitTerminalScript(
            watchRoot: "/Volumes/SHOOT"
        )
        _ = recorder.run(source: source)
        XCTAssertEqual(recorder.recorded.count, 1)
        XCTAssertEqual(recorder.recorded.first, source)
    }
}

// MARK: - Mocks

private struct MockFileSystem: FileSystemProbing {
    var executables: Set<String> = []
    var files:       Set<String> = []

    func fileExists(atPath path: String) -> Bool {
        files.contains(path) || executables.contains(path)
    }
    func isExecutableFile(atPath path: String) -> Bool {
        executables.contains(path)
    }
}

private struct MockEnvironment: EnvironmentProbing {
    var values: [String: String] = [:]
    func value(forName name: String) -> String? { values[name] }
}

private final class RecordingPasteboard: PasteboardWriting {
    var recorded: [String] = []
    @discardableResult
    func setString(_ string: String) -> Bool {
        recorded.append(string)
        return true
    }
}

private final class RecordingAppleScriptRunner: AppleScriptRunning {
    var recorded: [String] = []
    @discardableResult
    func run(source: String) -> Bool {
        recorded.append(source)
        return true
    }
}
