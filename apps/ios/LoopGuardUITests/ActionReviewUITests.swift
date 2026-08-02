import XCTest

@MainActor
final class ActionReviewUITests: XCTestCase {
    func testApprovalShowsImmutableFactsAndQueuedIsNotExecution() {
        let app = launchApp()

        XCTAssertTrue(app.staticTexts["Continue once needs approval"].waitForExistence(timeout: 5))
        app.staticTexts["Continue once needs approval"].tap()
        XCTAssertTrue(app.navigationBars["Action review"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["EXPLICIT CONFIRMATION"].exists)
        XCTAssertTrue(app.staticTexts["Continue this paused run once from its verified state."].exists)
        app.swipeUp()
        XCTAssertTrue(app.descendants(matching: .any)["action-parameters"].waitForExistence(timeout: 3))
        XCTAssertTrue(app.descendants(matching: .any)["action-expected-state"].exists)

        app.swipeUp()
        let approve = app.buttons["action-approve-button"]
        XCTAssertTrue(approve.waitForExistence(timeout: 3))
        approve.tap()
        XCTAssertTrue(
            app.descendants(matching: .any)["action-current-state-queued"]
                .waitForExistence(timeout: 5)
        )
        XCTAssertFalse(app.staticTexts["Execution receipt"].exists)

        let screenshot = XCTAttachment(screenshot: app.screenshot())
        screenshot.name = "Action review — queued is not execution"
        screenshot.lifetime = .keepAlways
        add(screenshot)
    }

    private func launchApp() -> XCUIApplication {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchEnvironment["LOOPGUARD_UI_TEST_MODE"] = "1"
        app.launchEnvironment["LOOPGUARD_UI_TEST_STATE"] = "action"
        app.launchArguments += [
            "-UIPreferredContentSizeCategoryName",
            "UICTContentSizeCategoryL",
        ]
        app.launch()
        return app
    }
}
