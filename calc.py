"""Pure helpers with no hardware access, so they can be unit-tested on the host.

main.py runs a whole cycle on import and cannot be imported off the board.
"""


def median(values):
    """Return the median of a non-empty sequence of numbers.

    For an even count it returns the integer mean of the two middle values.
    """
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) // 2


def spread(values):
    """Return max - min of a non-empty sequence, a cheap measure of noise."""
    return max(values) - min(values)
