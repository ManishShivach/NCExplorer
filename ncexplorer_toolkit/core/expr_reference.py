"""The expression language ``expr``/``aexpr``/``exprf``/``aexprf`` accept.

Transcribed from the CDO Expr module documentation (2.6.3) and checked against
the installed 2.6.0 binary. It lives in ``core`` rather than beside the dialog
because it is data: the editor renders it, and the tests read it without needing
a display.

The whole language used to be one ``QLineEdit`` with the placeholder
"e.g. tas=var*2". Seventeen operators, about seventy intrinsic functions, an
``_ALL_`` template and ``_``-prefixed temporaries were undiscoverable from
inside the application, and a typo was only found after a full run against real
data.

Grouped exactly as the documentation groups them, so somebody reading both is
reading one list in two places.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class ExprEntry:
    """One insertable item: what to type, and what it does."""

    #: The token as it should appear in the expression — ``min(x,y)``, ``+``.
    signature: str
    #: One line. The documentation's own wording, trimmed.
    summary: str
    #: What clicking it inserts. Defaults to the signature; functions insert
    #: with the caret usefully placed by the editor, not by this.
    insert: str = ""

    @property
    def text(self) -> str:
        return self.insert or self.signature

    @property
    def name(self) -> str:
        """The bare function name, for matching what the user has typed."""
        return self.signature.split("(")[0].strip()


@dataclass(frozen=True)
class ExprGroup:
    title: str
    entries: Tuple[ExprEntry, ...]


def _g(title: str, *pairs: Tuple[str, str]) -> ExprGroup:
    return ExprGroup(title, tuple(ExprEntry(sig, text) for sig, text in pairs))


#: The seventeen operators, including the ternary. Kept apart from the functions
#: because they are infix and inserting them mid-expression is a different move.
EXPR_OPERATORS: ExprGroup = _g(
    "Operators",
    ("=", "assignment — assigns y to x"),
    ("+", "addition"),
    ("-", "subtraction"),
    ("*", "multiplication"),
    ("/", "division"),
    ("exp", "exponentiation — x exp y raises x to the power y"),
    ("==", "equal to — 1 if x equals y, else 0"),
    ("!=", "not equal to"),
    (">", "greater than"),
    ("<", "less than"),
    (">=", "greater or equal"),
    ("<=", "less or equal"),
    ("<=>", "less/equal/greater — -1 if x<y, 1 if x>y, else 0"),
    ("and", "logical AND — 1 if x and y are both non-zero"),
    ("or", "logical OR"),
    ("!", "logical NOT — 1 if x equals 0"),
    ("?:", "ternary conditional — x ? y : z gives y when x is non-zero, else z"),
)


EXPR_FUNCTIONS: Tuple[ExprGroup, ...] = (
    _g("Math intrinsics",
       ("abs(x)", "absolute value"),
       ("floor(x)", "largest integral value not greater than x"),
       ("ceil(x)", "smallest integral value not less than x"),
       ("float(x)", "32-bit float value of x"),
       ("int(x)", "integer value of x"),
       ("nint(x)", "nearest integer value of x"),
       ("sqr(x)", "square of x"),
       ("sqrt(x)", "square root of x"),
       ("exp(x)", "exponential of x"),
       ("ln(x)", "natural logarithm"),
       ("log10(x)", "base 10 logarithm"),
       ("sin(x)", "sine, x in radians"),
       ("cos(x)", "cosine, x in radians"),
       ("tan(x)", "tangent, x in radians"),
       ("asin(x)", "arc-sine"),
       ("acos(x)", "arc-cosine"),
       ("atan(x)", "arc-tangent"),
       ("sinh(x)", "hyperbolic sine"),
       ("cosh(x)", "hyperbolic cosine"),
       ("tanh(x)", "hyperbolic tangent"),
       ("asinh(x)", "inverse hyperbolic sine"),
       ("acosh(x)", "inverse hyperbolic cosine"),
       ("atanh(x)", "inverse hyperbolic tangent"),
       ("rad(x)", "convert x from degrees to radians"),
       ("deg(x)", "convert x from radians to degrees"),
       ("rand(x)", "replace x by pseudo-random numbers in 0…1"),
       ("isMissval(x)", "1 where x is missing"),
       ("mod(x,y)", "floating-point remainder of x/y"),
       ("min(x,y)", "minimum of x and y"),
       ("max(x,y)", "maximum of x and y"),
       ("pow(x,y)", "power function"),
       ("hypot(x,y)", "Euclidean distance, sqrt(x*x + y*y)"),
       ("atan2(x,y)", "arc tangent of y/x, using signs to pick the quadrant"),
       ("trimrel(x,kb)", "trim relative precision to kb keep-bits"),
       ("trimabs(x,err)", "trim absolute precision to a maximum error of err"),
       ),
    _g("Coordinates",
       ("clon(x)", "longitude of x — needs geographical coordinates"),
       ("clat(x)", "latitude of x — needs geographical coordinates"),
       ("gridarea(x)", "grid cell area of x — needs geographical coordinates"),
       ("gridindex(x)", "grid cell indices of x"),
       ("clev(x)", "level coordinate (0 if x is a 2D surface variable)"),
       ("clevidx(x)", "level index (0 if x is a 2D surface variable)"),
       ("cthickness(x)", "layer thickness, upper minus lower level bound"),
       ("ctimestep()", "timestep number, 1 to N"),
       ("cdate()", "verification date as YYYYMMDD"),
       ("ctime()", "verification time as HHMMSS.millisecond"),
       ("cdeltat()", "seconds between this timestep and the last"),
       ("cday()", "day as DD"),
       ("cmonth()", "month as MM"),
       ("cyear()", "year as YYYY"),
       ("csecond()", "second as SS.millisecond"),
       ("cminute()", "minute as MM"),
       ),
    _g("Constants",
       ("ngp(x)", "number of horizontal grid points"),
       ("nlev(x)", "number of vertical levels"),
       ("size(x)", "total number of elements, ngp(x)*nlev(x)"),
       ("missval(x)", "the missing value of variable x"),
       ),
    _g("Statistics over a field",
       *((f"fld{stat}(x)", f"field {stat} of x") for stat in (
           "min", "max", "range", "sum", "mean", "avg", "std", "std1",
           "var", "var1", "skew", "kurt", "median"))),
    _g("Zonal statistics (regular 2D grids)",
       *((f"zon{stat}(x)", f"zonal {stat} of x") for stat in (
           "min", "max", "range", "sum", "mean", "avg", "std", "std1",
           "var", "var1", "skew", "kurt", "median"))),
    _g("Vertical statistics",
       *((f"vert{stat}(x)", f"vertical {stat} of x") for stat in (
           "min", "max", "range", "sum", "mean", "avg", "std", "std1",
           "var", "var1"))),
    _g("Miscellaneous",
       ("sellevel(x,k)", "select level k of variable x"),
       ("sellevidx(x,k)", "select level index k of variable x"),
       ("sellevelrange(x,k1,k2)", "select all levels of x in the range k1…k2"),
       ("sellevidxrange(x,k1,k2)", "select all level indices in the range k1…k2"),
       ("remove(x)", "remove variable x from the output stream"),
       ),
)


#: The two facts that surprise people, both measured on CDO 2.6.0.
EXPR_NOTES: Tuple[str, ...] = (
    "Every statement ends in a semicolon, and there is usually more than one.",
    "_ALL_ is a template: a statement using it is repeated for every variable "
    "in the input.",
    "A variable whose name starts with an underscore (_tmp) is temporary — it "
    "is computed and never written to the output.",
    "expr and exprf REPLACE the input variables; aexpr and aexprf APPEND to "
    "them. That is the whole difference between the two pairs, and it is easy "
    "to pick wrong.",
    "A variable created here carries no units. CDO copies no unit attribute "
    "onto it, so the result of tempC = tas - 273.15 is unlabelled — and addc "
    "is worse, because it keeps the units the input had: cdo addc,-273.15 on a "
    "Kelvin field produces Celsius still labelled K. Add a setattribute or "
    "setunit step afterwards.",
)


#: The operators this editor is for, and whether each one appends or replaces.
EXPR_OPERATOR_NAMES = ("expr", "aexpr", "exprf", "aexprf")

#: The two that read the same language out of a file rather than an argument.
EXPR_FILE_OPERATORS = ("exprf", "aexprf")

#: The two that keep the input variables.
EXPR_APPENDING_OPERATORS = ("aexpr", "aexprf")


def is_expression_operator(name: str) -> bool:
    return name in EXPR_OPERATOR_NAMES


def appends(name: str) -> bool:
    """True when ``name`` keeps the input variables and adds to them."""
    return name in EXPR_APPENDING_OPERATORS


def reads_from_file(name: str) -> bool:
    """True when ``name``'s parameter is a script path rather than the script."""
    return name in EXPR_FILE_OPERATORS


def all_entries() -> Tuple[ExprEntry, ...]:
    """Every insertable item, operators first, in the documentation's order."""
    entries = list(EXPR_OPERATORS.entries)
    for group in EXPR_FUNCTIONS:
        entries.extend(group.entries)
    return tuple(entries)
