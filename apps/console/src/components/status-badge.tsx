import { Badge } from "@/components/ui/badge";

const STATUS_VARIANT: Record<
  string,
  "default" | "secondary" | "destructive" | "outline" | "success" | "warning"
> = {
  pending_approval: "warning",
  approved: "secondary",
  executed: "success",
  denied: "destructive",
  denied_by_human: "destructive",
  timed_out: "destructive",
  execution_failed: "destructive",
};

const STATUS_LABEL: Record<string, string> = {
  pending_approval: "Pending approval",
  approved: "Approved",
  executed: "Executed",
  denied: "Denied (policy)",
  denied_by_human: "Denied (human)",
  timed_out: "Timed out",
  execution_failed: "Execution failed",
};

export function RemediationStatusBadge({ status }: { status: string }) {
  return (
    <Badge variant={STATUS_VARIANT[status] ?? "outline"}>
      {STATUS_LABEL[status] ?? status}
    </Badge>
  );
}
