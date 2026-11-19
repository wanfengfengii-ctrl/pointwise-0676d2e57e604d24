from fractions import Fraction

from plan.rationals import parse_rational


def validate_unit(unit: object) -> str:
    if not isinstance(unit, str) or not unit or unit.strip() != unit:
        raise ValueError('unit must be a nonempty label without surrounding whitespace')
    return unit


def require_in_domain(
    value: object, domain: tuple[Fraction, Fraction], *, field: str
) -> Fraction:
    number = parse_rational(value)
    if not domain[0] <= number <= domain[1]:
        raise ValueError(f'{field} outside [{domain[0]}, {domain[1]}]')
    return number
