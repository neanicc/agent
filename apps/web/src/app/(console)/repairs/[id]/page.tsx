import { RepairDetailPage } from "@/components/repair-pages";


export default async function RepairPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <RepairDetailPage id={id} />;
}
