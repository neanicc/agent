import Foundation
import Testing
@testable import LoopGuard

@Suite("Repair capability and evidence contracts")
struct RepairCapabilityTests {
    @Test("repair tab requires a fresh ready capability")
    func freshCapability() throws {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        let ready = try decoder.decode(
            EffectiveCapabilities.self,
            from: Data(
                """
                {
                  "status":"ready",
                  "observed_at":"2026-07-30T12:00:00Z",
                  "ttl_seconds":60,
                  "features":{
                    "repair":{
                      "available":true,
                      "status":"ready",
                      "reason":"workflow_registered"
                    }
                  }
                }
                """.utf8
            )
        )

        #expect(
            ready.repairIsReady(
                now: Date(timeIntervalSince1970: 1_785_412_830)
            )
        )
        #expect(
            !ready.repairIsReady(
                now: Date(timeIntervalSince1970: 1_785_412_861)
            )
        )
    }

    @Test("candidate checks decode string and boolean evidence")
    func candidateEvidence() throws {
        let data = Data(
            """
            {
              "id":"candidate-1",
              "strategy":"normalize boundary",
              "changed_files":["src/coordinates.py"],
              "changed_lines":8,
              "evaluation":{
                "replay":"passed",
                "regression":false,
                "security":true,
                "contract_breaking":false,
                "contract_changes":[]
              }
            }
            """.utf8
        )

        let candidate = try JSONDecoder().decode(
            RepairCandidate.self,
            from: data
        )

        #expect(candidate.evaluation?.replay == .passed)
        #expect(candidate.evaluation?.regression == .failed)
        #expect(candidate.evaluation?.security == .passed)
        #expect(candidate.changedFiles == ["src/coordinates.py"])
    }
}
