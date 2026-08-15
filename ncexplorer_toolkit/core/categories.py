import re
from dataclasses import dataclass
from enum import Enum
from typing import Container, Dict, List, Literal, Optional, Sequence, Tuple

# The file-format vocabulary ``file_kind`` indexes into. Imported rather than
# spelled as bare strings so a typo is an AttributeError here instead of an
# All-Files chooser three layers away. ``filetypes`` imports nothing from
# ``core``, which is what keeps this from being a cycle.
from . import filetypes as _ft

ParamKind = Literal["int", "float", "string", "bool", "file", "grid", "select",
                    "multiselect", "expression"]

#: How one parameter is spelled inside the operator token. See
#: :attr:`OperatorParam.form`.
ParamForm = Literal["positional", "keyword", "flag"]

#: Values a ``bool`` parameter accepts from a surface, normalised. CDO's own
#: parser is narrower than this — ``pack,printparam=yes`` is rejected with
#: "Boolean parameter >yes< contains invalid characters!" — so the point of
#: normalising is that a user may type any of these and still get a command CDO
#: accepts. Measured on 2.6.3: ``1``, ``0``, ``true`` and ``false`` are taken.
_BOOL_TRUE = frozenset({"true", "1", "yes", "on"})
_BOOL_FALSE = frozenset({"false", "0", "no", "off"})


@dataclass(frozen=True)
class OperatorParam:
    """Canonical description of a single trailing parameter of a CDO operator.

    ``kind`` is a widget hint used by the GUI:

    * ``int``/``float`` – numeric field
    * ``string``        – free-text line edit (default)
    * ``bool``          – a checkbox. Present because rendering one of these as
      a text box invites a value CDO rejects outright: ``pack,printparam=yes``
      is "Boolean parameter >yes< contains invalid characters!". The schema had
      no way to say "this is a switch" and there are eleven of them in the File
      operation section alone.
    * ``file``          – existing file path (browse button)
    * ``grid``          – grid descriptor: file path or CDO preset (``t63grid``, ``r360x180``)
    * ``select``        – one of ``choices``
    * ``multiselect``   – any number of ``choices``, in the order the user picks
      them, spelled as one comma-separated value. Exactly one parameter in the
      catalog is this: ``outputtab``'s ``keynames``, whose value *is* a chosen
      list of columns and whose order is the column order. It is a separate kind
      from ``select`` rather than a flag on it because the two need different
      widgets and different checks — ``select`` validates "is this the value",
      ``multiselect`` validates every item of a list independently, and each
      item may carry a ``:len`` suffix that ``select`` has no notion of.
      Rendered as one token, so :func:`parameter_tokens` needs no branch for it:
      ``keynames="name,date,value"`` already joins into
      ``outputtab,name,date,value``, which is the command CDO documents.
    * ``expression``    – the ``expr`` language: a line edit plus a button that
      opens ``gui/expression_editor.py``. For ``exprf``/``aexprf`` the value is
      still a file path; the editor writes the script and hands it back.

    ``form`` is how the value is spelled in the operator token, and it is not
    guessable from ``kind`` — CDO uses three different grammars, sometimes two
    of them within one module, and the only way to tell is to ask the binary:

    * ``positional`` – the value alone, in declaration order: ``gtc,273.15``,
      ``distgrid,2,3``, ``splitsel,10,0,0``. The default, and what every
      operator outside this section uses.
    * ``keyword``    – ``name=value``: ``bitrounding,inflevel=0.999``,
      ``collgrid,nx=3,gridtype=unstructured``, ``pack,printparam=true``. An
      unset optional keyword is simply absent, so these are order-independent
      and individually skippable in a way positional parameters are not.
    * ``flag``       – the bare name, no value: ``splitname,swap``. Exactly one
      parameter in the File operation section is spelled this way.

    Getting the form wrong is not always a failed command, which is why it is
    declared rather than assumed. ``cdo splitmon,format=%B infile mon`` exits 0
    and writes ``monformat=January.nc`` — Splittime's ``format`` is positional,
    so ``format=`` is taken as part of the strftime string and appears in every
    output filename. Measured on 2.6.3; ``cdo splitmon,%B`` is the correct call
    and writes ``monJanuary.nc``.

    ``writes`` applies to ``file`` parameters only, and says the operator
    *creates* the file this parameter names. Only two do — ``tee``'s
    ``outfile2`` and ``writeremapscrip``'s second parameter — and until it was
    declared the execution layer had no way to know, so it treated both as
    inputs. Everything that follows from being an output was therefore missing
    for them: the path was aliased as if it already existed (on Windows,
    ``symlink_to`` raises and the ``shutil.copy2`` fallback then fails on a
    source that is not there), it was never moved back from its alias to the
    path the user asked for, and a failed or cancelled run left it on disk
    while reporting nothing was written.

    It is a separate field from ``reads`` rather than its negation, because
    ``reads=False`` already means three different things and only one of them
    is "this is an output" — the ``setpartab*`` family is ``reads=False``
    because its value may be a built-in table *name*, which is neither read as a
    path nor written to.

    ``reads`` applies to ``file`` parameters only, and says the value must name
    a file that already exists — which is what lets the execution layer refuse a
    bad path in its own words instead of letting CDO fail on it later. It is not
    true of every ``file`` parameter, so it cannot be assumed from ``kind``:

    * ``tee``'s parameter is an output. ``cdo -h tee`` calls it "Destination
      filename for the copy of the input file", so requiring it to exist would
      refuse every correct use of the operator.
    * ``writeremapscrip``'s second parameter is the SCRIP file it writes.
    * the ``setpartab*`` family may take a built-in table *name* rather than a
      path. That one is marked ``reads=False`` because it could not be checked:
      the installed CDO answers "Help for setpartab in module Setpartab not
      found", so there is no way to confirm the name form from the binary, and
      refusing a call that might be valid is the worse error of the two.

    ``open_choices`` says ``choices`` is a vocabulary this parameter *offers*
    rather than the whole of what it accepts, so :func:`invalid_parameter_values`
    must not refuse a value outside it.

    It names a rule that already existed and was keyed on the wrong thing.
    ``grid`` and ``file`` have been exempt from the choices check since it was
    written — a grid may be a preset from :data:`GRID_PRESETS` *or* a descriptor
    file *or* any ``rNxM`` — but the exemption was spelled as a test on ``kind``,
    which cannot express the same situation arising for a kind that is usually
    closed. Two things in the Magics section are exactly that:

    * a genuinely open vocabulary. The Magplot page lists 54 colour names and
      then says a colour may instead be given "in RGB format", so the list is
      real and enforcing it would refuse a documented call.
    * a closed vocabulary whose **spelling could not be established**.
      ``colour_triad`` accepts two values and no more, but the manual's table
      writes them "CW"/"ACW" while the module's own worked example runs
      ``colour_triad=cw``. Enforcing either casing would refuse a call CDO's
      documentation prints as correct, and the usual tiebreaker — ask the
      binary — is unavailable: see the Magics section note in ``_PARAM_SPECS``
      for the two measurements that close it off. The dropdown still offers the
      documented spelling; what is dropped is the refusal, not the guidance.

    Setting it also refuses a **comma** in the value, which is the one thing
    still decidable once the vocabulary is open, and is not a style rule. CDO
    splits the operator token on commas to find its ``key=value`` pairs, so
    ``colour_min=RGB(1,0,0)`` does not reach Magics as a malformed colour — it
    reaches CDO as ``colour_min=RGB(1``, a parameter called ``0`` and a
    parameter called ``0)``. That is why the manual's own RGB notation uses
    semicolons, ``RGB(0.0;0.0;1.0)``, where every other RGB notation in
    computing uses commas.

    ``item_suffix`` says the items of a ``multiselect`` may each carry a
    ``:something`` tail, and it is declared per parameter because the tail is
    one operator's grammar rather than the kind's. ``outputtab``/``outputkey``
    take a field width — ``name:12`` — and that is the whole of it in this
    catalog. Left at its default, a colon is simply part of the value and fails
    the vocabulary check, which is what the binary does: ``cdo selseas,DJF:8``
    aborts, exit 1, measured on 2.6.3. Before this was declared the colon
    grammar was keyed on ``kind == "multiselect"``, so every list parameter
    inherited a suffix only one of them has, and ``DJF:8`` would have been
    passed through to a command that cannot run.

    Deliberately not applied to every keyword parameter, though a comma breaks
    the token for any of them in principle. Measured against the catalog: seven
    keyword parameters take a comma-separated value on purpose —
    ``intlevel,level=150,300,700`` and ``cmor,cmor_name=tas,pr`` among them —
    where CDO reads the trailing items as a continuation of the list. A blanket
    rule would refuse those correct calls, so the refusal is carried by the
    parameters that cannot survive a comma rather than by the grammar they share.

    ``file_kind`` applies to ``file`` and ``grid`` parameters and says what
    *format* the file is in, as a key into :data:`~.filetypes.FILE_KINDS`. It is
    a different question from ``kind``, which says only that the value is a path
    and so decides the widget. Two ``kind="file"`` parameters of one operator
    can want entirely unrelated things — ``remap`` takes a target grid and a
    **SCRIP NetCDF** weights file, ``remapeta`` takes an **ASCII** vertical
    coordinate table and a **data file** holding the orography — and with
    nothing to say so, every browse button in the app opened the same chooser,
    offering shapefiles, GeoTIFFs and KML to operators that cannot read any of
    them.

    The default is ``""``, which :func:`~.filetypes.kind` reads as "the manual
    does not say" and answers with an All-Files chooser. That is the honest
    default rather than a lax one: inventing an extension for an undocumented
    parameter hides the correct file behind a filter the user cannot see past.
    A ``grid`` parameter that leaves it blank is given the grid chooser by
    :func:`parameter_file_kind`, since that much *is* implied by the kind.
    """

    name: str
    kind: ParamKind = "string"
    label: str = ""
    placeholder: str = ""
    optional: bool = False
    choices: Tuple[str, ...] = ()
    help: str = ""
    reads: bool = True
    writes: bool = False
    open_choices: bool = False
    item_suffix: bool = False
    form: ParamForm = "positional"
    file_kind: str = ""


@dataclass(frozen=True)
class OperatorInput:
    """What one input slot of an operator actually has to hold.

    ``nin`` says how many files an operator takes. It does not say what they
    are, and for the climate indices that gap is dangerous: ``eca_cwfi`` will
    run against any second file whose grid matches and write plausible,
    entirely wrong numbers, because the file it needs is a 10th-percentile
    climatology and nothing checks.

    * ``role``   – the caption for the slot, in place of "Input 2".
    * ``field``  – the variable it must carry, in words.
    * ``recipe`` – the CDO command that builds it from the first input, as a
      template over ``{in1}`` and ``{n}``; empty when no recipe exists,
      which is itself worth saying (``eca_gsl``'s second file is a land-water
      mask, and ``eca_etr``'s is a second raw series).
    * ``units``  – key into :data:`UNIT_FAMILIES`, or ``""`` when the slot has
      no unit expectation worth checking.
    * ``key``    – a stable slug naming the field itself, so two operators
      wanting the same climatology are known to want the same file. It is what
      lets ``operator_lab`` build one set of companions and route every index
      to the right one, rather than guessing from the operator's name.
    * ``recipe_source`` – which slot the recipe's ``{in1}`` refers to.

    ``recipe_source`` exists because the derived file is not always the second
    one. Every ``*arith`` operator takes its data in slot 0 and a statistics
    file built from it in slot 1, so "build slot 1 from slot 0" was hard-coded
    in three places. Conditional selection runs the other way: ``ifthen`` takes
    the **mask** in slot 0 and the data in slot 1, and it is the mask that is
    derived — ``cdo gtc,0 data mask``. Left at the old assumption the recipe
    would have been quoted as building the mask *from the mask*, the lab would
    never have built one, and the model builder's button would have wired the
    new node into the data port. Default 0, which is what every previously
    declared slot meant.

    ``holds_variable`` says this slot is expected to carry the same physical
    quantity the rest of the call is about. True for almost every slot, and the
    assumption ``pairing.check_pairing``'s variable check is built on: for
    ``fldcor`` or ``eca_etr``, a second file that does not hold the named
    variable is usually the wrong file and worth saying so.

    It is False for a slot that carries a *coordinate* rather than data, and
    ``intlevel3d`` is why it exists. Its second input is the 3D vertical source
    coordinate — one variable named for a height or a pressure, never for the
    field being interpolated — so once the slot was declared, checking it for
    the data variable produced a warning on every correct call: "3D vertical
    source coordinate does not hold a variable called 'ta' — it holds zcoord".
    That is a true statement and a false alarm, and a check that fires on
    correct input is how a user learns to ignore it.

    ``shape`` names the *kind of field* the slot must hold, as a key into
    :data:`~.fieldshape.DETECTORS`. It is a different question from ``units``
    and from ``field``, and the Transformation section is why it exists: those
    two describe a quantity, and what ``sp2gp`` requires is not a quantity at
    all but a **representation** — spherical-harmonic coefficients rather than
    gridpoints. Nothing about a units string or a variable name can express
    that, and the consequence of getting it wrong is the worst kind in this
    catalog: measured on 2.6.3, ``cdo sp2gp lonlat.nc out.nc`` warns "No
    spectral data found!", **exits 0**, and copies the input through unchanged.
    A finished file on the original grid, reported as a success by every
    surface here.

    Empty for almost every slot, and that is the honest default: a slot with no
    ``shape`` is one whose representation has not been measured, not one that
    accepts anything. See ``core/fieldshape.py`` for why the check that reads
    this warns rather than blocks.

    ``file_kind`` names the *format* the slot's file is in, as a key into
    :data:`~.filetypes.FILE_KINDS`, and decides what the chooser offers when
    this input is browsed for. It defaults to ``"data"`` — GRIB, NetCDF or one
    of the local SERVICE/EXTRA/IEG formats — because that is what an operator
    input is in every section of the manual but one.

    Import/Export is the exception, and it is why the field exists on inputs
    and not only on parameters. ``cdo import_binary infile.ctl outfile``: the
    input is a GrADS ASCII *descriptor*, not a dataset, and the chooser that
    offered NetCDF and GRIB there was offering the two things the operator
    cannot take. ``import_cmsaf`` is the same shape with HDF5 — a format CDO
    reads nowhere else, which is what that operator is for.
    """

    role: str
    field: str
    recipe: str = ""
    units: str = ""
    key: str = ""
    recipe_source: int = 0
    holds_variable: bool = True
    shape: str = ""
    file_kind: str = "data"


@dataclass(frozen=True)
class OperatorEnv:
    """One environment variable that changes what an operator computes.

    Deliberately *not* an :class:`OperatorParam` with a new ``form``. A
    ``params`` tuple is positional — it becomes the ``op,a,b`` comma-token, and
    ``missing_required_parameters``, ``invalid_parameter_values``,
    ``file_parameter_indexes`` and the GUI's own ``extra_args`` collection all
    index into it by position. An environment variable is not an argument at
    all: it never appears on the command line, and putting one in that tuple
    would shift every index after it while also spelling ``eof,3,off`` at CDO,
    which is a syntax error.

    ``kind`` and ``choices`` are the same vocabulary :class:`OperatorParam`
    uses, so a surface can render one of these with the widget it already has
    for that kind — no new widget kind is introduced for them.

    * ``name``    – the variable, e.g. ``CDO_WEIGHT_MODE``.
    * ``default`` – what CDO does when it is unset, as a *measured* string.
      Shown as the placeholder, and the value that means "leave it alone":
      nothing is put in the environment unless the user chooses otherwise.
    * ``affects`` – which of the operator's outputs or behaviour it changes.
    """

    name: str
    kind: ParamKind = "string"
    label: str = ""
    default: str = ""
    choices: Tuple[str, ...] = ()
    help: str = ""
    affects: str = ""


@dataclass(frozen=True)
class OperatorOption:
    """One CDO *global* option that meaningfully changes what an operator does.

    A third kind of thing again, and deliberately neither an
    :class:`OperatorParam` nor an :class:`OperatorEnv`:

    * it is not a parameter — it never joins the ``op,a,b`` token, and putting
      one there would be a parse error;
    * it is not an environment variable — it is an argv token, and it goes
      *before* the operator name, which is the one position CDO accepts it in.

    The execution layer already has the slot for these: ``execute_operator``'s
    ``options`` list, which is passed through unvalidated on purpose (see
    ``_resolve_operator_call``). What was missing was any way for a surface to
    know *which* options matter for the operator in front of the user, so the
    GUI offered one free-text box captioned "CDO global options" and left them
    to the manual.

    That gap costs the Statistic section more than most, because one of these
    silently decides an output timestamp for roughly 200 operators.

    * ``name``     – the option as typed, e.g. ``--timestat_date``.
    * ``argument`` – a one-word description of its value, or "" for a bare
      flag. Bare is not guessable: ``-p`` takes no argument, and the
      ``true``/``false`` spelling that looks like it belongs to it is the
      *environment* variable ``CDO_ASYNC_READ``. ``cdo --async_read true …``
      is "Operator >true< not found!".
    * ``choices``  – the accepted values, when CDO validates them.
    * ``default``  – what CDO does when the option is absent, measured.
    """

    name: str
    argument: str = ""
    choices: Tuple[str, ...] = ()
    default: str = ""
    help: str = ""


@dataclass(frozen=True)
class OperatorOutput:
    """What one output slot of an operator actually holds.

    The mirror of :class:`OperatorInput`, and it exists for the same reason:
    ``nout`` says how many files come back and nothing about what they are. For
    the eleven ``(n|2)`` operators that gap is the whole difficulty. ``cdo eof``
    writes a *spectrum* to outfile1 and a stack of *maps* to outfile2 — two
    different kinds of thing, on two different grids, from one command — and
    with nothing to say so both output rows captioned "Output File 1" / "Output
    File 2", which is the same dangerous kind of true as "Input 1"/"Input 2" on
    ``ifthen``.

    * ``role``     – the caption for the slot, in place of "Output File 2".
    * ``field``    – what it holds, in words: the sentence that has to survive
      the trip to the toolbar, the palette and the model builder.
    * ``drawable`` – whether the map canvas can render it. False is the
      interesting value: outfile1 of every eof operator is a 1x1 grid with one
      timestep per eigenvalue, and the canvas draws a single degenerate cell
      from it rather than refusing the file — the same trap ``reducegrid``
      carries and states.
    * ``suffix``   – what to append to the stem when suggesting a filename for
      this slot, so two outputs of one node cannot be proposed the same path.
    * ``media``    – what *kind of file* this is: ``"field"`` for the dataset
      every other operator writes, ``"image"`` for a rendered picture.

    ``media`` is a different axis from ``drawable`` and was added because the
    Magics section made the difference matter. ``drawable=False`` says "this is
    a dataset the map canvas would render misleadingly" — outfile1 of an eof,
    which is a spectrum drawn as one degenerate cell. It presumes the file is a
    dataset at all. What ``contour``, ``shaded``, ``grfill``, ``vector``,
    ``stream`` and ``graph`` write is a PostScript, PNG or SVG *picture*, and
    there is no reading of ``drawable`` that says so: False would mean "a
    dataset not worth drawing", which is wrong in a way that matters, because
    the code downstream of it still opens the file with the NetCDF reader. It
    only ever asks whether to put the result on the canvas or in the plot dock,
    and both answers are wrong for a ``.ps``.

    ``"field"`` is the default and is what all 943 catalog entries except those
    six mean, so nothing that existed before this field has to say anything.
    See :func:`writes_images`, which is what surfaces should ask rather than
    reaching in here, and ``nc_integration``'s
    ``_MISSING_FEATURE_ABORTS``/``missing_build_feature`` for why a run of one
    of the six will not normally get far enough to produce one on this build.

    Deliberately not given a ``recipe``: an output is produced by the run, not
    built beforehand, so the field would have nothing to hold.
    """

    role: str
    field: str
    drawable: bool = True
    suffix: str = ""
    media: Literal["field", "image"] = "field"


@dataclass(frozen=True)
class OperatorSpec:
    """Full metadata for one CDO operator, assembled from the Reference Card."""

    name: str
    nin: int
    nout: int
    category: "NCExplorerCategory"
    params: Tuple[OperatorParam, ...] = ()
    description: str = ""
    inputs: Tuple[OperatorInput, ...] = ()
    outputs: Tuple[OperatorOutput, ...] = ()
    env: Tuple[OperatorEnv, ...] = ()


class NCExplorerCategory(Enum):
    """Categories for cdo operators based on the reference card"""
    INFORMATION = "Information"
    FILE_OPERATIONS = "File operations"
    SELECTION = "Selection"
    CONDITIONAL_SELECTION = "Conditional selection"
    COMPARISON = "Comparison"
    MODIFICATION = "Modification"
    ARITHMETIC = "Arithmetic"
    STATISTICAL_VALUES = "Statistical values"
    #: The four operators of CDO's Correlation section. Between the statistics
    #: and the regression, which is where the reference manual puts the section
    #: and what it is: a statistic over two fields rather than one, and the
    #: thing a user reaches for just before they reach for a trend.
    #:
    #: A category of its own rather than four more entries under Statistical
    #: values, because the prefix cascade in ``_infer_category`` was filing them
    #: there on "fld"/"tim" — the same mechanism, and the same complaint, as the
    #: 32 Arithmetic operators and the 12 Comparison ones recorded above.
    #: ``_MODULE_CATEGORY`` names the four module titles instead, so the
    #: grouping is CDO's own and cannot drift from the binary.
    CORRELATION = "Correlation"
    #: The eight operators of CDO's EOFs section, in the two modules the binary
    #: reports: "Empirical Orthogonal Functions" (eof, eoftime, eofspatial,
    #: eof3d, eof3dtime, eof3dspatial) and "Principal coefficients of EOFs"
    #: (eofcoeff, eofcoeff3d).
    #:
    #: All eight were landing in Miscellaneous — no ``_MODULE_CATEGORY`` entry
    #: named either module, and ``_infer_category``'s prefix cascade has no
    #: branch an "eof" name matches, so they fell all the way through. CDO's own
    #: reference manual gives EOFs a section of its own, which is the same
    #: argument that earned Comparison and Correlation theirs: the app has no
    #: better claim about what belongs together than the tool it is a front end
    #: for. Named by module, not by the ``eof`` prefix — patching the cascade is
    #: the documented trap this file has now recorded four times.
    #:
    #: Miscellaneous was the alternative and is wrong on its own terms: it is
    #: where operators go when the binary *will not place them* (``harmonic``,
    #: ``lic``, ``ncopy`` — all three absent from ``CDO_OPERATOR_MODULES``).
    #: These eight are placed, by two unambiguous module titles, and a category
    #: that means "CDO could not say" should not hold operators CDO was
    #: perfectly clear about.
    EOF = "EOFs"
    REGRESSION = "Regression"
    INTERPOLATION = "Interpolation"
    TRANSFORMATION = "Transformation"
    #: CDO's Import/Export section, and the replacement for the category that
    #: used to be spelled ``FORMATTED_IO = "Formatted I/O"``.
    #:
    #: It is a rename rather than a second category, and the reason is that
    #: "Formatted I/O" was never CDO's name for anything. The 2.6.3 reference
    #: manual has one section here, titled Import/Export, holding six modules:
    #: Importbinary, Importcmsaf, Input, Output, Outputgmt and Outputtab. The old
    #: category named only the middle two of those six — the ``input*`` and
    #: ``output*`` families — so the section's other eleven documented operators
    #: were filed elsewhere: ``import_binary``/``import_cmsaf`` in Miscellaneous,
    #: and ``outputtab``/``gmtxyz``/``gmtcells`` in Information.
    #:
    #: Keeping both was the alternative and is rejected. Two categories over one
    #: manual section needs a rule for which operator falls on which side, and
    #: the manual draws no such line — so the rule would have to be invented
    #: here, as a fifth hand-kept list of operator names, which is the exact
    #: thing this file has already recorded four times as the mistake. There is
    #: no operator that belongs to "Formatted I/O" and not to Import/Export.
    #:
    #: Named by module in ``_MODULE_CATEGORY`` rather than by an operator list.
    #: That moves 28 operators, not the 13 the manual documents: the binary puts
    #: eleven more in the same six modules (``outputarr``, ``outputfld``,
    #: ``outputts``, ``outputxyz``, ``outputkml``, ``outputvrml``, ``outputtri``,
    #: ``outputvector``, ``outputcenter2`` and the two ``*cpt`` variants) plus
    #: the four aliases. That is the point rather than a side effect — the eleven
    #: are exporters sitting in Information today, and Information is where a
    #: user looks for ``info`` and ``sinfo``, not for a GMT writer.
    IMPORT_EXPORT = "Import/Export"
    #: CDO's "Graphic with Magics" section: the six operators of the Magplot,
    #: Magvector and Maggraph modules — contour, shaded, grfill, vector, stream
    #: and graph.
    #:
    #: The fifth time this file has been the fix rather than the cascade, and by
    #: the same mechanism as the EOFs: ``_MODULE_CATEGORY`` named none of the
    #: three module titles and ``_infer_category``'s prefix cascade has no
    #: branch a "contour" or a "grfill" matches, so all six fell all the way
    #: through to Miscellaneous. Registered by module title below, not by an
    #: operator list — the rule this file has now stated for Comparison,
    #: Correlation, EOFs and Import/Export.
    #:
    #: Miscellaneous was wrong on the same terms it was wrong for the EOFs: it
    #: is where operators go when the binary *will not place them*, and CDO
    #: places all six unambiguously. It is also wrong on a second count these
    #: six have to themselves — what they produce is not a dataset. Every other
    #: category in this enum groups operators by what they compute *from* a
    #: field *into* a field; these six end a chain and hand back a picture, and
    #: the manual says so in as many words ("These operators can be used as
    #: terminal operators"). A user looking for a plot is not browsing
    #: Miscellaneous for it.
    #:
    #: Named GRAPHICS rather than PLOTTING to match CDO's own section title,
    #: which is "Graphic with Magics". The displayed string drops "with Magics":
    #: Magics is the library CDO links against, and naming it in a menu makes
    #: the user's ability to find the operators depend on their knowing that.
    #: The dependency is real and is surfaced where it matters instead — see
    #: ``nc_integration.missing_build_feature``, which says a build without
    #: MAGICS cannot run these before the run rather than after it.
    GRAPHICS = "Graphics"
    MISCELLANEOUS = "Miscellaneous"
    ECA_INDICES = "ECA indices"

# ---------------------------------------------------------------------------
# Where OPERATOR_SIGNATURES went
#
# A hand-maintained ``{operator: (nin, nout)}`` table stood here: 716 entries
# against the catalog's 943, and it is the reason four operators of the
# Correlation section and twelve of the Comparison section could not be run
# from the operator panel at all.
#
# The mechanism, because it is the argument for deleting rather than extending
# it. Three places in ``gui/main_window.py`` read this table with a ``(1, 1)``
# default — the form builder, ``parse_parameters`` and ``execute_operation``.
# An operator absent from it therefore got one "Input File" row, was validated
# against one input, and reached the execution layer with one path, which
# assembled ``cdo timcor in.nc out.nc`` and got back "cdo (Abort): Missing
# inputs". Measured across the whole catalog: **38 operators** that the schema
# knows take two or more inputs were missing here and silently read as (1, 1) —
# timcor, timcovar, varrms, timrmsd, difftest, replace, recttocomplex,
# varquot2test, wct, subtrend, the whole yday/year/yhour/yseas arithmetic
# families, and all twelve ymon/yseas comparison operators. Not one entry that
# was *present* disagreed with the catalog, which is what made this invisible:
# the table was never wrong, only absent, and absence reads as a default.
#
# ``eq``/``ne``/``le``/``lt``/``ge``/``gt`` were listed and so worked, which is
# why half the Comparison category being unreachable went unnoticed for as long
# as it did.
#
# There is no replacement table. ``OPERATOR_SCHEMA`` already carries ``nin`` and
# ``nout`` for all 943, taken from ``cdo --operators`` by way of
# ``cdo_operator_catalog``, and every surface now reads it through
# :func:`get_operator_spec`. The one caller that had a real use for a plain
# ``{name: (nin, nout)}`` mapping — ``nc_integration``'s fallback for when the
# binary cannot be probed — builds it from the schema in one comprehension, so
# even the fallback is derived.
#
# This is the same judgement the file has already recorded three times, in the
# note above ``COMPARISON_OPERATORS``, in ``_infer_category``'s two deleted
# branches and above ``_MODULE_CATEGORY``: two lists of one thing disagree
# eventually. This one had 227 entries' worth of disagreement and a docstring in
# ``_build_operator_schema`` still describing it as an input that it had already
# stopped being.
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# The Comparison section, all twenty-four of it
#
# CDO's Comparison section is four modules, not two. The twelve names below that
# start ``ymon``/``yseas`` were filed under Statistical values, because
# ``_MODULE_CATEGORY`` named no comparison module and the prefix cascade in
# ``_infer_category`` tests ("ymon","yseas") before it tests any comparison
# name — the same trap that cost the Arithmetic section thirty-two operators,
# and the same fix: name the module, do not patch the cascade.
#
# This tuple is the *only* list of the twenty-four. It used to be spelled twice
# — here as twelve names, and again as a name set inside ``_infer_category``
# that also claimed ``maxc``/``minc`` — and the two disagreed in both
# directions. One name, one place: ``CATEGORY_FOR_OPERATOR`` is built from this
# tuple and is the first thing ``_infer_category`` consults, so a name here is
# the answer and the cascade below it never gets a say.
#
# ``maxc``/``minc`` are deliberately absent. They read like comparisons and CDO
# files them under Arithc, because they return a *value* where these twenty-four
# return a 0/1 mask. See the judgement calls above ``_MODULE_CATEGORY``.
COMPARISON_OPERATORS: Tuple[str, ...] = (
    # Comp — cdo <op> infile1 infile2 outfile
    'eq', 'ne', 'le', 'lt', 'ge', 'gt',
    # Compc — cdo <op>,c infile outfile
    'eqc', 'nec', 'lec', 'ltc', 'gec', 'gtc',
    # Ymoncomp — infile2 is a Ymonstat file, not a second series
    'ymoneq', 'ymonne', 'ymonle', 'ymonlt', 'ymonge', 'ymongt',
    # Yseascomp — infile2 is a Yseasstat file. Broken in CDO 2.6.0; see
    # _SURPRISING_DEFAULTS for the evidence and what the user is told.
    'yseaseq', 'yseasne', 'yseasle', 'yseaslt', 'yseasge', 'yseasgt',
)


# Complete operator mapping
OPERATOR_CATEGORIES = {
    NCExplorerCategory.INFORMATION: [
        'info', 'infov', 'map', 'sinfo', 'sinfov', 'diff', 'diffv', 'npar',
        'nlevel', 'nyear', 'nmon', 'ndate', 'ntime', 'showformat', 'showcode',
        'showname', 'showstdname', 'showlevel', 'showltype', 'showyear',
        'showmon', 'showdate', 'showtime', 'griddes', 'vct'
    ],
    # Ten, on the same grounds as the Arithmetic list below: ``menu_operators``
    # sorts this list and the toolbar shows the first ``TOP_LEVEL_LIMIT`` of it,
    # so sixteen names was not a shortlist but an alphabetical accident. The old
    # sixteen surfaced ``cat copy merge mergetime replace splitcode splitday
    # splitgrid splithour splitlevel`` — six of the fifteen split operators, and
    # neither ``splitname`` nor ``splitmon`` nor ``splitsel``, which are the
    # three anyone actually reaches for. Everything dropped is one click away
    # under "All File operations", grouped by its CDO module.
    #
    # What earned a slot, against 37 operators in the category:
    #   copy cat      the two whole-file writes, and the pair that has to be
    #                 seen together: copy replaces, cat appends, and the second
    #                 is the only operator in CDO that does not start from
    #                 nothing. `cdo -f nc copy` is also the format converter,
    #                 which is most users' first CDO command of all
    #   merge         different variables, same timesteps
    #   mergetime     same variables, different timesteps — the 30-yearly-files
    #                 job, and the operator users confuse with merge, which is
    #                 the argument for both being here rather than either
    #   replace       swap one variable for another in place; the only edit in
    #                 the section that changes a file's contents rather than its
    #                 shape or its packing
    #   tee           keep an intermediate without breaking the chain. It earns
    #                 its place from the model builder, where saving a
    #                 mid-pipeline result is the whole reason to draw a graph
    #   splitname     split by variable — the split whose output filenames a
    #                 human can read
    #   splitmon      split by month, and the only Splittime operator that takes
    #                 a format
    #   splityear     split by year; the archiving idiom, and the inverse of the
    #                 mergetime job above
    #   splitsel      split every N timesteps — the only split not keyed on
    #                 something in the metadata
    NCExplorerCategory.FILE_OPERATIONS: [
        'copy', 'cat', 'merge', 'mergetime', 'replace', 'tee',
        'splitname', 'splitmon', 'splityear', 'splitsel',
    ],
    NCExplorerCategory.SELECTION: [
        'selcode', 'delcode', 'selname', 'delname', 'selstdname', 'sellevel',
        'selgrid', 'selgridname', 'selzaxis', 'selzaxisname', 'selltype',
        'seltabnum', 'seltimestep', 'seltime', 'selhour', 'selday', 'selmon',
        'selyear', 'selseas', 'seldate', 'selsmon', 'sellonlatbox', 'selindexbox'
    ],
    # ``reducegrid`` is deliberately not here. The curated list is the shortlist
    # the menu shows before "All Conditional selection", and these five are the
    # family a user comes to this category looking for: they take a mask and
    # give back the same grid. ``reducegrid`` takes a mask and gives back an
    # *unstructured* grid that this app cannot draw (see its description), which
    # makes it the last of the six to reach for rather than one of the first
    # five. It is one click away under the All submenu, which is where the
    # category change put it.
    NCExplorerCategory.CONDITIONAL_SELECTION: [
        'ifthen', 'ifnotthen', 'ifthenelse', 'ifthenc', 'ifnotthenc'
    ],
    NCExplorerCategory.COMPARISON: list(COMPARISON_OPERATORS),
    NCExplorerCategory.MODIFICATION: [
        'setpartab', 'setcode', 'setname', 'setlevel', 'setltype', 'setdate',
        'settime', 'setday', 'setmon', 'setyear', 'settunits', 'settaxis',
        'setreftime', 'setcalendar', 'shifttime', 'chcode', 'chname',
        'chlevel', 'chlevelc', 'chlevelv', 'setgrid', 'setgridtype',
        'setzaxis', 'setattribute', 'invertlat', 'invertlon',
        'invertlatdes', 'invertlondes', 'invertlatdata', 'invertlondata',
        'maskregion', 'masklonlatbox', 'maskindexbox', 'setclonlatbox',
        'setcindexbox', 'enlarge', 'setmissval', 'setctomiss', 'setmisstoc',
        'setrtomiss'
    ],
    # Ten, and exactly ten, because ``menu_operators`` sorts this list and the
    # toolbar shows the first ``TOP_LEVEL_LIMIT`` of it — so a curated list
    # longer than ten is not a shortlist, it is an alphabetical accident. The
    # thirty-five names that used to be here surfaced ``abs acos add addc asin
    # atan atan2 cos div divc``: four trigonometric functions, and neither
    # ``sub`` nor ``mul`` nor ``expr``. Everything dropped is still one click
    # away under "All Arithmetic", grouped by its CDO module.
    #
    # What earned a slot, against 78 operators in the category:
    #   expr                  the escape hatch — anything the other 77 cannot do
    #   add sub mul div       the two-file core of the section
    #   addc mulc             constant arithmetic; unit conversion is the most
    #                         common real task in this app, and K→°C is
    #                         addc,-273.15 (see UNIT_FAMILIES for why that
    #                         particular one is worth reaching quickly)
    #   ymonsub               the anomaly idiom, cdo ymonsub in -ymonavg in out;
    #                         CDO ships `anomaly` as an alias for it, which is
    #                         as clear a statement of its standing as exists
    #   sqrt abs              the two Math functions that get used on fields,
    #                         rather than the fourteen that mostly do not
    NCExplorerCategory.ARITHMETIC: [
        'expr', 'add', 'sub', 'mul', 'div', 'addc', 'mulc', 'ymonsub',
        'sqrt', 'abs',
    ],
    # 138 names, and unlike the Arithmetic list above this one is deliberately
    # not a ten-slot shortlist: the category holds 287 operators, the menu shows
    # this list grouped by module with everything else under "All Statistical
    # values", and the pattern here is min/max/sum/mean/avg/var/std/pctl
    # repeated across each temporal and spatial family. Dropping it to ten would
    # mean choosing between families rather than within them.
    #
    # Three names were added to it, and the omissions are a curation call rather
    # than a bug — every operator in the category is reachable under the All
    # submenu, the command palette and the model builder:
    #
    #   timcumsum    the running total over time. Nothing else in the section
    #                does it, so a user who wants it has no near neighbour to
    #                find it beside; it is not a variant of the eight-fold
    #                pattern above and is invisible unless listed.
    #   fldmedian    the median is what people mean by "typical value" on a
    #                skewed field, and reaching it via fldpctl,pn=50 requires
    #                knowing both that it is the same thing and that Fldstat's
    #                pn is spelled with the keyword form.
    #   yearmonmean  the day-weighted yearly mean. It earns a slot for the
    #                reason its help text exists: ``yearmean`` is sitting right
    #                there in this list, is the arithmetic mean, and is the
    #                wrong operator for monthly input. Listing only the wrong
    #                one is how the mistake keeps being made.
    #
    # Left out on purpose, with the reason, so the next pass does not re-open
    # each one from scratch:
    #
    #   *std1 *var1        the n-1 normalisation. One per family would add ~30
    #                      names to distinguish a divisor; the pairing is
    #                      explained in the help text of both members instead.
    #   fldint fldcount    neither returns the input's quantity — fldint
    #   timminidx          multiplies by cell area, fldcount is a count,
    #   timmaxidx          the *idx pair returns a timestep index. They are
    #                      listed in the section's help notes as the four
    #                      shapes that break a units-derived colorbar, which is
    #                      a better place to meet them than a menu.
    #   timrange           max-min, one step from timmax and timmin which are
    #                      both here.
    #   timyearmean        the timyear* pair is the rarer of the two weighted
    #                      means; yearmonmean covers the common case.
    NCExplorerCategory.STATISTICAL_VALUES: [
        'timcumsum', 'fldmedian', 'yearmonmean',
        'ensmin', 'ensmax', 'enssum', 'ensmean', 'ensavg', 'ensvar', 'ensstd',
        'enspctl', 'fldmin', 'fldmax', 'fldsum', 'fldmean', 'fldavg',
        'fldvar', 'fldstd', 'fldpctl', 'zonmin', 'zonmax', 'zonsum',
        'zonmean', 'zonavg', 'zonvar', 'zonstd', 'zonpctl', 'mermin',
        'mermax', 'mersum', 'mermean', 'meravg', 'mervar', 'merstd',
        'merpctl', 'vertmin', 'vertmax', 'vertsum', 'vertmean', 'vertavg',
        'vertvar', 'vertstd', 'timselmin', 'timselmax', 'timselsum',
        'timselmean', 'timselavg', 'timselvar', 'timselstd', 'timselpctl',
        'runmin', 'runmax', 'runsum', 'runmean', 'runavg', 'runvar',
        'runstd', 'runpctl', 'timmin', 'timmax', 'timsum', 'timmean',
        'timavg', 'timvar', 'timstd', 'timpctl', 'hourmin', 'hourmax',
        'hoursum', 'hourmean', 'houravg', 'hourvar', 'hourstd', 'hourpctl',
        'daymin', 'daymax', 'daysum', 'daymean', 'dayavg', 'dayvar',
        'daystd', 'daypctl', 'monmin', 'monmax', 'monsum', 'monmean',
        'monavg', 'monvar', 'monstd', 'monpctl', 'yearmin', 'yearmax',
        'yearsum', 'yearmean', 'yearavg', 'yearvar', 'yearstd', 'yearpctl',
        'seasmin', 'seasmax', 'seassum', 'seasmean', 'seasavg', 'seasvar',
        'seasstd', 'seaspctl', 'ydaymin', 'ydaymax', 'ydaysum', 'ydaymean',
        'ydayavg', 'ydayvar', 'ydaystd', 'ydaypctl', 'ymonmin', 'ymonmax',
        'ymonsum', 'ymonmean', 'ymonavg', 'ymonvar', 'ymonstd', 'ymonpctl',
        'yseasmin', 'yseasmax', 'yseassum', 'yseasmean', 'yseasavg',
        'yseasvar', 'yseasstd', 'yseaspctl', 'ydrunmin', 'ydrunmax',
        'ydrunsum', 'ydrunmean', 'ydrunavg', 'ydrunvar', 'ydrunstd',
        'ydrunpctl'
    ],
    # All four, because the category is four. There is no shortlist to make
    # when the curated list and "All Correlation" would name the same
    # operators, and a category whose menu is complete is one a user can trust
    # they have seen the whole of.
    #
    # ``varrms``, ``fldrms`` and ``timrmsd`` are deliberately absent, and the
    # evidence is the same evidence that keeps ``harmonic``, ``lic`` and
    # ``ncopy`` in Miscellaneous: the binary will not place them. All three are
    # missing from ``CDO_OPERATOR_MODULES`` entirely and ``cdo -h varrms`` on
    # 2.6.3 answers "No help available for this operator!", so there is no
    # module title to key on and no documentation to read. They are two-input
    # (2|1) operators that measure agreement between two fields, so they *read*
    # like members of this section — which is exactly the reading this table
    # exists to refuse. They stay where the cascade puts them today —
    # ``fldrms`` and ``timrmsd`` under Statistical values on their "fld"/"tim"
    # prefix, ``varrms`` in Miscellaneous, having no prefix that matches
    # anything — and that the three of them land in two different categories is
    # itself the point: a cascade over names has no opinion worth honouring
    # here. If a later CDO documents them, the module lookup moves all three
    # with no change to this list.
    NCExplorerCategory.CORRELATION: [
        'fldcor', 'fldcovar', 'timcor', 'timcovar',
    ],
    # All five, on the same grounds as the four of Correlation above: the
    # curated list and "All Regression" would name the same operators, so there
    # is no shortlist to make, and a category whose menu is complete is one a
    # user can trust they have seen the whole of.
    #
    # ``regres`` and ``addtrend`` were the two missing, and they are not an
    # arbitrary pair. ``regres`` is ``trend``'s slope on its own and ``addtrend``
    # is ``subtrend`` run the other way, so what the old list of three offered
    # was one half of each of the two pairs in the section — reachable only
    # through ``menu_operators``' ``rest``, under a submenu a user has no reason
    # to open when the three names they expect are already in front of them.
    NCExplorerCategory.REGRESSION: [
        'detrend', 'regres', 'trend', 'addtrend', 'subtrend'
    ],
    NCExplorerCategory.INTERPOLATION: [
        'remapbil', 'remapbic', 'remapcon', 'remapdis', 'genbil', 'genbic',
        'gencon', 'gendis', 'remap', 'intgridbil', 'remapeta',
        'ml2pl', 'ml2hl', 'inttime', 'intntime', 'intyear'
    ],
    # Eleven. ``dv2ps`` was reachable only by search — it arrives in this
    # category through ``_infer_category``'s ``dv2`` prefix — and it belongs in
    # the browsable list: it is documented (the Wind2 module), it takes exactly
    # the same input as ``dv2uv`` beside it (spectral sd/svo, declared as one
    # shared slot in ``_OPERATOR_INPUTS``), and it is the third member of the
    # trio a user browsing this section is looking for. It costs ``uv2dvl`` its
    # place in the toolbar's first ten — the list is sorted and TOP_LEVEL_LIMIT
    # is 10 — and that is the right one to lose: it is the linear shorthand for
    # ``uv2dv``, which stays, and it is one click away under "All …".
    #
    # ``fourier`` is deliberately **not** added, though it reaches this category
    # the same way. Its every documented use needs ``-f nc4``, which none of the
    # three surfaces can emit (see ``_GLOBAL_OPTION_USERS``), so on this build
    # every click from a browsable list ends at "This operator needs fields with
    # complex numbers!" or at CDI's classic-format refusal. Advertising it as a
    # headline act of the section would be advertising a dead end. It stays
    # searchable, which is what it is today, and it should be added the moment
    # the operator form grows an output-format control.
    NCExplorerCategory.TRANSFORMATION: [
        'sp2gp', 'sp2gpl', 'gp2sp', 'gp2spl', 'sp2sp', 'spcut', 'dv2uv',
        'dv2uvl', 'dv2ps', 'uv2dv', 'uv2dvl'
    ],
    # Nine, on the same grounds as the File operations and Arithmetic lists
    # above: ``menu_operators`` sorts this list and the toolbar shows the first
    # ``TOP_LEVEL_LIMIT`` of it, so a curated list longer than ten is an
    # alphabetical accident rather than a shortlist. The eight names that used
    # to be here were the whole of the old Formatted I/O category, which is not
    # a shortlist either — it was the category — and sorted it surfaced
    # ``input inputext inputsrv output outputext outputf outputint outputsrv``:
    # both rarely-used ASCII header formats twice over, and neither import
    # operator, ``outputtab`` nor ``gmtxyz``, none of which were in this
    # category to be surfaced. Everything dropped is one click away under "All
    # Import/Export", grouped by its CDO module.
    #
    # What earned a slot, against 28 operators in the category:
    #   import_binary   read raw binary through a GrADS .ctl. The one operator
    #                   here that gets data *in* from outside CDO's own formats,
    #                   and the section's headline act
    #   import_cmsaf    the other way in: CM-SAF HDF5. Needs a CDO built with
    #                   HDF5, which is stated on the operator rather than
    #                   discovered from a failed run — see _SURPRISING_DEFAULTS
    #   output          the plain ASCII dump, and the operator whose numbers
    #                   ``input`` reads back, so the pair has to be seen together
    #   input           the inverse, and the only way into this app for numbers
    #                   that are not already in a file CDO can open
    #   outputf         the same dump with a format the user chooses; the one
    #                   people actually reach for when ``output``'s "%13.6g" is
    #                   not what they want
    #   outputtab       the tabular export, and the operator this whole category
    #                   exists to make usable — a chosen set of columns, which
    #                   is what anyone taking CDO data into a spreadsheet or
    #                   pandas wants. It is also the only one with a real
    #                   parameter grammar (see _OUTPUTTAB_KEYNAMES)
    #   gmtxyz gmtcells the two GMT writers, and a pair for the same reason
    #                   copy/cat are one: xyz is points for pscontour, cells is
    #                   polygons for psxy, and choosing wrongly produces a plot
    #                   rather than an error
    #   outputint       rounded integers; the cheap way to eyeball a mask or a
    #                   count field without reading six significant digits
    #
    # ``inputsrv``/``inputext``/``outputsrv``/``outputext`` are deliberately
    # absent: they are the SERVICE and EXTRA ASCII header formats, which matter
    # only to someone already holding a file in one of them, and they crowd out
    # the operators a user comes to this category looking for.
    NCExplorerCategory.IMPORT_EXPORT: [
        'import_binary', 'import_cmsaf', 'input', 'output', 'outputf',
        'outputint', 'outputtab', 'gmtxyz', 'gmtcells',
    ],
    # Ten, on the same grounds as the File operation and Import/Export lists
    # above: ``menu_operators`` sorts this list, the toolbar shows the first
    # ``TOP_LEVEL_LIMIT`` of ``curated + rest``, and everything else is one
    # click away under "All Miscellaneous", grouped by CDO module. So a curated
    # list longer than ten does not surface more operators — it just decides
    # which ten by the alphabet.
    #
    # The old list was nineteen, and the alphabet chose badly from it. What the
    # toolbar actually showed was:
    #
    #   const fdns gradsdes histcount histfreq histmean histsum hurr mastrfu
    #   random
    #
    # — four spellings of one histogram, two wind-index thresholds, and no
    # smooth, no gridarea, no sethalo, no setvals. Nine of the nineteen were
    # there to hold their operator in the section at all rather than to
    # recommend it (see the Histogram / Temporal sorting / Generate a field
    # entries in ``_MODULE_CATEGORY``, which now do that job properly), so
    # cutting them costs nothing: every name dropped below is still in the
    # category and still in the menu.
    #
    # What earned a slot, against 63 operators in the category:
    #   gridarea      cell areas — the file every area-weighted statistic needs
    #                 and the one thing in this section a map application
    #                 cannot do without
    #   smooth        the smoother, and the operator this whole pass fixed: six
    #                 real keyword parameters where there was one free-text box
    #   smooth9       its fixed 9-point sibling, here for the reason copy/cat
    #                 are both in the File operation list — the choice between
    #                 them is the question, so showing one alone hides it
    #   sethalo       grid bounds; the operator for a cyclic grid that needs a
    #                 wrap column before it will interpolate or plot cleanly
    #   setvals       replace listed values — the readable half of Replacevalues
    #   setrtoc       replace a *range* with a constant, which is the half
    #                 people actually reach for when clamping a field
    #   topo          global topography on any grid, and the quickest way to get
    #                 a real field onto a new map
    #   random        a test field from nothing
    #   const         a constant field from nothing; with random, the pair that
    #                 makes every other operator in the app explorable without
    #                 owning data yet
    #   deltat        differences between consecutive timesteps
    #
    # ``uv2vr_cfd`` and ``uv2dv_cfd`` follow and are **not** shortlist picks.
    # They are here because this list is the only thing ``_infer_category``
    # consults before the prefix cascade, and the cascade files them under
    # Transformation on "uv2" while CDO documents them in Miscellaneous. Their
    # module title cannot be used — three unrelated modules share it, see the
    # note in ``_MODULE_CATEGORY`` — so a name is the only evidence available.
    # Both sort last alphabetically and so do not displace any of the ten.
    NCExplorerCategory.MISCELLANEOUS: [
        'gridarea', 'smooth', 'smooth9', 'sethalo', 'setvals', 'setrtoc',
        'topo', 'random', 'const', 'deltat',
        # Placement only; see above.
        'uv2vr_cfd', 'uv2dv_cfd',
    ],
    NCExplorerCategory.ECA_INDICES: [
        'eca_cdd', 'eca_cfd', 'eca_csu', 'eca_cwd', 'eca_cwdi', 'eca_cwfi',
        'eca_etr', 'eca_fd', 'eca_gsl', 'eca_hd', 'eca_hwdi', 'eca_hwfi',
        'eca_id', 'eca_r10mm', 'eca_r20mm', 'eca_r75p', 'eca_r75ptot',
        'eca_r90p', 'eca_r90ptot', 'eca_r95p', 'eca_r95ptot', 'eca_r99p',
        'eca_r99ptot', 'eca_rr1', 'eca_rx1day', 'eca_rx5day', 'eca_sdii',
        'eca_su', 'eca_tg10p', 'eca_tg90p', 'eca_tn10p', 'eca_tn90p',
        'eca_tr', 'eca_tx10p', 'eca_tx90p'
    ]
}

# Reverse mapping for a quick lookup
CATEGORY_FOR_OPERATOR = {}
for category, operators in OPERATOR_CATEGORIES.items():
    for op in operators:
        CATEGORY_FOR_OPERATOR[op] = category


# ---------------------------------------------------------------------------
# Unified operator schema (single source of truth for GUI + execution layer)
#
# ``_PARAM_SPECS`` lists the trailing, non-file parameters every operator
# accepts, matched against the CDO Reference Card (v1.0.8, June 2007) and
# cross-checked with the User's Guide.  Optional parameters are flagged so the
# GUI can label them and the integration layer can strip trailing empty values
# before building the ``cdo op,a,b,,`` token.
# ---------------------------------------------------------------------------

# Grid descriptor presets understood by CDO (subset – enough for quick picks).
#
# The two Gaussian families are here because the Interpolation section's manual
# illustrates Remap, Remapbil, Remapbic and Remapcon with F32 in every example,
# and neither family was offered: a user following the CDO documentation had to
# type the grid the docs use by hand.
#
# Every one of the eight was run rather than read off the manual, as
# ``cdo remapbil,<preset> lev5.nc out.nc`` followed by ``cdo sinfon`` on 2.6.3
# (18x9 lonlat source). All eight were accepted, and the two families are
# genuinely different grids rather than two spellings of one:
#
#   F16  -> gaussian          points=2048   (64x32)
#   F32  -> gaussian          points=8192   (128x64)
#   F64  -> gaussian          points=32768  (256x128)
#   F128 -> gaussian          points=131072 (512x256)
#   n16  -> gaussian_reduced  points=1688   nlat=32
#   n32  -> gaussian_reduced  points=6114   nlat=64
#   n64  -> gaussian_reduced  points=23112  nlat=128
#   n128 -> gaussian_reduced  points=88838  nlat=256
#
# So F<N> is the *regular* Gaussian grid, 4N x 2N; n<N> is the *reduced*
# Gaussian grid with the same 2N latitudes but fewer points per row. Picking
# n32 when F32 was meant is a quarter of the gridpoints, and both run.
#
# Deliberately no "F32grid"/"n32grid": unlike the spectral t<N>grid presets those
# do not exist, and both were measured aborting with "Open failed on F32grid!" —
# CDO takes the unknown name as a filename.
GRID_PRESETS: Tuple[str, ...] = (
    "t21grid", "t31grid", "t42grid", "t63grid", "t85grid", "t106grid",
    "t159grid", "t255grid", "r72x36", "r180x90", "r360x180", "r720x360",
    "r1440x720", "global_1", "global_0.5",
    "F16", "F32", "F64", "F128", "n16", "n32", "n64", "n128",
)

_INT = "int"
_FLOAT = "float"
_STR = "string"
_BOOL = "bool"
_FILE = "file"
_GRID = "grid"
_SELECT = "select"
_MULTISELECT = "multiselect"
_EXPR = "expression"

_POSITIONAL = "positional"
_KEYWORD = "keyword"
_FLAG = "flag"


def _p(name: str,
       kind: ParamKind = _STR,
       label: str = "",
       placeholder: str = "",
       optional: bool = False,
       choices: Tuple[str, ...] = (),
       help: str = "",
       reads: bool = True,
       form: ParamForm = _POSITIONAL,
       writes: bool = False,
       open_choices: bool = False,
       item_suffix: bool = False,
       file_kind: str = "") -> OperatorParam:
    return OperatorParam(
        name=name,
        kind=kind,
        label=label or name,
        placeholder=placeholder,
        optional=optional,
        choices=choices,
        help=help,
        reads=reads,
        writes=writes,
        form=form,
        open_choices=open_choices,
        item_suffix=item_suffix,
        file_kind=file_kind,
    )


def _kw(name: str,
        kind: ParamKind = _STR,
        label: str = "",
        placeholder: str = "",
        optional: bool = True,
        choices: Tuple[str, ...] = (),
        help: str = "",
        reads: bool = True,
        open_choices: bool = False,
        file_kind: str = "") -> OperatorParam:
    """A ``name=value`` parameter. Optional by default, as nearly all of them are.

    Sugar over :func:`_p`, because the File operation section declares
    twenty-four keyword parameters and spelling ``form=_KEYWORD`` on each of
    them buries the one thing each line is actually saying.
    """
    return _p(name, kind, label, placeholder, optional, choices, help, reads,
              form=_KEYWORD, open_choices=open_choices, file_kind=file_kind)


# Shared by the six ETCCDI bootstrapping indices. ``m`` switches the output
# from yearly to monthly and is the only optional one.
_ETCCDI_BOOTSTRAP_PARAMS: Tuple[OperatorParam, ...] = (
    _p("n", _INT, "n", "window width in days"),
    _p("startboot", _INT, "startboot", "first year of the reference period"),
    _p("endboot", _INT, "endboot", "last year of the reference period"),
    _p("freq", _STR, "freq", "m for monthly output", optional=True,
       choices=("m",)),
)

# ``etccdi_r95p`` / ``etccdi_r99p``: no running window, and this build makes the
# frequency argument mandatory rather than optional.
_ETCCDI_PRECIP_PARAMS: Tuple[OperatorParam, ...] = (
    _p("startboot", _INT, "startboot", "first year of the reference period"),
    _p("endboot", _INT, "endboot", "last year of the reference period"),
    _p("freq", _STR, "freq", "m for monthly output", choices=("m",)),
)


# The two parameters CDO's Split module documents, shared by the nine operators
# it applies to. ``swap`` is the only flag-form parameter in the schema: it
# takes no value and swaps obase and xxx in each output filename, measured as
# ``cdo splitname,swap infile out`` writing ``randomout.nc`` rather than
# ``outrandom.nc``.
#
# ``splitrec`` is in this module and is deliberately *not* given these. It is
# undocumented, and the binary is unambiguous about it: ``cdo splitrec,swap``
# is "Too many arguments! Need 0 found 1." Declaring the module's parameters on
# it because it shares the module would put two fields in front of the user
# that can only produce a failed run.
_SPLIT_PARAMS: Tuple[OperatorParam, ...] = (
    _p("swap", _BOOL, "swap obase and xxx", optional=True, form=_FLAG,
       help="Write <xxx><obase><suffix> instead of <obase><xxx><suffix>."),
    _kw("uuid", _STR, "uuid attribute", "global attribute name",
        help="Add a UUID to each output file as the named global attribute."),
)


# The one parameter the whole Regression section takes, shared by all five of
# its operators the way ``_ETCCDI_BOOTSTRAP_PARAMS`` is shared by six: detrend,
# regres, trend, addtrend and subtrend each document exactly ``equal : BOOL``
# and nothing else.
#
# **Keyword form, and this is the fifth grammar surprise this file records.**
# The manual's synopsis is ``detrend[,equal]``, which reads as a positional
# value or a bare flag and is neither. Measured on 2.6.3, all four wrong
# spellings::
#
#     cdo detrend,true      in.nc o.nc  -> missing '=' in key/value string:
#                                          >true<  ... (Abort): Parse error!
#     cdo detrend,equal     in.nc o.nc  -> missing '=' in key/value string:
#                                          >equal<  ... (Abort): Parse error!
#     cdo regres,true       in.nc o.nc  -> (Abort): Parse error!
#     cdo detrend,equal=yes in.nc o.nc  -> (Abort): Boolean parameter >yes<
#                                          contains invalid characters!
#
# and the spellings that run::
#
#     cdo detrend,equal=false in.nc o.nc  -> exit 0
#     cdo detrend,equal=true  in.nc o.nc  -> exit 0
#     cdo detrend,equal=1     in.nc o.nc  -> exit 0
#     cdo detrend,equal=0     in.nc o.nc  -> exit 0
#
# So the value must be spelled ``equal=<bool>``, and CDO's own bool reader
# accepts only true/false/1/0 — which is exactly what ``parameter_tokens``
# normalises a ``_BOOL`` keyword to, so a surface may still offer "yes".
# ``cdo detrend,equal=`` aborts cleanly with "Missing value for parameter key
# >equal<!" rather than hanging, so a half-filled field is not the trap it is
# for the operators recorded above.
#
# Optional, because it has a default and the default is usually right for
# daily data. It is usually *wrong* for this application's data, which is what
# the help text and ``_REGRESSION_EQUAL_NOTE`` are for.
_REGRESSION_PARAMS: Tuple[OperatorParam, ...] = (
    _kw("equal", _BOOL, "equal — timesteps are evenly spaced",
        optional=True,
        help="Leave on for daily or hourly data. Turn it OFF when the time "
             "axis is monthly or yearly, or when the series has gaps: months "
             "are 28–31 days long and years 365 or 366, and left on CDO "
             "counts every timestep as one unit of t regardless. It changes "
             "the answer rather than the run — measured on twelve monthly "
             "steps, cdo regres gave a slope of 1.0 and cdo regres,equal=false "
             "gave 1.01672 on the same file."),
)


# ---------------------------------------------------------------------------
# The climate indices — ``eca_*`` and ``etccdi_*``
#
# Every shape below was read off ``cdo -h <operator>`` on the installed CDO
# 2.6.0 *and* run, because the two do not always agree; where they differ the
# binary wins and the entry carries a comment saying so.
#
# Two grammars live side by side in this module and must not be conflated:
#
# * the ``freq``/``params``/``parameter`` slot of the ECA indices is a
#   **key=value** string — ``freq=month``.  A bare ``m`` or ``month`` gets
#   "Argument parse error!" (or "missing '=' in key/value string").
# * the trailing ``m`` of the ETCCDI bootstrapping indices above is a
#   **positional** argument and really is a bare ``m``; ``freq=month`` there
#   gets "Integer parameter >m< contains invalid character".
#
# The two are declared apart for that reason, and ``_ECA_FREQ`` is the one to
# reuse anywhere a new index grows a frequency.
# ---------------------------------------------------------------------------

#: The temperature trap, spelled out wherever a threshold is typed. The docs
#: are explicit: the field is read as Kelvin, the threshold is written in
#: degrees Celsius, and CDO converts neither.
_CELSIUS_HELP = ("In degrees Celsius, while the input field is read as Kelvin. "
                 "CDO converts neither, so a field in °C gives wrong counts "
                 "rather than an error.")

#: The precipitation trap. ``eca_pd``'s own documentation says it outright.
_MM_HELP = ("In mm (equivalently kg m-2). A field carrying a rate in mm/s must "
            "be multiplied by 86400 first, or every count comes out near zero.")

# ---------------------------------------------------------------------------
# The Compc constant, and why the units check cannot reach it
#
# CDO's own example is ``cdo gtc,273.15 infile outfile`` — a freezing point in
# Kelvin. Against a field already in °C that comparison is true everywhere, and
# the result is a well-formed mask of ones rather than an error: the same shape
# of failure ``eca_su`` has, and the reason ``core/units.py`` exists.
#
# ``units.check_inputs`` cannot be extended to cover it, and the reason is
# structural rather than a missing table entry. Every :data:`UNIT_FAMILIES`
# entry is matched against a *file's* ``units`` attribute, by walking the
# operator's input slots; ``same_as_input1`` compares one file against another,
# which is what makes it work for ``eca_etr``. A Compc constant is not a file
# and has no units attribute — it is a bare number typed into a box, so there is
# nothing to normalise and nothing to disagree with. The check would have to
# compare a *parameter* against an input file, which is a different mechanism
# from the one that exists, not a wider version of it.
#
# So the fact goes where the number is typed. Both surfaces that render a
# parameter already show ``OperatorParam.help`` as a tooltip and ``placeholder``
# in the empty field — the operator form via ``_parameter_help`` and the model
# builder in ``_build_parameter_rows`` — so stating it here reaches both without
# a line of GUI code. No conversion is offered, because CDO converts neither
# operand and a GUI that quietly converted one would be lying about what ran.
# ---------------------------------------------------------------------------
_CONSTANT_UNITS_HELP = (
    "In the units of the input field itself — CDO compares the raw stored "
    "values and converts neither operand. CDO's own example is gtc,273.15, "
    "which is a freezing point in Kelvin; against a field already in °C it is "
    "true everywhere and returns an all-ones mask rather than an error. Check "
    "the field's units before trusting the result."
)

#: The one ``c`` the six Compc operators share. One object rather than six
#: identical ones, so the help cannot be improved for ``gtc`` and left stale on
#: ``lec`` — the same reason ``_ECA_FREQ`` is a single shared parameter.
_COMPC_C: OperatorParam = _p(
    "c", _FLOAT, "c", "constant, in the field's own units",
    help=_CONSTANT_UNITS_HELP,
)

# A closed list rather than free text, because there are exactly two legal
# values and every near-miss is an abort: 'm' and 'month' get "Argument parse
# error!", 'freq=day' gets "Frequency 'day' unknown". A user cannot be expected
# to guess the key=value form from a placeholder.
_ECA_FREQ: OperatorParam = _p(
    "freq", _SELECT, "freq", "freq=year (default)", optional=True,
    choices=("freq=year", "freq=month"),
    help="Length of the output period, as a key=value pair. Only 'year' and "
         "'month' exist — 'freq=day' gets \"Frequency 'day' unknown\".",
)

# ``[,R[,N[,params]]]``. ``N`` only controls the *second* output variable, the
# count of qualifying periods, so changing it leaves the headline index alone.
_ECA_CDD_PARAMS: Tuple[OperatorParam, ...] = (
    _p("R", _FLOAT, "R", "R=1 mm (default)", optional=True,
       help="A day is dry when precipitation is below R. " + _MM_HELP),
    _p("N", _INT, "N", "N=5 days (default)", optional=True,
       help="Counts dry spells longer than N days into the second output "
            "variable; the index itself does not depend on it."),
    _ECA_FREQ,
)

# ``cdo -h eca_cwd`` (and the 2.6.3 reference) print ``[,params]`` alone, but
# the binary parses the first two arguments positionally exactly as eca_cdd
# does: ``eca_cwd,R=1`` aborts with "Float parameter >R=1< contains invalid
# character at position 1", while ``eca_cwd,1,5,freq=month`` is accepted and
# does produce monthly output. Declared to match the binary.
_ECA_CWD_PARAMS: Tuple[OperatorParam, ...] = (
    _p("R", _FLOAT, "R", "R=1 mm (default)", optional=True,
       help="A day is wet when precipitation is at least R. " + _MM_HELP),
    _p("N", _INT, "N", "N=5 days (default)", optional=True,
       help="Counts wet spells longer than N days into the second output "
            "variable; the index itself does not depend on it."),
    _ECA_FREQ,
)

#: ``eca_su``/``etccdi_su``: ``[,T[,params]]``, T in °C against a Kelvin field.
_ECA_SU_PARAMS: Tuple[OperatorParam, ...] = (
    _p("T", _FLOAT, "T", "T=25 °C (default)", optional=True,
       help="Counts days with a maximum above T. " + _CELSIUS_HELP),
    _ECA_FREQ,
)

#: ``eca_tr``/``etccdi_tr``: the same shape with a different default.
_ECA_TR_PARAMS: Tuple[OperatorParam, ...] = (
    _p("T", _FLOAT, "T", "T=20 °C (default)", optional=True,
       help="Counts nights with a minimum above T. " + _CELSIUS_HELP),
    _ECA_FREQ,
)

#: ``eca_hd``/``etccdi_hd``: ``[,T1[,T2]]``; T2 defaults to T1.
_ECA_HD_PARAMS: Tuple[OperatorParam, ...] = (
    _p("T1", _FLOAT, "T1", "T1=17 °C (default)", optional=True,
       help="Reference temperature the daily mean is subtracted from. "
            + _CELSIUS_HELP),
    _p("T2", _FLOAT, "T2", "T2=T1 (default)", optional=True,
       help="Only days with a mean below T2 contribute. " + _CELSIUS_HELP),
)

#: ``eca_cwdi``/``eca_hwdi``: ``[,nday[,T]]`` against a climatological mean.
_ECA_WAVE_PARAMS: Tuple[OperatorParam, ...] = (
    _p("nday", _INT, "nday", "nday=6 days (default)", optional=True,
       help="Shortest run of qualifying days that counts as a wave."),
    _p("T", _FLOAT, "T", "T=5 °C (default)", optional=True,
       help="How far a day must sit from the reference-period mean. "
            "A temperature *difference*, so °C and K agree on its size — but "
            "both input files must be in the same units as each other."),
)

#: ``eca_cwfi``/``etccdi_csdi``: ``[,nday[,params]]``.
_ECA_SPELL_PARAMS: Tuple[OperatorParam, ...] = (
    _p("nday", _INT, "nday", "nday=6 days (default)", optional=True,
       help="Shortest run of qualifying days that counts as a spell."),
    _ECA_FREQ,
)

#: ``eca_gsl``/``etccdi_gsl``: ``[,nday[,T[,fland]]]``.
_ECA_GSL_PARAMS: Tuple[OperatorParam, ...] = (
    _p("nday", _INT, "nday", "nday=6 days (default)", optional=True,
       help="Consecutive days on the right side of T that open or close the "
            "growing season."),
    _p("T", _FLOAT, "T", "T=5 °C (default)", optional=True,
       help="Daily-mean threshold the season is defined by. " + _CELSIUS_HELP),
    _p("fland", _FLOAT, "fland", "fland=0.5 (default)", optional=True,
       help="Smallest land fraction in the second input's mask for a cell to "
            "be treated as land."),
)

#: ``eca_rx5day``/``etccdi_rx5day``: ``[,x[,params]]``.
_ECA_RX5DAY_PARAMS: Tuple[OperatorParam, ...] = (
    _p("x", _FLOAT, "x", "x=50 mm (default)", optional=True,
       help="Five-day totals above x are counted into the second output "
            "variable; the index itself does not depend on it. " + _MM_HELP),
    _ECA_FREQ,
)

#: ``eca_rr1``/``eca_sdii`` and their ETCCDI twins: ``[,R]`` alone.
_ECA_WETDAY_PARAMS: Tuple[OperatorParam, ...] = (
    _p("R", _FLOAT, "R", "R=1 mm (default)", optional=True,
       help="A day counts as wet at or above R. " + _MM_HELP),
)

#: The whole of ``[,freq]`` / ``[,parameter]``, for the indices that take
#: nothing else.
_ECA_FREQ_ONLY: Tuple[OperatorParam, ...] = (_ECA_FREQ,)


# ---------------------------------------------------------------------------
# outputtab's keynames — the eighteen, and why they are checked
#
# ``outputtab`` was declared as a free-text ``keys`` string, which put the one
# operator in this section with a real grammar behind the one widget that
# cannot express any of it. The manual defines exactly eighteen keynames and
# nothing else is accepted; the format of each is ``keyname[:len]``.
#
# The names below are read straight off ``cdo --help outputtab`` on the
# installed 2.6.3, in the order its table prints them, which is also a sensible
# order to offer them in. ``nohead`` is last in that table and is the odd one:
# it is not a column at all but a switch that suppresses the header line
# (measured — ``outputtab,nohead,value`` prints the values with no ``#`` row).
#
# Three things were measured about how CDO answers a bad one, and the third is
# the reason this is validated in the app rather than left to the binary:
#
#   outputtab,bogus      -> exit 1, "Key >bogus< unsupported!". A clean abort;
#                           the app could have relied on it.
#   outputtab,value:abc  -> exit 134 (SIGABRT), "libc++abi: terminating due to
#   outputtab,value:     -> uncaught exception of type std::invalid_argument:
#                           stoi: no conversion". Not an abort — an uncaught
#                           C++ exception. CDO dies on a signal, writes nothing
#                           a caller can parse, and on the async path arrives as
#                           a crash rather than a failed run.
#   outputtab,value:8.5  -> exit 0. stoi reads the leading "8" and stops, so a
#                           float is tolerated rather than rejected.
#
# So the ``:len`` rule below is exactly stoi's contract — after the colon there
# must be a leading integer — rather than a stricter guess. It refuses ``abc``
# and the empty string, which are the two spellings that crash, and accepts
# ``0``, ``-3`` and ``8.5``, which are the ones measured to work even though
# only one of the three is sensible. Refusing a value CDO accepts is the worse
# error of the two everywhere else in this file; here it would also be
# unnecessary, because the crash set is decidable.
_OUTPUTTAB_KEYNAME_CHOICES: Tuple[str, ...] = (
    "value", "name", "param", "code", "x", "y", "lon", "lat", "lev",
    "xind", "yind", "timestep", "date", "time", "year", "month", "day",
    "nohead",
)

_OUTPUTTAB_KEYNAMES: OperatorParam = _p(
    "keynames", _MULTISELECT, "keynames", "e.g. date,time,lon,lat,value",
    choices=_OUTPUTTAB_KEYNAME_CHOICES, item_suffix=True,
    help="One keyname per column of the table, in column order. Each may carry "
         "an optional :len giving the field width, e.g. name:12. Only the "
         "eighteen names CDO documents are accepted; 'nohead' is not a column "
         "but a switch that suppresses the header line.",
)


# ---------------------------------------------------------------------------
# The Miscellaneous section's shared parameters
# ---------------------------------------------------------------------------

#: The wind-speed trap, shared by strwin/strbre/strgal/hurr. All four count
#: days whose *daily maximum horizontal wind speed* passes a threshold, and
#: that field is not usually one a model writes: it is sqrt(u^2+v^2), built
#: from the two components. The recipe lives on the input slot; this is the
#: half that belongs where the number is typed.
_WIND_THRESHOLD_HELP = (
    "In m/s, against a field of daily maximum horizontal wind speed. CDO does "
    "not convert, so a field in km/h gives wrong counts rather than an error."
)

#: ``histcount,bounds`` and its three siblings. One parameter holding the whole
#: comma-separated list, because the number of bins is the user's choice and
#: n separate declared parameters cannot express n-1 bins.
_HIST_BOUNDS: OperatorParam = _p(
    "bounds", _STR, "bounds", "e.g. 0,10,20,30 or -inf,0,inf",
    help="Comma-separated bin bounds, lowest first; n bounds make n-1 bins. "
         "-inf and inf are accepted and are how the manual writes an open "
         "first or last bin.",
)

#: The four parameters ``uv2vr_cfd`` and ``uv2dv_cfd`` share. One object rather
#: than two copies, for the reason ``_COMPC_C`` is one: the two operators are
#: documented by a single synopsis and differ only in what they compute.
#:
#: ``boundOpt`` and ``outMode`` have closed value sets, taken from the manual —
#: the binary aborts on "u not found!" before it validates either, so neither
#: list could be confirmed the way the *grammar* was.
_UV_CFD_PARAMS: Tuple[OperatorParam, ...] = (
    _kw("u", _STR, "u", "name of the U variable",
        help="Zonal wind component, by variable name. Keyword form despite "
             "the manual's synopsis: u,v positionally is \"Parse error!\"."),
    _kw("v", _STR, "v", "name of the V variable",
        help="Meridional wind component, by variable name."),
    _kw("boundOpt", _SELECT, "boundary option", "0-3",
        choices=("0", "1", "2", "3"),
        help="How the longitude boundaries are treated in the centered "
             "finite differences. From the manual; the binary aborts on a "
             "missing variable before validating it."),
    _kw("outMode", _SELECT, "output mode", "new or append",
        choices=("new", "append"),
        help="Whether the result replaces the input variables or is appended "
             "alongside them."),
)


# ---------------------------------------------------------------------------
# The Statistic section's keyword parameters
#
# Every one of these was ``params=[]`` before, and every one is spelled
# ``name=value`` — measured, because the section mixes all three grammars and in
# two modules mixes two of them inside one operator token. The probes are quoted
# per group below; the general shape is that the Statistic section's *keyword*
# operators reject a positional value outright, with the parser's own words:
#
#   cdo fldmean,90 v.nc o.nc      -> missing '=' in key/value string: >90<
#   cdo monmean,TRUE v.nc o.nc    -> missing '=' in key/value string: >TRUE<
#
# so getting the form wrong here is a failed run rather than a silent one. The
# expensive mistakes in this section are elsewhere, and they are called out at
# the entries that carry them: ``fldpctl`` (a wrong *answer* on exit 0) and the
# four Vertstat operators that accept a key they never read.

#: Fldstat's area weighting. Default TRUE, which is why the checkbox matters:
#: ``fldmean`` is an *area-weighted* mean by default, and on a lonlat grid that
#: is a different number from the arithmetic one, not a rounding difference.
#: Measured on 2.6.3 against an 18x9 topo field:
#:
#:     cdo fldmean v.nc o.nc                 -> -2380.5625
#:     cdo fldmean,weights=FALSE v.nc o.nc   -> -1877.0123
_FLD_WEIGHTS: OperatorParam = _kw(
    "weights", _BOOL, "area weighting", "TRUE",
    help="Weight each cell by its area (the default). Turn this off for the "
         "plain arithmetic mean over cells. On a lonlat grid the two differ "
         "substantially — measured on an 18x9 field, fldmean gives -2380.56 "
         "weighted and -1877.01 unweighted. Applies to the mean/avg/var/std "
         "family; fldskew and fldkurt are not weighted either way.",
)

#: Fldstat's per-field report. Prints one line per timestep to stdout — the
#: Date/Time/Name/Level/Cell/Lon/Lat/value table — and does not change the
#: output file. Measured: ``cdo fldmin,verbose=TRUE`` prints
#: "2000-01-01 12:00:00  seq  0  106  300  22.5  -5864.7".
#:
#: Keyword, not a flag, and that is the whole reason it is declared rather than
#: assumed: ``cdo fldmean,verbose`` is
#: "missing '=' in key/value string: >verbose<". The File operation section has
#: a genuine bare-name flag (``splitname,swap``), so the two spellings coexist
#: in this schema and neither can be guessed from the other.
_FLD_VERBOSE: OperatorParam = _kw(
    "verbose", _BOOL, "verbose", "FALSE",
    help="Print the location of the result — cell index, longitude and "
         "latitude — for each timestep, to stdout. Does not change the output "
         "file. Spelled verbose=TRUE; a bare 'verbose' is a parse error.",
)

#: The pair every Fldstat operator takes **except ``fldpctl``**. Both keywords,
#: both optional, and CDO accepts them together in either order:
#: ``fldmean,weights=FALSE,verbose=TRUE`` and the reverse both exit 0.
#:
#: Fldstat validates its keys — ``cdo fldmean,banana=42`` is
#: "Invalid parameter key >banana<!" — so a typo here is a failed run and not a
#: silently ignored setting. That is *not* true of four Vertstat operators; see
#: ``_VERT_WEIGHTS``.
_FLDSTAT_PARAMS: Tuple[OperatorParam, ...] = (_FLD_WEIGHTS, _FLD_VERBOSE)

#: Vertstat's layer-thickness weighting. The same spelling as Fldstat's and a
#: different meaning — thickness, not area — which is why it is a separate
#: object with its own help rather than a reuse of ``_FLD_WEIGHTS``.
#:
#: Two measurements decide how this is declared:
#:
#: * It only does anything when the file carries layer bounds. Without them CDO
#:   warns "Layer bounds not available, using constant vertical weights for
#:   variable P!" and weighted and unweighted agree. With them they do not:
#:   after ``cdo genlevelbounds``, ``vertmean`` gives 760.8173 by default and
#:   840.2465 at weights=FALSE. ``check_vertical_weights`` in core/units.py says
#:   so before the run.
#: * Vertstat rejects ``verbose`` — "Invalid parameter key >verbose<!" — so the
#:   Fldstat pair is deliberately not reused here. One key, not two.
_VERT_WEIGHTS: Tuple[OperatorParam, ...] = (
    _kw("weights", _BOOL, "layer weighting", "TRUE",
        help="Weight each level by its layer thickness (the default). Only "
             "has an effect when the file carries layer bounds: without them "
             "CDO warns 'Layer bounds not available' and uses constant "
             "weights, so the setting is inert. Run genlevelbounds first if "
             "the weighting is what you are after. Unlike Fldstat, Vertstat "
             "takes no verbose key."),
)

#: Monstat/Daystat/Yearstat/Timstat/Hourstat's completeness switch.
#:
#: The brief this work came from named mon/day/year. Measured, ``tim*`` and
#: ``hour*`` take it too — all five modules — and the ones that look like they
#: should and do not are worth naming, because "Too many arguments! Need 0
#: found 1" is what you get for guessing:
#:
#:     monmean daymean yearmean timmean hourmean      complete_only=TRUE -> ok
#:     seasmean ymonmean yearmonmean                  -> Need 0 found 1
#:     ydaymean                                       -> Invalid parameter key
#:     timselmean         -> Integer parameter >complete_only=TRUE< invalid
#:
#: Seasstat is the omission that costs something, and it is CDO's rather than
#: this schema's: an incomplete first or last season is exactly the case a user
#: would reach for this to fix. ``check_season_completeness`` in core/units.py
#: says so instead.
_COMPLETE_ONLY: Tuple[OperatorParam, ...] = (
    _kw("complete_only", _BOOL, "complete periods only", "FALSE",
        help="Write a result only for periods that are fully covered by the "
             "input, instead of computing one from a partial period. Turn "
             "this on when the series starts or ends mid-period and a "
             "half-month mean would be read as a whole one."),
)

#: ``ydrun*``'s repeated-missing mode. Takes one value and only one:
#: ``rm=n`` and ``rm=x`` are both "Parameter rm must only contain 'c'!", so the
#: choices tuple is the binary's own answer rather than a transcription.
#:
#: Keyword, following a *positional* ``nts`` — this and ``ydrunpctl`` are the
#: only mixed-grammar operators in the section. ``cdo ydrunmean,5,rm=c`` is the
#: call; ``cdo ydrunmean,5,c`` is "missing '=' in key/value string: >c<".
_YDRUN_RM: OperatorParam = _kw(
    "rm", _SELECT, "repeated missing", "c",
    choices=("", "c"),
    help="Set to c to have repeated missing values end the running window "
         "rather than be carried through it. The only value CDO accepts — "
         "rm=n is 'Parameter rm must only contain c!'.",
)

#: Shared by all seventeen percentile operators. The name is ``pn`` because
#: that is what CDO calls it; see the block comment on the percentile entries
#: for which modules take it positionally and which take ``pn=``.
_PCTL_HELP = (
    "The percentile to compute, 0–100. Read as a percentile and not a "
    "fraction: pn=90 is the 90th percentile, pn=0 is the minimum and pn=100 "
    "the maximum. The default estimator bins the data into CDO_PCTL_NBINS "
    "bins (101), so a percentile is approximate at that resolution unless the "
    "operator offers pm=r8."
)

#: Remapstat's target grid. The second sentence is the module page's own
#: recommendation and the reason this help exists: a target cell with no source
#: point in it gets the missing value, which on a coarse-to-fine remap is most
#: of the field, and the result is a mostly-empty map rather than an error.
_REMAPSTAT_HELP = (
    "Target grid, as a grid description file or a CDO preset such as r360x180. "
    "Each target cell takes the statistic of the source points that fall "
    "inside it. A target cell containing no source point is written as "
    "missing, so remapping onto a finer grid than the input leaves holes — the "
    "module's own advice is to wrap the call as "
    "'cdo setmisstonn -remapmean,<grid> infile outfile' to fill them from the "
    "nearest neighbour."
)

#: ``ydrun*``'s window, in days. The truncation is the part users are surprised
#: by, so it is stated on the parameter rather than only in the run warning.
_YDRUN_NTS_HELP = (
    "Running window in days, centred on each day of the year. Must be odd. "
    "The output is shorter than the input at both ends: it starts (nts-1)/2 "
    "steps in and ends (nts-1)/2 steps early, so nts=5 loses two days at each "
    "end. Needs a continuous daily series — gaps are not detected."
)


# ---------------------------------------------------------------------------
# Graphics with Magics — the six plotting operators
#
# THE SOURCE OF EVERY CLAIM BELOW, AND WHY IT MATTERS MORE HERE THAN ANYWHERE
# ELSE IN THIS FILE.
#
# The *shipped* CDO cannot run these: it answers ``cdo --config has-magics``
# with "no" and all six abort with "MAGICS support not compiled in!". So this
# section was first written entirely from the CDO 2.6.3 reference manual and
# ``cdo -h <operator>``, and every claim was labelled unverified.
#
# **Most of it has since been verified**, against a CDO 2.6.3 built from source
# against Homebrew's magics 4.16.0 (x86_64, to match the Rosetta Homebrew this
# machine's libraries come from). Where a claim below says "measured", that is
# the build it was measured on; where it still says the manual is the only
# source, that is still true. The findings that changed the code are recorded
# at the parameters they belong to; three are worth having in one place:
#
#   * **The casing question is answered: Magics is case-insensitive.**
#     ``stat=true``/``stat=TRUE``, ``style=dash``/``DASH`` and
#     ``colour_triad=cw``/``CW`` each produce byte-identical output. See
#     :data:`_MAGICS_BOOL_CASE_RISK`, which is now a record rather than a risk.
#   * **An invalid parameter value fails silently.** This is the important one.
#     ``colour_triad=bogus`` and ``style=bogus`` print "Invalid parameter
#     specification 'colour_triad=bogus'" on stderr, **exit 0**, and write **no
#     file at all**. Not a non-zero exit, not a partial file — a successful run
#     that produced nothing. That is why the enum ``choices`` below are enforced
#     rather than offered: this application refusing a bad value is the only
#     thing between the user and a silent no-op.
#   * **Maggraph's output name is not what the manual says.** See
#     :func:`expected_plot_files`.
#
# Measuring any of this needs one trick, recorded because the naive method
# lies: PostScript output carries a ``%%CreationDate:`` line, so two identical
# runs differ. Strip that one line and the output is exactly reproducible —
# which is what makes "did this parameter do anything" a decidable question.
#
# Two facts about the *shipped* binary remain true and still bound what a user
# on the default build sees:
#
#   * **The MAGICS gate fires before any parameter is parsed.**
#     ``cdo shaded,totallybogus=1``, ``cdo shaded,device`` (no ``=`` at all)
#     and ``cdo shaded,device=png`` all give the identical abort. It also means
#     the blank-parameter hang this application guards against elsewhere cannot
#     occur for these six on that build — the gate precedes the prompt.
#   * **The parameter vocabulary is not in that binary at all.** ``strings``
#     finds none of ``colour_triad``, ``thin_fac``, ``unit_vec``,
#     ``file_split``, ``colour_min`` or ``chain_dash``. The Magics code is
#     elided at compile time rather than stubbed, which is why a rebuild rather
#     than a plugin was needed to answer any of the above.
#
# Where the manual's own tables are clipped in the PDFs under
# ``CDO_Documentation/Graphics with Magics/`` (the print-to-PDF cuts the
# Description column mid-sentence), the tail was recovered from ``cdo -h``,
# which prints the same table wrapped instead of clipped. Every such recovery
# is noted at its parameter with "recovered from ``cdo -h``". Nothing below is
# a guess at a missing tail.
#
# FOUR PLACES THE MANUAL CONTRADICTS ITSELF, AND WHAT WAS DONE ABOUT EACH.
# Recorded the way ``timcor``'s pvalue and ``splitmon``'s format are, because
# in all four the documentation is the only source and it is wrong or unclear:
#
#   1. ``style`` is documented **twice with different casing**. Magplot's
#      common table says "Contour line style (solid, dash, dot, chain_dash,
#      chain_dot)"; the contour-specific table says 'Line Style can be "SOLID",
#      "DASH", "DOT", "CHAIN_DASH", "CHAIN_DOT"' (the PDF clips after
#      CHAIN_DASH; the fifth value is recovered from ``cdo -h contour``).
#      Declared **once, lowercase**. It is one parameter, not two: both lists
#      name the same five styles in the same order, so this is a casing
#      difference and not a vocabulary difference — which is worth stating,
#      because "declare only the common one" and "declare only the contour one"
#      would both have silently dropped a documented spelling. Lowercase wins
#      on the page's own evidence: the common table is the one that applies to
#      all three Magplot operators rather than to ``contour`` alone, and the
#      only runnable example anywhere in the module spells a *different*
#      uppercase-documented enum in lowercase — ``colour_triad=cw`` where its
#      table says "CW" or "ACW". A page that runs ``cw`` against a table saying
#      "CW" is a page whose uppercase is presentational. Unverified either way;
#      see the note on case sensitivity at :data:`_MAGICS_BOOL_CASE_RISK`.
#   2. ``colour_max``'s description reads "Colour for the **Minimum** colour
#      band" — identical to ``colour_min``'s. This is a copy-paste error in
#      ECMWF's source rather than in the PDF: ``cdo -h shaded`` prints the same
#      sentence for both. The help below says *maximum*, and the evidence that
#      this is right rather than merely plausible is the module's own worked
#      example: ``shaded,interval=3,colour_min=violet,colour_max=red,
#      colour_triad=cw`` renders the figure printed beneath it, whose colourbar
#      runs violet at 221 K to red at 314 K. The figure settles it.
#   3. ``list`` is typed ``INTEGER`` and described as "List of levels to be
#      plotted" — which is neither an integer nor, for a contour level, an
#      integral quantity. Declared ``string``, not ``multiselect``: a
#      multiselect renders a closed set of ``choices`` to pick from, and the
#      set here is the data's own level values, which are floats that depend on
#      the field and cannot be enumerated ahead of the run. See the parameter
#      for the separator problem, which is the part that can actually break a
#      command.
#   4. ``RGB``, ``file_split``, ``stat`` and ``obsv`` are all typed ``STRING``
#      with the values "TRUE"/"FALSE". They are switches, so they are declared
#      ``_BOOL`` — but see :data:`_MAGICS_BOOL_CASE_RISK`, which is a real
#      unresolved risk and not a note.
# ---------------------------------------------------------------------------

#: **Retired risk, kept because the answer is worth recording.**
#:
#: :func:`parameter_tokens` normalises a ``_BOOL`` to lowercase ``true`` /
#: ``false`` on the way out, so ``RGB`` checked in the GUI becomes ``RGB=true``
#: while the manual writes every one of these values in capitals. That was an
#: open risk for as long as this machine's CDO had no MAGICS — an ignored
#: switch is not an error, so getting it wrong would have been silent.
#:
#: **It is not wrong. Magics compares these case-insensitively**, measured on a
#: CDO 2.6.3 built against magics 4.16.0. The method matters, because the naive
#: version of it lies: PostScript output embeds a ``%%CreationDate:`` line, so
#: two byte-identical runs hash differently unless that line is stripped. With
#: it stripped the output is exactly reproducible, and::
#:
#:     graph                    -> dff3fa85…   (baseline, no stat)
#:     graph,stat=true          -> 91b8ef9a…   (differs: the switch took effect)
#:     graph,stat=TRUE          -> 91b8ef9a…   (identical to lowercase)
#:
#: The same holds for the enums: ``style=dash``/``DASH`` and
#: ``colour_triad=cw``/``CW`` each produce identical output, which is why
#: :func:`invalid_parameter_values` now compares ``choices`` case-insensitively
#: rather than declaring these vocabularies open.
_MAGICS_BOOL_CASE_RISK = (
    "The CDO manual writes this value in capitals (TRUE/FALSE); this "
    "application sends it lowercase, and Magics accepts either — measured on a "
    "MAGICS build, stat=true and stat=TRUE give byte-identical output."
)

#: The 54 colour names the Magplot page lists for every colour-valued
#: parameter, transcribed from the "Note" section of ``cdo -h contour`` (which
#: prints the same list the PDF does, unclipped).
#:
#: Offered as ``choices`` rather than validated *against*: the same page says a
#: colour may instead be given "in RGB format", so a value outside this list is
#: not necessarily wrong and refusing one would refuse a documented call. The
#: GUI's ``select`` widget is editable for exactly this reason.
_MAGICS_COLOURS: Tuple[str, ...] = (
    "red", "green", "blue", "yellow", "cyan", "magenta", "black", "avocado",
    "beige", "brick", "brown", "burgundy", "charcoal", "chestnut", "coral",
    "cream", "evergreen", "gold", "grey", "khaki", "kellygreen", "lavender",
    "mustard", "navy", "ochre", "olive", "peach", "pink", "rose", "rust",
    "sky", "tan", "tangerine", "turquoise", "violet", "reddishpurple",
    "purplered", "purplishred", "orangishred", "redorange", "reddishorange",
    "orange", "yellowishorange", "orangeyellow", "orangishyellow",
    "greenishyellow", "yellowgreen", "yellowishgreen", "bluishgreen",
    "bluegreen", "greenishblue", "purplishblue", "bluepurple", "bluishpurple",
    "purple", "white",
)

#: Why a colour value must never contain a comma, said on every colour
#: parameter because it is the one way a user can silently corrupt the command.
#:
#: CDO splits the operator token on commas to get its ``key=value`` pairs — the
#: manual's own wording for the parameter is "Comma-separated list of plot
#: parameters" — so a comma *inside* a value ends that value and starts what
#: CDO reads as the next parameter. This is why the manual's RGB form uses
#: semicolons where every other RGB notation in computing uses commas:
#: ``RGB(0.0;0.0;1.0)``, as printed in the colour-table example. The semicolons
#: are load-bearing, not a house style.
#:
#: :func:`invalid_parameter_values` enforces it — see the ``_MAGICS_COLOUR_*``
#: entries there — so a pasted ``RGB(1,0,0)`` is refused by this application
#: with its own sentence rather than reaching CDO as two broken parameters.
_MAGICS_COLOUR_HELP = (
    "A colour name from the list, or RGB format written with SEMICOLONS: "
    "RGB(0.0;0.0;1.0). Commas are not allowed inside the value — CDO splits "
    "the operator token on commas to find its parameters, so RGB(1,0,0) is "
    "read as three parameters and the command breaks."
)

#: ``device``, shared by all six operators. The list is identical in all three
#: module pages and in ``cdo -h`` for each.
#:
#: The default is stated because it decides the *filename* the run produces,
#: not merely the format: the output is ``<obase>.<device>``, so leaving this
#: blank writes a PostScript file whose extension a user who typed "plot" did
#: not choose. See :func:`magics_output_suffix`.
_MAGICS_DEVICE = _kw(
    "device", _SELECT, "output device", "ps",
    choices=("ps", "eps", "pdf", "png", "gif", "gif_animation", "jpeg",
             "svg", "kml"),
    help="Output format, which also decides the file extension: the plot is "
         "written to <obase>.<device>, or <obase>_<variable>.<device> for "
         "contour/shaded/grfill. Default is ps (PostScript). Use "
         "gif_animation with step_freq to animate a time axis.",
)

#: ``projection``, shared by Magplot and Magvector. Absent from Maggraph, which
#: draws no map — declared per operator rather than as one "common" tuple for
#: that reason.
_MAGICS_PROJECTION = _kw(
    "projection", _SELECT, "projection", "cylindrical",
    choices=("cylindrical", "polar_stereographic", "robinson", "mercator"),
    help="Map projection for the plot. Only these four are offered by CDO, "
         "which is a much shorter list than Magics itself supports.",
)

#: ``step_freq``, shared by Magplot and Magvector. The PDF clips this one after
#: "(dev" — everything from there on is recovered from ``cdo -h shaded``, which
#: prints the full sentence.
#:
#: Both halves of the recovered tail matter and neither was guessable: the
#: default is 1, and the parameter is **ignored entirely** when the input has
#: more than one variable. A user animating a multi-variable file would
#: otherwise have no way to learn why the setting did nothing.
_MAGICS_STEP_FREQ = _kw(
    "step_freq", _INT, "timestep frequency", "1",
    help="Plot every nth timestep when building an animation "
         "(device=gif_animation). Default 1, meaning every timestep. Ignored "
         "if the input file has more than one variable. [recovered from "
         "cdo -h; the PDF table is clipped mid-word]",
)

#: The nine parameters common to contour, shaded and grfill, in the order the
#: module page's own table lists them.
#:
#: ``style`` sits in this tuple rather than beside ``contour``'s own additions,
#: which is contradiction 1 in the section note resolved in favour of the
#: common table: it is documented for all three operators there, and only
#: ``contour`` re-documents it. Declaring it twice would put two ``style``
#: fields on the contour form.
_MAGPLOT_COMMON: Tuple[OperatorParam, ...] = (
    _MAGICS_DEVICE,
    _MAGICS_PROJECTION,
    # A closed set, and enforced. Both halves are now measured against a
    # MAGICS-enabled CDO 2.6.3 rather than inferred:
    #   style=dash and style=DASH  -> byte-identical output (ignoring the PS
    #                                 CreationDate line, the only varying byte)
    #   style=chain_dot / CHAIN_DOT -> likewise
    #   style=bogus                 -> "Invalid parameter specification", and
    #                                  **exit 0 with no file written**
    # So the two documented casings are one vocabulary, and anything outside it
    # fails silently — which is why this is enforced rather than left open.
    # :func:`invalid_parameter_values` compares choices case-insensitively.
    _kw("style", _SELECT, "contour line style", "solid",
        choices=("solid", "dash", "dot", "chain_dash", "chain_dot"),
        help="Line style for the contour lines. The CDO manual lists these "
             "five twice — lowercase in the common table, uppercase in "
             "contour's own — and they are the same five styles: measured on "
             "a MAGICS build, dash and DASH produce identical output. Either "
             "casing is accepted. A value outside the five writes no file at "
             "all while still exiting 0."),
    _kw("min", _FLOAT, "minimum value",
        help="Lowest data value to plot. Values below it fall outside the "
             "lowest contour band."),
    _kw("max", _FLOAT, "maximum value",
        help="Highest data value to plot."),
    _kw("lon_min", _FLOAT, "minimum longitude",
        help="West edge of the plotted area, in degrees."),
    _kw("lon_max", _FLOAT, "maximum longitude",
        help="East edge of the plotted area, in degrees."),
    _kw("lat_min", _FLOAT, "minimum latitude",
        help="South edge of the plotted area, in degrees."),
    _kw("lat_max", _FLOAT, "maximum latitude",
        help="North edge of the plotted area, in degrees."),
    _kw("count", _INT, "number of levels",
        help="How many contour levels or colour bands to draw. An "
             "alternative to interval, which sets their spacing instead."),
    _kw("interval", _FLOAT, "interval between levels",
        help="Spacing between contour levels or colour bands, in the data's "
             "own units. The module's worked example uses interval=3 on a "
             "temperature field in K."),
    # Contradiction 3. Typed INTEGER in the manual and described as a list of
    # levels; declared as text because it is neither.
    #
    # The separator is the part that can actually break a command and the
    # manual never states it. It cannot be a comma — CDO splits the operator
    # token on commas, so ``list=1,2,3`` reaches Magics as ``list=1`` followed
    # by two parameters called ``2`` and ``3``. By analogy with the RGB form on
    # the same page, which uses semicolons for exactly this reason, a semicolon
    # is the likely separator; that is an inference from a sibling notation and
    # is labelled as one here rather than asserted in the help text, because a
    # user who follows a confident wrong instruction has no way to tell it was
    # a guess. Unverifiable on this build.
    _kw("list", _STR, "explicit level list", "e.g. 250;260;270",
        help="An explicit list of levels to plot, instead of count or "
             "interval. Do NOT separate them with commas: CDO splits the "
             "operator token on commas, so a comma ends this value. The "
             "manual does not say what the separator is; a semicolon is the "
             "likely one, since the RGB colour form on the same page uses "
             "semicolons for this reason. Unverified."),
    _kw("RGB", _BOOL, "colours given in RGB format",
        help="Say that the colour values above are RGB triples rather than "
             "colour names. " + _MAGICS_BOOL_CASE_RISK),
    _MAGICS_STEP_FREQ,
    # The tail of this one is also recovered from ``cdo -h``; the PDF clips
    # after "if input has mu". The "PS only" restriction was in the clipped
    # part and is the kind of thing a user finds out by getting one file.
    _kw("file_split", _BOOL, "one file per variable",
        help="Write a separate output file for each variable when the input "
             "holds several, instead of one file with several pages. Default "
             "off, and valid only for PostScript output. [recovered from "
             "cdo -h; the PDF table is clipped mid-word] "
             + _MAGICS_BOOL_CASE_RISK),
)

#: ``shaded`` and ``grfill``'s four additions. The module page documents them
#: once, under ``shaded``, and says in that entry's own prose that they are
#: "valid for shaded contour and gridfill operator" — so ``grfill`` gets the
#: same tuple, on the manual's word rather than on the two sharing a module.
_MAGPLOT_SHADED: Tuple[OperatorParam, ...] = (
    _kw("colour_min", _SELECT, "colour of the lowest band", "violet",
        choices=_MAGICS_COLOURS, open_choices=True,
        help=_MAGICS_COLOUR_HELP),
    # Contradiction 2. The manual and ``cdo -h`` both say "Minimum" here; the
    # module's own worked example and the figure printed with it say otherwise.
    _kw("colour_max", _SELECT, "colour of the highest band", "red",
        choices=_MAGICS_COLOURS, open_choices=True,
        help="Colour for the MAXIMUM colour band. (The CDO manual describes "
             "this as the minimum band, word for word identical to "
             "colour_min — a copy-paste error in ECMWF's own text, which "
             "cdo -h reproduces. The module's worked example, "
             "colour_min=violet with colour_max=red, renders a colourbar "
             "running violet at the cold end to red at the warm end.) "
             + _MAGICS_COLOUR_HELP),
    # Closed and enforced, on the same measurements as ``style`` above:
    #   cw == CW, and acw == ACW == the default (so the manual's recovered
    #   "Default is ACW" is confirmed), while colour_triad=bogus writes no
    #   file and still exits 0.
    _kw("colour_triad", _SELECT, "colour sequence direction", "ACW",
        choices=("CW", "ACW"),
        help="Which way round the colour wheel to run between colour_min and "
             "colour_max: CW clockwise, ACW anticlockwise. Default ACW "
             "(confirmed by measurement — colour_triad=acw and no colour_triad "
             "give identical output). Either casing is accepted; the manual's "
             "worked example writes cw while its table writes CW. Only has an "
             "effect together with colour_min and colour_max."),
    _kw("colour_table", _FILE, "colour table file", "file of RGB(r;g;b) lines",
        file_kind=_ft.TEXT,
        help="A file of user-specified colours: a line giving the number of "
             "colours, then one colour per line. The manual's example is six "
             "lines of RGB(0.0;0.0;1.0) form — semicolons, not commas."),
)

#: ``contour``'s three additions. ``style`` is documented here too and is
#: deliberately absent — see contradiction 1 and ``_MAGPLOT_COMMON``.
_MAGPLOT_CONTOUR: Tuple[OperatorParam, ...] = (
    _kw("colour", _SELECT, "contour line colour", "blue",
        choices=_MAGICS_COLOURS, open_choices=True,
        help=_MAGICS_COLOUR_HELP),
    _kw("thickness", _FLOAT, "contour line thickness",
        help="Thickness of the contour lines."),
)

_PARAM_SPECS: Dict[str, Tuple[OperatorParam, ...]] = {
    # ---------- Selection ----------
    "selcode":     (_p("codes", _STR, "codes", "e.g. 130,131"),),
    "delcode":     (_p("codes", _STR, "codes", "e.g. 130,131"),),
    "selname":     (_p("vars", _STR, "variables", "comma-separated variable names"),),
    "delname":     (_p("vars", _STR, "variables", "comma-separated variable names"),),
    "selstdname":  (_p("stdnames", _STR, "standard names", "comma-separated standard names"),),
    "sellevel":    (_p("levels", _STR, "levels", "e.g. 1000,850"),),
    "sellevidx":   (_p("levidx", _STR, "level indices", "comma-separated level indices"),),
    "selgrid":     (_p("grids", _STR, "grids", "grid IDs or names"),),
    "selgridname": (_p("gridnames", _STR, "gridnames", "grid names"),),
    "selzaxis":    (_p("zaxes", _STR, "zaxes", "z-axis IDs"),),
    "selzaxisname":(_p("zaxisnames", _STR, "zaxisnames", "z-axis names"),),
    "selltype":    (_p("ltypes", _STR, "ltypes", "GRIB level types"),),
    "seltabnum":   (_p("tabnums", _STR, "tabnums", "table numbers"),),
    "seltimestep": (_p("timesteps", _STR, "timesteps", "comma-separated time steps"),),
    "seltime":     (_p("times", _STR, "times", "e.g. 12:00,18:00"),),
    "selhour":     (_p("hours", _STR, "hours", "e.g. 0,6,12,18"),),
    "selday":      (_p("days", _STR, "days"),),
    "selmon":      (_p("months", _STR, "months", "e.g. 1,2,12"),),
    "selyear":     (_p("years", _STR, "years", "e.g. 1981/2010"),),
    # A *list* of seasons, not one of them, and the difference is measurable:
    # ``cdo selseas,DJF,MAM`` on a 730-step daily series keeps 364 timesteps
    # against 180 for ``selseas,DJF`` — measured on 2.6.3. Declared ``string``
    # with ``choices`` it was neither: the closed-set check refused the comma
    # form outright ("seasons must be one of DJF, MAM, JJA, SON, not
    # 'DJF,MAM'"), so the app declined a command CDO runs.
    #
    # ``multiselect`` is the kind that already means this, and it fixes a
    # second disagreement in the same breath. ``against_choices`` matches
    # case-insensitively — argued from Magics, where the manual prints its
    # enums in both cases — but seasons are case-*sensitive* in CDO:
    # ``selseas,djf`` aborts with exit 1. The per-item check below compares
    # exactly, so the app now refuses what the binary refuses.
    "selseas":     (_p("seasons", _MULTISELECT, "seasons", "e.g. DJF,MAM",
                       choices=("DJF", "MAM", "JJA", "SON"),
                       help="One or more seasons, comma-separated. Order does "
                            "not matter and a repeat is harmless — measured, "
                            "``selseas,DJF,DJF`` returns the same 180 "
                            "timesteps as ``selseas,DJF``."),),
    "seldate":     (
        _p("date1", _STR, "date1", "first date (YYYY-MM-DD)"),
        _p("date2", _STR, "date2", "second date (optional)", optional=True),
    ),
    "selsmon":     (
        _p("month", _INT, "month", "1–12"),
        _p("nts1", _INT, "nts1", "optional", optional=True),
        _p("nts2", _INT, "nts2", "optional", optional=True),
    ),
    "sellonlatbox":(
        _p("lon1", _FLOAT, "lon1"),
        _p("lon2", _FLOAT, "lon2"),
        _p("lat1", _FLOAT, "lat1"),
        _p("lat2", _FLOAT, "lat2"),
    ),
    "selindexbox": (
        _p("idx1", _INT, "idx1"),
        _p("idx2", _INT, "idx2"),
        _p("idy1", _INT, "idy1"),
        _p("idy2", _INT, "idy2"),
    ),
    "selparam":    (_p("params", _STR, "params", "parameter identifiers"),),
    "selmonth":    (_p("months", _STR, "months", "e.g. 1,2,12"),),
    # ``selseason`` is ``selseas`` under its other name — the binary lists both
    # and answers identically to ``selseason,DJF,MAM`` (364 timesteps) — so it
    # carries the same declaration for the same measured reasons above.
    "selseason":   (_p("seasons", _MULTISELECT, "seasons", "e.g. DJF,MAM",
                       choices=("DJF", "MAM", "JJA", "SON"),
                       help="One or more seasons, comma-separated. Order does "
                            "not matter and a repeat is harmless."),),
    "selvar":      (_p("vars", _STR, "variables", "comma-separated variable names"),),
    "selregion":   (_p("regions", _STR, "regions", "region IDs"),),
    "selrec":      (_p("records", _STR, "records", "record numbers"),),
    "selgridcell": (_p("cells", _STR, "cells", "grid cell indices"),),
    "selmulti":    (_p("params", _STR, "params", "parameter identifiers"),),
    "seloperator": (_p("params", _STR, "params", "operator parameter selectors"),),
    "delvar":      (_p("vars", _STR, "variables", "comma-separated variable names"),),
    "delparam":    (_p("params", _STR, "params", "parameter identifiers"),),
    "delmulti":    (_p("params", _STR, "params", "parameter identifiers"),),
    "delgridcell": (_p("cells", _STR, "cells", "grid cell indices"),),
    "changemulti": (_p("params", _STR, "params", "old,new parameter mapping"),),
    "setcodetab":  (_p("table", _FILE, "table", "parameter code table file",
                       file_kind=_ft.TEXT),),
    "settabnum":   (_p("tabnum", _INT, "tabnum", "table number"),),
    "setgridcell": (_p("cells", _STR, "cells", "grid cell indices"),),
    "setgridnumber": (_p("number", _INT, "gridnumber", "grid reference number"),),
    "setgriduri":  (_p("uri", _STR, "gridURI", "grid URI"),),
    "setprojparams": (_p("params", _STR, "proj4 params", "PROJ.4 parameter string"),),
    "setrcaname":  (_p("rcafile", _FILE, "RCA name file", "file listing RCA names",
                       file_kind=_ft.TEXT),),
    "setstdname":  (_p("stdname", _STR, "standard name", "e.g. air_temperature"),),
    "usegridnumber": (_p("number", _INT, "gridnumber", "grid reference number"),),
    "mod":         (_p("c", _FLOAT, "c (divisor)"),),
    "ensbrs":      (_p("x", _FLOAT, "x (threshold)"),),
    # Not a threshold like ensbrs's: CDO parses it as an integer and rejects a
    # probability with "Integer parameter >0.5< contains invalid character".
    "ensroc":      (_p("nbins", _INT, "nbins", "number of ROC bins"),),
    # ``neof`` sizes outfile2 and nothing else — measured on 2.6.3, ``eof,3``
    # over a 36x18 input wrote 3 eigenvectors to outfile2 and all 648
    # eigenvalues to outfile1. The help says so because the name does not: a
    # user who asks for 3 and finds 648 timesteps in the first file has no way
    # to tell whether they mis-typed the parameter.
    #
    # The four environment variables that change what these operators compute
    # are *not* here: this tuple is positional and becomes the ``op,a,b``
    # token, and an env var is neither. See ``_OPERATOR_ENV``.
    **{name: (_p("neof", _INT, "neof", "number of EOFs to write",
                 help="Sizes outfile2 only. outfile1 always receives the "
                      "whole eigenvalue spectrum — measured on 2.6.3, eof,3 "
                      "over a 36x18 field wrote 3 eigenvectors to outfile2 "
                      "and all 648 eigenvalues to outfile1."),)
       for name in ("eof", "eoftime", "eofspatial", "eof3d", "eof3dtime",
                    "eof3dspatial")},
    # ``epsilon``, which is what CDO and the manual both call it; it was
    # declared as ``sign`` here, a name neither uses. Constrained because CDO
    # does not constrain it: measured on 2.6.3 against a complex field,
    # ``cdo -f nc4 fourier,0`` and ``fourier,2`` **both exit 0** and write a
    # file, so the documented -1/1 contract is enforced nowhere but here.
    # ``choices`` on an ``int`` is honoured by ``invalid_parameter_values``.
    #
    # -1 is the forward transform and 1 the inverse, which is the order the
    # manual's own example uses: ``cdo -f ext fourier,1 -fourier,-1 …``.
    "fourier":     (_p("epsilon", _INT, "epsilon", "-1 or 1",
                       choices=("-1", "1"),
                       help="-1 transforms forward, 1 back. CDO accepts any "
                            "integer — fourier,0 and fourier,2 both exit 0 on "
                            "2.6.3 — so a typo here is a finished file rather "
                            "than an error, and this app refuses it instead. "
                            "The operator also needs complex input, which only "
                            "NetCDF4 and EXTRA can hold."),),
    "harmonic":    (_p("nwaves", _INT, "nwaves"), _p("end", _INT, "end")),
    # Metres, and the same trap the ml2pl/ap2pl group carries: a wrong unit
    # runs. ``cdo -h gh2hl``: "levels (Float) Comma-separated list of height
    # levels in meter".
    **{name: (
        _p("levels", _STR, "height levels", "500,1000,2000,5000",
           help="Comma-separated target height levels in METRES. The input "
                "must carry the 3D geometric height, identified by the CF "
                "standard name geometric_height_at_full_level_center."),
    ) for name in ("gh2hl", "gh2hlx")},
    # Was ``zdes`` / "z-axis description file", and both were wrong: this is a
    # DATA file holding a 3D target coordinate *field*, not a text Z-axis
    # description of the kind intlevel's zdescription takes. Straight from
    # ``cdo -h intlevel3d`` on 2.6.3:
    #
    #   SYNOPSIS  cdo intlevel3d,tgtcoordinate infile1 infile2 outfile
    #   PARAMETERS
    #     tgtcoordinate (STRING)  filename for 3D vertical target coordinates
    #
    # Verified by running it: a NetCDF file carrying one 3D variable is
    # accepted, which a zaxis .txt description would not be.
    **{name: (
        _p("tgtcoordinate", _FILE, "3D target coordinate file",
           "NetCDF file holding the 3D target coordinate",
           file_kind=_ft.DATA,
           help="A data file whose 3D field is the vertical target coordinate "
                "to interpolate onto — not a Z-axis description text file. "
                "The source coordinate is input 2; this is the target."),
    ) for name in ("intlevel3d", "intlevelx3d")},
    "isosurface":  (_p("value", _FLOAT, "isovalue"),),
    "import_obs":  (_p("grid", _GRID, "grid", choices=GRID_PRESETS),),

    # ---------- Climate model output rewriting ----------
    #
    # ``cdo cmor,MIPtable[,cmor_name=VarList[,key=value[,…]]] infile``. One
    # input, and — uniquely in this file — **zero argv outputs**: CMOR builds
    # its own filenames from the project's DRS template and writes one file per
    # output variable under ``drs_root``. See ``_CMOR_OUTPUT_NOTE``.
    #
    # The installed CDO cannot run this operator. ``cdo --config has-cmor``
    # answers ``no`` on 2.6.3 and every call aborts with "CMOR support not
    # compiled in!" — so the *grammar* below is measured and the *runtime*
    # behaviour is not. What that costs is recorded per-parameter where it
    # matters; the general shape of it is that ``cdo -h cmor`` prints the full
    # PARAMETERS section on this build even though the operator cannot run, so
    # names, types and optionality are the binary's own answer, while anything
    # that would need a successful run is marked as inferred.
    #
    # The abort happens *before* the key/value parser is reached, which was
    # measured rather than assumed: ``cdo cmor,SomeTable.json,bogus_key=1 in.nc``
    # gets the identical "CMOR support not compiled in!" and never complains
    # about the key. So no spelling can be confirmed through this operator, and
    # the two places the manual contradicts itself are settled from ``cdo -h``
    # alone:
    #
    #   * The manual's Example line reads ``…,grid_table=…,info=cmor.rc``, and
    #     its own PARAMETERS list documents ``grid_info | gi`` and never mentions
    #     ``grid_table``. ``cdo -h cmor`` on 2.6.3 lists ``grid_info | gi`` and
    #     no ``grid_table``, so ``grid_info`` is what is declared. ``grid_table``
    #     is left undeclared rather than declared as an alias: it appears in one
    #     example line, in no parameter list, and cannot be tried against a
    #     binary that aborts first.
    #   * ``required_time_units`` is documented with the format string
    #     'days since YYYY-day-month hh:mm:ss', which is not a coherent udunits
    #     template — it names the day twice and the month in the seconds' place.
    #     The placeholder below is the real udunits form. Recorded rather than
    #     silently corrected, since a user reading the manual beside this field
    #     will see two different strings.
    #
    # Aliases are deliberately not declared. Every keyword has a documented short
    # form (``cn``, ``gi``, ``mt``, ``rtu``, …) and the canonical long name is
    # what this emits; the short forms are for reading CDO's documentation, not
    # for building a command, and a second accepted spelling per parameter would
    # be twenty-two more ways for two surfaces to disagree. They are named in
    # ``help`` where they are worth knowing.
    #
    # The cross-parameter rule the DESCRIPTION states — "If name or code is
    # specified, a corresponding cmor_name … is also required" — is deliberately
    # NOT enforced here, and that is a decision rather than an oversight. All
    # three checkers (``missing_required_parameters``, ``invalid_parameter_values``,
    # ``missing_parameter_files``) are single-parameter and positional by
    # construction, so expressing it would mean a fourth checker with a different
    # shape for one operator. Against that: the rule's own precondition is
    # unverifiable here — whether a given ``cmor_name`` "can also be found in the
    # MIPtable" needs the table parsed and the CMOR runtime this build lacks — so
    # a check could only ever test that the *field* is non-blank, which is the
    # weaker half of the rule and would still let the real error through. It is
    # stated in ``help`` on both ``name`` and ``code`` instead, where the user
    # filling the field can read it, and left to CDO to enforce.
    "cmor":        (
        # Required, and required is what makes the last gate before argv work:
        # with nothing declared, ``missing_required_parameters("cmor", [])``
        # returned [] and ``cdo cmor infile`` was assembled and run. That call
        # cannot succeed on any build — on this one it is "Operator missing,
        # in.nc is a file on disk!", which names the user's data file rather
        # than the table they forgot.
        #
        # ``reads=False`` on the same reasoning already recorded for the
        # ``setpartab*`` family: CMOR resolves a table name against its own
        # search path, and the manual's own example passes a relative
        # ``Tables/CMIP6_day.json``. Declaring it ``reads=True`` would put
        # ``missing_parameter_files`` between the user and every call that
        # relies on that search path. Kind ``file`` all the same, so the form
        # offers a browse button and the path is aliased if it holds a space.
        _p("MIPtable", _FILE, "MIP table", "e.g. Tables/CMIP6_day.json",
           reads=False, file_kind=_ft.JSON,
           help="Name of the MIP table as used by CMOR. May be a path or a "
                "bare table name that CMOR resolves against its own search "
                "path, which is why it is not checked for existence here."),
        _kw("cmor_name", _STR, "cmor_name", "tas, or tas,pr",
            help="Variable selector: a comma-separated list of CMOR variable "
                 "names as spelled in the MIP table. Default is to process "
                 "every variable. Short form: cn."),
        _kw("name", _STR, "name", "name of an infile variable",
            help="Variable selector, by the name the INFILE uses. CDO also "
                 "requires a cmor_name alongside it, to map the infile "
                 "variable onto the CMOR variable. Short form: n."),
        # INTEGER per ``cdo -h cmor``, so the int parser refuses "1a" here
        # before the run rather than after it.
        _kw("code", _INT, "code", "three-digit GRIB code",
            help="Variable selector, by GRIB code. CDO also requires a "
                 "cmor_name alongside it, to map the infile variable onto the "
                 "CMOR variable. Short form: c."),
        # A comma-separated list of FILENAMES, and declared ``string`` rather
        # than ``file`` for exactly that reason. ``kind=_FILE`` means one path
        # to the whole toolkit: ``file_parameter_indexes`` hands the value to
        # ``_create_input_alias`` as a single path, and ``missing_parameter_files``
        # asks ``Path(value).is_file()`` of it — so a perfectly legal
        # ``info=a.rc,b.rc`` would be aliased into one nonexistent name and then
        # refused before the run. The schema has no list-of-files kind today and
        # inventing one for a single parameter of an operator this build cannot
        # run is the wrong trade; ``string`` is the option that never refuses a
        # valid command, which is the rule this file applies whenever a check
        # cannot be made correctly. The cost is real and worth naming: a path
        # here containing a space is not aliased, and CDO re-splits its own argv,
        # so such a path will fail. Said in ``help`` rather than left to be
        # discovered.
        _kw("info", _STR, "info files", ".cdocmorinfo, or a.rc,b.rc",
            help="Comma-separated list of files holding global attributes and "
                 "control keywords. Default: .cdocmorinfo in the working "
                 "directory. Avoid paths containing spaces — a list cannot be "
                 "path-aliased, and CDO re-splits its own command line. "
                 "Short form: i."),
        # Single-valued, so these two can be real ``file`` parameters: checked
        # for existence, and aliased when the path holds a space.
        # ``_ft.GRID`` rather than ``_ft.NETCDF`` or ``_ft.TEXT``, because the
        # manual takes both: "NetCDF or table formatted file with model grid
        # description". That is the grid chooser's whole job.
        _kw("grid_info", _FILE, "grid info file",
            "NetCDF or table file describing the model grid",
            file_kind=_ft.GRID,
            help="Substitutes the horizontal and vertical axes with the ones "
                 "from this file. Short form: gi. The manual's Example line "
                 "spells this key grid_table; `cdo -h cmor` on 2.6.3 does not "
                 "know that name, and grid_info is what it documents."),
        _kw("mapping_table", _FILE, "mapping table",
            "Fortran namelist of variable info",
            file_kind=_ft.TEXT,
            help="Fortran Namelist carrying variable information — renaming "
                 "chiefly. The way to map more than one variable in a single "
                 "operator call. Short form: mt."),
        # CHARACTER in the manual and single letters in practice, which is not
        # the same thing as a boolean: ``_BOOL`` would render a checkbox and
        # ``parameter_tokens`` would emit ``keep_all_attributes=true``, a value
        # CDO's own documentation never mentions. Declared ``select`` with the
        # documented value set, so the GUI offers a picker over exactly the
        # letters CDO names. Same for drs, output_mode, cell_methods, positive,
        # character_axis and t_axis below.
        _kw("keep_all_attributes", _SELECT, "keep all attributes",
            choices=("y", "n"),
            help="y passes every infile attribute through; n discards them "
                 "all. Short form: kaa."),
        _kw("drs", _SELECT, "use DRS structure", choices=("y", "n"),
            help="y (CDO's default) moves the output into the project's DRS "
                 "directory structure; n leaves it where it was written. "
                 "Short form: d."),
        # A DIRECTORY, so emphatically not ``kind=_FILE``: ``missing_parameter_files``
        # tests ``Path(value).is_file()``, which is False for every directory
        # that exists, so declaring this a file would refuse every correct value
        # it can ever hold.
        #
        # Which also means it is not path-aliased, and it carries the same space
        # trap ``info`` does: CDO re-splits its own command line, so a directory
        # whose path contains a space fails however it is quoted. The aliasing
        # machinery is built for files — ``_create_input_alias`` symlinks a file
        # or its parent — and a *destination* directory that does not exist yet
        # is neither. Said in ``help`` rather than half-handled.
        _kw("drs_root", _STR, "DRS root directory",
            "output root [default: the working directory]",
            help="CMOR's output root directory, and the one place to look for "
                 "what this operator wrote. Defaults to the working directory, "
                 "which NCExplorer pins per run and shows in the session log. "
                 "Avoid a path containing spaces: CDO re-splits its own command "
                 "line, and a directory cannot be path-aliased. Short form: dr."),
        _kw("output_mode", _SELECT, "output mode", choices=("r", "a"),
            help="r replaces (CDO's default); a appends to the chunk named by "
                 "last_chunk. Short form: om."),
        # A chunk CMOR itself wrote, so a dataset rather than a description —
        # NetCDF in practice, but declared ``data`` because nothing in the
        # manual forbids the other formats CDI writes.
        _kw("last_chunk", _FILE, "last chunk", "file to append to",
            file_kind=_ft.DATA,
            help="Filename of the chunk output_mode=a appends to. Short "
                 "form: lc."),
        _kw("max_size", _INT, "max size", "in GIGABYTES",
            help="Limit on the size of one output file, in gigabytes — not "
                 "bytes and not megabytes. Short form: ms."),
        # Negative values are documented and legal, and Python's int() takes
        # them, so ``invalid_parameter_values`` passes ``-1`` through. Asserted
        # in the test file rather than assumed, because a field that silently
        # refuses the documented "no compression" value is a field that cannot
        # express the default.
        _kw("deflate_level", _INT, "deflate level", "-1, 0, or 1-9",
            help="Compression level. -1 is no compression at all; 0 applies "
                 "the shuffle filter only. Short form: dl."),
        _kw("version_date", _INT, "version date", "e.g. 20240115",
            help="Names the version subdirectory in the CMIP6 DRS. Short "
                 "form: vd."),
        # The manual's format string — 'days since YYYY-day-month hh:mm:ss' —
        # is not a udunits template; the placeholder is the real form.
        _kw("required_time_units", _STR, "required time units",
            "days since 1850-01-01 00:00:00",
            help="The time axis reference date the experiment requires, as a "
                 "udunits string. CDO's manual documents the format as "
                 "'days since YYYY-day-month hh:mm:ss', which is not a "
                 "udunits template; the form in the placeholder is. "
                 "Short form: rtu."),
        _kw("cell_methods", _SELECT, "cell methods",
            choices=("m", "p", "c", "n", "d"),
            help="Cell method of the time axis; m is CDO's default. "
                 "Short form: cm."),
        _kw("units", _STR, "units", "e.g. K, mm/day",
            help="Units of the variable. Must be a string UDunits knows, or "
                 "CMOR rejects it. Short form: u."),
        _kw("variable_comment", _STR, "variable comment",
            help="Free text written as the variable's comment attribute. "
                 "Short form: vc."),
        _kw("positive", _SELECT, "positive direction", choices=("u", "d"),
            help="Positive flux direction: u for upward, d for downward. "
                 "Short form: p."),
        _kw("z_axis", _STR, "z-axis variable", "e.g. plev",
            help="Name of the coordinate variable for the target variable's "
                 "z-axis. Short form: za."),
        _kw("character_axis", _SELECT, "character axis",
            choices=("basin", "vegtype", "oline"),
            help="Name of the coordinate variable for a character axis of the "
                 "target variable. Short form: ca."),
        _kw("t_axis", _SELECT, "t-axis", choices=("cmip",),
            help="Snaps time values and bounds to the nearest value the named "
                 "project requires. cmip is the only value CDO documents. "
                 "Short form: ta."),
    ),

    # ``cmorlite,table[,convert]``. ``convert`` is a bare flag, the second one
    # in the schema after ``splitname,swap``, and the binary distinguishes the
    # three spellings cleanly (measured on 2.6.3 against a real parameter
    # table, since a missing table aborts before the flag is looked at):
    #
    #   cmorlite,tbl.txt,convert       -> exit 0
    #   cmorlite,tbl.txt,convert=true  -> "Unknown parameter: >convert=true<"
    #   cmorlite,tbl.txt,bogus         -> "Unknown parameter: >bogus<"
    #
    # ``table`` is required, and required here matters more than usual: with no
    # parameter at all ``cdo cmorlite in.nc out.nc`` **hangs** rather than
    # aborting — no output, no exit, and closing stdin does not release it. See
    # the note above ``_MISC_HANGS_WITHOUT_PARAMETERS``.
    "cmorlite":    (
        # Text, not JSON like ``cmor``'s MIPtable: ``cdo -h cmorlite`` says it
        # "process[es] the header and variable section of such MIP tables. In
        # addition to the CMOR 2 and 3 table format, the CDO parameter table
        # format is also supported" — and the CDO parameter table is ASCII.
        _p("table", _FILE, "CMOR table", file_kind=_ft.TEXT,
           help="Parameter table to apply. Required — with no table at all "
                "cmorlite hangs instead of failing."),
        _p("convert", _BOOL, "convert units", optional=True, form=_FLAG,
           help="Convert the units of a variable when the table asks for units "
                "the input does not already have."),
    ),
    "genscon2":    (_p("grid", _GRID, "grid", choices=GRID_PRESETS),),
    "genycon2test":(_p("grid", _GRID, "grid", choices=GRID_PRESETS),),
    # Both hypothesis tests prompt for "constant and risk (e.g. 0.05)"; the
    # constant has to be positive or CDO aborts on it.
    "meandiff2test":(
        _p("c", _FLOAT, "c (constant)", "must be > 0"),
        _p("risk", _FLOAT, "risk", "significance level, e.g. 0.05"),
    ),
    "varquot2test":(
        _p("c", _FLOAT, "c (constant)", "must be > 0"),
        _p("risk", _FLOAT, "risk", "significance level, e.g. 0.05"),
    ),
    "gridboxmedian":(_p("nx", _INT, "nx"), _p("ny", _INT, "ny")),
    "gridboxkurt": (_p("nx", _INT, "nx"), _p("ny", _INT, "ny")),
    "gridboxskew": (_p("nx", _INT, "nx"), _p("ny", _INT, "ny")),
    "gridboxstd1": (_p("nx", _INT, "nx"), _p("ny", _INT, "ny")),
    "gridboxvar1": (_p("nx", _INT, "nx"), _p("ny", _INT, "ny")),
    # ---------- Operators that print "Too few arguments" without a param ----------
    "delday":      (_p("day", _INT, "day", "1–31"),),
    # ``cdo -h etccdi`` documents one shape for the whole module —
    # ``<operator>,n,startboot,endboot[,m]`` — but CDO 2.6.0 does not implement
    # it that way for all six: the two precipitation indices reject a fourth
    # argument and read the first as ``startboot``, so they get their own entry.
    "etccdi":      _ETCCDI_BOOTSTRAP_PARAMS,
    "etccdi_tn10p":_ETCCDI_BOOTSTRAP_PARAMS,
    "etccdi_tn90p":_ETCCDI_BOOTSTRAP_PARAMS,
    "etccdi_tx10p":_ETCCDI_BOOTSTRAP_PARAMS,
    "etccdi_tx90p":_ETCCDI_BOOTSTRAP_PARAMS,
    "etccdi_r95p": _ETCCDI_PRECIP_PARAMS,
    "etccdi_r99p": _ETCCDI_PRECIP_PARAMS,
    # A GMT colour palette table, the thing ``makecpt`` writes — the manual's
    # own GMT examples run ``makecpt -T213/318/3 -Crainbow > gmt.cpt``.
    "lic":         (_p("cpt", _FILE, "cpt palette file", file_kind=_ft.CPT),),
    "outputkml":   (_p("cpt", _FILE, "cpt palette file", file_kind=_ft.CPT),),
    "outputvrml":  (_p("cpt", _FILE, "cpt palette file", file_kind=_ft.CPT),),
    "outputboundscpt": (_p("cpt", _FILE, "cpt palette file", file_kind=_ft.CPT),),
    "outputcentercpt": (_p("cpt", _FILE, "cpt palette file", file_kind=_ft.CPT),),
    # Unlike maskcircle, which still takes three positional numbers, selcircle
    # shares selregion's key=value parser — and the radius carries its unit.
    "selcircle":   (_p("circle", _STR, "circle",
                       "lon=0,lat=0,radius=1000km"),),
    "delete":      (_p("selection", _STR, "selection", "e.g. name=tas"),),
    "select":      (_p("selection", _STR, "selection", "e.g. name=tas,level=850"),),

    # ---------- Conditional selection ----------
    "ifthenc":     (_p("c", _FLOAT, "c", "constant"),),
    "ifnotthenc":  (_p("c", _FLOAT, "c", "constant"),),

    # ---------- Comparison (constants) ----------
    # ``c`` is compared against the raw field, so it carries the field's units
    # and nothing in the app or in CDO checks that. See _CONSTANT_UNITS_HELP.
    "eqc":         (_COMPC_C,),
    "nec":         (_COMPC_C,),
    "lec":         (_COMPC_C,),
    "ltc":         (_COMPC_C,),
    "gec":         (_COMPC_C,),
    "gtc":         (_COMPC_C,),
    "maxc":        (_p("c", _FLOAT, "c", "constant"),),
    "minc":        (_p("c", _FLOAT, "c", "constant"),),

    # ---------- Modification ----------
    # ``reads=False`` on all five: the value may be a built-in table *name*
    # rather than a path, and the installed CDO cannot be asked — it answers
    # "Help for setpartab in module Setpartab not found". Unverifiable, so not
    # checked, rather than checked on a guess that would refuse valid calls.
    "setpartab":   (_p("table", _FILE, "table", "parameter table name or file",
                       reads=False, file_kind=_ft.TEXT),),
    "setpartabc":  (_p("table", _FILE, "table", "parameter table (code)",
                       reads=False, file_kind=_ft.TEXT),),
    "setpartabn":  (_p("table", _FILE, "table", "parameter table (name)",
                       reads=False, file_kind=_ft.TEXT),),
    "setpartabp":  (_p("table", _FILE, "table", "parameter table (param)",
                       reads=False, file_kind=_ft.TEXT),),
    "setpartabv":  (_p("table", _FILE, "table", "parameter table (variable)",
                       reads=False, file_kind=_ft.TEXT),),
    "setcode":     (_p("code", _INT, "code", "GRIB code number"),),
    "setparam":    (_p("param", _STR, "param", "parameter identifier"),),
    "setname":     (_p("name", _STR, "name", "new variable name"),),
    "setvar":      (_p("name", _STR, "name", "new variable name"),),
    "setunit":     (_p("unit", _STR, "unit", "e.g. K, mm/day"),),
    "setlevel":    (_p("level", _STR, "level"),),
    "setltype":    (_p("ltype", _INT, "ltype", "GRIB level type"),),
    "setdate":     (_p("date", _STR, "date", "YYYY-MM-DD"),),
    "settime":     (_p("time", _STR, "time", "HH:MM"),),
    "setday":      (_p("day", _INT, "day", "1–31"),),
    "setmon":      (_p("month", _INT, "month", "1–12"),),
    "setyear":     (_p("year", _INT, "year"),),
    "settunits":   (_p("units", _STR, "units", "e.g. days, hours"),),
    "settaxis":    (
        _p("date", _STR, "date", "start date (YYYY-MM-DD)"),
        _p("time", _STR, "time", "HH:MM"),
        _p("inc", _STR, "inc", "e.g. 6hour (optional)", optional=True),
    ),
    "settbounds":  (_p("frequency", _STR, "frequency",
                       "hour/day/month/year",
                       choices=("hour", "day", "month", "year")),),
    "setreftime":  (
        _p("date", _STR, "date", "reference date (YYYY-MM-DD)"),
        _p("time", _STR, "time", "reference time (HH:MM)"),
        _p("units", _STR, "units", "optional", optional=True),
    ),
    "setcalendar": (_p("calendar", _STR, "calendar",
                       choices=("standard", "proleptic_gregorian", "gregorian",
                                "360_day", "365_day", "366_day")),),
    "setmaxsteps": (_p("nsteps", _INT, "nsteps"),),
    "setmissval":  (_p("miss", _FLOAT, "miss", "missing value"),),
    "setctomiss":  (_p("c", _FLOAT, "c", "constant"),),
    "setmisstoc":  (_p("c", _FLOAT, "c", "constant"),),
    "setrtomiss":  (
        _p("rmin", _FLOAT, "rmin"),
        _p("rmax", _FLOAT, "rmax"),
    ),
    "setvals":     (_p("pairs", _STR, "pairs", "oldval,newval,..."),),
    "setvrange":   (
        _p("rmin", _FLOAT, "rmin"),
        _p("rmax", _FLOAT, "rmax"),
    ),
    "setattribute":(_p("attrs", _STR, "attrs", "e.g. name@units=K"),),
    "delattribute":(_p("attrs", _STR, "attrs", "e.g. name@units, or *@history"),),
    "shifttime":   (_p("sval", _STR, "sval", "e.g. 1day, -6hour"),),
    "shiftx":      (_p("nshift", _INT, "nshift", optional=True),),
    "shifty":      (_p("nshift", _INT, "nshift", optional=True),),
    "chcode":      (_p("pairs", _STR, "oldcode,newcode,...",
                       "comma-separated old/new code pairs"),),
    "chname":      (_p("pairs", _STR, "ovar,nvar,...",
                       "comma-separated old/new variable name pairs"),),
    "chvar":       (_p("pairs", _STR, "ovar,nvar,..."),),
    "chunit":      (_p("pairs", _STR, "oldunit,newunit,..."),),
    "chparam":     (_p("pairs", _STR, "oldparam,newparam,..."),),
    "chlevel":     (_p("pairs", _STR, "oldlev,newlev,..."),),
    "chlevelc":    (
        _p("code", _INT, "code"),
        _p("oldlev", _STR, "oldlev"),
        _p("newlev", _STR, "newlev"),
    ),
    "chlevelv":    (
        _p("var", _STR, "var"),
        _p("oldlev", _STR, "oldlev"),
        _p("newlev", _STR, "newlev"),
    ),
    "chltype":     (
        _p("oldtype", _INT, "oldtype"),
        _p("newtype", _INT, "newtype"),
    ),
    "chtabnum":    (
        _p("oldtab", _INT, "oldtab"),
        _p("newtab", _INT, "newtab"),
    ),
    "setgrid":     (_p("grid", _GRID, "grid", "preset or grid file",
                       choices=GRID_PRESETS),),
    "setgridtype": (_p("gridtype", _STR, "gridtype",
                       choices=("curvilinear", "unstructured",
                                "regular", "regularnn", "lonlat",
                                "dereference", "cell")),),
    # These two are ``_GRID`` for the widget — the form's preset dropdown and
    # browse button — but the file they take is not a grid description. CDO
    # names them separately from ``setgrid``'s: "gridarea [STRING] Data file,
    # the first field is used as grid cell area", "gridmask [STRING] Data file,
    # the first field is used as grid mask". So the chooser offers datasets,
    # not descriptions; a ``.txt`` grid description here is the wrong file.
    "setgridarea": (_p("grid", _GRID, "grid (area file)", file_kind=_ft.DATA),),
    "setgridmask": (_p("grid", _GRID, "grid (mask file)", file_kind=_ft.DATA),),
    "setzaxis":    (_p("zaxis", _FILE, "zaxis", "z-axis description file",
                       file_kind=_ft.TEXT),),
    "setgatt":     (
        _p("attname", _STR, "attname"),
        _p("attstring", _STR, "attstring"),
    ),
    "setgatts":    (_p("attfile", _FILE, "attfile", "attribute file",
                       file_kind=_ft.TEXT),),
    # Moved to Miscellaneous with the rest of its module; see the
    # "Set the bounds of a field" entry in ``_MODULE_CATEGORY``. The parameters
    # stay here, next to the other ``set*`` operators, because this table is
    # ordered by where an entry is easiest to find rather than by category.
    #
    # Four keyword halos and a fill value, read off ``cdo -h sethalo`` on 2.6.3
    # and each one run. What stood here was ``lhalo,rhalo`` positional, and the
    # interesting part is that it was not simply broken: ``cdo sethalo,1,1``
    # exits 0 and widens a 36x18 grid to 38x18, exactly as
    # ``sethalo,east=1,west=1`` does. The binary accepts a two-argument
    # positional form that its own help does not document.
    #
    # It is replaced anyway, because two positional arguments can only ever
    # reach east and west. Measured on 2.6.3:
    #
    #   sethalo,1,1        -> 38x18   (same as east=1,west=1)
    #   sethalo,2,3        -> 41x18   (same as west=2,east=3)
    #   sethalo,-1,-1      -> 34x18   (negative values shrink)
    #   sethalo,1          -> "Parse error!"
    #   sethalo,1,1,1      -> "Parse error!"
    #   sethalo,1,1,1,1    -> "Parse error!"
    #   sethalo,east=1,west=1,south=1,north=1 -> 38x20
    #
    # So the old declaration could express one row of that table and there was
    # no spelling of it — three arguments or four — that reached south or north
    # at all. A user wanting a halo on the top of the grid had no way to ask.
    "sethalo":     (
        _kw("east", _INT, "east", "cells to add on the east edge",
            help="Number of cells to add on this side. Negative removes them: "
                 "sethalo,east=-1,west=-1 narrows a 36x18 grid to 34x18."),
        _kw("west", _INT, "west", "cells to add on the west edge",
            help="Number of cells to add on this side. Negative removes them."),
        _kw("south", _INT, "south", "cells to add on the south edge",
            help="Number of cells to add on this side. Negative removes them. "
                 "Unreachable in the two-argument positional form this "
                 "operator also accepts."),
        _kw("north", _INT, "north", "cells to add on the north edge",
            help="Number of cells to add on this side. Negative removes them. "
                 "Unreachable in the two-argument positional form this "
                 "operator also accepts."),
        _kw("value", _FLOAT, "value", "fill value (default: the missing value)",
            help="What the new cells are filled with. Defaults to the field's "
                 "missing value."),
    ),
    "maskregion":  (_p("regions", _FILE, "regions", "ASCII region file",
                       file_kind=_ft.TEXT),),
    "masklonlatbox":(
        _p("lon1", _FLOAT, "lon1"),
        _p("lon2", _FLOAT, "lon2"),
        _p("lat1", _FLOAT, "lat1"),
        _p("lat2", _FLOAT, "lat2"),
    ),
    "maskindexbox":(
        _p("idx1", _INT, "idx1"),
        _p("idx2", _INT, "idx2"),
        _p("idy1", _INT, "idy1"),
        _p("idy2", _INT, "idy2"),
    ),
    "maskcircle":  (
        _p("lon0", _FLOAT, "lon0"),
        _p("lat0", _FLOAT, "lat0"),
        _p("r", _FLOAT, "radius"),
    ),
    "setclonlatbox":(
        _p("c", _FLOAT, "c", "constant"),
        _p("lon1", _FLOAT, "lon1"),
        _p("lon2", _FLOAT, "lon2"),
        _p("lat1", _FLOAT, "lat1"),
        _p("lat2", _FLOAT, "lat2"),
    ),
    "setcindexbox":(
        _p("c", _FLOAT, "c", "constant"),
        _p("idx1", _INT, "idx1"),
        _p("idx2", _INT, "idx2"),
        _p("idy1", _INT, "idy1"),
        _p("idy2", _INT, "idy2"),
    ),
    "enlarge":     (_p("grid", _GRID, "grid", choices=GRID_PRESETS),),
    "thinout":     (_p("xinc", _INT, "xinc"), _p("yinc", _INT, "yinc")),
    "samplegrid":  (_p("factor", _INT, "resample factor", "2, 3, 4 …"),),
    "samplegridicon": (
        # ICON grid files are NetCDF — the manual's ICON note has CDO reading
        # the ``grid_file_uri`` attribute of one and passing it to ``setgrid``.
        _p("gridfile", _FILE, "ICON grid file", file_kind=_ft.NETCDF),
        _p("factor", _INT, "resample factor", "2, 3, 4 …"),
    ),
    "subgrid":     (
        _p("i0", _INT, "i0"), _p("i1", _INT, "i1"),
        _p("j0", _INT, "j0"), _p("j1", _INT, "j1"),
    ),
    # Both parameters are CDO's own, in CDO's own order: the mask is required
    # and limitCoordsOutput is optional, which is also the order
    # ``test_required_params_come_first`` insists on. The two choices are the
    # only two ``cdo -h reducegrid`` documents, and both were run against 2.6.3:
    # plain gives points=13 nvertex=4 with cellbounds, ``nobounds`` drops the
    # bounds, ``nocoords`` drops lon/lat entirely.
    "reducegrid":  (
        # "mask [STRING] file which holds the mask field" — a dataset, and the
        # module's own example passes a ``.grb``:
        # ``cdo -f nc reducegrid,lsm_gme96.grb temp_gme96.grb out.nc``.
        _p("mask", _FILE, "mask", "file holding the reduction mask",
           file_kind=_ft.DATA),
        _p("limitCoordsOutput", _SELECT, "coordinate output", optional=True,
           choices=("", "nobounds", "nocoords"),
           help="nobounds omits the cell bounds; nocoords omits coordinates "
                "entirely. Leave blank to keep both."),
    ),
    # Undocumented, and left with no parameters because the binary will not say
    # otherwise: ``cdo -h ncopy`` answers "No help available for this
    # operator!" and it has no module. See the note above ``_MODULE_CATEGORY``
    # for why it also stays in Miscellaneous rather than being read as
    # "NetCDF copy" and filed with the Copy module on the strength of its name.
    "ncopy":       (),

    # ---------- Arithmetic ----------
    # The whole Expr language behind one field. ``_EXPR`` is what earns it an
    # editor rather than a line edit — see gui/expression_editor.py. The two
    # ``f`` operators keep taking a *path*: the editor writes the script there,
    # so the inline and the file form are one skill rather than two.
    "expr":        (_p("instr", _EXPR, "instr", "e.g. tas=var*2"),),
    "exprf":       (_p("filename", _EXPR, "filename", "expression script file"),),
    "aexpr":       (_p("instr", _EXPR, "instr", "e.g. tas=var*2"),),
    "aexprf":      (_p("filename", _EXPR, "filename", "expression script file"),),
    "addc":        (_p("c", _FLOAT, "c (constant)"),),
    "subc":        (_p("c", _FLOAT, "c (constant)"),),
    "mulc":        (_p("c", _FLOAT, "c (constant)"),),
    "divc":        (_p("c", _FLOAT, "c (constant)"),),
    "pow":         (_p("value", _FLOAT, "y (exponent)", "o = i^y"),),
    "parmul":      (_p("number", _INT, "number of multiply"),),

    # ---------- Statistical: percentile / running / time-range ----------
    #
    # The parameter is ``pn`` throughout, which is CDO's own spelling and not
    # what this schema used to call it. It was ``p``, and the rename is the
    # point rather than a tidy-up: ``operator_syntax`` prints the parameter
    # name, one module accepts the name as part of the *value*, and a usage line
    # reading "fldpctl ifile ofile p" tells a user to type the one thing that
    # module will not take.
    #
    # **The grammar is per module. It was measured per module.** ``pn`` is
    # neither uniformly positional nor uniformly keyword on 2.6.3:
    #
    #   cdo fldpctl,90     -> ok  |  cdo fldpctl,pn=90   -> ok, identical output
    #                                (cdo diffn between the two is clean)
    #   cdo zonpctl,pn=50  -> Float parameter >pn=50< contains invalid
    #   cdo merpctl,pn=50     character at position 1!
    #   cdo varspctl,pn=50
    #   cdo enspctl,pn=50
    #   cdo monpctl,pn=90  -> same, and so for every temporal pctl operator
    #
    # So Fldstat is declared keyword and everything else positional. The
    # temptation is to normalise the family to one form; four of the six
    # modules would then abort on every call.
    #
    # Why keyword is the right choice for the one module that has it, given
    # that both spellings run: it is the spelling that cannot be confused with
    # the *other* thing Fldstat's single argument slot accepts. ``fldpctl``
    # takes exactly one argument — "Too many arguments! Need 1 found 2" for two
    # of anything — and that one slot will also swallow ``weights=FALSE`` or
    # ``verbose=TRUE``, which is why neither is declared on it below. Spend the
    # slot on ``weights`` and CDO does not complain; it defaults ``pn`` to 0 and
    # returns the field **minimum**, on exit 0:
    #
    #   cdo fldpctl,weights=FALSE v.nc o.nc   -> -5864.667  (== cdo fldmin)
    #   cdo fldpctl,pn=0          v.nc o.nc   -> -5864.667
    #
    # Printing ``pn=<float>`` in the usage line is what steers a user away from
    # that. A blank slot is safe either way — ``cdo fldpctl`` and
    # ``cdo fldpctl,`` are both "Too few arguments! Need 1 found 0", no hang —
    # and ``missing_required_parameters`` refuses it before argv regardless.
    "fldpctl":     (_kw("pn", _FLOAT, "pn (percentile)", "0–100",
                        optional=False, help=_PCTL_HELP),),
    "zonpctl":     (_p("pn", _FLOAT, "pn (percentile)", "0–100",
                       help=_PCTL_HELP),),
    "merpctl":     (_p("pn", _FLOAT, "pn (percentile)", "0–100",
                       help=_PCTL_HELP),),
    "enspctl":     (_p("pn", _FLOAT, "pn (percentile)", "0–100",
                       help=_PCTL_HELP),),
    "timpctl":     (_p("pn", _FLOAT, "pn (percentile)", "0–100",
                       help=_PCTL_HELP),),
    "monpctl":     (_p("pn", _FLOAT, "pn (percentile)", "0–100",
                       help=_PCTL_HELP),),
    "yearpctl":    (_p("pn", _FLOAT, "pn (percentile)", "0–100",
                       help=_PCTL_HELP),),
    "seaspctl":    (_p("pn", _FLOAT, "pn (percentile)", "0–100",
                       help=_PCTL_HELP),),
    "daypctl":     (_p("pn", _FLOAT, "pn (percentile)", "0–100",
                       help=_PCTL_HELP),),
    "hourpctl":    (_p("pn", _FLOAT, "pn (percentile)", "0–100",
                       help=_PCTL_HELP),),
    "ydaypctl":    (_p("pn", _FLOAT, "pn (percentile)", "0–100",
                       help=_PCTL_HELP),),
    "ymonpctl":    (_p("pn", _FLOAT, "pn (percentile)", "0–100",
                       help=_PCTL_HELP),),
    "yseaspctl":   (_p("pn", _FLOAT, "pn (percentile)", "0–100",
                       help=_PCTL_HELP),),
    # The section's second mixed-grammar operator, and the more elaborate one:
    # two positional values, then two keywords. Measured, all four together:
    #
    #   cdo ydrunpctl,90,5,rm=c,pm=r8 in min max out   -> ok
    #   cdo ydrunpctl,90,5,pm=r8      in min max out   -> ok
    #   cdo ydrunpctl,pn=90,5         in min max out   -> abort (see above)
    #
    # ``parameter_tokens`` renders this correctly with no change, because a
    # form decides rendering and never index — but that is an invariant worth a
    # test rather than a claim, and test_statistic_grammars.py has one that
    # builds this exact token.
    "ydrunpctl":   (
        _p("pn", _FLOAT, "pn (percentile)", "0–100", help=_PCTL_HELP),
        _p("nts", _INT, "nts", "window length", help=_YDRUN_NTS_HELP),
        _YDRUN_RM,
        # ``pm`` takes two values and only two: r8 and nrank. Everything else is
        # "Percentile method <x> not available!" — r7, r1, 8 and hf8 were all
        # tried. nrank is the default, measured by it agreeing with an unset pm
        # to the last digit while r8 does not (ydrunpctl,90,11 on a 400-step
        # daily series): unset 3150.333252, nrank 3150.333252, r8 3150.866699.
        _kw("pm", _SELECT, "percentile method", "nrank",
            choices=("", "nrank", "r8"),
            help="Percentile estimator. nrank is the default rank method; r8 "
                 "is the Hyndman-Fan type 8 interpolated estimator and gives a "
                 "different answer — measured 3150.87 against 3150.33 on the "
                 "same field. CDO_PCTL_NBINS does not affect this operator "
                 "under either method (it affects the histogram-based ones "
                 "such as timpctl)."),
    ),
    "timselpctl":  (
        _p("pn", _FLOAT, "pn (percentile)", "0–100", help=_PCTL_HELP),
        _p("nsets", _INT, "nsets"),
        _p("noffset", _INT, "noffset", optional=True),
        _p("nskip", _INT, "nskip", optional=True),
    ),
    "runpctl":     (
        _p("pn", _FLOAT, "pn (percentile)", "0–100", help=_PCTL_HELP),
        _p("nts", _INT, "nts", "window length"),
    ),
    "timselmin":   (_p("nsets", _INT, "nsets"),
                    _p("noffset", _INT, "noffset", optional=True),
                    _p("nskip", _INT, "nskip", optional=True)),
    "timselmax":   (_p("nsets", _INT, "nsets"),
                    _p("noffset", _INT, "noffset", optional=True),
                    _p("nskip", _INT, "nskip", optional=True)),
    "timselsum":   (_p("nsets", _INT, "nsets"),
                    _p("noffset", _INT, "noffset", optional=True),
                    _p("nskip", _INT, "nskip", optional=True)),
    "timselmean":  (_p("nsets", _INT, "nsets"),
                    _p("noffset", _INT, "noffset", optional=True),
                    _p("nskip", _INT, "nskip", optional=True)),
    "timselavg":   (_p("nsets", _INT, "nsets"),
                    _p("noffset", _INT, "noffset", optional=True),
                    _p("nskip", _INT, "nskip", optional=True)),
    "timselvar":   (_p("nsets", _INT, "nsets"),
                    _p("noffset", _INT, "noffset", optional=True),
                    _p("nskip", _INT, "nskip", optional=True)),
    "timselstd":   (_p("nsets", _INT, "nsets"),
                    _p("noffset", _INT, "noffset", optional=True),
                    _p("nskip", _INT, "nskip", optional=True)),
    "timselrange":(_p("nsets", _INT, "nsets"),
                    _p("noffset", _INT, "noffset", optional=True),
                    _p("nskip", _INT, "nskip", optional=True)),
    "timselvar1": (_p("nsets", _INT, "nsets"),
                    _p("noffset", _INT, "noffset", optional=True),
                    _p("nskip", _INT, "nskip", optional=True)),
    "timselstd1": (_p("nsets", _INT, "nsets"),
                    _p("noffset", _INT, "noffset", optional=True),
                    _p("nskip", _INT, "nskip", optional=True)),
    "runmin":      (_p("nts", _INT, "nts", "window length"),),
    "runmax":      (_p("nts", _INT, "nts", "window length"),),
    "runsum":      (_p("nts", _INT, "nts", "window length"),),
    "runmean":     (_p("nts", _INT, "nts", "window length"),),
    "runavg":      (_p("nts", _INT, "nts", "window length"),),
    "runvar":      (_p("nts", _INT, "nts", "window length"),),
    "runstd":      (_p("nts", _INT, "nts", "window length"),),
    "runrange":    (_p("nts", _INT, "nts", "window length"),),
    "runvar1":     (_p("nts", _INT, "nts", "window length"),),
    "runstd1":     (_p("nts", _INT, "nts", "window length"),),
    # The nine Ydrunstat operators: positional ``nts``, then keyword ``rm``.
    # The mixed grammar is the reason these are spelled out rather than left to
    # a loop — and all nine were checked to *validate* their keys, so declaring
    # ``rm`` here offers a control that CDO actually reads:
    #
    #   cdo ydrunmean,5,banana=42 vy.nc o.nc -> Invalid parameter key >banana<!
    #
    # That check is not idle. Twelve operators elsewhere in this section accept
    # any key at all and silently ignore it; see ``_VERT_WEIGHTS`` and the note
    # on the temporal percentile operators in ``_SURPRISING_DEFAULTS``.
    **{name: (_p("nts", _INT, "nts", "window length", help=_YDRUN_NTS_HELP),
              _YDRUN_RM)
       for name in ("ydrunmin", "ydrunmax", "ydrunsum", "ydrunmean",
                    "ydrunavg", "ydrunvar", "ydrunstd", "ydrunvar1",
                    "ydrunstd1")},
    "varspctl":    (_p("pn", _FLOAT, "pn (percentile)", "0–100",
                       help=_PCTL_HELP),),
    "gridboxmin":  (_p("nx", _INT, "nx"), _p("ny", _INT, "ny")),
    "gridboxmax":  (_p("nx", _INT, "nx"), _p("ny", _INT, "ny")),
    "gridboxsum":  (_p("nx", _INT, "nx"), _p("ny", _INT, "ny")),
    "gridboxmean": (_p("nx", _INT, "nx"), _p("ny", _INT, "ny")),
    "gridboxavg":  (_p("nx", _INT, "nx"), _p("ny", _INT, "ny")),
    "gridboxstd":  (_p("nx", _INT, "nx"), _p("ny", _INT, "ny")),
    "gridboxvar":  (_p("nx", _INT, "nx"), _p("ny", _INT, "ny")),
    "gridboxrange":(_p("nx", _INT, "nx"), _p("ny", _INT, "ny")),
    "boxavg":      (_p("nx", _INT, "nx"), _p("ny", _INT, "ny")),

    # ---------- Statistical: the keyword families ----------
    #
    # Fldstat, minus ``fldpctl``. Fifteen names are listed; the module has
    # seventeen operators and the other two are accounted for here:
    #
    # * ``globavg`` is not listed because it is an *alias* — the catalog gives
    #   it the description "--> fldavg" — and ``_resolve_params`` hands an
    #   undeclared alias its target's parameter object rather than a copy. So
    #   it takes these two keys by inheriting them, and the two cannot drift.
    #   (It really does take them: ``cdo globavg,banana=42`` aborts as
    #   "cdo fldavg (Abort): Invalid parameter key >banana<!", naming the
    #   target, which is the same evidence that it is an alias.) What it needed
    #   was not a parameter entry but a category — it was in Miscellaneous, and
    #   the module entry in ``_MODULE_CATEGORY`` is what moves it.
    # * ``fldpctl`` is deliberately absent. It has exactly one argument slot
    #   and that slot belongs to ``pn``; putting ``weights`` in it returns the
    #   field minimum on exit 0. The percentile block above has the numbers.
    #
    # ``fldrms`` is absent for a third reason, worth one line so the omission
    # does not read as an oversight: it is a two-input operator (the catalog
    # gives it ``(2, 1)``), CDO gives it no module at all, and it is not part
    # of the Fldstat page.
    **{name: _FLDSTAT_PARAMS
       for name in ("fldmin", "fldmax", "fldsum", "fldmean", "fldavg",
                    "fldvar", "fldvar1", "fldstd", "fldstd1", "fldrange",
                    "fldskew", "fldkurt", "fldmedian", "fldcount", "fldint")},

    # Vertstat. Ten operators by the manual's count; six of them are declared
    # here and four are not, which is the whole content of this entry.
    #
    # ``vertmax``, ``vertmin``, ``vertrange`` and ``vertsum`` accept
    # ``weights=FALSE`` and exit 0 — and they accept ``banana=42`` and exit 0
    # as well. They do not validate parameter keys at all, so the key is read
    # by nobody and changes nothing. Measured, side by side:
    #
    #   cdo vertsum,banana=42 v3.nc o.nc   -> exit 0
    #   cdo vertmean,banana=42 v3.nc o.nc  -> Invalid parameter key >banana<!
    #
    # Declaring ``weights`` on those four would put a checkbox in the parameter
    # form that silently does nothing — which is a worse failure than the
    # missing checkbox was, because the user gets a plausible answer and a
    # reason to believe they asked for it. They are order statistics and a sum;
    # a thickness weight has no meaning for them, and CDO's not reading the key
    # is consistent rather than a bug.
    #
    # ``vertint`` validates and takes ``weights``, but is a vertical
    # *interpolation* and is left alone here: it is in the module by CDO's
    # accounting and not by the manual's, and its parameters are a level list
    # this work did not measure.
    **{name: _VERT_WEIGHTS
       for name in ("vertmean", "vertavg", "vertvar", "vertvar1",
                    "vertstd", "vertstd1")},

    # Timstat, Daystat, Monstat, Yearstat and Hourstat — the five modules that
    # take ``complete_only``. See ``_COMPLETE_ONLY`` for the four that look as
    # though they should and abort instead.
    #
    # ``timmaxidx``/``timminidx``/``yearmaxidx``/``yearminidx`` are in the list
    # because they are in those modules and take the key; what they return is
    # an index rather than a value, which their help says.
    **{name: _COMPLETE_ONLY
       for name in ("timmin", "timmax", "timsum", "timmean", "timavg",
                    "timvar", "timvar1", "timstd", "timstd1", "timrange",
                    "timminidx", "timmaxidx",
                    "daymin", "daymax", "daysum", "daymean", "dayavg",
                    "dayvar", "dayvar1", "daystd", "daystd1", "dayrange",
                    "monmin", "monmax", "monsum", "monmean", "monavg",
                    "monvar", "monvar1", "monstd", "monstd1", "monrange",
                    "yearmin", "yearmax", "yearsum", "yearmean", "yearavg",
                    "yearvar", "yearvar1", "yearstd", "yearstd1", "yearrange",
                    "yearminidx", "yearmaxidx",
                    "hourmin", "hourmax", "hoursum", "hourmean", "houravg",
                    "hourvar", "hourvar1", "hourstd", "hourstd1",
                    "hourrange")},

    # Zonstat's zonal descriptor, and it is **``zonmean`` alone**.
    #
    # The brief this came from said "zonmean (and the rest of Zonstat where CDO
    # takes it)". Measured, that parenthetical is empty — every other operator
    # in the module refuses a parameter outright:
    #
    #   cdo zonmean,zonal_10 v.nc o.nc    -> ok, ysize 9 -> 18
    #   cdo zonavg,zonal_10  v.nc o.nc    -> Too many arguments! Need 0 found 1
    #   cdo zonmax,zonal_10 / zonsum / zonstd / zonmedian / ... -> the same
    #   cdo zonpctl,zonal_10 v.nc o.nc    -> Float parameter >zonal_10< contains
    #                                        invalid character at position 1!
    #
    # ``zonavg`` refusing it is the one that decides the shape of this entry: a
    # loop over the module would have declared a parameter that fails on
    # thirteen of fourteen operators.
    #
    # Positional, not keyword — ``zonmean,zonaldes=zonal_10`` is "Open failed
    # on zonaldes=zonal_10!", i.e. CDO read the whole string as a filename,
    # which is the same shape of mistake ``remapmean,grid=r6x3`` makes.
    "zonmean": (
        _p("zonaldes", _STR, "zonal description", "zonal_10", optional=True,
           help="Zonal bands to average onto, as a grid description file or a "
                "zonal_<DY> preset where DY is the band width in degrees — "
                "zonal_10 gives 18 bands of 10°, measured. Leave blank to "
                "average onto the input's own latitude rows. This is the one "
                "Zonstat operator that takes a parameter; the others abort "
                "with 'Need 0 found 1'."),
    ),

    # ---------- Regression ----------
    # All five of the section, all the same one parameter — see
    # ``_REGRESSION_PARAMS`` for the grammar and the measurements. Listed one
    # per line rather than built with a comprehension so that grepping for an
    # operator name finds it here, which is how every other entry in this table
    # behaves.
    "detrend":     _REGRESSION_PARAMS,
    "regres":      _REGRESSION_PARAMS,
    "trend":       _REGRESSION_PARAMS,
    "addtrend":    _REGRESSION_PARAMS,
    "subtrend":    _REGRESSION_PARAMS,

    # ---------- Interpolation ----------
    "remapbil":    (_p("grid", _GRID, "grid", choices=GRID_PRESETS),),
    "remapbic":    (_p("grid", _GRID, "grid", choices=GRID_PRESETS),),
    "remapcon":    (_p("grid", _GRID, "grid", choices=GRID_PRESETS),),
    "remapcon2":   (_p("grid", _GRID, "grid", choices=GRID_PRESETS),),
    # remapdis has an UNDOCUMENTED optional second positional integer. Neither
    # the manual nor ``cdo -h remapknn`` mentions it; it was found by running it.
    # Measured on 2.6.3, all four against an 18x9 source:
    #
    #   cdo remapdis,r36x18 lev5.nc out.nc
    #     -> "Distance-weighted averaged weights from lonlat (18x9) to ..."
    #   cdo remapdis,r36x18,6 lev5.nc out.nc
    #     -> "Distance-weighted averaged (k=6) weights from lonlat (18x9) to ..."
    #   cdo remapdis,grid=r36x18,k=6 lev5.nc out.nc
    #     -> Abort: "Integer parameter >k=6< contains invalid character at
    #        position 1!"   (so the keyword spelling of the same thing is out)
    #   cdo remapdis,r36x18,6,2 lev5.nc out.nc
    #     -> Abort: "Too many arguments! Need 1 found 3."
    #
    # The last message is worth not believing too literally: it says "Need 1"
    # while two arguments are accepted, so the arity in the error is the
    # documented arity rather than the parsed one.
    #
    # ``k``'s default here is 4, not the 1 that remapknn defaults to — from
    # ``cdo -h remapknn``: "remapdis,<targetgrid> corresponds to
    # remapknn,grid=<targetgrid>,k=4,kmin=1,weighted=dist,extrapolate=true".
    "remapdis":    (
        _p("grid", _GRID, "grid", choices=GRID_PRESETS),
        _p("k", _INT, "k", "number of neighbours (optional, default 4)",
           optional=True,
           help="Undocumented second positional argument, found by running it: "
                "cdo remapdis,r36x18,6 prints 'Distance-weighted averaged "
                "(k=6) weights'. It is positional and only positional — "
                "remapdis,grid=r36x18,k=6 aborts with 'Integer parameter >k=6< "
                "contains invalid character at position 1!'. Left blank CDO "
                "uses k=4, which is what remapdis is defined as. Note gendis, "
                "the weight-generating twin, does NOT accept this."),
    ),
    # remapnn takes exactly ONE positional grid and rejects the keyword form.
    # This spec was already correct; the comment is here because the manual
    # invites precisely the wrong edit. ``cdo -h remapknn`` says "remapnn,
    # <targetgrid> corresponds to remapknn,grid=<targetgrid>,extrapolate=true",
    # which reads as an invitation to spell it the keyword way. It is not one.
    # Measured on 2.6.3:
    #
    #   cdo remapnn,grid=r36x18 lev5.nc out.nc
    #     -> Abort: "Open failed on grid=r36x18!"  (the whole string is taken
    #        as a *filename*, so this fails as a missing file rather than as a
    #        syntax error — the most confusing shape the mistake could have)
    #   cdo remapnn,r36x18,true lev5.nc out.nc
    #     -> Abort: "Too many arguments! Need 1 found 2."
    #   cdo remapnn,r36x18 lev5.nc out.nc          -> exit 0
    #
    # So: one positional grid, no second parameter, no keyword spelling.
    "remapnn":     (_p("grid", _GRID, "grid", choices=GRID_PRESETS),),
    "remaplaf":    (_p("grid", _GRID, "grid", choices=GRID_PRESETS),),
    "remapycon":   (_p("grid", _GRID, "grid", choices=GRID_PRESETS),),
    # ---------- Remapstat ----------
    # The thirteen operators CDO documents under Statistic and this app files
    # under Interpolation — see the block at the end of ``_MODULE_CATEGORY``
    # for that decision and its cost. What they share is a *target grid*, and
    # what the module's own page warns about is the empty-cell case, so both go
    # in the help rather than being left to the run.
    #
    # Positional, like every other remap parameter: ``cdo remapmean,grid=r6x3``
    # is "Open failed on grid=r6x3!" — CDO read the whole string as a filename.
    # Measured against ``cdo remapmean,r6x3``, which gives an xsize 6, ysize 3
    # grid.
    **{name: (_p("grid", _GRID, "grid", choices=GRID_PRESETS,
                 help=_REMAPSTAT_HELP),)
       for name in ("remapsum", "remapavg", "remapmin", "remapmax",
                    "remapmean", "remapmedian", "remaprange", "remapstd",
                    "remapstd1", "remapvar", "remapvar1", "remapskew",
                    "remapkurt")},
    # The twelve entries that used to sit here — remapavg through remapkurt,
    # captioned "the rest of the first-order conservative family" — were the
    # same declaration twelve times and are now the comprehension above, which
    # also carries the module's empty-cell warning in its help. They are not
    # the conservative family: that is remapcon/remapcon2/gencon, in the
    # Interpolation section. These thirteen are CDO's Remapstat module.
    "remapavgtest":(_p("grid", _GRID, "grid", choices=GRID_PRESETS),),
    "remapycon2test":(_p("grid", _GRID, "grid", choices=GRID_PRESETS),),
    "testfield":   (_p("grid", _GRID, "grid", choices=GRID_PRESETS),),
    "testcellsearch": (
        _p("sgrid", _GRID, "source grid", choices=GRID_PRESETS),
        _p("tgrid", _GRID, "target grid", choices=GRID_PRESETS),
    ),
    "testpointsearch": (
        _p("sgrid", _GRID, "source grid", choices=GRID_PRESETS),
        _p("tgrid", _GRID, "target grid", choices=GRID_PRESETS),
    ),
    # The k-nearest-neighbour trio takes key=value pairs rather than a bare
    # grid, and every one of its six parameters is keyword-form. Measured on
    # 2.6.3 against an 18x9 source:
    #
    #   cdo remapknn,r36x18 lev5.nc out.nc
    #     -> "missing '=' in key/value string: >r36x18<" / Abort: "Parse error!"
    #
    # so ``grid=`` is required spelling and not optional sugar, which is why
    # ``grid`` is declared _KEYWORD like the rest rather than positional.
    #
    #   cdo remapknn,grid=r36x18 lev5.nc out.nc
    #     -> "K-nearest neighbor (k=1 weighted=dist) weights from lonlat (18x9)
    #        to lonlat (36x18) grid"            — confirms both stated defaults
    #   cdo remapknn,grid=r36x18,k=4,kmin=2,weighted=gauss,gauss_scale=0.2,\
    #       extrapolate=true lev5.nc out.nc    -> exit 0, all six accepted
    #
    # These replace a single free-text ``knnopts`` string the user had to spell
    # by hand, with no grid picker and no validation of any of it.
    #
    # ``map3d`` is deliberately NOT here, and that is a straight
    # manual-versus-binary contradiction rather than an omission. ``cdo -h
    # remapknn`` lists "map3d (BOOL) Generate all mapfiles of the first 3D
    # field [default: false]" under this module's PARAMETERS, and its genknn
    # prose describes the numbered mapfiles. The binary rejects it on both:
    #
    #   cdo genknn,grid=r36x18,map3d=true lev5.nc gk.nc
    #     -> Abort: "Invalid parameter key >map3d<!"
    #   cdo remapknn,grid=r36x18,map3d=true lev5.nc rk.nc
    #     -> Abort: "Invalid parameter key >map3d<!"
    #   cdo intgridknn,grid=r36x18,map3d=true lev5.nc out.nc
    #     -> Abort: "Invalid parameter key >map3d<!"
    #
    # map3d is real, but only on the positional-grid gen* family below.
    #
    # intgridknn was probed separately rather than assumed to match its two
    # siblings — this section has operators in one module with opposite
    # grammars — and it does match: bare positional aborts "Parse error!",
    # grid= works, and all six keywords together exit 0.
    **{name: (
        _kw("grid", _GRID, "grid", "r360x180", optional=False,
            choices=GRID_PRESETS,
            help="Target grid, as a preset name or a grid description file. "
                 "Required, and required in keyword form: cdo remapknn,r36x18 "
                 "aborts with \"missing '=' in key/value string\"."),
        _kw("k", _INT, "k", "1",
            help="Number of nearest neighbours. Default 1, confirmed by the "
                 "run banner 'K-nearest neighbor (k=1 weighted=dist)' when it "
                 "is left unset."),
        _kw("kmin", _INT, "kmin", "same as k",
            help="Minimum number of neighbours that must be found. Defaults to "
                 "k. Not validated against k by CDO: remapknn,grid=r36x18,k=2,"
                 "kmin=9 was measured exiting 0 with no complaint."),
        # Five choices, not the three the 2.6.3 manual tabulates. The manual's
        # own table under `cdo -h remapknn` lists dist/avg/gauss only; the
        # binary enumerates its real set when it refuses one:
        #   cdo remapknn,grid=r36x18,weighted=bogus lev5.nc out.nc
        #     -> Abort: "method=bogus unsupported
        #               (available: avg|dist|linear|gauss|rbf)"
        # linear and rbf are undocumented here and were taken from that message.
        _kw("weighted", _SELECT, "weighting", "dist",
            choices=("dist", "avg", "gauss", "linear", "rbf"),
            help="How the k neighbours are combined. Default dist (inverse "
                 "distance). The choice list comes from the binary, not the "
                 "manual: the manual documents dist/avg/gauss, and CDO's own "
                 "refusal of a bad value names 'avg|dist|linear|gauss|rbf'. "
                 "linear and rbf are undocumented in 2.6.3."),
        _kw("gauss_scale", _FLOAT, "gauss scale", "0.1",
            help="Scaling factor for the Gaussian filter. Only meaningful with "
                 "weighted=gauss; with any other weighting CDO accepts it and "
                 "ignores it — remapknn,grid=r36x18,gauss_scale=0.5 was "
                 "measured exiting 0 and still printing 'weighted=dist'."),
        _kw("extrapolate", _BOOL, "extrapolate", "false",
            help="Fill target points outside the source grid. Default false. "
                 "Note REMAP_EXTRAPOLATE in this operator's environment "
                 "settings does the same job and is enabled by default for "
                 "circular grids, so the effective default depends on the "
                 "source grid."),
    ) for name in ("genknn", "remapknn", "intgridknn")},
    # Every weight file in this section is NetCDF, and CDO says so where it
    # documents them: "The remap type and the interpolation weights of one
    # input grid are read from a NetCDF file … should follow the [SCRIP]
    # convention" (Remap). The same goes for the SCRIP file writeremapscrip
    # writes — SCRIP "grid description is stored in NetCDF" (§1.5.2). A GRIB
    # file in any of these slots is a failed run, not a slow one.
    "verifyweights": (_p("weights", _FILE, "weights", "remap weight file",
                         file_kind=_ft.NETCDF),),
    "writeremapscrip": (
        _p("weights", _FILE, "weights", "remap weight file to read",
           file_kind=_ft.NETCDF),
        # The one it writes, so it is not required to exist beforehand — and
        # ``writes`` so the execution layer treats it as the output it is: the
        # same aliasing, relocation and clean-up-on-failure an ofile gets.
        _p("scrip", _FILE, "scrip", "SCRIP file to write",
           reads=False, writes=True, file_kind=_ft.NETCDF),
    ),
    # The weight-generating family, and the first place in the schema where one
    # operator token MIXES two grammars: a positional grid followed by a keyword
    # map3d. Measured on 2.6.3 for each of genbil, genbic, gencon, genlaf,
    # gennn and gendis — all six behave identically:
    #
    #   cdo genbil,r36x18 lev5.nc w1.nc            -> exit 0
    #   cdo genbil,r36x18,map3d=true lev5.nc w1.nc -> exit 0
    #   cdo genbil,r36x18,true lev5.nc w1.nc
    #     -> "missing '=' in key/value string: >true<" / Abort: "Parse error!"
    #
    # so the second parameter must be spelled map3d=true and the first must not
    # be spelled grid=. ``cdo -h genbil`` agrees, and states the mixed form in
    # its own synopsis: "cdo genbil,targetgrid[,map3d] infile outfile".
    #
    # Nothing in parameter_tokens needed changing for this: form is a
    # per-parameter fact applied per index, so a positional followed by a
    # keyword already renders correctly. test/test_interpolation_tokens.py pins
    # that, since it is the first operator to depend on it.
    #
    # gendis is NOT given remapdis's undocumented positional k, and that is the
    # measurement rather than an oversight — the two operators of one module
    # have opposite grammars here:
    #
    #   cdo gendis,r36x18,6 lev5.nc w1.nc
    #     -> "missing '=' in key/value string: >6<" / Abort: "Parse error!"
    #
    # while remapdis,r36x18,6 is accepted and prints "(k=6)". genknn is likewise
    # absent from this group: it is fully keyword-form and lives with remapknn
    # above (cdo genknn,r36x18 -> "Parse error!").
    #
    # ``map3d=true`` changes the output arity at runtime — CDO then writes
    # <outfile><00001>.nc rather than the path it was given. That is handled in
    # nc_integration by ``writes_output_prefix``; see this module's
    # :func:`writes_output_prefix` for the measurement.
    **{name: (
        _p("grid", _GRID, "grid", choices=GRID_PRESETS),
        _kw("map3d", _BOOL, "map3d", "false",
            help="Generate one weight file per distinct mask of the first 3D "
                 "field, instead of a single weight file. This CHANGES WHERE "
                 "THE OUTPUT GOES: CDO then treats the output path as a prefix "
                 "and writes <outfile><xxx>.nc with a five-digit counter. "
                 "Measured on 2.6.3: 'cdo genbil,r36x18,map3d=true lev5.nc "
                 "w1.nc' wrote w1.nc00001.nc — not w1.nc — and against a source "
                 "whose missing-value mask differs per level it wrote three "
                 "files, w1.nc00001.nc through w1.nc00003.nc. Spelled "
                 "map3d=true; a bare 'true' is a parse error."),
    ) for name in ("genbil", "genbic", "gencon", "genlaf", "gennn", "gendis",
                   "genycon")},
    # gencon2 and genscon keep the bare grid because they could NOT be probed:
    # neither is installed on this build. "cdo gencon2,r36x18 lev5.nc g2.nc"
    # answers "Operator >gencon2< not found! Similar operators are: gencon
    # gennn genycon genycon2test", and genscon the same. Declaring map3d on
    # them would be inferring a grammar from a sibling, which is exactly what
    # this section punishes — genycon is in the group above because it *was*
    # run (it is an alias of gencon, "genycon --> gencon" in cdo --operators,
    # and 'cdo genycon,r36x18,map3d=true lev5.nc wg.nc' wrote wg.nc00001.nc).
    "gencon2":     (_p("grid", _GRID, "grid", choices=GRID_PRESETS),),
    "genscon":     (_p("grid", _GRID, "grid", choices=GRID_PRESETS),),
    "remap":       (
        _p("grid", _GRID, "grid", choices=GRID_PRESETS),
        # "weights [STRING] Interpolation weights (SCRIP NetCDF file)". The
        # one place in the schema where two file parameters of one operator
        # want different formats, and the reason the browse button could not
        # go on offering the same nine filters to both.
        _p("weights", _FILE, "weights", "pre-computed weight file",
           file_kind=_ft.NETCDF),
    ),
    "interpolate": (_p("grid", _GRID, "grid", choices=GRID_PRESETS),),
    "intgridbil":  (_p("grid", _GRID, "grid", choices=GRID_PRESETS),),
    "intgriddis":  (_p("grid", _GRID, "grid", choices=GRID_PRESETS),),
    "intgridnn":   (_p("grid", _GRID, "grid", choices=GRID_PRESETS),),
    "intgrid":     (_p("grid", _GRID, "grid", choices=GRID_PRESETS),),
    "intgridtraj":(_p("grid", _GRID, "grid"),),
    # ``vct`` is an ASCII table, not a NetCDF file, and the browse filter must
    # not restrict to *.nc. From ``cdo -h remapeta``: "vct (STRING) File name of
    # an ASCII dataset with the vertical coordinate table". Each line is a level
    # index followed by its A (Pa) and B (dimensionless) hybrid coefficients:
    #
    #   0    0.00000000    0.00000000
    #   1 2000.00000000    0.00000000
    #   2 4000.00000000    0.00000000
    #  ...
    #  19    0.00000000    1.00000000
    #
    # with one more line than there are layers, because the coefficients are at
    # the layer *interfaces*.
    #
    # ``oro`` stays on remapeta alone. ``cdo -h remapeta`` gives one synopsis,
    # "cdo remapeta,vct[,oro] infile outfile", and covers all three operators;
    # whether remapeta_s and remapeta_z also take a second parameter could NOT
    # be determined from the binary, because CDO opens the vct before it checks
    # the argument count — remapeta_s,novct.txt,nooro.nc aborts "Open failed on:
    # novct.txt", exactly as the one-parameter and three-parameter calls do. So
    # the arity of _s and _z is left as it was rather than widened on the
    # strength of a shared help page.
    "remapeta": (
        _p("vct", _FILE, "vct", "ASCII vertical coordinate table",
           file_kind=_ft.TEXT,
           help="Plain-text hybrid coefficient table, NOT a NetCDF file: one "
                "line per hybrid layer interface, each 'index A B' with A in "
                "Pa and B dimensionless, e.g. '1 2000.00000000 0.00000000'. "
                "There is one more line than there are layers."),
        # The other half of the pair the note above is about: "oro [STRING]
        # File name with the orography (surf. geopotential) of the target
        # dataset", so this one *is* a dataset while vct beside it is not.
        _p("oro", _FILE, "oro", "target orography (optional)", optional=True,
           file_kind=_ft.DATA,
           help="Surface geopotential of the target dataset. Optional."),
    ),
    **{name: (
        _p("vct", _FILE, "vct", "ASCII vertical coordinate table",
           file_kind=_ft.TEXT,
           help="Plain-text hybrid coefficient table, NOT a NetCDF file: one "
                "line per hybrid layer interface, each 'index A B' with A in "
                "Pa and B dimensionless, e.g. '1 2000.00000000 0.00000000'. "
                "There is one more line than there are layers."),
    ) for name in ("remapeta_s", "remapeta_z")},
    "zs2zl":       (_p("levels", _STR, "levels", "depth levels in metres"),),
    "zs2zlx":      (_p("levels", _STR, "levels", "depth levels in metres"),),
    # intlevel is the first operator in the schema whose form is not a
    # per-parameter fact, and the reason all four are declared _KEYWORD.
    #
    # CDO accepts the level list either way, but the moment ANY other parameter
    # is set the whole token must be keyword-spelled. Measured on 2.6.3:
    #
    #   cdo intlevel,150,300,700 lev5.nc out.nc              -> exit 0
    #   cdo intlevel,level=150,300,700 lev5.nc out.nc        -> exit 0
    #   cdo intlevel,150,300,extrapolate=true lev5.nc out.nc
    #     -> Abort: "Float parameter >extrapolate=true< contains invalid
    #        character at position 1!"
    #   cdo intlevel,level=150,300,700,extrapolate=true lev5.nc out.nc -> exit 0
    #
    # Declaring all four keyword and always emitting ``level=`` is what makes
    # that mode switch unreachable: the keyword spelling works on its own, so
    # there is no call this can build that the positional spelling would have
    # accepted and this does not. The alternative — teaching parameter_tokens a
    # mode switch — would have cost it the indexing invariant its docstring
    # depends on, for no behaviour the user can reach.
    #
    # ``level=`` carries a comma-separated list inside ONE value, and the
    # comma-join in _resolve_operator_call reproduces it byte-for-byte:
    # parameter_tokens emits the single token "level=150,300,700", which
    # ','.join then places verbatim, giving "intlevel,level=150,300,700" —
    # the exact string measured above. test/test_interpolation_tokens.py pins it.
    #
    # THE TRAP, and why "always emit level=" is not the whole rule: level and
    # zdescription are mutually exclusive, not merely alternatives.
    #
    #   cdo intlevel,level=150,zdescription=zax5.txt lev5.nc out.nc
    #     -> Abort: "Parameter zdescription and level can't be mixed!"
    #   cdo intlevel,zdescription=zax5.txt lev5.nc out.nc    -> exit 0
    #
    # A blank keyword parameter is simply absent from the token, so filling in
    # one or the other produces a call CDO accepts; filling in both produces
    # CDO's own message above, which names the conflict precisely.
    #
    # Also measured, and the reason ``level`` is not marked optional: with
    # neither set, CDO does not abort cleanly at all —
    #   cdo intlevel,extrapolate=true lev5.nc out.nc
    #     -> "cdi error, zaxisCreate, zaxis.c, line 267 / assertion `size` failed"
    **{name: (
        _kw("level", _STR, "levels", "150,300,700", optional=False,
            help="Comma-separated list of target levels, in the source file's "
                 "own level units. Always sent as level=... because mixing the "
                 "bare list with any other parameter is a parse error: "
                 "intlevel,150,300,extrapolate=true aborts with 'Float "
                 "parameter >extrapolate=true< contains invalid character at "
                 "position 1!'. Leave this blank only if you are using a "
                 "Z-axis description file instead — the two cannot both be set."),
        # kind=file, reads=True is what gets this aliased and existence-checked
        # by _alias_file_parameters before CDO is spawned.
        _kw("zdescription", _FILE, "Z-axis description file",
            "path to a zaxis description", file_kind=_ft.TEXT,
            help="A Z-axis description file giving the target levels, instead "
                 "of typing them. Cannot be combined with the level list: CDO "
                 "aborts with \"Parameter zdescription and level can't be "
                 "mixed!\" if both are set."),
        _kw("zvarname", _STR, "3D source coordinate variable", "e.g. zg",
            help="Use this 3D coordinate variable as the vertical source "
                 "coordinate instead of the file's 1D coordinate variable."),
        _kw("extrapolate", _BOOL, "extrapolate", "false",
            help="Fill target layers outside the source layer range with the "
                 "nearest source layer, instead of leaving them missing."),
    ) for name in ("intlevel", "intlevelx")},
    # Pressure levels are in PASCAL and height levels in METRES, and getting it
    # wrong is not an error: a user who types 925,850,500 meaning hPa gets a
    # successful run and levels 500-1000 times too low. Every placeholder below
    # is therefore the manual's own example — 92500,85000,50000,20000 — rather
    # than a description of the unit, because the example is the thing that
    # makes the magnitude obvious at a glance.
    **{name: (
        _p("plevels", _STR, "pressure levels", "92500,85000,50000,20000",
           help="Comma-separated target pressure levels in PASCAL, not hPa. "
                "The manual's own example is 92500,85000,50000,20000. Typing "
                "hPa values here runs successfully and interpolates onto "
                "levels a hundred times too low."),
    ) for name in ("ml2pl", "ml2plx", "ap2pl", "ap2plx")},
    **{name: (
        _p("hlevels", _STR, "height levels", "500,1000,2000,5000",
           help="Comma-separated target height levels in METRES."),
    ) for name in ("ml2hl", "ml2hlx", "ap2hl", "ap2hlx")},
    "inttime":     (
        _p("date", _STR, "date", "start date (YYYY-MM-DD)"),
        # CDO documents hh:mm:ss. Both spellings were measured parsing on
        # 2.6.3 — 'cdo inttime,2000-01-01,12:00,12hour' and
        # 'cdo inttime,2000-01-01,12:00:00,12hour' both exit 0 — so this is the
        # documented spelling rather than the only accepted one.
        _p("time", _STR, "time", "hh:mm:ss",
           help="Start time. CDO documents hh:mm:ss; hh:mm is also accepted on "
                "2.6.3, but the documented spelling is what is shown here."),
        _p("inc", _STR, "inc", "e.g. 6hour (optional, default 0hour)",
           optional=True,
           help="Time increment, e.g. 6hour or 1day. Default 0hour. Measured: "
                "the parameter may be omitted entirely and the run exits 0."),
    ),
    "intntime":    (_p("n", _INT, "n", "number of timesteps"),),
    # Both grammars the manual gives, because only one was being shown. All
    # three forms were measured accepted on 2.6.3 against two single-year
    # files: 'intyear,2001,2002', 'intyear,2001/2002' and 'intyear,2001/2002/1'
    # each wrote yr2001.nc and yr2002.nc.
    "intyear":     (
        _p("years", _STR, "years", "1986,1987,1988,1989 or 1981/2010[/2]",
           help="Either a comma-separated list of years (the manual's own "
                "example is intyear,1986,1987,1988,1989) or a first/last range "
                "with an optional increment, 1981/2010 or 1981/2010/2. All "
                "three forms were measured accepted on 2.6.3. One output file "
                "is written per year, named <obase><year>.nc."),
    ),

    # ---------- Transformation ----------
    #
    # The two halves of this section spell the *same-looking* slot with two
    # different and mutually incompatible grammars, and the manual documents
    # both as ``[,type]``/``[,gridtype]`` without saying so. Every line below
    # was run on 2.6.3 (macOS, no LIBFFTW3) against a T21 file built with
    # ``cdo gp2sp -random,t21grid,1`` and a Gaussian u/v pair.
    #
    #   Spectral module  (sp2gp, gp2sp, sp2gpl, gp2spl): a bare word, or
    #                    ``type=<word>``, or ``trunc=<n>`` — and exactly one of
    #                    them.
    #   Wind module      (dv2uv, uv2dv, dv2uvl, uv2dvl): a bare word only.
    #                    ``type=`` and ``trunc=`` are both rejected here.
    #
    # Declared as **one** parameter for the Spectral four rather than a
    # ``type`` and a ``trunc``, because the schema has no mutual exclusion and
    # ``parameter_tokens`` preserves blanks in positional slots on purpose (see
    # its docstring). Two parameters with the first left empty would emit
    # ``sp2gp,,trunc=42``, and CDO counts the empty slot:
    #
    #   cdo sp2gp,,trunc=42 spec.nc o.nc     -> (Abort): Too many parameters
    #   cdo sp2gp,type=linear,trunc=42 …     -> (Abort): Too many parameters
    #   cdo sp2gp,trunc=42, spec.nc o.nc     -> (Abort): sp2gp: ',' is not
    #                                           followed by any operator argument
    #
    # A mutual-exclusion concept was considered and not added: it would have to
    # be understood by ``parameter_tokens``, ``missing_required_parameters``,
    # ``invalid_parameter_values`` and both form-building surfaces, to express
    # something one placeholder says in eight words for four operators.
    **{name: (_p("type", _STR, "type", "quadratic | linear | cubic | trunc=<n>",
                 optional=True,
                 help="One value only — CDO takes a bare type word, "
                      "type=<word> or trunc=<n>, and answers \"Too many "
                      "parameters\" to any two of them. A bare integer is read "
                      "as a *type*, never as a truncation: cdo sp2gp,42 is "
                      "\"(Abort): Unsupported type: 42\". gridtype= is not a "
                      "spelling either — cdo sp2gp,gridtype=linear is "
                      "\"Unsupported type: gridtype=linear\". Left empty the "
                      "operator runs quadratic, which is its documented "
                      "default and the only type that works on a CDO built "
                      "without FFTW3."),)
       for name in ("sp2gp", "sp2gpl", "gp2sp", "gp2spl")},

    # The wind pair, and the trap this section is worth stating for: the
    # keyword spelling the *Spectral* page documents is refused here.
    #
    #   cdo uv2dv,linear uv.nc o.nc       -> exit 0, T31 output
    #   cdo uv2dv,type=linear uv.nc o.nc  -> (Abort): Unsupported type: type=linear
    #   cdo uv2dv,gridtype=linear …       -> (Abort): Unsupported type: gridtype=linear
    #   cdo uv2dvl,trunc=10 uv.nc o.nc    -> (Abort): Unsupported type: trunc=10
    #   cdo uv2dv,42 uv.nc o.nc           -> (Abort): Unsupported type: 42
    #
    # The ``l`` variants take the same slot rather than being fixed at linear —
    # measured, ``cdo uv2dvl,quadratic`` and ``cdo gp2spl,quadratic`` both exit
    # 0 and produce the quadratic truncation — so they are declared alike.
    **{name: (_p("gridtype", _SELECT, "gridtype", "quadratic",
                 optional=True,
                 choices=("quadratic", "linear", "cubic"),
                 help="A bare word, and only a bare word. Both key=value "
                      "spellings are rejected here even though the Spectral "
                      "module (sp2gp, gp2sp) accepts type= — cdo "
                      "uv2dv,type=linear is \"(Abort): Unsupported type: "
                      "type=linear\". Empty means quadratic. On a CDO without "
                      "FFTW3, linear and cubic abort in the spectral-to-"
                      "gridpoint direction (dv2uv) and work in the other "
                      "(uv2dv)."),)
       for name in ("dv2uv", "dv2uvl", "uv2dv", "uv2dvl")},

    # Positional and digits-only. The keyword spelling its sibling module takes
    # is an abort here, which is the same disagreement one module over:
    #   cdo sp2sp,10 spec.nc o.nc        -> exit 0, T10 output
    #   cdo sp2sp,trunc=10 spec.nc o.nc  -> (Abort): parameter truncation must
    #                                       comprise only digits [0-9]!
    # Extra arguments are accepted and ignored rather than refused:
    #   cdo sp2sp,10,linear spec.nc o.nc -> exit 0, and behaves as sp2sp,10.
    "sp2sp":       (_p("trunc", _INT, "trunc", "truncation"),),
    # Positional wave numbers, and the same digits-only parser:
    #   cdo spcut,1 / spcut,1,2,3        -> exit 0
    #   cdo spcut,wnums=1 spec.nc o.nc   -> (Abort): Integer parameter
    #                                       >wnums=1< contains invalid
    #                                       character at position 1!
    "spcut":       (_p("wnums", _STR, "wnums", "wave numbers"),),

    # ---------- File operations ----------
    #
    # Every form below was read off the 2.6.3 manual *and* run, because the two
    # disagree more here than anywhere else in the app, and a wrong form is not
    # always a wrong-looking command. The three disagreements, all measured:
    #
    #   * The manual types ``printparam``, ``printbits`` and ``skip_same_time``
    #     as BOOL, which reads as a bare switch. This build's parser refuses a
    #     bare key — ``cdo pack,printparam`` is "missing '=' in key/value
    #     string: >printparam<" — and refuses ``yes`` as a value. They are
    #     keywords taking ``true``/``false``/``1``/``0``.
    #   * ``format`` is documented under Splittime, whose Name line lists six
    #     operators. Only ``splitmon`` accepts it: the other five answer "Too
    #     many arguments! Need 0 found 1." It is also *positional* —
    #     ``splitmon,format=%B`` exits 0 and writes ``monformat=January.nc``,
    #     because the whole string is the strftime format.
    #   * ``swap`` is documented as STRING and is the section's one true flag:
    #     ``cdo splitname,swap infile out`` writes ``randomout.nc``, obase and
    #     xxx swapped.
    #
    # The one thing nothing here declares is a CDO *global* option. ``-f``,
    # ``-b``, ``-z``, ``-r``, ``-O`` and ``--chunkspec`` are not operator
    # parameters and have no slot in the command this app builds; ``copy``,
    # ``pack``, ``unpack``, ``bitrounding``, ``setchunkspec`` and ``setfilter``
    # are all documented with one. See ``_MISSING_GLOBAL_OPTIONS`` below, which
    # is what tells the user so on all three surfaces.

    # Copy — copy, clone, cat, szip take no parameters. ``cdo -h copy`` prints
    # no PARAMETERS section and all four run bare. Not declared rather than
    # declared empty, which is the same thing to ``_PARAM_SPECS.get(op, ())``.

    # An output, not an input: ``cdo -h tee`` calls it "Destination filename for
    # the copy of the input file". Requiring it to exist would refuse every
    # correct use of the operator — and it is the reason ``reads`` exists.
    # Named ``outfile2`` because that is what the binary and the manual call it.
    "tee":         (_p("outfile2", _FILE, "outfile2",
                       "second file to write", reads=False, writes=True,
                       file_kind=_ft.DATA),),

    "pack":        (
        _kw("printparam", _BOOL, "print pack parameters",
            help="Print add_offset and scale_factor per variable to stdout."),
        _kw("filename", _FILE, "parameter file",
            "file of per-variable pack parameters", file_kind=_ft.TEXT,
            help="One line per variable: "
                 "name=<> add_offset=<> scale_factor=<>"),
    ),
    # unpack takes none.

    # Both required — the synopsis is ``setchunkspec,parameter``, not
    # ``[,parameter]``. Both read the file, so both are checked for existence.
    #
    # Measured caveat, undocumented: the value in that file must be *quoted*.
    # The outer key=value parser splits on ``=`` and ``,``, which are exactly
    # the characters the documented inner grammars need, so ``random=t=1`` is
    # "Missing value for parameter key >random<" and ``random=307,9`` is "Too
    # many values for parameter key >random<". ``random="t=1"`` and
    # ``random="307,9"`` both parse. Said to the user in _SURPRISING_DEFAULTS.
    "setchunkspec": (_kw("filename", _FILE, "chunk specification file",
                         "varname=\"<chunkspec>\" per line", optional=False,
                         file_kind=_ft.TEXT),),
    "setfilter":    (_kw("filename", _FILE, "filter specification file",
                         "varname=\"<filterspec>\" per line", optional=False,
                         file_kind=_ft.TEXT),),

    "bitrounding": (
        _kw("inflevel", _FLOAT, "inflevel", "0 - 1 [default: 0.9999]",
            help="Fraction of the information in the mantissa bits to keep."),
        _kw("addbits", _INT, "addbits", "[default: 0]"),
        _kw("minbits", _INT, "minbits", "[default: 1]"),
        _kw("maxbits", _INT, "maxbits", "[default: 23]"),
        _kw("numsteps", _INT, "numsteps", "1 = first timestep only"),
        _kw("numbits", _INT, "numbits",
            help="Sets the number of significant bits outright, in place of "
                 "calculating one from inflevel."),
        _kw("printbits", _BOOL, "print numbits",
            help="Print the maximum numbits per variable of the first "
                 "timestep to stdout."),
        _kw("filename", _FILE, "numbits file", "name=numbits per line",
            file_kind=_ft.TEXT,
            help="Per-variable numbits. Variables absent from the file are "
                 "still calculated."),
    ),

    # replace, mergegrid and merge take none; confirmed against `cdo -h`, which
    # prints no PARAMETERS section for the first two and lists the Merge
    # module's two under mergetime's synopsis only (``merge`` has no
    # ``[,parameters]``).
    "mergetime":   (
        _kw("skip_same_time", _BOOL, "skip duplicate timestamps",
            help="Skip consecutive timesteps that carry the same timestamp."),
        _kw("names", _SELECT, "names", optional=True,
            choices=("", "union", "intersect"),
            help="union fills a missing variable with missing values; "
                 "intersect keeps only the variables every input has."),
    ),

    "duplicate":   (_p("ndup", _INT, "ndup", "[default: 2]", optional=True),),

    # The Split module's nine, all taking the same two. ``splittabnum`` used to
    # declare a ``tabnums`` parameter instead: it appears in no CDO 2.6.3
    # documentation, and the binary answers ``cdo splittabnum,5 infile obase``
    # with "Unknown parameter: >5<". It was a field that could only ever break
    # the run it was filled into. Deleted.
    **{name: _SPLIT_PARAMS for name in (
        "splitcode", "splitparam", "splitname", "splitlevel", "splitgrid",
        "splitzaxis", "splittabnum", "splitensemble", "splitvar",
    )},
    # splitrec: none — see _SPLIT_PARAMS.

    # Splittime documents ``format`` for a Name line of six operators, and
    # exactly one of them takes it. ``cdo splithour,%Y infile obase`` and the
    # other four answer "Too many arguments! Need 0 found 1."; splitmon exits 0
    # and writes ``mon2000.nc``. The manual's own prose agrees — the sentence
    # about ``format`` sits under splitmon — so this follows both once you read
    # it that way, and only the Name line suggested otherwise.
    #
    # Positional, not keyword. ``splitmon,format=%B`` also exits 0, and writes
    # ``monformat=January.nc``.
    "splitmon":    (_p("format", _STR, "format", "strftime, e.g. %B",
                       optional=True,
                       help="C-style strftime format for the month part of "
                            "each output filename."),),

    # splitdate and splitdatetime take none. They *accept* an argument and
    # ignore it — ``cdo splitdate,%Y infile out`` exits 0 and still writes
    # ``out2000-01-01.nc`` — which is a reason to declare nothing rather than a
    # reason to declare something harmless.

    "splitsel":    (
        _p("nsets", _INT, "nsets"),
        _p("noffset", _INT, "noffset", optional=True),
        _p("nskip", _INT, "nskip", optional=True),
    ),

    # ``ny`` is optional: the manual gives it "[default: 1]" and
    # ``cdo distgrid,2 infile obase`` writes two files. It was declared
    # required, which made the model builder and the operator form both refuse
    # a call CDO accepts.
    "distgrid":    (
        _p("nx", _INT, "nx", "regions in x, or pieces on an unstructured grid"),
        _p("ny", _INT, "ny", "[default: 1]", optional=True),
    ),

    "collgrid":    (
        _kw("nx", _INT, "nx", "[default: number of input files]",
            help="Needed only for curvilinear grids."),
        _kw("name", _STR, "name", "comma-separated variable names"),
        # Typed STRING although the manual says INTEGER: the value it documents
        # is "comma-separated list or first/last[/inc] range", neither of which
        # is an integer. Declared the way every other comma-grammar value in
        # this file is, so ``invalid_parameter_values`` does not refuse a range.
        _kw("levidx", _STR, "levidx", "list, or first/last[/inc]"),
        _kw("gridtype", _SELECT, "gridtype", optional=True,
            choices=("", "unstructured")),
    ),

    # ---------- Split / formatted ----------
    # ``cdo input,grid[,zaxis] outfile``. ``zaxis`` was missing, so the only way
    # to read a multi-level field back in was to leave the field blank and let
    # CDO default to surface — with no row on the form to say the option
    # existed. Same widget as ``grid`` and the same reason: the manual types
    # both STRING and documents both as "description file or name".
    "input":       (
        _p("grid", _GRID, "grid", choices=GRID_PRESETS,
           help="Horizontal grid every field read from stdin is on: a grid "
                "description file, or a CDO preset such as r360x180."),
        _p("zaxis", _STR, "zaxis", "z-axis description file or name",
           optional=True,
           help="Vertical axis for the fields read from stdin. Omitted, CDO "
                "builds a single surface level."),
    ),
    # ``cdo outputf[,format[,nelem]] infiles`` — the manual brackets both, and
    # both were declared required, so the form refused to run the operator
    # until two fields were filled and the second had no documented value to
    # fill it with. ``nelem`` defaults to 1 ("The default for nelem is 1",
    # cdo --help output on 2.6.3).
    #
    # ``format`` stays first and positional, so leaving it blank while filling
    # ``nelem`` is refused by the trim rule rather than silently promoting
    # nelem into the format slot.
    "outputf":     (
        _p("format", _STR, "format", "C-style format, e.g. %8.4g",
           optional=True,
           help="C-style format applied to every value, e.g. %8.4g or %6.2f."),
        _p("nelem", _INT, "nelem", "elements per row [default: 1]",
           optional=True,
           help="How many values are printed on each row. Default 1."),
    ),
    # The whole grammar is _OUTPUTTAB_KEYNAMES' business; see the note above it
    # for the eighteen names, the :len suffix and the crash that made checking
    # them worth doing. ``outputkey`` is deliberately absent from this table —
    # it is an alias, and aliases now inherit their target's parameters in
    # ``_build_operator_schema`` rather than carrying a second copy here.
    "outputtab":   (_OUTPUTTAB_KEYNAMES,),
    "outputvector":(_p("increment", _INT, "increment", "print every nth vector"),),

    # ---------- Miscellaneous ----------
    #
    # Every grammar below was determined by *running* the operator on CDO 2.6.3
    # against a throwaway ``cdo -f nc random,r36x18`` sample, not by reading the
    # synopsis — this section has three places where the manual and the binary
    # disagree, and one where the binary's own help contradicts itself. The
    # discriminating failure modes, since they are what makes a grammar
    # decidable rather than guessable:
    #
    #   Parse error! / missing '=' in key/value string  -> keyword-only
    #   Float parameter >v=12< contains invalid
    #     character at position 1!                      -> positional-only
    #   Too many arguments! Need 0 found 1.             -> takes nothing
    #   Invalid parameter key >bogus<!                  -> keyword, unknown key
    #
    # Sibling operators in one module are *not* assumed to share a grammar, and
    # this section is why the rule exists: ``strwin`` is positional while
    # ``strbre``, ``strgal`` and ``hurr`` — same shape, same purpose, adjacent
    # in the manual — are keyword.
    "setrtoc":     (
        _p("rmin", _FLOAT, "rmin", "lower bound of the range"),
        _p("rmax", _FLOAT, "rmax", "upper bound of the range"),
        _p("c", _FLOAT, "c", "value to write inside the range"),
    ),
    "setrtoc2":    (
        _p("rmin", _FLOAT, "rmin", "lower bound of the range"),
        _p("rmax", _FLOAT, "rmax", "upper bound of the range"),
        _p("c", _FLOAT, "c", "value to write inside the range"),
        _p("c2", _FLOAT, "c2", "value to write outside the range"),
    ),
    "const":       (
        _p("const", _FLOAT, "constant"),
        _p("grid", _GRID, "grid", choices=GRID_PRESETS),
    ),
    "random":      (
        _p("grid", _GRID, "grid", choices=GRID_PRESETS),
        _p("seed", _INT, "seed", optional=True),
    ),
    "for":         (
        _p("start", _FLOAT, "start"),
        _p("stop", _FLOAT, "stop"),
        _p("step", _FLOAT, "step", optional=True),
    ),
    # ``cdo seq,start,end[,inc]``. Renamed from ``start,stop,step``: these names
    # are user-visible — ``operator_syntax`` renders them into the usage hint
    # and both surfaces caption the fields with them — so they should be the
    # manual's words rather than Python's ``range`` vocabulary. The grammar is
    # unchanged and positional (``seq,start=1,end=10`` is "Float parameter
    # >start=1< contains invalid character at position 1!").
    "seq":         (
        _p("start", _FLOAT, "start", "first value"),
        _p("end", _FLOAT, "end", "last value"),
        _p("inc", _FLOAT, "inc", "increment (default 1)", optional=True),
    ),
    "stdatm":      (_p("levels", _STR, "levels", "e.g. 0,100,500,1000",
                       help="Comma-separated height levels in metres. Writes "
                            "pressure and temperature of the US standard "
                            "atmosphere at each one."),),
    "query":       (_p("queryentries", _STR, "query entries",
                       "key=value, e.g. name=tas"),),
    # Prompt text: "Enter detrend type, length of segments, number of segments,
    # window type" — the two coded choices are spelled out in the prompt, so
    # they are spelled out here too.
    "spectrum":    (
        _p("detrend", _INT, "detrend type",
           "0 none, 1 subtract mean, 2 detrend series, 3 detrend segments",
           choices=("0", "1", "2", "3")),
        _p("seglen", _INT, "length of segments"),
        _p("nseg", _INT, "number of segments"),
        _p("window", _INT, "window type",
           "0 none, 1 Hann, 2 Bartlett, 3 Welch",
           choices=("0", "1", "2", "3")),
    ),
    "mask":        (_p("grid", _GRID, "grid", choices=GRID_PRESETS),),
    # The five Miscellaneous-section wind operators are all **positional**, and
    # the spelling that looks like the modern CDO idiom is the dangerous one.
    # Measured on 2.6.3 against a file holding variables named u and v:
    #
    #   rotuvNorth,u,v      -> "Only rotated lon/lat grids supported!"  (parsed)
    #   rotuvNorth,u=u,v=v  -> "Variable u=u not found!"
    #
    # The keyword spelling is not a parse error: the whole string ``u=u`` is
    # taken as a variable *name*, so the operator looks for a variable nobody
    # has and blames the file. That is why these are declared rather than left
    # to the caller's habits.
    "rotuvb":      (_p("pairs", _STR, "u,v,...", "comma-separated U,V variable names",
                       help="One or more U,V pairs, by name or GRIB code. "
                            "Positional — u=U is read as a variable named "
                            "'u=U' and reported as missing."),),
    # mrotuvb takes one component per input file and finds them itself; its only
    # argument is the flag that skips interpolation when both files already
    # share a grid ("Input grids are the same, use parameter >noint<").
    #
    # Declared ``form=_FLAG`` rather than as the one-choice string it was.
    # Measured on 2.6.3 with two same-grid files: ``mrotuvb,noint`` exits 0,
    # while ``mrotuvb,noint=true`` and ``mrotuvb,1`` both fail with the very
    # abort that asks for the flag — so the old declaration produced a working
    # command only because its single choice happened to be the bare word.
    # A flag says that in the type instead of relying on the choice list, and
    # it is what makes the checkbox render as a checkbox.
    "mrotuvb":     (_p("noint", _BOOL, "noint", optional=True, form=_FLAG,
                       help="Skip the interpolation step. Required when both "
                            "input files are already on the same grid — "
                            "without it CDO aborts asking for it."),),
    # Not a typo for the two above: mrotuv finds U and V by itself and rejects
    # any argument at all ("Too many arguments! Need 0 found 2").
    "mrotuv":      (),
    "rotuvN":      (_p("pairs", _STR, "u,v", "names or GRIB codes, e.g. 33,34"),),
    "rotuvNorth":  (_p("pairs", _STR, "u,v", "names or GRIB codes, e.g. 33,34",
                       help="The U and V variables, by name or GRIB code. "
                            "Positional — u=U is read as a variable name."),),
    "projuvLatLon":(_p("pairs", _STR, "u,v", "names or GRIB codes, e.g. 33,34",
                       help="The U and V variables, by name or GRIB code. "
                            "Positional — u=U is read as a variable name."),),
    # ``uvDestag,u,v[,-/+0.5,-/+0.5]``. The offsets are one parameter holding
    # *two* comma-separated numbers, and that is not cosmetic: a single offset
    # crashes the binary rather than aborting. Measured on 2.6.3:
    #
    #   uvDestag,u,v,-0.5,0.5 -> parsed (then fails on the grid)
    #   uvDestag,u,v,0.5      -> SIGABRT, "Assertion failed: idx < argc,
    #                            cdo_operator_argv, process_int.cc:81"
    #
    # An uncaught assertion is not a failed run a caller can report — it
    # arrives as a signal, the same shape of trap ``outputtab,value:abc``
    # carries. Keeping both numbers in one field is what makes the half-given
    # case unspellable from the GUI; ``invalid_parameter_values`` rejects a
    # lone number as well, so neither surface can reach it.
    "uvDestag":    (
        _p("pairs", _STR, "u,v", "names or GRIB codes, e.g. 33,34",
           help="The U and V variables, by name or GRIB code. Positional."),
        _p("offsets", _STR, "offsets", "e.g. -0.5,-0.5", optional=True,
           help="Both stagger offsets, as one comma-separated pair — each "
                "-0.5 or +0.5. Give both or neither: a single offset makes "
                "CDO abort on an internal assertion rather than fail."),
    ),
    # The four "strong wind days" indices, and the counterexample that this
    # section's grammars are per *operator* rather than per module. All four
    # take one optional wind-speed threshold; ``strwin`` takes it positionally
    # and the other three take it as ``v=``. Measured on 2.6.3:
    #
    #   strwin,12    -> exit 0        strwin,v=12  -> "Float parameter >v=12<
    #                                                  contains invalid
    #                                                  character at position 1!"
    #   strbre,12    -> "Argument     strbre,v=12  -> exit 0
    #   strgal,12       parse         strgal,v=12  -> exit 0
    #   hurr,12         error!"       hurr,v=12    -> exit 0
    #
    # Two documented facts are wrong about the last three. The manual presents
    # ``strbre``/``strgal`` as having fixed thresholds and gives ``hurr`` no
    # parameter at all — ``cdo -h hurr`` prints the synopsis "cdo hurr infile
    # outfile" with no PARAMETERS section — yet the binary accepts ``v=`` on
    # all three and uses it. Declared to match the binary, and optional, so a
    # user who wants the documented threshold simply leaves the field empty.
    "strwin":      (_p("v", _FLOAT, "v", "v=10.5 m/s (default)", optional=True,
                       help=_WIND_THRESHOLD_HELP),),
    "strbre":      (_kw("v", _FLOAT, "v", "v=10.5 m/s (default)",
                        help=_WIND_THRESHOLD_HELP + " Undocumented: the manual "
                             "presents this threshold as fixed."),),
    "strgal":      (_kw("v", _FLOAT, "v", "v=20.5 m/s (default)",
                        help=_WIND_THRESHOLD_HELP + " Undocumented: the manual "
                             "presents this threshold as fixed."),),
    "hurr":        (_kw("v", _FLOAT, "v", "v=32.5 m/s (default)",
                        help=_WIND_THRESHOLD_HELP + " Undocumented: cdo -h hurr "
                             "prints no PARAMETERS section at all, but the "
                             "binary accepts and uses v=."),),
    # ``cdo <operator>,bounds`` — a positional comma list of bin bounds, n
    # bounds giving n-1 bins. ``-inf`` and ``inf`` are accepted by CDO and the
    # manual writes open end bins with them, which is the reason ``bounds`` is
    # one ``string`` parameter and not a list of floats: ``float`` slots are
    # checked by ``invalid_parameter_values``, and that check refuses a
    # non-finite number ("must be a finite number"). Declared as a float it
    # would have been the app, not CDO, rejecting the manual's own example.
    "histcount":   (_HIST_BOUNDS,),
    "histsum":     (_HIST_BOUNDS,),
    "histmean":    (_HIST_BOUNDS,),
    "histfreq":    (_HIST_BOUNDS,),
    # Six keyword parameters, all optional, replacing a single free-text
    # ``options`` box. The box was not merely coarse — it put a grammar the
    # user had to know behind a field that could not hint at it, and the
    # obvious thing to type in it, ``smooth,2``, is "Parse error!".
    #
    # Two of the six are not what their names suggest, which is the argument
    # for declaring them individually rather than documenting the string:
    #
    #   weighted  reads as a boolean and is a *method*. ``weighted=true`` is
    #             "method=true unsupported (available: avg|dist|linear|gauss|
    #             rbf)" — so the value set is closed and is spelled below.
    #   radius    reads as a number and is a string with a unit suffix:
    #             1deg, 500km, 0.1rad and a bare 5 are all accepted.
    #
    # Defaults are CDO's own, from ``cdo -h smooth`` on 2.6.3.
    "smooth":      (
        _kw("nsmooth", _INT, "nsmooth", "nsmooth=1 (default)",
            help="How many times to run the smoother over the field."),
        _kw("radius", _STR, "radius", "radius=1deg (default)",
            help="Search radius, with a unit suffix: deg, rad, km or m. "
                 "A bare number is read as degrees."),
        _kw("maxpoints", _INT, "maxpoints", "maxpoints=<gridsize> (default)",
            help="Largest number of neighbours used for one point. Defaults "
                 "to the whole grid, so the radius is normally what limits it."),
        _kw("weighted", _SELECT, "weighting method", "weighted=linear (default)",
            choices=("avg", "dist", "linear", "gauss", "rbf"),
            help="Weighting method, not a yes/no switch: weighted=true is "
                 "rejected with \"method=true unsupported\"."),
        _kw("weight0", _FLOAT, "weight0", "weight0=0.25 (default)",
            help="Weight given to the point at distance 0."),
        _kw("weightR", _FLOAT, "weightR", "weightR=0.25 (default)",
            help="Weight given to a point at the search radius."),
    ),
    # ``smooth9`` shares the module and takes nothing — a fixed 9-point filter.
    # Measured: ``cdo smooth9,2`` exits 0 rather than complaining, so the empty
    # tuple is what stops a field being offered whose value is silently ignored.
    "smooth9":     (),
    # The Filter module, and the one place in this section where the binary
    # contradicts *itself* rather than contradicting the manual.
    #
    # ``cdo -h lowpass`` prints, in the same page:
    #
    #   SYNOPSIS      cdo lowpass,fmin   infile outfile
    #                 cdo highpass,fmax  infile outfile
    #   OPERATORS     lowpass  ... (pass for frequencies lower than fmax)
    #                 highpass ... (pass for frequencies greater than fmin)
    #
    # The two are swapped with respect to each other. The descriptions are the
    # ones that make physical sense — a lowpass is bounded above, so its
    # argument is a maximum — and they are also self-consistent with bandpass,
    # whose two arguments are fmin,fmax in that order. Named for the
    # descriptions, therefore, and against the synopsis.
    #
    # Nothing about the *command* depends on this: both are one positional
    # float and CDO never sees the name. What depends on it is the usage hint
    # ``operator_syntax`` builds and the caption on the field, which is to say
    # everything the user has to go on. The old declaration called both of them
    # ``fcut``, which is neither of CDO's names and appears nowhere in its
    # documentation.
    #
    # Positional, measured: ``lowpass,fmin=10`` and ``lowpass,fmax=10`` are
    # both "Float parameter >fmax=10< contains invalid character at position 2!"
    "bandpass":    (
        _p("fmin", _FLOAT, "fmin", "lowest frequency to keep, per year"),
        _p("fmax", _FLOAT, "fmax", "highest frequency to keep, per year"),
    ),
    "lowpass":     (_p("fmax", _FLOAT, "fmax", "frequencies per year",
                       help="Passes frequencies *lower* than fmax and "
                            "suppresses everything above it. CDO's synopsis "
                            "calls this argument fmin and its own description "
                            "of the operator calls it fmax; the description is "
                            "the one that matches what the operator does."),),
    "highpass":    (_p("fmin", _FLOAT, "fmin", "frequencies per year",
                       help="Passes frequencies *greater* than fmin and "
                            "suppresses everything below it. CDO's synopsis "
                            "calls this argument fmax and its own description "
                            "of the operator calls it fmin; the description is "
                            "the one that matches what the operator does."),),
    # ``cdo gradsdes[,mapversion] infile`` — nout=0, and the only operator in
    # the app whose real output is a file it writes *beside its input*. See
    # ``nc_integration._OPERATORS_WRITING_BESIDE_INPUT`` for what the execution
    # layer has to do about that.
    #
    # Positional, measured: ``gradsdes,mapversion=2`` is "Integer parameter
    # >mapversion=2< contains invalid character at position 1!".
    "gradsdes":    (_p("mapversion", _INT, "map file version", "2 or 4",
                       optional=True, choices=("1", "2", "4"),
                       help="Version of the GrADS *map* file, which is only "
                            "written for GRIB1 input; NetCDF input produces "
                            "the .ctl descriptor alone. 1 machine-specific, "
                            "2 machine-independent (GrADS 1.8+), 4 for GRIB "
                            "files over 2GB. Default 4 above 2GB, else 2."),),
    # ``after[,vct]``. Its real controls are not parameters at all — they are
    # an ECHAM namelist read from standard input — so this one optional file is
    # the whole token. :func:`reads_stdin` is what gives it the namelist row on
    # both surfaces; see ``_STDIN_NAMELIST_OPERATORS`` and its entry in
    # ``_SURPRISING_DEFAULTS``.
    "after":       (_p("vct", _FILE, "vct file", "vertical coordinate table",
                       optional=True, file_kind=_ft.TEXT,
                       help="File holding the vertical coordinate table, when "
                            "the input does not carry one."),),
    # ``gridarea[,radius=]`` — keyword, measured: ``gridarea,6371000`` is
    # "Parse error!" and ``gridarea,bogus=1`` is "Invalid parameter key".
    #
    # ``gridweights`` is deliberately the empty tuple rather than the same
    # parameter, and this is a correction to what the two look like from the
    # manual, which documents them together under one synopsis
    # ("cdo <operator>[,parameters]"). Measured on 2.6.3:
    #
    #   gridarea,radius=6371000    -> "Using user defined planet radius"
    #   gridweights,radius=6371000 -> "Too many arguments! Need 0 found 1."
    #
    # There is a reason rather than an inconsistency, and it is worth stating
    # because it also settles the PLANET_RADIUS question below: gridweights
    # returns weights normalised to sum to 1, so the radius cancels out of
    # every one of them. Confirmed numerically — the field is identical with
    # PLANET_RADIUS unset and set to 1234567 — while gridarea's field scales
    # with the square of it.
    "gridarea":    (_kw("radius", _FLOAT, "planet radius", "6371000 m (default)",
                        help="Planet radius in metres. Also settable with the "
                             "PLANET_RADIUS environment variable; the "
                             "parameter wins. Areas scale with its square."),),
    "gridweights": (),
    # ``gridcellindex,lon=,lat=`` — keyword, and nout=0: the answer is one
    # integer on stdout and there is no output file to ask for. Measured:
    # ``gridcellindex,10,20`` is "Parse error!", and handing it an output path
    # is "Operator cannot be assigned".
    #
    # Both are declared optional, against the manual's ``cdo gridcellindex,
    # parameters infile``, which reads as required. Measured on 2.6.3: bare
    # ``cdo gridcellindex in.nc`` exits 0 and prints 0, and each key defaults
    # to 0 independently — ``lon=10`` alone answers for (10, 0). Declaring them
    # required would refuse a call CDO accepts, which this file treats as the
    # worse of the two errors.
    "gridcellindex":(
        _kw("lon", _FLOAT, "lon", "degrees east (default 0)",
            help="Longitude of the cell to look up, in degrees."),
        _kw("lat", _FLOAT, "lat", "degrees north (default 0)",
            help="Latitude of the cell to look up, in degrees."),
    ),
    # ``topo[,grid]``. The manual's synopsis is ``cdo topo,grid outfile`` and
    # reads as required; the binary accepts bare ``topo`` and falls back to a
    # global half-degree grid. Measured on 2.6.3: ``cdo -f nc topo out.nc``
    # exits 0 and ``griddes`` reports lonlat 720x360, xfirst=-179.75,
    # yfirst=-89.75, xinc=yinc=0.5. Declared optional to match the binary.
    "topo":        (_p("grid", _GRID, "grid", "default: global 0.5 degree",
                       optional=True, choices=GRID_PRESETS,
                       help="Target grid. Omitted, CDO writes a global "
                            "half-degree lonlat grid (720x360)."),),
    # Verified to take nothing, each by the same measurement: any argument is
    # "Too many arguments! Need 0 found 1." An explicit empty tuple rather than
    # a missing key, so a surface offers no field instead of a free-text box.
    "deltat":      (),
    "timsort":     (),
    "mastrfu":     (),
    "verifygrid":  (),
    "wct":         (),
    # ``fdns`` is the one operator in this section that neither takes a
    # parameter nor refuses one: measured on 2.6.3, ``fdns,1``, ``fdns,bogus``
    # and ``fdns,1,2,3`` all exit 0 and produce a field identical to bare
    # ``fdns``. The argument is swallowed and ignored, so an empty tuple is
    # both the honest declaration and the one that prevents a user typing into
    # a box whose contents can only ever be discarded. (``wct``, its sibling in
    # every other respect, aborts on the same input.)
    "fdns":        (),
    # The Pressure and Derivepar modules. All seven take nothing — measured
    # individually, "Too many arguments! Need 0 found 1." on each — and all
    # seven need hybrid sigma pressure level data, which is what their input
    # slots say so the requirement arrives before the run rather than as
    # "No 3D variable with hybrid sigma pressure coordinate found!".
    "pressure":         (),
    "pressure_half":    (),
    "delta_pressure":   (),
    "sealevelpressure": (),
    "gheight":          (),
    "gheight_half":     (),
    "air_density":      (),
    # ``rhopot[,pressure]`` — positional, and this one *was* measurable:
    # ``rhopot,pressure=10`` is "Float parameter >pressure=10< contains
    # invalid character at position 1!", which is a parse failure and so
    # decides the grammar even though the run then aborts on the data.
    "rhopot":      (_p("pressure", _FLOAT, "pressure", "in bar", optional=True,
                       help="Constant pressure in bar, applied to every level. "
                            "Omitted, pressure is derived from the level "
                            "information."),),
    # ``adisit``/``adipot`` take ``[,pressure]`` and the grammar could **not**
    # be measured, unlike rhopot's. Both abort with "Sea water salinity not
    # found!" before the parameter is looked at, so all three spellings —
    # bare, ``,10`` and ``,pressure=10`` — produce the identical message and
    # none of them discriminates.
    #
    # Attempts made, so a future reader knows what has already been ruled out:
    # a merged file with variables named tho/sao (the names the manual gives),
    # tho/s, t/sao, and one with GRIB codes 2 and 5 set explicitly. The check
    # is not satisfied by any of them and there is no synthetic ocean file
    # short of real MPIOM output.
    #
    # Declared positional to match ``rhopot``, which is the same module's
    # neighbour, carries the identically-named and identically-documented
    # parameter, and *was* measured. That is an inference, not a measurement,
    # and it is written here rather than in the placeholder so that nobody
    # later reads it as a verified fact.
    "adisit":      (_p("pressure", _FLOAT, "pressure", "in bar", optional=True,
                       help="Constant pressure in bar, applied to every level. "
                            "Omitted, pressure is derived from the level "
                            "information."),),
    "adipot":      (_p("pressure", _FLOAT, "pressure", "in bar", optional=True,
                       help="Constant pressure in bar, applied to every level. "
                            "Omitted, pressure is derived from the level "
                            "information."),),
    # ``symmetrize[,lat=][,grid=]`` — keyword, measured: ``symmetrize,negative``
    # is "Parse error!" and ``symmetrize,lat=bogus`` is "parameter type=bogus:
    # invalid value!", which is what fixes the choice list below.
    #
    # Both values of ``lat`` are accepted, against the expectation that only
    # ``negative`` is meaningful: ``lat=positive`` exits 0 too.
    "symmetrize":  (
        _kw("lat", _SELECT, "hemisphere to mirror", "lat=negative (default)",
            choices=("negative", "positive"),
            help="Which hemisphere is copied onto the other. A value outside "
                 "this pair is \"parameter type=<value>: invalid value!\"."),
        _kw("grid", _GRID, "target grid", "grid descriptor or preset",
            choices=GRID_PRESETS,
            help="Grid to write the mirrored field on."),
    ),
    # The healpix pair. Keyword, measured: ``hpdegrade,4`` is "Parse error!"
    # and ``hpdegrade,bogus=1`` is "Invalid parameter key >bogus<!".
    #
    # The *values* could not be measured. Every call against a non-healpix
    # sample stops at "Input grid is not healpix!", which fires before the
    # values are validated — ``order=bogus`` gets the grid message, not a
    # complaint about the order — so the choice lists below are the manual's
    # and not the binary's. Building a healpix fixture needs a healpix grid,
    # which is why both operators are in ``operator_lab``'s UNTESTABLE.
    #
    # ``--async_read``, which the manual shows in its examples for these two,
    # is a CDO *global option* and not part of the operator token. It belongs
    # in the options row, and putting it here would produce
    # ``hpdegrade,nside=4,--async_read``, which is an invalid parameter key.
    "hpdegrade":   (
        _kw("nside", _INT, "nside", "target resolution parameter",
            help="Resolution of the target healpix grid; must be lower than "
                 "the input's. A power of 2."),
        _kw("order", _SELECT, "pixel ordering", "nested or ring",
            choices=("nested", "ring"),
            help="Pixel ordering scheme of the output. From the manual — the "
                 "grid check aborts before this value is validated, so it "
                 "could not be confirmed against the binary."),
        _kw("power", _FLOAT, "power", "weighting exponent",
            help="Exponent applied when averaging the source pixels."),
    ),
    "hpupgrade":   (
        _kw("zoom", _INT, "zoom", "levels to refine by",
            help="How many resolution levels to upgrade the grid by."),
        _kw("order", _SELECT, "pixel ordering", "nested or ring",
            choices=("nested", "ring"),
            help="Pixel ordering scheme of the output. From the manual — the "
                 "grid check aborts before this value is validated, so it "
                 "could not be confirmed against the binary."),
    ),
    # ``uv2vr_cfd``/``uv2dv_cfd``: **keyword**, and the manual is wrong.
    # ``cdo -h uv2vr_cfd`` prints "cdo <operator>[,u,v,boundOpt,outMode]",
    # which is the positional spelling, and measured on 2.6.3 that spelling is
    # "Parse error!" while ``u=u,v=v,boundOpt=1,outMode=new`` parses and runs.
    #
    # These two share the module *title* "Wind transformation" with dv2uv,
    # uv2dv and the uvDestag family, which is why they are placed by the
    # curated list rather than by ``_MODULE_CATEGORY``. See the note there.
    "uv2vr_cfd":   _UV_CFD_PARAMS,
    "uv2dv_cfd":   _UV_CFD_PARAMS,

    # No parameter, and the empty tuple is the claim: ``cdo import_binary
    # infile.ctl outfile`` is the whole synopsis, and the .ctl file *is* infile.
    #
    # What stood here was ``_p("ctlfile", _FILE, …)``, an extra file parameter
    # on top of the operator's own nin=1. ``_resolve_operator_call`` puts a
    # file-valued parameter inside the operator token and then appends the
    # inputs and outputs, so the command it built was
    #
    #     cdo import_binary,demo.ctl in.nc out.nc
    #
    # — three file tokens for a two-file operator, and a form that asked for a
    # .ctl *and* an input file when there is only one file to name. Measured on
    # 2.6.3: the correct call is ``cdo -f nc import_binary demo.ctl out.nc``,
    # which exits 0 and writes the 8x4x3 fixture as NetCDF.
    #
    # ``import_grads`` is the same operator under another name and carried no
    # parameter, so the two disagreed about their own shape. It is not listed
    # here either — since aliases inherit from their target in
    # ``_build_operator_schema``, the two are now the same object and cannot
    # drift apart again, which is what the disagreement actually needed fixing.
    "import_binary":(),
    "import_cmsaf":(),
    "import_amsr": (),

    # ---------- Graphics with Magics ----------
    #
    # See the section note above ``_PARAM_SPECS`` for the sourcing, the two
    # measurements that bound what is knowable here, and the four documented
    # contradictions these entries resolve.
    #
    # All six are entirely optional, which is a documented fact rather than a
    # cautious default: ``cdo graph amoc plot`` is the Maggraph page's own
    # worked example and carries no parameter at all. Optional throughout also
    # keeps ``test_required_params_come_first`` trivially satisfied — there is
    # no required head to come first.
    #
    # Every parameter is ``_KEYWORD``. The two runnable examples in the module
    # are ``cdo shaded,interval=3,colour_min=violet,colour_max=red,
    # colour_triad=cw temp plot`` and ``cdo vector,thin_fac=1,unit_vec=70
    # uvdata plot`` — ``name=value``, comma-joined, and each independently
    # omissible, which is what ``_KEYWORD`` already means and why
    # ``parameter_tokens`` needs no change for this section.
    "contour":     _MAGPLOT_COMMON + _MAGPLOT_CONTOUR,
    "shaded":      _MAGPLOT_COMMON + _MAGPLOT_SHADED,
    "grfill":      _MAGPLOT_COMMON + _MAGPLOT_SHADED,

    # Magvector. Five parameters, and note it does *not* take the Magplot
    # common set: no style, no min/max, no lon/lat window, no count/interval.
    # Magvector has a table of its own and this is all of it.
    "vector":      (
        _MAGICS_DEVICE,
        _MAGICS_PROJECTION,
        _kw("thin_fac", _FLOAT, "arrow thinning factor", "2",
            help="How many wind arrows to draw: higher plots fewer. Default "
                 "2. The module's example uses thin_fac=1, which it describes "
                 "as plotting all of them."),
        _kw("unit_vec", _FLOAT, "wind speed per 1 cm arrow", "e.g. 70",
            help="The wind speed in m/s that one centimetre of arrow "
                 "represents. Sets the scale of the arrows and the legend."),
        _MAGICS_STEP_FREQ,
    ),

    # ``stream`` is undocumented: there is no Magstream page in the reference
    # manual and none in this project's docs folder. It is not undeclared,
    # though, and the parameters below are the binary's answer rather than an
    # assumption from the name:
    #
    #   * ``cdo -h stream`` prints the **Magvector** page — "vector - Lon/Lat
    #     vector plot", with Magvector's five-row parameter table. CDO's help
    #     dispatcher does not fall back: ``cdo -h zzznotanop`` answers "is
    #     neither an operator nor an option", so resolving to Magvector's entry
    #     is registration, not a near-miss.
    #   * ``cdo --operators`` gives it ``(1|1)`` and an empty description, the
    #     same shape as ``vector``.
    #   * ``CDO_OPERATOR_MODULES`` — regenerated from this binary — already
    #     records its module as "Lon/Lat vector plot".
    #
    # So it shares Magvector's module, arity and parameter table, and is given
    # the same tuple object for the reason ``_resolve_params`` gives for
    # aliases: two declarations of one thing disagree eventually. It is *not*
    # declared as an alias of ``vector`` — the catalog's alias notation is
    # CDO's own "--> target" and CDO does not say that here, so claiming it
    # would be this file inventing a relationship the binary did not assert.
    # A streamline plot and an arrow plot are different pictures from the same
    # two velocity components; what they share is the parameter table.
    "stream":      (
        _MAGICS_DEVICE,
        _MAGICS_PROJECTION,
        _kw("thin_fac", _FLOAT, "arrow thinning factor", "2",
            help="How many streamlines to draw: higher plots fewer. Default "
                 "2. Documented for vector, which is the help page CDO gives "
                 "for stream; unverified for stream itself."),
        _kw("unit_vec", _FLOAT, "wind speed per 1 cm arrow", "e.g. 70",
            help="The wind speed in m/s that one centimetre represents. "
                 "Documented for vector, which is the help page CDO gives for "
                 "stream; unverified for stream itself."),
        _MAGICS_STEP_FREQ,
    ),

    # Maggraph. No projection — it draws a line graph, not a map — and no
    # device-independent geometry, so this shares only ``device`` with the
    # other five.
    "graph":       (
        _MAGICS_DEVICE,
        _kw("ymin", _FLOAT, "y-axis minimum",
            help="Lowest value on the y axis."),
        _kw("ymax", _FLOAT, "y-axis maximum",
            help="Highest value on the y axis."),
        _kw("linewidth", _INT, "line width", "8",
            help="Width of the plotted lines. Default 8."),
        # Tail recovered from ``cdo -h graph``; the PDF clips after
        # 'Default is "FALS'. The recovered part is the half that matters: the
        # setting is overridden back to off, silently, when the input files
        # do not line up in time.
        _kw("stat", _BOOL, "plot the mean of the inputs",
            help="Compute and draw the mean across the input files. Default "
                 "off. Forced back off — without an error — if the input "
                 "files have unequal numbers of timesteps or different "
                 "start/end times. [recovered from cdo -h; the PDF table is "
                 "clipped mid-word] " + _MAGICS_BOOL_CASE_RISK),
        _kw("sigma", _FLOAT, "standard deviations to shade",
            help="Draw a shaded band of this many standard deviations around "
                 "the mean. Only has an effect together with stat. "
                 "[tail recovered from cdo -h]"),
        # Also clipped in the PDF, after 'by setting to "TRU'. Two facts were
        # in the clipped tail and neither is guessable from the name: which
        # file is the observation, and that it is always drawn in black.
        _kw("obsv", _BOOL, "first input is observations",
            help="Say that the FIRST input file holds observations rather "
                 "than model output. It is always drawn in black. Default "
                 "off. [recovered from cdo -h; the PDF table is clipped "
                 "mid-word] " + _MAGICS_BOOL_CASE_RISK),
    ),

    # ---------- ECA indices ----------
    # Every index the installed CDO has, including the ones that take nothing:
    # an explicit empty tuple says "verified to take no parameter", which a
    # missing key cannot, and it is what stops a new index being launched with
    # no field to type into. See the block above for the two grammars.

    # -- spells of dry / wet / frost / summer days --
    "eca_cdd":     _ECA_CDD_PARAMS,
    "etccdi_cdd":  _ECA_CDD_PARAMS,
    "eca_cwd":     _ECA_CWD_PARAMS,
    "etccdi_cwd":  _ECA_CWD_PARAMS,
    "eca_cfd":     (_p("N", _INT, "N", "N=5 days (default)", optional=True,
                       help="Counts frost spells longer than N days into the "
                            "second output variable; the index itself does "
                            "not depend on it."),),
    "eca_csu":     (
        _p("T", _FLOAT, "T", "T=25 °C (default)", optional=True,
           help="A summer day has a maximum above T. " + _CELSIUS_HELP),
        _p("N", _INT, "N", "N=5 days (default)", optional=True,
           help="Counts summer spells longer than N days into the second "
                "output variable; the index itself does not depend on it."),
    ),

    # -- threshold day counts --
    "eca_su":      _ECA_SU_PARAMS,
    "etccdi_su":   _ECA_SU_PARAMS,
    "eca_tr":      _ECA_TR_PARAMS,
    "etccdi_tr":   _ECA_TR_PARAMS,
    "eca_fd":      _ECA_FREQ_ONLY,      # documented as [,parameter]; it is freq
    "etccdi_fd":   _ECA_FREQ_ONLY,
    "eca_id":      _ECA_FREQ_ONLY,
    "etccdi_id":   _ECA_FREQ_ONLY,
    "eca_hd":      _ECA_HD_PARAMS,
    "etccdi_hd":   _ECA_HD_PARAMS,

    # -- precipitation day counts --
    "eca_rr1":     _ECA_WETDAY_PARAMS,
    "eca_r1mm":    _ECA_WETDAY_PARAMS,  # an alias of eca_rr1, so [,R] not freq
    "etccdi_r1mm": _ECA_FREQ_ONLY,      # *not* the alias: documented [,parameter]
    "eca_sdii":    _ECA_WETDAY_PARAMS,
    "etccdi_sdii": _ECA_WETDAY_PARAMS,
    "eca_pd":      (_p("x", _FLOAT, "x", "threshold in mm — required",
                       help="A day counts when precipitation is at least x. "
                            + _MM_HELP),),
    # eca_pd with a fixed threshold. CDO accepts an argument here and silently
    # ignores it — eca_r10mm,0.001 and eca_r10mm,99999 give byte-identical
    # output — so offering a field would be offering a lie.
    "eca_r10mm":     (),
    "eca_r20mm":     (),
    "etccdi_r10mm":  (),
    "etccdi_r20mm":  (),

    # -- precipitation amounts --
    "eca_rx1day":       _ECA_FREQ_ONLY,
    "etccdi_rx1day":    _ECA_FREQ_ONLY,
    "etccdi_rx1daymon": _ECA_FREQ_ONLY,
    "eca_rx5day":       _ECA_RX5DAY_PARAMS,
    "etccdi_rx5day":    _ECA_RX5DAY_PARAMS,
    "etccdi_rx5daymon": _ECA_RX5DAY_PARAMS,

    # -- two-input indices; see _OPERATOR_INPUTS for what the second file is --
    "eca_cwdi":    _ECA_WAVE_PARAMS,
    "eca_hwdi":    _ECA_WAVE_PARAMS,
    "eca_cwfi":    _ECA_SPELL_PARAMS,
    "etccdi_csdi": _ECA_SPELL_PARAMS,
    "eca_hwfi":    _ECA_SPELL_PARAMS,   # [,nday[,freq]] — same shape as cwfi
    "etccdi_wsdi": _ECA_SPELL_PARAMS,
    "eca_gsl":     _ECA_GSL_PARAMS,
    "etccdi_gsl":  _ECA_GSL_PARAMS,
    "eca_etr":     (),
    # The percentile indices carry their whole configuration in the second
    # input file — the percentile is baked into the climatology, not typed.
    "eca_tg10p":   (),
    "eca_tg90p":   (),
    "eca_tn10p":   (),
    "eca_tn90p":   (),
    "eca_tx10p":   (),
    "eca_tx90p":   (),
    "eca_r75p":    (),
    "eca_r75ptot": (),
    "eca_r90p":    (),
    "eca_r90ptot": (),
    "eca_r95p":    (),
    "eca_r95ptot": (),
    "eca_r99p":    (),
    "eca_r99ptot": (),
}


# ---------------------------------------------------------------------------
# What each input slot must contain
#
# The recipes come from the CDO reference documentation for the climate
# indices and were each run against the installed 2.6.0 before being written
# down. ``ydrunpctl`` and ``ydaypctl`` are themselves three-input operators —
# data, running minimum, running maximum — which is why their recipes are
# longer than the one-liner the index documentation quotes.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class UnitFamily:
    """One unit expectation: what to show a user, and what to accept quietly."""

    label: str
    accepts: Tuple[str, ...]


#: Keyed by :attr:`OperatorInput.units`. ``accepts`` is matched case- and
#: space-insensitively against the variable's ``units`` attribute; a file whose
#: units are absent or unrecognised is *not* reported as wrong, only as
#: unverifiable, because plenty of valid model output carries no units at all.
UNIT_FAMILIES: Dict[str, UnitFamily] = {
    # The temperature indices read the field as Kelvin while their threshold
    # argument is in degrees Celsius. Both halves of that are silent.
    "kelvin": UnitFamily("K", (
        "k", "kelvin", "kelvins", "degk", "deg_k", "degreesk", "degrees_k",
        "degree_k", "degrees kelvin", "degree kelvin",
    )),
    # Amounts, not rates: mm/s must be scaled by 86400 first.
    "precip": UnitFamily("mm (or kg m-2)", (
        "mm", "millimeter", "millimeters", "millimetre", "millimetres",
        "kgm-2", "kg/m2", "kg/m^2", "kgm**-2", "kgm2", "kgm^-2",
    )),
    # A difference rather than a level, so °C and K agree numerically.
    "kelvin_diff": UnitFamily("K or °C (a difference)", (
        "k", "kelvin", "kelvins", "c", "degc", "deg_c", "celsius",
        "degreesc", "degrees_c", "degree_c", "degrees celsius", "°c",
    )),
    # The two-input temperature indices compare the files against each other,
    # so what matters is that they agree — checked against input 1, not a list.
    "same_as_input1": UnitFamily("the same units as input 1", ()),

    # ---- the Miscellaneous section ----
    #
    # ``celsius`` is the mirror image of ``kelvin`` above and exists because
    # ``wct`` inverts the ECA convention. The temperature indices read Kelvin
    # and take their threshold in °C; ``wct`` reads **°C** and CDO documents
    # its validity range in °C too ("only valid for temperatures below 33 °C").
    # A Kelvin field through wct is not an error — 288 K reads as 288 °C, which
    # is outside the range, so every cell comes back missing and the run
    # succeeds. That is the same shape of silent wrongness the kelvin family
    # was written for, pointing the other way, and it needs its own entry
    # rather than a reuse: the two lists must not accept each other.
    "celsius": UnitFamily("°C", (
        "c", "degc", "deg_c", "celsius", "degreesc", "degrees_c", "degree_c",
        "degrees celsius", "degree celsius", "°c", "degreecelsius",
        "degreescelsius",
    )),
    # ``wct``'s second input and the four strong-wind indices. m/s, and the
    # spellings are the ones CF and the common models write.
    "wind_speed": UnitFamily("m/s", (
        "m/s", "ms-1", "m s-1", "ms**-1", "m s**-1", "meter/second",
        "metres/second", "meters per second", "metrespersecond",
        "meterspersecond", "mpers",
    )),
    # ``fdns``'s second input: a surface snow *amount*, whose threshold CDO
    # documents as 1 cm. Both the depth spellings and the water-equivalent mass
    # ones are accepted, because CMIP writes snw in kg m-2 and many regional
    # models write a depth in m — this cannot distinguish them from the units
    # attribute alone, and refusing either would fire on correct input. The
    # threshold's own units are stated on the input slot instead.
    "snow_amount": UnitFamily("m or cm of snow (or kg m-2 water equivalent)", (
        "m", "meter", "meters", "metre", "metres",
        "cm", "centimeter", "centimeters", "centimetre", "centimetres",
        "mm", "millimeter", "millimeters", "millimetre", "millimetres",
        "kgm-2", "kg/m2", "kg/m^2", "kgm**-2", "kgm2", "kgm^-2",
    )),
}

_TG = "daily mean temperature (TG)"
_TN = "daily minimum temperature (TN)"
_TX = "daily maximum temperature (TX)"
_RR = "daily precipitation amount (RR)"

_IN_TG = OperatorInput("TG — daily mean temperature", _TG, units="kelvin", key="tg")
_IN_TN = OperatorInput("TN — daily minimum temperature", _TN, units="kelvin", key="tn")
_IN_TX = OperatorInput("TX — daily maximum temperature", _TX, units="kelvin", key="tx")
_IN_RR = OperatorInput("RR — daily precipitation", _RR, units="precip", key="rr")

#: ``eca_gsl``: not a climatology at all.
_GSL_INPUTS = (
    _IN_TG,
    OperatorInput("Land-water mask", "fraction of each cell that is land, "
                  "0–1; cells at or above fland are treated as land",
                  units="", key="landmask"),
)

#: The ETCCDI bootstrapping indices take the running minimum and maximum of
#: their own input, exactly as the reference documentation's example does:
#: ``cdo etccdi_tx90p,5,1960,1989,m txfile -ydrunmin,5 txfile -ydrunmax,5 txfile``
def _bootstrap_inputs(base: OperatorInput) -> Tuple[OperatorInput, ...]:
    name = base.role.split(" —")[0]
    return (
        base,
        OperatorInput(f"Running minimum of {name}",
                      "running minimum over the same window as n",
                      "ydrunmin,{n} {in1}", units="same_as_input1",
                      key=f"{base.key}_runmin"),
        OperatorInput(f"Running maximum of {name}",
                      "running maximum over the same window as n",
                      "ydrunmax,{n} {in1}", units="same_as_input1",
                      key=f"{base.key}_runmax"),
    )


def _percentile_inputs(base: OperatorInput, percentile: int) -> Tuple[OperatorInput, ...]:
    """A daily series plus the running percentile of its reference period.

    ``ydrunpctl`` is itself a three-input operator — data, running minimum,
    running maximum — which is why the recipe is longer than the one-liner the
    index documentation quotes.
    """
    name = base.role.split(" —")[0]
    return (
        base,
        OperatorInput(
            f"{name}n{percentile} — {percentile}th-percentile climatology",
            f"{name} {percentile}th percentile of the reference period, one "
            "field per day of the year",
            f"ydrunpctl,{percentile},5 {{in1}} -ydrunmin,5 {{in1}} "
            "-ydrunmax,5 {in1}",
            units="same_as_input1", key=f"{base.key}n{percentile}"),
    )


#: The eleven three-input percentile operators of the Statistic section, each
#: mapped to the ``*min``/``*max`` stem that builds its second and third file.
#:
#: The value is the stem, not the whole recipe, because the two differ only in
#: that stem — except for the last two, which must also carry the window
#: parameter, and that is the entire reason this table has a second column.
#:
#: ``{param}`` is substituted with the window token when the operator has one.
#: Every entry below was run against a 400-step daily series on an 18x9 grid
#: before it was written here, and all eleven exit 0.
_PCTL_COMPANIONS: Dict[str, str] = {
    "timpctl":    "tim{stat} {in1}",
    "daypctl":    "day{stat} {in1}",
    "monpctl":    "mon{stat} {in1}",
    "yearpctl":   "year{stat} {in1}",
    "seaspctl":   "seas{stat} {in1}",
    "hourpctl":   "hour{stat} {in1}",
    "ydaypctl":   "yday{stat} {in1}",
    "ymonpctl":   "ymon{stat} {in1}",
    "yseaspctl":  "yseas{stat} {in1}",
    # The two that carry a window, and the reason each recipe was run rather
    # than transcribed. **Both modules' PDFs print the shorthand without it**:
    #
    #   Ydrunpctl:  cdo ydrunpctl,90,5 infile -ydrunmin infile -ydrunmax infile
    #               outfile
    #   Timselpctl: the same shape, with timselmin/timselmax
    #
    # Neither is runnable, and neither *fails* either. Measured on 2.6.3, both
    # **hang** — no output, no abort, no timeout of their own; killed after
    # twenty seconds. ``-ydrunmin infile`` on its own hangs the same way, which
    # is the known shape of a CDO operator reaching a required parameter it was
    # not given: it prompts on stdin and keeps prompting. Against a GUI that is
    # a frozen window rather than a failed run.
    #
    # With the window carried, both exit 0:
    #
    #   cdo timselpctl,90,5 in -timselmin,5 in -timselmax,5 in out   -> ok
    #   cdo ydrunpctl,90,5  in -ydrunmin,5  in -ydrunmax,5  in out   -> ok
    #
    # ``{n}`` and not ``{nsets}``/``{nts}``: ``format_recipe`` substitutes
    # exactly two names, ``in1`` and ``n``, so a recipe naming its parameter
    # after the operator's own spelling would raise ``KeyError`` the moment a
    # surface rendered it. ``{n}`` is this file's established placeholder for
    # "the window this node was given" — see ``_bootstrap_inputs`` — and
    # ``_window_argument`` in the model builder is what fills it from the
    # node's parameters.
    "timselpctl": "timsel{stat},{{n}} {in1}",
    "ydrunpctl":  "ydrun{stat},{{n}} {in1}",
}


def _pctl_inputs(operator: str, template: str) -> Tuple[OperatorInput, ...]:
    """The three slots of one Statistic-section percentile operator.

    Slot 1 is the data; slots 2 and 3 are the matching minimum and maximum over
    the same grouping, which is what CDO uses to bracket the histogram it bins
    the percentile into. They are not interchangeable and they are not optional:
    a run with three copies of the data exits 0 and writes wrong numbers.

    The recipe is a template over ``{in1}`` like every other, so
    ``operator_lab`` builds the companions and the model builder wires them with
    no special case for this family.
    """
    stem = operator[:-4]          # "timpctl" -> "tim"

    def recipe(stat: str) -> str:
        return template.format(stat=stat, in1="{in1}")

    return (
        OperatorInput(
            "Data — the series to take the percentile of",
            f"the field the percentile is computed over. The other two slots "
            f"are derived from this one and must come from the same series",
            units="", key=f"{stem}pctl_data"),
        OperatorInput(
            f"Minimum — the matching {stem}min",
            f"the {stem}-wise minimum of the same series, which brackets the "
            f"bottom of the histogram. A file that is not this operator's own "
            f"minimum gives a wrong percentile on exit 0, not an error",
            recipe=recipe("min"), units="same_as_input1",
            key=f"{stem}pctl_min"),
        OperatorInput(
            f"Maximum — the matching {stem}max",
            f"the {stem}-wise maximum of the same series, bracketing the top "
            f"of the histogram. Swapping it with the minimum slot is also not "
            f"an error to CDO",
            recipe=recipe("max"), units="same_as_input1",
            key=f"{stem}pctl_max"),
    )


def _wetday_percentile_inputs(percentile: int) -> Tuple[OperatorInput, ...]:
    """A precipitation series plus the day-of-year percentile the docs name."""
    return (
        _IN_RR,
        OperatorInput(
            f"RR{percentile} — {percentile}th-percentile climatology",
            f"{percentile}th percentile of wet-day precipitation in the "
            "reference period, one field per day of the year",
            f"ydaypctl,{percentile} {{in1}} -ydaymin {{in1}} -ydaymax {{in1}}",
            units="precip", key=f"rr{percentile}"),
    )


#: ``eca_cwfi``/``etccdi_csdi`` and ``eca_hwfi``/``etccdi_wsdi``: the second
#: file is a running percentile of the reference period, not a second series.
_SPELL_COLD = _percentile_inputs(_IN_TG, 10)
_SPELL_WARM = _percentile_inputs(_IN_TG, 90)


# ---------------------------------------------------------------------------
# The families whose second input is a statistics file, and will not say so
#
# Nine modules take two inputs where the second is not a second data series. It
# is a *statistics* file with one field per day, month, year, hour of the day,
# day of the year, month of the year or season, and every one of the nine says
# the same sentence in its documentation:
#
#     Usually infile2 is generated by an operator of the module <X>stat.
#
# Seven are arithmetic — Dayarith, Monarith, Yeararith, Yhourarith, Ydayarith,
# Ymonarith, Yseasarith — and two are comparison: Ymoncomp and Yseascomp, whose
# help text carries that sentence verbatim alongside "For each field in infile1
# the corresponding field of the timestep in infile2 with the same month [resp.
# season] of year is used". The table was written for the seven and named for
# them; the trap is the module's, not arithmetic's, so the name is not.
#
# The GUI asked for "2 files" and said nothing about which. A user who wires two
# raw series into ``monsub`` gets "Timestep 2 in <file> has wrong date! Current
# year=2000 mon=1, expected…" *after* the run, with no hint what to do about it —
# and ``ymonsub`` given two raw series does not even fail, it silently subtracts
# the wrong fields. ``ymoneq`` given two raw series is the same failure wearing a
# mask: every field comes back 0/1, and nothing about the output says the two
# operands were never meant to be compared field for field.
#
# The pairing is mechanical, so the app can simply state it. Each entry carries
# the companion statistics module, the operator the module's own documented
# example uses, what one field of infile2 covers, the operator that example is
# written for, and what that example is *for* — a comparison does not "subtract
# the average from a series", so the purpose is an entry rather than a fixed
# phrase in the sentence below.
# ---------------------------------------------------------------------------

#: ``CDO module title -> (stat module, example operator, what one field covers,
#: the operator the doc's example is written for, what that example does)``.
#: Keyed by module rather than by name prefix: prefixes cannot tell ``ymon``
#: from ``mon`` without ordering rules, and ``CDO_OPERATOR_MODULES`` already has
#: the answer exactly.
_COMPANION_MODULES: Dict[str, Tuple[str, str, str, str, str]] = {
    "Daily arithmetic":
        ("Daystat", "dayavg", "day of the series", "daysub",
         "subtract the average from a series"),
    "Monthly arithmetic":
        ("Monstat", "monavg", "month of the series", "monsub",
         "subtract the average from a series"),
    "Yearly arithmetic":
        ("Yearstat", "yearavg", "year of the series", "yearsub",
         "subtract the average from a series"),
    "Multi-year hourly arithmetic":
        ("Yhourstat", "yhouravg", "hour of the day", "yhoursub",
         "subtract the average from a series"),
    "Multi-year daily arithmetic":
        ("Ydaystat", "ydayavg", "day of the year", "ydaysub",
         "subtract the average from a series"),
    "Multi-year monthly arithmetic":
        ("Ymonstat", "ymonavg", "month of the year", "ymonsub",
         "subtract the average from a series"),
    "Multi-year seasonal arithmetic":
        ("Yseasstat", "yseasavg", "season of the year", "yseassub",
         "subtract the average from a series"),
    # The two comparison modules. Same shape, same companion file, same
    # documented sentence — the only difference is that the result is a mask,
    # so the example is written to mark where the series sits above its own
    # climatology rather than to subtract it.
    "Multi-year monthly comparison":
        ("Ymonstat", "ymonavg", "month of the year", "ymongt",
         "mark where a series exceeds its own multi-year monthly average"),
    "Multi-year seasonal comparison":
        ("Yseasstat", "yseasavg", "season of the year", "yseasgt",
         "mark where a series exceeds its own multi-year seasonal average"),
}


def _companion_inputs(operator: str) -> Tuple[OperatorInput, ...]:
    """The two input slots of one companion-module operator, or () if not one."""
    from .cdo_operator_catalog import CDO_OPERATOR_MODULES

    entry = _COMPANION_MODULES.get(CDO_OPERATOR_MODULES.get(operator, ""))
    if entry is None:
        return ()
    stat_module, example_operator, covers, _, _ = entry
    return (
        OperatorInput("Time series", "the data to operate on", key="series"),
        OperatorInput(
            f"{stat_module} file — one field per {covers}",
            f"not a second series: one field per {covers}, usually made by an "
            f"operator of the module {stat_module}",
            f"{example_operator} {{in1}}",
            units="same_as_input1",
            key=f"{example_operator}_companion"),
    )


def _companion_note(operator: str) -> str:
    """The sentence and the example the module's own documentation carries."""
    from .cdo_operator_catalog import CDO_OPERATOR_MODULES

    entry = _COMPANION_MODULES.get(CDO_OPERATOR_MODULES.get(operator, ""))
    if entry is None:
        return ""
    stat_module, example_operator, covers, doc_operator, purpose = entry
    return (
        f"infile2 is not a second data series: it holds one field per {covers}, "
        f"and is usually generated by an operator of the module {stat_module}. "
        f"Two raw series here produce wrong dates or silently wrong fields. "
        f"For example, to {purpose}: "
        f"cdo {doc_operator} infile -{example_operator} infile outfile"
    )


#: Arith and Comp are two-input as well, but their second input really is
#: another dataset, so they get a different warning: the broadcast rule, which
#: is what turns "this did what I meant" into "this reused one field 730 times".
#: Both modules document it in the same words — "One of the input files can
#: contain only one timestep or one field" — and Comp adds where the result's
#: metadata comes from, which is the other thing a user cannot predict from the
#: operands.
_BROADCAST_NOTE = (
    "One of the two input files may hold only a single timestep or a single "
    "variable; CDO then reuses it for every field of the other and says so on "
    "stdout. Neither the order of the variables nor the date is checked, so two "
    "files with different variables in them will be combined without complaint, "
    "and the output inherits its metadata from infile1 or infile2."
)

#: The modules whose documentation states the broadcast rule. Comp is here for
#: the same reason Arith is — ``cdo --help gt`` carries the sentence word for
#: word — and it was excluded only because the gate that appended the note asked
#: whether the module was *arithmetic* rather than whether it broadcasts.
_BROADCAST_MODULES = frozenset({
    "Arithmetic on two datasets",   # Arith
    "Comparison of two fields",     # Comp
})


# ---------------------------------------------------------------------------
# Conditional selection: which of the files is the mask
#
# ``nin`` says ``ifthen`` takes two files. It does not say that the first is a
# mask and the second the data, and swapping them is not an error. Measured on
# the installed CDO 2.6.3:
#
#     cdo ifthen data.nc mask.nc out.nc      # arguments the wrong way round
#     -> exit 0, no warning, and `cdo diff out.nc mask.nc` prints nothing:
#        the output *is* the mask file, copied through unchanged.
#
# Because "a value not equal to zero is treated as true", a data field used as
# a mask is true almost everywhere, so nothing is filtered and the mask comes
# back out in the data's place. The run looks like a success from every angle
# the app can see — exit code, stderr, a well-formed output file with no
# missing values in it.
#
# That is the same class of failure the ``*arith`` companion note was written
# for, and until now this family had none of that treatment: all five operators
# had no declared inputs at all, so the model builder printed no slot rows and
# the operator form folded both files into one unlabelled two-row widget whose
# only hint was "drag rows to reorder".
# ---------------------------------------------------------------------------

#: What a user cannot guess about the mask, in the order it bites: that it is
#: not the data, what counts as true, what a missing mask value does, and how
#: many fields it needs. All four are ``cdo -h ifthen``'s own claims.
_MASK_FIELD = (
    "Not the data — this is the file that decides. Any value other than zero "
    "selects, zero rejects, and where the mask itself is missing the output is "
    "missing too rather than falling back to anything. It needs either as many "
    "fields as the data file, or as many as one timestep of it, or exactly one."
)

#: The mask slot of ``ifthen``/``ifnotthen``/``ifthenelse`` — slot **0**, which
#: is the reverse of every ``*arith`` operator and the whole reason
#: ``OperatorInput.recipe_source`` exists.
#:
#: ``units`` is "" deliberately. A 0/1 mask has no unit expectation worth
#: checking, and the alternative — ``same_as_input1`` — would compare the mask
#: against the data it selects from and complain about a file that is exactly
#: right.
#:
#: The recipe is an *example*, not a requirement: CDO takes any field as a mask.
#: ``gtc,0`` is the one the CDO documentation itself reaches for when it needs a
#: mask in a hurry (the reducegrid example builds one with ``cdo -gtc,0 -topo``),
#: and it is a single operator, so the model builder can offer to wire it in.
#:
#: One consequence worth knowing rather than discovering: ``operator_lab`` builds
#: its mask by running this recipe against the ``tg`` sample, which is Kelvin
#: (253–293 K), so ``gtc,0`` is true in every cell and the lab's mask is all
#: ones. Every conditional operator still runs against a well-formed 0/1 mask —
#: a large improvement on the two raw series it used to be handed, where the
#: *data* was read as the mask — but it is a no-op mask, so the lab proves these
#: operators execute rather than that they select. Threshold-in-range would fix
#: that and cost the user a documented idiom they can read at a glance; the
#: numerical checks live in ``tests/test_conditional_selection.py`` instead,
#: which builds a genuinely mixed mask and asserts 13 of 32 cells survive.
_IN_MASK = OperatorInput(
    "Mask — infile1, the file that decides",
    _MASK_FIELD,
    "gtc,0 {in1}", units="", key="mask01", recipe_source=1)

#: The data slot of ``ifthen``/``ifnotthen``. ``key`` here picks the *sample*
#: ``operator_lab`` should feed the slot rather than constraining the field:
#: conditional selection takes any variable, and ``tg`` is simply a series that
#: already exists on the grid the mask is built from. Without a key here the
#: mask's recipe would have no base to build against and the lab would go back
#: to handing these operators two raw series.
_IN_MASKED_DATA = OperatorInput(
    "Data — infile2, the values that come out",
    "the fields the mask selects from; the output inherits its metadata — "
    "variable names, units, attributes — from this file and not from the mask",
    units="", key="tg")

_COND_INPUTS = (_IN_MASK, _IN_MASKED_DATA)


# ---------------------------------------------------------------------------
# Correlation: two raw series, and three constraints nothing checks
#
# These four are the case ``OperatorInput``'s docstring was written about. They
# take two data files, they will run against any second file that matches on
# grid, and what comes back is a well-formed number that means nothing if the
# second file was the wrong one. Undeclared, ``operator_inputs`` handed back
# "Input 1"/"Input 2" with empty field, units and key — so the model builder
# printed two unlabelled slot rows, the units check had no expectation to test,
# and ``operator_lab`` had no key to route a second file by.
#
# Three constraints, measured on the installed CDO 2.6.3, in the order they
# bite:
#
#   grid       identical, or CDO aborts: "Grid size of the input field 'tas' do
#              not match!" (exit 1, no output file left behind). The one
#              constraint the tool does enforce.
#   timesteps  equal — and this is the dangerous one. fldcor and fldcovar
#              **warn and exit 0**, truncating to the shorter series: 6 steps
#              against 3 wrote a 3-step answer. See _SURPRISING_DEFAULTS.
#   quantity   the same physical thing in both files. Nothing checks this at
#              all. Correlating temperature against precipitation is a
#              perfectly good CDO run and a meaningless number, which is why it
#              is stated in ``field`` rather than left to be discovered.
#
# ``recipe`` is "" for every slot here, and the empty string is the honest
# answer rather than an oversight: the second file is another measurement, not
# something derivable from the first. A recipe would be a command the model
# builder could offer to wire in, and every command it could offer — a copy, a
# shifted copy — would produce a correlation of exactly 1 or a lie.
#
# ``units="same_as_input1"`` gives ``core.units.check_inputs`` an expectation to
# test, which is what puts a warning in front of somebody about to correlate K
# against °C. Warn and never block: the two fields genuinely may carry
# different units — correlating temperature against pressure is a real thing to
# want — and the correlation coefficient is invariant to both scale and offset,
# so mismatched units are a reason to look rather than a reason to refuse.
# (Covariance is *not* scale-invariant, but its answer is still meaningful in
# the product of the two units, so the same reasoning holds.)
#
# The keys are ``series``/``series2``: the same slug ``_companion_inputs``
# already uses for "the operator's own data" plus the obvious second, which is
# what lets ``operator_lab`` hand these four its two independently generated
# samples — same grid, same time axis, different values — instead of pairing
# them by a name prefix that knows nothing about them.
# ---------------------------------------------------------------------------

#: What both slots of all four operators must satisfy. One string because it is
#: one set of constraints; the per-operator half of the story is the ``role``.
_CORRELATION_FIELD = (
    "A raw data series, not a statistic. It must sit on the same horizontal "
    "grid as the other input (CDO aborts otherwise) and carry the same number "
    "of timesteps, and it should hold the same physical quantity — nothing in "
    "CDO checks that two unrelated variables are being correlated, and the "
    "number that comes back looks exactly like a real one."
)


def _correlation_inputs(first: str, second: str) -> Tuple[OperatorInput, ...]:
    """The two slots of one Correlation operator, captioned with what it means.

    Both roles are spelled out per operator rather than shared, because the
    thing a user needs from the caption is what the *sign* of the answer will
    mean — which is a statement about this operator's axis, not about the
    family.
    """
    return (
        OperatorInput(first, _CORRELATION_FIELD,
                      units="same_as_input1", key="series"),
        OperatorInput(second, _CORRELATION_FIELD,
                      units="same_as_input1", key="series2"),
    )


_FLDCOR_INPUTS = _correlation_inputs(
    "Field 1 — infile1",
    "Field 2 — correlated against field 1 across the map, at every timestep")
_FLDCOVAR_INPUTS = _correlation_inputs(
    "Field 1 — infile1",
    "Field 2 — covaried with field 1 across the map, at every timestep")
_TIMCOR_INPUTS = _correlation_inputs(
    "Series 1 — infile1",
    "Series 2 — correlated against series 1 over time, at every gridpoint")
_TIMCOVAR_INPUTS = _correlation_inputs(
    "Series 1 — infile1",
    "Series 2 — covaried with series 1 over time, at every gridpoint")

#: What the four strong-wind indices need in their one slot, and the reason it
#: is worth a recipe: **VX is not a field a model writes**. All four count days
#: whose daily maximum horizontal wind speed passes a threshold, and that
#: quantity is sqrt(u^2+v^2) taken over the day — so a user holding ordinary
#: model output has the two components and not the field, and nothing else in
#: the app would tell them that. The recipe is the two-step form built from a
#: file holding both components.
_IN_VX = OperatorInput(
    "VX — daily maximum horizontal wind speed",
    "daily maximum of sqrt(u^2+v^2), in m/s — a derived field, not one most "
    "models write directly",
    recipe="setname,vx -daymax -sqrt -add -sqr -selname,u {in1} "
           "-sqr -selname,v {in1}",
    units="wind_speed", key="vx",
)
# Run verbatim on 2.6.3 against a 6-hourly file holding u and v: exit 0, one
# variable named vx, and ``cdo strwin,12`` on the result exits 0. The
# ``setname,vx`` is not decoration — without it the result inherits the name of
# ``-add``'s first operand and comes out called "u", which is the wrong name
# for a wind speed and the sort of thing that is noticed three operators later.

#: The three ocean operators name the fields they need, by variable name *and*
#: GRIB1 code, in their own help. Putting those names in the slot is what turns
#: "Sea water salinity not found!" — which arrives after a run, names nothing
#: the user typed, and is the abort all three give against any ordinary file —
#: into something readable before pressing Run.
_ADISIT_INPUTS = (OperatorInput(
    "Potential temperature and salinity",
    "one file holding both sea water potential temperature (name=tho, code=2) "
    "and sea water salinity (name=sao, code=5). MPIOM output; a file missing "
    "either aborts with \"Sea water salinity not found!\" before anything is "
    "computed",
    units="", key="mpiom_tho_sao"),)

_ADIPOT_INPUTS = (OperatorInput(
    "In-situ temperature and salinity",
    "one file holding both sea water in-situ temperature (name=t, code=2) and "
    "sea water salinity (name=sao or s, code=5). MPIOM output",
    units="", key="mpiom_to_sao"),)

_RHOPOT_INPUTS = (OperatorInput(
    "In-situ temperature and salinity",
    "one file holding both sea water in-situ temperature (name=to, code=20) "
    "and sea water salinity (name=sao, code=5). Aborts with \"In-situ "
    "temperature not found!\" against a file holding potential temperature "
    "(tho) instead — adisit converts one to the other",
    units="", key="mpiom_to_sao"),)


# ---------------------------------------------------------------------------
# Transformation: the five kinds of field this section will not tell you it
# wanted.
#
# One :class:`OperatorInput` per kind rather than one per operator, because the
# operators genuinely share them — ``dv2uv``, ``dv2uvl`` and ``dv2ps`` all take
# the same divergence/vorticity file, and the ``key`` is what tells
# ``operator_lab`` to build that file once and route it to all three.
#
# Each ``recipe`` was run before it was written down; they are the commands the
# lab's samples are actually built with.
# ---------------------------------------------------------------------------

_IN_SPECTRAL = OperatorInput(
    "Spectral coefficients",
    "spherical-harmonic coefficients — what CDO writes as a 1-D nsp axis and "
    "sinfon reports as \"spectral : points=506 nsp=253 T21\". A gridpoint "
    "field here does not fail: sp2gp warns \"No spectral data found!\", exits "
    "0, and copies the input through unchanged",
    recipe="gp2sp {in1}", units="", key="spectral", shape="spectral")

_IN_GAUSSIAN = OperatorInput(
    "Global regular Gaussian grid",
    "a field on a global regular Gaussian grid (t21grid, t63grid …) — the only "
    "grid the forward transform reads. A lonlat field here does not fail: "
    "gp2sp warns \"No data on regular Gaussian grid found!\", exits 0, and "
    "copies the input through unchanged",
    units="", key="gaussian", shape="gaussian")

_IN_UV_GAUSSIAN = OperatorInput(
    "U and V wind, on a Gaussian grid",
    "one file holding both wind components on a global regular Gaussian grid, "
    "named u and v or coded 131 and 132. A file missing either does not fail: "
    "uv2dv warns \"U-wind not found!\" and \"V-wind not found!\", exits 0, and "
    "copies the input through unchanged",
    units="", key="wind_gaussian", shape="uv")

_IN_SD_SVO = OperatorInput(
    "Divergence and vorticity, spectral",
    "one file holding both divergence and vorticity as spectral coefficients, "
    "named sd and svo or coded 155 and 138 — which is exactly what uv2dv "
    "writes. A file missing either does not fail: dv2uv warns \"Divergence not "
    "found!\" and \"Vorticity not found!\", exits 0, and copies the input "
    "through unchanged",
    recipe="uv2dv {in1}", units="", key="divergence_vorticity",
    shape="divergence_vorticity")

_IN_COMPLEX = OperatorInput(
    "Complex-valued field",
    "a field of complex numbers, which only NetCDF4 and EXTRA can hold. This "
    "is the one slot in the section whose mistake is *not* silent — a real "
    "field is \"(Abort): This operator needs fields with complex numbers!\", "
    "exit 1 — but building the input needs a global option this app cannot "
    "emit: cdo -f nc4 retocomplex infile outfile",
    recipe="retocomplex {in1}", units="", key="complex", shape="complex")

#: The seven Pressure/Derivepar operators all need the same thing and all give
#: the same abort without it: "No 3D variable with hybrid sigma pressure
#: coordinate found!". Measured on 2.6.3 against a plain lonlat sample — every
#: one of the seven, identically.
_HYBRID_LEVEL_INPUT = OperatorInput(
    "Model-level data on hybrid sigma pressure levels",
    "a 3D variable on hybrid sigma pressure levels, with the surface pressure "
    "(code 134) and the vertical coordinate table the levels are defined by. "
    "Anything else is \"No 3D variable with hybrid sigma pressure coordinate "
    "found!\"",
    units="", key="hybrid_levels",
)


_OPERATOR_INPUTS: Dict[str, Tuple[OperatorInput, ...]] = {
    # ---------- Import/Export ----------
    #
    # The only operators in the catalog whose *input* is not a CDO dataset, and
    # the reason ``OperatorInput`` grew a ``file_kind`` at all. Everywhere else
    # the default answers correctly; here it named the two formats the operator
    # cannot read.
    #
    # ``import_binary``: "cdo import_binary infile.ctl outfile", and the manual
    # is explicit that the .ctl **is** infile — "This operator imports gridded
    # binary data sets via a GrADS data descriptor file … The descriptor file
    # is an ASCII file that can be created easily with a text editor". The
    # binary it points at is named *inside* it (``DSET ^infile.bin``) and is
    # never handed to CDO, which is the mistake the old chooser invited: a user
    # shown NetCDF and GRIB filters reaches for the .bin.
    #
    # ``import_grads`` is the same operator under another name — aliases
    # inherit from their target in ``_build_operator_schema``, so listing it
    # here keeps the two from drifting the way they once did over parameters.
    **{name: (
        OperatorInput(
            "GrADS data descriptor",
            "the ASCII .ctl file describing the binary data — not the binary "
            "file itself, which the descriptor names in its own DSET line and "
            "which CDO is never given directly",
            file_kind=_ft.CTL, holds_variable=False),
    ) for name in ("import_binary", "import_grads")},

    # "This operator imports gridded CM-SAF (Satellite Application Facility on
    # Climate Monitoring) HDF5 files." HDF5 is not otherwise a CDO input — this
    # operator is the way in — so offering it on every other form was as wrong
    # as withholding it here.
    "import_cmsaf": (
        OperatorInput(
            "CM-SAF HDF5 product",
            "a CM-SAF HDF5 file. The manual's example names a .hdf "
            "(cdo -f nc remapbil,r360x180 -import_cmsaf cmsaf_product.hdf "
            "out.nc); satellite-projection data needs a separate geolocation "
            "file passed to setgrid alongside",
            file_kind=_ft.HDF5, holds_variable=False),
    ),

    # ---------- Transformation ----------
    #
    # Declared for one reason, and it is the strongest case in this table.
    # Every operator here, handed a field it cannot use, **warns on stderr,
    # exits 0, and copies the input through unchanged**. Measured on 2.6.3
    # against a plain ``cdo -f nc random,r18x9,1`` lonlat file, and in every
    # case ``cdo sinfon`` on the output showed the same variable on the same
    # 18x9 lonlat grid the input had:
    #
    #   cdo gp2sp lonlat.nc o.nc -> "(Warning): No data on regular Gaussian
    #                                grid found!"                     exit 0
    #   cdo sp2gp lonlat.nc o.nc -> "(Warning): No spectral data found!"
    #                                                                 exit 0
    #   cdo uv2dv lonlat.nc o.nc -> "(Warning): U-wind not found!" and
    #                               "(Warning): V-wind not found!"    exit 0
    #   cdo dv2uv lonlat.nc o.nc -> "(Warning): Divergence not found!" and
    #                               "(Warning): Vorticity not found!" exit 0
    #   cdo dv2ps lonlat.nc o.nc -> the same two warnings              exit 0
    #
    # So the slot is where the requirement has to be said: at the point the
    # file is chosen, not in a note nobody reads after the run looked fine.
    # ``shape`` is what ``core/fieldshape.py`` checks the chosen file against.
    "sp2gp":  (_IN_SPECTRAL,),
    "sp2gpl": (_IN_SPECTRAL,),
    "sp2sp":  (_IN_SPECTRAL,),
    "spcut":  (_IN_SPECTRAL,),
    "gp2sp":  (_IN_GAUSSIAN,),
    "gp2spl": (_IN_GAUSSIAN,),
    "uv2dv":  (_IN_UV_GAUSSIAN,),
    "uv2dvl": (_IN_UV_GAUSSIAN,),
    "dv2uv":  (_IN_SD_SVO,),
    "dv2uvl": (_IN_SD_SVO,),
    "dv2ps":  (_IN_SD_SVO,),
    "fourier": (_IN_COMPLEX,),

    # ---------- Miscellaneous ----------
    #
    # ``wct``. CDO documents the units of both slots and the validity range,
    # and every part of that is silent when it is wrong: outside the range the
    # operator writes the missing value rather than failing, so a run against
    # the wrong units produces a file that is entirely missing and exits 0.
    "wct": (
        OperatorInput("Temperature — in °C",
                      "daily mean temperature in degrees Celsius. Only valid "
                      "at or below 33 °C; warmer cells are written as missing "
                      "rather than reported. A Kelvin field is therefore "
                      "silently all-missing, not an error",
                      units="celsius", key="t_celsius"),
        OperatorInput("Wind speed — in m/s",
                      "daily mean wind speed in m/s. Only valid at or above "
                      "1.39 m/s; slower cells are written as missing",
                      units="wind_speed", key="wind_speed"),
    ),
    # ``fdns``. The first slot is Kelvin here rather than °C — the opposite of
    # wct's, in the same section — which is why each slot names its own units
    # instead of inheriting a section-wide assumption.
    "fdns": (
        OperatorInput("TN — daily minimum temperature, in Kelvin",
                      "daily minimum temperature. Read as Kelvin, unlike "
                      "wct's first input, which is read as °C",
                      units="kelvin", key="tn"),
        OperatorInput("Surface snow amount",
                      "snow on the ground. A day counts as frost-without-snow "
                      "when the snow amount is below 1 cm",
                      units="snow_amount", key="snow"),
    ),
    "strwin": (_IN_VX,),
    "strbre": (_IN_VX,),
    "strgal": (_IN_VX,),
    "hurr":   (_IN_VX,),
    # ``mrotuvb`` takes one wind component per file, which is the whole reason
    # to declare it: "Input 1"/"Input 2" says nothing about which is which, and
    # swapping them produces a rotation rather than an error.
    "mrotuvb": (
        OperatorInput("U — zonal component",
                      "the u wind component on a rotated Arakawa C grid "
                      "(MPIOM). Both files must be on the same grid, and then "
                      "the noint flag is required",
                      units="", key="mpiom_u"),
        OperatorInput("V — meridional component",
                      "the v wind component, on the same rotated Arakawa C "
                      "grid as the first file",
                      units="", key="mpiom_v"),
    ),
    "adisit": _ADISIT_INPUTS,
    "adipot": _ADIPOT_INPUTS,
    "rhopot": _RHOPOT_INPUTS,
    "pressure":         (_HYBRID_LEVEL_INPUT,),
    "pressure_half":    (_HYBRID_LEVEL_INPUT,),
    "pressure_full":    (_HYBRID_LEVEL_INPUT,),
    "delta_pressure":   (_HYBRID_LEVEL_INPUT,),
    "sealevelpressure": (_HYBRID_LEVEL_INPUT,),
    "gheight":          (_HYBRID_LEVEL_INPUT,),
    "gheight_half":     (_HYBRID_LEVEL_INPUT,),
    "gheight_full":     (_HYBRID_LEVEL_INPUT,),
    "air_density":      (_HYBRID_LEVEL_INPUT,),
    # ``mastrfu``: the abort measured on 2.6.3 against a plain sample is
    # "Unexpected vertical grid surface!", which names the symptom rather than
    # the requirement.
    "mastrfu": (OperatorInput(
        "Zonal mean of the v-velocity, on pressure levels",
        "the meridional wind averaged over longitude, on pressure levels — "
        "the output of zonmean applied to a v field. The recipe below builds "
        "the zonal mean; the *pressure levels* are the part it cannot supply, "
        "and a surface-level field is \"Unexpected vertical grid surface!\" "
        "even after zonmean has run",
        recipe="zonmean -selname,v {in1}", units="", key="v_zonmean"),),
    # The healpix pair: the grid, not the field, is what has to be right.
    "hpdegrade": (OperatorInput(
        "Data on a healpix grid",
        "a field on a healpix grid, at a resolution higher than the nside "
        "asked for. Anything else is \"Input grid is not healpix!\"",
        units="", key="healpix"),),
    "hpupgrade": (OperatorInput(
        "Data on a healpix grid",
        "a field on a healpix grid. Anything else is \"Input grid is not "
        "healpix!\"",
        units="", key="healpix"),),
    # The two NCL wind operators find their components by the u=/v= parameters,
    # so the slot's job is to say both must be in the one file.
    "uv2vr_cfd": (OperatorInput(
        "U and V in one file",
        "one file holding both wind components, named by the u= and v= "
        "parameters. A missing one is \"u not found!\"",
        units="", key="uv_pair"),),
    "uv2dv_cfd": (OperatorInput(
        "U and V in one file",
        "one file holding both wind components, named by the u= and v= "
        "parameters. A missing one is \"u not found!\"",
        units="", key="uv_pair"),),
    # ``after`` is declared nin=-1 by the catalog, so exactly one slot is used.
    "after": (OperatorInput(
        "ECHAM spectral or Gaussian data",
        "ECHAM GRIB or NetCDF output in spectral or Gaussian representation. "
        "Anything else is \"Unsupported file structure (no spectral or "
        "Gaussian data found)!\". The selection is made by a namelist on "
        "standard input, not by the operator's parameters",
        units="", key="echam"),),

    # -- one input; declared so the units check has an expectation to test --
    "eca_cdd": (_IN_RR,), "etccdi_cdd": (_IN_RR,),
    "eca_cwd": (_IN_RR,), "etccdi_cwd": (_IN_RR,),
    "eca_rr1": (_IN_RR,), "eca_r1mm": (_IN_RR,), "etccdi_r1mm": (_IN_RR,),
    "eca_sdii": (_IN_RR,), "etccdi_sdii": (_IN_RR,),
    "eca_pd": (_IN_RR,),
    "eca_r10mm": (_IN_RR,), "eca_r20mm": (_IN_RR,),
    "etccdi_r10mm": (_IN_RR,), "etccdi_r20mm": (_IN_RR,),
    "eca_rx1day": (_IN_RR,), "etccdi_rx1day": (_IN_RR,),
    "etccdi_rx1daymon": (_IN_RR,),
    "eca_rx5day": (_IN_RR,), "etccdi_rx5day": (_IN_RR,),
    "etccdi_rx5daymon": (_IN_RR,),
    "eca_cfd": (_IN_TN,),
    "eca_fd": (_IN_TN,), "etccdi_fd": (_IN_TN,),
    "eca_tr": (_IN_TN,), "etccdi_tr": (_IN_TN,),
    "eca_csu": (_IN_TX,),
    "eca_su": (_IN_TX,), "etccdi_su": (_IN_TX,),
    "eca_id": (_IN_TX,), "etccdi_id": (_IN_TX,),
    "eca_hd": (_IN_TG,), "etccdi_hd": (_IN_TG,),

    # -- two inputs, the second a climatology of the reference period --
    "eca_cwdi": (_IN_TN, OperatorInput(
        "TNnorm — reference-period mean", "TN mean of the reference period, "
        "one field per day of the year",
        "ydrunmean,5 {in1}", units="same_as_input1", key="tnnorm")),
    "eca_hwdi": (_IN_TX, OperatorInput(
        "TXnorm — reference-period mean", "TX mean of the reference period, "
        "one field per day of the year",
        "ydrunmean,5 {in1}", units="same_as_input1", key="txnorm")),
    "eca_cwfi": _SPELL_COLD,
    "etccdi_csdi": _SPELL_COLD,
    "eca_hwfi": _SPELL_WARM,
    "etccdi_wsdi": _SPELL_WARM,

    "eca_tg10p": _percentile_inputs(_IN_TG, 10),
    "eca_tg90p": _percentile_inputs(_IN_TG, 90),
    "eca_tn10p": _percentile_inputs(_IN_TN, 10),
    "eca_tn90p": _percentile_inputs(_IN_TN, 90),
    "eca_tx10p": _percentile_inputs(_IN_TX, 10),
    "eca_tx90p": _percentile_inputs(_IN_TX, 90),

    "eca_r75p": _wetday_percentile_inputs(75),
    "eca_r75ptot": _wetday_percentile_inputs(75),
    "eca_r90p": _wetday_percentile_inputs(90),
    "eca_r90ptot": _wetday_percentile_inputs(90),
    "eca_r95p": _wetday_percentile_inputs(95),
    "eca_r95ptot": _wetday_percentile_inputs(95),
    "eca_r99p": _wetday_percentile_inputs(99),
    "eca_r99ptot": _wetday_percentile_inputs(99),

    # -- two inputs that are not a climatology at all --
    # Two raw series rather than a series and a climatology.
    "eca_etr": (_IN_TX, OperatorInput(
        "TN — daily minimum temperature", _TN + ", the same period as input 1",
        units="same_as_input1", key="tn")),
    "eca_gsl": _GSL_INPUTS,
    "etccdi_gsl": _GSL_INPUTS,

    # -- correlation: two raw series, same grid, same length, same quantity --
    "fldcor": _FLDCOR_INPUTS,
    "fldcovar": _FLDCOVAR_INPUTS,
    "timcor": _TIMCOR_INPUTS,
    "timcovar": _TIMCOVAR_INPUTS,

    # -- vertical interpolation onto a 3D coordinate: slot order is silent --
    #
    # intlevel3d is exactly the case OperatorInput's docstring describes for
    # eca_cwfi, and it was undeclared. From ``cdo -h intlevel3d``: "infile1
    # contains the 3D data variables and infile2 the 3D vertical source
    # coordinate. The parameter tgtcoordinate is a datafile with the 3D
    # vertical target coordinate."
    #
    # Swapping the two runs. Measured on 2.6.3 against a data/source-coordinate
    # pair on one grid:
    #
    #   cdo intlevel3d,tgt.nc data.nc src.nc out.nc      -> exit 0, fldmean 0.684423
    #   cdo intlevel3d,tgt.nc src.nc data.nc swapped.nc  -> exit 0, fldmean -9e+33
    #
    # so the swapped call writes a well-formed file of nothing but missing
    # values, and ``cdo diffn`` reports 9 of 18 fields differing. No error, no
    # warning, exit 0 both ways — which is why the slots are captioned rather
    # than left as "Input 1" and "Input 2".
    #
    # No recipe on either slot, and that is a statement rather than an omission:
    # the source coordinate is a property of the dataset, not something that can
    # be derived from the data file. Saying so is what the docstring asks for.
    **{name: (
        OperatorInput(
            "3D data variables",
            "the fields to interpolate, on the source vertical coordinate"),
        OperatorInput(
            "3D vertical source coordinate",
            "one 3D variable giving the source level of every gridpoint — "
            "height or pressure, in the same units as the target coordinate. "
            "No recipe: it comes with the dataset and cannot be derived from "
            "input 1",
            # A coordinate, not the data variable, so the pairing check must
            # not look for the latter in it — see OperatorInput.holds_variable.
            holds_variable=False),
    ) for name in ("intlevel3d", "intlevelx3d")},

    # intyear takes two files bracketing the years asked for, and was likewise
    # undeclared. Order is chronological — CDO reports "Year 2001 out of bounds
    # (first year 2000; last year 2000)!" naming the span the two files define —
    # and no recipe exists for either: they are two separate years of data.
    "intyear": (
        OperatorInput("earlier year",
                      "the data for the year before the ones being interpolated"),
        OperatorInput("later year",
                      "the data for the year after the ones being interpolated. "
                      "No recipe: this is a second year of observations"),
    ),

    # -- conditional selection: the mask is the *first* file --
    "ifthen": _COND_INPUTS,
    "ifnotthen": _COND_INPUTS,

    # ifthenelse takes a third file, and CDO is specific about two things it
    # does not share with the two-file forms: infile2 and infile3 must have the
    # same number of fields, and a *missing* mask value yields missing rather
    # than infile3. Verified on 2.6.3 against a mask holding 0, 1 and missing —
    # where the mask was missing the output was missing, while infile3 there
    # held -0.000328708.
    "ifthenelse": (
        _IN_MASK,
        OperatorInput(
            "Data used where the mask is true — infile2",
            "the values that come out where the mask is non-zero; the output "
            "takes its metadata from this file. Must hold the same number of "
            "fields as infile3.",
            units="", key="tg"),
        OperatorInput(
            "Data used where the mask is false — infile3",
            "the values that come out where the mask is zero. Not used where "
            "the mask is *missing* — that yields missing. Must hold the same "
            "number of fields as infile2.",
            units="", key="tx"),
    ),

    # The constant forms take one file and that file *is* the mask, which is
    # the thing their signature hides most completely: "1 input" reads like
    # "the data" everywhere else in the app.
    #
    # No recipe, and the empty string is the honest answer rather than an
    # oversight: the only slot there is would have to be built from itself.
    # What to build it from belongs in the field text, where it is advice
    # rather than a command the graph could wire up wrongly.
    "ifthenc": (OperatorInput(
        "Mask — the input is the mask, not the data",
        "the file that decides where the constant is written. Non-zero is "
        "true; the output is c there and missing everywhere else, including "
        "where the mask itself is missing. Build one from a data field with a "
        "comparison operator, e.g. cdo gtc,0 data mask.",
        units="", key="mask01"),),
    "ifnotthenc": (OperatorInput(
        "Mask — the input is the mask, not the data",
        "the file that decides where the constant is written. The output is c "
        "where the mask is zero, and missing where it is non-zero or missing. "
        "Build one from a data field with a comparison operator, e.g. "
        "cdo gtc,0 data mask.",
        units="", key="mask01"),),

    # -- File operations: five operators whose second file is not "more data" --
    #
    # ``nin`` says how many files these take and nothing about what they are,
    # and for all five the relationship between the files is the operator. A
    # user handed "Input 2" for ``replace`` has no way to know from the app that
    # the file has to share variable *names* with the first, and CDO's failure
    # when it does not is "Variable X not found" — about a variable, not about
    # which file was wrong.
    #
    # ``merge``, ``mergetime`` and ``collgrid`` take a variable number of
    # inputs, so ``operator_inputs`` gives them one slot; the text describes
    # what every input must be rather than what the second one must be.
    "replace": (
        OperatorInput(
            "Data to replace variables in — infile1",
            "the full dataset. Variables it shares a name with infile2 are the "
            "ones that get replaced; the rest pass through untouched.",
            units="", key=""),
        OperatorInput(
            "Replacement variables — infile2",
            "the variables to substitute in, under the names they already have "
            "in infile1. Both files need the same number of timesteps, and a "
            "name may appear only once in either.",
            recipe="selname,<name> {in1} replacement.nc",
            units="", key=""),
    ),
    "mergegrid": (
        OperatorInput(
            "Target grid — infile1",
            "the larger dataset, whose grid the output keeps.",
            units="", key=""),
        OperatorInput(
            "Patch — infile2",
            "a region to paste into infile1. Its grid must be smaller than or "
            "equal to infile1's and at exactly the same resolution, rectilinear "
            "only, with the same variables and the same number of timesteps. "
            "Only its non-missing values are used.",
            recipe="sellonlatbox,<lon1>,<lon2>,<lat1>,<lat2> {in1} patch.nc",
            units="", key=""),
    ),
    "merge": (OperatorInput(
        "Datasets holding different fields, on the same timesteps",
        "every input contributes its own variables to one output. They must "
        "have the same number of timesteps and no field in common — either "
        "different variables, or different levels of one variable, but not a "
        "mixture of the two across files. Use mergetime for the other case, "
        "where the files hold the same fields at different times.",
        units="", key=""),),
    "mergetime": (OperatorInput(
        "Datasets holding the same fields, at different times",
        "every input has the same structure and the same variables, and the "
        "output is all of their timesteps sorted by date and time. Use merge "
        "for the other case, where the files hold different variables at the "
        "same times.",
        units="", key=""),),
    # The cleanest recipe in the file, because it is exact rather than
    # advisory: collgrid's inputs are literally what distgrid writes.
    "collgrid": (OperatorInput(
        "The pieces of one grid, as distgrid wrote them",
        "every input holds the same variables and timesteps on a different "
        "region of one horizontal grid, and together they must tile it "
        "completely. On a structured lon/lat grid the regions have to "
        "reassemble into a rectangular box; on an unstructured grid they are "
        "concatenated in the order given.",
        recipe="distgrid,2,3 {in1} piece",
        units="", key=""),),

    # reducegrid's mask arrives as a *parameter*, not as an input file, so its
    # one input slot really is the data. Declared anyway so the slot is not
    # captioned "Input 1" next to an operator whose whole subject is a mask.
    "reducegrid": (OperatorInput(
        "Data to reduce — the mask is the 'mask' parameter below, not a file "
        "slot",
        "the fields to cut down to the mask's non-zero locations. Its "
        "horizontal grid must be identical to the mask's.",
        units="", key="tg"),),

    # -- the six eof operators: one input, and it is not the raw series --
    #
    # Declared for the reason ``reducegrid``'s single slot is: the operator's
    # whole subject is a precondition on this file, and "Input 1" says none of
    # it. CDO exits 0 on a raw series (measured on 2.6.3), so nothing downstream
    # will catch the mistake either.
    **{name: (OperatorInput(
        "Anomalies — the mean already removed",
        "the field with its time mean subtracted, not the raw series. "
        "Build it with cdo sub infile -timmean infile anom_file. CDO does not "
        "check this and does not warn.",
        recipe="", units="", key="anomalies"),) for name in (
            "eof", "eoftime", "eofspatial", "eof3d", "eof3dtime",
            "eof3dspatial")},

    # -- eofcoeff: the eigenvectors first, the data second --
    #
    # The ``ifthen`` mask-swap trap in a new place, and worse. Both slots
    # captioned "Input 1"/"Input 2", and the two files are an eigenvector file
    # and an anomaly series — same variable name, same grid, same units, and
    # nothing but the timestep count to tell them apart. Measured on 2.6.3:
    # ``cdo eofcoeff eofs anom pc`` wrote 3 files (one per eigenvector); the
    # swap ``cdo eofcoeff anom eofs bad`` exited 0 and wrote **730**, one per
    # timestep of the series. So the wrong order is not a wrong-looking answer,
    # it is 730 files and a full disk.
    #
    # Neither slot declares a ``recipe``, and the constraint is worth stating
    # rather than working around. ``format_recipe`` builds a *single-output*
    # command line over ``{in1}`` — "cdo <recipe>" with one target — and the
    # command that builds slot 0 is ``cdo eof,<neof> {in1} evalfile eoffile``,
    # which has two outputs and wants the *second* of them. Nothing in the
    # recipe string can say "the second output of this", and the one-click
    # companion button in the model builder reads it through
    # ``_single_operator_recipe``, which would wire the new node's only port
    # into this slot and silently connect the eigenvalues — the wrong file, with
    # a button on it. Slot 1 is refused a recipe for a different reason: its
    # ``{in1}`` would have to name the raw series, and the raw series is not one
    # of this operator's inputs, so ``recipe_source`` has no slot to point at.
    #
    # The commands go in ``field`` instead, the way ``ifthenc`` puts its
    # ``gtc,0`` recipe in the description rather than claiming a slot recipe it
    # cannot honour. See ``_eof_note`` for the same lines in the description,
    # and OPERATOR_FIX_PLAN.md for why widening the mechanism was not done here.
    #
    # ``units="same_as_input1"`` on slot 1 rather than on slot 0: the check
    # compares a slot against the *first* input, so the expectation has to hang
    # off the second one. Both files carry the data's own units — eigenvectors
    # are a normalised field in the same quantity — so a mismatch means the two
    # files are not from the same run.
    **{name: (
        OperatorInput(
            "EOFs — the eigenvector file",
            "the *second* output of a matching eof run, holding one field per "
            "eigenvector. Not the eigenvalue file, and not the data: this slot "
            "is what eofcoeff projects onto. Build it with "
            "cdo eof,<neof> anomfile evalfile eoffile — the eigenvector file is "
            "the last of those, outfile2.",
            units="", key="eofs"),
        OperatorInput(
            "Anomalies — the series the EOFs were computed from",
            "the same anomaly field that was handed to eof, not the raw series "
            "and not a different period. One coefficient timestep is written "
            "per timestep of this file into every output file, which is why "
            "getting the two slots the wrong way round writes one file per "
            "timestep of the series instead of one per eigenvector. Build it "
            "with cdo sub infile -timmean infile anomfile.",
            units="same_as_input1", key="anomalies"),
    ) for name in ("eofcoeff", "eofcoeff3d")},

    # -- addtrend / subtrend: the series, then a, then b, in that order --
    #
    # The ``eofcoeff`` trap again, and with the same three files in the same
    # section: all three slots carry the input's own variable name, its own
    # grid and its own units, so nothing but the position says which is which.
    # Undeclared they were captioned "Input 1"/"Input 2"/"Input 3", and the
    # order is the whole of the operator.
    #
    # Measured on 2.6.3, twelve monthly steps of a series with a = 100 and
    # b = 3 (so that a swap is visible; on a series where a == b it is not)::
    #
    #     cdo trend nd.nc nda.nc ndb.nc
    #     cdo subtrend nd.nc nda.nc ndb.nc ok.nc     -> exit 0, field mean 0
    #                                                   at every timestep
    #     cdo subtrend nd.nc ndb.nc nda.nc swap.nc   -> exit 0, field mean runs
    #                                                   97, 0, -97 ... -970
    #     cdo subtrend nd.nc nd.nc nd.nc junk.nc     -> exit 0, field mean runs
    #                                                   0, -97, -194 ... -1067
    #
    # No warning, no non-zero exit, and a full plausible field either way. That
    # is the same failure class as ``fldcor``'s silent truncation in
    # ``core/pairing.py``: a finished file of wrong numbers that every surface
    # in this application reports as a success.
    #
    # The recipe is *exact* rather than advisory, like ``collgrid``'s: there is
    # exactly one command that produces these two files and it produces both at
    # once. It is quoted whole on both slots, and each slot says which of the
    # two outputs it wants, because ``format_recipe`` renders a single command
    # line and cannot say "the second output of this" — the same limit
    # ``eofcoeff`` records above. Unlike ``eofcoeff`` the recipe is still
    # declared here rather than moved into ``field``: both files come from the
    # one run, the model builder's companion button wires the new node's
    # *first* output, and that first output is genuinely slot 2's file. Slot 3
    # gets the same recipe so the lab and the panel can both build it; what the
    # button cannot do for slot 3 is say "take output 2", which is why the
    # sentence in ``field`` names ``bfile`` explicitly.
    #
    # Distinct ``key`` values so ``operator_lab`` can build one pair and route
    # each file to the slot that wants it, the way the ECA climatologies are
    # routed. ``units="same_as_input1"`` on both: a and b are in the input's
    # own units — CDO copies them across unchanged, which for b is itself a
    # lie worth knowing (see ``_REGRESSION_TREND_NOTE``), but it does mean a
    # unit mismatch here means the files are from different runs.
    **{name: (
        OperatorInput(
            "The series — the data the trend is applied to",
            "the full time series, one field per timestep. This is infile1, "
            "and it is the only one of the three inputs that is not a trend "
            "coefficient.",
            units="", key="series"),
        OperatorInput(
            "a — the intercept, from trend's outfile1",
            "the first output of a trend run over the same series: one "
            "timestep holding the constant term. It must be outfile1 and not "
            "outfile2 — the two files are indistinguishable by name, units and "
            "shape, and CDO exits 0 on a swap and writes a plausible wrong "
            "answer rather than complaining.",
            recipe="trend {in1} afile bfile",
            units="same_as_input1", key="trend_intercept"),
        OperatorInput(
            "b — the slope, from trend's outfile2",
            "the *second* output of the same trend run — bfile in the recipe "
            "below, not afile. One timestep holding the change per timestep. "
            "Getting this and the previous slot the wrong way round is not an "
            "error to CDO.",
            recipe="trend {in1} afile bfile",
            units="same_as_input1", key="trend_slope"),
    ) for name in ("addtrend", "subtrend")},

    # -- three inputs: the bootstrapping indices --
    "etccdi_tx90p": _bootstrap_inputs(_IN_TX),
    "etccdi_tx10p": _bootstrap_inputs(_IN_TX),
    "etccdi_tn90p": _bootstrap_inputs(_IN_TN),
    "etccdi_tn10p": _bootstrap_inputs(_IN_TN),
    "etccdi_r95p": _bootstrap_inputs(_IN_RR),
    "etccdi_r99p": _bootstrap_inputs(_IN_RR),

    # -- the two ensemble modules whose first file is not a member --
    #
    # All five of these are ``nin == -1``, so ``operator_inputs`` renders one
    # slot for them and that slot is the whole of what can be said — which is
    # lucky, because for these five it is also the only thing worth saying: the
    # first file is *not* an ensemble member and putting a member there is not
    # an error, just a different and wrong answer.
    #
    # Ensval. The first file is the reference — a climatology, an observation
    # or a reanalysis — that the ensemble's skill is measured against. Feed six
    # members and no reference and CDO scores the ensemble against one of its
    # own members, exits 0, and writes three or four well-formed files.
    **{name: (
        OperatorInput(
            "Reference — the observation or climatology to score against",
            "the first file is the *reference*, not a member: the "
            "climatology, observation or reanalysis the ensemble's skill is "
            "measured relative to. Every file after it is an ensemble member. "
            "Passing a member here is not an error — the scores come out "
            "against that member instead",
            units="", key="ens_reference"),
    ) for name in ("enscrps", "ensbrs")},

    # Ensstat2. Same shape, different word for it: the module page calls the
    # first file ``obsfile``.
    **{name: (
        OperatorInput(
            "Observations — obsfile, not an ensemble member",
            "the first file holds the observations the ensemble is compared "
            "against; the remaining files are the members. CDO's own synopsis "
            "for this module names it obsfile",
            units="", key="ens_obsfile"),
    ) for name in ("ensrkhisttime", "ensrkhistspace", "ensroc")},

    # -- three inputs: the eleven percentile operators of the Statistic section
    #
    # The same trap the ECA indices above are declared against, in a section
    # where it is easier to fall into because nothing about the operator's name
    # suggests it takes anything but data. All eleven report ``nin=3`` and had
    # no declared inputs at all, so the GUI asked for "3 files" and said
    # nothing about what they were — and wiring three copies of the raw series
    # produces a finished file of wrong numbers rather than an error.
    #
    # Each recipe below was **run** before being written down; see
    # ``_pctl_inputs`` for the shape and the two that carry a window parameter.
    **{op: _pctl_inputs(op, stat) for op, stat in _PCTL_COMPANIONS.items()},
}


# ---------------------------------------------------------------------------
# What each output file holds
#
# Only the multi-output operators are declared. A ``(n|1)`` operator's single
# output is "the result" and a caption saying so adds nothing; a ``(n|2)``
# operator's two outputs are two different objects, and which is which is not
# guessable from the command line.
#
# Every claim below was measured on CDO 2.6.3 against
# ``sample_climate_tg.nc`` — 730 timesteps, 36x18 = 648 gridpoints, one
# variable. ``cdo eof,3`` was run and both outputs read back with ``sinfon``.
# ---------------------------------------------------------------------------

#: outfile1 of every eof operator. The measurement that matters: with
#: ``neof=3``, outfile1 came back with **648** timesteps on a 1x1 grid — the
#: full spectrum, one eigenvalue per gridpoint of the input, not three. ``neof``
#: sizes outfile2 and leaves this file alone, which is the opposite of what
#: "number of EOFs" suggests and the reason it is spelled out here.
_EOF_EIGENVALUES = OperatorOutput(
    # Kept short enough to read as a form label; the surprise itself is in
    # ``field``, which is the placeholder and the tooltip.
    "Eigenvalues — the whole spectrum",
    "one eigenvalue per timestep on a 1x1 grid, in descending order. neof does "
    "NOT truncate this file — measured on a 36x18 input, eof,3 wrote 648 "
    "timesteps here, one per gridpoint. It is a spectrum, not a map: the map "
    "canvas draws a single degenerate cell from it, so open it in the plot or "
    "statistics panel. Divide by the sum for the fraction of variance each "
    "mode explains.",
    drawable=False,
    suffix="_eigenvalues",
)

#: outfile2. Measured: 3 timesteps on the input's own 36x18 grid for ``eof,3``.
_EOF_EIGENVECTORS = OperatorOutput(
    "EOFs — the first neof eigenvectors",
    "the leading neof patterns, one timestep per mode, on the input's own "
    "grid. This is the file eofcoeff takes as its *first* input.",
    drawable=True,
    suffix="_eofs",
)

_EOF_OUTPUTS = (_EOF_EIGENVALUES, _EOF_EIGENVECTORS)

_OPERATOR_OUTPUTS: Dict[str, Tuple[OperatorOutput, ...]] = {
    **{name: _EOF_OUTPUTS for name in (
        "eof", "eoftime", "eofspatial", "eof3d", "eof3dtime", "eof3dspatial")},

    # trend, from ``cdo --help trend`` on 2.6.3: "the estimation for a is
    # stored in outfile1 and that for b is stored in outfile2", where the model
    # is N(a+b*t, S^2). Declared here as well as the eof family because it is
    # the operator that proves the multi-output path is generic rather than an
    # eof special case — it gains its two labelled output rows from the same
    # change and needed no code of its own.
    "trend": (
        OperatorOutput(
            "a — the intercept",
            "the constant term of the fitted line a + b*t, one field on the "
            "input's grid. Pairs with outfile2; subtrend and addtrend take both "
            "in this order.",
            drawable=True, suffix="_intercept"),
        OperatorOutput(
            "b — the slope",
            "the trend itself: change per timestep, one field on the input's "
            "grid. This is the file to draw when the question is 'where is it "
            "warming'.",
            drawable=True, suffix="_slope"),
    ),

    # The six Magics operators. One output slot each — they are ``(n|1)`` — but
    # the slot does not hold what every other ``nout == 1`` slot in this catalog
    # holds, and both halves of that are stated here rather than left to be
    # discovered from a ``.ps`` in the NetCDF reader.
    #
    # ``media="image"`` is the first half. The second is that the path is an
    # **obase**, which is not expressible on an OperatorOutput at all — it is a
    # property of the argument rather than of the file — and is carried by
    # :data:`OBASE_OPERATORS` below.
    #
    # ``drawable=False`` as well, and not redundantly: a surface that has not
    # been taught about ``media`` yet still asks ``drawable``, and the honest
    # answer to "can the map canvas render this" is no. Declaring only
    # ``media`` would leave every existing reader of ``drawable`` believing a
    # PostScript file could go on the canvas.
    **{
        name: (
            OperatorOutput(
                "Plot base name (obase, not a file)",
                "the base name for the plot files. CDO appends to it rather "
                "than writing the path as given: <obase>_<variable>.<device> "
                "for contour/shaded/grfill, <obase>.<device> for "
                "vector/stream/graph. Nothing is ever created at the literal "
                "path you type, so a name like 'plot' is what to give here, "
                "not 'plot.ps'. The result is a picture, not a dataset — it "
                "cannot be opened as NetCDF or added as a map layer.",
                drawable=False,
                media="image",
            ),
        )
        for name in ("contour", "shaded", "grfill", "vector", "stream", "graph")
    },
}


# ---------------------------------------------------------------------------
# obase: the output argument that is not an output file
#
# ``nout`` counts output *arguments*, not files, and for most of the catalog
# those are the same thing. Three groups in this catalog break the identity,
# and until the third was declared the execution layer knew about two:
#
#   1. ``nout == -1`` — every ``split*``, ``distgrid``, ``intyear`` and the two
#      Ensval operators. Static, and derivable from the signature.
#   2. ``map3d=true`` on a ``gen*`` operator. ``nout == 1``, and decidable only
#      from a parameter value; see :func:`writes_output_prefix`.
#   3. The six Magics operators. ``nout == 1``, and decidable from neither —
#      it is simply what the operator does. The manual is explicit and both
#      module pages say it in their synopsis: ``cdo <operator>,parameter infile
#      obase``, and then "The output file will be named <obase>_<param>.<device>"
#      (Magplot) or "<obase>.<device>" (Magvector, Maggraph).
#
# The consequence of group 3 being undeclared was silent loss rather than a
# failed run, and it is worth spelling out because nothing in a successful run
# would have shown it. ``variable_output`` was False, so
# ``_prepare_output_target`` recorded ``kind="file"``; a user output path
# containing a space is aliased into a temp directory, CDO writes
# ``<alias>_tas.ps`` beside the alias, and ``_materialise_output_aliases`` then
# moves only the exact alias path — which does not exist. The plots stay in the
# temp directory and the application reports the run finished. On a build with
# MAGICS that is a successful run that produces nothing where the user looked.
#
# Declaring it here fixes four call sites at once, because each already handles
# a prefix correctly once told it has one: the pre-run snapshot, the alias
# relocation, the failed-run cleanup, and ``_discovered_prefix_outputs``, which
# is what makes the app able to *name* the files CDO chose. It also suppresses
# ``result.output_file``, which is the fix for handing a ``.ps`` to the NetCDF
# reader — see the ``not call.variable_output`` guard in ``execute_operator``.
# ---------------------------------------------------------------------------

#: Operators whose trailing argument is a base name CDO appends to, despite
#: ``nout == 1`` saying it is one file.
#:
#: Only the Magics six. Every other operator that treats its output as a base
#: is ``nout == -1`` and needs no entry, which is why this is a small set and
#: not a second copy of the split family.
OBASE_OPERATORS: frozenset = frozenset({
    "contour", "shaded", "grfill", "vector", "stream", "graph",
})

#: Of those, the three whose obase also gains a per-variable infix. Magplot
#: writes ``<obase>_<param>.<device>``; Magvector and Maggraph write
#: ``<obase>.<device>``. Kept apart because it is the difference between "you
#: will get one file" and "you will get one file per variable", which is the
#: thing to tell a user before the run rather than after it.
_MAGPLOT_OPERATORS: frozenset = frozenset({"contour", "shaded", "grfill"})


def writes_images(name: str) -> bool:
    """Whether ``name``'s outputs are pictures rather than datasets.

    The question every surface should ask before opening a result, offering it
    as a map layer, or wiring it into the next node of a model. Answered from
    the declared :attr:`OperatorOutput.media` rather than from a list of
    operator names, so an operator gains the answer by being described.
    """
    spec = OPERATOR_SCHEMA.get(name)
    if spec is None:
        return False
    return any(output.media == "image" for output in spec.outputs)


def expected_plot_files(name: str, obase: str,
                        supplied: "Sequence[str]" = ()) -> str:
    """The filename pattern a Magics run will actually produce, in words.

    Exists because the one thing a user gets wrong here is invisible until they
    go looking in the folder: they type ``plot.ps``, CDO writes ``plot.ps.ps``
    or ``plot.ps_tas.ps``, and nothing in the run says so. Every surface that
    asks for the output path can put this under the field.

    The device is read from the call's own parameters rather than assumed, so
    the sentence names the extension the user has actually chosen; ``ps`` is
    CDO's default and is what is reported when the parameter is left blank.

    Returns "" for an operator that does not write plots, so a caller can use
    it unconditionally.
    """
    if name not in OBASE_OPERATORS:
        return ""

    device = "ps"
    spec = OPERATOR_SCHEMA.get(name)
    if spec is not None:
        for index, param in enumerate(spec.params):
            if param.name != "device":
                continue
            if index < len(supplied) and str(supplied[index]).strip():
                device = str(supplied[index]).strip()
            break

    base = obase.strip() or "obase"
    if name in _MAGPLOT_OPERATORS:
        return (f"Writes one plot per variable, named "
                f"{base}_<variable>.{device} — not {base} itself.")

    # Maggraph inserts a page number for every device except PostScript, and
    # the manual does not mention it: its page says the output is
    # "<obase>.<device>" flatly. Measured on a MAGICS-enabled CDO 2.6.3:
    #
    #     graph,device=ps  -> g_ps.ps          (as documented)
    #     graph,device=png -> g_png.1.png      (a ".1" the manual never names)
    #     graph,device=svg -> g_svg.1.svg
    #
    # PostScript is multi-page in one file, so it needs no counter; the raster
    # and vector devices get one file per page and are numbered from 1. Magplot
    # is *not* affected — ``shaded`` on a three-timestep input still wrote a
    # single ``m_png_RAINFALL.png`` — so this is Maggraph's own behaviour and
    # not a general device rule.
    #
    # Worth stating rather than glossing: a user who follows the manual looks
    # for ``plot.png``, finds nothing, and has no reason to suspect a page
    # number. The prefix machinery finds the file either way — it globs — so
    # this affects what the app *tells* the user, not whether the output is
    # collected.
    if name == "graph" and device != "ps":
        return (f"Writes {base}.1.{device} — note the page number, which the "
                f"CDO manual does not mention (it appears for every device "
                f"except ps). Not {base} itself.")
    return f"Writes {base}.{device} — not {base} itself."


# ---------------------------------------------------------------------------
# Environment variables that change the answer
#
# CDO reads a handful of environment variables that change what an operator
# computes rather than how it is spelled. The app had no way to set one at all —
# ``subprocess.run`` was called with no ``env=`` and nothing in
# ``OperatorRequest`` carried one — so the EOFs section was reachable only in
# its default configuration, and a user following the CDO manual's own advice
# about ``CDO_WEIGHT_MODE`` had nowhere to put it.
#
# Declared per operator rather than globally: these four change what *eof*
# computes, and offering them beside an operator they do not reach would be a
# worse kind of wrong than not offering them at all.
#
# Every default below is measured against the installed 2.6.3 rather than read
# off the documentation. The method for CDO_WEIGHT_MODE: run ``cdo eof,3`` on
# sample_climate_tg.nc three times — once with the variable unset, once =on,
# once =off — and ``cdo diffn`` the eigenvalue files pairwise. Unset and =off
# were identical (no differing fields); unset and =on differed in 648 of 648
# fields. So the default is ``off``, and it is off that the Eofcoeff module's
# non-weighted dot product requires.
# ---------------------------------------------------------------------------

#: Measured on sample_climate_tg.nc: with =on the leading eigenvalue was
#: 75056.094, with =off (the default) it was 48654900 — a factor of ~650, which
#: is the area-weight normalisation and not a rounding difference. The leading
#: eigenvector differed by ~1.0 almost everywhere, so this is a different answer
#: rather than a rescaled one. Also measured: the number of jacobi column pairs
#: failing to converge changed with it, 96653 of 209628 at =off against 141333
#: at =on, so the weighting also decides whether the solver copes.
_ENV_WEIGHT_MODE = OperatorEnv(
    name="CDO_WEIGHT_MODE",
    kind="select",
    label="Area weighting",
    default="off",
    choices=("off", "on"),
    help="Weight each gridpoint by its cell area before solving. Default off, "
         "measured by comparing an unset run against both settings — unset and "
         "off produced identical eigenvalue files, on differed in all 648 "
         "fields. On this sample =on changed the leading eigenvalue from "
         "48654900 to 75056.094 and the leading eigenvector almost everywhere. "
         "Leave it off when the EOFs will be fed to eofcoeff: that operator "
         "computes a non-weighted dot product, so a weighted eof run gives "
         "coefficients that do not match its own EOFs.",
    affects="both output files",
)

#: The two solvers. Measured, and the measurement is a warning rather than a
#: recommendation: on the anomaly file, jacobi returned zeros *and said so*
#: ("Setting Matrix and Eigenvalues to 0 before return", on stderr), while
#: danielson_lanczos returned zeros and said **nothing at all** — empty stdout,
#: empty stderr, exit 0. Switching solvers to escape the warning therefore
#: escapes the warning and not the failure.
_ENV_SVD_MODE = OperatorEnv(
    name="CDO_SVD_MODE",
    kind="select",
    label="Solver",
    default="jacobi",
    choices=("jacobi", "danielson_lanczos"),
    help="Which eigen-solver to use. Default jacobi. Measured on an anomaly "
         "series where jacobi failed: danielson_lanczos returned an all-zero "
         "spectrum too, but silently — no warning on either stream, exit 0. "
         "Changing this to escape a non-convergence warning hides the failure "
         "rather than fixing it; check the eigenvalues are not all zero.",
    affects="both output files",
)

#: Measured: 200 and 500 both left the anomaly case returning zeros, with the
#: same 209628-of-209628 non-convergence. Offered because the CDO manual
#: documents it and a different dataset may well be rescued by it, but the help
#: says what was actually seen rather than promising a fix.
#: The histogram resolution the eleven three-input percentile operators share,
#: and the reason those operators want a minimum and a maximum at all: they do
#: not sort their samples, they bin them into ``CDO_PCTL_NBINS`` bins spanning
#: the range those two files give, then read the percentile off the histogram.
#: The result is therefore approximate at one part in the bin count.
#:
#: Declared on those eleven and on nothing else. The one-input percentile
#: operators — fldpctl, zonpctl, merpctl, varspctl, runpctl — take no minimum
#: or maximum because they have the whole sample in hand, and the variable was
#: measured inert for every one of them.
#:
#: Default 101, measured rather than transcribed: an unset variable and
#: ``CDO_PCTL_NBINS=101`` give bit-identical output where 11 and 1001 do not.
#: On a 400-step daily series (``timpctl,90``):
#:
#:     unset 3139.828369   101 3139.828369   11 3140.037842   1001 3139.333252
#:
#: Six of the eleven moved on that sample — timpctl, monpctl, yearpctl,
#: seaspctl, ymonpctl, yseaspctl. The other five did not, and the help says why
#: rather than claiming the variable does nothing for them: daypctl, hourpctl,
#: ydaypctl, timselpctl and ydrunpctl each had only a handful of values in one
#: output period on that series, and a handful of values land in distinct bins
#: at any resolution.
_ENV_PCTL_NBINS = OperatorEnv(
    name="CDO_PCTL_NBINS",
    kind="int",
    label="Percentile histogram bins",
    default="101",
    help="Number of bins the percentile histogram uses between this "
         "operator's minimum and maximum inputs. The percentile is read off "
         "that histogram rather than from a sort, so it is approximate at "
         "roughly one part in this number — raise it for a finer answer on a "
         "long series. Measured on timpctl,90 over 400 daily steps: 11 bins "
         "gave 3140.04, the default 101 gave 3139.83, 1001 gave 3139.33. It "
         "changes nothing when an output period holds only a few timesteps.",
    affects="the percentile value in the output file",
)

_ENV_MAX_JACOBI_ITER = OperatorEnv(
    name="MAX_JACOBI_ITER",
    kind="int",
    label="Max jacobi iterations",
    default="12",
    help="Iteration ceiling for the jacobi solver. Raising it did not rescue "
         "the non-converging sample this was measured against — 200 and 500 "
         "both still returned an all-zero spectrum — but it costs nothing to "
         "try on a dataset that is close to converging.",
    affects="both output files",
)

#: Same measurement, same honesty: 1e-6 did not rescue it either.
_ENV_FNORM_PRECISION = OperatorEnv(
    name="FNORM_PRECISION",
    kind="float",
    label="Orthogonality tolerance",
    default="1e-12",
    help="The orthogonality the jacobi solver must reach; it is this number "
         "the 'did not achieve requested orthogonality of 1e-12' warning names. "
         "Loosening it to 1e-6 did not rescue the non-converging sample.",
    affects="both output files",
)

#: ``gridarea`` only, and the answer to "should the execution layer pass
#: PLANET_RADIUS through the environment": yes, and through the mechanism that
#: already exists rather than a new one. ``run_environment`` and the ``env=``
#: argument on ``execute_operator`` were built for the EOF and Interpolation
#: variables; declaring this one here is the whole change, and both surfaces
#: render it with the widget they already have.
#:
#: Measured on 2.6.3 against an r36x18 field, which is also where the default
#: below comes from — CDO announces it on stdout:
#:
#:   (unset)                  "Using default planet radius: 6371000m",
#:                            field sum 5.10064e+14 m2
#:   PLANET_RADIUS=1234567    "Using planet radius from env.var.
#:                            PLANET_RADIUS: 1234567m", sum 1.91531e+13
#:   gridarea,radius=1234567  "Using user defined planet radius" — the
#:                            parameter and the variable are two spellings of
#:                            one setting, and the parameter wins
#:
#: Deliberately *not* given to ``gridweights``, its module sibling, even though
#: the manual documents the two together. ``gridweights,radius=…`` is "Too many
#: arguments! Need 0 found 1." and the variable changes nothing it returns:
#: the weights are normalised to sum to 1, so the radius cancels out of every
#: one of them. Confirmed by comparing the field with the variable unset and
#: set to 1234567 — identical to six significant figures. Offering the control
#: there would be offering a knob that does nothing.
_ENV_PLANET_RADIUS = OperatorEnv(
    name="PLANET_RADIUS",
    kind="float",
    label="Planet radius (m)",
    default="6371000",
    help="Radius used to turn the grid into areas, in metres. Areas scale "
         "with its square, so Mars (3389500) gives areas 0.28x Earth's. The "
         "radius= parameter sets the same thing and takes precedence.",
    affects="the magnitude of every cell area",
)

#: eofcoeff only, and it changes filenames rather than numbers. Measured:
#: CDO_FILE_SUFFIX=.grb with -f grb wrote pc00000.grb; CDO_FILE_SUFFIX=NULL
#: wrote pc00000 with no extension at all. The default is the suffix the output
#: format implies, which for NetCDF is ".nc".
_ENV_FILE_SUFFIX = OperatorEnv(
    name="CDO_FILE_SUFFIX",
    kind="string",
    label="Output file suffix",
    default="(from the output format)",
    help="The suffix appended to each numbered output file. Measured on 2.6.3: "
         "'.grb' wrote pc00000.grb, and the literal 'NULL' suppresses the "
         "suffix entirely — pc00000, no extension. Left unset, the suffix "
         "follows the output format, which is .nc for NetCDF.",
    affects="the names of the output files",
)

# ---------------------------------------------------------------------------
# The Interpolation section's environment variables
#
# For six of that section's modules an environment variable is the ONLY control
# surface CDO offers — there is no operator parameter for any of these, so
# without them whole behaviours of the section are unreachable from the app.
#
# The per-operator mapping below is not read off the manual and not inferred
# from operator names: it is what each operator's own help declares. Generated
# by walking every remap*/gen*/int*/ml2*/ap2* operator in ``cdo --operators``,
# running ``cdo -h <op>``, and reading its ENVIRONMENT section. That is why, for
# instance, remapbil gets REMAP_EXTRAPOLATE but not CDO_GRIDSEARCH_RADIUS while
# remapknn gets both, and why the ycon/con2 aliases get nothing: their help
# pages declare nothing.
#
# CDO_REMAP_NORM is the dangerous one, and it is the same failure shape as
# _FLDCOR_TRUNCATES — a plausible wrong answer that every surface reports as a
# success. Measured on 2.6.3 with a masked 18x9 source remapped to r36x18:
# fracarea and destarea both exit 0, both write a well-formed 4216-byte file,
# and the field means are 0.66293 and 0.537057. Nothing in the run says which
# normalisation produced the numbers, which is why it is also written into the
# logged command.
# ---------------------------------------------------------------------------

#: Measured: `CDO_REMAP_NORM=fracarea cdo remapcon,r36x18 mask_src.nc n_frac.nc`
#: against the same run unset -> `cdo diffn` reports **0 differing fields**, so
#: the default is fracarea. Against destarea -> **1 of 1 fields differ**, field
#: mean 0.66293 vs 0.537057. Both runs exit 0.
_ENV_REMAP_NORM = OperatorEnv(
    name="CDO_REMAP_NORM",
    kind="select",
    label="Conservative normalisation",
    default="fracarea",
    choices=("fracarea", "destarea"),
    help="How each target cell value is normalised. Default fracarea, "
         "confirmed by diffing an unset run against both settings — unset and "
         "fracarea were byte-identical, destarea differed in every field. "
         "fracarea divides by the non-masked source area actually intersected, "
         "giving a reasonable flux that is not locally conserved; destarea "
         "divides by the whole target cell area, conserving flux locally but "
         "producing unreasonable values where the source is partly masked. "
         "Measured field means on one masked sample: 0.66293 against 0.537057. "
         "Both exit 0 and both write a well-formed file, so nothing but this "
         "setting tells you which of the two answers you have.",
    affects="the values in the output file",
)

#: Measured on remapcon over a masked source: unset and 0.0 gave the identical
#: field mean 0.66293, so the default is 0.0. 0.5 -> 0.662819, 0.9 -> 0.666156.
#: A real change in the numbers, and a silent one.
_ENV_REMAP_AREA_MIN = OperatorEnv(
    name="REMAP_AREA_MIN",
    kind="float",
    label="Minimum destination area fraction",
    default="0.0",
    help="Target cells covered by less than this fraction of unmasked source "
         "area are left missing instead of being filled. Default 0.0, measured "
         "by comparing an unset run against REMAP_AREA_MIN=0.0 — identical "
         "field means. Raising it changes the answer quietly: on one masked "
         "sample the field mean went 0.66293 (0.0) -> 0.662819 (0.5) -> "
         "0.666156 (0.9), every run exiting 0.",
    affects="which target cells are filled, and the values in the output file",
)

#: The one variable whose default is NOT a fixed value: CDO's own help says
#: "By default the extrapolation is enabled for cyclic grids" (remapbil/genbil)
#: and "for circular grids" (the knn family), so what unset means depends on the
#: source grid. Measured on a cyclic 18x9 lonlat source with remapbil: unset,
#: =on and =off all produced the identical field mean 0.676773 — i.e. no
#: observable difference on a source that needs no extrapolation. The help says
#: exactly that rather than claiming an effect that was not seen.
_ENV_REMAP_EXTRAPOLATE = OperatorEnv(
    name="REMAP_EXTRAPOLATE",
    kind="select",
    label="Extrapolation",
    default="on for cyclic grids",
    choices=("on", "off"),
    help="Fill target points that fall outside the source grid. The default is "
         "grid-dependent rather than fixed — CDO's own help says extrapolation "
         "is enabled by default for cyclic (circular) grids — so leaving this "
         "unset does not mean 'off'. Measured on a cyclic lonlat source, unset "
         "/ on / off all gave the same field mean 0.676773, which is expected "
         "there: nothing needed extrapolating. It matters when the target "
         "extends beyond the source.",
    affects="target points outside the source grid",
)

#: Measured on remapknn over a masked source: unset, 180 and 30 all gave field
#: mean 0.664231; 1 and 0.001 both gave 0.689171. So the default is 180, and
#: shrinking it changes the answer with no warning and exit 0.
_ENV_GRIDSEARCH_RADIUS = OperatorEnv(
    name="CDO_GRIDSEARCH_RADIUS",
    kind="float",
    label="Grid search radius (degrees)",
    default="180",
    help="How far, in degrees, the neighbour search may look. Default 180, "
         "measured by comparing an unset run against explicit values — unset, "
         "180 and 30 all produced the identical field mean 0.664231. Shrinking "
         "it silently changes the result: 1 and 0.001 both gave 0.689171, "
         "exit 0, no warning. Too small a radius leaves points with no "
         "neighbour found.",
    affects="which source points are found, and the values in the output file",
)

#: NOT measured, and the help says so. Exercising it needs a file on ECHAM
#: hybrid model levels plus a vertical coordinate table, neither of which could
#: be built here — every attempt aborts at "Open failed on: <vct>" before the
#: variable is ever consulted. The default below is transcribed from
#: ``cdo -h remapeta``: "REMAPETA_PTOP Sets the minimum pressure level for
#: condensation. Above this level the humidity is set to the constant 1.E-6.
#: The default value is 0 Pa."
_ENV_REMAPETA_PTOP = OperatorEnv(
    name="REMAPETA_PTOP",
    kind="float",
    label="Minimum pressure for condensation (Pa)",
    default="0",
    help="Minimum pressure level for condensation, in Pa; above it humidity is "
         "set to the constant 1.E-6. Default 0 Pa. Unlike the other variables "
         "offered here this one's effect was NOT measured against the "
         "installed binary — doing so needs data on ECHAM hybrid levels and a "
         "vertical coordinate table, which were not available — so both the "
         "default and the description come from CDO's own help.",
    affects="humidity above the given pressure level",
)

_OPERATOR_ENV: Dict[str, Tuple[OperatorEnv, ...]] = {
    # The Miscellaneous section's one environment variable. See
    # ``_ENV_PLANET_RADIUS`` for why ``gridweights`` is not here beside it.
    "gridarea": (_ENV_PLANET_RADIUS,),

    **{name: (_ENV_WEIGHT_MODE, _ENV_SVD_MODE, _ENV_MAX_JACOBI_ITER,
              _ENV_FNORM_PRECISION)
       for name in ("eof", "eoftime", "eofspatial", "eof3d", "eof3dtime",
                    "eof3dspatial")},
    # CDO_WEIGHT_MODE appears here too, and that is the point rather than a
    # duplication: the Eofcoeff manual page states that eofcoeff computes a
    # non-weighted dot product, so the setting has to agree across the two runs
    # or the coefficients are inconsistent with the EOFs they project onto.
    # Whether the app can *check* that agreement is a separate question — see
    # the note in gui/model_builder.py, where both nodes exist in one graph.
    **{name: (_ENV_WEIGHT_MODE, _ENV_FILE_SUFFIX)
       for name in ("eofcoeff", "eofcoeff3d")},

    # The Interpolation section. Each group is exactly what that operator's own
    # ENVIRONMENT section declares — see the block comment above for how the
    # mapping was generated. The asymmetries are real and deliberate: the
    # bilinear/bicubic pair honours extrapolation but not the search radius,
    # laf honours the area minimum but not the normalisation, and remap — which
    # applies pre-computed weights from any method — honours all four.
    **{name: (_ENV_REMAP_EXTRAPOLATE, _ENV_GRIDSEARCH_RADIUS)
       for name in ("remapknn", "remapnn", "remapdis",
                    "genknn", "gennn", "gendis")},
    **{name: (_ENV_REMAP_EXTRAPOLATE,)
       for name in ("remapbil", "remapbic", "genbil", "genbic")},
    **{name: (_ENV_REMAP_NORM, _ENV_REMAP_AREA_MIN)
       for name in ("remapcon", "gencon")},
    **{name: (_ENV_REMAP_AREA_MIN,)
       for name in ("remaplaf", "genlaf")},
    **{name: (_ENV_REMAPETA_PTOP,)
       for name in ("remapeta", "remapeta_s", "remapeta_z")},
    "remap": (_ENV_REMAP_NORM, _ENV_REMAP_AREA_MIN, _ENV_REMAP_EXTRAPOLATE,
              _ENV_GRIDSEARCH_RADIUS),
    # Same variable the Eofcoeff module uses, for the same reason: intyear
    # writes one numbered file per year and this names their suffix.
    "intyear": (_ENV_FILE_SUFFIX,),

    # The eleven three-input percentile operators of the Statistic section.
    # Exactly the keys of ``_PCTL_COMPANIONS``, and deliberately derived from
    # that dict rather than retyped: an operator that gains a min/max pair
    # gains the histogram this variable sizes, and the two lists disagreeing
    # would mean a declared bin count on an operator that does not bin.
    **{name: (_ENV_PCTL_NBINS,) for name in _PCTL_COMPANIONS},
}


# ---------------------------------------------------------------------------
# The Statistic section's global options
#
# None of these is a comma-parameter, so none belongs in ``_PARAM_SPECS``; they
# are argv tokens that go between ``cdo`` and the operator name, which is what
# ``execute_operator(..., options=[...])`` already carries. What is added here
# is the *declaration* — which options matter for which operator — so a surface
# can name them instead of showing one free-text box and hoping.
#
# All four were exercised on 2.6.3 with a clean exit status (a pipe to `head`
# will hide a CDO abort, so these were run through subprocess and the
# returncode read directly).

#: The one that changes an answer rather than a performance characteristic, and
#: the reason this table exists at all. It sets which timestamp a temporal
#: statistic stamps on each output period, for roughly 200 operators.
#:
#: The default is ``middle`` and that surprises people who expect ``last``.
#: Measured, ``monmean`` over a 400-step daily series starting 2000-01-01:
#:
#:     (unset)  2000-01-16  2000-02-15  2000-03-16   <- identical to middle
#:     first    2000-01-01  2000-02-01  2000-03-01
#:     middle   2000-01-16  2000-02-15  2000-03-16
#:     last     2000-01-31  2000-02-29  2000-03-31
#:
#: CDO validates the value: ``--timestat_date bogus`` is "option
#: --timestat_date: unsupported argument: bogus", which is what makes
#: ``choices`` an assertion rather than a transcription.
_OPT_TIMESTAT_DATE = OperatorOption(
    name="--timestat_date",
    argument="srcdate",
    choices=("first", "middle", "last"),
    default="middle",
    help="Which timestamp each output period carries. The default is the "
         "middle of the period, not its end — a monthly mean of January comes "
         "out dated the 16th unless you say otherwise. Use last when the "
         "result will be joined against end-of-period data, first when it "
         "will be joined against period labels.",
)

#: Documented for Timstat, Daystat, Monstat, Yearstat and Hourstat. Accepted on
#: 2.6.3 (exit 0) but printed nothing on the samples used here, including one
#: built with missing values, so the help says what CDO's own ``--help`` says
#: and does not claim an observed output.
_OPT_DIAGNOSTIC = OperatorOption(
    name="-S",
    default="off",
    help="Also produce a diagnostic stream carrying the number of non-missing "
         "values that went into each output period — CDO's --help calls it "
         "'a diagnostic output stream for the module TIMSTAT'. Long spelling "
         "--diagnostic. Accepted on this build; it produced no extra output "
         "on the samples this was measured against.",
)

#: A performance switch, and a bare flag. The ``true``/``false`` spelling
#: belongs to the environment variable, not to the option — measured:
#: ``cdo --async_read true monmean in out`` is "Operator >true< not found!"
#: while ``cdo --async_read monmean in out`` and ``cdo -p monmean in out``
#: both exit 0.
_OPT_ASYNC_READ = OperatorOption(
    name="-p",
    default="false",
    help="Read the input asynchronously, which can help on a slow filesystem. "
         "A bare flag with no argument — long spelling --async_read. To set "
         "it by environment instead, CDO_ASYNC_READ takes true/false; the "
         "option itself does not.",
)

#: Ensstat's documented global option. Not a Statistic-wide default: it is
#: named on that module's page because an ensemble run is the one most likely
#: to be repeated over an existing output.
_OPT_OVERWRITE = OperatorOption(
    name="-O",
    default="off",
    help="Overwrite an existing output file instead of refusing. Long "
         "spelling --overwrite. This is the Ensstat module's documented "
         "global option; without it CDO aborts with 'Outputfile … already "
         "exists!'.",
)


def _timestat_operators() -> "Tuple[str, ...]":
    """Every operator whose module stamps a timestamp on an aggregated period.

    Derived from the module titles rather than from a name list, for the reason
    ``_MODULE_CATEGORY`` exists: this is ~200 operators and a prefix rule over
    their names cannot separate ``ymonmean`` from ``ymonsub``.
    """
    from .cdo_operator_catalog import CDO_OPERATOR_MODULES

    titles = {
        "Statistical values over all timesteps", "Temporal percentile",
        "Daily statistics", "Daily percentile",
        "Monthly statistics", "Monthly percentile",
        "Yearly statistics", "Yearly percentile",
        "Seasonal statistics", "Seasonal percentile",
        "Hourly statistics", "Hourly percentile",
        "Multi-year daily statistics", "Multi-year daily percentile",
        "Multi-year monthly statistics", "Multi-year monthly percentile",
        "Multi-year seasonal statistics", "Multi-year seasonal percentile",
        "Multi-year hourly statistics",
        "Multi-day hourly statistics", "Multi-day by the minute statistics",
        "Multi-year daily running statistics",
        "Multi-year daily running percentile",
        "Running statistics", "Running percentile",
        "Time range statistics", "Time range percentile",
        "Weighted yearly mean from monthly data",
        "Weighted temporal mean from yearly data",
    }
    return tuple(sorted(op for op, module in CDO_OPERATOR_MODULES.items()
                        if module in titles))


#: The five modules whose manual pages name ``-S`` and ``-p``.
_DIAGNOSTIC_TITLES = frozenset({
    "Statistical values over all timesteps", "Daily statistics",
    "Monthly statistics", "Yearly statistics", "Hourly statistics",
})


def _diagnostic_operators() -> "Tuple[str, ...]":
    from .cdo_operator_catalog import CDO_OPERATOR_MODULES

    return tuple(sorted(op for op, module in CDO_OPERATOR_MODULES.items()
                        if module in _DIAGNOSTIC_TITLES))


def _build_operator_options() -> Dict[str, Tuple[OperatorOption, ...]]:
    """Which global options are worth naming for each operator."""
    options: Dict[str, List[OperatorOption]] = {}
    for op in _timestat_operators():
        options.setdefault(op, []).append(_OPT_TIMESTAT_DATE)
    for op in _diagnostic_operators():
        options.setdefault(op, []).extend((_OPT_DIAGNOSTIC, _OPT_ASYNC_READ))
    from .cdo_operator_catalog import CDO_OPERATOR_MODULES
    for op, module in CDO_OPERATOR_MODULES.items():
        if module in ("Ensemble statistics", "Ensemble validation tools",
                      "Statistical values over an ensemble"):
            options.setdefault(op, []).append(_OPT_OVERWRITE)
    return {op: tuple(opts) for op, opts in options.items()}


_OPERATOR_OPTIONS: Dict[str, Tuple[OperatorOption, ...]] = \
    _build_operator_options()


def operator_options(name: str) -> Tuple[OperatorOption, ...]:
    """The global options worth naming for ``name``, in display order.

    Empty for most operators, which is the honest answer: CDO's global option
    set is large and applies broadly, and these are the ones a *specific*
    operator's own manual page calls out or whose default measurably surprises
    people on that operator. ``-f``, ``-b``, ``-z`` and ``-r`` are deliberately
    absent — they apply to everything, and the surfaces already offer a free
    field for them.
    """
    return _OPERATOR_OPTIONS.get(name, ())


def operator_options_hint(name: str) -> str:
    """A one-line placeholder naming ``name``'s most useful global option."""
    options = operator_options(name)
    if not options:
        return "e.g. -f nc  (optional)"
    first = options[0]
    if first.choices:
        return f"e.g. {first.name} {first.choices[0]}  (optional)"
    return f"e.g. {first.name}  (optional)"


def operator_env(name: str) -> Tuple[OperatorEnv, ...]:
    """The environment variables declared for ``name``, in display order.

    Empty for all but the eight operators of the EOFs section and the twenty of
    the Interpolation section, which is the honest answer: CDO reads other
    variables, but these are the ones whose effect has been measured against the
    installed binary — with one declared exception, ``REMAPETA_PTOP``, whose
    help says outright that it was transcribed rather than measured because the
    data needed to exercise it could not be built.
    """
    spec = OPERATOR_SCHEMA.get(name)
    return spec.env if spec is not None else ()


def operator_outputs(name: str) -> Tuple[OperatorOutput, ...]:
    """The declared output slots of ``name``, or generic ones when it has none.

    Always as many entries as the operator writes, so a caller can index them by
    slot without asking whether the operator was declared — the same contract
    :func:`operator_inputs` offers, and for the same reason: the GUI builds a
    row per slot and must not have to branch on whether metadata exists.

    ``nout == -1`` gets one slot, since a split operator writes a family of
    files under one prefix and there is no fixed number to describe. ``nout ==
    0`` gets none: an info operator's result is its stdout.
    """
    spec = OPERATOR_SCHEMA.get(name)
    if spec is None:
        return ()
    if spec.nout == 0:
        return ()
    declared = list(spec.outputs)
    count = 1 if spec.nout == -1 else max(spec.nout, 0)
    while len(declared) < count:
        index = len(declared) + 1
        # "Output File 1" rather than "Output 1": this is the caption the
        # operator form has always used for the single-output case, and the
        # fallback should not quietly rename the row for 900-odd operators.
        declared.append(OperatorOutput(
            f"Output File {index}" if count > 1 else "Output File", ""))
    return tuple(declared[:count])


def operator_inputs(name: str) -> Tuple[OperatorInput, ...]:
    """The declared input slots of ``name``, or generic ones when it has none.

    Always as many entries as the operator has inputs, so a caller can index
    them by slot without worrying whether the operator was declared. A
    variable-arity operator (``nin == -1``) gets one generic slot, since there
    is no fixed number to describe.
    """
    spec = OPERATOR_SCHEMA.get(name)
    if spec is None:
        return ()
    declared = list(spec.inputs)
    count = 1 if spec.nin == -1 else max(spec.nin, 0)
    while len(declared) < count:
        index = len(declared) + 1
        declared.append(OperatorInput(f"Input {index}", ""))
    return tuple(declared[:count]) if spec.nin != -1 else tuple(declared[:1])


def parameter_file_kind(param: OperatorParam) -> str:
    """The :mod:`~.filetypes` key naming what format ``param``'s file is in.

    The one place the fallback from ``kind`` to ``file_kind`` is spelled, so no
    surface has to know it. A ``grid`` parameter that declares nothing gets the
    grid chooser — a description file, a SCRIP grid or a data file, per manual
    §1.5.2 — because that much follows from the kind. A ``file`` parameter that
    declares nothing gets :data:`~.filetypes.ANY`, because nothing follows from
    "this is a path" except that it is one.

    Returns ``""`` for a parameter that is not a file at all, so a caller can
    use the empty string to mean "no chooser belongs on this row".
    """
    if param.file_kind:
        return param.file_kind
    if param.kind == _GRID:
        return _ft.GRID
    if param.kind == _FILE:
        return _ft.ANY
    return ""


def input_file_kind(operator: str, slot: int = 0) -> str:
    """The :mod:`~.filetypes` key for one *data input* slot of ``operator``.

    ``data`` for all but the Import section, whose operators read a descriptor
    or an HDF5 product rather than a dataset. Out-of-range slots answer
    ``data`` rather than raising: a form drawing an optional third input for a
    variable-arity operator has no declared slot to consult and the answer is
    the same for every one of them.
    """
    slots = operator_inputs(operator)
    if 0 <= slot < len(slots):
        return slots[slot].file_kind or _ft.DATA
    return _ft.DATA


def format_recipe(recipe: str, *, in1: str = "infile", n: str = "5") -> str:
    """One input slot's recipe as a runnable ``cdo`` command line.

    ``{in1}`` is the operator's own first input and ``{n}`` the window width
    its first parameter carries, so the string a user is shown is the command
    that would actually build the file they are missing.
    """
    if not recipe:
        return ""
    return "cdo " + recipe.format(in1=in1, n=n or "5")


# ---------------------------------------------------------------------------
# What CDO says an operator is
#
# CDO groups its operators into modules, and the module is the only authority on
# which operators belong together. No rule over names can be: ``ymonsub`` is
# arithmetic and ``ymonmean`` is a statistic, they differ by three letters, and
# the prefix cascade below read both as "ymon…" and filed them together.
#
# That cost 32 of the Arithmetic section's operators. The statistics prefixes
# claimed all twenty of the day/mon/year/yday/ymon/yseas arithmetic operators,
# ``op.endswith("c")`` claimed ``minc``/``maxc`` for Comparison while also
# dragging ``harmonic`` and ``lic`` *into* Arithmetic, ``setmiss`` went to
# Modification on its ``set`` prefix, and the rest fell through to
# Miscellaneous. A user browsing for ``ymonsub`` under Arithmetic did not find
# it.
#
# ``CDO_OPERATOR_MODULES`` is probed from the installed binary by
# ``generate_operator_modules.py``, so the membership cannot drift from the CDO
# that will actually run. This table is the policy laid over that data: which
# module belongs in which of *our* categories. It is deliberately partial —
# an operator whose module is not named here falls through to the cascade
# exactly as before, so this fixes the Arithmetic section without silently
# re-categorising the other 771 operators.
#
# Three judgement calls, all resolved the same way — CDO's grouping wins,
# because the app has no better claim than the tool it is a front end for:
#
#   * ``setmiss`` reads as modification and is documented in Arith. It sets
#     missing values *by arithmetic comparison against a constant*, alongside
#     add/sub/mul/div, and it is the second input's values it compares. Arith.
#   * ``minc``/``maxc`` read as comparison and are documented in Arithc. They
#     return a value, not a mask; ``eqc``/``nec``/``lec`` and the rest of the
#     comparison family return 0/1 and stay in Comparison where they are.
#   * ``anomaly`` is an alias of ``ymonsub`` and ``mod`` is undocumented, but
#     the binary files both under arithmetic modules. Included.
#
# The File operation section adds five operators the 2.6.3 manual does not
# document at all. Each was decided by asking the binary the same question the
# rest of this table asks — which module is it in — rather than by reading its
# name:
#
#   * ``szip`` — ``CDO_OPERATOR_MODULES`` puts it in "Copy datasets", and
#     ``cdo -h szip`` prints the Copy page. File operations, by the module.
#   * ``splitrec`` — module "Split a dataset". File operations, by the module.
#   * ``splitdatetime`` — module "Splits a file into dates". Same.
#   * ``splitvar`` — module "Split a dataset", and ``cdo --operators`` prints
#     ``splitvar --> splitname``, so it is an alias rather than a distinct
#     operator. It lands in File operations with the operator it aliases, which
#     is the only answer that does not split an alias from its target.
#   * ``ncopy`` — the exception, and left in Miscellaneous deliberately. It is
#     the one of the five the binary will not place: it appears in
#     ``cdo --operators`` as (1|1) with no description, ``cdo -h ncopy`` answers
#     "No help available for this operator!", and it is absent from
#     ``CDO_OPERATOR_MODULES`` entirely. Its name reads as "NetCDF copy" and
#     that reading is probably right, but probably-right from a name is exactly
#     what this table exists to stop — it is the same evidence that put
#     ``harmonic`` and ``lic`` in Miscellaneous, and they are there for the same
#     reason. If a later CDO documents it, the module lookup will move it with
#     no change here.
# ---------------------------------------------------------------------------

#: The thirteen modules of the CDO Arithmetic section, keyed by the module title
#: ``cdo --help <operator>`` prints. Titles rather than identifiers because the
#: identifier is not always reachable: ``--module_info Yseasarith`` reports
#: "Module not found" on 2.6.0 even though ``--help yseasadd`` documents those
#: four operators.
_MODULE_CATEGORY: Dict[str, "NCExplorerCategory"] = {
    "Arithmetic on two datasets":     NCExplorerCategory.ARITHMETIC,  # Arith
    "Arithmetic with a constant":     NCExplorerCategory.ARITHMETIC,  # Arithc
    "Mathematical functions":         NCExplorerCategory.ARITHMETIC,  # Math
    "Evaluate expressions":           NCExplorerCategory.ARITHMETIC,  # Expr
    "Daily arithmetic":               NCExplorerCategory.ARITHMETIC,  # Dayarith
    "Monthly arithmetic":             NCExplorerCategory.ARITHMETIC,  # Monarith
    "Yearly arithmetic":              NCExplorerCategory.ARITHMETIC,  # Yeararith
    "Multi-year hourly arithmetic":   NCExplorerCategory.ARITHMETIC,  # Yhourarith
    "Multi-year daily arithmetic":    NCExplorerCategory.ARITHMETIC,  # Ydayarith
    "Multi-year monthly arithmetic":  NCExplorerCategory.ARITHMETIC,  # Ymonarith
    "Multi-year seasonal arithmetic": NCExplorerCategory.ARITHMETIC,  # Yseasarith
    "Arithmetic with days":           NCExplorerCategory.ARITHMETIC,  # Arithdays
    "Arithmetic with latitude":       NCExplorerCategory.ARITHMETIC,  # Arithlat

    # The four modules of the CDO Comparison section. The last two are the
    # reason this half of the table exists: ``ymoneq`` and ``yseasgt`` were
    # filed under Statistical values, because the prefix cascade below tests
    # ("ymon","yseas") before it tests any comparison name and a prefix cannot
    # tell ``ymoneq`` from ``ymonmean``. Naming the module settles it in the one
    # place that cannot drift from the binary.
    "Comparison of two fields":            NCExplorerCategory.COMPARISON,  # Comp
    "Comparison of a field with a constant":
        NCExplorerCategory.COMPARISON,                                     # Compc
    "Multi-year monthly comparison":       NCExplorerCategory.COMPARISON,  # Ymoncomp
    "Multi-year seasonal comparison":      NCExplorerCategory.COMPARISON,  # Yseascomp

    # The two Conditional selection modules. Both are named here rather than
    # left to the ``op.startswith("if")`` branch further down, for the reason
    # this table exists at all: the module is the authority and a name is a
    # guess. The guess happened to be right for the five ``if*`` operators and
    # could not be right for ``reducegrid``, which CDO documents under
    # Conditional selection and which the cascade filed under Miscellaneous
    # because its name starts with "r".
    #
    # CDO gives it a module of its own — the title below is what
    # ``cdo -h reducegrid`` prints — so there is no single "Conditional
    # selection" module to key on; both titles map to the one category.
    "Conditional selection":               NCExplorerCategory.CONDITIONAL_SELECTION,
    "Reduce fields to user-defined mask":  NCExplorerCategory.CONDITIONAL_SELECTION,

    # The four modules of the CDO Correlation section — one module per
    # operator, which is CDO's own way of saying these are four different
    # questions rather than one family with four spellings.
    #
    # Named here for the reason the Arithmetic and Comparison halves of this
    # table were: the cascade below tests ("fld", …, "tim", …) for Statistical
    # values, and no prefix can tell ``fldcor`` from ``fldmean``. All four were
    # filed under Statistical values, next to ninety-six one-input reductions,
    # which is the wrong shelf twice over — they take two files, and what they
    # return is a relationship between two fields rather than a summary of one.
    "Correlation in grid space":           NCExplorerCategory.CORRELATION,  # Fldcor
    "Covariance in grid space":            NCExplorerCategory.CORRELATION,  # Fldcovar
    "Correlation over time":               NCExplorerCategory.CORRELATION,  # Timcor
    "Covariance over time":                NCExplorerCategory.CORRELATION,  # Timcovar

    # The two modules of the CDO EOFs section, and the fourth time this table
    # has been the fix rather than the cascade. No prefix branch below matches
    # an "eof" name at all, so all eight fell off the end into Miscellaneous —
    # a different mechanism from the Arithmetic and Comparison cases (which were
    # claimed by a *wrong* branch) with the same result and the same remedy.
    #
    # Both titles were read back off the installed 2.6.3: ``cdo --help eof``
    # prints "eof, eoftime, eofspatial, eof3d - Empirical Orthogonal Functions"
    # and ``cdo --help eofcoeff`` prints "eofcoeff - Principal coefficients of
    # EOFs". ``eof3dtime``, ``eof3dspatial`` and ``eofcoeff3d`` carry no
    # description of their own in ``cdo --operators`` but are in the modules
    # all the same, which is exactly the case module lookup handles and a
    # description-based rule could not.
    "Empirical Orthogonal Functions":      NCExplorerCategory.EOF,  # EOFs
    "Principal coefficients of EOFs":      NCExplorerCategory.EOF,  # Eofcoeff

    # The seventeen modules of the CDO File operation section. Named here for
    # the third time for the same reason: the section had no module in this
    # table at all, so every one of its operators was placed by the cascade
    # below, and the cascade's only file-operation branch tested a "split"
    # prefix and a six-name list. What that cost, all reproducible before this
    # entry existed:
    #
    #   setchunkspec setfilter          -> Modification, on op.startswith("set")
    #   mergegrid                       -> Statistical values, on the "mer"
    #                                      meridional-statistics prefix
    #   clone tee pack unpack duplicate
    #   bitrounding distgrid collgrid
    #   szip ncopy                      -> Miscellaneous, by falling off the end
    #
    # Twelve of the section's operators in three wrong categories. ``mergegrid``
    # is the one worth naming twice: ``mermean``/``merstd`` are meridional
    # statistics and ``mergegrid`` is a file operation, they share three
    # letters, and no rule over names can tell them apart — which is the whole
    # argument for this table.
    "Copy datasets":                       NCExplorerCategory.FILE_OPERATIONS,
    "Duplicate a data stream and write it to file":
        NCExplorerCategory.FILE_OPERATIONS,
    "Pack data":                           NCExplorerCategory.FILE_OPERATIONS,
    "Unpack data":                         NCExplorerCategory.FILE_OPERATIONS,
    "Specify chunking":                    NCExplorerCategory.FILE_OPERATIONS,
    "Specify filter":                      NCExplorerCategory.FILE_OPERATIONS,
    "Bit rounding":                        NCExplorerCategory.FILE_OPERATIONS,
    "Replace variables":                   NCExplorerCategory.FILE_OPERATIONS,
    "Duplicates a dataset":                NCExplorerCategory.FILE_OPERATIONS,
    "Merge grid":                          NCExplorerCategory.FILE_OPERATIONS,
    "Merge datasets":                      NCExplorerCategory.FILE_OPERATIONS,
    "Split a dataset":                     NCExplorerCategory.FILE_OPERATIONS,
    "Split timesteps of a dataset":        NCExplorerCategory.FILE_OPERATIONS,
    "Split selected timesteps":            NCExplorerCategory.FILE_OPERATIONS,
    "Splits a file into dates":            NCExplorerCategory.FILE_OPERATIONS,
    "Distribute horizontal grid":          NCExplorerCategory.FILE_OPERATIONS,
    "Collect horizontal grid":             NCExplorerCategory.FILE_OPERATIONS,

    # The six modules of the CDO Import/Export section, and the fifth time this
    # table has been the fix rather than the cascade. All six titles were read
    # back off the installed 2.6.3 with ``cdo --help <operator>``; the manual's
    # module headings (Importbinary, Importcmsaf, Input, Output, Outputgmt,
    # Outputtab) are *not* what the binary prints, which is why the titles below
    # are the binary's wording and not the manual's.
    #
    # Note "Formatted input" and "Formatted output" are two modules, not one:
    # ``cdo --help input`` and ``cdo --help output`` print different NAME lines
    # ("input, inputsrv, inputext" against "output, outputf, outputint,
    # outputsrv, outputext"). The old FORMATTED_IO category ran them together,
    # which is the one place the app's grouping was finer-grained than CDO's own
    # and still managed to be wrong about the section.
    #
    # What this placed that nothing placed before:
    #
    #   import_binary import_grads
    #   import_cmsaf                    -> were Miscellaneous, by falling off
    #                                      the end of the cascade
    #   outputtab outputkey
    #   gmtxyz gmtcells + 9 GMT siblings
    #   outputarr outputfld outputts
    #   outputxyz                       -> were Information, because the
    #                                      ``nout == 0`` branch fired before
    #                                      this lookup was reached at all
    #
    # That second group is the reason the order of the two tests in
    # ``_infer_category`` had to change as well as this table growing. See the
    # note there: a module title is evidence and an output count is a guess, and
    # the guess was being consulted first.
    "Import binary data sets":             NCExplorerCategory.IMPORT_EXPORT,
    "Import CM-SAF HDF5 files":            NCExplorerCategory.IMPORT_EXPORT,
    "Formatted input":                     NCExplorerCategory.IMPORT_EXPORT,
    "Formatted output":                    NCExplorerCategory.IMPORT_EXPORT,
    "Table output":                        NCExplorerCategory.IMPORT_EXPORT,
    "GMT output":                          NCExplorerCategory.IMPORT_EXPORT,

    # The three modules of the CDO "Graphic with Magics" section. Same
    # mechanism as the EOFs block above and the same remedy: no prefix branch
    # in ``_infer_category`` matches "contour", "shaded", "grfill", "vector",
    # "graph" or "stream", so all six fell off the end into Miscellaneous.
    #
    # The titles are the binary's own, as ``CDO_OPERATOR_MODULES`` records
    # them, and they are the *operator descriptions* CDO prints rather than the
    # module class names the manual prints — "Lon/Lat plot" is what
    # ``cdo --operators`` says for contour, where the manual's page is headed
    # Magplot. This table is keyed on what the binary says, so it uses those.
    #
    # Three titles cover all six operators, including the undocumented one.
    # ``stream`` is registered here without being named: the binary already
    # files it under "Lon/Lat vector plot" — and ``cdo -h stream`` prints the
    # Magvector page to prove that is registration rather than a near-miss,
    # since ``cdo -h`` does not fall back (a nonexistent operator gets "is
    # neither an operator nor an option"). Naming ``stream`` in an operator
    # list here would have been the mistake this table exists to avoid, and it
    # would also have been unnecessary.
    "Lon/Lat plot":                        NCExplorerCategory.GRAPHICS,
    "Lon/Lat vector plot":                 NCExplorerCategory.GRAPHICS,
    "Line graph plot":                     NCExplorerCategory.GRAPHICS,

    # Four modules of the CDO Miscellaneous section, and the sixth time this
    # table has been the fix rather than the cascade. All four were being
    # placed by a *prefix*, and in three of the four the prefix split a module
    # across two categories — which is precisely the failure this table exists
    # to prevent, showing up inside one section:
    #
    #   deltat          -> Selection, on the "del" prefix, while its module
    #                      sibling timederivative went to Statistical values on
    #                      "tim". One module, two wrong categories, neither of
    #                      them the section it is documented in.
    #   delta_pressure  -> Selection, on "del" again, while pressure and
    #                      pressure_half — same module, same manual page —
    #                      fell through to Miscellaneous.
    #   setvals         -> Modification, on "set", while setrtoc and setrtoc2
    #                      fell through to Miscellaneous. Same module again.
    #   sethalo         -> Modification, by being named outright in the
    #                      cascade's ``op in {"enlarge", "sethalo"}`` set.
    #
    # ``sethalo`` is the one worth spelling out, because removing it from that
    # set is part of this change. It was there deliberately — it *is* a
    # modification in the ordinary sense of the word, and Modification is not
    # an unreasonable shelf for it. But the same is true of ``setvals``, and
    # CDO documents both under Miscellaneous; once the module is named, keeping
    # a hand-written exception for one operator of it would put the two halves
    # of one manual page in two categories for a third time. ``enlarge`` stays
    # in the cascade: CDO files it under "Enlarge fields", a Modification
    # module this table does not name, so nothing about it changes here.
    #
    # Each title was read off the installed 2.6.3 with ``cdo -h <operator>``,
    # and each maps its whole module rather than the operators that prompted
    # it — that is the point, and it is what moves ``timederivative`` and
    # ``tpnhalo`` along with the four names above.
    #
    # "Wind transformation" is deliberately **absent**, and the reason is worth
    # recording because naming it is the obvious next line. Three different CDO
    # modules print that identical title on 2.6.3:
    #
    #   dv2uv, uv2dv, dv2uvl, uv2dvl, rotuvN   -> the Transformation section
    #   uv2vr_cfd, uv2dv_cfd                   -> Miscellaneous (NCL_wind)
    #   uvDestag, rotuvNorth, projuvLatLon     -> Miscellaneous (WindTrans)
    #
    # So the module title is not a key here — it does not identify a module —
    # and mapping it would drag ``uv2dv`` and ``dv2uv`` out of Transformation,
    # where CDO's manual documents them beside sp2gp and gp2sp. The two
    # ``_cfd`` operators are placed by the curated ``OPERATOR_CATEGORIES`` list
    # instead, which ``_infer_category`` consults first. That is a per-operator
    # list, which this file otherwise argues against; it is the right tool here
    # only because the evidence a module title normally carries is genuinely
    # ambiguous in this one case, and the note above the entry says so.
    "Difference between timesteps":        NCExplorerCategory.MISCELLANEOUS,
    "Pressure on model levels":            NCExplorerCategory.MISCELLANEOUS,
    "Replace data values":                 NCExplorerCategory.MISCELLANEOUS,
    "Set the bounds of a field":           NCExplorerCategory.MISCELLANEOUS,

    # Three more Miscellaneous modules, named for a second reason on top of
    # correctness: without them these operators can only stay in their own
    # section by being listed in the curated menu below, and the curated list
    # is a *shortlist* — ten slots, sorted, shown on the toolbar. Nine names
    # were being spent there to hold operators in place rather than to
    # recommend them, which is what made the old Miscellaneous shortlist four
    # histogram variants deep.
    #
    #   Histogram        histcount/histsum/histmean/histfreq match the "hist"
    #                    prefix in the Statistical values branch below, so all
    #                    four leave the section the moment they leave the list.
    #   Temporal sorting timsort matches "tim", the same way.
    #   Generate a field const/random/seq/stdatm/topo fall through to
    #                    Miscellaneous by luck rather than by rule, and `mask`
    #                    does not: it matches the "mask" prefix and was filed
    #                    under Modification. It is a *generator* — CDO
    #                    documents it in Vargen, and ``cdo -h mask`` prints
    #                    "const, random, topo, seq, stdatm - Generate a field"
    #                    — so it belongs with the fields it is made of and not
    #                    with maskregion/masklonlatbox/maskindexbox, which are
    #                    the "Mask regions" and "Mask a box" modules and stay
    #                    in Modification untouched.
    #
    # This also picks up coshill, sincos, temp, testfield and `for`, which were
    # already landing in Miscellaneous with nothing asserting they should.
    "Histogram":                           NCExplorerCategory.MISCELLANEOUS,
    "Temporal sorting":                    NCExplorerCategory.MISCELLANEOUS,
    "Generate a field":                    NCExplorerCategory.MISCELLANEOUS,

    # ``gradsdes`` and ``dumpmap``, and the module that draws the line the
    # ``nout == 0`` branch in ``_infer_category`` cannot draw for itself.
    #
    # Both are ``nout == 0``, so without this entry both are Information — and
    # the note above that branch argues at length for leaving ``gridcellindex``
    # and ``verifygrid`` exactly where it puts them. These two are the other
    # side of the same line. ``verifygrid`` prints a report about its input and
    # leaves nothing behind; ``gradsdes`` **writes a file** — a GrADS ``.ctl``
    # descriptor, and a ``.gmp`` map alongside it for GRIB1 — and is
    # ``nout == 0`` only because that file does not go through CDO's output
    # slot. That makes it a writer for another program, which is precisely what
    # the Import/Export change moved *out* of Information, and it is why the
    # test above cannot be trusted for it.
    #
    # Named here rather than restored to the curated menu list below, which was
    # the other way to hold it: the list is a ten-slot shortlist, and spending
    # a slot to assert a category is the habit that entry is being cut to break.
    "GrADS data descriptor file":          NCExplorerCategory.MISCELLANEOUS,

    # ``cmor``, and the eighth time this table has been the fix rather than the
    # cascade. It is a single-operator module, so this line moves exactly one
    # operator and settles nothing beyond it — which is the point: the fix for
    # "this operator is in the wrong category" is a module title, never a new
    # branch in ``_infer_category``.
    #
    # What it was: INFORMATION, on the ``nout == 0`` test alone, listed beside
    # ``sinfo`` and ``showname``. ``cmor`` writes NetCDF files — one per output
    # variable, into a DRS tree — and is ``nout == 0`` only because those
    # filenames are CMOR's to choose and never pass through CDO's output slot.
    # That is precisely the distinction the note above the ``nout == 0`` branch
    # draws for ``gradsdes``: an operator that answers a question about its
    # input belongs in Information, and one whose product is a file belongs with
    # the writers. ``cmor`` is as far from ``sinfo`` as an operator in this
    # catalog gets — it is the one that writes the most files.
    #
    # Why Import/Export of the available categories, which was the close call:
    #
    #   * IMPORT_EXPORT is where this file already put the ``nout == 0``
    #     *writers* — outputtab, gmtxyz, gmtcells and thirteen more — when it
    #     moved them out of Information for this exact reason. ``cmor``'s
    #     product is the same kind of thing theirs is: a file written to another
    #     consumer's specification, in that consumer's layout, which the user
    #     publishes rather than opens next in this app. A CMIP data request is a
    #     more elaborate destination than a GMT plot, not a different kind of
    #     one.
    #   * MISCELLANEOUS is rejected on the grounds the EOF note states: it is
    #     where operators go when the binary *will not place them* (``harmonic``,
    #     ``lic``, ``ncopy``, all three absent from ``CDO_OPERATOR_MODULES``).
    #     CDO places ``cmor`` unambiguously, with a module title of its own.
    #   * FILE_OPERATIONS is rejected because that is CDO's name for a different
    #     section, and ``cmor`` is not documented in it. Filing it there would
    #     be this app inventing a grouping the tool it fronts does not have,
    #     which is the habit ``_MODULE_CATEGORY`` exists to break.
    #
    # A category of its own — CDO's manual does give "Climate model output
    # rewriting" a section — was considered and not taken: a section of one
    # operator, which this build cannot even run, would put an empty-looking
    # shelf on the toolbar next to twelve populated ones. Comparison, Correlation
    # and EOFs each earned an enum member by holding a family; this does not.
    #
    # ``cmorlite`` is deliberately untouched and stays in Miscellaneous. CDO
    # gives it a separate module ("CMOR lite") which this table does not name,
    # so the two are split by CDO's own sectioning rather than by a judgement
    # made here — and moving it would be a change to a section this work is not
    # about.
    "Climate Model Output Rewriting to produce CMIP-compliant data":
        NCExplorerCategory.IMPORT_EXPORT,

    # Five modules of the CDO Statistic section, and the ninth time this table
    # has been the fix rather than the cascade. Between them they hold 46
    # operators, and every one was in **Miscellaneous** — by falling off the end
    # of the cascade, because its Statistical values branch tests a prefix list
    # ("fld", "zon", "mer", "tim", "day", "mon", "year", "seas", "hour", "run",
    # "yday", "ymon", "yseas", "ydrun") that none of these five names begins
    # with. Not a wrong branch claiming them, as in the Arithmetic and
    # Comparison cases: no branch at all.
    #
    # Counted from the installed catalog before the change:
    #
    #   vars*                        14   varsmin varsmax ... varspctl
    #   yhour*                       10   yhourmin ... yhourvar1
    #   dhour*                       10   dhourmin ... dhourvar1
    #   dminute*                     10   dminutemin ... dminutevar1
    #   consecsum, consects           2
    #
    # 46 operators, five module titles, and the reason the fix is five lines
    # rather than five prefixes added to the cascade: ``dminute*`` and
    # ``dhour*`` differ from the ``day*`` and ``hour*`` branches by a prefix
    # that already matches something else, and a sixth prefix in a fourteen-way
    # cascade is exactly how ``mergegrid`` ended up in Statistical values.
    "Statistical values over all variables": NCExplorerCategory.STATISTICAL_VALUES,
    "Multi-year hourly statistics":          NCExplorerCategory.STATISTICAL_VALUES,
    "Multi-day hourly statistics":           NCExplorerCategory.STATISTICAL_VALUES,
    "Multi-day by the minute statistics":    NCExplorerCategory.STATISTICAL_VALUES,
    "Consecute timestep periods":            NCExplorerCategory.STATISTICAL_VALUES,

    # A forty-seventh operator moves with them and is worth naming because no
    # module title above mentions it: ``globavg``. It is an alias for
    # ``fldavg`` — the catalog describes it as "--> fldavg" — and it sits in
    # "Statistical values over a field", a module this table never needed to
    # name because the cascade's "fld" prefix already caught the other
    # sixteen. It does not begin with "fld", so it fell through to
    # Miscellaneous on its own. Naming the module is what collects it, and is
    # why that title appears here despite changing nothing for its siblings.
    "Statistical values over a field":       NCExplorerCategory.STATISTICAL_VALUES,

    # ---------------------------------------------------------------------
    # Remapstat: the thirteen ``remap*`` statistics, deliberately NOT listed.
    #
    # This is the one placement in the section that is a judgement rather than
    # a transcription, so it gets said out loud rather than left to the fact
    # that no line mentions it.
    #
    # CDO documents remapmin, remapmax, remaprange, remapsum, remapmean,
    # remapavg, remapstd, remapstd1, remapvar, remapvar1, remapskew, remapkurt
    # and remapmedian under **Statistic**, in a module titled "Remaps source
    # points to target cells". Adding that title here is a one-line change and
    # would move all thirteen into Statistical values. It is not made, and the
    # reason is not inertia — this table exists precisely to overrule the
    # prefix cascade, and here the cascade's answer ("remap" -> Interpolation)
    # is kept on purpose:
    #
    #   * What these operators do to a file is **regrid** it. The parameter is
    #     a target grid, the output is on a different grid from the input, and
    #     that is the property that decides which menu a user browses when they
    #     have a file on the wrong grid. Every other operator with that
    #     property is in Interpolation.
    #   * ``remapcon`` — first-order conservative remapping — is an
    #     area-weighted mean of source points onto target cells, which is
    #     ``remapmean`` with a different weighting rule. CDO puts it in the
    #     Interpolation section and ``remapmean`` in the Statistic section.
    #     Following both would file two answers to one question in two menus;
    #     a user comparing "conservative or plain mean?" would have to know the
    #     manual's sectioning to find the second one.
    #   * Their one parameter is a ``_GRID``, which is the widget and the
    #     ``GRID_PRESETS`` list the Interpolation category is already wired
    #     for.
    #
    # What this costs, stated so it can be reversed on evidence: the "All
    # Statistical values" submenu does not reach them, so a user who thinks of
    # ``remapmean`` as a statistic finds it under Interpolation or through the
    # command palette and the model builder, both of which search every
    # category. That is the same trade the Comparison entries above make in the
    # other direction, and it is a menu placement rather than a capability.
}


def operator_module(name: str) -> str:
    """The CDO module ``name`` belongs to, or "" when the binary does not say.

    Empty for the ~94 operators CDO answers "No help available for this
    operator!" about — ``harmonic`` and ``lic`` among them, which is why neither
    can be placed by module and both fall through to Miscellaneous.
    """
    from .cdo_operator_catalog import CDO_OPERATOR_MODULES

    return CDO_OPERATOR_MODULES.get(name, "")


def _infer_category(op: str, nin: int, nout: int) -> "NCExplorerCategory":
    """Fallback category assignment for operators not in the curated menu list."""
    if op in CATEGORY_FOR_OPERATOR:
        return CATEGORY_FOR_OPERATOR[op]

    # CDO's own grouping, before any guess about the name *or the arity*. Only
    # the modules _MODULE_CATEGORY names are decided here; everything else falls
    # through.
    #
    # This used to sit below the ``nout == 0`` test, and that order was the
    # whole reason the Import/Export section was split across three categories.
    # An operator that writes no file was declared Information and the lookup
    # was never reached, so ``outputtab``, ``gmtxyz``, ``gmtcells`` and the
    # thirteen other exporters in their modules were filed next to ``info`` and
    # ``sinfo`` — on the strength of an output count, while CDO was sitting
    # there with a module title that said "GMT output".
    #
    # The general form of the rule, which is why the swap is right beyond the
    # operators that prompted it: a module title is something the binary
    # asserts, and ``nout == 0`` is an inference from a shape. Evidence before
    # inference. The ``nout == 0`` test keeps its job — it is a real signal for
    # the ~60 operators CDO gives no module at all, which is most of the
    # Information section — it just no longer overrules the binary.
    #
    # Measured before the swap: of every ``nout == 0`` operator in the catalog,
    # *none* had a module named in this table, so reordering these two tests on
    # its own changed nothing. Every operator that moves does so because of the
    # six Import/Export titles added above, and the test file pins which ones.
    by_module = _MODULE_CATEGORY.get(operator_module(op))
    if by_module is not None:
        return by_module

    # ``gridcellindex`` and ``verifygrid`` are decided here, and deliberately
    # left where this branch puts them rather than moved to Miscellaneous with
    # the rest of their section. Both were checked against the alternative:
    #
    # For moving them: CDO's manual documents both under Miscellaneous, and the
    # Import/Export change above established that a module title is evidence
    # while ``nout == 0`` is an inference — which is why that lookup now runs
    # first. Applied literally, that argument moves these two.
    #
    # For leaving them: what the Import/Export change actually moved out of
    # Information were *writers* — gmtxyz, outputtab, the GMT and table
    # exporters — operators whose product is a file in another program's format
    # and which were sitting next to ``info`` only because they happen to
    # write nothing through CDO's own output slot. These two are not that.
    # ``verifygrid`` prints a grid report ("Grid consists of 648 (36x18) cells
    # … 72 cells have duplicate vertices") and ``gridcellindex`` prints one
    # integer. Both answer a question *about the input* and neither produces
    # anything to open elsewhere, which is the same thing ``griddes``,
    # ``sinfo`` and ``ngridpoints`` do and is what a user opens the Information
    # category to find. Their modules — "Verify grid coordinates" and "Get grid
    # cell index" — are single-operator modules, so naming them would move
    # exactly these two and settle nothing beyond them.
    #
    # Left in Information, therefore, on what they are rather than on where the
    # manual prints them; recorded here because the decision is close and the
    # next reader deserves the argument rather than the outcome. They are
    # correspondingly absent from the curated Miscellaneous list below, since a
    # name there would silently override this.
    if nout == 0:
        return NCExplorerCategory.INFORMATION

    if op.startswith("eca_") or op.startswith("etccdi"):
        return NCExplorerCategory.ECA_INDICES
    # ``delete`` and nothing else. What stood here was
    # ``op.startswith("split") or op in {copy, cat, merge, mergetime, replace,
    # delete}``, and every name in it except ``delete`` is now returned by the
    # module lookup above — all fifteen ``split*`` operators included, which is
    # more than the prefix reached, since it never placed ``distgrid`` or
    # ``collgrid`` at all. Two lists of one thing disagree eventually; this one
    # already disagreed with itself about the section it named.
    #
    # ``delete`` stays because the module lookup does *not* cover it: CDO files
    # it under "Select fields", a Selection module, alongside ``delcode`` and
    # ``delname``. Naming that module would be right and would also move every
    # other operator in it, which is a Selection-section change and not this
    # one's to make. Left where it has always been rather than re-categorised as
    # a side effect; the ``del`` prefix two branches down would claim it the
    # moment this line goes.
    if op == "delete":
        return NCExplorerCategory.FILE_OPERATIONS
    if op.startswith("sel") or op.startswith("del"):
        return NCExplorerCategory.SELECTION
    if op.startswith(("remap", "gen", "intgrid", "intlevel", "intntime",
                      "inttime", "intyear", "ml2", "ap2")):
        return NCExplorerCategory.INTERPOLATION
    if op.startswith(("sp2", "gp2", "uv2", "dv2", "fc2", "fourier",
                      "grid2fourier")):
        return NCExplorerCategory.TRANSFORMATION
    # ``sethalo`` used to be named here alongside ``enlarge``. It is now
    # returned by the "Set the bounds of a field" module lookup above, together
    # with ``tpnhalo``, which the name-set never reached — see the note there
    # for why the module wins over a hand-written exception even when the
    # exception was defensible. ``enlarge`` stays: CDO files it under a
    # Modification module this table does not name.
    if op.startswith("set") or op.startswith("ch") or op.startswith("invert") \
            or op.startswith("mask") or op.startswith("shift") \
            or op == "enlarge":
        return NCExplorerCategory.MODIFICATION
    if op.startswith(("fld", "zon", "mer", "vert", "ens", "tim", "run",
                      "hour", "day", "mon", "year", "seas", "yday",
                      "ymon", "yseas", "ydrun", "gridbox", "hist")):
        return NCExplorerCategory.STATISTICAL_VALUES
    if op.startswith("if") or op in {"ifthen", "ifnotthen", "ifthenelse"}:
        return NCExplorerCategory.CONDITIONAL_SELECTION
    # No comparison branch here, for the same reason there is no arithmetic one:
    # every operator CDO files under a comparison module was already returned by
    # the module lookup above, and all twenty-four are named in
    # ``COMPARISON_OPERATORS``, which ``CATEGORY_FOR_OPERATOR`` is built from and
    # which is consulted before anything else in this function. What stood here
    # was a second, hand-kept copy of those names that had drifted from the
    # first: it listed ``maxc``/``minc``, which CDO files under Arithc and which
    # the module lookup has returned as Arithmetic since that table existed — so
    # the branch could not have placed them anywhere even when it was reached.
    # Two lists of one thing disagree eventually; this one already had.
    if op in {"detrend", "trend", "subtrend", "addtrend", "regres"}:
        return NCExplorerCategory.REGRESSION
    # No arithmetic branch here: every operator CDO files under an arithmetic
    # module was already returned above. What stood here was a name list plus
    # ``op.endswith("c")``, which reached six of the seventy-eight and claimed
    # ``harmonic`` and ``lic`` as well.
    # No formatted-I/O branch here, for the same reason there is no arithmetic
    # or comparison one: every operator it could have placed is returned above.
    # What stood here was
    #
    #     if op.startswith("output") or op.startswith("input"):
    #         return NCExplorerCategory.FORMATTED_IO
    #
    # and it was already dead for the eighteen ``output*`` operators before this
    # change — all of them are ``nout == 0``, and that test returned Information
    # several branches earlier, which is how the category it names ended up with
    # no ``output*`` operator in it except the eight the curated list pinned by
    # hand. A branch that cannot fire is worse than none: it reads as the rule
    # and is not the rule, and it is why the old category looked deliberate.
    #
    # The five ``input*`` names were reachable and are now returned by the
    # "Formatted input" module lookup instead. Measured across the catalog after
    # the change: all 23 ``input*``/``output*`` operators are claimed by the
    # curated list or by a module title, and none reaches this point.
    return NCExplorerCategory.MISCELLANEOUS


# ---------------------------------------------------------------------------
# What the result looks like
#
# ``cdo --operators`` gives each index a one-line description that says what it
# measures and nothing about the shape of the answer. Two things about that
# shape decide whether a number is read correctly, and neither is visible
# anywhere in the app today, so they are appended to the description.
#
# Every claim below was measured on CDO 2.6.0 against a 730-day daily series
# starting 2000-01-01. See the sets themselves for which operators were found
# to behave which way; four do not follow their family and are called out
# individually rather than being described wrongly in bulk.
# ---------------------------------------------------------------------------

_ECA_PERIOD_NOTE = (
    "Covers the whole input series unless freq= is given, and dates the result "
    "at the last timestep that contributed to it."
)

_ETCCDI_PERIOD_NOTE = (
    "The ETCCDI form: yearly by default rather than whole-series, a period "
    "crossing a year boundary is accounted to the first year, and each result "
    "is dated at the middle of its interval — so this and the eca_ operator of "
    "the same name differ in both the number of timesteps and their dates."
)

_TWO_VARIABLE_NOTE = (
    "Writes two variables: the index, and a count of the qualifying periods. "
    "Most of the app shows the first one only."
)

#: What every one of the twenty-four comparison operators actually returns.
#:
#: ``cdo --operators`` gives them titles — "Equal", "Greater than constant" —
#: which name the test and say nothing about the answer. Two facts decide
#: whether that answer can be read, and the three-branch formula CDO prints for
#: each operator is entirely about them:
#:
#:     o(t,x) = 1 if the comparison holds, 0 if it does not, and miss if either
#:              operand is miss.
#:
#: The second is the one that is worth a sentence. Missing does **not** become 0,
#: so a mask cannot be summed as if it were a count of "did not hold" — the
#: cells where the question was unanswerable are still unanswerable. It is also
#: what separates ``gtc,273.15`` from the ``expr`` a user might write instead:
#: ``expr,'m = tas > 273.15'`` returns 0 where ``tas`` is missing.
#:
#: Verified on the installed CDO 2.6.0 rather than read off the formula: a field
#: with 101 missing cells through ``gtc`` came back with values in exactly
#: {0, 1}, and missing in exactly the 101 cells the input had missing.
_COMPARISON_MASK_NOTE = (
    "The result is a mask, not a value: 1 where the comparison holds and 0 "
    "where it does not. Where either operand is missing the result is missing "
    "rather than 0, so a cell that could not be compared is not counted as a "
    "cell that failed the comparison."
)

#: Membership form of :data:`COMPARISON_OPERATORS`, since ``_describe`` runs
#: once per operator in the catalog. Derived, not a second list — the whole
#: point of that tuple is that there is nowhere else to keep these names.
_COMPARISON_NAMES = frozenset(COMPARISON_OPERATORS)

#: Verified whole-series + last-timestep dating. ``eca_rr1``/``eca_r1mm`` are
#: deliberately absent: they share the eca_pd module and come out dated at the
#: middle of the series instead, so the sentence would be wrong for them.
_ECA_WHOLE_SERIES = frozenset({
    "eca_cdd", "eca_cwd", "eca_cfd", "eca_csu", "eca_su", "eca_tr", "eca_fd",
    "eca_id", "eca_hd", "eca_sdii", "eca_pd", "eca_r10mm", "eca_r20mm",
    "eca_rx1day", "eca_rx5day", "eca_cwdi", "eca_hwdi", "eca_cwfi",
    "eca_hwfi", "eca_gsl", "eca_etr",
})

#: Verified yearly + mid-interval dating.
_ETCCDI_YEARLY = frozenset({
    "etccdi_cdd", "etccdi_cwd", "etccdi_fd", "etccdi_id", "etccdi_su",
    "etccdi_tr", "etccdi_rx1day", "etccdi_rx5day", "etccdi_csdi",
    "etccdi_wsdi", "etccdi_tx90p", "etccdi_tx10p", "etccdi_tn90p",
    "etccdi_tn10p", "etccdi_r95p", "etccdi_r99p",
})

#: Verified to write two data variables rather than one.
_TWO_VARIABLE_INDICES = frozenset({
    "eca_cdd", "etccdi_cdd", "eca_cwd", "etccdi_cwd", "eca_cfd", "eca_csu",
    "eca_rx5day", "etccdi_rx5day", "etccdi_rx5daymon",
    "eca_cwdi", "eca_hwdi", "eca_cwfi", "eca_hwfi",
    "etccdi_csdi", "etccdi_wsdi",
})

# ---------------------------------------------------------------------------
# Per-operator caveats: what this build does that its name does not predict
#
# Measured, not inferred — each entry ran against the 730-day series and
# produced what the note describes.
#
# The table was written for indices whose *default output* is not what their
# family suggests, and it is named for that. Six operators added since do not
# fit that description: they do not run at all on the installed CDO. The
# mechanism is still the right one — it is the only thing in the app that puts a
# measured, per-operator, version-specific fact in front of a user *before* they
# press Run, which is exactly what "this will abort" needs — so it was widened
# rather than duplicated. What a caveat has in common is not that it concerns a
# default; it is that the operator's name and its one-line description both fail
# to predict what happens next.
# ---------------------------------------------------------------------------

#: The seasonal comparison module is broken, and the evidence that it is the
#: module rather than the inputs is a controlled comparison. It was first
#: recorded against 2.6.0 in ``operator_lab/profiles.py`` and re-measured in
#: full against 2.6.3, which is what these numbers are:
#:
#:   * all six ``yseas`` comparisons abort with "Season MAM already allocated!"
#:     (exit 1) on (series, yseasmean);
#:   * ``yseasadd`` and ``yseassub`` succeed on **the same two files**, so the
#:     operands are well-formed and the pairing is right;
#:   * all six ``ymon`` comparisons succeed on (series, ymonmean), so neither
#:     the comparison half of the module nor the series/climatology idiom is
#:     what is broken.
#:
#: (``operator_lab``'s note names ``yseasrange`` as a third control. It is not
#: one and cannot be: ``cdo --operators`` gives it (1|1), so handed two files it
#: fails with "Operator cannot be assigned" — a different failure, about arity,
#: that says nothing about the season table. Corrected there too.)
#:
#: Handed a single-season file as both inputs the same operators say "Season MAM
#: not found!" about the season that file is made of — the module stores and
#: looks the season table up under different keys. No input the harness can
#: build gets past it, and there is no ``--seasonstart`` to change the
#: convention with.
#:
#: Kept as an explicit list of versions rather than a single one, because it has
#: now survived an upgrade: naming only the version it was found in would have
#: made the warning look stale the moment CDO moved, and dropping the version
#: entirely would make it un-retestable.
#: ``tests/test_comparison_category.py`` asserts the installed CDO is one of
#: these *and* re-measures the abort, so a CDO that fixes this fails a test
#: rather than leaving the app warning about a bug that no longer exists.
_YSEASCOMP_BROKEN_IN: Tuple[str, ...] = ("2.6.0", "2.6.3")

_YSEASCOMP_ABORTS = (
    "Broken in every CDO this has been measured against, "
    f"{' and '.join(_YSEASCOMP_BROKEN_IN)} included: this aborts with "
    "\"Season MAM already allocated!\" whatever the two input files are. The "
    "module mis-keys its own season table — yseasadd and yseassub succeed on "
    "exactly the same pair of files, and all six ymon comparisons succeed on "
    "theirs. It still leaves an output file behind, but a header-only one with "
    "no data arrays in it that CDO itself then refuses to open, so a file "
    "appearing is not a sign it worked. There is no argument that avoids this; "
    "use the ymon operators, or compare against a single season selected with "
    "selseas."
)

def _open_file_limit() -> int:
    """This process's soft limit on open file descriptors, or 0 if unknowable.

    Read rather than assumed, because it is the number that decides whether a
    given split or merge runs and it is not a constant: macOS defaults vary
    between 256 and a million depending on how the shell was launched, and a
    Linux default of 1024 is common. Quoting a wrong number in a warning is
    worse than quoting none.
    """
    try:
        import resource
    except ImportError:                                     # pragma: no cover
        return 0                                            # Windows
    try:
        soft, _hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    except (OSError, ValueError):                           # pragma: no cover
        return 0
    return soft if soft not in (-1, resource.RLIM_INFINITY) else 0


def _open_file_note(operator: str) -> str:
    """The caveat for an operator that holds every input or output file open.

    Five modules document it — Merge and Collgrid open every *input* at once,
    Split, Splittime, Distgrid and Intyear open every *output* — and CDO's own
    note says only that "the maximum number of open files depends on the
    operating system", which is true and unactionable. This says which
    direction the operator is exposed in and what this machine's limit is.

    ``intyear`` is named explicitly rather than caught by a prefix: it is the
    only output-side operator in the list whose name does not begin "split" or
    "distgrid", and defaulting it to "input" would have described the wrong
    end of the operator — it takes exactly two input files and writes one per
    year.
    """
    opens = ("output" if operator.startswith(("split", "distgrid"))
             or operator == "intyear" else "input")
    note = (f"Opens every {opens} file simultaneously, so a large run can fail "
            f"on the operating system's open-file limit rather than on "
            f"anything about the data.")
    limit = _open_file_limit()
    if limit:
        note += (f" This process's limit is {limit:,}, so that is roughly the "
                 f"number of {opens} files at which it stops working; raise it "
                 f"with `ulimit -n` before starting the application.")
    return note


#: Operators that *extend* their output file rather than create it.
#:
#: One, and its whole documented purpose is to be the one: "Concatenates all
#: input datasets and appends the result to the end of outfile. If outfile does
#: not exist it will be created." Every other operator in CDO writes its output
#: from nothing, which is the assumption the execution layer's clean-up is built
#: on — a path that existed before the run is left alone, because deleting it
#: would destroy data this run did not produce.
#:
#: For ``cat`` that assumption inverts, and it was measured rather than
#: reasoned about. Against a 54,444-byte / 180-timestep output, ``cdo cat`` from
#: a large input killed part-way through left the file at 14.7 MB after 0.5 s
#: and 45.6 MB after 1.2 s — while ``cdo ntime`` still reported 180 timesteps,
#: because the NetCDF header is only committed at close. The file therefore
#: *reads* as untouched and is tens of megabytes of nothing on disk, and the
#: Operators whose result depends on the process's working directory, mapped to
#: the parameter that overrides it.
#:
#: For every other operator in the catalog the working directory is an
#: implementation detail: paths are absolute by the time the execution layer
#: builds argv, and a run started anywhere produces the same files in the same
#: places. ``cmor`` is the exception, twice over — ``drs_root`` defaults to the
#: working directory, so that is where the output tree lands, and ``info``
#: defaults to ``CWD/.cdocmorinfo``, so that is where the run's global
#: attributes are read from. Two of the three things that decide what a ``cmor``
#: run produces are therefore invisible in its command line.
#:
#: Which is what this table is for. It has two readers and they ask different
#: questions of it — ``nc_integration`` reads the parameter name, to know which
#: directory to scan for output; ``session_log`` reads the *membership*, to know
#: that a recorded command is not reproducible without a ``cd`` in front of it.
#: One table rather than a copy in each, because a second list here would be a
#: list of one thing, and this file has recorded four times what those do.
CWD_DEPENDENT_OPERATORS: Dict[str, str] = {"cmor": "drs_root"}


def depends_on_working_directory(name: str) -> bool:
    """Whether ``name``'s result depends on where the process was started.

    True for exactly one operator; see :data:`CWD_DEPENDENT_OPERATORS`. A
    function rather than a bare ``in`` at each call site so the surfaces that
    only need the yes/no do not have to know the table's shape.
    """
    return name in CWD_DEPENDENT_OPERATORS


#: clean-up deliberately left it that way. Recording the size before the run and
#: truncating back to it is the only thing that restores what the caller had.
#:
#: ``cmor`` is deliberately **not** here, and it is the one operator that made
#: the question worth answering rather than assuming. ``cmor,…,output_mode=a``
#: with ``last_chunk=<file>`` is append semantics by CDO's own description, so
#: on the face of it this set is exactly where it belongs. Three things say
#: otherwise, and the first is decisive on its own:
#:
#: * **This set is keyed by operator, and that is not the shape of the fact.**
#:   ``cat`` always appends; ``cmor`` appends only when ``output_mode`` reads
#:   ``a``, which is a parameter *value*. Membership here would make the
#:   append-size snapshot apply to every ``cmor`` run including the default
#:   replace mode. The schema already has the right shape for a value-decided
#:   fact — see :func:`writes_output_prefix`, which resolves ``map3d`` per
#:   call — and this would be the second one, so the honest change is a
#:   per-call predicate rather than a name in a set.
#: * **The snapshot has nothing to snapshot.** ``_ResolvedCall.append_sizes`` is
#:   built from ``aliased_outputs``, and ``cmor`` has none: ``nout == 0``, and
#:   the file it would extend is named by the ``last_chunk`` *parameter*.
#:   Adding the name to this set would therefore be inert — it would record
#:   sizes for an empty list — which is the worst of the three outcomes, because
#:   it reads as handled and does nothing.
#: * **It cannot be measured on this build.** ``cat``'s entry above is a
#:   measurement, byte counts and all. The equivalent for ``cmor`` needs a run,
#:   and ``cdo --config has-cmor`` is ``no`` here. Writing a truncation rule for
#:   a file format whose partial-write behaviour nobody in this project has
#:   observed would be a guess that destroys user data when it is wrong.
#:
#: Left out, therefore, and stated rather than passed over: a ``cmor`` run in
#: append mode that fails part-way may leave a lengthened chunk file behind, and
#: this application does not restore it. That is a known gap with a named cause,
#: not an oversight, and closing it needs a CMOR-enabled binary to measure
#: against first.
APPENDING_OPERATORS: "frozenset[str]" = frozenset({"cat"})


#: The CDO global options this app has no way to emit, and the operators whose
#: documented use needs one. See ``_MISSING_GLOBAL_OPTIONS_NOTE``.
_GLOBAL_OPTION_USERS = {
    "copy":  "-f nc",
    "clone": "-f nc",
    "cat":   "-f nc",
    "pack":  "-b I32",
    "unpack": "-b F64",
    "bitrounding": "-f nc4 -z zip",
    "setchunkspec": "--chunkspec",
    "setfilter": "--filter",
    # Complex numbers exist in NetCDF4 and EXTRA and nowhere else, so both the
    # operator that makes them and the one that reads them need a format the
    # forms cannot ask for. Measured on 2.6.3:
    #   cdo retocomplex gauss.nc c.nc  -> cdi error (cdfDefDatatype): CDI
    #      library does not support complex numbers with NetCDF classic! exit 1
    #   cdo -f nc4 retocomplex gauss.nc c.nc                            exit 0
    #   cdo -f nc4 fourier,-1 gauss.nc o.nc -> (Abort): This operator needs
    #      fields with complex numbers!                                 exit 1
    #   cdo -f nc4 fourier,-1 c.nc o.nc                                 exit 0
    # The manual's own example spells it the other way round with EXTRA —
    # ``cdo -f ext fourier,1 -fourier,-1 …`` — and either format works; nc4 is
    # named here because it is the one this app can open again afterwards.
    "fourier": "-f nc4",
    "retocomplex": "-f nc4",
}

_MISSING_GLOBAL_OPTIONS_NOTE = (
    "Its documented use needs a CDO global option — `cdo {option} {operator} "
    "…` — and the operator forms have no field for one. Reachable from a "
    "script or the batch runner, which pass `options=` to the execution layer; "
    "not yet from the toolbar, the palette or the model builder."
)

#: fldcor/fldcovar handed two series of different lengths.
#:
#: This is what this table exists for. Measured on 2.6.3 with a 6-timestep file
#: against a 3-timestep one:
#:
#:     cdo fldcor a.nc b3.nc out.nc
#:     -> "cdo fldcor (Warning): Input streams have different number of time
#:        steps!", **exit 0**, and out.nc holds 3 timesteps.
#:
#: Exit 0 and a well-formed file, so every surface in this application reports
#: it as a success — the run finished, the output opened, the numbers are
#: plausible. They are also silently the wrong numbers: the longer series was
#: truncated to the shorter one's length, and CDO paired timestep 1 with
#: timestep 1 regardless of what dates either carried.
#:
#: timcor and timcovar do not share this. The same pair gets them "different
#: number of fields!", then a cdi error, exit 1 — and an unreadable output file
#: left on disk, which ``_discard_partial_outputs`` did not remove because it
#: only runs when the process failed to *complete*, and exiting 1 counts as
#: completing. That is handled in ``nc_integration`` rather than here.
_FLDCOR_TRUNCATES = (
    "Two inputs of different lengths do not fail here: CDO warns on stdout, "
    "exits 0, and silently truncates the result to the shorter of the two "
    "series. Measured on this build — 6 timesteps against 3 wrote a 3-timestep "
    "answer with no error of any kind. The dates are not checked either, so "
    "timestep 1 is paired with timestep 1 whatever they are dated. Check that "
    "both files have the same number of timesteps before trusting this; the "
    "operator panel now refuses the pair rather than letting it through."
)

#: What ``cmor`` leaves on disk, and where. Its catalog line says what the
#: operator is for and nothing about the one thing a user has to know to find
#: the result.
#:
#: ``nout == 0`` here does not mean "writes nothing" — it means CMOR chooses the
#: filenames. It writes one file per output variable, named from the project's
#: DRS template, under ``drs_root``; and ``drs_root`` defaults to the working
#: directory, as does the ``.cdocmorinfo`` it reads when ``info`` is not given.
#: For every other operator in this catalog the working directory is an
#: implementation detail of the execution layer. For this one it decides both
#: what the run reads and where the result lands, which is why NCExplorer pins
#: it per run and records it in the session log rather than leaving it to
#: whatever the process happened to inherit.
_CMOR_OUTPUT_NOTE = (
    "Writes no file at the command line: CMOR builds the filenames itself from "
    "the project's DRS template and writes one file per output variable under "
    "drs_root. Set drs_root, or the result lands in the run's working "
    "directory — which is also where an unset info= looks for .cdocmorinfo. "
    "NCExplorer records the working directory it used with each run, and scans "
    "drs_root afterwards to report what was actually written."
)

#: Said before the run, on every surface, because otherwise nothing in this
#: application can tell a user why ``cmor`` failed.
#:
#: CMOR is a build-time option and this build does not have it: ``cdo --config
#: has-cmor`` answers ``no`` on the installed 2.6.3, and every call gets "cdo
#: cmor (Abort): CMOR support not compiled in!" on stderr with exit 1. The
#: Features line of ``cdo --version`` — "8GB 8threads c++20 Fortran pthreads
#: HDF5 NC4/HDF5 dap sz proj sse4_2" — names no CMOR either, but ``--config``
#: is the signal this application probes: it answers yes *and* no, where the
#: Features line only ever offers an absence to read into.
#:
#: The text is unconditional rather than probed at import, deliberately. This
#: string is baked into ``OPERATOR_SCHEMA`` at module load, the schema is shared
#: by every surface, and the binary is a per-instance setting a user can point
#: elsewhere — so a description that asserted "your CDO cannot do this" would go
#: stale the moment they did. It says what to check and how; the live answer for
#: the binary actually in use comes from
#: ``nc_integration.NCExplorerIntegration.cmor_support()``, which probes it and
#: reports before the run.
_CMOR_CAPABILITY_NOTE = (
    "Needs a CDO built with CMOR support, which is a compile-time option many "
    "builds omit — including the one this was developed against. Check with "
    "`cdo --config has-cmor`; if it answers no, every call to this operator "
    "aborts with \"CMOR support not compiled in!\" whatever the parameters "
    "say, and the fix is a different CDO binary rather than a different "
    "command."
)

#: The Transformation section's silent no-op, one sentence per operator.
#:
#: Built from a template because the shape of the defect is identical across
#: all five and only the warning differs — and the warning is the part worth
#: quoting, since it is the only thing CDO says and it goes to stderr while the
#: run reports success.
#:
#: Measured on 2.6.3 against ``cdo -f nc random,r18x9,1``; in every case
#: ``cdo sinfon`` on the output showed the input's own variable on the input's
#: own 18x9 lonlat grid. This is ``fldcor``'s silent truncation one section
#: over: a finished file, a drawable map, and no transformation.
def _pass_through_note(warning: str, wanted: str) -> str:
    return (
        f"Handed anything but {wanted} this does **not** fail. Measured on "
        f"2.6.3 against a plain lonlat field it warns \"{warning}\" on stderr, "
        f"exits 0, and writes the input straight back out — same variable, "
        f"same grid, nothing transformed. The run reports success and the map "
        f"draws, so there is no symptom to notice. Check what the input "
        f"actually holds before trusting the output; the operator panel now "
        f"warns when the file does not look like {wanted}.")


#: Recorded because it is a property of the *build*, not of the operator or the
#: parameters, and nothing in the application can discover it.
#:
#: ``cdo --config`` is this app's capability probe everywhere else — it is how
#: ``cmor`` and MAGICS are answered — and it has **no FFTW key at all**. Asked
#: for one on 2.6.3 it answers "unknown config option: has-fftw3" and lists 24
#: has-* options, none of them naming FFTW. The ``Features`` line of ``cdo
#: --version`` is no better and is not parsed here on principle. So this is
#: hard-coded and says which build it was measured on, rather than being
#: probed the way ``cmor_support()`` probes CMOR.
#:
#: Measured on CDO 2.6.3, macOS x86_64, Features line "8GB 8threads c++20
#: Fortran pthreads HDF5 NC4/HDF5 dap sz proj sse4_2" — no FFTW:
#:
#:     cdo sp2gp,linear spec.nc o.nc   -> FFT does not work with len=44 (n=11)!
#:                                        (Warning): LIBFFTW3 support not
#:                                        compiled in! (Abort): FFT error!
#:                                        "Retry it with the fftw3 library!"
#:                                        exit 1
#:     cdo sp2gp,cubic  spec.nc o.nc   -> the same, len=104 (n=13)
#:     cdo dv2uv,linear sdsvo.nc o.nc  -> the same, len=44 (n=11)
#:     cdo sp2gp,quadratic / cdo sp2gp -> exit 0
#:
#: and the control that shows this is the *direction* rather than the type:
#:
#:     cdo gp2sp,linear gauss.nc o.nc  -> exit 0
#:     cdo uv2dv,cubic  uv.nc    o.nc  -> exit 0
_NO_FFTW3 = (
    "Grid type here depends on how CDO was built. On a CDO without FFTW3 — "
    "including the 2.6.3 this was measured on — the spectral-to-gridpoint "
    "direction accepts only quadratic: linear and cubic abort with \"LIBFFTW3 "
    "support not compiled in!\" and \"(Abort): FFT error!\", exit 1. The "
    "gridpoint-to-spectral direction (gp2sp, uv2dv) takes all three "
    "regardless. The fix is a CDO rebuilt against FFTW3, not a different "
    "command; there is no `cdo --config` key to check it with, so this cannot "
    "be probed the way CMOR support is.")

#: The two linear shorthands, which therefore have no working call at all on a
#: build without FFTW3 — with or without a parameter, since the type they are
#: shorthand for is the one that needs the FFT.
#:
#:     cdo sp2gpl spec.nc o.nc              -> (Abort): FFT error!  exit 1
#:     cdo sp2gpl,quadratic spec.nc o.nc    -> (Abort): FFT error!  exit 1
#:     cdo sp2gpl,trunc=42 spec.nc o.nc     -> (Abort): FFT error!  exit 1
#:     cdo dv2uvl sdsvo.nc o.nc             -> (Abort): FFT error!  exit 1
#:
#: and their forward counterparts, which are unaffected:
#:
#:     cdo gp2spl gauss.nc o.nc             -> exit 0
#:     cdo uv2dvl uv.nc    o.nc             -> exit 0
_NO_FFTW3_ALWAYS = (
    "This is the linear shorthand, and linear is exactly the type a CDO built "
    "without FFTW3 cannot do in this direction — so on such a build **every** "
    "call aborts with \"(Abort): FFT error!\", with or without a parameter, "
    "measured on 2.6.3. Use the quadratic form (sp2gp / dv2uv with no type) or "
    "rebuild CDO against FFTW3. The gridpoint-to-spectral shorthands gp2spl "
    "and uv2dvl are unaffected and work on this build.")

#: Operators whose behaviour on this build is not what their name predicts.
_SURPRISING_DEFAULTS = {
    # -- runs, exits 0, and answers the wrong question --
    "fldcor": _FLDCOR_TRUNCATES,
    "fldcovar": _FLDCOR_TRUNCATES,

    # -- Transformation: the same defect, five times over --
    "gp2sp": _pass_through_note(
        "No data on regular Gaussian grid found!",
        "a field on a global regular Gaussian grid"),
    "gp2spl": _pass_through_note(
        "No data on regular Gaussian grid found!",
        "a field on a global regular Gaussian grid"),
    "sp2gp": " ".join((_pass_through_note(
        "No spectral data found!", "spectral coefficients"), _NO_FFTW3)),
    "sp2gpl": " ".join((_pass_through_note(
        "No spectral data found!", "spectral coefficients"), _NO_FFTW3_ALWAYS)),
    "sp2sp": _pass_through_note(
        "No spectral data found!", "spectral coefficients"),
    "spcut": _pass_through_note(
        "No spectral data found!", "spectral coefficients"),
    "uv2dv": _pass_through_note(
        "U-wind not found!\" and \"V-wind not found!",
        "u and v on a Gaussian grid"),
    "uv2dvl": _pass_through_note(
        "U-wind not found!\" and \"V-wind not found!",
        "u and v on a Gaussian grid"),
    "dv2uv": " ".join((_pass_through_note(
        "Divergence not found!\" and \"Vorticity not found!",
        "spectral divergence and vorticity"), _NO_FFTW3)),
    "dv2uvl": " ".join((_pass_through_note(
        "Divergence not found!\" and \"Vorticity not found!",
        "spectral divergence and vorticity"), _NO_FFTW3_ALWAYS)),
    # dv2ps declares no parameters and does not enforce that it has none:
    # measured, ``cdo dv2ps,a,b,c sdsvo.nc o.nc`` exits 0 and ignores all
    # three. Left undeclared deliberately — there is nothing to declare — and
    # said here so the empty entry reads as checked rather than as missed.
    "dv2ps": _pass_through_note(
        "Divergence not found!\" and \"Vorticity not found!",
        "spectral divergence and vorticity"),

    # -- Statistic: the Ensval output names, which the manual gets wrong --
    #
    # The last argument is a *base name*, not a file, and CDO fans it out into
    # three or four files whose suffixes it chooses. The module page documents
    # those suffixes and five of the seven are wrong on 2.6.3, so a user who
    # read the manual is looking for files that do not exist. Measured:
    #
    #   cdo enscrps  ref e1..e5 cbase  -> cbase.crps.nc  cbase.crps_pot.nc
    #                                     cbase.crps_reli.nc
    #     manual says: crps, crpspot, reli
    #   cdo ensbrs,5 ref e1..e5 obase  -> obase.brs.nc  obase.brs_reli.nc
    #                                     obase.brs_reso.nc obase.brs_unct.nc
    #     manual says: brs, brsreli, brsreso, brsunct
    #
    # The application no longer relies on either list — the run reports the
    # files it actually saw appear, via ``discovered_outputs`` — but the names
    # are still worth stating up front, because they are what the user will
    # look for in the folder afterwards.
    "enscrps": (
        "The last argument is a base name, not a file: this writes THREE "
        "files — <base>.crps.nc (the score), <base>.crps_pot.nc (the "
        "potential CRPS) and <base>.crps_reli.nc (the reliability), with "
        "CRPS = CRPS_pot + RELI. Measured on 2.6.3; the manual names the last "
        "two 'crpspot' and 'reli', which is not what this build writes. The "
        "first input file is the reference, not an ensemble member."
    ),
    "ensbrs": (
        "The last argument is a base name, not a file: this writes FOUR "
        "files — <base>.brs.nc, <base>.brs_reli.nc, <base>.brs_reso.nc and "
        "<base>.brs_unct.nc, with BRS = RELI - RESO + UNCT. Measured on "
        "2.6.3; the manual names the last three 'brsreli', 'brsreso' and "
        "'brsunct', which is not what this build writes. The threshold is "
        "echoed on stdout as 'brs_thres'. The first input file is the "
        "reference, not an ensemble member."
    ),

    # -- Statistic: what an ensemble run costs the operating system --
    #
    # The Ensstat module page states that every input file is opened at the
    # same time and that the OS limit on open file descriptors therefore
    # applies. Worth saying here rather than letting a 200-member ensemble
    # fail on a message about file descriptors that names no operator.
    **{name: (
        "Every input file is held open simultaneously, so a large ensemble "
        "can reach the operating system's open-file limit — raise it with "
        "'ulimit -n' if a run fails on too many open files. All members must "
        "have the same structure and the same variables. -O (--overwrite) is "
        "this module's documented global option, for re-running over an "
        "existing output."
    ) for name in ("ensmin", "ensmax", "enssum", "ensmean", "ensavg",
                   "ensvar", "ensvar1", "ensstd", "ensstd1", "ensrange",
                   "enspctl", "ensskew", "enskurt", "ensmedian")},

    # ``ensrkhist*``: the docs say more than one level is prohibited. The
    # binary disagrees about how strongly — measured on a three-level file, it
    # warns twice and exits 0, so the "prohibition" is a warning and the
    # result is whatever one level's histogram was. ``check_ensemble_levels``
    # in core/units.py says so before the run rather than after.
    **{name: (
        "Single-level data only. A file with more than one level is not "
        "refused — measured on 2.6.3 it warns 'More than one level not "
        "supported when processing ranked histograms' and exits 0 — so use "
        "splitlevel and run this once per level. The first input file is the "
        "observations (obsfile), not an ensemble member."
    ) for name in ("ensrkhisttime", "ensrkhistspace")},

    # -- needs a build feature, and writes where nothing is looking --
    "cmor": f"{_CMOR_CAPABILITY_NOTE} {_CMOR_OUTPUT_NOTE}",

    # -- Interpolation: what the operator needs of its input --
    #
    # These are input requirements rather than defaults, and they are here for
    # the same reason the Import/Export entries below are: this is the one
    # string every surface already shows. A user whose file does not meet them
    # gets a CDO abort naming a variable they never heard of, and the remedy —
    # which is CDO's own — is two operators away.
    **{name: (
        "Needs data on ECHAM hybrid model levels. The a/b coefficients must be "
        "present at the hybrid layer INTERFACES (model half-layers), and the "
        "required fields are identified by GRIB1 code or CF standard name: 152 "
        "log surface pressure, 134 surface_air_pressure, 130 air_temperature, "
        "129 surface_geopotential and 156 geopotential_height. Run sinfo first "
        "to check the vertical coordinate is recognised as a hybrid system; if "
        "it is not, setzaxis with a hybrid Z-axis description is CDO's own "
        "remedy. The vct parameter is a plain ASCII table, not a NetCDF file."
    ) for name in ("remapeta", "remapeta_s", "remapeta_z")},
    **{name: (
        "Levels are in PASCAL, not hPa — the manual's own example is "
        "92500,85000,50000,20000, and hPa values run successfully onto levels a "
        "hundred times too low. Needs the a/b coefficients at the hybrid layer "
        "INTERFACES plus GRIB1 codes 152/134/130/129/156 or their CF standard "
        "names (surface_air_pressure, air_temperature, surface_geopotential, "
        "geopotential_height)."
    ) for name in ("ml2pl", "ml2plx")},
    **{name: (
        "ICON-specific: the manual says outright that this is an implementation "
        "for NetCDF files from the ICON model and may not work with data from "
        "other sources. The input must carry the 3D air pressure in pascal, "
        "identified by the CF standard name air_pressure. Missing values in the "
        "input are not supported, and all variables must share one horizontal "
        "grid. Target levels are in PASCAL — e.g. 92500,85000,50000,20000."
    ) for name in ("ap2pl", "ap2plx")},
    **{name: (
        "ICON-specific: the manual says outright that this is an implementation "
        "for NetCDF files from the ICON model and may not work with data from "
        "other sources. The input must carry the 3D geometric height in metres, "
        "identified by the CF standard name "
        "geometric_height_at_full_level_center — geopotential height is a "
        "different field and will not be found. Missing values in the input are "
        "not supported. Target levels are in METRES."
    ) for name in ("gh2hl", "gh2hlx")},
    # Measured, and the reason this is not phrased as a preference: swapping the
    # two inputs exits 0 and writes a file of nothing but missing values.
    **{name: (
        "Input order is load-bearing and CDO does not check it: input 1 must be "
        "the 3D data variables and input 2 the 3D vertical source coordinate. "
        "Measured on 2.6.3, swapping them still exits 0 and writes a "
        "well-formed file containing only missing values. The tgtcoordinate "
        "parameter is a data file holding the 3D vertical TARGET coordinate, "
        "not a Z-axis description file."
    ) for name in ("intlevel3d", "intlevelx3d")},
    # The one mode switch in the section a user can hit by hand.
    **{name: (
        "Set either the level list or a Z-axis description file, never both: "
        "CDO aborts with \"Parameter zdescription and level can't be mixed!\". "
        "The level list is always sent as level=... because CDO refuses a bare "
        "list once any other parameter is set — measured on 2.6.3, "
        "intlevel,150,300,extrapolate=true aborts with \"Float parameter "
        ">extrapolate=true< contains invalid character at position 1!\"."
    ) for name in ("intlevel", "intlevelx")},

    # -- Import/Export: what the operator needs, said before it is run --
    #
    # These reach every surface without a line of GUI code, because all three
    # already show ``OperatorSpec.description``. ``operator_lab/profiles.py``
    # has carried skip reasons for both of these since it was written — "needs a
    # GrADS control file describing raw binary data", "needs a CM-SAF HDF5
    # file" — so the facts were known to the test lab and to nothing the user
    # can see.
    **{name: (
        "Reads a GrADS control (.ctl) file, which *is* the input — the .bin it "
        "describes is named inside it and must not be selected here. Only "
        "32-bit IEEE floats are supported for standard binary files. Give this "
        "-f nc in the options field: measured on 2.6.3, without it CDO writes "
        "GRIB with 16-bit packing however the output file is named, so a file "
        "called out.nc is neither NetCDF nor full precision."
    ) for name in ("import_binary", "import_grads")},
    "import_cmsaf": (
        "Needs a CDO built with HDF5 — 'cdo --version' must list HDF5 under "
        "Features, or every call fails on the library rather than on the file. "
        "Remapping CM-SAF's equal-area projections additionally needs PROJ 5 "
        "or newer. Give this -f nc in the options field to get NetCDF out."
    ),

    # -- does not run at all on the installed CDO --
    **{name: _YSEASCOMP_ABORTS
       for name in ("yseaseq", "yseasne", "yseasle", "yseaslt",
                    "yseasge", "yseasgt")},

    # -- runs, but not with the period or dating its family implies --
    "etccdi_hd": "Unlike the other etccdi_ indices this one is neither yearly "
                 "nor whole-series: it writes one value per input timestep and "
                 "ignores freq= entirely.",
    "etccdi_gsl": "Unlike the other etccdi_ indices this one writes one value "
                  "per input timestep and ignores freq= entirely.",
    "etccdi_rx1daymon": "Give this freq=month. With no argument it writes one "
                        "record per input timestep, all carrying the same "
                        "date, which no reader can interpret.",
    "etccdi_rx5daymon": "Give this freq=month. With no argument it writes one "
                        "record per input timestep, all carrying the same "
                        "date, which no reader can interpret.",
    "etccdi_sdii": "Whole-series rather than yearly, dated at the middle of "
                   "the interval.",
    "etccdi_r1mm": "Whole-series rather than yearly, dated at the middle of "
                   "the interval.",
    "etccdi_r10mm": "Whole-series rather than yearly, dated at the middle of "
                    "the interval.",
    "etccdi_r20mm": "Whole-series rather than yearly, dated at the middle of "
                    "the interval.",
    "eca_rr1": "Dated at the middle of the interval, unlike most eca_ indices.",
    "eca_r1mm": "An alias of eca_rr1. Dated at the middle of the interval, "
                "unlike most eca_ indices.",

    # -- the File operation section --
    #
    # The one operator in CDO that appends. Worth saying on every surface
    # because two things about it are the opposite of every other operator: an
    # output that already exists is the intended case rather than something to
    # warn about, and running it twice does not give the same answer twice.
    "cat": "Appends to outfile instead of replacing it, and creates it only if "
           "it is not there. An existing output is the intended case, so "
           "running this twice concatenates twice. Use copy for the "
           "create-or-replace behaviour every other operator has.",

    # Measured, and the reason setchunkspec/setfilter are the only two
    # operators in the section that fail on well-formed input. Both grammars
    # come straight from the manual and neither parses.
    "setchunkspec":
        "Quote the chunkspec inside the parameter file: a line reading "
        "random=t=1 aborts with \"Missing value for parameter key >random<\", "
        "and random=\"t=1\" works. Measured on CDO 2.6.3 — the outer "
        "key=value parser splits on = and , which are exactly the characters "
        "the documented chunkspec grammar needs. The CDO option --chunkspec "
        "applies one spec to every variable and avoids the file entirely.",
    "setfilter":
        "Quote the filterspec inside the parameter file: a line reading "
        "random=307,9 aborts with \"Too many values for parameter key "
        "<random>\", and random=\"307,9\" parses. Measured on CDO 2.6.3. The "
        "filter plugins themselves also have to be installed and "
        "HDF5_PLUGIN_PATH pointed at them, or the run fails inside NetCDF "
        "with \"nc_def_var_filter failed\". The CDO option --filter applies "
        "one filter to every variable.",

    # Both of these write a file *and* print their per-variable answer to
    # stdout, which no other (1|1) operator does. Said here as well as being
    # lifted out of stdout by ``stream_notices``, because the useful part is
    # knowing to ask for it.
    "pack": "printparam=true prints the add_offset and scale_factor chosen for "
            "each variable to stdout, in addition to writing the output file. "
            "Without the CDO option -b the packed type is 16-bit integer.",
    "bitrounding":
        "printbits=true prints the number of mantissa bits kept per variable "
        "to stdout, in addition to writing the output file. Bit rounding on "
        "its own does not make the file smaller — it makes it compressible, so "
        "it is worth running only alongside compression (-z zip and a NetCDF4 "
        "output format).",

    # The four modules that hold every file open at once. The number is this
    # machine's, read at import from the process's own limit rather than
    # guessed, because it is the number that decides whether a given split
    # succeeds and it varies by an order of magnitude between systems.
    **{name: _open_file_note(name) for name in (
        "merge", "mergetime", "collgrid",
        "splitcode", "splitparam", "splitname", "splitlevel", "splitgrid",
        "splitzaxis", "splittabnum", "splitensemble", "splitvar",
        "splithour", "splitday", "splitseas", "splityear", "splityearmon",
        "splitmon", "distgrid",
        # intyear belongs here and was missing. Its manual page states it
        # outright — "This operator needs to open all output files
        # simultaneously. The maximum number of open files depends on the
        # operating system!" — and it is the one operator in the Interpolation
        # section where that limit decides whether the run is possible: one
        # output file per year, so a 40-year interpolation wants 40 descriptors
        # at once and a 400-year one wants 400.
        "intyear",
    )},
}

def _append_global_option_notes() -> None:
    """Add the missing-global-option note to the operators that need one.

    A second pass rather than eight more lines in the table above, because an
    operator can need both notes and several do: ``pack`` and ``bitrounding``
    have something to say about stdout *and* need an option, and ``cat`` appends
    *and* is one of the format converters. The two facts also have different
    lifetimes — the entries above are about what CDO does, and this one is about
    a gap in this application that is meant to close.
    """
    for operator, option in _GLOBAL_OPTION_USERS.items():
        existing = _SURPRISING_DEFAULTS.get(operator, "")
        note = _MISSING_GLOBAL_OPTIONS_NOTE.format(option=option,
                                                   operator=operator)
        _SURPRISING_DEFAULTS[operator] = f"{existing} {note}".strip()


_append_global_option_notes()


# ---------------------------------------------------------------------------
# The Miscellaneous section's worst failure mode: a blank required parameter
# does not abort, it hangs
#
# Every operator below was run on 2.6.3 as ``cdo <op> in.nc out.nc`` with no
# parameter at all and with stdin at ``/dev/null``. None of them exited. No
# output, no error, no timeout of CDO's own — the process simply stays alive
# until it is killed. ``cdo setrtoc in.nc out.nc`` is a hang; ``cdo setrtoc,0,1
# in.nc out.nc`` is the clean "Too few arguments! Need 3 found 2."
#
# So the *partial* case aborts properly and only the empty one hangs, which is
# what makes this easy to miss: every way of getting the parameter wrong is
# well behaved except leaving it out, and leaving it out is what a form does
# when a user has not typed yet.
#
# It is not a stdin wait — see :func:`reads_stdin`, which records the same
# distinction for ``outputtab`` — so closing stdin does not release it and the
# execution layer cannot rescue it. :func:`missing_required_parameters` is the
# only thing standing between a user and a frozen run, which is why every
# required parameter in this section is declared required and none of them was
# left optional for convenience.
#
# Listed rather than derived from "has a required parameter", because the two
# are not the same set and pretending otherwise would put a false warning on
# most of the catalog: ``cdo gtc in.nc out.nc`` is missing a required parameter
# too and aborts in milliseconds with "Too few arguments!". These nineteen were
# each measured to hang.
_MISC_HANGS_WITHOUT_PARAMETERS: Tuple[str, ...] = (
    "bandpass", "lowpass", "highpass",
    "setvals", "setrtoc", "setrtoc2",
    "const", "random", "seq", "stdatm",
    "histcount", "histsum", "histmean", "histfreq",
    "cmorlite",
    "uvDestag", "rotuvb", "rotuvNorth", "projuvLatLon",
)

_HANGS_WITHOUT_PARAMETERS_NOTE = (
    "Leave no parameter blank: measured on CDO 2.6.3, this operator run with "
    "no parameter at all does not fail — it hangs indefinitely, with stdin "
    "closed and nothing printed. A partly-filled parameter list aborts "
    "cleanly; only the empty one hangs."
)


def _append_hang_notes() -> None:
    """Warn on the nineteen operators a blank parameter list freezes.

    A second pass for the same reason ``_append_global_option_notes`` is one:
    several of these already carry a note about something else — ``random``
    about its quoted-seed parsing, ``cmorlite`` about its table — and this fact
    is about the run rather than about the result, so it is appended rather
    than folded into a sentence that is already saying something different.
    """
    for operator in _MISC_HANGS_WITHOUT_PARAMETERS:
        existing = _SURPRISING_DEFAULTS.get(operator, "")
        _SURPRISING_DEFAULTS[operator] = \
            f"{existing} {_HANGS_WITHOUT_PARAMETERS_NOTE}".strip()


_append_hang_notes()


# ---------------------------------------------------------------------------
# The Statistic section's help text
#
# The section's overview page opens with the distinction the whole section
# turns on, and the catalog descriptions this app inherited do not carry it:
# ``fldmean`` was "Field mean" and ``fldavg`` was "Field average", which
# distinguishes nothing and reads as two words for one operation. They are two
# operations, and which one you want depends entirely on whether your field has
# missing values.
#
# Quoted from the 2.6.3 Statistic page: "While computing the mean, only the not
# missing values are considered to belong to the sample with the side effect of
# a probably reduced sample size. Computing the average is just adding the
# sample members and divide the result by the sample size. For example, the
# mean of 1, 2, miss and 3 is (1+2+3)/3 = 2, whereas the average is
# (1+2+miss+3)/4 = miss/4 = miss."
#
# Attached by *suffix over the operators of the Statistic modules* rather than
# by a hand-written list of names, for the reason ``_MODULE_CATEGORY`` exists:
# there are 289 operators in this category and a list would be wrong within a
# release. The module set is the same one the categorisation uses, so an
# operator cannot be in the section for one purpose and out of it for the
# other.

_MEAN_NOTE = (
    "mean vs avg: 'mean' ignores missing values and averages over the ones "
    "that are left, so the sample size shrinks; 'avg' includes them, so any "
    "missing value makes the result missing. The mean of 1, 2, miss, 3 is 2; "
    "the average is missing. On a field with no missing values the two are "
    "identical."
)

_VAR_NOTE = (
    "var vs var1: 'var' normalises by n (the population variance), 'var1' by "
    "n-1 (the sample variance). Use var1 when the timesteps or cells are a "
    "sample of something larger, which for model output they usually are. The "
    "difference is largest on short series — at n=5 the two differ by 25%."
)

_STD_NOTE = (
    "std vs std1: 'std' normalises by n, 'std1' by n-1 — the square roots of "
    "var and var1 respectively. Use std1 for a sample standard deviation."
)

_FLD_WEIGHTED_NOTE = (
    "Area-weighted: each cell contributes in proportion to its area, which on "
    "a lonlat grid means the poles count for much less than the equator. "
    "Measured on an 18x9 field, fldmean gives -2380.56 weighted and -1877.01 "
    "with weights=FALSE. Pass weights=FALSE for the plain per-cell mean."
)

_FLD_UNWEIGHTED_NOTE = (
    "Not area-weighted, unlike fldmean/fldavg/fldvar/fldstd: every cell "
    "counts the same however large it is."
)

_VERT_WEIGHTED_NOTE = (
    "Weighted by layer thickness, not by area — each level contributes in "
    "proportion to how thick it is. This needs layer bounds in the file; "
    "without them CDO warns 'Layer bounds not available' and falls back to "
    "constant weights, so the result is the unweighted one. Run "
    "genlevelbounds first if the weighting matters."
)

#: The four output shapes that are not the input's quantity, and the reason
#: they are called out together: anything in this application that labels an
#: axis or a colorbar from the input file's units is wrong for all four.
_SHAPE_NOTES = {
    "fldint": (
        "Returns an INTEGRAL, not a mean: each cell is multiplied by its area "
        "before summing, so the output unit is the input unit times m². A "
        "colorbar or axis labelled from the input's units is wrong for this "
        "operator."
    ),
    "fldcount": (
        "Returns a COUNT of non-missing cells, not a value in the input's "
        "units. The output is dimensionless however the input was labelled."
    ),
}

_INDEX_NOTE = (
    "Returns an INDEX, not a value: the number of the timestep at which the "
    "extremum occurs, counting from 1. The output is dimensionless — a "
    "colorbar labelled from the input's units is wrong for it — and its range "
    "is the length of the input series."
)

#: Every module of the Statistic section, by CDO's own title. Used to scope the
#: suffix rules below, so a ``*mean`` outside the section (``ymonmean`` is in,
#: ``mergetime`` is not) cannot pick up a note that does not apply to it.
_STATISTIC_MODULE_TITLES = frozenset({
    "Statistical values over a field", "Statistical values over all timesteps",
    "Statistical values over all variables",
    "Statistical values over grid boxes", "Statistical values over an ensemble",
    "Vertical statistics", "Zonal statistics", "Meridional statistics",
    "Ensemble statistics", "Ensemble validation tools",
    "Daily statistics", "Daily percentile",
    "Monthly statistics", "Monthly percentile",
    "Yearly statistics", "Yearly percentile",
    "Seasonal statistics", "Seasonal percentile",
    "Hourly statistics", "Hourly percentile",
    "Multi-year daily statistics", "Multi-year daily percentile",
    "Multi-year monthly statistics", "Multi-year monthly percentile",
    "Multi-year seasonal statistics", "Multi-year seasonal percentile",
    "Multi-year hourly statistics",
    "Multi-day hourly statistics", "Multi-day by the minute statistics",
    "Multi-year daily running statistics",
    "Multi-year daily running percentile",
    "Running statistics", "Running percentile",
    "Time range statistics", "Time range percentile",
    "Temporal percentile", "Cumulative sum over all timesteps",
    "Consecute timestep periods",
    "Weighted yearly mean from monthly data",
    "Weighted temporal mean from yearly data",
    "Remaps source points to target cells",
})


def _append_statistic_notes() -> None:
    """Attach the section's shared distinctions to the operators they apply to.

    Four rules, each derived rather than listed:

    * every ``*mean``/``*avg`` in a Statistic module gets the missing-value
      rule, because that is the difference between them and nothing else in
      the app said it;
    * every ``*var``/``*var1`` and ``*std``/``*std1`` gets the n / n-1 rule;
    * the Fldstat and Vertstat operators get their weighting note, split by
      which ones are actually weighted — ``fldskew``/``fldkurt`` are not, and
      the four Vertstat operators that ignore the key are not either;
    * the four operators whose output is not the input's quantity get a note
      saying so.
    """
    from .cdo_operator_catalog import CDO_OPERATOR_MODULES

    def note(operator: str, text: str) -> None:
        existing = _SURPRISING_DEFAULTS.get(operator, "")
        _SURPRISING_DEFAULTS[operator] = f"{existing} {text}".strip()

    for operator, module in sorted(CDO_OPERATOR_MODULES.items()):
        if module not in _STATISTIC_MODULE_TITLES:
            continue

        if operator.endswith(("mean", "avg")):
            note(operator, _MEAN_NOTE)
        elif operator.endswith(("var", "var1")):
            note(operator, _VAR_NOTE)
        elif operator.endswith(("std", "std1")):
            note(operator, _STD_NOTE)

        # Fldstat's area weighting, and which of its operators have it. CDO
        # accepts ``weights`` on all sixteen, but the manual's formula table
        # only defines a weighted form for the mean/avg/var/std family — skew
        # and kurtosis have none — so the note is split rather than blanket.
        if module == "Statistical values over a field":
            if operator.endswith(("mean", "avg", "var", "var1", "std",
                                  "std1")):
                note(operator, _FLD_WEIGHTED_NOTE)
            elif operator in ("fldskew", "fldkurt"):
                note(operator, _FLD_UNWEIGHTED_NOTE)

        # Vertstat, and only the six that read the key — see ``_VERT_WEIGHTS``
        # for why vertmax/vertmin/vertrange/vertsum are excluded.
        if module == "Vertical statistics" and operator in (
                "vertmean", "vertavg", "vertvar", "vertvar1", "vertstd",
                "vertstd1"):
            note(operator, _VERT_WEIGHTED_NOTE)

    for operator, text in _SHAPE_NOTES.items():
        note(operator, text)
    for operator in ("timminidx", "timmaxidx", "yearminidx", "yearmaxidx"):
        note(operator, _INDEX_NOTE)

    # Yearstat's own note, and the mistake it exists to stop. ``yearmean`` is
    # an *arithmetic* mean over whatever timesteps fall in the year; over
    # monthly input that weights February the same as January.
    # ``yearmonmean`` is the day-weighted one. The two names are one letter
    # apart in a menu and produce different numbers from the same file.
    for operator, sibling in (("yearmean", "yearmonmean"),
                              ("yearavg", "yearmonavg"),
                              ("timyearmean", "yearmonmean"),
                              ("timyearavg", "yearmonavg")):
        note(operator,
             f"Arithmetic, not day-weighted: every timestep in the year "
             f"counts equally, so over MONTHLY input a 28-day February counts "
             f"as much as a 31-day January. Use {sibling} for the "
             f"day-weighted mean of monthly data.")
    for operator in ("yearmonmean", "yearmonavg"):
        note(operator,
             "Day-weighted: each month contributes in proportion to its "
             "length, which is what you want when averaging monthly data to "
             "years. Use yearmean for the plain arithmetic mean.")

    # Timcumsum: the missing-value convention that makes its output continuous
    # where the input was not.
    note("timcumsum",
         "Missing values are treated as numeric ZERO rather than propagated, "
         "so the running total continues across a gap instead of becoming "
         "missing from that point on. The result never has a missing value "
         "after the first valid one, which can hide how much of the series "
         "was actually there.")


_append_statistic_notes()


#: What the ECHAM afterburner needs that no other operator does, and the reason
#: it is worth saying rather than leaving to a failed run. ``after`` is driven
#: entirely by a namelist on stdin — the operator token carries only an optional
#: vct file — so without one it runs with CDO's printed defaults (TYPE=0,
#: CODE=-1, LEVEL=-1, INTERVAL=0, MEAN=0, EXTRAPOLATE=1) and copies its input.
#:
#: The second half is the manual's own restriction and the one a model builder
#: user will hit: "The input files can't be combined with other CDO operators
#: because of an optimized reader for this operator."
_SURPRISING_DEFAULTS["after"] = (
    "Driven by an ECHAM namelist read from standard input, not by its "
    "parameters — give one in the stdin file row, or CDO runs with its own "
    "defaults (TYPE=0, CODE=-1, LEVEL=-1, INTERVAL=0, MEAN=0, EXTRAPOLATE=1) "
    "and simply copies the fields through. Needs spectral or Gaussian ECHAM "
    "data: anything else is \"Unsupported file structure (no spectral or "
    "Gaussian data found)!\". Its input cannot be piped from another operator "
    "— the manual excludes it because of an optimised reader."
)


# ---------------------------------------------------------------------------
# Conditional selection: the same sentence on all three surfaces
#
# Written as a function appended by ``_describe`` for the same reason
# ``_companion_note`` is: the menus, the command palette and the model builder
# all read ``spec.description``, so saying it once here is what keeps them in
# agreement, and ``audit_operator_surfaces.py`` checks that they are.
#
# Everything asserted below was run against the installed CDO 2.6.3. The
# swapped-argument result in particular is measured, not predicted:
# ``cdo ifthen data.nc mask.nc out.nc`` exited 0 and ``cdo diff out.nc mask.nc``
# printed nothing.
# ---------------------------------------------------------------------------

#: The half of the warning that is identical for all five ``if*`` operators.
_MASK_TRUTH = (
    "A value other than zero is true, zero is false, and where the mask is "
    "missing the output is missing."
)

#: The line a user needs when they have data but no mask. Not attributed to the
#: CDO documentation, because ``cdo -h ifthen`` on 2.6.3 carries no EXAMPLES
#: section — this is the two-step form that was actually run to verify the
#: behaviour described here.
_MASK_RECIPE_LINE = (
    "To build a mask from a data field: cdo gtc,0 infile mask."
)


def _conditional_note(operator: str) -> str:
    """Which file is the mask, and what happens when that is got backwards."""
    if operator in ("ifthen", "ifnotthen"):
        return (
            f"infile1 is the mask and infile2 is the data, not the other way "
            f"round. {_MASK_TRUTH} The number of fields in the mask must match "
            f"the data, or one timestep of it, or be exactly one, and the "
            f"output inherits its metadata from infile2. Swapping the two is "
            f"not an error: CDO exits 0 and hands back the mask file as though "
            f"it were the data, because a data field is non-zero almost "
            f"everywhere and so selects almost everything. "
            f"{_MASK_RECIPE_LINE} Then: cdo {operator} mask infile outfile"
        )
    if operator == "ifthenelse":
        return (
            "infile1 is the mask, infile2 supplies the values where it is true "
            f"and infile3 where it is false. {_MASK_TRUTH} A missing mask value "
            "yields missing — it does not fall through to infile3. infile2 and "
            "infile3 must hold the same number of fields, and the output takes "
            f"its metadata from infile2. {_MASK_RECIPE_LINE} Then: "
            "cdo ifthenelse mask infile2 infile3 outfile"
        )
    if operator in ("ifthenc", "ifnotthenc"):
        selects = "non-zero" if operator == "ifthenc" else "zero"
        return (
            f"The single input file is the mask, not the data: the output is "
            f"the constant c wherever the mask is {selects}, and missing "
            f"everywhere else — including wherever the mask itself is missing. "
            f"{_MASK_RECIPE_LINE} Then: cdo {operator},1 mask outfile"
        )
    if operator == "reducegrid":
        return (
            "The mask is the first parameter, not an input file, and its "
            "horizontal grid must be identical to infile's. Output keeps only "
            "the locations where the mask is non-zero. "
            # Measured: a 8x4 lon/lat field and a 13-cell mask gave
            # "unstructured : points=13 nvertex=4".
            "The result is an *unstructured* grid, which is why the CDO "
            "documentation's own example forces NetCDF — an unstructured grid "
            "cannot be stored in GRIB: "
            "cdo -f nc reducegrid,lsm.grb temp.grb tempOnLand.nc. "
            # The honest warning, from reading the loader rather than guessing.
            "This application cannot draw the result: the map canvas takes a "
            "variable with two dimensions to be lat/lon, and a reduced file's "
            "(time, ncells) is two dimensions, so it renders time against cell "
            "index as if it were a map instead of refusing the file. Use "
            "reducegrid to export or to compute over a region, not to display."
        )
    return ""


# ---------------------------------------------------------------------------
# EOFs: the precondition nothing enforces, and the swap that writes 730 files
#
# "Empirical Orthogonal Functions." is the whole of what the catalog says about
# six operators, and three of them carry no description at all. What it leaves
# out is everything that decides whether the answer means anything.
#
# Everything below was measured on the installed CDO 2.6.3 against
# sample_climate_tg.nc — 730 daily timesteps, 36x18 = 648 gridpoints, one
# variable — and against the anomaly file built from it with
# ``cdo sub infile -timmean infile anom.nc``.
# ---------------------------------------------------------------------------

#: The six that compute EOFs, and the two that project onto them. Spelled out
#: rather than derived from the category, because the note below is about what
#: these operators *do* and not about where they are filed — and because a set
#: built from ``spec.category`` could not be used during ``_describe``, which
#: runs while the schema is still being assembled.
_EOF_OPERATORS = frozenset({
    "eof", "eoftime", "eofspatial", "eof3d", "eof3dtime", "eof3dspatial",
})

_EOFCOEFF_OPERATORS = frozenset({"eofcoeff", "eofcoeff3d"})

#: True of all six eof operators, and the one thing a user must know before
#: pressing Run. Measured: ``cdo eof,3 <raw series> eval.nc eofs.nc`` exits 0
#: and writes both files. Nothing warns that the input was not anomalies —
#: there is no diagnostic anywhere in the output distinguishing the two runs,
#: so a result computed from raw data is indistinguishable from a correct one
#: except by knowing what was fed in.
_EOF_ANOMALY_NOTE = (
    "The input is assumed to be anomalies — the mean must already have been "
    "removed. CDO does not check and does not warn: run on a raw series it "
    "exits 0 and writes a plausible-looking pair of files whose leading mode is "
    "the climatological mean rather than a mode of variability. Build the "
    "anomalies first: cdo sub infile -timmean infile anom_file, then "
    "cdo eof,40 anom_file eval_file eof_file."
)

#: From the CDO manual's EOFs page, and left as the manual's claim rather than
#: presented as a measurement: this app has not built a time-varying mask to
#: test it against. The workaround is the manual's own.
_EOF_MISSING_NOTE = (
    "Missing values are only supported for masks that do not change over time; "
    "a mask that varies from timestep to timestep is not handled, and the "
    "documented workaround is to replace missing values with 0 in infile first."
)

#: What the two output files are. The eigenvalue file is the surprise: neof
#: sizes outfile2 and nothing else, so a user who asked for 3 EOFs gets a
#: 648-timestep spectrum in the first file.
_EOF_OUTPUT_NOTE = (
    "Two output files, in this order: outfile1 is the eigenvalue spectrum — "
    "*all* of them, one per timestep on a 1x1 grid, since neof sizes outfile2 "
    "only — and outfile2 holds the first neof eigenvectors on the data grid. "
    "outfile1 is a spectrum rather than a map, so drawing it on the map canvas "
    "produces a single degenerate cell; open it in the plot or statistics panel "
    "instead."
)

#: eof only. Measured: on this sample (730 timesteps, 648 gridpoints) ``eof,2``
#: and ``eofspatial,2`` produced byte-identical output — ``cdo diffn`` reported
#: no differing fields for either file — while ``eoftime,2`` took the other
#: path and failed to converge. So ``eof`` is not a third algorithm; it picks
#: whichever of the two is cheaper for the input's shape, and which one it
#: picked is not reported anywhere.
_EOF_DISPATCH_NOTE = (
    "eof chooses the time-space or grid-space algorithm by whichever dimension "
    "is smaller, and does not say which it chose. eoftime and eofspatial force "
    "the choice. Measured on a 730-timestep 648-gridpoint series, eof took the "
    "spatial path and returned exactly what eofspatial returned."
)

#: The failure this section's worst trap is made of. Measured on the anomaly
#: file: every one of the 209628 column pairs failed to reach orthogonality,
#: CDO printed "Setting Matrix and Eigenvalues to 0 before return", exited 0,
#: and wrote an eigenvalue file whose maximum absolute value is 0. Neither
#: CDO_SVD_MODE=danielson_lanczos nor MAX_JACOBI_ITER=200/500 nor
#: FNORM_PRECISION=1e-6 rescued it. Both warnings go to stderr.
_EOF_ZERO_NOTE = (
    "The one-sided jacobi solver can fail to converge and return all zeros: "
    "CDO prints 'Setting Matrix and Eigenvalues to 0 before return', exits 0 "
    "and writes an eigenvalue file that is entirely zero. An all-zero spectrum "
    "means the run failed, whatever the exit code said. This application "
    "reports that warning and treats the run as a failure; see the notices "
    "panel."
)


def _eof_note(operator: str) -> str:
    """What one EOFs-section operator needs said before it is run.

    In the shape of :func:`_conditional_note`: one function, appended once in
    :func:`_describe`, so the toolbar, the Ctrl+K palette and the model builder
    cannot disagree about it.
    """
    if operator in _EOF_OPERATORS:
        notes = [_EOF_ANOMALY_NOTE, _EOF_OUTPUT_NOTE]
        if operator == "eof":
            notes.append(_EOF_DISPATCH_NOTE)
        notes.append(_EOF_ZERO_NOTE)
        notes.append(_EOF_MISSING_NOTE)
        return " ".join(notes)

    if operator in _EOFCOEFF_OPERATORS:
        return (
            "infile1 is the EOF file and infile2 is the anomaly series, not the "
            "other way round. infile1 must be the *second* output of an eof "
            "run — the eigenvectors — and infile2 the same anomalies those EOFs "
            "were computed from. Swapping them is not an error: measured on "
            "2.6.3, the right order wrote one file per eigenvector and the swap "
            "exited 0 and wrote one file per timestep of the series — 3 files "
            "against 730 from the same two inputs. "
            "One output file is written per timestep of infile1, named "
            "<obase><nnnnn> with a five-digit number starting at 00000 "
            "(measured: pc00000.nc, pc00001.nc, pc00002.nc — the CDO "
            "documentation's six-digit obase000000 is wrong on this build), "
            "plus the suffix CDO_FILE_SUFFIX or the output format implies. "
            "The full recipe: cdo sub infile -timmean infile anom; "
            "cdo eof,<neof> anom eval eofs; cdo eofcoeff eofs anom pc. "
            "eofcoeff computes a non-weighted dot product, so CDO_WEIGHT_MODE "
            "must be off — its default — for the eof run as well, or the "
            "coefficients do not match the EOFs they came from."
        )
    return ""


# ---------------------------------------------------------------------------
# Correlation: the shape of the answer, and the two ways it lies
#
# "Correlation over time." is the whole of what the catalog says, and it leaves
# out the only two things that decide whether the result can be read: what shape
# comes back, and what happens when the inputs do not line up.
#
# Everything below was measured on the installed CDO 2.6.3 against two 18x9
# lonlat series of 6 timesteps each, one variable, and cross-checked against
# numpy/scipy where a claim is numerical.
# ---------------------------------------------------------------------------

#: True of all four: the sample is intersected, never padded.
_CORRELATION_MISSING_NOTE = (
    "Only elements where *both* inputs are valid enter the sample, so missing "
    "values in either file shrink it without saying so — a result computed "
    "from three timesteps and one computed from three hundred look identical."
)

#: fldcor/fldcovar. Measured: output grid is 1x1 (points=1), one value per
#: timestep, 6 in and 6 out. The weighting was checked numerically rather than
#: taken from the documentation — against the same two files, CDO's answer
#: matches an area-weighted Pearson r (weights sin(lat_hi)-sin(lat_lo), the
#: lonlat cell areas) to ~1e-3 and differs from the *unweighted* one in the
#: third decimal at every timestep, so "area-weighted" is a measurement here
#: and not a quotation.
_FLD_CORRELATION_NOTE = (
    "Works across the map: one number per timestep, computed over all "
    "gridpoints and weighted by cell area, so the tropics count for more than "
    "the poles. The result is therefore not a map — it is a 1x1 grid with one "
    "value per timestep, a scalar time series. Plotting it on the canvas draws "
    "a single degenerate cell; open it in the plot or statistics panel instead."
)

#: timcor/timcovar. Measured: output is the full input grid (points=162) with
#: exactly 1 timestep, dated at the *last* input timestep. Unweighted, and
#: unweighted by construction rather than by choice — the operator never
#: combines two gridpoints, so there is no area for a weight to apply to.
#: Verified numerically: timcor's field matches numpy's per-gridpoint Pearson r
#: to 3e-8 with no weighting anywhere.
_TIM_CORRELATION_NOTE = (
    "Works down the time axis: one number per gridpoint, computed over every "
    "timestep. No area weighting is involved, since gridpoints are never "
    "combined. The result is a map with exactly one timestep, whatever the "
    "length of the inputs, and it is dated at the last timestep that "
    "contributed to it."
)

#: timcor only, and the condition is measured rather than read.
#:
#: ``cdo -h timcor`` says "If there is only one input field, the p-value
#: (probability value) is also written out". Measured: with a one-variable
#: input the output holds two variables, ``<name>`` and ``pvalue``; with a
#: two-variable input it holds two correlations and no pvalue at all. So "one
#: input field" means one field per timestep, not one input file, and a user
#: cannot predict from their own file whether the extra variable appears.
#:
#: The second half of this note is the part CDO's documentation gets wrong, and
#: it is worth the sentence because acting on the name produces exactly the
#: wrong answer. Measured against scipy on 20 timesteps over 162 gridpoints
#: spanning r = -0.97 to +0.96: the correlation field matches Pearson's r to
#: 5.5e-16, but ``pvalue`` matches ``1 - p_two_sided/2`` — the cumulative
#: t-distribution at |t| — to within 0.009, and does *not* match the two-sided
#: p-value at all (maximum disagreement 1.0, the whole range). It runs from
#: ~0.5 where the correlation is zero up to 1.0 where it is perfect. It is a
#: confidence level. Filtering ``pvalue < 0.05`` for "significant" selects
#: nothing whatsoever, and ``pvalue > 0.95`` is what the user meant.
_TIMCOR_PVALUE_NOTE = (
    "When the input holds a single field per timestep, the output carries a "
    "second variable called 'pvalue' beside the correlation — so the result "
    "has two variables and both are worth looking at. A two-variable input "
    "gets two correlations and no pvalue instead. Despite the name it is not a "
    "p-value: measured against the t-distribution it is the *confidence "
    "level*, running from about 0.5 where the correlation is zero to 1.0 where "
    "it is perfect. High means significant. Selecting 'pvalue < 0.05' returns "
    "nothing; the test you want is 'pvalue > 0.95'."
)


# ---------------------------------------------------------------------------
# Regression: which file is which, and a default that is wrong for this app
#
# The catalog gives these five a title and nothing else — "Trend of time
# series." is the whole of what a user is told about an operator that writes two
# indistinguishable files and takes a parameter that changes the answer.
#
# Everything below was measured on the installed CDO 2.6.3. The sample, unless a
# note says otherwise, is twelve monthly timesteps on an r4x2 grid::
#
#     cdo -f nc const,1,r4x2 t.nc
#     cdo -f nc setreftime,2000-01-01 -settaxis,2000-01-01,1,1mon -for,1,12 base.nc
#     cdo -f nc enlarge,t.nc base.nc in.nc
# ---------------------------------------------------------------------------

#: True of all five, and the reason the section needed touching at all.
#:
#: ``equal=true`` is the default and means "every timestep is one unit of t".
#: On a monthly axis that is false — months are 28 to 31 days — and the answer
#: changes accordingly. Measured on the sample above::
#:
#:     cdo regres in.nc r.nc              -> slope 1.0
#:     cdo regres,equal=false in.nc r.nc  -> slope 1.01672
#:
#: and identically for ``trend``'s outfile2. Monthly, yearly and gappy axes are
#: the normal case in this application's domain, so the operator's own default
#: is the wrong one for most real input here — which is worth saying out loud
#: rather than leaving the user to discover that a field they cannot check
#: silently depends on a box they never saw.
_REGRESSION_EQUAL_NOTE = (
    "The 'equal' parameter defaults to true, which tells CDO to treat every "
    "timestep as one equal unit of time. That is right for daily or hourly "
    "data and wrong for a monthly or yearly axis, or a series with gaps — and "
    "it changes the number, not just the run: measured on twelve monthly "
    "steps, the slope came back 1.0 with the default and 1.01672 with "
    "equal=false. Set it to false whenever the timesteps are not evenly "
    "spaced."
)

#: trend only. Both outputs come back carrying the input's own variable name,
#: the input's own units and one timestep each — measured with ``showname``,
#: ``ntime`` and ``ncdump -h`` on both files, whose headers differ only in the
#: name of the file. Nothing written *into* either file says which is which, so
#: the filenames the run was given are the only surviving record.
#:
#: The unit is worth the clause: the slope is input-units *per timestep*, and
#: CDO copies the input's units across unchanged, so a Kelvin input yields a
#: slope file labelled "K" that is really K/timestep. Measured with
#: ``cdo setattribute,seq@units=K`` on the sample and ``ncdump -h`` on outfile2.
_REGRESSION_TREND_NOTE = (
    "Two output files, and which is which is decided by their order alone: "
    "outfile1 is a, the intercept, and outfile2 is b, the slope — the trend "
    "itself. Both come back holding the input's variable name, the input's "
    "units and a single timestep, and the two files are otherwise identical in "
    "structure, so nothing inside either one records which it is. Name them so "
    "you can tell them apart. The slope's true unit is the input's units per "
    "timestep; CDO copies the input's units over unchanged and does not "
    "relabel it. Asking for only one output file is refused — "
    "cdo trend infile outfile aborts with \"Missing inputs\"."
)

#: regres. One sentence, because the operator is one half of trend.
_REGRESSION_REGRES_NOTE = (
    "This is trend's second output on its own: it estimates b, the slope, and "
    "not a. Use trend when the intercept is wanted too — regres computes "
    "nothing that trend does not."
)

#: addtrend/subtrend. The measurement that matters most in the section, and the
#: same failure class as ``fldcor``'s silent truncation in ``core/pairing.py``.
#:
#: Measured on a 12-step monthly series with a = 100 and b = 3 — deliberately
#: a != b, because on the ``in.nc`` sample above both are 1.0 and a swap is
#: invisible::
#:
#:     cdo trend nd.nc afile bfile
#:     cdo subtrend nd.nc afile bfile ok.nc    -> exit 0; field mean 0 throughout
#:     cdo subtrend nd.nc bfile afile swap.nc  -> exit 0; field mean 97, 0, -97,
#:                                                ... -970
#:     cdo subtrend nd.nc nd.nc nd.nc junk.nc  -> exit 0; field mean 0, -97,
#:                                                ... -1067
_REGRESSION_ARITH_NOTE = (
    "infile2 must be trend's outfile1 (a, the intercept) and infile3 its "
    "outfile2 (b, the slope), in that order: cdo trend infile afile bfile, "
    "then this operator on infile afile bfile. Nothing checks it. Measured on "
    "2.6.3 against a series with a=100 and b=3, feeding the two files the "
    "wrong way round exited 0 and wrote a full, plausible, entirely wrong "
    "field, and so did feeding the raw series into both slots. A wrong "
    "companion here looks exactly like a successful run."
)

#: detrend. The Note the Detrend page carries and no surface repeated, which is
#: the reason both spellings of this computation exist.
#:
#: The equivalence is measured rather than taken from the page: on the sample,
#: ``cdo detrend`` and ``cdo trend`` + ``cdo subtrend`` differed by 0 at every
#: timestep (``cdo sub``, then ``fldmax`` of ``abs``).
_REGRESSION_DETREND_NOTE = (
    "detrend holds every timestep in memory at once. When the series will not "
    "fit, the same computation runs in two low-memory steps instead: "
    "cdo trend infile afile bfile, then cdo subtrend infile afile bfile "
    "outfile. Measured on 2.6.3, the two routes agree to the last bit."
)

#: All five, and the only *documentation* bug in the section.
#:
#: Measured: ``cdo -h addtrend`` and ``cdo -h subtrend`` both print
#: "SYNOPSIS  cdo trend[,equal] infile1 infile2 infile3 outfile", naming
#: ``trend`` — a (1|2) operator — for a pair of (3|1) ones. The Trendarith page
#: of the 2.6.3 reference manual prints the same line. Recorded the way
#: ``_TIMCOR_PVALUE_NOTE`` records CDO's p-value error: the binary and the
#: manual agree with each other and are both wrong, so a user checking one
#: against the other finds nothing.
#:
#: Nothing derives a syntax string from that line — ``operator_syntax`` builds
#: it from ``(nin, nout)`` and ``spec.params`` — so this note exists to stop the
#: *user* acting on it, not the code.
_REGRESSION_SYNOPSIS_NOTE = (
    "Note that CDO's own help is wrong about this operator's name: both "
    "cdo -h addtrend and cdo -h subtrend print the synopsis as "
    "\"cdo trend[,equal] infile1 infile2 infile3 outfile\", and so does the "
    "reference manual's Trendarith page. trend is a different operator taking "
    "one input and writing two outputs."
)


def _regression_note(operator: str) -> str:
    """What one Regression operator's catalog title leaves out.

    Shaped like :func:`_correlation_note` and appended in the same place, so the
    toolbar, the Ctrl+K palette and the model builder cannot disagree about it.

    **Which operators are in scope is the schema's own answer**: the ones that
    declare :data:`_REGRESSION_PARAMS`, tested by object identity the way
    :func:`_resolve_params` relies on an alias sharing its target's parameter
    *object*. The five operators of CDO's Regression section are exactly the
    five that document ``equal : BOOL`` — measured from ``cdo -h`` on each — so
    the parameter is a faithful statement of membership and needs no name list
    beside it.

    Two things it deliberately is not keyed on. Not ``CATEGORY_FOR_OPERATOR``,
    which is built from the curated *menu* list: whether the toolbar shows an
    operator has nothing to do with what its description should say, and keying
    on it would have silently dropped the note from ``regres`` and ``addtrend``
    for as long as they were missing from that list. Not a name tuple here,
    because that would be the second copy of a fact ``_PARAM_SPECS`` already
    holds.

    Within the section the individual notes do name their operators, as
    :func:`_correlation_note` and :func:`_eof_note` do: which of two files is
    which is a fact about one operator, and a fact has to say who it is about.
    """
    if _PARAM_SPECS.get(operator) is not _REGRESSION_PARAMS:
        return ""

    notes = []
    # Which file is which, before anything about how it is computed: for four
    # of the five this is the question that decides whether the answer can be
    # read at all.
    if operator == "trend":
        notes.append(_REGRESSION_TREND_NOTE)
    elif operator == "regres":
        notes.append(_REGRESSION_REGRES_NOTE)
    elif operator in ("addtrend", "subtrend"):
        notes.append(_REGRESSION_ARITH_NOTE)
        notes.append(_REGRESSION_SYNOPSIS_NOTE)
    elif operator == "detrend":
        notes.append(_REGRESSION_DETREND_NOTE)

    # Last, and unconditional: every operator that reached this line declares
    # the parameter, which is what got it here.
    notes.append(_REGRESSION_EQUAL_NOTE)
    return " ".join(notes)


def _correlation_note(operator: str) -> str:
    """What shape one Correlation operator's answer comes back in."""
    if operator in ("fldcor", "fldcovar"):
        return f"{_FLD_CORRELATION_NOTE} {_CORRELATION_MISSING_NOTE}"
    if operator == "timcor":
        return (f"{_TIM_CORRELATION_NOTE} {_TIMCOR_PVALUE_NOTE} "
                f"{_CORRELATION_MISSING_NOTE}")
    if operator == "timcovar":
        return f"{_TIM_CORRELATION_NOTE} {_CORRELATION_MISSING_NOTE}"
    return ""


def _describe(op: str, base: str, nin: int) -> str:
    """One operator's description, plus what the catalog line leaves out.

    ``nin`` is passed in rather than looked up because this runs *during*
    :func:`_build_operator_schema`, before ``OPERATOR_SCHEMA`` exists — and the
    caller already holds the catalog's own number, which is the one this file
    says wins. It used to be read from ``OPERATOR_SIGNATURES``, the
    hand-maintained table that was 227 operators short of the catalog; no
    operator's broadcast note actually changed hands when this moved (checked
    across all fourteen operators in a broadcast module, both directions), so
    this is a change of source and not of behaviour.
    """
    # "--> ymonsub" is how the catalog spells an alias, which is CDO's notation
    # and not a sentence. Two surfaces used to drop such a description entirely
    # and fall back to a generic one for the category, losing the one fact that
    # actually mattered about the operator.
    if base.startswith("-->"):
        base = f"Another name for {base[3:].strip()}."
    # ``cdo --operators`` gives a title, not a sentence, so the notes appended
    # below would otherwise run straight on from it.
    if base and base[-1] not in ".!?":
        base += "."
    notes = [base] if base else []

    # What the answer looks like, before anything about how it is built: a
    # comparison returns a mask and propagates missing, and neither is guessable
    # from "Equal." All twenty-four say it, because all twenty-four do it.
    if op in _COMPARISON_NAMES:
        notes.append(_COMPARISON_MASK_NOTE)

    # Which file is the mask, for the six operators where that is the question.
    # Before the companion/broadcast pair rather than after: those two are about
    # what a *second data file* has to contain, and here the second file is not
    # a data file at all.
    conditional = _conditional_note(op)
    if conditional:
        notes.append(conditional)

    companion = _companion_note(op)
    if companion:
        notes.append(companion)
    # Asked of the module rather than of the category: the question is whether
    # this operator's documentation states the broadcast rule, and Comp's does
    # in the same words as Arith's. Gating on ``category is ARITHMETIC`` — which
    # is what stood here — answered a different question and silently excluded
    # eq/ne/le/lt/ge/gt.
    elif operator_module(op) in _BROADCAST_MODULES and nin == 2:
        notes.append(_BROADCAST_NOTE)

    # The Correlation section: what shape the answer comes back in, which is
    # the one thing none of these four titles says and the thing that decides
    # whether the result can be read at all.
    correlation = _correlation_note(op)
    if correlation:
        notes.append(correlation)

    # The EOFs section: the anomaly precondition, which of the two output files
    # is which, and — for eofcoeff — which of the two *input* files is which.
    # Three of these eight operators have no catalog description at all, so for
    # them this is the entire description.
    eof = _eof_note(op)
    if eof:
        notes.append(eof)

    # The Regression section: which of trend's two outputs is which, which of
    # addtrend/subtrend's three inputs is which, and a default that is wrong for
    # every monthly series this application is pointed at. Beside the EOFs note
    # rather than anywhere else because it answers the same two questions for
    # the same reason — the catalog gives these five a title and nothing more.
    regression = _regression_note(op)
    if regression:
        notes.append(regression)

    if op in _ECA_WHOLE_SERIES:
        notes.append(_ECA_PERIOD_NOTE)
    elif op in _ETCCDI_YEARLY:
        notes.append(_ETCCDI_PERIOD_NOTE)
    if op in _SURPRISING_DEFAULTS:
        notes.append(_SURPRISING_DEFAULTS[op])
    if op in _TWO_VARIABLE_INDICES:
        notes.append(_TWO_VARIABLE_NOTE)
    return " ".join(notes)


def _resolve_params(op: str, desc: str) -> "Tuple[OperatorParam, ...]":
    """``op``'s declared parameters, inherited from its target when it is an alias.

    An alias is CDO's own ``--> target`` notation in the catalog description, so
    which operators are aliases is the binary's answer rather than a list kept
    here.

    The rule is: an operator with its own ``_PARAM_SPECS`` entry uses it, and an
    alias without one inherits its target's *object* — not a copy of it. That is
    what makes the two structurally incapable of disagreeing, which a second
    declaration spelling the same fields is not.

    Both halves of the rule are load-bearing, and were measured across all 32
    aliases in the catalog before it was written:

    * **The alias's own entry wins.** ``selgridname`` is an alias for
      ``selgrid`` and declares its parameter as ``gridnames`` where the target
      calls it ``grids``. Same shape, different label, and the label is the
      better one for the alias — so inheritance must not overwrite a
      declaration that is deliberately there. Fourteen more aliases declare a
      parameter identical to their target's; those are now redundant rather than
      wrong, and are left alone because deleting them is a change to five other
      sections and not this one's to make.
    * **An alias without one inherits.** Before this, exactly one alias in the
      catalog was in that state and it was ``import_grads``, whose target
      ``import_binary`` declared a ``ctlfile`` parameter it should never have
      had. The two therefore disagreed about the operator's own shape — the same
      operator, reachable under two names, asking for a different number of
      files depending on which name was used.

    Measured after the change: no alias's rendered parameters differ from what
    it had before, because the only two that move are ``import_grads`` (whose
    target is now parameterless, as it was itself) and ``outputkey`` (whose
    duplicate declaration this let us delete). See the Import/Export entries in
    ``_PARAM_SPECS``.
    """
    own = _PARAM_SPECS.get(op)
    if own is not None:
        return own
    if desc.startswith("-->"):
        return _PARAM_SPECS.get(desc[3:].strip(), ())
    return ()


def _build_operator_schema() -> Dict[str, OperatorSpec]:
    """Build OPERATOR_SCHEMA from the pinned CDO catalog.

    Signatures and descriptions come from ``cdo --operators`` (via
    ``cdo_operator_catalog.CDO_OPERATORS``), which is the whole of the input.

    The docstring here used to say that ``OPERATOR_SIGNATURES`` was "merged in
    for compatibility" with the catalog winning on conflicts. That had stopped
    being true: this function reads the catalog and nothing else, and it has
    for as long as it has looked like this. The claim survived in prose after it
    stopped being true in code, which is the cost a second copy of the truth
    charges even when it is no longer wired up — so the table it named is gone
    and this paragraph is what is left of it. See the note above
    ``OPERATOR_SIGNATURES``' former home for where its 716 entries went.
    """
    from .cdo_operator_catalog import CDO_OPERATORS

    schema: Dict[str, OperatorSpec] = {}
    for op, (nin, nout, desc) in CDO_OPERATORS.items():
        params = _resolve_params(op, desc)
        schema[op] = OperatorSpec(
            name=op,
            nin=nin,
            nout=nout,
            category=_infer_category(op, nin, nout),
            params=params,
            description=_describe(op, desc, nin),
            inputs=_OPERATOR_INPUTS.get(op) or _companion_inputs(op),
            outputs=_OPERATOR_OUTPUTS.get(op, ()),
            env=_OPERATOR_ENV.get(op, ()),
        )
    return schema


OPERATOR_SCHEMA: Dict[str, OperatorSpec] = _build_operator_schema()


def get_operator_spec(name: str) -> "OperatorSpec | None":
    """Return the canonical spec for ``name`` or ``None`` if unknown."""
    return OPERATOR_SCHEMA.get(name)


# Schema-derived convenience tables.  These replace the hand-maintained
# ``EXTRA_PARAM_COUNTS`` in ``nc_integration.py`` and the ``extra_map`` in
# ``main_window.py``.

def operator_required_param_count(name: str) -> int:
    spec = OPERATOR_SCHEMA.get(name)
    if spec is None:
        return 0
    return sum(1 for p in spec.params if not p.optional)


def operator_total_param_count(name: str) -> int:
    spec = OPERATOR_SCHEMA.get(name)
    if spec is None:
        return 0
    return len(spec.params)


def missing_required_parameters(name: str,
                                supplied: "Sequence[str]") -> "List[str]":
    """Label every required parameter of ``name`` that ``supplied`` leaves blank.

    The one rule for "this call is not ready to run", shared by the three places
    that have to decide it: the model builder's validator, the operator form's
    Execute button, and ``_resolve_operator_call`` in the execution layer, which
    is the last gate before argv and is reached by the batch runner too.

    It has to be one rule because getting it wrong is not a failed command. CDO
    does not abort on a missing comma-parameter — it prompts for it on stdin and
    keeps prompting after EOF, so ``cdo pow ifile ofile`` writes 39 MB of
    "Enter value > " in five seconds against a pipe the application is draining.
    A blank field is a frozen window, not an error message.

    Positional, because a CDO parameter list is: index *i* of ``supplied`` is
    ``spec.params[i]``. Required parameters always precede optional ones in
    ``_PARAM_SPECS`` — asserted by ``test_required_params_come_first`` — so the
    first ``required`` positions are exactly the ones that must be non-blank,
    and a short list means the tail is missing rather than misaligned.

    Returns labels rather than a bool so the caller can name the fields; an
    unknown operator returns nothing, since nothing here knows better than the
    schema what it needs.
    """
    required = operator_required_param_count(name)
    if not required:
        return []
    spec = OPERATOR_SCHEMA.get(name)
    values = list(supplied)
    return [
        (spec.params[index].label or spec.params[index].name) if spec else str(index + 1)
        for index in range(required)
        if index >= len(values) or not str(values[index]).strip()
    ]


def invalid_parameter_values(name: str,
                             supplied: "Sequence[str]") -> "List[str]":
    """Every parameter of ``name`` whose value cannot be what it is declared as.

    ``OperatorParam.kind`` has always been declarative — a widget hint, read by
    the two surfaces that build a form and by nothing that checks a value. So
    ``gtc,abc`` was assembled into argv and handed to CDO, which answered
    "Float parameter >abc< contains invalid character at position 1". That is a
    perfectly good diagnosis in CDO's words about CDO's command line, arriving
    after a subprocess, for a mistake the app could name in its own words about
    the field the user typed into.

    Only ``int``, ``float`` and ``bool`` are *parsed*, because they are the only
    three kinds with a decidable answer. ``string`` covers the key=value
    grammars, the comma-separated lists and the date formats, ``grid`` may be a
    preset or a path, and ``file`` may legitimately not exist yet — guessing at
    any of those would refuse commands that work.

    ``choices`` is a fourth decidable answer, and is now checked rather than
    read only by the widget that renders a ``select`` as a combo box. A declared
    closed set is the schema saying it knows the whole accepted vocabulary, and
    that claim is as checkable on an ``int`` as on a ``string``: ``fourier``'s
    ``epsilon`` is an ``int`` restricted to -1 and 1, and ``cdo -f nc4
    fourier,0`` does not fail — measured on 2.6.3, it exits 0 and writes a file.
    Refusing it here is the only place it is refused.

    Checked for every kind **except ``grid`` and ``file``**, where ``choices``
    is a list of suggestions rather than the accepted vocabulary — the same
    reason those two are not parsed above. See :func:`against_choices` for the
    command that proves it.

    Checked *after* the kind's own parse, so ``fourier,abc`` is reported as "not
    a whole number" rather than as "not one of -1, 1" — the first is what the
    user did wrong. ``multiselect`` keeps its own per-item branch below, since
    its value is a list and each item is checked separately.

    ``bool`` is checked here for the reason the whole function exists: a
    checkbox cannot produce ``"maybe"``, but a saved project, a batch CSV and a
    model file can all hand one over, and without this the value would reach
    argv and CDO would answer "Boolean parameter >maybe< contains invalid
    characters!". The accepted spellings are wider than CDO's own — see
    ``_BOOL_TRUE``/``_BOOL_FALSE`` — because :func:`parameter_tokens`
    normalises them to ``true``/``false`` before they get there.

    The parsers are Python's own, which is what keeps the shapes CDO accepts
    working: ``-5``, ``+5``, ``1e-3`` and ``-2.5E+10`` all pass, and ``5.0``
    fails an ``int`` slot exactly as CDO fails it ("Integer parameter >5.0<
    contains invalid character"). A non-finite float is refused too: ``nan``
    parses, and as a threshold it makes every comparison false and every mask
    missing, which is the silent-wrong-answer case this module exists to stop.

    Positional and blank-tolerant, on the same grounds as
    :func:`missing_required_parameters`: index *i* of ``supplied`` is
    ``spec.params[i]``, a blank in a required slot is that function's business,
    and a blank in an optional one is how a form says "not given". Returns
    labelled complaints rather than a bool so the caller can name the field.
    """
    spec = OPERATOR_SCHEMA.get(name)
    if spec is None:
        return []

    problems: "List[str]" = []
    for index, value in enumerate(supplied):
        if index >= len(spec.params):
            break
        param = spec.params[index]
        text = str(value).strip()
        if not text:
            continue
        label = param.label or param.name

        # The one thing still decidable about a value whose vocabulary is open.
        # CDO splits the operator token on commas to find its key=value pairs,
        # so a comma inside a value ends it and starts what CDO reads as the
        # next parameter: ``colour_min=RGB(1,0,0)`` arrives as three parameters,
        # none of them a colour. Refused here rather than left to CDO because
        # CDO's own complaint would name ``0)``, a token the user never typed.
        # Scoped to ``open_choices`` deliberately — seven keyword parameters in
        # the catalog take a comma-separated value on purpose; see
        # :attr:`OperatorParam.open_choices`.
        if param.open_choices and "," in text:
            # The RGB hint only when it is the mistake being made; on a
            # colour_triad or a style a sentence about RGB would be noise.
            hint = (" Use semicolons in an RGB value: RGB(0.0;0.0;1.0)"
                    if "rgb" in text.lower() else "")
            problems.append(
                f"{label} must not contain a comma — CDO splits the operator "
                f"token on commas, so {text!r} would be read as several "
                f"parameters.{hint}")
            continue

        def against_choices(number=None) -> None:
            """Refuse a value outside a declared closed set. No-op without one.

            ``number`` is the already-parsed value for a numeric kind, compared
            numerically rather than as text so the spellings CDO accepts for the
            same number are not refused on their punctuation: ``+1`` and ``01``
            are ``fourier``'s epsilon 1 however they are typed.

            ``grid`` and ``file`` are exempt, and the exemption is the whole
            reason this is a separate rule rather than a blanket one. For those
            two kinds ``choices`` is an *offered* vocabulary, not the accepted
            one — the three grid parameters list :data:`GRID_PRESETS` so a
            surface can put them in a dropdown, and a CDO grid may equally be a
            descriptor file or any ``rNxM``. Enforcing it there refused
            ``cdo random,r18x9,42``, which is a correct command and one the
            catalog's own tests run. Every other kind with ``choices`` has a
            genuinely closed vocabulary that CDO itself validates.

            ``open_choices`` is the same exemption said declaratively, for the
            parameters where an open vocabulary is a fact about the parameter
            rather than about its kind — Magics' colours, which are 54 names or
            an RGB triple. See :attr:`OperatorParam.open_choices`; the comma
            those values must not contain is refused below rather than here,
            because it is a different question from the vocabulary.
            """
            if not param.choices or param.kind in (_GRID, _FILE):
                return
            if param.open_choices:
                return
            if number is None:
                # Case-insensitively, which is measured rather than lenient.
                # CDO 2.6.3 with MAGICS accepts ``style=dash`` and
                # ``style=DASH`` as byte-identical output, likewise
                # ``colour_triad=cw``/``CW`` — and the manual prints these
                # enums in both cases in different tables, so a case-sensitive
                # check would refuse a spelling the documentation gives.
                #
                # Safe for every other operator too: measured across the whole
                # catalog, no ``choices`` tuple contains two entries differing
                # only in case, so this can never merge two distinct values. It
                # only ever accepts more, which is the direction this file
                # errs in deliberately — see ``reads`` on setpartab.
                matched = text.casefold() in {c.casefold() for c in param.choices}
            else:
                matched = any(
                    _same_number(number, choice) for choice in param.choices)
            if not matched:
                problems.append(
                    f"{label} must be one of {', '.join(param.choices)}, "
                    f"not {text!r}")

        if param.kind not in (_INT, _FLOAT, _BOOL, _MULTISELECT):
            against_choices()
            continue
        # A comma-separated list, every item of which must be one of ``choices``
        # with an optional ``:len``. Checked here rather than left to CDO
        # because one of the two ways to get it wrong does not produce a failed
        # run at all: ``outputtab,value:abc`` dies on SIGABRT with an uncaught
        # std::invalid_argument from stoi, which the async path reports as a
        # crash. See the note above _OUTPUTTAB_KEYNAMES for the measurements.
        if param.kind == _MULTISELECT:
            for item in text.split(","):
                item = item.strip()
                if not item:
                    problems.append(
                        f"{label} has an empty entry — remove the extra comma")
                    continue
                # Only where the operator's grammar has a per-item tail; see
                # :attr:`OperatorParam.item_suffix`. Without it the colon is
                # part of the value, and ``DJF:8`` is refused here exactly as
                # ``cdo selseas,DJF:8`` is refused by the binary.
                if param.item_suffix:
                    keyname, sep, length = item.partition(":")
                else:
                    keyname, sep, length = item, "", ""
                if param.choices and keyname not in param.choices:
                    problems.append(
                        f"{label}: {keyname!r} is not one of "
                        f"{', '.join(param.choices)}")
                    continue
                # stoi's own contract: it needs a leading integer and ignores
                # whatever follows it, so this refuses exactly the spellings
                # measured to abort and accepts the ones measured to work.
                if sep and not re.match(r"^[+-]?\d", length.strip()):
                    problems.append(
                        f"{label}: {item!r} needs a number after the colon "
                        f"(the field width), e.g. {keyname}:8")
            continue
        if param.kind == _BOOL:
            if parameter_bool(text) is None:
                accepted = ", ".join(sorted(_BOOL_TRUE | _BOOL_FALSE))
                problems.append(
                    f"{label} must be one of {accepted}, not {text!r}")
            continue
        if param.kind == _INT:
            try:
                whole = int(text)
            except ValueError:
                problems.append(f"{label} must be a whole number, not {text!r}")
                continue
            against_choices(whole)
            continue
        try:
            number = float(text)
        except ValueError:
            problems.append(f"{label} must be a number, not {text!r}")
            continue
        if number != number or number in (float("inf"), float("-inf")):
            problems.append(f"{label} must be a finite number, not {text!r}")
            continue
        against_choices(number)
    return problems


#: The CDO module whose operators take their field data on standard input.
#: One module, and naming it is the whole rule — see :func:`reads_stdin`.
_STDIN_MODULE = "Formatted input"

#: The operators that read a *namelist* — not field data — on standard input,
#: and so cannot be placed by module the way the three above are. Both names of
#: the ECHAM afterburner are listed: ``afterburner`` is an alias of ``after``,
#: and while aliases inherit parameters in ``_build_operator_schema`` they do
#: not inherit membership of a name set, so leaving it out would give the two
#: spellings of one operator different surfaces.
_STDIN_NAMELIST_OPERATORS: "frozenset[str]" = frozenset({"after", "afterburner"})


def reads_stdin(name: str) -> bool:
    """True when ``name`` reads its field data from standard input.

    Three operators do: ``input``, ``inputsrv`` and ``inputext``. They are not
    listed here — the answer is CDO's own, taken from the module title, for the
    reason ``_MODULE_CATEGORY`` exists: a list of three names in this file is a
    fourth place the same fact is written and the one most likely to be missed
    when a build adds a fifth format.

    It matters because these three are the only operators in the catalog whose
    *data* is not named on the command line, and the execution layer has to
    treat them differently twice over. Without a file to pipe in they must still
    get an immediate EOF rather than an inherited terminal — measured on 2.6.3,
    ``cdo input,r4x2 out.nc`` with stdin at ``/dev/null`` aborts in milliseconds
    with "Too few input elements (0 of 8)!", and with stdin left attached it
    waits for a human that a GUI has no way to provide. With a file, its
    contents are what the operator reads: ``cdo input,r4x2 back.nc < dump.txt``
    round-trips a field written by ``cdo output`` exactly.

    Deliberately *not* the same question as "does a missing parameter make CDO
    hang", which is a different failure with a different fix. ``cdo outputtab``
    with no keynames spins forever even with stdin at ``/dev/null`` — it is not
    waiting on input, and only :func:`missing_required_parameters` stops it.

    ``after`` is the one operator that cannot be answered by the module, and it
    is named in :data:`_STDIN_NAMELIST_OPERATORS` instead. What it reads on
    stdin is a *namelist* rather than field data — "This operator reads
    selection parameters as namelist from stdin", and the manual's own idiom is
    the shell redirect ``cdo after infile outfile < namelist``. The mechanism
    the three formatted-input operators need is exactly the mechanism it needs,
    so it gets the same one: ``stdin_path`` on the execution layer, a file row
    on both surfaces, and no new dialog.

    Measured on 2.6.3, because the alternative was to declare it unsupported:
    with stdin at ``/dev/null`` ``cdo after in.nc out.nc`` does **not** hang —
    it prints "Default namelist: TYPE=0, CODE=-1, …" and runs with those
    defaults. So the operator was never going to freeze the app; it was simply
    impossible to steer, since nothing offered a way to supply the namelist.
    That is what this fixes, and it is why the answer here is to widen the
    function rather than to hide the operator.
    """
    return (operator_module(name) == _STDIN_MODULE
            or name in _STDIN_NAMELIST_OPERATORS)


def file_parameter_indexes(name: str) -> "Tuple[int, ...]":
    """The positions of ``name``'s parameters that carry a file path.

    Driven off ``kind`` rather than off a list of operator names, because the
    list would be a second copy of ``_PARAM_SPECS`` that drifts from it: there
    are twenty-nine such parameters across twenty-six operators today, and the
    only thing they have in common is what the schema already says about them.

    Used by the execution layer to give a file-valued parameter the same
    treatment an input file gets. ``grid`` is deliberately not included: its
    value may be a CDO preset (``t63grid``) rather than a path, and there is no
    way to tell one from the other without guessing.
    """
    spec = OPERATOR_SCHEMA.get(name)
    if spec is None:
        return ()
    return tuple(index for index, param in enumerate(spec.params)
                 if param.kind == _FILE)


def output_parameter_indexes(name: str) -> "Tuple[int, ...]":
    """The positions of ``name``'s parameters that are outputs, not inputs.

    The counterpart to :func:`file_parameter_indexes`, and split off it for the
    same reason that function is driven off ``kind``: the two operators
    concerned — ``tee`` and ``writeremapscrip`` — are not a list worth keeping
    anywhere but here, where the schema already says it.

    An operator whose real second output is declared as an ordinary parameter
    is the one case where ``(nin, nout)`` understates what a run touches, and
    everything the execution layer does about outputs has to follow this rather
    than ``nout``. See :attr:`OperatorParam.writes`.
    """
    spec = OPERATOR_SCHEMA.get(name)
    if spec is None:
        return ()
    return tuple(index for index, param in enumerate(spec.params)
                 if param.kind == _FILE and param.writes)


def missing_parameter_files(name: str,
                            supplied: "Sequence[str]") -> "List[str]":
    """Every ``file`` parameter of ``name`` that names something not on disk.

    The third of the three checks between a parameter list and argv, beside
    :func:`missing_required_parameters` and :func:`invalid_parameter_values`,
    and it exists for the same reason they do: the error is better here than it
    is from CDO. ``reducegrid`` handed a mask path with a typo in it gets as far
    as opening the file before it fails, and says so in terms of the file rather
    than of the field the user typed it into.

    Only parameters marked ``reads`` are checked. ``tee``'s parameter is the
    file it writes, ``writeremapscrip``'s second one is the SCRIP file it
    writes, and the ``setpartab*`` family may take a table name rather than a
    path — requiring any of those to exist would refuse correct commands. See
    :class:`OperatorParam` for why each is marked the way it is.

    Blank-tolerant and positional, on the same grounds as the other two: a blank
    in a required slot is ``missing_required_parameters``' business, and a blank
    in an optional one is how a form says "not given".
    """
    spec = OPERATOR_SCHEMA.get(name)
    if spec is None:
        return []

    from pathlib import Path

    problems: "List[str]" = []
    for index, value in enumerate(supplied):
        if index >= len(spec.params):
            break
        param = spec.params[index]
        if param.kind != _FILE or not param.reads:
            continue
        text = str(value).strip()
        if not text:
            continue
        if not Path(text).expanduser().is_file():
            label = param.label or param.name
            problems.append(f"{label}: no such file, {text!r}")
    return problems


def parameter_bool(value: str) -> "Optional[bool]":
    """Read one ``bool`` parameter's value, or ``None`` if it is not one.

    Blank is ``None`` rather than ``False``: for every ``bool`` parameter in the
    schema, "not given" and "given as false" produce the same command — CDO's
    default is off — but they are different answers to "did the user say
    anything", and only the renderer should collapse them.
    """
    text = str(value).strip().lower()
    if not text:
        return None
    if text in _BOOL_TRUE:
        return True
    if text in _BOOL_FALSE:
        return False
    return None


def _same_number(value: float, choice: str) -> bool:
    """Whether a parsed numeric parameter equals one spelling of a choice.

    Used only by :func:`invalid_parameter_values`, so that a closed set written
    as text — ``choices=("-1", "1")`` — can be compared against a number the
    user typed in whatever form CDO would also have read. A choice that is not a
    number at all is False rather than an error: a numeric parameter with a
    non-numeric choice is a schema mistake, and refusing every value because of
    it would be the worse failure of the two.
    """
    try:
        return float(choice) == float(value)
    except (TypeError, ValueError):
        return False


#: Parameters whose being switched on turns the operator's single output path
#: into a numbered *prefix*. One entry, and the table exists rather than a
#: hard-coded ``"map3d"`` so the next one is a line rather than a branch.
_NUMBERED_OUTPUT_PARAMS = frozenset({"map3d"})


def writes_output_prefix(name: str,
                         supplied: "Sequence[str]" = ()) -> bool:
    """Whether this *call*'s output argument is a base CDO appends to.

    The one question the execution layer has to answer before it can alias, snapshot,
    relocate or clean up an output: is the trailing path a file CDO writes as
    given, or a stem it decorates? Three different things make the answer yes,
    and they are worth keeping distinct because only one of them is derivable
    from the signature:

    * **``nout == -1``** — the split/distribute family, ``intyear``, and the two
      Ensval operators. Static, and the only half the execution layer originally
      knew about.
    * **A parameter value.** ``genbil``, ``genbic``, ``gencon``, ``genlaf``,
      ``gennn``, ``gendis`` and their aliases have ``nout == 1``, so the whole
      execution layer treated the output as one file it could name. With
      ``map3d=true`` CDO instead uses that path as a prefix and appends a
      five-digit counter. Measured on 2.6.3::

          cdo genbil,r36x18,map3d=true lev5.nc w1.nc   -> wrote w1.nc00001.nc
          cdo genbil,r36x18,map3d=true lev5.nc pfx     -> wrote pfx00001.nc

      — the given path is kept verbatim, extension included, and the counter
      goes after it. Against a source whose missing-value mask differs per level
      it wrote one file per distinct mask, ``w1.nc00001.nc`` ..
      ``w1.nc00003.nc``, so the count is genuinely a runtime fact and not a
      second static number.
    * **The operator itself.** The six Magics operators are ``nout == 1`` and
      their trailing argument is an *obase* in CDO's own synopsis. Nothing
      about the signature or the parameters says so; see
      :data:`OBASE_OPERATORS`.

    Every consequence of getting this wrong was silent. ``_prepare_output_target``
    aliased a single path, ``_materialise_output_aliases`` then looked for a
    file that was never created, and ``_existing_output_paths`` /
    ``_discard_partial_outputs`` globbed the wrong shape, so a failed run left
    the mapfiles on disk while reporting it had cleaned up. The run itself exits
    0 and the app reported success with nothing at the path it named — which is
    what a user sees as "it wrote no file at all".

    Deliberately *not* a refusal of ``map3d=true``: it is a working feature that
    produces real files, and refusing it would deny the only way to generate
    per-mask weights. Making the arity derivable is what fixes all four call
    sites at once, since each already handles a prefix correctly when told it
    has one.

    Renamed from ``writes_numbered_outputs``. The old name described the two
    cases that existed when it was written and stopped being true when the
    Magics six were declared — a Magplot obase gains ``_<variable>.<device>``,
    which is not a number and not a series. This file has already recorded once
    what it costs to leave a name asserting something the code no longer does
    (see :func:`_build_operator_schema`'s docstring), so the name moved with the
    behaviour rather than after it.

    ``supplied`` is positional by declaration order, the same invariant
    :func:`parameter_tokens` documents, so a blank or absent value reads as off.
    """
    spec = OPERATOR_SCHEMA.get(name)
    if spec is None:
        return False
    if spec.nout == -1:
        return True
    if name in OBASE_OPERATORS:
        return True
    for index, param in enumerate(spec.params):
        if param.name not in _NUMBERED_OUTPUT_PARAMS:
            continue
        if index < len(supplied) and parameter_bool(str(supplied[index])):
            return True
    return False


def parameter_tokens(name: str,
                     supplied: "Sequence[str]") -> "List[str]":
    """The pieces of ``name``'s operator token, each already in CDO's spelling.

    ``cdo <op>,<a>,<b>`` is built by joining what this returns with commas. It
    is the one place that knows the difference between the three grammars in
    :attr:`OperatorParam.form`, so the execution layer does not have to and the
    surfaces can show the same token they are about to run.

    **The indexing invariant, which the other three checkers depend on:**
    ``supplied`` is positional *by declaration order* — index *i* is
    ``spec.params[i]`` — whatever form the parameters have. A form decides how a
    value is rendered, never where it sits in the caller's list. That is what
    keeps :func:`missing_required_parameters`, :func:`invalid_parameter_values`
    and :func:`missing_parameter_files` correct with no change: all three index
    the same way and all three keep their positional-and-blank-tolerant
    docstrings honestly.

    What each form emits:

    * ``positional`` – the value verbatim, blanks included. A blank in the
      middle of a positional list is preserved because dropping it would
      silently promote the next value into its slot.
    * ``keyword``    – ``name=value``, or nothing at all when blank. This is why
      an unset optional keyword needs no placeholder: it is simply absent, and
      the ones after it do not shift.
    * ``flag``       – the bare ``name`` when the value reads as true, nothing
      otherwise.

    A ``bool`` is normalised to ``true``/``false`` on the way out, so a surface
    may hand over ``yes`` or ``on`` and still produce a token CDO parses — it
    rejects both itself.

    Values past the end of the declared parameters are passed through verbatim.
    That is what keeps ``_invoke_legacy_operator``'s single combined ``"a,b"``
    string working, and an operator whose parameters are not declared at all
    behaves exactly as it did before this function existed.
    """
    spec = OPERATOR_SCHEMA.get(name)
    values = [str(value) for value in supplied]
    if spec is None or not spec.params:
        return values

    tokens: "List[str]" = []
    for index, value in enumerate(values):
        if index >= len(spec.params):
            tokens.append(value)
            continue
        param = spec.params[index]
        text = value.strip()

        if param.form == _FLAG:
            if parameter_bool(text):
                tokens.append(param.name)
            continue

        if param.form == _KEYWORD:
            if not text:
                continue
            if param.kind == _BOOL:
                flag = parameter_bool(text)
                if flag is None:
                    # Unreadable, and invalid_parameter_values has already said
                    # so on every path that reaches argv. Passed through rather
                    # than guessed at, so CDO's own complaint names the value.
                    tokens.append(f"{param.name}={text}")
                else:
                    tokens.append(f"{param.name}={'true' if flag else 'false'}")
            else:
                tokens.append(f"{param.name}={text}")
            continue

        tokens.append(value)

    return tokens


def operator_syntax(name: str) -> str:
    """The ``ifile ofile ...`` usage hint for one operator.

    Derived from the schema rather than written out per operator: the file part
    follows from ``(nin, nout)``, which comes from ``cdo --operators``, and the
    trailing parameters are the same ``spec.params`` the parameter form builds
    its fields from.  A hand-maintained table covered 386 of 943 operators and
    disagreed with the installed binary on eight of them — ``copy`` and the
    ``info``/``sinfo`` family take any number of inputs, ``eca_gsl`` takes two —
    which is the kind of drift a derived string cannot have.

    An unknown operator gets the generic one-in/one-out form; that is what the
    old table's fallback returned and it stays the least surprising guess.
    """
    spec = OPERATOR_SCHEMA.get(name)
    if spec is None:
        return "ifile ofile"

    if spec.nin == -1:
        inputs = "ifiles"
    elif spec.nin == 0:
        inputs = ""
    elif spec.nin == 1:
        inputs = "ifile"
    else:
        inputs = " ".join(f"ifile{n}" for n in range(1, spec.nin + 1))

    # ``nout == -1`` is the split/distribute family: one base path that CDO
    # suffixes per output file, not a file it writes as given.
    #
    # ``OBASE_OPERATORS`` is the same shape reached the other way: the Magics
    # six are ``nout == 1`` and their trailing argument is still a base. The
    # signature cannot express it, so the set is consulted before the count.
    # Getting this line wrong is not cosmetic — the usage hint is what tells a
    # user whether to type "plot" or "plot.ps", and CDO creates nothing at the
    # literal path either way.
    if spec.nout == -1 or name in OBASE_OPERATORS:
        outputs = "obase"
    elif spec.nout == 0:
        outputs = ""
    elif spec.nout == 1:
        outputs = "ofile"
    else:
        outputs = " ".join(f"ofile{n}" for n in range(1, spec.nout + 1))

    parts = [part for part in (inputs, outputs) if part]

    # Spelled the way the parameter is actually written, not just named: a
    # usage line reading "bitrounding ifile ofile [,inflevel]" tells a user the
    # wrong thing, because ``bitrounding,0.999`` is a parse error on 2.6.3 and
    # ``bitrounding,inflevel=0.999`` is the call. See ``OperatorParam.form``.
    def spelled(param: OperatorParam) -> str:
        if param.form == _FLAG:
            return param.name
        if param.form == _KEYWORD:
            return f"{param.name}=<{param.kind}>" if param.kind != _BOOL \
                else f"{param.name}=true"
        return param.name

    required = ",".join(spelled(p) for p in spec.params if not p.optional)
    optional = "".join(f"[,{spelled(p)}]" for p in spec.params if p.optional)
    if required or optional:
        parts.append(required + optional)

    return " ".join(parts)


def menu_operators(
    category: "NCExplorerCategory",
    available: "Optional[Container[str]]" = None,
) -> "Tuple[List[str], List[str]]":
    """Split one category's operators into ``(curated, rest)`` for its menu.

    ``curated`` is the hand-picked ``OPERATOR_CATEGORIES`` list, and being in it
    is what earns an operator a place near the top of its menu — the toolbar
    shows the first ten of ``curated + rest`` directly.  ``rest`` is every other
    operator the schema files under the same category, which is what the command
    palette and the model builder have always offered and the menus did not: 561
    operators reachable by search but not by browsing.

    ``available`` is the installed CDO's operator names, or None to trust the
    schema alone.  Filtering against it is what keeps a menu from listing an
    operator this build cannot run, the way the palette and the builder already
    do.  Both lists come back sorted.
    """
    def installed(name: str) -> bool:
        return available is None or name in available

    curated = sorted(
        name for name in OPERATOR_CATEGORIES.get(category, ()) if installed(name)
    )
    seen = set(curated)
    rest = sorted(
        name
        for name, spec in OPERATOR_SCHEMA.items()
        if spec.category is category and name not in seen and installed(name)
    )
    return curated, rest
