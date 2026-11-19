import re
from fractions import Fraction


_RATIONAL = re.compile(r'[+-]?[0-9]+(?:/[0-9]+)?')


def parse_rational(value: object) -> Fraction:
    """Read an exact number without accepting floats or booleans."""
    if isinstance(value, Fraction):
        return value
    if type(value) is int:
        return Fraction(value)
    if isinstance(value, str) and _RATIONAL.fullmatch(value):
        try:
            return Fraction(value)
        except (ValueError, ZeroDivisionError) as error:
            raise ValueError('invalid rational number') from error
    raise ValueError('expected an integer, Fraction, or rational text')


def encode_rational(value: object) -> str:
    return str(parse_rational(value))
