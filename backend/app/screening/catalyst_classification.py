"""Map BPIQ stage/event labels to the catalyst types used by the screen.

BPIQ documents `stage_event.stage_label` and `stage_event.event_label`, but not their value
vocabulary. These rules are therefore INFERRED and deliberately conservative:

- A Phase 2 / Phase 3 stage alone is never treated as a results announcement; the event label
  must indicate data or results.
- Combined-phase stages (e.g. "Phase 2/3", "Phase 1/2") are not mapped. They stay unrecognized
  until a rule is agreed.
- Labels matching no rule are UNRECOGNIZED, so they surface as Unknown rather than Fail.

Run `python -m app.diagnostics --labels` with live credentials to list the labels BPIQ returns,
then refine these rules.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain.models import CATALYST_TYPE_LABELS, CatalystType, ClassificationStatus

_PDUFA = re.compile(r"\bpdufa\b", re.IGNORECASE)
_COMBINED_PHASE = re.compile(r"phase\s*(?:1|i|2|ii)\s*[/\-–]\s*(?:2|ii|3|iii)\b", re.IGNORECASE)
_PHASE_2 = re.compile(r"^\s*phase\s*(?:2|ii)\s*[ab]?\s*$", re.IGNORECASE)
_PHASE_3 = re.compile(r"^\s*phase\s*(?:3|iii)\s*$", re.IGNORECASE)
_NON_TRIAL_STAGE = re.compile(
    r"^\s*(?:phase\s*(?:1|i)\s*[ab]?|preclinical|approved|marketed|discovery)\s*$", re.IGNORECASE
)
_RESULTS_EVENT = re.compile(r"\b(?:data|results?|readout|top[\s-]?line)\b", re.IGNORECASE)
_NON_RESULTS_EVENT = re.compile(
    r"\b(?:enrol(?:l)?ment|initiat\w*|start\w*|first\s+patient|dosing|ind\b|submission|filing|"
    r"sales|launch)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Classification:
    catalyst_type: CatalystType | None
    status: ClassificationStatus
    reason: str


def classify(stage_label: str | None, event_label: str | None) -> Classification:
    stage = (stage_label or "").strip()
    event = (event_label or "").strip()
    shown = f"stage '{stage or '—'}', event '{event or '—'}'"

    if _PDUFA.search(event) or _PDUFA.search(stage):
        return _matched(CatalystType.PDUFA, f"{shown} mentions PDUFA")

    if _COMBINED_PHASE.search(stage):
        return Classification(
            None,
            ClassificationStatus.UNRECOGNIZED,
            f"Combined-phase {shown} has no agreed mapping to Phase 2 or Phase 3 results",
        )

    phase_type = (
        CatalystType.PHASE2_RESULTS
        if _PHASE_2.match(stage)
        else CatalystType.PHASE3_RESULTS
        if _PHASE_3.match(stage)
        else None
    )
    if phase_type is not None:
        if _RESULTS_EVENT.search(event):
            return _matched(phase_type, f"{shown} indicates a results announcement")
        if _NON_RESULTS_EVENT.search(event):
            return Classification(
                None,
                ClassificationStatus.NOT_QUALIFYING,
                f"{shown} is a trial milestone, not a results announcement",
            )
        return Classification(
            None,
            ClassificationStatus.UNRECOGNIZED,
            f"{shown}: trial stage without a recognizable results event",
        )

    if _NON_TRIAL_STAGE.match(stage):
        return Classification(
            None, ClassificationStatus.NOT_QUALIFYING, f"{shown} is outside Phase 2/3 results and PDUFA"
        )

    return Classification(None, ClassificationStatus.UNRECOGNIZED, f"{shown} matches no classification rule")


def _matched(catalyst_type: CatalystType, reason: str) -> Classification:
    return Classification(
        catalyst_type, ClassificationStatus.MATCHED, f"{CATALYST_TYPE_LABELS[catalyst_type]}: {reason}"
    )
