import { ResourceList } from "@/components/control-pages";

export default function ChangesPage() {
  return (
    <ResourceList
      description="Observed file changes with verification state, actor, and provenance."
      detailBase="/changes"
      emptyDetail="Changes appear after a paired integration observes a write in a registered repository."
      emptyTitle="No observed changes"
      path="/v1/changes"
      title="Changes"
    />
  );
}
