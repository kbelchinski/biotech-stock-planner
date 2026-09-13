import pytest

from app.domain.models import CatalystType, ClassificationStatus
from app.screening.catalyst_classification import classify

M, N, U = ClassificationStatus.MATCHED, ClassificationStatus.NOT_QUALIFYING, ClassificationStatus.UNRECOGNIZED


@pytest.mark.parametrize(
    ("stage", "event", "status", "catalyst_type"),
    [
        ("Phase 3", "Topline data", M, CatalystType.PHASE3_RESULTS),
        ("Phase III", "Results", M, CatalystType.PHASE3_RESULTS),
        ("Phase 2", "Data readout", M, CatalystType.PHASE2_RESULTS),
        ("Phase 2b", "Top-line results", M, CatalystType.PHASE2_RESULTS),
        ("Phase IIa", "Interim data", M, CatalystType.PHASE2_RESULTS),
        ("NDA Filing", "PDUFA", M, CatalystType.PDUFA),
        ("BLA Filing", "PDUFA date", M, CatalystType.PDUFA),
        # A trial stage alone is never a results announcement.
        ("Phase 3", "Enrollment complete", N, None),
        ("Phase 2", "Initiation", N, None),
        ("Phase 3", None, U, None),
        ("Phase 3", "Advisory committee", U, None),
        # Combined phases have no agreed mapping.
        ("Phase 2/3", "Topline data", U, None),
        ("Phase 1/2", "Data", U, None),
        ("Approved", "Quarterly sales", N, None),
        ("Phase 1", "Data", N, None),
        ("Something else", "Other", U, None),
        (None, None, U, None),
    ],
)
def test_classification(stage, event, status, catalyst_type):
    result = classify(stage, event)
    assert result.status is status
    assert result.catalyst_type == catalyst_type
    assert result.reason
