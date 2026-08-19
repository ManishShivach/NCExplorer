# Copyright (c) 2026 Manish Shivach
# SPDX-License-Identifier: MIT
"""Fuzzy operator search: a centred palette opened with Ctrl+K.

Fourteen category menus were a workable way to reach a few dozen operators. CDO
2.6.0 publishes 943, and finding one means remembering which category somebody
filed it under. This is the other way in: type part of a name, or part of what
the operator *does*, and pick from a ranked list.

Three things the design turns on:

* **Descriptions are searched as well as names.** "average" has to surface
  ``timmean`` and ``fldmean`` even though the word appears in neither name. That
  is also why a name match always outranks a description match — otherwise the
  operator you typed the name of gets buried under everything that mentions it.
* **The index is built once.** Re-deriving 943 entries per keystroke is the
  obvious way to make a palette feel sluggish, so :func:`build_entries` runs at
  construction and the per-keystroke work is one pass of integer comparisons
  over an already-lowercased index.
* **The installed CDO wins.** Not every operator in the static catalog exists in
  every build, so the runtime list from ``cdo --operators`` is preferred and the
  catalog is the fallback — and supplies the longer descriptions where the
  runtime one is blank.

The palette never runs anything itself: accepting a row calls
``show_operator_parameters``, the same entry point the toolbar menus use, so the
form that opens is identical either way.
"""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass

from PyQt6.QtCore import QEvent, QObject, QSettings, Qt
from PyQt6.QtGui import QAbstractTextDocumentLayout, QPalette, QTextDocument
from PyQt6.QtWidgets import (
    QApplication, QDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QStyle, QStyledItemDelegate, QStyleOptionViewItem, QVBoxLayout
)

from ..core.categories import NCExplorerCategory, get_operator_spec
from ..resources.icons import category_icon

logger = logging.getLogger(__name__)

#: Rows kept after ranking. Nobody scrolls past a couple of hundred results, and
#: capping keeps the list widget's own work bounded on a one-letter query.
MAX_RESULTS = 200

#: Operators remembered for the empty-query list.
MAX_RECENT = 8

SETTINGS_KEY = "commandPalette/recentOperators"

DESCRIPTION_LIMIT = 78

# Match quality, best first. Only the ordering matters.
RANK_EXACT = 0
RANK_PREFIX = 1
RANK_NAME = 2
RANK_DESCRIPTION = 3


@dataclass(frozen=True, slots=True)
class PaletteEntry:
    """One searchable operator.

    ``lower_name`` and ``lower_description`` are stored rather than computed
    because they are what every keystroke actually compares against.

    ``description`` is the one-line catalog title, which is what a result row
    has room for and what ranking should match on. ``help_text`` is the schema's
    full description — the title plus everything the title leaves out, such as
    which file a ``*arith`` operator's second input has to be — and is what the
    row's tooltip shows. Kept apart so a paragraph of caveats cannot dominate
    the ranking of every operator that carries one.
    """

    name: str
    description: str
    nin: int
    nout: int
    category: NCExplorerCategory
    lower_name: str
    lower_description: str
    help_text: str = ""

    #: Why this build cannot run the operator, from
    #: ``NCExplorerIntegration.capability_gap``: a short label for the row
    #: ("needs MAGICS support") and the full sentence for the tooltip. Empty for
    #: the operators this build can run, and empty whenever the capability could
    #: not be established — the index does not invent a gap it did not measure.
    #:
    #: Carried on the entry rather than asked per surface because two surfaces
    #: are built from this index — the command palette and the model builder's
    #: operator tree — and asking twice is how they would come to disagree.
    unavailable: str = ""
    unavailable_detail: str = ""

    @property
    def signature(self) -> str:
        """``1 → 1``, with CDO's -1 spelled as the ``n`` it means."""
        return f"{_arity(self.nin)} → {_arity(self.nout)}"


@dataclass(frozen=True, slots=True)
class PaletteMatch:
    """One entry that matched, with everything needed to order and draw it."""

    entry: PaletteEntry
    rank: int
    tiebreak: tuple
    positions: tuple[int, ...] = ()

    def sort_key(self) -> tuple:
        return (self.rank, *self.tiebreak)


def _arity(value: int) -> str:
    return "n" if value == -1 else str(value)


def _category_for(name: str) -> NCExplorerCategory:
    """Category of one operator, defaulting to Miscellaneous.

    The schema covers everything in the static catalog; an operator that exists
    only in the installed binary still needs an icon, and Miscellaneous is what
    the schema's own inference falls back to anyway.
    """
    spec = get_operator_spec(name)
    return spec.category if spec is not None else NCExplorerCategory.MISCELLANEOUS


def build_entries(integration=None) -> list[PaletteEntry]:
    """The searchable operator index, runtime catalog first.

    ``integration`` is an :class:`~..core.nc_integration.NCExplorerIntegration`
    or None. Its catalog is preferred so the palette cannot offer an operator
    this CDO build does not have; the static catalog fills in both the missing
    case and any description ``cdo --operators`` left blank.
    """
    from ..core.categories import OPERATOR_SCHEMA
    from ..core.nc_operator_catalog import CDO_OPERATORS

    runtime: dict[str, tuple[int, int]] = {}
    runtime_descriptions: dict[str, str] = {}
    if integration is not None:
        try:
            for name, meta in integration.get_operator_catalog().items():
                runtime[name] = meta["signature"]
                runtime_descriptions[name] = meta.get("description", "")
        except Exception:
            logger.debug("Runtime operator catalog unavailable; using the static one",
                         exc_info=True)

    names = runtime or {name: (nin, nout) for name, (nin, nout, _) in CDO_OPERATORS.items()}

    entries = []
    for name in sorted(names):
        nin, nout = names[name]
        catalogued = CDO_OPERATORS.get(name)
        description = (
            runtime_descriptions.get(name, "")
            or (catalogued[2] if catalogued else "")
        )
        spec = OPERATOR_SCHEMA.get(name)
        gap = _capability_gap(integration, name)
        entries.append(PaletteEntry(
            name=name,
            description=description,
            nin=nin,
            nout=nout,
            category=_category_for(name),
            lower_name=name.lower(),
            lower_description=description.lower(),
            help_text=(spec.description if spec is not None else "") or description,
            unavailable=gap[0] if gap else "",
            unavailable_detail=gap[1] if gap else "",
        ))

    gated = sum(1 for entry in entries if entry.unavailable)
    logger.debug("Command palette indexed %d operators (%s catalog), "
                 "%d gated by a missing build feature",
                 len(entries), "runtime" if runtime else "static", gated)
    return entries


def _capability_gap(integration, operator: str) -> tuple[str, str] | None:
    """``(summary, detail)`` when this build cannot run ``operator``, else None.

    A wrapper around the integration's own answer rather than a rule of its own,
    and it swallows everything: this runs once per operator while an index of
    900-odd is built, and a build-feature probe that fails is not a reason for
    the palette to have no entries at all. The cost is one dict lookup after the
    first call — ``build_capability`` probes the binary once and caches.
    """
    if integration is None:
        return None
    try:
        return integration.capability_gap(operator)
    except Exception:                                           # pragma: no cover
        logger.debug("Capability gap unavailable for %s", operator, exc_info=True)
        return None


def subsequence_positions(needle: str, haystack: str) -> tuple[int, ...] | None:
    """Indices at which ``needle``'s characters appear in order, or None.

    Greedy and leftmost, which is what makes "tmn" find ``timmean``: exactness
    is not the point, the ranking is what separates a good match from a lucky
    one.
    """
    positions = []
    start = 0
    for character in needle:
        found = haystack.find(character, start)
        if found < 0:
            return None
        positions.append(found)
        start = found + 1
    return tuple(positions)


def match_entry(entry: PaletteEntry, compact: str, tokens: list[str]) -> PaletteMatch | None:
    """Score one entry against a query, or None if it does not match.

    ``compact`` is the query with whitespace removed (used against the name);
    ``tokens`` is the query split into words (used against the description, so
    "monthly mean" matches a description containing both words in any order).
    """
    name = entry.lower_name

    if name == compact:
        return PaletteMatch(entry, RANK_EXACT, (0, 0, len(name), name))
    if name.startswith(compact):
        return PaletteMatch(entry, RANK_PREFIX, (0, 0, len(name), name),
                            tuple(range(len(compact))))

    positions = subsequence_positions(compact, name)
    if positions is not None:
        # A tight match near the front of a short name beats a scattered one:
        # "mean" should reach fldmean before remapdis_mean_of_whatever.
        span = positions[-1] - positions[0]
        return PaletteMatch(entry, RANK_NAME, (span, positions[0], len(name), name), positions)

    if tokens and all(token in entry.lower_description for token in tokens):
        return PaletteMatch(entry, RANK_DESCRIPTION, (0, 0, len(name), name))

    return None


def rank_entries(entries: list[PaletteEntry], query: str,
                 limit: int = MAX_RESULTS) -> list[PaletteMatch]:
    """The best ``limit`` matches for ``query``, best first."""
    cleaned = query.strip().lower()
    if not cleaned:
        return []

    compact = "".join(cleaned.split())
    tokens = cleaned.split()

    matches = []
    for entry in entries:
        found = match_entry(entry, compact, tokens)
        if found is not None:
            matches.append(found)

    matches.sort(key=PaletteMatch.sort_key)
    return matches[:limit]


class RecentOperators:
    """The operators most recently chosen from the palette, newest first.

    Stored through ``QSettings``, which flattens a one-element string list back
    into a bare string on the INI backend — hence the shape check in
    :meth:`names`, the same one ``gui/recent_files.py`` documents.
    """

    def __init__(self, settings: QSettings | None = None):
        self._settings = settings if settings is not None else QSettings()

    def names(self) -> list[str]:
        raw = self._settings.value(SETTINGS_KEY, [])
        if raw is None:
            return []
        if isinstance(raw, str):
            return [raw] if raw else []
        return [str(item) for item in raw if item]

    def add(self, operator: str) -> list[str]:
        remaining = [name for name in self.names() if name != operator]
        updated = [operator] + remaining
        del updated[MAX_RECENT:]
        self._settings.setValue(SETTINGS_KEY, updated)
        return updated


class _RowDelegate(QStyledItemDelegate):
    """Draws each row as rich text, so matched characters can be emphasised.

    The item still carries its category icon the ordinary way; only the text is
    taken over, and it is drawn into the rect the style itself reserved for it,
    so icon spacing and selection highlighting stay exactly as Qt would do them.
    """

    def paint(self, painter, option, index):
        options = QStyleOptionViewItem(option)
        self.initStyleOption(options, index)

        style = options.widget.style() if options.widget else QApplication.style()
        document = QTextDocument()
        document.setDefaultFont(options.font)
        document.setHtml(options.text)

        options.text = ""
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, options, painter, options.widget)

        text_rect = style.subElementRect(
            QStyle.SubElement.SE_ItemViewItemText, options, options.widget
        )
        context = QAbstractTextDocumentLayout.PaintContext()
        context.palette = options.palette
        if options.state & QStyle.StateFlag.State_Selected:
            # Without this the text keeps its ordinary colour and disappears
            # into the selection bar.
            context.palette.setColor(
                QPalette.ColorRole.Text,
                options.palette.color(QPalette.ColorRole.HighlightedText),
            )

        painter.save()
        painter.translate(text_rect.topLeft())
        painter.setClipRect(text_rect.translated(-text_rect.topLeft()))
        document.documentLayout().draw(painter, context)
        painter.restore()

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        # The HTML is one line; the plain-text hint is close enough, but a
        # couple of pixels of breathing room stops descenders being clipped.
        size.setHeight(size.height() + 4)
        return size


class CommandPalette(QDialog):
    """Modal operator finder. Type to filter, Up/Down to move, Enter to open."""

    def __init__(self, main_window):
        super().__init__(main_window)
        self.main_window = main_window
        self.recent = RecentOperators()

        self.setWindowTitle("Find Operator")
        self.setModal(True)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setMinimumWidth(620)

        self._entries = build_entries(getattr(main_window, "NCExplorer", None))
        self._by_name = {entry.name: entry for entry in self._entries}
        self._shown: list[PaletteEntry] = []

        self._build_ui()
        self._show_recent()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search operators by name or description…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._on_query_changed)
        self.search.returnPressed.connect(self._accept_current)
        # Up/Down have to reach the list while the field keeps the caret, and an
        # event filter is the only placement that is not at the mercy of which
        # widget happens to have focus.
        self.search.installEventFilter(self)
        layout.addWidget(self.search)

        self.results = QListWidget()
        self.results.setItemDelegate(_RowDelegate(self.results))
        self.results.setUniformItemSizes(True)
        # Clicking a row accepts it outright, as a palette is expected to.
        # ``itemActivated`` is deliberately not also connected: a double click
        # emits both, and the operator form would be opened twice.
        self.results.itemClicked.connect(lambda _item: self._accept_current())
        layout.addWidget(self.results)

        self.hint = QLabel("↑↓ to move · Enter to open · Esc to close")
        self.hint.setStyleSheet("color: gray; font-size: 10px;")
        layout.addWidget(self.hint)

        self.search.setFocus()

    def showEvent(self, event):
        """Centre on the main window every time, not just the first."""
        super().showEvent(event)
        parent = self.parentWidget()
        if parent is not None:
            geometry = self.frameGeometry()
            geometry.moveCenter(parent.frameGeometry().center())
            # Sit a little above centre: a list that grows downwards otherwise
            # ends up hanging off the bottom of a short window.
            geometry.moveTop(max(parent.frameGeometry().top() + 40, geometry.top() - 80))
            self.move(geometry.topLeft())

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------
    def _on_query_changed(self, text: str) -> None:
        if not text.strip():
            self._show_recent()
            return

        matches = rank_entries(self._entries, text)
        self.hint.setText(
            f"{len(matches)} match{'' if len(matches) == 1 else 'es'} · "
            "↑↓ to move · Enter to open · Esc to close"
        )
        self._populate([(match.entry, match.positions) for match in matches])

    def _show_recent(self) -> None:
        """With no query, offer what was picked last — or nothing at all."""
        names = [name for name in self.recent.names() if name in self._by_name]
        self.hint.setText(
            "Recently used · type to search all operators"
            if names else
            "Type to search all operators by name or description"
        )
        self._populate([(self._by_name[name], ()) for name in names])

    def _populate(self, rows: list[tuple[PaletteEntry, tuple[int, ...]]]) -> None:
        self.results.clear()
        self._shown = [entry for entry, _ in rows]

        for entry, positions in rows:
            item = QListWidgetItem(_row_html(entry, positions))
            try:
                item.setIcon(category_icon(entry.category))
            except KeyError:
                logger.debug("No icon for category %s", entry.category)
            tooltip = f"{entry.name} ({entry.signature})\n{entry.help_text}"
            if entry.unavailable_detail:
                tooltip = (f"{entry.name} ({entry.signature})\n"
                           f"{entry.unavailable_detail}\n\n{entry.help_text}")
            item.setToolTip(tooltip)
            self.results.addItem(item)

        if self._shown:
            self.results.setCurrentRow(0)

    # ------------------------------------------------------------------
    # Keyboard
    # ------------------------------------------------------------------
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.search and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                self._step(1 if key == Qt.Key.Key_Down else -1)
                return True
            if key in (Qt.Key.Key_PageDown, Qt.Key.Key_PageUp):
                self._step(10 if key == Qt.Key.Key_PageDown else -10)
                return True
        return super().eventFilter(watched, event)

    def _step(self, offset: int) -> None:
        count = self.results.count()
        if count == 0:
            return
        row = max(0, min(count - 1, self.results.currentRow() + offset))
        self.results.setCurrentRow(row)

    def _accept_current(self) -> None:
        """Open the highlighted operator's parameter form and close.

        An operator this build cannot run explains itself in the hint line and
        the palette stays open, rather than opening a form whose Run would
        refuse. The row is deliberately still selectable — a modal search box
        where the arrow keys skip rows for reasons the eye cannot predict is
        worse than one where Enter declines and says why — so this is where that
        decision is enforced.
        """
        row = self.results.currentRow()
        if not (0 <= row < len(self._shown)):
            return

        entry = self._shown[row]
        if entry.unavailable_detail:
            self.hint.setText(f"{entry.name} — {entry.unavailable_detail}")
            logger.info("Command palette refused %s: %s",
                        entry.name, entry.unavailable)
            return

        operator = entry.name
        self.recent.add(operator)
        self.accept()
        logger.info("Command palette opened operator %s", operator)
        self.main_window.show_operator_parameters(operator)


def _row_html(entry: PaletteEntry, positions: tuple[int, ...]) -> str:
    """One result row: emphasised name, then category, signature, description.

    An operator this build cannot run keeps its row and its ranking, and is
    drawn grey with the reason in place of the description. Grey rather than
    absent for the reason given in ``toolbar._add_operator_actions``: a missing
    operator reads as a defect in this application, a greyed one names the
    build. The reason takes the description's place rather than following it
    because the description is what gets truncated at ``DESCRIPTION_LIMIT``, and
    the one line that must survive is the one explaining why the row is grey.
    """
    highlighted = set(positions)
    emphasis = "b" if not entry.unavailable else "i"
    name = "".join(
        f"<{emphasis}>{html.escape(character)}</{emphasis}>"
        if index in highlighted else html.escape(character)
        for index, character in enumerate(entry.name)
    )

    description = entry.description
    if len(description) > DESCRIPTION_LIMIT:
        description = description[:DESCRIPTION_LIMIT - 1].rstrip() + "…"

    detail = html.escape(f"{entry.category.value} · {entry.signature}")
    if entry.unavailable:
        detail += " · " + html.escape(entry.unavailable)
    elif description:
        detail += " · " + html.escape(description)

    if entry.unavailable:
        # The whole row greys, name included — the distinction the eye needs
        # here is runnable from not, not name from detail.
        return f"<span style='color:gray;'>{name} &nbsp;{detail}</span>"
    return f"{name} &nbsp;<span style='color:gray;'>{detail}</span>"
