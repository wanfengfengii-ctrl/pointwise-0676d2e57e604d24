from dataclasses import dataclass
from fractions import Fraction

from issue.robust_evidence import RobustLossEvidence


@dataclass(frozen=True)
class RobustOptimum:
    reference_interval: tuple[Fraction, Fraction]
    minimum_error: Fraction


def _total(curve_attr: str, first: RobustLossEvidence, second: RobustLossEvidence, t: Fraction) -> Fraction:
    return getattr(first, curve_attr).evaluate(t) + getattr(second, curve_attr).evaluate(t)


def compute_robust_optimum(
    first: RobustLossEvidence, second: RobustLossEvidence,
) -> RobustOptimum:
    """Minimize the worst shared-offset total loss over t in the common domain.

    For a fixed t the local minimum loss is jointly convex in (d, t), hence the
    worst offset over the closed interval is one of its endpoints.  The two total
    endpoint losses F(t) and G(t) are convex piecewise-linear, so R(t)=max(F,G) is
    convex piecewise-linear as well; its remaining interior kinks are exact
    intersections of affine pieces.  All arithmetic stays in exact Fractions.
    """
    if first.domain != second.domain:
        raise ValueError('robust calibration requires a common reference domain')
    if first.offset_interval != second.offset_interval:
        raise ValueError('the two robust bundles must share one fixed offset interval')
    domain = first.domain
    knots = sorted({
        *(point for point, _ in first.lower_loss.points),
        *(point for point, _ in first.upper_loss.points),
        *(point for point, _ in second.lower_loss.points),
        *(point for point, _ in second.upper_loss.points),
    })
    events = set(knots)
    for left, right in zip(knots, knots[1:]):
        span = right - left
        f_left = _total('lower_loss', first, second, left)
        f_right = _total('lower_loss', first, second, right)
        g_left = _total('upper_loss', first, second, left)
        g_right = _total('upper_loss', first, second, right)
        slope_f = (f_right - f_left) / span
        slope_g = (g_right - g_left) / span
        if slope_f != slope_g:
            crossing = left + (g_left - f_left) / (slope_f - slope_g)
            if left < crossing < right:
                events.add(crossing)
    ordered = sorted(events)

    def worst(t: Fraction) -> Fraction:
        return max(
            _total('lower_loss', first, second, t),
            _total('upper_loss', first, second, t),
        )

    values = [(t, worst(t)) for t in ordered]
    minimum_error = min(error for _, error in values)
    optimal = [t for t, error in values if error == minimum_error]
    # R is convex, so every t between its first and last minimizer is also optimal.
    return RobustOptimum((optimal[0], optimal[-1]), minimum_error)
