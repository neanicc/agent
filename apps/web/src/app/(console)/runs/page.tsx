import { ResourceList } from "@/components/control-pages";

export default function RunsPage() {
  return (
    <ResourceList
      description="Agent sessions ordered by recent activity, with state and repository identity visible at a glance."
      detailBase="/runs"
      emptyDetail="Start a run through a paired integration. It will appear here with durable timeline evidence."
      emptyTitle="No runs observed"
      path="/v1/sessions"
      title="Runs"
    />
  );
}
