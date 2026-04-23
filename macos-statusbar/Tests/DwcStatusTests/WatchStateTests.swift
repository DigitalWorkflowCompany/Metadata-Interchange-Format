import XCTest
@testable import DwcStatus

final class WatchStateTests: XCTestCase {

    func fixture(_ name: String) throws -> Data {
        let url = Bundle.module.url(forResource: name, withExtension: "json",
                                    subdirectory: "Fixtures")
        guard let url else {
            XCTFail("fixture \(name).json not found — run tools/macos-statusbar/sync_fixtures.py")
            throw NSError(domain: "missing", code: 0)
        }
        return try Data(contentsOf: url)
    }

    func testDecodesRichState() throws {
        let state = try WatchState.decode(from: fixture("watchstate_full"))
        XCTAssertFalse(state.processedMhlSha256.isEmpty)
        XCTAssertFalse(state.emitted.isEmpty)
        // emitted is stored oldest→newest; the most recent clip is .last
        XCTAssertEqual(state.emitted.last?.clipName, "A001_C042_0420AB")
        // recent() reverses so newest is at index 0
        XCTAssertEqual(state.recent(limit: 1).first?.clipName, "A001_C042_0420AB")
    }

    func testOlderFileWithoutEmittedDecodes() throws {
        // §1.8 contract: state files predating the emitted field must
        // still load; emitted defaults to [].
        let state = try WatchState.decode(from: fixture("watchstate_legacy"))
        XCTAssertFalse(state.processedMhlSha256.isEmpty)
        XCTAssertEqual(state.emitted, [])
    }

    func testQuarantinedCountCountsMatchingStatus() throws {
        let state = try WatchState.decode(from: fixture("watchstate_mixed_status"))
        XCTAssertEqual(state.quarantinedCount, 2)
    }

    func testRecentReturnsNewestFirst() throws {
        let state = try WatchState.decode(from: fixture("watchstate_full"))
        let recent = state.recent(limit: 3)
        XCTAssertLessThanOrEqual(recent.count, 3)
        if recent.count > 1 {
            // Sorted newest-first by list order — last element of
            // original emitted comes out at index 0
            let raw = state.emittedRaw ?? []
            XCTAssertEqual(recent.first?.omcPath, raw.last?.omcPath)
        }
    }
}
