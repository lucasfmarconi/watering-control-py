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


SOIL_RAW_MIN = 50  # at or below this the probe is likely shorted/unplugged (ADC rail)
SOIL_RAW_MAX = 4040  # at or above this the probe is likely disconnected (ADC rail)


def soil_error(raw, spread, max_spread):
    """Return why a soil reading is untrustworthy, or None if it looks valid.

    Args:
        raw: Median raw 12-bit ADC value.
        spread: max - min of the samples behind that median.
        max_spread: Largest spread still considered stable.

    Returns:
        "rail-low", "rail-high" or "noisy", or None.
    """
    if raw < SOIL_RAW_MIN:
        return "rail-low"
    if raw > SOIL_RAW_MAX:
        return "rail-high"
    if spread > max_spread:
        return "noisy"
    return None


def raw_to_percent(raw, dry, wet):
    """Map a raw capacitive-probe value to a 0-100 moisture percentage.

    The probe reads a higher value when dry, so the mapping is inverted:
    raw == dry gives 0 and raw == wet gives 100. Values outside that range are
    clamped. Returns None if dry == wet, since the scale is undefined.
    """
    if dry == wet:
        return None
    pct = (dry - raw) * 100 / (dry - wet)
    return max(0, min(100, round(pct)))


def salt_valid(moisture_pct, low, high):
    """Return True if the soil moisture is inside [low, high] and so the salt reading is comparable.

    moisture_pct is None when the soil reading was invalid, which is never valid.
    """
    return moisture_pct is not None and low <= moisture_pct <= high


def valid_soil_calibration(dry, wet):
    """Return True if dry and wet are distinct integers within the 12-bit ADC range."""
    for value in (dry, wet):
        if not isinstance(value, int) or not 0 <= value <= 4095:
            return False
    return dry != wet
