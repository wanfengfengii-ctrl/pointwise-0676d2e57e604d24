from dataclasses import dataclass
from fractions import Fraction

from plan.rationals import parse_rational


@dataclass(frozen=True)
class ReferenceSpec:
    position: int
    lower: Fraction = Fraction(0)
    upper: Fraction = Fraction(10)

    @property
    def domain(self) -> tuple[Fraction, Fraction]:
        return self.lower, self.upper


def validate_position(position: object) -> int:
    if type(position) is not int:
        raise ValueError('position must be an integer in the plan order')
    return position


def validate_reference(reference: ReferenceSpec) -> ReferenceSpec:
    if not isinstance(reference, ReferenceSpec):
        raise ValueError('expected a reference specification')
    position = validate_position(reference.position)
    lower = parse_rational(reference.lower)
    upper = parse_rational(reference.upper)
    if (lower, upper) != (0, 10):
        raise ValueError('the calibration domain must be [0, 10]')
    return ReferenceSpec(position, lower, upper)
