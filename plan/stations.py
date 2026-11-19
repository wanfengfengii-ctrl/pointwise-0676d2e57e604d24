def require_name(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError(f'{field} must be a nonempty label without surrounding whitespace')
    return value


def validate_station_pair(stations: object) -> tuple[str, str]:
    if not isinstance(stations, (tuple, list)) or len(stations) != 2:
        raise ValueError('a calibration plan requires two stations')
    first = require_name(stations[0], 'station_id')
    second = require_name(stations[1], 'station_id')
    if first == second:
        raise ValueError('the plan stations must be distinct')
    return first, second


def require_applicable_station(station_id: str, stations: tuple[str, str]) -> str:
    if require_name(station_id, 'station_id') not in stations:
        raise ValueError('station is not applicable to the plan')
    return station_id
