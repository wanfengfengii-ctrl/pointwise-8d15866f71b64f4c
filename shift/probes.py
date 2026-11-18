from collections.abc import Collection, Iterable


def normalize_slot_probes(
    probes: Iterable[str], configured: Collection[str]
) -> tuple[str, ...]:
    """Resolve the slot's probe set against the published calibration names."""
    if isinstance(probes, (str, bytes)):
        raise ValueError('slot probes must be a collection of names')
    try:
        names = tuple(probes)
    except TypeError as error:
        raise ValueError('slot probes must be iterable') from error
    for probe in names:
        if not isinstance(probe, str) or probe not in configured:
            raise ValueError('slot refers to an unconfigured probe')
    return tuple(sorted(set(names)))
