from fractions import Fraction

from issue.evidence import StationLossEvidence
from plan.reference import ReferenceSpec
from plan.units import require_in_domain
from station.ledger import Reading
from station.positions import group_readings_by_position, ordered_positions
from station.revisions import ReadingRevision


def _minimum(
    groups: dict[int, tuple[Reading, ...]], positions: tuple[int, ...], anchor_position: int,
    target: Fraction, candidates: list[Fraction],
) -> Fraction:
    # Up to the preceding position, ending at each candidate fitted value.
    previous: list[Fraction | None] = [Fraction(0)] * len(candidates)
    for position in positions:
        current = []
        prefix_best = None
        for value, prior in zip(candidates, previous):
            if prior is not None and (prefix_best is None or prior < prefix_best):
                prefix_best = prior
            if prefix_best is None or (position == anchor_position and value != target):
                current.append(None)
                continue
            error = sum(
                (reading.weight * abs(reading.value - value) for reading in groups.get(position, ())),
                Fraction(0),
            )
            current.append(prefix_best + error)
        previous = current
    return min(error for error in previous if error is not None)


def minimum_at_reference(
    revision: ReadingRevision, reference: ReferenceSpec, reference_value: object
) -> Fraction:
    target = require_in_domain(reference_value, reference.domain, field='reference')
    groups = group_readings_by_position(revision.readings)
    positions = ordered_positions(groups, reference.position)
    # The domain, the observed values and the anchor bound the linear regions of the fit.
    candidates = sorted({*reference.domain, target, *(reading.value for reading in revision.readings)})
    return _minimum(groups, positions, reference.position, target, candidates)


def build_loss_curve(revision: ReadingRevision, reference: ReferenceSpec) -> StationLossEvidence:
    groups = group_readings_by_position(revision.readings)
    positions = ordered_positions(groups, reference.position)
    candidates = sorted({*reference.domain, *(reading.value for reading in revision.readings)})
    points: list[tuple[Fraction, Fraction]] = []
    # Between successive observed values, the order of candidates is fixed. The
    # minimum of the affine fit costs is concave there and the value function is
    # also convex by joint convexity of the anchored fit, so the interval is affine.
    for target in candidates:
        error = _minimum(groups, positions, reference.position, target, candidates)
        if len(points) >= 2:
            (left, left_error), (middle, middle_error) = points[-2:]
            if (middle_error - left_error) * (target - middle) == (error - middle_error) * (middle - left):
                points.pop()
        points.append((target, error))
    return StationLossEvidence(tuple(points))
