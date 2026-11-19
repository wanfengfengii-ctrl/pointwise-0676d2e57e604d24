from bisect import bisect_right
from dataclasses import dataclass
from fractions import Fraction

from plan.rationals import encode_rational, parse_rational


@dataclass(frozen=True)
class StationLossEvidence:
    points: tuple[tuple[Fraction, Fraction], ...]

    def __post_init__(self) -> None:
        points = tuple((parse_rational(x), parse_rational(y)) for x, y in self.points)
        if len(points) < 2 or any(y < 0 for _, y in points):
            raise ValueError('loss requires at least two points and nonnegative errors')
        previous_slope = None
        for (left, low_error), (right, high_error) in zip(points, points[1:]):
            if right <= left:
                raise ValueError('loss breakpoints must strictly increase')
            slope = (high_error - low_error) / (right - left)
            if previous_slope is not None and slope < previous_slope:
                raise ValueError('station loss must be convex')
            previous_slope = slope
        object.__setattr__(self, 'points', points)

    @property
    def domain(self) -> tuple[Fraction, Fraction]:
        return self.points[0][0], self.points[-1][0]

    def evaluate(self, reference_value: object) -> Fraction:
        reference = parse_rational(reference_value)
        if not self.domain[0] <= reference <= self.domain[1]:
            raise ValueError('reference outside station loss domain')
        index = bisect_right(self.points, reference, key=lambda point: point[0]) - 1
        left, low_error = self.points[index]
        if reference == left:
            return low_error
        right, high_error = self.points[index + 1]
        return low_error + (high_error - low_error) * (reference - left) / (right - left)


def encode_evidence(evidence: StationLossEvidence) -> dict[str, list[list[str]]]:
    return {'points': [[encode_rational(x), encode_rational(y)] for x, y in evidence.points]}


def decode_evidence(payload: object, domain: tuple[Fraction, Fraction]) -> StationLossEvidence:
    if not isinstance(payload, dict) or set(payload) != {'points'}:
        raise ValueError('invalid station loss fields')
    rows = payload['points']
    if not isinstance(rows, list):
        raise ValueError('station loss points must be an array')
    points = []
    for row in rows:
        if not isinstance(row, list) or len(row) != 2 or any(type(value) is not str for value in row):
            raise ValueError('each station loss point requires two rational strings')
        points.append((parse_rational(row[0]), parse_rational(row[1])))
    evidence = StationLossEvidence(tuple(points))
    if evidence.domain != domain:
        raise ValueError('station loss does not cover its declared domain')
    return evidence
