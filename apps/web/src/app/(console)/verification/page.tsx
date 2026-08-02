import { ResourceList } from "@/components/control-pages";

export default function VerificationPage() {
  return (
    <ResourceList
      description="Deterministic checks, signed proof, and explicit inconclusive outcomes."
      detailBase="/verification"
      emptyDetail="Verification records appear after LoopGuard captures a baseline or evaluates an observed change."
      emptyTitle="No verification records"
      path="/v1/verifications"
      title="Verification"
    />
  );
}
