from dataclasses import dataclass
from fractions import Fraction

from issue.evidence import StationLossEvidence, encode_evidence
from plan.rationals import encode_rational, parse_rational


@dataclass(frozen=True)
class RobustLossEvidence:
    """V2 station evidence: exact local loss curves at the two offset endpoints.

    The conditioned readings are value + sensitivity * d.  For a fixed reference t
    the local minimum loss is jointly convex and piecewise linear in (d, t), so its
    worst value over the closed offset interval is attained at an endpoint.  The
    reading ledger, reading numbers, sensitivities and private positions stay at the
    station and never appear in the wire artifact.
    """

    offset_interval: tuple[Fraction, Fraction]
    lower_loss: StationLossEvidence
    upper_loss: StationLossEvidence

    def __post_init__(self) -> None:
        if not isinstance(self.offset_interval, (tuple, list)) or len(self.offset_interval) != 2:
            raise ValueError('offset interval requires two rational endpoints')
        lower = parse_rational(self.offset_interval[0])
        upper = parse_rational(self.offset_interval[1])
        if lower > upper:
            raise ValueError('offset interval lower endpoint must not exceed its upper endpoint')
        if not isinstance(self.lower_loss, StationLossEvidence) or \
                not isinstance(self.upper_loss, StationLossEvidence):
            raise ValueError('robust evidence requires two station loss curves')
        if self.lower_loss.domain != self.upper_loss.domain:
            raise ValueError('the two offset loss curves must share the calibration domain')
        object.__setattr__(self, 'offset_interval', (lower, upper))

    @property
    def domain(self) -> tuple[Fraction, Fraction]:
        return self.lower_loss.domain


def encode_robust_evidence(evidence: RobustLossEvidence) -> dict[str, object]:
    lower, upper = evidence.offset_interval
    return {
        'offset_interval': [encode_rational(lower), encode_rational(upper)],
        'lower_loss': encode_evidence(evidence.lower_loss),
        'upper_loss': encode_evidence(evidence.upper_loss),
    }


def decode_robust_evidence(payload: object, domain: tuple[Fraction, Fraction]) -> RobustLossEvidence:
    if not isinstance(payload, dict) or set(payload) != {'offset_interval', 'lower_loss', 'upper_loss'}:
        raise ValueError('invalid robust evidence fields')
    interval = payload['offset_interval']
    if not isinstance(interval, list) or len(interval) != 2 or \
            any(type(token) is not str for token in interval):
        raise ValueError('robust evidence offset interval requires two rational strings')
    offset_interval = (parse_rational(interval[0]), parse_rational(interval[1]))
    if offset_interval[0] > offset_interval[1]:
        raise ValueError('robust evidence offset interval is not a closed interval')
    lower_loss = _decode_loss(payload['lower_loss'], domain)
    upper_loss = _decode_loss(payload['upper_loss'], domain)
    return RobustLossEvidence(offset_interval, lower_loss, upper_loss)


def _decode_loss(payload: object, domain: tuple[Fraction, Fraction]) -> StationLossEvidence:
    # Reuse the V1 structural, convexity and domain checks for each curve.
    from issue.evidence import decode_evidence
    return decode_evidence(payload, domain)
