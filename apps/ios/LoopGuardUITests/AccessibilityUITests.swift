import XCTest

@MainActor
final class AccessibilityUITests: XCTestCase {
    func testEveryVisibleActionHasAUsefulAccessibilityLabel() {
        let app = launchApp()
        XCTAssertTrue(app.navigationBars["Inbox"].waitForExistence(timeout: 5))

        assertVisibleActionsAreNamed(in: app, screen: "Inbox")
        app.buttons["Runs"].firstMatch.tap()
        XCTAssertTrue(app.navigationBars["Runs"].waitForExistence(timeout: 5))
        assertVisibleActionsAreNamed(in: app, screen: "Runs")

        app.buttons["Settings"].firstMatch.tap()
        XCTAssertTrue(app.navigationBars["Settings"].waitForExistence(timeout: 5))
        assertVisibleActionsAreNamed(in: app, screen: "Settings")
    }

    func testLargestAccessibilitySizeKeepsPrimaryFlowsHittable() {
        let app = launchApp(contentSize: "UICTContentSizeCategoryAccessibilityXXXL")
        XCTAssertTrue(app.navigationBars["Inbox"].waitForExistence(timeout: 5))

        for destination in ["Inbox", "Runs", "Changes", "Settings"] {
            let tab = app.buttons[destination].firstMatch
            XCTAssertTrue(tab.exists, "\(destination) tab should exist at the largest content size")
            XCTAssertTrue(tab.isHittable, "\(destination) tab should remain hittable at the largest content size")
        }

        app.staticTexts["Regression needs review"].tap()
        XCTAssertTrue(app.navigationBars["Run detail"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.descendants(matching: .any)["run-detail-screen"].exists)

        let screenshot = XCTAttachment(screenshot: app.screenshot())
        screenshot.name = "Run detail — largest Dynamic Type"
        screenshot.lifetime = .keepAlways
        add(screenshot)
    }

    private func assertVisibleActionsAreNamed(in app: XCUIApplication, screen: String) {
        let actionableTypes: [XCUIElement.ElementType] = [.button, .link, .switch, .textField, .secureTextField]
        for type in actionableTypes {
            let elements = app.descendants(matching: type).allElementsBoundByIndex.filter(\.exists)
            for element in elements where element.label.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                let namedRepresentationSharesFrame = elements.contains { candidate in
                    !candidate.label.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                        && candidate.frame.insetBy(dx: -1, dy: -1).intersects(element.frame)
                }
                XCTAssertTrue(
                    namedRepresentationSharesFrame,
                    "\(screen) has an unnamed visible \(String(describing: type)) control: \(element.debugDescription)"
                )
            }
        }
    }

    private func launchApp(contentSize: String = "UICTContentSizeCategoryL") -> XCUIApplication {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchEnvironment["LOOPGUARD_UI_TEST_MODE"] = "1"
        app.launchArguments += [
            "-UIPreferredContentSizeCategoryName",
            contentSize,
        ]
        app.launch()
        return app
    }
}
