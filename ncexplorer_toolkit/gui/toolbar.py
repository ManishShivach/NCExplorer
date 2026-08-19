# Copyright (c) 2026 Manish Shivach
# SPDX-License-Identifier: MIT
import logging
from typing import NamedTuple

from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import (
    QCompleter, QLineEdit, QMenu, QStyledItemDelegate, QToolBar, QToolButton
)

from ..core.categories import NCExplorerCategory, menu_operators, operator_module
from ..resources.icons import category_icon
from .command_palette import build_entries, rank_entries

logger = logging.getLogger(__name__)


class _CapabilityGap(NamedTuple):
    """Why one operator cannot run on the installed CDO, in two lengths.

    ``summary`` goes beside the operator name in the menu, so it has to survive
    being read at a glance: "needs MAGICS support". ``detail`` is the full
    sentence from the integration, shown on hover, which names the check to run
    and says the fix is a different binary rather than a different command.
    """

    summary: str
    detail: str


#: Where a search suggestion carries its build-feature summary. A role of its
#: own because ``QStandardItem`` has no slot that would hold it safely: setText
#: and setData(EditRole) are **the same storage** — Qt maps EditRole onto
#: DisplayRole inside QStandardItem — so a label with the reason appended is
#: also the string the completer hands back on activation, and ``_choose`` would
#: receive "shaded  (needs MAGICS support)" as an operator name. Measured, not
#: assumed: setting the two separately and reading them back returns one value.
_GAP_ROLE = int(Qt.ItemDataRole.UserRole) + 1


class _SuggestionDelegate(QStyledItemDelegate):
    """Draws a suggestion's build-feature reason without storing it on the item.

    ``initStyleOption`` is the whole trick: it edits the text *about to be
    painted*, so the row reads "shaded  (needs MAGICS support)" while the model
    still holds "shaded" and the completer still returns "shaded". Ten lines
    here in place of an identity contract that depends on nobody ever appending
    to a caption — see :data:`_GAP_ROLE`.
    """

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        reason = index.data(_GAP_ROLE)
        if reason:
            option.text = f"{option.text}  ({reason})"


#: Operators a category menu shows directly. Ten is about what can be read
#: without the eye scanning; everything past it lives behind "All …", which is
#: one extra click for the long tail and none for the common case.
TOP_LEVEL_LIMIT = 10

#: Above this many operators, the "All …" submenu is split into alphabetical
#: chunks. Four categories are over it — Miscellaneous alone holds 202 — and a
#: single list that long is a menu with scroll arrows rather than a menu.
CHUNK_THRESHOLD = 40

#: Operators per alphabetical chunk. Chunks follow initial letters, so they come
#: out uneven; this is the target, not a cap.
CHUNK_SIZE = 30

#: Width of the toolbar's operator search box. Wide enough for the longest
#: operator name, narrow enough to leave the fourteen category buttons room.
SEARCH_WIDTH = 190


class OperatorSearchBox(QLineEdit):
    """Type-to-find over the whole operator catalog, inline in the toolbar.

    The ranking is the command palette's own, so "tmn" reaches ``timmean`` here
    exactly as it does under Ctrl+K — this is that index shown inline, not a
    second search with rules of its own. What it does with a chosen operator is
    also the same: open the parameter form.

    The index is built on the first keystroke rather than at construction. It
    walks the installed catalog, and a toolbar must not pay for that before
    anyone has typed.
    """

    #: Emitted with an operator name when the user picks one.
    operator_chosen = pyqtSignal(str)

    #: Rows offered under the box. A completer popup is not a result list; past
    #: a dozen the user is better served by typing another letter.
    MAX_SUGGESTIONS = 12

    def __init__(self, host=None, parent=None):
        super().__init__(parent)
        self._host = host
        self._entries = None
        #: ``{operator: detail}`` for what this build cannot run, filled with
        #: the index because it comes off the same entries.
        self._gaps: dict = {}

        self.setPlaceholderText("Search operators…")
        self.setClearButtonEnabled(True)
        self.setToolTip(
            "Find any CDO operator by name or by what it does.\n"
            "Ctrl+K opens the full palette."
        )
        self.setFixedWidth(SEARCH_WIDTH)

        self._model = QStandardItemModel(self)
        self._completer = QCompleter(self._model, self)
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        # The rows below are already chosen and ordered by rank_entries. Qt's
        # own prefix filtering on top of that would throw away every fuzzy
        # match, which is most of why the search is worth having.
        self._completer.setCompletionMode(
            QCompleter.CompletionMode.UnfilteredPopupCompletion
        )
        self.setCompleter(self._completer)
        # After setCompleter: the popup is created lazily and asking for it
        # earlier builds one the completer then replaces.
        self._completer.popup().setItemDelegate(_SuggestionDelegate(self._completer))

        self._completer.activated.connect(self._choose)
        self.textEdited.connect(self._refresh_suggestions)
        self.returnPressed.connect(self._accept_typed)

    def index(self):
        """The searchable operator index, built once on first use."""
        if self._entries is None:
            self._entries = build_entries(getattr(self._host, "NCExplorer", None))
            self._gaps = {entry.name: entry.unavailable_detail
                          for entry in self._entries if entry.unavailable_detail}
            logger.debug("Toolbar search indexed %d operators (%d gated by a "
                         "missing build feature)",
                         len(self._entries), len(self._gaps))
        return self._entries

    def _refresh_suggestions(self, text):
        """Rebuild the popup rows for the current query."""
        matches = rank_entries(self.index(), text, limit=self.MAX_SUGGESTIONS)

        self._model.clear()
        for match in matches:
            entry = match.entry
            item = QStandardItem(entry.name)
            item.setEditable(False)
            item.setToolTip(f"{entry.name} ({entry.signature})\n{entry.description}")
            if entry.unavailable:
                # The reason goes in a role the delegate paints and nothing
                # else reads, so the item's text stays the operator's name and
                # the completer keeps handing ``_choose`` something it can look
                # up. See _GAP_ROLE for why the obvious setText is wrong.
                item.setData(entry.unavailable, _GAP_ROLE)
                item.setToolTip(f"{entry.name} ({entry.signature})\n"
                                f"{entry.unavailable_detail}")
                item.setEnabled(False)
            try:
                item.setIcon(category_icon(entry.category))
            except KeyError:
                logger.debug("No icon for category %s", entry.category)
            self._model.appendRow(item)

        if matches:
            self._completer.complete()
        else:
            self._completer.popup().hide()

    def _accept_typed(self):
        """Enter with nothing highlighted takes the best match for what was typed."""
        text = self.text().strip()
        if not text:
            return
        matches = rank_entries(self.index(), text, limit=1)
        if matches:
            self._choose(matches[0].entry.name)

    def _choose(self, operator):
        if not operator:
            return

        # The one chokepoint the two entry paths share — a popup row activated,
        # and Enter on typed text taking the best match. Disabling the row covers
        # the first and nothing covers the second, so the refusal belongs here
        # rather than beside either one: typing "shaded" and pressing Enter must
        # not open a form whose Run would refuse.
        detail = self._gaps.get(operator)
        if detail:
            logger.info("Toolbar search refused %s: build feature missing", operator)
            window = self.window()
            if hasattr(window, "statusBar"):
                window.statusBar().showMessage(f"{operator} — {detail}", 8000)
            QTimer.singleShot(0, self.clear)
            return

        # Cleared on the next turn of the event loop: the completer is still
        # writing the accepted text into this box while activated() runs, so
        # clearing here would be undone.
        QTimer.singleShot(0, self.clear)
        self.operator_chosen.emit(operator)


class NCExplorerToolbar(QToolBar):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.main_window = parent
        self.setup_ui()

    def setup_ui(self):
        self.setMovable(True)
        self.setIconSize(QSize(24, 24))

        # Batch first, then the operator search, then the fourteen category
        # buttons. The basemap selector goes ahead of Batch, but it is built
        # after the canvas exists — see add_leading_widget().
        self._add_batch_action()
        self._add_operator_search()

        # Asked once and shared: building it walks the whole 943-operator
        # catalog, and all fourteen menus filter against the same answer.
        available = self._installed_operator_names()

        # Create category menus
        self.category_menus = {}
        for category in NCExplorerCategory:
            menu = QMenu(category.value, self)
            self.category_menus[category] = menu

            # Create a tool button for each category
            btn = QToolButton(self)
            btn.setText(category.value)
            btn.setMenu(menu)
            btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            self._apply_category_icon(category, btn, menu)
            self.addWidget(btn)

            # Add operators to the menu
            self.populate_category_menu(category, available)

    def _add_batch_action(self):
        """Put Batch Process near the head of the toolbar.

        Its own action rather than a copy of the menu's: the two would need
        keeping in step, and only one of them can own the key sequence without
        Qt calling it ambiguous. The menu entry owns the sequence; this one is
        deliberately plain.
        """
        action = QAction("Batch…", self)
        action.setToolTip("Apply a recorded pipeline to a folder of files")
        action.triggered.connect(
            lambda: self.main_window.open_batch_dialog() if self.main_window else None
        )
        self.addAction(action)
        self.batch_action = action
        self.addSeparator()

    def _add_operator_search(self):
        """Put the inline operator search between Batch and the category buttons."""
        self.search = OperatorSearchBox(self.main_window, self)
        self.search.operator_chosen.connect(self.operator_selected)
        self.search_action = self.addWidget(self.search)
        self.addSeparator()

    def add_leading_widget(self, widget):
        """Insert a widget at the head of the toolbar, ahead of Batch.

        The basemap selector belongs first but cannot be built here: it needs
        the canvas, which the main window creates well after this toolbar.
        Inserting before the batch action is what lets it still come first, and
        repeated calls keep the order they were made in, since each one lands
        immediately before Batch.
        """
        return self.insertWidget(self.batch_action, widget)

    def add_leading_separator(self):
        """Separator at the head of the toolbar, ahead of Batch."""
        return self.insertSeparator(self.batch_action)

    @staticmethod
    def _apply_category_icon(category, btn, menu):
        """Make the button its category's glyph, with the name on hover.

        Fourteen labelled buttons filled the toolbar edge to edge and left
        nothing for the basemap selector or the search box, so the label moves
        into the tooltip. A missing or unreadable asset must never leave a blank
        button: the button then falls back to showing its text.
        """
        btn.setToolTip(category.value)

        try:
            icon = category_icon(category)
        except KeyError:
            logger.warning("No icon mapped for category %s; using text only", category.name)
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            return

        if icon.isNull():
            logger.warning("Icon for category %s failed to load; using text only", category.name)
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            return

        btn.setIcon(icon)
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        menu.setIcon(icon)

    def _installed_operator_names(self):
        """Names the resolved CDO actually has, or None if it cannot be asked.

        Same rule the command palette and the model builder follow: the
        installed binary decides what can be offered. Returning None on any
        failure leaves the menus schema-only, which is what a headless test or
        a broken CDO resolution gets — better a menu built from the schema than
        no menus at all.
        """
        integration = getattr(self.main_window, "NCExplorer", None)
        if integration is None:
            return None
        try:
            return set(integration.get_operator_catalog())
        except Exception:
            logger.debug("Runtime operator catalog unavailable; menus use the schema",
                         exc_info=True)
            return None

    def _add_operator_actions(self, menu, operators):
        """Append one action per operator to ``menu``.

        An operator this CDO cannot run is added **disabled and greyed, not
        omitted**, with the reason as its tooltip and a suffix on the label so
        the state is visible without hovering.

        Hiding it was the alternative and is worse in the way that matters: an
        operator that is simply absent reads as a bug in this application, and a
        user comparing the menu against CDO's documentation finds six plotting
        operators missing with nothing to explain it. Greyed with "(needs MAGICS
        support)" beside it says the build is the problem and points at the fix.

        Only build-capability gaps disable an action. An operator the installed
        CDO does not have at all is filtered out earlier by ``menu_operators``,
        because that is a different statement — "this CDO has no such operator"
        rather than "this CDO has it and cannot run it".
        """
        for operator in operators:
            reason = self._capability_gap(operator)
            action = QAction(operator if not reason else f"{operator}  ({reason.summary})",
                             menu)
            # The operator's identity, separate from its label. The label is
            # presentation and now carries a suffix for a gated operator;
            # anything that needs the *name* must read this instead of parsing
            # the caption back. ``operator_lab.surfaces`` does exactly that, and
            # without this the surface audit reported six operators named
            # "shaded  (needs MAGICS support)" that no other surface had heard
            # of — a false disagreement produced entirely by the caption.
            action.setData(operator)
            if reason:
                action.setEnabled(False)
                action.setToolTip(reason.detail)
            else:
                action.triggered.connect(
                    lambda checked, op=operator: self.operator_selected(op)
                )
            menu.addAction(action)

    def _capability_gap(self, operator):
        """The build-feature gap that stops ``operator`` running, or None.

        Asked of the integration rather than of a list kept here, so the menus,
        the execution layer and the operator lab cannot disagree about which
        operators this build can run — the same rule the surface audit exists to
        enforce for parameters. ``capability_gap`` is where both halves of the
        answer are derived; this only adapts its tuple to the named shape the
        menu code reads.

        Returns None whenever the probe could not answer, which is what keeps
        this from greying out operators on a CDO too old for ``--config``.
        """
        integration = getattr(self.main_window, "NCExplorer", None) if self.main_window else None
        if integration is None:
            return None
        try:
            gap = integration.capability_gap(operator)
        except Exception:                                       # pragma: no cover
            return None
        return _CapabilityGap(*gap) if gap else None

    @staticmethod
    def _module_groups(operators):
        """Group a category's operators by the CDO module they come from.

        Returns ``[(label, names)]`` sorted by module title, or ``None`` when
        grouping this way would not help.

        It helps for Arithmetic more than anywhere: seventy-eight operators
        chunked alphabetically read "abs … divdpy", "divdpm … ymondiv", which
        says nothing about what is inside. By module they read "Arithmetic on
        two datasets (8)", "Arithmetic with a constant (7)", "Multi-year monthly
        arithmetic (5)" — which is both the distinction a user is actually
        making and the way the CDO documentation is organised.

        ``None`` for two cases. A category whose operators CDO does not place
        (no module is known for any of them) has nothing to group by. And a
        category that would produce more than ``CHUNK_THRESHOLD`` groups —
        Statistical values makes 44, Miscellaneous 54 — is not improved by
        replacing a long menu with a longer one; at that size the search box
        and the command palette are the answer, not the menu.
        """
        groups = {}
        unplaced = []
        for operator in operators:
            module = operator_module(operator)
            if module:
                groups.setdefault(module, []).append(operator)
            else:
                unplaced.append(operator)

        if not groups or len(groups) + bool(unplaced) > CHUNK_THRESHOLD:
            return None

        labelled = [
            (f"{module} ({len(names)})", sorted(names))
            for module, names in sorted(groups.items())
        ]
        if unplaced:
            # CDO answers "No help available for this operator!" for about
            # ninety of its own operators, so they belong to no module it will
            # name. They go last rather than being hidden.
            labelled.append((f"Other ({len(unplaced)})", sorted(unplaced)))
        return labelled

    @staticmethod
    def _alphabetical_chunks(operators):
        """Group a sorted operator list into ``(label, names)`` runs.

        Splitting happens only between initial letters, so every operator
        starting with the same letter stays in one chunk and a name's chunk can
        be guessed from the name. ``CHUNK_SIZE`` is therefore a target that a
        large letter group (``tim*``, ``sel*``) will overshoot.
        """
        chunks = []
        current = []
        for operator in operators:
            if current and len(current) >= CHUNK_SIZE and operator[0] != current[-1][0]:
                chunks.append(current)
                current = []
            current.append(operator)
        if current:
            chunks.append(current)

        return [
            (f"{chunk[0]} … {chunk[-1]}" if len(chunk) > 1 else chunk[0], chunk)
            for chunk in chunks
        ]

    def populate_category_menu(self, category, available=None):
        """Fill one category menu: ten operators, then all of them.

        The ten at the top are the head of the curated list — the hand-picked
        few that have always been here — falling through to the schema's
        alphabetical order for a category the curation never reached. Statistical
        values alone holds 278 operators, so what the menu opens on has to be a
        shortlist rather than the category.

        "All …" below it is the complete category, grouped by CDO module where
        that is readable (see :meth:`_module_groups`) and chunked alphabetically
        otherwise. It repeats the ten deliberately: a list that skipped whatever
        happened to be shown above it would have holes in the alphabet exactly
        where someone is scanning for a name.

        ``available`` is the installed CDO's operator names, or None to trust
        the schema alone.
        """
        menu = self.category_menus[category]
        menu.clear()

        curated, rest = menu_operators(category, available)
        # Curated first: those are the operators the menus have always opened
        # on, and growing the catalog must not push them out of reach.
        everything = curated + rest
        if not everything:
            return

        self._add_operator_actions(menu, everything[:TOP_LEVEL_LIMIT])

        if len(everything) <= TOP_LEVEL_LIMIT:
            return

        menu.addSeparator()
        all_menu = QMenu(f"All {category.value} ({len(everything)})…", menu)
        menu.addMenu(all_menu)

        complete = sorted(everything)
        if len(complete) <= CHUNK_THRESHOLD:
            self._add_operator_actions(all_menu, complete)
            return

        groups = self._module_groups(complete) or self._alphabetical_chunks(complete)
        for label, chunk in groups:
            chunk_menu = QMenu(label, all_menu)
            all_menu.addMenu(chunk_menu)
            self._add_operator_actions(chunk_menu, chunk)

    def operator_selected(self, operator):
        """Handle operator selection from menu"""
        if self.main_window:
            self.main_window.show_operator_parameters(operator)
