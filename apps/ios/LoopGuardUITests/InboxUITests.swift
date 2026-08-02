import XCTest

@MainActor
final class InboxUITests: XCTestCase {
    func testInboxPrioritizesAttentionWithoutInventingRepairs() throws {
        let app = launchApp()
        XCTAssertTrue(app.navigationBars["Inbox"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["Needs attention"].exists)
        XCTAssertTrue(app.staticTexts["Regression needs review"].exists)
        XCTAssertTrue(app.staticTexts["Quiet completions"].exists)
        XCTAssertFalse(app.tabBars.buttons["Repairs"].exists)

        let screenshot = XCTAttachment(screenshot: app.screenshot())
        screenshot.name = "Inbox — attention and quiet completion"
        screenshot.lifetime = .keepAlways
        add(screenshot)
    }

    func testInboxOpensProofFirstRunDetail() throws {
        let app = launchApp()
        app.staticTexts["Regression needs review"].tap()

        XCTAssertTrue(app.navigationBars["Run detail"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["Verification proof"].exists)
        app.swipeUp()
        XCTAssertTrue(app.staticTexts["Timeline"].waitForExistence(timeout: 3))
        attachScreenshot(of: app, named: "Run detail — proof before timeline")
    }

    func testLoadingEmptyErrorStaleAndResyncStatesAreExplicit() throws {
        let loading = launchApp(scenario: "loading")
        XCTAssertTrue(loading.staticTexts["Loading"].waitForExistence(timeout: 5))

        let empty = launchApp(scenario: "empty")
        XCTAssertTrue(empty.staticTexts["Nothing needs attention"].waitForExistence(timeout: 5))

        let error = launchApp(scenario: "error")
        XCTAssertTrue(error.staticTexts["Couldn’t load this view"].waitForExistence(timeout: 5))
        XCTAssertTrue(error.staticTexts["Request req_ui_fixture"].exists)

        let stale = launchApp(scenario: "stale")
        XCTAssertTrue(stale.staticTexts["Showing stale data"].waitForExistence(timeout: 5))

        let resync = launchApp(scenario: "resync")
        resync.staticTexts["Regression needs review"].tap()
        resync.swipeUp()
        XCTAssertTrue(resync.staticTexts["Resyncing missing events"].waitForExistence(timeout: 5))
    }

    private func launchApp(scenario: String? = nil) -> XCUIApplication {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchEnvironment["LOOPGUARD_UI_TEST_MODE"] = "1"
        if let scenario {
            app.launchEnvironment["LOOPGUARD_UI_TEST_STATE"] = scenario
        }
        app.launchArguments += [
            "-UIPreferredContentSizeCategoryName",
            "UICTContentSizeCategoryL",
        ]
        app.launch()
        return app
    }

    private func attachScreenshot(of app: XCUIApplication, named name: String) {
        let screenshot = XCTAttachment(screenshot: app.screenshot())
        screenshot.name = name
        screenshot.lifetime = .keepAlways
        add(screenshot)
    }
}
