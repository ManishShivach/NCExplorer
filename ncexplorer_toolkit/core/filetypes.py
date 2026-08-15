"""What CDO will actually read in one file slot, and the dialog filter for it.

Every "Browse" button in this app used to open the same chooser. It offered
nine filters — NetCDF, GRIB, GrADS descriptors, HDF5, text/table, GeoTIFF,
vector (``.shp``/``.geojson``/``.gpkg``/``.gml``/``.kml``), an "All Supported
Files" entry that unioned the lot, and All Files — regardless of which slot of
which operator was being filled. Two things were wrong with that, and they are
different sizes:

* Most of the list is not a CDO input at all. CDO reads GRIB, NetCDF, SERVICE,
  EXTRA and IEG and nothing else (manual §1.1: "The Climate Data Interface
  [CDI] is used for the fast and file format independent access to GRIB and
  NetCDF datasets. The local MPI-MET data formats SERVICE, EXTRA and IEG are
  also supported"). A shapefile or a GeoTIFF offered in the operator form is an
  offer the run cannot honour: those are the *map canvas's* formats, and they
  arrived in this chooser because one constant served both surfaces.
* A slot is not always a dataset. ``remap``'s ``weights`` is a SCRIP NetCDF
  file, ``maskregion``'s ``regions`` is ASCII polygons, ``cmor``'s ``MIPtable``
  is JSON, ``import_binary``'s *input* is a GrADS ``.ctl`` descriptor and not a
  dataset at all. One filter for all of them cannot be right for any of them.

So the kind of file a slot holds is declared on the schema — ``file_kind`` on
:class:`~.categories.OperatorParam` and :class:`~.categories.OperatorInput` —
and this module is the vocabulary those keys index into, the same arrangement
``units`` has with ``UNIT_FAMILIES`` and ``shape`` has with
``fieldshape.DETECTORS``. Nothing here knows about operators; it knows about
formats.

Two rules the entries follow, both learned from CDO rather than chosen:

* **Every filter ends in "All Files (*)".** Not a hedge — CDO's own examples
  name files with no extension at all (``cdo griddes infile > mygrid``, then
  ``cdo setgrid,mygrid …``), and SERVICE/EXTRA/IEG data is conventionally
  extensionless too. A filter that hid those would refuse a correct file, which
  is the worse error of the two. The list is short and every entry is a format
  CDO reads; that is what "fewer options" has to mean here.
* **A filter never claims more than the manual does.** Where CDO's own
  documentation does not say what a parameter file looks like — and for a
  handful of undocumented operators it does not, ``cdo -h`` answering "No help
  available for this operator!" — the kind is :data:`ANY` and the chooser says
  so, rather than guessing an extension the user then cannot see past.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple


# ---------------------------------------------------------------------------
# Extension groups
#
# Advisory, not authoritative: CDO identifies a file by its contents, not its
# name, so these decide what the dialog *offers* and never what a run accepts.
# ---------------------------------------------------------------------------

#: NetCDF, all five variants CDO supports (manual §1.2, ``-f <format>``:
#: nc1/nc2/nc/nc4/nc4c/nc5). ``.cdf`` and ``.netcdf`` are here because files in
#: the wild carry them; CDO never writes either, which is why
#: :data:`WRITTEN_EXTENSIONS` below is the smaller set.
NETCDF_EXTENSIONS: Tuple[str, ...] = (
    ".nc", ".nc1", ".nc2", ".nc4", ".nc5", ".cdf", ".netcdf",
)

#: GRIB 1 and 2. GRIB2 needs a CDO built with ecCodes; that is a run-time
#: concern for ``nc_integration``, not a reason to hide the format here.
GRIB_EXTENSIONS: Tuple[str, ...] = (".grb", ".grb1", ".grb2", ".grib", ".grib2")

#: The three local MPI-MET formats. Conventionally written without a suffix at
#: all, so these are a courtesy — the "All Files" entry is what actually
#: reaches them.
MPIMET_EXTENSIONS: Tuple[str, ...] = (".srv", ".ext", ".ieg")

#: Everything CDO can read as a dataset, in one tuple.
DATA_EXTENSIONS: Tuple[str, ...] = (
    NETCDF_EXTENSIONS + GRIB_EXTENSIONS + MPIMET_EXTENSIONS
)

#: The subset CDO *writes*, which is what an output path may end in.
#: ``core.nc_integration.OUTPUT_EXTENSIONS`` is derived from this so the engine
#: and the save dialog cannot disagree about which suffixes mean something.
WRITTEN_EXTENSIONS: Tuple[str, ...] = (
    ".nc", ".nc2", ".nc4", ".nc5",
    ".grb", ".grib", ".grb2", ".grib2",
    ".srv", ".ext", ".ieg",
)

#: The plain-text description and table files CDO reads as *parameters* — grid
#: descriptions, z-axis descriptions, parameter tables, region polygons,
#: vertical coordinate tables, the per-variable ``name=value`` files the File
#: operation section takes. CDO names none of these with a required extension,
#: which is why "All Files" matters more for this kind than for any other.
TEXT_EXTENSIONS: Tuple[str, ...] = (".txt", ".dat", ".asc", ".tab", ".lst")


# ---------------------------------------------------------------------------
# The kinds
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FileKind:
    """One answer to "what sort of file goes in this slot?".

    ``entries`` is the dialog's dropdown, in order, as ``(caption,
    extensions)`` pairs; the first is what the chooser opens on. An empty
    extension tuple means "every file", and only the last entry is ever
    allowed to be that.

    ``summary`` is one sentence for a tooltip — what CDO says the slot holds.
    It is on the kind rather than on the parameter because it describes the
    format, and the parameter's own ``help`` already describes the meaning.
    """

    key: str
    entries: Tuple[Tuple[str, Tuple[str, ...]], ...]
    summary: str

    @property
    def extensions(self) -> Tuple[str, ...]:
        """Every extension this kind offers, for scanning a folder.

        The union of the named entries, skipping the "All Files" one — a
        folder scan that accepted everything would sweep up the ``.png`` beside
        the data.
        """
        seen: list[str] = []
        for _caption, exts in self.entries:
            for ext in exts:
                if ext not in seen:
                    seen.append(ext)
        return tuple(seen)


#: "All Files (*)", the last entry of every kind. See the module docstring for
#: why it is never dropped.
_ANY_ENTRY: Tuple[str, Tuple[str, ...]] = ("All Files", ())

DATA = "data"
NETCDF = "netcdf"
GRID = "grid"
TEXT = "text"
JSON = "json"
CPT = "cpt"
CTL = "ctl"
HDF5 = "hdf5"
ANY = "any"


FILE_KINDS: Dict[str, FileKind] = {

    # -- datasets ----------------------------------------------------------
    #
    # The default for every operator input, and the one this whole module
    # exists to narrow. Four entries where there were nine, and all four are
    # formats CDO reads.
    DATA: FileKind(
        DATA,
        (
            ("CDO Data Files", DATA_EXTENSIONS),
            ("NetCDF Files", NETCDF_EXTENSIONS),
            ("GRIB Files", GRIB_EXTENSIONS),
            _ANY_ENTRY,
        ),
        "A dataset in a format CDO reads: NetCDF, GRIB, or the local "
        "SERVICE/EXTRA/IEG formats.",
    ),

    # NetCDF alone, for the slots where CDO says NetCDF and means it. The
    # remapping weights are the case that matters: "The remap type and the
    # interpolation weights of one input grid are read from a NetCDF file …
    # should follow the [SCRIP] convention" (Remap). A GRIB file there is not
    # a slow path, it is a failed run.
    NETCDF: FileKind(
        NETCDF,
        (
            ("NetCDF Files", NETCDF_EXTENSIONS),
            _ANY_ENTRY,
        ),
        "A NetCDF file. CDO requires NetCDF for this slot specifically — "
        "SCRIP weights and grid descriptions are only defined in NetCDF.",
    ),

    # -- descriptors -------------------------------------------------------
    #
    # A horizontal grid, which manual §1.5.2 says may be given three ways: a
    # predefined name (``r360x180``, ``t63grid`` — the form's preset dropdown
    # covers those, no file involved), a *data file* whose grid is copied
    # ("You can use the grid description from another datafile"), or a
    # description file — plain text in CDO's own format, or SCRIP in NetCDF
    # ("This grid description is stored in NetCDF").
    #
    # So both file shapes belong in this chooser, and the combined entry is
    # first because a user reaching for Browse here usually has one of them and
    # does not care which the format is called. Split below into the two that
    # matter — a description you wrote, or a file you already have — rather
    # than into one entry per format: a SCRIP grid *is* a NetCDF file, so a
    # separate SCRIP entry would list the same suffixes twice and take the
    # chooser back over four entries for no new reach.
    GRID: FileKind(
        GRID,
        (
            ("Grid Files (descriptions and data)",
             TEXT_EXTENSIONS + DATA_EXTENSIONS),
            ("Grid Description Files", TEXT_EXTENSIONS),
            ("SCRIP Grids & Data Files", DATA_EXTENSIONS),
            _ANY_ENTRY,
        ),
        "A grid: a CDO description file, a SCRIP grid in NetCDF, or any data "
        "file whose grid should be copied. A preset name needs no file.",
    ),

    # Every ASCII description and table CDO takes as a parameter. One kind
    # rather than one per operator, because the *chooser* cannot tell a z-axis
    # description from a parameter table and there is nothing to gain by
    # pretending it can — the parameter's own label and help say which is
    # wanted.
    TEXT: FileKind(
        TEXT,
        (
            ("Text / Table Files", TEXT_EXTENSIONS),
            _ANY_ENTRY,
        ),
        "A plain-text description or table. CDO requires no particular "
        "extension for these and its own examples use none.",
    ),

    # -- specific formats --------------------------------------------------

    JSON: FileKind(
        JSON,
        (
            ("JSON Files", (".json",)),
            _ANY_ENTRY,
        ),
        "A JSON table, in the format the CMOR library reads.",
    ),

    CPT: FileKind(
        CPT,
        (
            ("GMT Colour Palette Files", (".cpt",)),
            _ANY_ENTRY,
        ),
        "A GMT colour palette table, as written by makecpt.",
    ),

    # ``import_binary``'s input, and the reason an operator's *input* slot
    # needed a kind of its own: "cdo import_binary infile.ctl outfile", and
    # the .ctl file **is** infile. Offering NetCDF there was offering the one
    # thing the operator cannot take.
    CTL: FileKind(
        CTL,
        (
            ("GrADS Data Descriptors", (".ctl",)),
            _ANY_ENTRY,
        ),
        "A GrADS data descriptor: the ASCII .ctl file that describes the "
        "binary data, not the binary file itself.",
    ),

    # ``import_cmsaf``'s input. The manual's own example names a ``.hdf``
    # ("cdo -f nc remapbil,r360x180 -import_cmsaf cmsaf_product.hdf out.nc")
    # and its geolocation example a ``.h5``, so both are offered.
    HDF5: FileKind(
        HDF5,
        (
            ("HDF5 Files", (".h5", ".hdf", ".hdf5", ".he5")),
            _ANY_ENTRY,
        ),
        "A CM-SAF HDF5 product. Plain HDF5 is not otherwise a CDO input; "
        "this operator is the way in.",
    ),

    # What an unannotated slot gets. Deliberately one entry: if the manual
    # does not say what the file is, the honest chooser is one that hides
    # nothing rather than one that invents a suffix.
    ANY: FileKind(
        ANY,
        (_ANY_ENTRY,),
        "CDO's documentation does not say what format this file is in.",
    ),
}


# ---------------------------------------------------------------------------
# Building the strings Qt wants
# ---------------------------------------------------------------------------

def _entry(caption: str, extensions: Tuple[str, ...]) -> str:
    if not extensions:
        return f"{caption} (*)"
    return f"{caption} ({' '.join('*' + e for e in extensions)})"


def kind(key: str) -> FileKind:
    """The :class:`FileKind` for ``key``, falling back to :data:`ANY`.

    An unknown key is a schema entry naming a kind this module has not grown
    yet. It gets the chooser that hides nothing, because the alternative —
    raising — would take out the form for an operator that is otherwise fine.
    """
    return FILE_KINDS.get(key or ANY, FILE_KINDS[ANY])


def dialog_filter(key: str) -> str:
    """One ``;;``-joined QFileDialog filter string for the named kind."""
    return ";;".join(_entry(caption, exts) for caption, exts in kind(key).entries)


def extensions_for(key: str) -> Tuple[str, ...]:
    """Extensions to accept when scanning a folder for files of this kind."""
    return kind(key).extensions


def summary(key: str) -> str:
    """One sentence naming what CDO wants here, for a tooltip."""
    return kind(key).summary


#: What a printed reading is saved as. Plain text, since that is what every
#: ``nout == 0`` operator writes, and what every ``nin == 0`` operator reads on
#: standard input.
STDOUT_FILTER: str = ";;".join((
    _entry("Text / Table Files", TEXT_EXTENSIONS + (".csv", ".gmt")),
    _entry(*_ANY_ENTRY),
))

#: Where a run may write. The same formats as :data:`DATA` minus the ones CDO
#: only reads, since an output path ending in ``.cdf`` is silently written as
#: NetCDF4 rather than as what its name says.
OUTPUT_FILTER: str = ";;".join((
    _entry("NetCDF Files", (".nc", ".nc2", ".nc4", ".nc5")),
    _entry("GRIB Files", (".grb", ".grib")),
    _entry("GRIB2 Files", (".grb2", ".grib2")),
    _entry("SERVICE / EXTRA / IEG Files", MPIMET_EXTENSIONS),
    _entry(*_ANY_ENTRY),
))
