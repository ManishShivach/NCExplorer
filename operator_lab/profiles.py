"""What the harness knows about an operator before it runs it.

Three questions, none of which ``cdo --operators`` answers:

* **What trailing parameters does it need?** The schema names them
  (``OPERATOR_SCHEMA[...].params``) but carries no values, so a bulk run has to
  supply one. :data:`PARAMETER_DEFAULTS` is that supply, keyed by parameter name
  rather than by operator so ``levels`` means the same thing to every operator
  that takes it.
* **Can it be tested from files alone?** :data:`UNTESTABLE` lists the ones that
  cannot, each with the reason that goes into the report — a skip with no
  explanation is indistinguishable from an oversight.
* **What file extensions does it prefer?** :func:`preferred_input_extension` and
  :func:`preferred_output_extension`. Mostly ``.nc`` in and ``.nc`` out, but the
  informational operators write to stdout (``.txt``), the GMT/KML/GrADS
  operators write their own text formats, and the ``import_*`` family reads
  anything but NetCDF.
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

from ncexplorer_toolkit.core.categories import OPERATOR_SCHEMA, NCExplorerCategory

#: The variable name in the generated samples. Every operator that needs a
#: variable named in a parameter gets this one, so ``selname,VAR`` selects
#: something rather than failing with "No variables selected".
SAMPLE_VARIABLE = "random"

#: Grid the samples are built on, and the answer for every ``grid`` parameter.
#: Small enough that a 943-operator sweep finishes, large enough that
#: remapping and zonal statistics have something to work with.
SAMPLE_GRID = "r36x18"

#: The years carried by the pair of single-year samples ``intyear`` interpolates
#: between. Three apart rather than consecutive, so there is a year strictly
#: inside the interval to ask for — with 2000 and 2001 there is none, and CDO
#: answers every request with "Year N out of bounds".
#:
#: Declared here rather than in ``samples`` because both sides need it: the
#: generator builds the files from it, and the parameter for ``intyear`` is
#: read back off it below instead of being written out a second time.
BRACKET_YEARS = (2000, 2003)


# --------------------------------------------------------------------------
# Trailing parameters
# --------------------------------------------------------------------------

#: One plausible value per parameter name, merged from the two bulk testers
#: that preceded this module. Values are chosen to suit the generated samples:
#: dates land inside their 2000–2001 span, ``var``/``vars`` name the variable
#: they actually contain, and grids match :data:`SAMPLE_GRID`.
#:
#: A name missing from here is not fatal — a parameter the schema marks
#: optional is simply omitted, and only a *required* one with no default makes
#: the harness skip the operator (and say so).
PARAMETER_DEFAULTS: Dict[str, str] = {
    # --- attributes and names ---
    "attname": "history",
    "attstring": "written by operator_lab",
    "code": "1",
    "codes": "1",
    "name": SAMPLE_VARIABLE,
    "names": SAMPLE_VARIABLE,
    "param": "1",
    "params": "1",
    "stdname": "air_temperature",
    "stdnames": "air_temperature",
    "tabnum": "128",
    "tabnums": "128",
    "units": "days",
    "var": SAMPLE_VARIABLE,
    "vars": SAMPLE_VARIABLE,
    "variables": SAMPLE_VARIABLE,
    "variable": SAMPLE_VARIABLE,
    "oldcode_newcode": "1,2",
    "oldname_newname": f"{SAMPLE_VARIABLE},renamed",
    "oldstdname_newstdname": "air_temperature,temperature",
    "oldunit_newunit": "1,1",
    "oldcode_newname": "1,renamed",
    "oldlev_newlev": "0,1",

    # --- time ---
    "calendar": "standard",
    "date": "2000-01-15",
    "date1": "2000-01-01",
    "date2": "2000-03-31",
    "day": "1",
    "days": "1,2",
    "hours": "0,12",
    "inc": "1day",
    "month": "1",
    "months": "1,2,12",
    "nday": "3",
    "noffset": "0",
    "nsets": "3",
    "nskip": "0",
    "nts": "2",
    "nts1": "0",
    "nts2": "0",
    "seasons": "DJF,MAM",
    "sval": "1day",
    "time": "00:00",
    "times": "00:00,12:00",
    "timesteps": "1",
    "year": "2000",
    "years": "2000",
    # The ETCCDI bootstrapping window, in years the sample actually spans.
    "startboot": "2000",
    "endboot": "2001",
    # The ECA form, which is the majority: 23 indices parse this slot as a
    # key=value pair and reject a bare "m" with "Argument parse error!". The
    # seven ETCCDI bootstrapping indices parse the same-named slot positionally
    # and reject "freq=month" with "Integer parameter >m< contains invalid
    # character", so they get an override in OPERATOR_PARAMETERS below.
    "freq": "freq=month",

    # --- geometry ---
    "cells": "1",
    "grid": SAMPLE_GRID,
    # The k-nearest-neighbour operators take key=value pairs, not a bare grid.
    "knnopts": f"grid={SAMPLE_GRID}",
    "gridname": SAMPLE_GRID,
    "gridnames": "lonlat",
    "grids": "1",
    "gridtype": "lonlat",
    "hlevels": "0",
    "idx1": "1",
    "idx2": "2",
    "idy1": "1",
    "idy2": "2",
    "lat1": "10",
    "lat2": "30",
    "levels": "0",
    "levidx": "1",
    "lon1": "70",
    "lon2": "90",
    "ltypes": "105",
    "newlev": "1",
    "nx": "3",
    "ny": "3",
    "xinc": "2",
    "yinc": "2",
    # subgrid's sub-grid corner indices.
    "i0": "1",
    "i1": "2",
    "j0": "1",
    "j1": "2",
    "oldlev": "0",
    "plevels": "1000,850",
    "regions": "1",
    "thickness": "1",
    "zaxes": "1",
    "zaxisnames": "surface",
    "zranges": "0,1",

    # --- numeric ---
    "bins": "0,0.25,0.5,0.75,1",
    "c": "1",
    "const": "1",
    "end": "10",
    # A resample factor of 1 would be a no-op; CDO wants 2, 3, 4 …
    "factor": "2",
    "format": "%g",
    "increment": "1",
    "miss": "-9999",
    "mode": "m",
    "n": "2",
    "nbins": "2",
    "neof": "1",
    "nelem": "1",
    "number": "1",
    "nwaves": "1",
    # CDO's own name for the percentile, and the reason this key is ``pn`` and
    # not the ``p`` it used to be: the schema was renamed to match the binary,
    # because one module accepts the name as part of the value
    # (``fldpctl,pn=90``) and printing ``p`` in the usage line told a user to
    # type something Fldstat does not take. 90 rather than 50 so a wrong
    # percentile is visible in the output rather than coinciding with a median.
    "pn": "90",
    "records": "1",
    "rmax": "1",
    "rmin": "0",
    # ``fourier``'s only parameter, renamed from ``sign`` — which is what this
    # schema called it and what neither CDO nor the manual does. -1 is the
    # forward transform and the value the manual's own example runs first.
    # It matters that this is now one of the two accepted values rather than
    # any integer: ``invalid_parameter_values`` enforces ``choices``, so a
    # shared default of 0 or 2 would be refused by the app before argv, where
    # before it would have reached CDO and exited 0.
    "epsilon": "-1",
    "T": "10",
    "T1": "0",
    "T2": "20",
    "value": "1.0",
    "x": "0.5",
    # The two 2-sample hypothesis tests: CDO rejects a constant of 0.
    "risk": "0.05",

    # --- the Statistic section's keyword parameters ---
    #
    # All five are optional, so a sweep that left them out would still pass and
    # would prove nothing about them. The point of giving them a value is that
    # the sweep then builds the *token* — ``fldmean,weights=false`` rather than
    # ``fldmean`` — and a wrong form is a failed run rather than a silent skip.
    #
    # ``false`` for both Fldstat switches, for two different reasons.
    # ``weights`` defaults to TRUE, so false is the value that actually changes
    # the command and the answer. ``verbose`` defaults to false and is kept
    # there deliberately: verbose=true prints a per-timestep table to stdout,
    # and the harness compares stdout across runs.
    "weights": "false",
    "verbose": "false",
    # Defaults to false in CDO too; exercised at false because true drops the
    # partial periods and a short sample can lose every timestep that way,
    # which reads as a broken operator rather than a working switch.
    "complete_only": "false",
    # zonmean's only parameter, and the only Zonstat operator that takes one.
    # A preset rather than a file so the sweep needs no fixture: zonal_10 is 18
    # bands of ten degrees, measured.
    "zonaldes": "zonal_10",
    # ydrun*'s repeated-missing mode. ``c`` is the only value CDO accepts —
    # rm=n is "Parameter rm must only contain 'c'!" — so this is the whole
    # domain of the parameter rather than a choice from it.
    "rm": "c",
    # ydrunpctl's percentile method. r8 rather than nrank because nrank is the
    # default: picking it would build a token that cannot be distinguished
    # from leaving the parameter out.
    "pm": "r8",
    # cdo spectrum: detrend type, segment length, segment count, window type.
    # 1/1 are "subtract the mean" and "Hann"; both are spelled out in the
    # prompt the operator emits when it is given nothing.
    #
    # This key is spectrum's *parameter*, not the operator ``detrend``, and the
    # two do not collide: this table is keyed by parameter name, and the five
    # operators of the Regression section — ``detrend`` among them — declare
    # their one parameter as ``equal``, immediately below. Checked across the
    # whole schema rather than assumed: ``detrend`` is a parameter name on
    # exactly one operator (spectrum, positional int) and ``equal`` on exactly
    # five (keyword bool).
    "detrend": "1",
    "seglen": "64",
    "nseg": "1",
    "window": "1",

    # --- the Regression section ---
    #
    # ``equal`` on detrend, regres, trend, addtrend and subtrend. Keyword form,
    # so the harness supplies the bare value and ``parameter_tokens`` spells the
    # ``equal=false`` token — a literal "equal=false" here would reach CDO as
    # ``equal=equal=false``.
    #
    # ``false`` rather than ``true``, and the reason is measured. The generated
    # series is *daily* (see ``_build``'s ``-settaxis,...,1day``), which is
    # genuinely equidistant, and on an equidistant axis the parameter cannot
    # change the answer — 12 daily steps gave a slope of 1 both ways, where the
    # same series on a monthly axis gave 1.0 against 1.01672. So ``false``
    # exercises the token path all the way into CDO while being unable to turn
    # a passing operator into a failing one, which is what a sweep default
    # should be. ``true`` would have been indistinguishable from passing no
    # parameter at all, and a bug that dropped the token would pass silently.
    "equal": "false",

    # --- the Miscellaneous section ---
    #
    # Every value below was run against the generated samples on CDO 2.6.3.
    # Where a name is shared with an operator that means something else by it,
    # the majority use is here and the exception is an OPERATOR_PARAMETERS
    # override — the same rule the ``pairs`` and ``freq`` entries follow.
    #
    # sethalo's four halos. One cell on each side, which widens the r36x18
    # sample to 38x20 and is small enough to stay quick.
    "east": "1",
    "west": "1",
    "south": "1",
    "north": "1",
    # gridarea. Earth's radius, which is also CDO's own default — so the run
    # exercises the parameter path without changing the answer, and a
    # regression that drops the token is still visible in CDO's stdout
    # ("Using user defined planet radius" against "Using default").
    # ``smooth`` means a search radius by the same name and overrides it below.
    "radius": "6371000",
    # gridcellindex, a point inside the sample's own extent (lon 0..350,
    # lat -85..85). ``symmetrize`` means a hemisphere by ``lat`` and overrides.
    "lon": "10",
    "lat": "20",
    # smooth's other five. Defaults deliberately *not* CDO's own, so a token
    # that fails to reach the binary changes the output rather than matching it.
    "nsmooth": "2",
    "maxpoints": "10",
    "weighted": "gauss",
    "weight0": "0.25",
    "weightR": "0.25",
    # The strong-wind indices' threshold, in m/s. Four operators mean this;
    # uv2vr_cfd/uv2dv_cfd mean the V variable's *name* and override below.
    "v": "12",
    # gradsdes. Version 2 is the machine-independent map format and CDO's
    # default for files under 2GB.
    "mapversion": "2",
    # rhopot/adisit/adipot, in bar. Only rhopot reaches the parser.
    "pressure": "10",
    # The four histogram operators. Three bounds, two bins, and the -inf/inf
    # spelling the manual uses — which is also the value that would break if
    # ``bounds`` were ever declared as a float rather than a string, since
    # ``invalid_parameter_values`` refuses a non-finite number.
    "bounds": "-inf,0,inf",
    # random's optional seed. Fixed rather than arbitrary so two sweeps of the
    # lab produce the same field and can be diffed.
    "seed": "42",
    # The healpix pair. Neither runs against a synthetic sample — both are in
    # UNTESTABLE — but the values are declared anyway so that a future healpix
    # fixture needs no new profile work, and so the schema's choice lists have
    # something to be checked against.
    "nside": "4",
    "zoom": "2",
    "order": "nested",
    "power": "0",
    # uv2vr_cfd/uv2dv_cfd. boundOpt 0 is the documented default; outMode "new"
    # returns the computed field alone.
    "boundOpt": "0",
    "outMode": "new",
    # cmorlite's flag. A CDO flag parameter is declared ``bool`` and rendered as
    # the bare word by ``parameter_tokens``, so the value here is the truth
    # value and not the word — "convert" would fail ``invalid_parameter_values``.
    "convert": "true",
    # uvDestag's optional stagger offsets. Both numbers, because a single one
    # aborts CDO on an internal assertion rather than failing cleanly.
    "offsets": "-0.5,-0.5",

    # --- expressions and free text ---
    # The Expr language is seventeen operators, ~70 functions, an _ALL_ template
    # and _-prefixed temporaries. This used to be "random=random*1;" — one
    # assignment, no function call, no comma inside an argument list, no
    # template, no temporary — so a sweep that "covered expr" covered almost
    # none of it. Four statements, chosen to exercise the parts most likely to
    # break and least likely to be noticed:
    #
    #   _tmp        a temporary: computed, and deliberately never written
    #   isMissval   a one-argument intrinsic
    #   min(x,y)    a function whose argument list contains a comma — the token
    #               that would break if anything on the way to argv re-split the
    #               parameter string, which is exactly the bug a quoting
    #               workaround would introduce
    #   ?:          the ternary
    #   _ALL_       the template key, expanded once per input variable
    "instr": (
        f"_tmp = {SAMPLE_VARIABLE} * 2;"
        f" masked = isMissval({SAMPLE_VARIABLE}) ? 0 : _tmp;"
        f" bounded = min({SAMPLE_VARIABLE},0.5);"
        f" _ALL_ = _ALL_;"
    ),
    "uri": "auto",
    "attrs": f"{SAMPLE_VARIABLE}@units=mm",
    "keys": "name,date,lon,lat,value",
    "selection": f"name={SAMPLE_VARIABLE}",
    "queryentries": f"name={SAMPLE_VARIABLE}",

    # --- remaining required names, one operator or two each ---
    "start": "1",
    "stop": "10",
    "fcut": "5",
    "fmin": "1",
    "fmax": "5",
    "lon0": "0",
    "lat0": "0",
    "r": "1000",
    # selcircle's key=value form; the radius has to carry its unit.
    "circle": "lon=0,lat=0,radius=1000km",
    "level": "0",
    "ltype": "105",
    "oldtype": "105",
    "newtype": "105",
    "oldtab": "128",
    "newtab": "128",
    "lhalo": "1",
    "rhalo": "1",
    "nsteps": "10",
    "c2": "1",
    "frequency": "day",
    "unit": "mm",
    # ``sp2sp`` is this key's only consumer, and 21 was a no-op against the T21
    # sample the lab builds: measured, ``cdo sp2sp,21`` over a T21 file exits 0
    # and ``cdo sinfon`` reports the same "T21 complexPacking" grid it was
    # given. A pass that changed nothing. 10 truncates — the output comes back
    # "points=132 nsp=66 T10" — so the sweep now tests the operator rather than
    # its no-op case.
    #
    # Note this key does *not* reach sp2gp/gp2sp: their single slot is declared
    # as ``type`` because a bare integer there is read as a grid type, never as
    # a truncation (``cdo sp2gp,42`` is "Unsupported type: 42").
    "trunc": "10",
    "wnums": "1,2",
    "pairs": "1,2",
}

#: Values that beat :data:`PARAMETER_DEFAULTS` for one operator.
#:
#: Some parameter names in the schema are too generic to carry a single value.
#: ``pairs`` is the clearest case: ``chcode,1,2`` wants numbers, ``chname``
#: wants variable names and ``chunit`` wants a unit string, yet all three call
#: the parameter ``pairs``. Keying the exception by operator keeps the shared
#: table meaningful instead of degrading it to the one value that offends
#: everybody equally.
OPERATOR_PARAMETERS: Dict[str, Dict[str, str]] = {
    "chname": {"pairs": f"{SAMPLE_VARIABLE},renamed"},
    "chvar": {"pairs": f"{SAMPLE_VARIABLE},renamed"},
    "chunit": {"pairs": "mm,cm"},
    "chlevel": {"pairs": "0,1"},
    # The harness hands mrotuvb two files on the same grid, which is exactly the
    # case CDO wants this flag for.
    #
    # The value is "true" and not "noint", which it used to be. ``noint`` is
    # declared ``form=flag`` now rather than as a one-choice string, and a flag
    # is rendered from its *truth value*: ``parameter_tokens`` emits the bare
    # word ``noint`` when the value reads as true, and emits nothing at all
    # otherwise. "noint" is not one of the accepted truth spellings, so it
    # reads as neither true nor false — ``invalid_parameter_values`` refuses it
    # before the run, which is how this was caught.
    "mrotuvb": {"noint": "true"},

    # --- Miscellaneous: the shared-name exceptions ---
    #
    # ``radius`` means a planet radius in metres to gridarea and a *search*
    # radius to smooth, where the unit suffix is part of the value: 6371000
    # would be read as 6371000 degrees. CDO's own default is 1deg.
    "smooth": {"radius": "5deg"},
    # ``lat`` means a latitude in degrees to gridcellindex and a *hemisphere* to
    # symmetrize, where the only two values are "negative" and "positive".
    "symmetrize": {"lat": "negative"},
    # ``inc`` means a time increment ("1day") to settaxis and friends, which is
    # most of the catalog, and a numeric step to seq. "1day" there is "Float
    # parameter >1day< contains invalid character at position 2!".
    "seq": {"inc": "2"},
    # ``v`` means a wind-speed threshold to the four strong-wind indices and the
    # *name of the V variable* to the two NCL wind operators. Both files the
    # harness builds carry one variable, so these two are in UNTESTABLE — the
    # names are declared so that a future two-component fixture needs no
    # further profile work.
    "uv2vr_cfd": {"u": "u", "v": "v"},
    "uv2dv_cfd": {"u": "u", "v": "v"},
    # The wind-rotation family names its two components rather than changing a
    # pair of values, so "1,2" would be read as GRIB codes 1 and 2.
    "rotuvb": {"pairs": "u,v"},
    "rotuvN": {"pairs": "u,v"},
    "rotuvNorth": {"pairs": "u,v"},
    "projuvLatLon": {"pairs": "u,v"},
    "uvDestag": {"pairs": "u,v"},
    # setattribute assigns, delattribute only names — a value would be part of
    # the attribute name it went looking for.
    "delattribute": {"attrs": "history"},
    # ``freq`` means a bare "m" to the ETCCDI bootstrapping indices and a
    # key=value pair to every other index, which is exactly the ``pairs``
    # problem this table exists for. The default is the majority form; these
    # seven are the exception, verified one at a time against the binary.
    "etccdi": {"freq": "m"},
    "etccdi_tn10p": {"freq": "m"},
    "etccdi_tn90p": {"freq": "m"},
    "etccdi_tx10p": {"freq": "m"},
    "etccdi_tx90p": {"freq": "m"},
    "etccdi_r95p": {"freq": "m"},
    "etccdi_r99p": {"freq": "m"},
    # A year strictly between the two the bracketing samples carry, so the
    # value moves with the samples instead of being asserted alongside them.
    "intyear": {"years": str(BRACKET_YEARS[0] + 1)},

    # --- Climate model output rewriting ---
    #
    # ``cmor`` is in UNTESTABLE and will stay there until a CMOR-enabled binary
    # is to hand, so nothing below is ever executed today. It is declared all
    # the same, and the reason is the difference between the two kinds of skip:
    # with no entry here the harness would refuse the operator at
    # ``_resolve_parameters`` for "no default for the required parameter
    # 'MIPtable'" — a fact about this file — while the reported reason claimed a
    # missing build feature. Two different causes, one of them wrong, and the
    # wrong one is the one that would go on being true after somebody installed
    # a CDO with CMOR. Now the only thing standing between this operator and a
    # run is the capability the reason names.
    #
    # Four of these are ``""`` on purpose rather than absent, and that is the
    # substance of the entry. ``name``, ``code`` and ``units`` are generic
    # parameter names the shared PARAMETER_DEFAULTS table already answers — with
    # ``random``, ``1`` and ``days`` — and inheriting those would build
    # ``cmor,…,name=random,code=1,units=days``, which asks CDO to select one
    # infile variable by name *and* by code at once, with no ``cmor_name`` to map
    # either onto. That is the exact mistake the operator's DESCRIPTION warns
    # about, assembled by accident out of defaults meant for other operators.
    # Blanked here, which ``parameter_tokens`` renders as absent.
    #
    # ``MIPtable`` is a bare table name rather than a path to a synthesised
    # stub: CMOR resolves names against its own search path, and the harness's
    # generic fallback for a required ``file`` parameter — a text file
    # containing "0" — is not a MIP table and would fail for a reason that has
    # nothing to do with what is being tested. A real run therefore needs a real
    # table as well as a CMOR-enabled build; the skip names the build because
    # that is the one that has to be fixed first.
    "cmor": {
        "MIPtable": "CMIP6_day.json",
        "cmor_name": "tas",
        "name": "",
        "code": "",
        "units": "",
        # Left to the execution layer's own default, which is the per-run
        # directory it creates and records. Naming one here would put the DRS
        # tree outside the sweep's output directory.
        "drs_root": "",
        # The documented "no compression" value, and a negative integer, so a
        # future run exercises the one parameter whose legal value looks like a
        # parse error.
        "deflate_level": "-1",
    },

    # --- Interpolation ---
    # intlevel's parameter is ``level`` now that the four real ones are
    # declared, and the shared "level" default is "0" — a level the vertical
    # sample does not carry. These ask for levels inside SAMPLE_LEVELS
    # (1000, 850, 500) so the run exercises the interpolation rather than
    # failing on a target outside the source range. ``zdescription`` is left
    # unset deliberately: CDO aborts with "Parameter zdescription and level
    # can't be mixed!" if both are given, so the lab exercises the level form.
    "intlevel": {"level": "900,700"},
    "intlevelx": {"level": "900,700"},
    # The shared "levels"/"hlevels" default is "0", which for these two is a
    # target height below everything the sample carries — the geoheight sample's
    # zg runs 500 to 3500 m — so the run succeeded without interpolating
    # between anything. These sit inside that range.
    "gh2hl": {"levels": "800,1500,2500"},
    "gh2hlx": {"levels": "800,1500,2500"},
    # The k-nearest-neighbour trio takes keyword parameters now rather than one
    # free-text string, so each gets a real value instead of the old
    # "grid=r360x180" blob. weighted=gauss is chosen over the default dist
    # precisely because it is the branch gauss_scale reaches.
    **{name: {"grid": SAMPLE_GRID, "k": "4", "kmin": "2",
              "weighted": "gauss", "gauss_scale": "0.2",
              "extrapolate": "true"}
       for name in ("remapknn", "genknn", "intgridknn")},
    # remapdis's undocumented second positional, so the schema entry is backed
    # by a run: the banner prints "(k=6)" when this arrives.
    "remapdis": {"k": "6"},
    # map3d=true on the gen* family, which is what makes the run write
    # <outfile><00001>.nc instead of the path it was given. Exercising it here
    # is what tests writes_output_prefix end to end rather than in isolation.
    **{name: {"map3d": "true"}
       for name in ("genbil", "genbic", "gencon", "genlaf", "gennn", "gendis",
                    "genycon")},

    # --- File operations ---
    # ``names`` means a list of variables nearly everywhere and one of two fixed
    # words here: CDO answers the default with "Invalid value for key >names<
    # (names=<union/intersect>)". union is the interesting half — it fills a
    # variable one input lacks with missing values rather than refusing.
    "mergetime": {"names": "union"},
    # ``gridtype`` is "lonlat" for every other operator that takes one, and
    # collgrid takes it only to be told the inputs are *unstructured*. Handed
    # the default it aborts with "gridtype=-2086645503 unsupported!" — the
    # word parsed into an internal constant it does not accept here. The
    # samples are on a lon/lat grid, so the honest value is no value: the
    # parameter is optional and omitting it is what the manual describes.
    #
    # ``levidx`` is left empty for the same reason. It selects level indices out
    # of the inputs, the samples have one level, and CDO rejects both 0 and 1
    # for it ("Level index 1 not found!") on a single-level file.
    #
    # ``nx`` is left empty too. It defaults to the number of input files, which
    # is exactly right for however many the harness supplies; the shared default
    # of 3 fights that and aborts with "Number of input files (2) and number of
    # blocks (3x0) differ!". The manual says nx is needed only for curvilinear
    # grids, and the samples are not.
    "collgrid": {"nx": "", "gridtype": "", "levidx": ""},
    # ---- Transformation: the shared ``gridtype`` default is invalid here ----
    #
    # ``PARAMETER_DEFAULTS["gridtype"]`` is "lonlat", which is right for
    # collgrid's vocabulary and is not a word the Wind module knows: measured on
    # 2.6.3, ``cdo uv2dv,lonlat`` would be "(Abort): Unsupported type: lonlat".
    # It no longer even gets that far — the slot declares
    # ``choices=("quadratic", "linear", "cubic")`` and
    # ``invalid_parameter_values`` now enforces ``choices``, so the app refuses
    # it before argv. Either way the shared default cannot serve these four,
    # which is exactly what this table is for.
    #
    # "quadratic" rather than one of the other two because it is the only type
    # the spectral-to-gridpoint direction can do on a CDO built without FFTW3:
    # measured, ``cdo dv2uv,linear`` and ``dv2uv,cubic`` abort with "LIBFFTW3
    # support not compiled in!" while ``dv2uv,quadratic`` exits 0, and all
    # three work for uv2dv. Naming it explicitly also means the sweep exercises
    # the parameter instead of the bare form.
    **{name: {"gridtype": "quadratic"}
       for name in ("dv2uv", "uv2dv", "uv2dvl")},
    # ``dv2uvl`` is the exception, and gets *no* value rather than that one.
    # The type parameter does not rescue it: measured on 2.6.3, bare,
    # ``,quadratic`` and ``,cubic`` all abort with "(Abort): FFT error!" —
    # quadratic does not even change the transform length, which stays at the
    # linear "len=44 (n=11)" the bare form reports. So there is no working call
    # on this build, and passing a type would dress a build limitation up as a
    # parameter choice. The empty string is needed rather than absence because
    # the shared "lonlat" would otherwise apply, exactly as for collgrid above.
    "dv2uvl": {"gridtype": ""},
    # The Spectral four take one slot spelled three ways, declared as ``type``.
    # There is no shared default for that name, so without these the sweep would
    # run the bare form and never test the parameter at all.
    #
    # gp2sp/gp2spl get "linear": the forward direction takes all three types on
    # this build (measured, ``cdo gp2sp,linear`` and ``gp2sp,cubic`` both exit
    # 0), so a non-default value proves the slot is wired.
    "gp2sp": {"type": "linear"},
    "gp2spl": {"type": "linear"},
    # sp2gp gets the keyword spelling instead, so the sweep covers the *other*
    # grammar this one slot accepts. 42 rather than 21: measured, trunc must be
    # at least the input truncation — ``cdo sp2gp,trunc=20`` over T21 is
    # "(Abort): Output trunctation=20 muss be greater than input
    # trunctation=21" — and 42 is a value this build's FFT can actually do,
    # where 22 passes that check and then dies in the FFT.
    "sp2gp": {"type": "trunc=42"},
    # sp2gpl is left bare deliberately. It is the linear shorthand, linear is
    # the one type a CDO without FFTW3 cannot do in this direction, and every
    # call aborts with "(Abort): FFT error!" whatever is passed — measured with
    # no parameter, with "quadratic" and with "trunc=42". Setting a value would
    # dress a build limitation up as a parameter problem.
    # ``ndup`` is optional and defaults to 2. Given explicitly so the sweep
    # exercises the parameter rather than the bare form, and kept small because
    # the operator's output is ndup times its input.
    "duplicate": {"ndup": "3"},
    # The Split module's ``uuid``, exercised on all nine.
    #
    # ``swap`` is deliberately left unset, and the reason is about the harness
    # rather than the operator. It swaps obase and xxx in the output filename,
    # and this sweep's obase is an *absolute path*, so swapping puts the xxx in
    # front of the whole thing: `cdo splitname,swap infile /a/b/out` tries to
    # create `random/a/b/out.nc` and fails with "No such file or directory".
    # That is correct behaviour on a value no user would pair with an absolute
    # obase, and setting it here would report nine operators as broken for it.
    # The flag's argv shape is covered instead by
    # ``tests/test_file_operations_category.py``, which can assert it without
    # needing a relative working directory.
    **{name: {"uuid": "operator_lab"} for name in (
        "splitcode", "splitparam", "splitname", "splitlevel", "splitgrid",
        "splitzaxis", "splittabnum", "splitensemble", "splitvar",
    )},
    # Positional and strftime, so a literal like "union" would end up in the
    # filename. %B is the manual's own example.
    "splitmon": {"format": "%B"},
    # The two BOOL keywords, exercised as true so their stdout is produced and
    # ``stream_notices`` has something to lift out.
    "pack": {"printparam": "true"},
    "bitrounding": {"printbits": "true", "inflevel": "0.999"},
}

#: What a synthesised parameter *file* should contain, by parameter name.
#: Used for schema params of kind ``file`` when no real file is to hand; the
#: generic fallback is a single ``0``, which satisfies the operators that only
#: want a column of numbers.
PARAMETER_FILE_CONTENT: Dict[str, str] = {
    "attfile": '&history@global\n  text = "written by operator_lab"\n/\n',
    "table": "&parameter\n  code = 1\n  name = var1\n/\n",
    "vct": "0 0.0 0.0\n1 0.0 1.0\n",
    # exprf/aexprf read a CDO expression script, and the generic "0" fallback is
    # a syntax error to the expression parser. tee shares the parameter name but
    # only ever writes to the path, so the content it inherits does not matter.
    #
    # The same four statements ``instr`` carries, one per line, which is the one
    # thing the file form can do that the inline form cannot — so a script that
    # was a single line was not testing the file form at all. See the comment on
    # PARAMETER_DEFAULTS["instr"] for why these four.
    "filename": (
        f"_tmp = {SAMPLE_VARIABLE} * 2;\n"
        f"masked = isMissval({SAMPLE_VARIABLE}) ? 0 : _tmp;\n"
        f"bounded = min({SAMPLE_VARIABLE},0.5);\n"
        f"_ALL_ = _ALL_;\n"
    ),
}

#: The same thing keyed by operator *and* parameter name, for the four File
#: operation operators that share the parameter name ``filename`` with
#: ``exprf``/``aexprf`` and mean something completely different by it.
#:
#: Keying on the name alone was enough while one family used it. It stopped
#: being enough the moment ``pack``, ``bitrounding``, ``setchunkspec`` and
#: ``setfilter`` were declared: all four inherited the expression script above
#: and failed on its contents — "Too many values for parameter key >_tmp<" —
#: which reads like a bug in the operator and is a wrong test fixture.
#:
#: Every body below was run. The two quoted ones are quoted for a measured
#: reason: the outer key=value parser splits on ``=`` and ``,``, so the
#: documented ``varname=<chunkspec>`` and ``varname=<filterspec>`` grammars only
#: survive inside quotes. See ``_SURPRISING_DEFAULTS`` in core/categories.py.
PARAMETER_FILE_CONTENT_FOR: Dict[str, Dict[str, str]] = {
    # Documented format: name=<> add_offset=<> scale_factor=<>
    "pack": {"filename":
             f"name={SAMPLE_VARIABLE} add_offset=0 scale_factor=0.001\n"},
    # Documented format: name=numbits
    "bitrounding": {"filename": f"{SAMPLE_VARIABLE}=7\n"},
    # Chunk the time dimension. Unquoted this is a parse error on 2.6.3.
    "setchunkspec": {"filename": f'{SAMPLE_VARIABLE}="t=1"\n'},
    # Filter 1 is zlib at level 4 — deliberately not the manual's bzip2
    # example (307), which needs an HDF5 plugin the machine may not have and
    # fails inside NetCDF rather than in CDO. zlib is built in, so this tests
    # the operator instead of the plugin path.
    "setfilter": {"filename": f'{SAMPLE_VARIABLE}="1,4"\n'},
}


def parameter_file_content(name: str, operator: str = "") -> str:
    """Body for a synthesised parameter file named ``name``.

    ``operator`` disambiguates the parameter names that mean different things to
    different operators — ``filename`` is an expression script to ``exprf`` and
    a table of packing constants to ``pack``. Optional so existing callers keep
    working; without it the name-only table is used exactly as before.
    """
    per_operator = PARAMETER_FILE_CONTENT_FOR.get(operator, {})
    if name in per_operator:
        return per_operator[name]
    return PARAMETER_FILE_CONTENT.get(name, "0\n")


# --------------------------------------------------------------------------
# Operators a file-driven sweep cannot reach
# --------------------------------------------------------------------------

#: ``operator -> why it is skipped``. The reason is reported verbatim, so each
#: one has to say what is missing rather than merely that something is.
#:
#: Every entry here is a limitation of *automated bulk testing*, not a claim
#: that the operator is broken: they need a namelist on stdin, a resource this
#: harness has no way to synthesise (CMOR tables, DCW coastlines, HEALPix
#: grids), or a build flag the installed CDO was not compiled with.
UNTESTABLE: Dict[str, str] = {
    # Read their payload from stdin and block forever without one.
    "input": "reads its field data from stdin",
    "inputext": "reads its field data from stdin",
    "inputsrv": "reads its field data from stdin",
    "after": "reads an afterburner namelist from stdin",
    "afterburner": "reads an afterburner namelist from stdin",
    "sincos": "reads a namelist from stdin",
    "coshill": "reads a namelist from stdin",
    "setgatts": "reads an attribute namelist from stdin",
    "setgridarea": "reads a grid-area namelist from stdin",
    "setgridmask": "reads a grid-mask namelist from stdin",
    "setzaxis": "reads a z-axis description from stdin",
    "changemulti": "reads a multi-parameter namelist from stdin",
    "delmulti": "reads a multi-parameter namelist from stdin",
    "selmulti": "reads a multi-parameter namelist from stdin",
    "seloperator": "reads a multi-parameter namelist from stdin",
    "selrec": "reads a record namelist from stdin",
    "selregion": "reads a region description from stdin",
    "setgridcell": "reads a grid-cell namelist from stdin",
    # ``splittabnum`` was here, and it does not belong: with stdin closed,
    # ``cdo splittabnum infile obase`` exits 0 and writes obase000.nc, and so
    # does ``cdo splittabnum,uuid=x``. The skip was an artefact of the schema
    # declaring a ``tabnums`` parameter that CDO 2.6.3 does not have — supplied
    # one, the operator asked for the value it could not parse, which is the
    # stdin prompt the reason described. The parameter is gone; so is the skip.
    "maskregion": "reads a region description from stdin",
    "intgridtraj": "reads a trajectory description from stdin",

    # Need an external resource the harness cannot synthesise.
    "conv_cmor_table": "needs a CMOR table file",
    "dump_cmor_table": "needs a CMOR table file",
    # Measured on the installed 2.6.3: ``cdo --config has-cmor`` answers ``no``,
    # and every call — including one with a deliberately bogus key — aborts with
    # "CMOR support not compiled in!" before the parameters are parsed at all.
    # A capability skip, not a missing-declaration one: the parameters this
    # would run with are in OPERATOR_PARAMETERS above, so the day a CMOR-enabled
    # binary appears, deleting this line is the whole change.
    "cmor": "needs a CDO built with CMOR support",
    "dcw": "needs the Digital Chart of the World data (DIR_DCW)",
    "specinfo": "needs a spectral descriptor rather than a file",
    "lic": "needs a GMT colour-palette file",
    "outputkml": "needs a GMT colour-palette file",
    "outputvrml": "needs a GMT colour-palette file",
    "outputboundscpt": "needs a GMT colour-palette file",
    "outputcentercpt": "needs a GMT colour-palette file",
    # Measured on 2.6.3 against the r36x18 sample, with and without every
    # parameter: "Input grid is not healpix!", every time. The grid check runs
    # before the parameter *values* are validated, which is why the schema's
    # order= choice list is documented as coming from the manual rather than
    # from the binary — ``hpdegrade,nside=4,order=bogus`` also gets the grid
    # message, not a complaint about the ordering.
    "hpdegrade": "needs a HEALPix grid",
    "hpupgrade": "needs a HEALPix grid",

    # --- Miscellaneous: rotated, curvilinear and multi-component inputs ---
    #
    # The four positional wind operators need a rotated lon/lat grid, and say
    # so precisely. Measured on 2.6.3 against a plain lonlat file holding
    # variables named u and v: ``rotuvNorth,u,v`` and ``projuvLatLon,u,v`` are
    # "Only rotated lon/lat grids supported!", ``rotuvb,u,v`` likewise. The
    # parameters *parse* — that is how their positional grammar was
    # established — so the skip is about the grid and not about the token.
    "rotuvNorth": "needs a rotated lon/lat grid",
    "projuvLatLon": "needs a rotated lon/lat grid",
    "rotuvb": "needs a rotated lon/lat grid",
    # uvDestag is the same module and fails differently: on a plain grid it is
    # "Unexpected operatorID 0", which is neither a grid message nor a
    # parameter one. It needs staggered (Arakawa C) input, which is what the
    # offsets describe.
    "uvDestag": "needs data on a staggered (Arakawa C) grid",
    # The two NCL wind operators need *both* components in one file, found by
    # the u= and v= parameters. Every sample this harness generates carries a
    # single variable, so the abort is "u not found!" whatever is passed.
    # Their keyword grammar was still established from the binary — the
    # positional spelling is "Parse error!", which fires before the lookup.
    "uv2vr_cfd": "needs one file holding both wind components, named by u= and v=",
    "uv2dv_cfd": "needs one file holding both wind components, named by u= and v=",

    # --- Miscellaneous: MPIOM ocean fields ---
    #
    # All three want two specific sea-water variables in one file and abort
    # before doing anything else. Measured on 2.6.3 against files built with
    # the documented names and with the documented GRIB codes: tho/sao, tho/s,
    # t/sao and code 2 + code 5 were each tried, and adisit answers "Sea water
    # salinity not found!" to every one of them. rhopot gets as far as
    # "In-situ temperature not found!" against a tho/sao file, which is a
    # different message and the reason its parameter grammar *could* be
    # measured while adisit's and adipot's could not.
    "adisit": "needs MPIOM ocean fields: potential temperature (tho) and salinity (sao)",
    "adipot": "needs MPIOM ocean fields: in-situ temperature (t) and salinity (sao)",
    "rhopot": "needs MPIOM ocean fields: in-situ temperature (to) and salinity (sao)",
    # Needs a zonal mean of v on *pressure* levels. Measured: the zonal mean
    # alone is not enough — ``cdo zonmean -selname,v`` then ``mastrfu`` is
    # still "Unexpected vertical grid surface!", because the samples are
    # surface fields.
    "mastrfu": "needs a zonal mean of v-velocity on pressure levels",
    "samplegridicon": "needs an ICON grid file",
    "subgrid": "needs an LCC-projected grid; CDO says to use selindexbox otherwise",

    # Need binary inputs in formats the harness does not produce.
    "import_amsr": "needs an AMSR binary file",
    "import_cmsaf": "needs a CM-SAF HDF5 file",
    "import_e5ml": "needs an ECHAM5 model-level GRIB file",
    # The export half wants what the import half reads: a Gaussian grid, its
    # spectral fields and a hybrid z-axis together. Fed each in turn it simply
    # names the next thing it is missing.
    "export_e5ml": "needs an ECHAM5 dataset: Gaussian, spectral and hybrid levels",
    # A curvilinear MPIOM grid. On a plain lon/lat grid CDO trips over its own
    # variable list; on a rotated pole it says "Grid projection unsupported!".
    "mrotuv": "needs a curvilinear MPIOM grid",
    "splitensemble": "needs GRIB2 ensemble data (key perturbationNumber)",
    "import_fv3grid": "needs an FV3 grid file",
    "import_grads": "needs a GrADS control file",
    "import_binary": "needs a GrADS control file describing raw binary data",
    "import_obs": "needs an observation data file",

    # Need field structure the generated samples deliberately do not have.
    "outputts": "needs a single-gridpoint time series",
    "graph": "needs a single-gridpoint time series",
    # gh2hl/gh2hlx and intlevel3d/intlevelx3d were here. All four are built
    # now, the same way ap2pl's "airpressure" sample was: gh2hl wants a 3D
    # field carrying the CF standard name geometric_height_at_full_level_center
    # ("geoheight"), and intlevel3d wants three files — data, a 3D vertical
    # source coordinate and a 3D vertical target coordinate — which are
    # "levels3d_data", "levels3d_coord" and the tgtcoordinate parameter file.
    # The first skip reason was also wrong on its own terms: gh2hl asks for
    # *geometric* height, not geopotential height.
    # conj and im were here for complex-valued data. The harness can build it
    # now — `cdo -f nc4 retocomplex` — so they are tested rather than excused.

    # Not implemented upstream; CDO refuses the name even though it is
    # catalogued. Quoted rather than paraphrased, because the binary's own
    # sentence names the two replacements and a paraphrase would not:
    #
    #     $ cdo eof3dspatial,2 sample_airpressure.nc a.nc b.nc
    #     cdo eof3dspatial (Abort): Operator not Implemented - use eof3d or
    #                               eof3dtime instead
    #
    # Exit code 1, re-measured on 2.6.3. This is the *whole* of the EOFs section
    # that is untestable — see below for the entry that used to sit beside it.
    "eof3dspatial": ("CDO refuses the name: 'Operator not Implemented - use "
                     "eof3d or eof3dtime instead'"),
    #
    # ``eofspatial`` was here, described as "superseded by eof/eoftime", and it
    # was measurably wrong. On 2.6.3 the operator runs:
    #
    #     $ cdo eofspatial,2 sample_climate_tg.nc eval.nc eofs.nc
    #     exit 0; eval.nc has 648 timesteps on a 1x1 grid,
    #             eofs.nc has 2 timesteps on the 36x18 data grid
    #
    # and it returns byte-identical output to ``cdo eof,2`` on this sample —
    # ``cdo diffn`` reports no differing fields for either file, because eof
    # dispatches to the spatial algorithm when the grid is smaller than the time
    # axis, which it is here (648 < 730).
    #
    # What the original note almost certainly measured is the *arity*, not the
    # operator. Given one output file it aborts, and the message says nothing
    # about being superseded:
    #
    #     $ cdo eofspatial,2 infile outfile
    #     cdo (Abort): Missing inputs
    #
    # That is a (1|2) operator handed one target, which is a statement about how
    # it was called. ``eof3dspatial`` above genuinely refuses its own name, and
    # the two failures reading alike from a one-output sweep is what made one
    # entry out of two different facts. The harness has handled ``nout >= 2``
    # correctly since ``_output_targets`` was written, so ``eofspatial`` is now
    # swept like any other operator rather than excused.
    # The seasonal comparison module is broken, and the evidence is that nothing
    # else with the same inputs is: yseasadd and yseassub both pass on
    # (series, yseasmean), and so do all six ymon* comparisons on
    # (series, ymonmean). Only these six abort. Handed a yseasmean with exactly
    # one field per season they say "Season MAM already allocated!"; handed a
    # single-season file as *both* inputs they say "Season MAM not found!" about
    # the season that file is made of — so the module stores and looks up the
    # season table by different keys. No input this harness can build gets past
    # it, and there is no --seasonstart to change the convention with.
    #
    # First measured on 2.6.0 and re-measured in full on 2.6.3, where it is
    # unchanged. Two corrections to the original note, both from that re-run:
    # the version is no longer named as 2.6.0 alone, and yseasrange is no longer
    # named as a third control. It never was one — `cdo --operators` gives it
    # (1|1), so handed two files it fails with "Operator cannot be assigned",
    # which is a statement about arity and not about the season table. It passes
    # in this harness because the harness gives a one-input operator one file.
    # The two real controls are yseasadd and yseassub, both (2|1), both passing.
    #
    # The app states this before a run as well; see _YSEASCOMP_ABORTS in
    # core/categories.py, which is kept in step with this entry.
    **{name: "CDO's seasonal comparison operators mis-key their season table "
             "(measured on 2.6.0 and 2.6.3); yseasadd/yseassub pass on the "
             "same two files"
       for name in ("yseaseq", "yseasne", "yseasle", "yseaslt",
                    "yseasge", "yseasgt")},
    # `cdo --operators` lists infov as an alias of infon, but the binary then
    # refuses it: "cdo infon (Abort): Operator not callable by this name!".
    # The mapping is CDO's own — sinfov, its counterpart, still works.
    "infov": "CDO 2.6.0 catalogues the name but will not run it",
    # The module name rather than an index. `cdo -h etccdi` documents the six
    # etccdi_* operators and none called etccdi; given any argument list it
    # aborts inside CDI on a null variable name.
    "etccdi": "the ETCCDI module name, not one of its indices",

    # The ECHAM-style diagnostics: every one needs a 3D field on hybrid sigma
    # pressure levels, with the surface pressure and the vertical coordinate
    # table that go with it. Nothing built from `cdo -random` has that, and a
    # failure against ordinary data says nothing about the operator.
    "air_density": "needs 3D data on hybrid sigma pressure levels",
    "delta_pressure": "needs 3D data on hybrid sigma pressure levels",
    "gheight": "needs 3D data on hybrid sigma pressure levels",
    "gheight_full": "needs 3D data on hybrid sigma pressure levels",
    "gheight_half": "needs 3D data on hybrid sigma pressure levels",
    "gheighthalf": "needs 3D data on hybrid sigma pressure levels",
    "pressure": "needs 3D data on hybrid sigma pressure levels",
    "pressure_full": "needs 3D data on hybrid sigma pressure levels",
    "pressure_half": "needs 3D data on hybrid sigma pressure levels",
    "sealevelpressure": "needs 3D data on hybrid sigma pressure levels",
    # Names its three missing inputs itself: temperature (130), specific
    # humidity (133) and vertical velocity (135), on hybrid levels.
    "vertwind": "needs ECHAM codes 130/133/135 on hybrid sigma pressure levels",

    # Deleting or re-selecting by a code, table number or standard name the
    # sample does not carry leaves nothing behind, and CDO calls an empty
    # result an error. A single-variable sample cannot exercise these; a file
    # with several variables and real GRIB metadata can (use --input).
    "delname": "would delete the only variable in the sample",
    "delvar": "would delete the only variable in the sample",
    # Same cause: `delete,name=random` is accepted and works against a file with
    # two variables, but here it empties the file and CDO calls that an error.
    "delete": "would delete the only variable in the sample",
    "chlevelc": "addresses data by GRIB code; the sample carries none",
    "selcode": "the sample carries no GRIB code to select by",
    "selltype": "the sample carries no GRIB level type to select by",
    "selparam": "the sample carries no GRIB parameter to select by",
    "selstdname": "the sample variable has no CF standard name",
    "seltabnum": "the sample carries no GRIB table number to select by",
    "selzaxisname": "the sample has only the default surface z-axis",
}


def skip_reason(operator: str) -> Optional[str]:
    """Why ``operator`` cannot be bulk-tested, or None if it can."""
    return UNTESTABLE.get(operator)


# --------------------------------------------------------------------------
# Preferred file extensions
# --------------------------------------------------------------------------

#: Extensions for operators whose *input file* is not an ordinary data file.
#:
#: Only operators that really take an input slot belong here. The CMOR-table
#: operators do not: their table is a trailing parameter, and CDO gives them a
#: signature with no input at all, so listing a ``.json`` for them would put a
#: file in a column that describes a slot they do not have.
_INPUT_EXTENSIONS: Dict[str, str] = {
    "import_binary": ".ctl",
    "import_grads": ".ctl",
    "import_cmsaf": ".h5",
    "import_amsr": ".hdf",
    "import_e5ml": ".grb",
    "import_e5res": ".grb",
    "import_obs": ".txt",
    "import_fv3grid": ".nc",
}

#: Extensions for operators that write something other than NetCDF, including
#: the stdout-only ones whose "output" is whatever the shell redirects it to.
_OUTPUT_EXTENSIONS: Dict[str, str] = {
    # Text formats with a conventional suffix of their own.
    "gradsdes": ".ctl",
    "gmtcells": ".gmt",
    "gmtxyz": ".gmt",
    "outputbounds": ".gmt",
    "outputboundscpt": ".gmt",
    "outputcenter": ".gmt",
    "outputcenter2": ".gmt",
    "outputcentercpt": ".gmt",
    "outputtri": ".gmt",
    "outputkml": ".kml",
    "outputvrml": ".wrl",
    "outputsrv": ".srv",
    "outputext": ".ext",
    # Binary exports.
    "export_e5ml": ".grb",
    "export_e5res": ".grb",
}

#: Every data format the operators will read. Offered as the file-dialog filter
#: in the GUI, and quoted in the report so "preferred" reads as a preference
#: rather than a requirement.
DATA_EXTENSIONS: Tuple[str, ...] = (
    ".nc", ".nc4", ".cdf", ".nc2", ".grb", ".grb2", ".grib", ".grib2",
    ".srv", ".ext", ".ieg",
)


def preferred_input_extension(operator: str) -> str:
    """The extension this operator's input file usually has.

    ``—`` for the generator operators (``random``, ``const``, ``topo``…) that
    take no input at all, and for the two that take neither input nor output.
    Everything else defaults to ``.nc``: CDO reads GRIB and SERVICE just as
    happily, but NetCDF is what this project is built around and what the
    samples are written as.
    """
    spec = OPERATOR_SCHEMA.get(operator)
    # "No input slot" wins over any override: an operator CDO reports as
    # taking zero inputs has nowhere to put a file, whatever format it reads
    # through its parameters.
    if spec is not None and spec.nin == 0:
        return "—"
    return _INPUT_EXTENSIONS.get(operator, ".nc")


def preferred_output_extension(operator: str) -> str:
    """The extension this operator's output file usually has.

    An operator with ``nout == 0`` writes to stdout — ``info``, ``sinfo``,
    ``showname``, the whole informational family — so its "output file" is
    whatever that stream is redirected into, and the harness captures it as
    ``.txt``. The split family (``nout == -1``) is given a base path that CDO
    extends with its own per-file suffix, so the extension quoted is the one
    the *pieces* end up with.
    """
    override = _OUTPUT_EXTENSIONS.get(operator)
    if override:
        return override

    spec = OPERATOR_SCHEMA.get(operator)
    if spec is None:
        return ".nc"
    if spec.nout == 0:
        return ".txt"
    return ".nc"


#: Operators whose single output file is not a map, keyed by what it is instead.
#:
#: "file" is a true answer for ``fldcor`` and a useless one: what it writes is a
#: 1x1 grid with one value per timestep — a scalar time series — and a reader
#: scanning the output column for something to plot cannot tell it from
#: ``timcor``'s full map. Measured on CDO 2.6.3, two 18x9 inputs of 6 timesteps:
#: ``fldcor`` → points=1, 6 steps; ``timcor`` → points=162, 1 step.
#:
#: Derived from the category rather than listed operator by operator, because
#: the distinction is exactly the one ``NCExplorerCategory.CORRELATION``
#: already draws: the two ``fld`` members reduce space and keep time, the two
#: ``tim`` members reduce time and keep space. The field statistics
#: (``fldmean``, ``fldsum`` …) collapse to a single point in the same way and
#: are described by the same rule, so the prefix is asked of operators the
#: category has already narrowed to those that reduce a whole field.
_SPATIAL_REDUCERS = ("fld", "zon", "mer")


def output_kind(operator: str) -> str:
    """How to read the output column: a file, a stdout capture, or a set.

    Kept separate from the extension because ``.txt`` alone cannot distinguish
    "writes a text file" from "prints to the terminal", and the report needs to
    say which — and, for the operators that reduce a field to a point, because
    "file" does not distinguish a map from a number.
    """
    spec = OPERATOR_SCHEMA.get(operator)
    if spec is None:
        return "file"
    if spec.nout == 0:
        return "stdout → text"
    if spec.nout == -1:
        return "split files (obase)"
    if spec.nout > 1:
        return f"{spec.nout} files"
    if operator.startswith("fld") and spec.category is NCExplorerCategory.CORRELATION:
        return "file (1x1 grid — a scalar series, not a map)"
    if operator.startswith(_SPATIAL_REDUCERS) \
            and spec.category is NCExplorerCategory.STATISTICAL_VALUES:
        return "file (reduced grid — not a map)"
    if operator.startswith("tim") and spec.category is NCExplorerCategory.CORRELATION:
        return "file (map, exactly 1 timestep)"
    return "file"
