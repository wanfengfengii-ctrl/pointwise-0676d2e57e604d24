from dataclasses import dataclass
from fractions import Fraction

from issue.evidence import StationLossEvidence


@dataclass(frozen=True)
class JointOptimum:
    reference_interval: tuple[Fraction, Fraction]
    minimum_error: Fraction


def compute_joint_optimum(first: StationLossEvidence, second: StationLossEvidence) -> JointOptimum:
    if first.domain != second.domain:
        raise ValueError('joint calibration requires a common reference domain')
    knots = sorted({x for x, _ in first.points} | {x for x, _ in second.points})
    totals = [(reference, first.evaluate(reference) + second.evaluate(reference)) for reference in knots]
    minimum_error = min(error for _, error in totals)
    optimal_knots = [reference for reference, error in totals if error == minimum_error]
    # The sum is affine between adjacent knots and convex on the whole domain.
    return JointOptimum((optimal_knots[0], optimal_knots[-1]), minimum_error)
