# Copyright (c) 2026 Manish Shivach
# SPDX-License-Identifier: MIT
"""Where each operator can be picked from in the app.

An operator can pass its CDO run and still be unreachable — filed under a
category whose menu never lists it, or missing from the model builder's
palette. Those are separate defects from "the command failed", and the report
carries a column for each, so this module walks the *real* widgets: the actual
``QMenu`` tree the toolbar builds, the actual ``build_entries`` the command
palette searches, the actual ``OperatorCatalog`` the model builder offers. A
restatement of the code that builds them would agree with itself by
construction and prove nothing.

Qt is imported lazily and every failure is survivable: with no display, no
PyQt6 or no CDO, the surfaces come back ``None`` (unknown) rather than ``False``
(absent), because "we could not look" and "it is not there" must not appear
the same in a report.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Set, Tuple

from ncexplorer_toolkit.core.categories import OPERATOR_CATEGORIES, OPERATOR_SCHEMA

logger = logging.getLogger(__name__)

#: The QApplication this module had to create, if it created one. Held at module
#: scope because a QApplication that only a local name refers to is collected
#: the moment the expression ends, and the next QWidget then aborts the process
#: with "Must construct a QApplication before a QWidget".
_OWNED_APP = None


def configure_headless() -> None:
    """Make Qt loadable with no display, exactly as ``tests/conftest.py`` does.

    A conda environment reports its own Qt5 plugin path, which Qt6 cannot load
    and which aborts ``QApplication`` with "Could not find the Qt platform
    plugin". Pointing at PyQt6's bundled plugins is the fix in both places.
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if "QT_QPA_PLATFORM_PLUGIN_PATH" in os.environ:
        return
    try:
        import PyQt6
    except ImportError:
        return

    bundled = Path(PyQt6.__file__).resolve().parent / "Qt6" / "plugins"
    if (bundled / "platforms").is_dir():
        os.environ["QT_PLUGIN_PATH"] = str(bundled)
        os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = str(bundled / "platforms")


@dataclass(frozen=True)
class OperatorSurfaces:
    """Which of the app's operator surfaces offer one operator.

    ``None`` on any field means the surface could not be inspected in this
    process — no Qt, no display, no resolved CDO — and is reported as unknown.
    """

    installed: Optional[bool] = None
    in_schema: bool = False
    toolbar: Optional[bool] = None
    toolbar_category: str = ""
    toolbar_placement: str = ""
    palette: Optional[bool] = None
    model_builder: Optional[bool] = None

    @property
    def reachable(self) -> bool:
        """True only when a user can get at this operator from every surface."""
        return bool(self.toolbar and self.palette and self.model_builder)


#: ``main_window`` labels its widget kinds differently from the schema. Mapped
#: back rather than compared as-is, so a real disagreement is not hidden behind
#: a vocabulary difference and a vocabulary difference is not reported as one.
_UI_KIND_TO_SCHEMA = {
    "integer": "int",
    "float": "float",
    "string": "string",
    "checkbox": "bool",
    "paramfile": "file",
    "paramgrid": "grid",
    "select": "select",
    "multiselect": "multiselect",
    "expression": "expression",
}

#: One parameter as the audit compares it: enough to catch a surface offering a
#: different field, a differently-typed field or a differently-ordered form.
ParameterShape = Tuple[str, str, str, bool]  # (name, kind, label, optional)


@dataclass(frozen=True)
class SurfaceScan:
    """One walk of every surface, plus the raw sets that produced it."""

    installed: Dict[str, Tuple[int, int]]
    menus: Dict[str, Tuple[object, int]]
    palette: Set[str]
    builder: Set[str]
    per_operator: Dict[str, OperatorSurfaces]
    errors: Dict[str, str]
    #: ``surface -> operator -> the parameter fields that surface would draw``.
    #: Empty when the surfaces could not be inspected.
    parameters: Dict[str, Dict[str, Tuple[ParameterShape, ...]]] = None  # type: ignore[assignment]
    #: ``surface -> operator -> (nin, nout)`` as that surface believes it.
    #: Empty when the surfaces could not be inspected.
    arity: Dict[str, Dict[str, Tuple[int, int]]] = None  # type: ignore[assignment]

    def get(self, operator: str) -> OperatorSurfaces:
        return self.per_operator.get(operator, OperatorSurfaces(
            in_schema=operator in OPERATOR_SCHEMA))

    @property
    def names(self) -> Set[str]:
        return set(self.installed) | set(self.menus) | self.palette | self.builder

    @property
    def disagreements(self) -> list:
        """Operators that some surfaces offer and others do not."""
        return sorted(
            name for name in self.names
            if not (name in self.installed and name in self.menus
                    and name in self.palette and name in self.builder)
        )

    @property
    def parameter_disagreements(self) -> Dict[str, Dict[str, Tuple[ParameterShape, ...]]]:
        """``operator -> {surface: fields}`` wherever the surfaces differ.

        Reachability was never the only thing three surfaces could disagree
        about. ``main_window`` carried its own parameter map for 129 operators
        beside the schema the model builder reads, and it had drifted — it
        offered ``eca_rx1day`` a ``mode`` CDO 2.6 rejects outright. Nothing
        noticed, because nothing compared them. This is that comparison.
        """
        if not self.parameters:
            return {}
        surfaces = sorted(self.parameters)
        operators: Set[str] = set()
        for table in self.parameters.values():
            operators |= set(table)

        differing: Dict[str, Dict[str, Tuple[ParameterShape, ...]]] = {}
        for name in sorted(operators):
            seen = {surface: self.parameters[surface].get(name, ())
                    for surface in surfaces}
            if len(set(seen.values())) > 1:
                differing[name] = seen
        return differing

    @property
    def arity_disagreements(self) -> Dict[str, Dict[str, Tuple[int, int]]]:
        """``operator -> {surface: (nin, nout)}`` wherever the surfaces differ.

        The third thing surfaces can disagree about, and the most expensive of
        the three: a surface that is wrong about how many *files* an operator
        takes does not draw an odd form, it builds a command CDO refuses.

        This exists because that happened. Three places in ``main_window`` read
        a hand-maintained signature table with a ``(1, 1)`` default while the
        model builder and the batch runner read ``OPERATOR_SCHEMA``. The table
        was 227 operators short, so 38 two-input operators got one input row
        here and two ports there — ``cdo timcor in.nc out.nc``, "Missing
        inputs" — and nothing compared the two answers. All twelve
        ``ymon``/``yseas`` comparison operators and all four of the Correlation
        section were unrunnable from the operator panel.

        The installed binary is deliberately *not* one of the surfaces compared
        here. The catalog in this repository is pinned to one CDO release and
        the binary on the machine may be another, so schema-against-binary drift
        is a real and separately tracked fact; this property answers the
        narrower question the app can actually be held to — whether the app
        agrees with itself.
        """
        if not self.arity:
            return {}
        surfaces = sorted(self.arity)
        operators: Set[str] = set()
        for table in self.arity.values():
            operators |= set(table)

        differing: Dict[str, Dict[str, Tuple[int, int]]] = {}
        for name in sorted(operators):
            seen = {surface: self.arity[surface].get(name) for surface in surfaces}
            if len(set(seen.values())) > 1:
                differing[name] = seen
        return differing


def _walk_menu(menu, depth: int = 0) -> Dict[str, int]:
    """``{operator: shallowest depth}`` for everything reachable from ``menu``.

    Depth 0 is a direct click; deeper means it sits behind the "All …" submenu
    and, in the larger categories, behind an alphabetical chunk under that.

    The shallowest wins because an operator can legitimately appear twice: the
    ten a category opens on are repeated inside its complete "All …" listing,
    and what the scan reports is how far the user has to go, not how many
    places the name occurs.
    """
    found: Dict[str, int] = {}
    for action in menu.actions():
        if action.isSeparator():
            continue
        submenu = action.menu()
        if submenu is not None:
            for name, found_depth in _walk_menu(submenu, depth + 1).items():
                found[name] = min(found.get(name, found_depth), found_depth)
        else:
            # ``data()`` is the operator's name; ``text()`` is its caption, and
            # the two stopped being the same thing when the menus began
            # labelling an operator this CDO cannot run as "shaded  (needs
            # MAGICS support)". Reading the caption reported those as six
            # unknown operators absent from every other surface — a
            # disagreement invented by this scan rather than found by it.
            #
            # Falls back to the caption so a menu built elsewhere, or an older
            # one that sets no data, still scans exactly as before.
            name = action.data() or action.text()
            found[name] = min(found.get(name, depth), depth)
    return found


def _schema_shapes(operator: str) -> Tuple[ParameterShape, ...]:
    """What ``core/categories`` declares, which is what the schema surface is."""
    spec = OPERATOR_SCHEMA.get(operator)
    if spec is None:
        return ()
    return tuple((p.name, p.kind, p.label, p.optional) for p in spec.params)


def _form_shapes(operator: str, form_class) -> Tuple[ParameterShape, ...]:
    """What the toolbar's and the palette's shared parameter form would draw.

    Both surfaces open ``main_window.show_operator_parameters``, which builds
    its fields from this one call — so this *is* their parameter list, read
    from the real function rather than restated.
    """
    shapes = []
    for entry in form_class.get_extra_parameters_for_operator(operator):
        name, ui_kind, label = entry[0], entry[1], entry[2]
        kind = _UI_KIND_TO_SCHEMA.get(ui_kind, ui_kind)
        # The form has no notion of optional; take it from the same spec the
        # form's own placeholder came from, so only real differences show up.
        spec = OPERATOR_SCHEMA.get(operator)
        declared = {p.name: p.optional for p in spec.params} if spec else {}
        shapes.append((name, kind, label, declared.get(name, False)))
    return tuple(shapes)


def _builder_shapes(operator: str, catalog) -> Tuple[ParameterShape, ...]:
    """What the model builder's inspector would draw, via its own catalog."""
    spec = catalog.spec(operator)
    if spec is None:
        return ()
    return tuple((p.name, p.kind, p.label, p.optional) for p in spec.params)


def _scan_parameters(names, catalog, form_class):
    """The parameter fields each surface would offer, per operator."""
    return {
        "schema": {name: _schema_shapes(name) for name in names},
        "toolbar/palette form": {
            name: _form_shapes(name, form_class) for name in names
        },
        "model builder": {
            name: _builder_shapes(name, catalog) for name in names
        },
    }


def _form_input_rows(operator: str, form_class) -> Tuple[int, int]:
    """``(nin, nout)`` as the operator form's own row-building would have it.

    Counted from the rows :meth:`parse_parameters` actually emits rather than
    asked of the helper beside it, because the rows are the observable thing: a
    form that believes ``timcor`` takes one file draws one "Input File" row, and
    that row is what the user cannot supply a second file through. Reading the
    arity helper directly would compare the fix against itself and pass whatever
    the rows did.

    ``-1`` in either position is recovered from the shape the parser uses for
    it: three input rows (one required, two optional) for a variable-input
    operator, and an "Output Prefix" string row instead of an "Output File" for
    a split operator.
    """
    rows = form_class.parse_parameters(operator, "")
    labels = [row[1] for row in rows]

    inputs = [label for label in labels if "Input File" in label]
    # The parser's own spelling of nin == -1: exactly the three rows it emits,
    # the last two marked optional in their placeholder.
    if len(inputs) == 3 and any("optional" in str(row[2]).lower()
                                for row in rows if row[1] in inputs):
        nin = -1
    else:
        nin = len(inputs)

    if any("Output Prefix" in label for label in labels):
        nout = -1
    else:
        # An output row is a *file* row whose caption starts with "Output" —
        # not one whose caption is literally "Output File".
        #
        # The rows are still what is counted, which is this function's whole
        # argument; what changed is that the count no longer depends on one
        # exact spelling. Multi-output operators now caption their rows from the
        # schema — "Output 1: Eigenvalues — the whole spectrum" — because
        # "Output File 1" and "Output File 2" tell a user nothing about which of
        # them is the 1x1 spectrum and which is the stack of maps. Matching the
        # old literal counted those as zero and reported all seven (n|2)
        # operators as disagreeing with the schema they had just been built
        # from.
        #
        # ``row[0] == "file"`` is what keeps this honest: the environment rows
        # the EOFs section adds are select/int/float/string rows captioned
        # "Env: …", and a caption-only rule would be one careless label away
        # from counting them.
        nout = sum(1 for row in rows
                   if row[0] == "file" and str(row[1]).startswith("Output"))
    return nin, nout


def _scan_arity(names, catalog, form_class):
    """``(nin, nout)`` per surface, per operator.

    Three surfaces, because three of them decide independently how many files
    an operator takes: the schema (the intended answer), the operator form
    behind the toolbar and the command palette, and the model builder's
    catalog, which is what the graph wires ports from.
    """
    schema = {}
    form = {}
    builder = {}
    for name in names:
        spec = OPERATOR_SCHEMA.get(name)
        if spec is not None:
            schema[name] = (spec.nin, spec.nout)
        try:
            form[name] = _form_input_rows(name, form_class)
        except Exception:                                       # pragma: no cover
            logger.debug("Could not read form rows for %s", name, exc_info=True)
        signature = catalog.signature(name)
        if signature is not None:
            builder[name] = tuple(signature)
    return {
        "schema": schema,
        "toolbar/palette form": form,
        "model builder": builder,
    }


def scan(integration=None) -> SurfaceScan:
    """Walk all three surfaces and the installed catalog.

    ``integration`` is an ``NCExplorerIntegration`` or None. Passing one is
    strongly preferred: all three surfaces filter against the installed binary,
    so without it they answer from the static schema and the scan says less
    than it appears to.
    """
    errors: Dict[str, str] = {}

    installed: Dict[str, Tuple[int, int]] = {}
    if integration is not None:
        try:
            installed = integration.get_operator_signatures()
        except Exception as exc:  # a broken CDO must not stop the scan
            errors["installed"] = str(exc)
            logger.debug("Installed catalog unavailable", exc_info=True)

    menus: Dict[str, Tuple[object, int]] = {}
    palette: Set[str] = set()
    builder: Set[str] = set()
    parameters: Dict[str, Dict[str, Tuple[ParameterShape, ...]]] = {}
    arity: Dict[str, Dict[str, Tuple[int, int]]] = {}

    configure_headless()
    try:
        global _OWNED_APP
        from PyQt6.QtWidgets import QApplication, QMainWindow

        from ncexplorer_toolkit.core.model import OperatorCatalog
        from ncexplorer_toolkit.gui.command_palette import build_entries
        from ncexplorer_toolkit.gui.main_window import NCExplorerOperatorGUI
        from ncexplorer_toolkit.gui.toolbar import NCExplorerToolbar

        if QApplication.instance() is None:
            _OWNED_APP = QApplication([])

        # QToolBar needs a real widget parent, and it reads ``NCExplorer`` off
        # that parent for the runtime catalog — so the host has to be a window
        # rather than a plain stand-in object.
        class Host(QMainWindow):
            NCExplorer = integration

        toolbar = NCExplorerToolbar(Host())
        for category, menu in toolbar.category_menus.items():
            for name, depth in _walk_menu(menu).items():
                menus[name] = (category, depth)

        palette = {entry.name for entry in build_entries(integration)}
        catalog = OperatorCatalog.from_integration(integration)
        builder = set(catalog.names())
        scanned = sorted(set(installed) | set(menus) | palette | builder)
        parameters = _scan_parameters(scanned, catalog, NCExplorerOperatorGUI)
        arity = _scan_arity(scanned, catalog, NCExplorerOperatorGUI)
    except Exception as exc:
        errors["surfaces"] = str(exc)
        logger.warning("Could not inspect the operator surfaces: %s", exc)
        logger.debug("Surface inspection failed", exc_info=True)

    curated = {op for ops in OPERATOR_CATEGORIES.values() for op in ops}
    inspected = "surfaces" not in errors

    per_operator: Dict[str, OperatorSurfaces] = {}
    for name in set(installed) | set(menus) | palette | builder | set(OPERATOR_SCHEMA):
        category, depth = menus.get(name, (None, None))
        if depth is None:
            placement = ""
        elif depth > 0:
            placement = "All …"
        else:
            placement = "top (curated)" if name in curated else "top"

        per_operator[name] = OperatorSurfaces(
            installed=(name in installed) if installed else None,
            in_schema=name in OPERATOR_SCHEMA,
            toolbar=(name in menus) if inspected else None,
            toolbar_category=getattr(category, "value", "") or "",
            toolbar_placement=placement,
            palette=(name in palette) if inspected else None,
            model_builder=(name in builder) if inspected else None,
        )

    return SurfaceScan(
        installed=installed, menus=menus, palette=palette, builder=builder,
        per_operator=per_operator, errors=errors, parameters=parameters,
        arity=arity,
    )
