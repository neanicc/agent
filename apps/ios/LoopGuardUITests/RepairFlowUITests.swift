import XCTest

@MainActor
final class RepairFlowUITests: XCTestCase {
    func testReadyRepairCapabilityActivatesEvidenceAndSignedPublicationReview() {
        let app = XCUIApplication()
        app.launchEnvironment["LOOPGUARD_UI_TEST_MODE"] = "1"
        app.launchEnvironment["LOOPGUARD_UI_TEST_STATE"] = "repair-ready"
        app.launchArguments += [
            "-UIPreferredContentSizeCategoryName",
            "UICTContentSizeCategoryAccessibilityL",
            "-UIAccessibilityReduceMotionEnabled", "YES",
        ]
        app.launch()

        XCTAssertTrue(
            app.buttons["Repairs"].firstMatch.waitForExistence(timeout: 5)
        )
        app.buttons["Repairs"].firstMatch.tap()
        XCTAssertTrue(app.navigationBars["Repairs"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["Pipeline repair 018f0000"].exists)
        app.staticTexts["Pipeline repair 018f0000"].tap()

        XCTAssertTrue(
            app.navigationBars["Repair detail"].waitForExistence(timeout: 5)
        )
        XCTAssertTrue(scrollTo(app.staticTexts["Reproduction proof"], in: app))
        XCTAssertTrue(scrollTo(app.staticTexts["Candidate evidence"], in: app))
        XCTAssertTrue(
            scrollTo(
                app.staticTexts["Smallest fully verified compatible patch."],
                in: app
            )
        )
        let review = app.buttons["repair-publication-review"]
        XCTAssertTrue(scrollTo(review, in: app))
        review.tap()
        XCTAssertTrue(
            app.navigationBars["Action review"].waitForExistence(timeout: 5)
        )
        XCTAssertTrue(app.staticTexts["Publish the verified repair as a draft pull request."].exists)
        XCTAssertTrue(scrollTo(app.buttons["Confirm & approve"], in: app))
        attachScreenshot(of: app, named: "Repair publication — accessibility review")
    }

    func testCoreFixtureKeepsRepairsHiddenWithoutReadyCapability() {
        let app = XCUIApplication()
        app.launchEnvironment["LOOPGUARD_UI_TEST_MODE"] = "1"
        app.launch()

        XCTAssertTrue(app.buttons["Runs"].firstMatch.waitForExistence(timeout: 5))
        XCTAssertFalse(app.buttons["Repairs"].firstMatch.exists)
    }

    private func attachScreenshot(of app: XCUIApplication, named name: String) {
        let screenshot = XCTAttachment(screenshot: app.screenshot())
        screenshot.name = name
        screenshot.lifetime = .keepAlways
        add(screenshot)
    }

    private func scrollTo(
        _ element: XCUIElement,
        in app: XCUIApplication,
        maximumSwipes: Int = 10
    ) -> Bool {
        for _ in 0 ..< maximumSwipes {
            if element.exists, element.isHittable { return true }
            app.swipeUp()
        }
        return element.exists && element.isHittable
    }
}
