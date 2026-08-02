import { VerificationDetail } from "@/components/control-pages";

export default async function VerificationRecordPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <VerificationDetail id={id} />;
}
