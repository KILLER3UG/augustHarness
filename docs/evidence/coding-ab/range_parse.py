def parse_range(value):
    """Parse an HTTP Range header value like 'bytes=0-499' or 'bytes=-500'.

    Parameters
    ----------
    value : str
        The part after the 'bytes=' key, e.g. "0-499" or "-500".

    Returns
    -------
        A tuple (start, end) of integers.

    Rules
    -----
    * 'bytes=a-b' -> start = int(a), end = int(b)
    * 'bytes=-N'  -> start = 0, end = N   (last N bytes)

    Raises
    ------
        ValueError if the format is not recognized or arguments are invalid.
    """
    if value is None:
        raise ValueError("value must be a string")
    if not isinstance(value, str):
        raise ValueError("value must be a string")

    if value.startswith('bytes='):
        suffix = value.split('=', 1)[1]  # e.g. "0-499" or "-500"
        if suffix.startswith('-'):
            end = int(suffix.lstrip('-'))
            start = 0
        else:
            parts = suffix.split('-')
            if len(parts) != 2:
                raise ValueError(f"Invalid range: {suffix!r} (expected 'a-b' or '-N')")
            try:
                start = int(parts[0])
                end = int(parts[1])
            except ValueError as exc:
                raise ValueError(f"Non-integer bounds in '{suffix}'") from exc
        return (start, end)
    else:
        raise ValueError("Expected a 'bytes=...' header value; got: " + repr(value))
