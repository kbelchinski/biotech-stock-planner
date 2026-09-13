import { CircleCheck, CircleHelp, CircleMinus, CircleX, type LucideIcon } from "lucide-react";
import { ELIGIBILITY_LABEL, STATUS_LABEL } from "../format";
import type { CriterionStatus, Eligibility } from "../types";

const STATUS_ICON: Record<CriterionStatus, LucideIcon> = {
  pass: CircleCheck,
  fail: CircleX,
  unknown: CircleHelp,
  not_applied: CircleMinus,
};

const ELIGIBILITY_TONE: Record<Eligibility, CriterionStatus> = {
  qualifies: "pass",
  does_not_qualify: "fail",
  insufficient_data: "unknown",
};

export function CriterionBadge({ status, size = "md" }: { status: CriterionStatus; size?: "sm" | "md" }) {
  const Icon = STATUS_ICON[status];
  return (
    <span className={`badge badge-${status} badge-${size}`}>
      <Icon aria-hidden size={size === "sm" ? 12 : 14} strokeWidth={2.25} />
      {STATUS_LABEL[status]}
    </span>
  );
}

export function EligibilityBadge({ value }: { value: Eligibility }) {
  const tone = ELIGIBILITY_TONE[value];
  const Icon = STATUS_ICON[tone];
  return (
    <span className={`badge badge-${tone} badge-strong`}>
      <Icon aria-hidden size={14} strokeWidth={2.25} />
      {ELIGIBILITY_LABEL[value]}
    </span>
  );
}
