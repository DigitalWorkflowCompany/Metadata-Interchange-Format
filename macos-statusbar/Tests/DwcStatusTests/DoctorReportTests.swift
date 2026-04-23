import XCTest
@testable import DwcStatus

final class DoctorReportTests: XCTestCase {

    func fixture(_ name: String) throws -> Data {
        let url = Bundle.module.url(forResource: name, withExtension: "json",
                                    subdirectory: "Fixtures")
        guard let url else {
            XCTFail("fixture \(name).json not found — run tools/macos-statusbar/sync_fixtures.py")
            throw NSError(domain: "missing", code: 0)
        }
        return try Data(contentsOf: url)
    }

    func testDecodesAllPassReport() throws {
        let report = try DoctorReport.decode(from: fixture("doctor_all_pass"))
        XCTAssertEqual(report.status, .pass)
        XCTAssertFalse(report.checks.isEmpty)
        XCTAssertEqual(report.counts.fail, 0)
        XCTAssertEqual(report.counts.warn, 0)
    }

    func testDecodesMixedStatusReport() throws {
        let report = try DoctorReport.decode(from: fixture("doctor_mixed"))
        XCTAssertEqual(report.status, .warn)
        XCTAssertGreaterThan(report.counts.warn, 0)
        XCTAssertEqual(report.counts.fail, 0)
    }

    func testDecodesFailingReport() throws {
        let report = try DoctorReport.decode(from: fixture("doctor_failing"))
        XCTAssertEqual(report.status, .fail)
        XCTAssertGreaterThan(report.counts.fail, 0)
    }

    func testCountsRollUpCorrectly() throws {
        let report = DoctorReport(
            status: .warn,
            checks: [
                .init(status: .pass, title: "a", detail: "", remedy: ""),
                .init(status: .pass, title: "b", detail: "", remedy: ""),
                .init(status: .warn, title: "c", detail: "", remedy: ""),
                .init(status: .fail, title: "d", detail: "", remedy: ""),
            ]
        )
        XCTAssertEqual(report.counts.pass, 2)
        XCTAssertEqual(report.counts.warn, 1)
        XCTAssertEqual(report.counts.fail, 1)
    }
}
