import { ChangeDetail } from "@/components/control-pages";

export default async function ChangePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <ChangeDetail id={id} />;
}
