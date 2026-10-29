import { Badge } from "@/components/ui/badge";
import type { ExchangeStatus } from "@/lib/api";
import { cn } from "@/lib/utils";

const STYLES: Record<ExchangeStatus, { label: string; className: string }> = {
  answered: { label: "Answered", className: "bg-success/10 text-success border-success/25" },
  refused: { label: "Refused", className: "bg-warning/10 text-warning border-warning/30" },
  unsupported: { label: "Unsupported", className: "bg-muted text-muted-foreground border-border" },
  failed: { label: "Failed", className: "bg-destructive/10 text-destructive border-destructive/25" },
};

export const REASON_LABELS: Record<string, string> = {
  safety_violation: "Request declined for safety",
  sensitive_data: "Private information removed",
  safety_check_unavailable: "Request checks unavailable",
  capabilities: "About this assistant",
  personalized_advice: "Personalised advice declined",
  out_of_scope: "Outside supported topics",
  prompt_injection: "Instruction override attempt",
  no_relevant_evidence: "No relevant passages found",
  model_found_insufficient: "Sources don't answer this",
  citations_failed_verification: "Citations failed verification",
  model_error: "Model or service error",
};

export function StatusBadge({ status }: { status: ExchangeStatus }) {
  const { label, className } = STYLES[status];
  return (
    <Badge variant="outline" className={cn("font-medium", className)}>
      {label}
    </Badge>
  );
}
