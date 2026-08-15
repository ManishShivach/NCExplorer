"""Calendar-aware display labels for a NetCDF time dimension.

NCExplorer opens every dataset with ``decode_times=False``, so a time axis stays
raw and numeric and a timestep is always selected by plain integer index
(``isel(time=i)``). That is deliberate: the index is the one addressing scheme
that behaves identically for a Gregorian axis, a 360-day model calendar and a
file whose time attributes CDO has mangled. Flipping the flag would change the
dtype of ``time_values`` underneath code that assumes numbers.

What the flag costs is legibility — the user sees ``54786.0`` instead of a date.
This module supplies the missing half: a parallel list of labels decoded with
:func:`cftime.num2date`, which understands the model-only CF calendars
(``noleap``/``365_day``, ``360_day``, ``all_leap``, ``julian``,
``proleptic_gregorian``) that :mod:`datetime` cannot represent at all.

Two rules matter to callers:

* **A broken time axis must never stop a file from loading.** Missing or
  malformed ``units``, an unrecognised calendar, or anything ``num2date``
  refuses degrades to the raw number rendered as a string. CDO output with a
  damaged time axis is common enough that raising here would be the bug.
* **The format is chosen once for the whole series**, from the data itself, so a
  slider reads as a uniform column of dates rather than a ragged mix of
  precisions. Yearly data collapses to ``1979``, monthly to ``1979-01``, daily
  to ``1979-01-15``, and anything sub-daily keeps its clock time.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import cftime
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Exactly the calendars cftime accepts. Checking up front rather than catching
# the resulting ValueError lets the debug log name the offending calendar.
KNOWN_CALENDARS = frozenset({
    "standard",
    "gregorian",
    "proleptic_gregorian",
    "noleap",
    "365_day",
    "all_leap",
    "366_day",
    "360_day",
    "julian",
})

# CF says an absent calendar attribute means the mixed Julian/Gregorian one.
DEFAULT_CALENDAR = "standard"

FORMAT_YEAR = "%Y"
FORMAT_MONTH = "%Y-%m"
FORMAT_DAY = "%Y-%m-%d"
FORMAT_MINUTE = "%Y-%m-%d %H:%M"
FORMAT_SECOND = "%Y-%m-%d %H:%M:%S"


@dataclass(frozen=True)
class TimeAxis:
    """One decoded time dimension.

    ``values`` is exactly what was read from the file — raw and numeric —
    because timestep selection indexes into it. ``labels`` is the parallel
    display form and always has the same length.
    """

    name: str
    values: list
    labels: list[str]
    units: str = ""
    calendar: str = ""

    def apply_to(self, netcdf_props) -> None:
        """Copy this axis onto a NetCDFProperties-shaped object."""
        netcdf_props.time_dimension = self.name
        netcdf_props.time_values = self.values
        netcdf_props.time_labels = self.labels
        netcdf_props.time_units = self.units
        netcdf_props.time_calendar = self.calendar


def time_attributes(variable) -> tuple[str, str]:
    """The ``units`` and ``calendar`` of a time variable.

    Both are read from ``.attrs`` first and ``.encoding`` second: xarray moves
    them out of the attributes whenever it decodes a variable itself, so a
    dataset that reached us from a decoding loader keeps them only in
    ``.encoding``.
    """
    attrs = getattr(variable, "attrs", None) or {}
    encoding = getattr(variable, "encoding", None) or {}
    units = attrs.get("units") or encoding.get("units") or ""
    calendar = attrs.get("calendar") or encoding.get("calendar") or ""
    return str(units), str(calendar)


def read_time_axis(dataset, name: str) -> TimeAxis:
    """Read and decode the time dimension ``name`` of an xarray dataset.

    Works whether ``name`` is a coordinate, a data variable, or a bare dimension
    with no variable behind it — in the last case the index is all the file
    offers, so it becomes both the value and the label.
    """
    if name not in dataset:
        indices = list(range(int(dataset.sizes.get(name, 0))))
        return TimeAxis(name, indices, [str(i) for i in indices])

    variable = dataset[name]
    units, calendar = time_attributes(variable)
    raw = np.atleast_1d(np.asarray(variable.values))
    return TimeAxis(
        name=name,
        values=raw.tolist(),
        labels=build_labels(raw, units, calendar),
        units=units,
        calendar=calendar,
    )


def build_labels(values, units: str = "", calendar: str = "") -> list[str]:
    """Display labels for one time axis; raw numbers as strings on any failure."""
    raw = np.atleast_1d(np.asarray(values))
    fallback = [str(value) for value in raw.tolist()]

    dates = _decode(raw, units, calendar)
    if dates is None:
        return fallback

    fmt = _choose_format(dates)
    labels = []
    for date, plain in zip(dates, fallback):
        try:
            labels.append(date.strftime(fmt))
        except (AttributeError, ValueError):
            # A masked or out-of-range element: keep the rest of the series
            # readable rather than discarding every label for one bad value.
            labels.append(plain)
    return labels


def label_at(netcdf_props, index: int) -> str:
    """Label for one timestep of a layer, falling back to its raw value.

    Written defensively with ``getattr`` because one loader attaches an anonymous
    object rather than a real ``NetCDFProperties`` to ``props.netcdf``.
    """
    labels = getattr(netcdf_props, "time_labels", None) or []
    if 0 <= index < len(labels):
        return labels[index]

    values = getattr(netcdf_props, "time_values", None) or []
    if 0 <= index < len(values):
        return str(values[index])
    return ""


def _decode(raw: np.ndarray, units: str, calendar: str) -> list | None:
    """Decode a raw time array to datetime-like objects, or None if impossible."""
    if np.issubdtype(raw.dtype, np.datetime64):
        # An axis xarray already decoded. datetime64[ns] has no usable .tolist()
        # (it yields integer nanoseconds), so go through pandas to get objects
        # that carry strftime.
        return list(pd.to_datetime(raw))

    if raw.dtype == object:
        # cftime objects, straight from xarray's own decoder.
        objects = raw.tolist()
        if all(hasattr(value, "strftime") for value in objects):
            return objects
        return None

    if not units:
        logger.debug("Time axis has no units attribute; showing raw values")
        return None

    resolved = calendar.strip().lower() or DEFAULT_CALENDAR
    if resolved not in KNOWN_CALENDARS:
        logger.debug("Unrecognised time calendar %r; showing raw values", calendar)
        return None

    try:
        decoded = cftime.num2date(raw, units, resolved)
    except Exception:
        logger.debug("Could not decode time axis (units=%r calendar=%r)",
                     units, resolved, exc_info=True)
        return None

    return list(np.atleast_1d(decoded))


def _choose_format(dates: list) -> str:
    """Pick one strftime format for the whole series based on its resolution."""
    # Masked or otherwise undecodable entries carry no resolution information
    # and would raise on attribute access, so they take no part in the choice.
    usable = [date for date in dates if hasattr(date, "strftime")]
    if not usable:
        return FORMAT_DAY

    if any(getattr(d, "second", 0) or getattr(d, "microsecond", 0) for d in usable):
        return FORMAT_SECOND
    if any(getattr(d, "hour", 0) or getattr(d, "minute", 0) for d in usable):
        return FORMAT_MINUTE

    # Everything is midnight from here, so only the date part carries meaning.
    if len(usable) < 2:
        return FORMAT_DAY

    # A series with one step per calendar month reads better collapsed: a
    # monthly mean stamped on the 1st is "1979-01", not "1979-01-01". The
    # constant day-of-month plus one-value-per-month test is what distinguishes
    # it from daily data that merely happens to be short.
    if len({d.day for d in usable}) != 1:
        return FORMAT_DAY
    if len({(d.year, d.month) for d in usable}) != len(usable):
        return FORMAT_DAY
    if len({d.month for d in usable}) == 1 and len({d.year for d in usable}) == len(usable):
        return FORMAT_YEAR
    return FORMAT_MONTH
