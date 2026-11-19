from collections import defaultdict
from collections.abc import Iterable, Mapping

from plan.reference import validate_position as validate_position
from station.ledger import Reading


def group_readings_by_position(readings: Iterable[Reading]) -> dict[int, tuple[Reading, ...]]:
    by_position: dict[int, list[Reading]] = defaultdict(list)
    for reading in readings:
        by_position[reading.position].append(reading)
    return {
        position: tuple(sorted(group, key=lambda reading: reading.reading_id))
        for position, group in by_position.items()
    }


def ordered_positions(groups: Mapping[int, tuple[Reading, ...]], reference_position: int) -> tuple[int, ...]:
    return tuple(sorted(set(groups) | {reference_position}))
