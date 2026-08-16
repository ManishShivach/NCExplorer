"""The model builder: a canvas for wiring operators together before running them.

Everything structural lives in ``core/model.py``; this module is the front end
and holds no rules of its own. When the canvas rejects a wire it is because
:meth:`ModelGraph.connect` raised, and the message shown is the one that raised —
so the editor and a script driving the same graph can never disagree about what
is legal.

Four decisions worth stating:

* **``QGraphicsView``, not a node-editor package.** ``requirements.txt`` is
  deliberately short and the application ships as a PyInstaller bundle where
  every dependency is weight and another thing that can fail to freeze. A node,
  a port and a wire are three ``QGraphicsItem`` subclasses; that is a smaller
  cost than a package.
* **The dock floats and starts large.** The other analysis docks are tabbed into
  a strip beside the map, which suits a table or a plot and does not suit a
  canvas — a graph three operators wide does not fit in a panel that width. It is
  added to the right area for consistency with its siblings, but sized and
  offered as a floating window, which is where anyone actually drawing a model
  will want it.
* **Validation runs while you draw, not when you press Run.** Debounced, because
  it walks the graph and the user is dragging. Being told about a missing
  parameter at the moment the parameter is missing is the difference between a
  panel that helps and one that scolds.
* **The command preview is the point.** For somebody learning CDO, watching the
  invocation assemble itself as boxes are wired together teaches more than the
  reference card does. It is nearly free once ``compile()`` exists.

The parameter editor here is built from ``OperatorSpec.params`` rather than
reusing ``main_window.show_operator_parameters``: that method is 200 lines that
write into ``self.parameter_widgets`` and ``self.params_layout`` on the main
window, drive their types from a parsed syntax string rather than from the
schema, and cannot be called for a widget that is not the operator form. Pulling
it apart is a change to ``main_window.py``, not a part of this feature.
"""

from __future__ import annotations

import logging
import shlex
from pathlib import Path
from typing import Sequence, Tuple

from PyQt6.QtCore import QLineF, QMimeData, QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QBrush, QColor, QDoubleValidator, QIntValidator, QPainter, QPainterPath,
    QPen, QPolygonF
)
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QGraphicsItem, QGraphicsPathItem, QGraphicsScene,
    QGraphicsView, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
    QSplitter, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget
)

from ..core import filetypes as ft
from ..core.categories import (
    GRID_PRESETS, NCExplorerCategory, OPERATOR_SCHEMA, format_recipe,
    input_file_kind, operator_env, operator_inputs, operator_outputs,
    parameter_file_kind, reads_stdin,
)
from ..core.model import (
    EXTENSION, FOLDER, MODEL_SUFFIX, OPERATOR, PRODUCER_KINDS, SINK, SOURCE,
    ModelError, ModelGraph, ModelNode, OperatorCatalog, ValidationIssue,
    fused_commands, load_model, portable_allocator, save_model,
)
from ..core.model_runner import ModelRunner
from ..resources.icons import category_icon
from .command_palette import build_entries, rank_entries
from .widgets import MultiSelectEdit

logger = logging.getLogger(__name__)

#: Milliseconds of quiet before the graph is revalidated. Long enough that
#: dragging a node does not revalidate on every mouse-move, short enough that a
#: finished edit feels checked immediately.
VALIDATE_DELAY = 220

NODE_WIDTH = 168.0
NODE_HEIGHT = 62.0
PORT_RADIUS = 6.0

#: Status tints, keyed by the state the runner last reported for a node.
IDLE = "idle"
RUNNING = "running"
DONE = "done"
FAILED = "failed"

STATUS_COLOURS = {
    IDLE: QColor("#3c4450"),
    RUNNING: QColor("#8a6d1f"),
    DONE: QColor("#2f6a3f"),
    FAILED: QColor("#8a2f2f"),
}

KIND_COLOURS = {
    SOURCE: QColor("#2f5a7a"),
    FOLDER: QColor("#2f5a7a"),
    SINK: QColor("#5a3a6a"),
}

#: Offered when an info operator's reading is kept. Plain text because that is
#: what those operators print; the engine's own output extensions mean nothing
#: here, since nothing it writes is involved.
TEXT_FILTER = "Text file (*.txt);;All Files (*)"
TEXT_SUFFIX = ".txt"

#: What a source or sink node names. Both are datasets, so both get the CDO
#: data chooser — the four entries CDO documents rather than the two this used
#: to write out, which omitted ``.grb2``/``.grib2`` and the SERVICE/EXTRA/IEG
#: formats the engine has always accepted.
FILE_FILTER = ft.dialog_filter(ft.DATA)
MODEL_FILTER = f"NCExplorer model (*{MODEL_SUFFIX});;All Files (*)"

#: (format, menu label, file dialog filter, suggested name). The same three
#: exporters ``core/session_log.py`` provides — a model is exported by building
#: session steps from its compiled requests, never by a fourth exporter.
EXPORT_FORMATS: tuple[tuple[str, str, str, str], ...] = (
    ("shell", "Shell script…", "Shell script (*.sh);;All Files (*)", "model.sh"),
    ("makefile", "Makefile…", "Makefile (Makefile *.mk);;All Files (*)", "Makefile"),
    ("notebook", "Jupyter notebook…", "Notebook (*.ipynb);;All Files (*)", "model.ipynb"),
)


def _arity(value: int) -> str:
    """CDO's -1 spelled as the ``n`` it means, exactly as the palette spells it."""
    return "n" if value == -1 else str(value)


# ---------------------------------------------------------------------------
# Canvas items
# ---------------------------------------------------------------------------

class PortItem(QGraphicsItem):
    """One connection point. Inputs sit on the left edge, the output on the right."""

    def __init__(self, node_item: "NodeItem", index: int, is_input: bool):
        super().__init__(node_item)
        self.node_item = node_item
        self.index = index
        self.is_input = is_input
        self.setAcceptHoverEvents(True)
        self.setZValue(2)
        self._hovered = False

    def boundingRect(self) -> QRectF:
        # Generous compared with what is drawn: a 6px circle is a hard thing to
        # hit with a mouse, and the extra margin is invisible.
        return QRectF(-PORT_RADIUS - 3, -PORT_RADIUS - 3,
                      2 * (PORT_RADIUS + 3), 2 * (PORT_RADIUS + 3))

    def paint(self, painter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        colour = QColor("#d8dee9") if self._hovered else QColor("#8b95a5")
        painter.setBrush(QBrush(colour))
        painter.setPen(QPen(QColor("#20252c"), 1))
        painter.drawEllipse(QPointF(0, 0), PORT_RADIUS, PORT_RADIUS)

    def hoverEnterEvent(self, event) -> None:
        self._hovered = True
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:
        self._hovered = False
        self.update()
        super().hoverLeaveEvent(event)


class NodeItem(QGraphicsItem):
    """One box: a caption, the operator's signature, and its ports."""

    def __init__(self, node: ModelNode, scene_view: "ModelCanvas"):
        super().__init__()
        self.node_id = node.id
        self.view = scene_view
        self.status = IDLE
        self._ports: list[PortItem] = []
        self._output_port: PortItem | None = None
        self._title = ""
        self._subtitle = ""
        self._invalid = False

        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setPos(QPointF(*node.position))
        self.setZValue(1)

    # -- geometry -------------------------------------------------------
    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, NODE_WIDTH, NODE_HEIGHT)

    def input_pos(self, index: int) -> QPointF:
        for port in self._ports:
            if port.index == index:
                return port.scenePos()
        return self.scenePos()

    def output_pos(self) -> QPointF:
        if self._output_port is not None:
            return self._output_port.scenePos()
        return self.scenePos() + QPointF(NODE_WIDTH, NODE_HEIGHT / 2)

    # -- appearance -----------------------------------------------------
    def configure(self, node: ModelNode, inputs: int, signature: str) -> None:
        """Rebuild the caption and the ports for one (possibly edited) node."""
        self._title = node.title
        self._subtitle = signature
        self._rebuild_ports(node, inputs)
        self.update()

    def _rebuild_ports(self, node: ModelNode, inputs: int) -> None:
        for port in self._ports:
            port.setParentItem(None)
            if port.scene() is not None:
                port.scene().removeItem(port)
        self._ports = []

        if node.kind != SOURCE:
            spacing = NODE_HEIGHT / (inputs + 1) if inputs else NODE_HEIGHT / 2
            for index in range(inputs):
                port = PortItem(self, index, True)
                port.setPos(0, spacing * (index + 1))
                self._ports.append(port)

        if node.kind == SINK:
            if self._output_port is not None:
                self._output_port.setParentItem(None)
                if self._output_port.scene() is not None:
                    self._output_port.scene().removeItem(self._output_port)
                self._output_port = None
        elif self._output_port is None:
            self._output_port = PortItem(self, 0, False)
            self._output_port.setPos(NODE_WIDTH, NODE_HEIGHT / 2)

    def set_status(self, status: str) -> None:
        if status != self.status:
            self.status = status
            self.update()

    def set_invalid(self, invalid: bool) -> None:
        if invalid != self._invalid:
            self._invalid = invalid
            self.update()

    def paint(self, painter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        node = self.view.graph.node(self.node_id)
        kind = node.kind if node is not None else OPERATOR

        base = KIND_COLOURS.get(kind) or STATUS_COLOURS.get(self.status, STATUS_COLOURS[IDLE])
        if kind == OPERATOR and self.status != IDLE:
            base = STATUS_COLOURS[self.status]

        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, NODE_WIDTH, NODE_HEIGHT), 7, 7)
        painter.fillPath(path, QBrush(base))

        if self.isSelected():
            pen = QPen(QColor("#7fb3ff"), 2.5)
        elif self._invalid:
            pen = QPen(QColor("#d46a6a"), 2.0)
        else:
            pen = QPen(QColor("#20252c"), 1.2)
        painter.setPen(pen)
        painter.drawPath(path)

        painter.setPen(QPen(QColor("#f2f4f8")))
        font = painter.font()
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(QRectF(10, 8, NODE_WIDTH - 20, 20),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         _elide(self._title, 20))

        font.setBold(False)
        font.setPointSizeF(max(7.0, font.pointSizeF() - 1.5))
        painter.setFont(font)
        painter.setPen(QPen(QColor("#c3cbd8")))
        painter.drawText(QRectF(10, 30, NODE_WIDTH - 20, 24),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                         _elide(self._subtitle, 26))

    # -- interaction ----------------------------------------------------
    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self.view.node_moved(self.node_id, self.pos())
        return super().itemChange(change, value)


class EdgeItem(QGraphicsPathItem):
    """One wire, drawn as a horizontal-tangent cubic with an arrow at the end."""

    def __init__(self, connection):
        super().__init__()
        self.connection = connection
        self.setZValue(0)
        self.setPen(QPen(QColor("#7d8797"), 2.0))

    def retarget(self, start: QPointF, end: QPointF) -> None:
        path = QPainterPath(start)
        # A flat tangent proportional to the gap keeps the curve readable whether
        # the nodes are side by side or right across the canvas.
        reach = max(40.0, abs(end.x() - start.x()) * 0.5)
        path.cubicTo(start + QPointF(reach, 0), end - QPointF(reach, 0), end)
        self.setPath(path)

        head = QPolygonF([
            end, end + QPointF(-9, -4.5), end + QPointF(-9, 4.5),
        ])
        arrow = QPainterPath()
        arrow.addPolygon(head)
        self._arrow = arrow

    def paint(self, painter, option, widget=None) -> None:
        super().paint(painter, option, widget)
        arrow = getattr(self, "_arrow", None)
        if arrow is not None:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.fillPath(arrow, QBrush(self.pen().color()))


class ModelCanvas(QGraphicsView):
    """The drawing surface: nodes, wires, and the gestures that make them."""

    #: The graph changed in a way that needs revalidating and recompiling.
    changed = pyqtSignal()
    #: (node id or "") — the selection changed.
    selected = pyqtSignal(str)

    def __init__(self, graph: ModelGraph, catalog: OperatorCatalog, parent=None):
        super().__init__(parent)
        self.graph = graph
        self.catalog = catalog

        self._scene = QGraphicsScene(self)
        self._scene.setSceneRect(-2000, -2000, 4000, 4000)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setBackgroundBrush(QBrush(QColor("#1b1f25")))
        self.setAcceptDrops(True)

        self._nodes: dict[str, NodeItem] = {}
        self._edges: list[EdgeItem] = []
        self._dragging_from: PortItem | None = None
        self._rubber: QGraphicsPathItem | None = None

        self._scene.selectionChanged.connect(self._on_selection_changed)

    # -- rebuilding -----------------------------------------------------
    def rebuild(self) -> None:
        """Redraw everything from the graph.

        Wholesale rather than incremental on purpose: a graph is tens of nodes,
        not thousands, and an incremental path is where a canvas silently drifts
        out of step with the model behind it.
        """
        selected = self.selected_node()
        self._scene.clear()
        self._nodes.clear()
        self._edges.clear()
        self._rubber = None
        self._dragging_from = None

        for node in self.graph.nodes:
            item = NodeItem(node, self)
            self._scene.addItem(item)
            item.configure(node, self._input_count(node), self._signature(node))
            self._nodes[node.id] = item

        for connection in self.graph.connections:
            edge = EdgeItem(connection)
            self._scene.addItem(edge)
            self._edges.append(edge)

        self._retarget_edges()
        if selected and selected in self._nodes:
            self._nodes[selected].setSelected(True)

    def refresh_node(self, node_id: str) -> None:
        """Re-caption one node after an edit, without redrawing the canvas."""
        node = self.graph.node(node_id)
        item = self._nodes.get(node_id)
        if node is None or item is None:
            return
        item.configure(node, self._input_count(node), self._signature(node))
        self._retarget_edges()

    def _input_count(self, node: ModelNode) -> int:
        """How many operand slots to draw.

        A variable-arity operator gets one more than it currently uses, so there
        is always somewhere to drop the next input and never a row of empty ports
        implying a limit that does not exist.
        """
        if node.kind in PRODUCER_KINDS:
            return 0
        if node.kind == SINK:
            return 1
        nin = (self.catalog.signature(node.operator) or (1, 1))[0]
        if nin == -1:
            return len(self.graph.incoming(node.id)) + 1
        return max(nin, 0)

    def _signature(self, node: ModelNode) -> str:
        if node.kind == FOLDER:
            count = len(node.paths)
            return f"{count} file{'' if count == 1 else 's'}" if count else "no files chosen"
        if node.kind in (SOURCE, SINK):
            return Path(node.path).name if node.path else "no file chosen"
        signature = self.catalog.signature(node.operator)
        if signature is None:
            return f"{node.operator} — not installed"
        nin, nout = signature
        if nout == 0:
            kept = f"  ⏺ {Path(node.path).name}" if node.path else ""
        else:
            kept = "  ⏺" if node.keep_output and node.path else ""
        return f"{node.operator}  ·  {_arity(nin)} → {_arity(nout)}{kept}"

    def _retarget_edges(self) -> None:
        for edge in self._edges:
            source = self._nodes.get(edge.connection.source)
            target = self._nodes.get(edge.connection.target)
            if source is None or target is None:
                continue
            edge.retarget(source.output_pos(),
                          target.input_pos(edge.connection.target_port))

    # -- status ---------------------------------------------------------
    def set_status(self, node_id: str, status: str) -> None:
        item = self._nodes.get(node_id)
        if item is not None:
            item.set_status(status)

    def clear_statuses(self) -> None:
        for item in self._nodes.values():
            item.set_status(IDLE)

    def mark_issues(self, issues: list[ValidationIssue]) -> None:
        faulty = {issue.node for issue in issues if issue.is_error and issue.node}
        for node_id, item in self._nodes.items():
            item.set_invalid(node_id in faulty)

    def focus_node(self, node_id: str) -> None:
        item = self._nodes.get(node_id)
        if item is None:
            return
        self._scene.clearSelection()
        item.setSelected(True)
        self.centerOn(item)

    def selected_node(self) -> str:
        for item in self._scene.selectedItems():
            if isinstance(item, NodeItem):
                return item.node_id
        return ""

    def _on_selection_changed(self) -> None:
        self.selected.emit(self.selected_node())

    def node_moved(self, node_id: str, position: QPointF) -> None:
        node = self.graph.node(node_id)
        if node is None:
            return
        self.graph.update_node(node_id, position=(position.x(), position.y()))
        self._retarget_edges()
        # Deliberately not `changed`: a move alters no command, and recompiling
        # on every pixel of a drag would allocate a temporary file per frame.

    # -- wiring ---------------------------------------------------------
    def mousePressEvent(self, event) -> None:
        port = self._port_at(event.position().toPoint())
        if port is not None and event.button() == Qt.MouseButton.LeftButton:
            self._dragging_from = port
            self._rubber = QGraphicsPathItem()
            self._rubber.setPen(QPen(QColor("#7fb3ff"), 2, Qt.PenStyle.DashLine))
            self._scene.addItem(self._rubber)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._rubber is not None and self._dragging_from is not None:
            start = self._dragging_from.scenePos()
            end = self.mapToScene(event.position().toPoint())
            path = QPainterPath(start)
            path.lineTo(end)
            self._rubber.setPath(path)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._dragging_from is None:
            super().mouseReleaseEvent(event)
            return

        origin, self._dragging_from = self._dragging_from, None
        if self._rubber is not None:
            self._scene.removeItem(self._rubber)
            self._rubber = None

        target = self._port_at(event.position().toPoint())
        if target is None or target.node_item is origin.node_item:
            event.accept()
            return

        # Drawn either way round: dragging from an input back to an output is the
        # same wire, and refusing it teaches the user nothing.
        if origin.is_input == target.is_input:
            self._reject("Wire an output into an input")
            event.accept()
            return
        output, operand = (target, origin) if origin.is_input else (origin, target)

        try:
            self.graph.connect(output.node_item.node_id, output.index,
                               operand.node_item.node_id, operand.index)
        except ModelError as exc:
            # A silent no-op reads as a broken canvas; the reason is always
            # something the user can act on.
            self._reject(str(exc))
            event.accept()
            return

        self.rebuild()
        self.changed.emit()
        event.accept()

    def _port_at(self, point) -> PortItem | None:
        for item in self.items(point):
            if isinstance(item, PortItem):
                return item
        return None

    def _reject(self, message: str) -> None:
        logger.debug("Rejected a connection: %s", message)
        window = self.window()
        status = getattr(window, "statusBar", None)
        if callable(status):
            status().showMessage(message, 4000)
        QMessageBox.information(self, "Model Builder", message)

    # -- context menu and keys ------------------------------------------
    def contextMenuEvent(self, event) -> None:
        item = self.itemAt(event.pos())
        while item is not None and not isinstance(item, (NodeItem, EdgeItem)):
            item = item.parentItem()

        menu = QMenu(self)
        if isinstance(item, NodeItem):
            menu.addAction("Delete node").triggered.connect(
                lambda: self._delete_node(item.node_id))
            menu.addAction("Disconnect inputs").triggered.connect(
                lambda: self._clear_inputs(item.node_id))
        elif isinstance(item, EdgeItem):
            menu.addAction("Delete wire").triggered.connect(
                lambda: self._delete_edge(item.connection))
        else:
            menu.addAction("Fit to view").triggered.connect(self.fit_to_content)
        menu.exec(event.globalPos())

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            node_id = self.selected_node()
            if node_id:
                self._delete_node(node_id)
                event.accept()
                return
        super().keyPressEvent(event)

    def _delete_node(self, node_id: str) -> None:
        self.graph.remove_node(node_id)
        self.rebuild()
        self.changed.emit()

    def _delete_edge(self, connection) -> None:
        self.graph.disconnect(connection.source, connection.source_port,
                              connection.target, connection.target_port)
        self.rebuild()
        self.changed.emit()

    def _clear_inputs(self, node_id: str) -> None:
        for connection in self.graph.incoming(node_id):
            self.graph.disconnect(connection.source, connection.source_port,
                                  connection.target, connection.target_port)
        self.rebuild()
        self.changed.emit()

    def fit_to_content(self) -> None:
        rect = self._scene.itemsBoundingRect()
        if rect.isValid():
            self.fitInView(rect.adjusted(-40, -40, 40, 40),
                           Qt.AspectRatioMode.KeepAspectRatio)

    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
            self.scale(factor, factor)
            event.accept()
            return
        super().wheelEvent(event)

    # -- drops ----------------------------------------------------------
    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls() or event.mimeData().hasText():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls() or event.mimeData().hasText():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        where = self.mapToScene(event.position().toPoint())
        mime = event.mimeData()

        if mime.hasUrls():
            for offset, url in enumerate(mime.urls()):
                path = url.toLocalFile()
                if not path:
                    continue
                position = (where.x(), where.y() + 90 * offset)
                if Path(path).is_dir():
                    # A dropped folder means the same thing the picker does: one
                    # node holding what is in it, rather than a box per file.
                    from ..core.batch import discover_inputs

                    found = discover_inputs(path, "*.nc")
                    self.graph.add(FOLDER, path=path, paths=tuple(found),
                                   pattern="*.nc", position=position)
                else:
                    self.graph.add(SOURCE, path=path, position=position)
            self.rebuild()
            self.changed.emit()
            event.acceptProposedAction()
            return

        operator = mime.text().strip()
        if operator and operator in self.catalog:
            self.add_operator(operator, where)
            event.acceptProposedAction()
            return
        event.ignore()

    def add_operator(self, operator: str, where: QPointF | None = None) -> ModelNode:
        """Drop one operator onto the canvas and select it."""
        position = where if where is not None else self._free_spot()
        node = self.graph.add(OPERATOR, operator=operator,
                              position=(position.x(), position.y()))
        self.rebuild()
        self.focus_node(node.id)
        self.changed.emit()
        return node

    def add_file_node(self, kind: str, path: str = "") -> ModelNode:
        position = self._free_spot()
        node = self.graph.add(kind, path=path, position=(position.x(), position.y()))
        self.rebuild()
        self.focus_node(node.id)
        self.changed.emit()
        return node

    def _free_spot(self) -> QPointF:
        """Somewhere in view that is not already covered by a node."""
        centre = self.mapToScene(self.viewport().rect().center())
        taken = [QPointF(*node.position) for node in self.graph.nodes]
        candidate = centre
        for _ in range(60):
            if all(QLineF(candidate, point).length() > 40 for point in taken):
                return candidate
            candidate += QPointF(26, 22)
        return candidate


def _single_operator_recipe(recipe: str) -> Tuple[str, Tuple[str, ...]]:
    """``(operator, parameters)`` when ``recipe`` is one operator over one file.

    ``"ymonavg {in1}"`` → ``("ymonavg", ())`` and ``"gtc,0 {in1}"`` →
    ``("gtc", ("0",))``; a chain → ``("", ())``. The ECA indices' recipes are
    chains ("ydrunpctl,90,5 {in1} -ydrunmin,5 {in1} …"), and those are worth
    showing but not worth a one-click button that would have to guess how to
    wire three nodes.

    Parameters used to be refused along with the chains, on the grounds that
    wiring one would be a guess. It is not: a CDO comma-parameter belongs to the
    operator token, so ``gtc,0`` carries everything the new node needs and the
    parameters can simply be handed to it. Refusing them cost the conditional
    family its button entirely, since the only single-operator way to build a
    mask is a comparison against a constant — every one of which is
    parameterised. Blank parameters are still refused: a node created with an
    empty required field is one the validator immediately flags, which is worse
    than no button.
    """
    parts = recipe.split()
    if len(parts) != 2 or parts[1] != "{in1}":
        return "", ()
    operator, separator, arguments = parts[0].partition(",")
    if not operator:
        return "", ()
    if not separator:
        return operator, ()
    # A comma was written, so parameters were meant. Every one of them has to
    # be there: "gtc," would otherwise build a gtc node with an empty required
    # field, which is precisely the node the validator exists to complain about.
    parameters = tuple(arguments.split(","))
    if any(not value.strip() for value in parameters):
        return "", ()
    return operator, parameters


def _elide(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _split_options(text: str) -> tuple[str, ...]:
    """"-f nc" as the two argv tokens CDO wants, not one.

    ``shlex`` rather than ``str.split`` so a quoted value survives — the
    ``--chunkspec`` and ``--percentile`` options both take one. An unbalanced
    quote is left as a single token rather than raising: this runs on every
    keystroke-completed edit, and a half-typed quote is not an error yet.
    """
    try:
        return tuple(shlex.split(text.strip()))
    except ValueError:
        return (text.strip(),) if text.strip() else ()


def _as_text_path(path: str) -> str:
    """Give a chosen capture file a ``.txt`` suffix if it has none of its own.

    Only when there is no extension at all: somebody who deliberately typed
    ``.csv`` or ``.log`` meant it, and this is plain text either way — unlike the
    engine's own outputs, where the extension decides the format and an
    unrecognised one is a trap.
    """
    if not path:
        return ""
    return path if Path(path).suffix else path + TEXT_SUFFIX


class OperatorTree(QTreeWidget):
    """The palette, dragging the operator's *name* rather than its row text.

    ``QTreeWidget``'s own mime data carries the visible label, which here is
    ``timmean   1 → 1`` — a string the canvas would look up and not find. One
    override is cheaper than parsing that back apart on the other side.
    """

    def mimeData(self, items) -> QMimeData:  # type: ignore[override]
        data = QMimeData()
        for item in items:
            name = item.data(0, Qt.ItemDataRole.UserRole)
            if name:
                data.setText(str(name))
                break
        return data


# ---------------------------------------------------------------------------
# Parameter editing
# ---------------------------------------------------------------------------

class ParameterEditor(QWidget):
    """The selected node's settings, built from the schema's own widget hints."""

    #: (node id) — something in the form was committed.
    edited = pyqtSignal(str)

    def __init__(self, dock: "ModelBuilderWindow"):
        super().__init__(dock)
        self.dock = dock
        self._node_id = ""
        self._fields: list[QWidget] = []

        self._layout = QFormLayout(self)
        self._layout.setContentsMargins(6, 6, 6, 6)
        self._layout.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.show_node("")

    def show_node(self, node_id: str) -> None:
        """Rebuild the form for one node, or show the empty state."""
        self._node_id = node_id
        self._fields = []
        self._clear_rows()

        node = self.dock.graph.node(node_id) if node_id else None
        if node is None:
            hint = QLabel("Select a node to edit it.")
            hint.setStyleSheet("color: gray;")
            hint.setWordWrap(True)
            self._layout.addRow(hint)
            return

        label = QLineEdit(node.label)
        label.setPlaceholderText(node.title)
        label.editingFinished.connect(
            lambda: self._commit(label=label.text().strip()))
        self._layout.addRow("Label", label)

        if node.kind == FOLDER:
            self._build_folder_rows(node)
        elif node.kind in (SOURCE, SINK):
            self._build_file_row(node)
        else:
            self._build_operator_rows(node)

    def _clear_rows(self) -> None:
        """Empty the form without destroying widgets that are still in use.

        ``QFormLayout.removeRow`` destroys the row's widgets *immediately*, and
        this runs from inside those very widgets: the "Keep this result" box
        commits from ``toggled``, which ``_on_node_edited`` answers by rebuilding
        the form that box lives in. Qt then returned into
        ``QCheckBox::nextCheckState`` — which had only just emitted the signal —
        holding a freed ``this``, and the process died with EXC_BAD_ACCESS below
        ``QAbstractButton::mouseReleaseEvent``. Nothing about it was specific to
        that one checkbox: every editor here commits from a signal Qt emits
        mid-event, so the select combo's ``currentTextChanged`` and each
        ``editingFinished`` (which fires on focus loss, while Qt is still walking
        the focus change) could all free the widget under the event delivering
        them.

        ``takeRow`` unparents rather than deletes, so ``deleteLater`` can run the
        widget down once the event still using it has unwound. They are hidden
        first because a taken row keeps this editor as its parent, and an
        unmanaged widget would otherwise sit on top of the rebuilt form for the
        rest of the turn.
        """
        while self._layout.rowCount():
            row = self._layout.takeRow(0)
            for item in (row.labelItem, row.fieldItem):
                if item is None:
                    continue
                widget = item.widget()
                if widget is not None:
                    widget.hide()
                    widget.deleteLater()
                elif item.layout() is not None:
                    item.layout().deleteLater()

    # -- rows -----------------------------------------------------------
    def _build_file_row(self, node: ModelNode) -> None:
        field = QLineEdit(node.path)
        field.setPlaceholderText("No file chosen")
        field.editingFinished.connect(lambda: self._commit(path=field.text().strip()))

        browse = QPushButton("Browse…")
        browse.clicked.connect(lambda: self._browse_into(field, node.kind))

        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(field)
        layout.addWidget(browse)
        self._layout.addRow("File", row)

        if node.kind == SOURCE:
            use_layer = QPushButton("Use the active layer")
            use_layer.setToolTip("Take the file behind the layer selected on the map")
            use_layer.clicked.connect(self._use_active_layer)
            self._layout.addRow("", use_layer)

    def _build_folder_rows(self, node: ModelNode) -> None:
        """A folder shows where it points and which of its files are in play.

        The file list is shown, not merely counted: the one thing a user needs
        from a folder node is to see at a glance what it will actually feed the
        operator, and a bare "12 files" is exactly the wrong amount of detail.
        """
        folder = QLabel(node.path or "no folder chosen")
        folder.setWordWrap(True)
        folder.setToolTip(node.path)
        self._layout.addRow("Folder", folder)

        if node.pattern:
            self._layout.addRow("Matching", QLabel(node.pattern))

        listing = QListWidget()
        listing.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        listing.setMaximumHeight(150)
        missing = 0
        for path in node.paths:
            item = QListWidgetItem(Path(path).name)
            item.setToolTip(path)
            if not Path(path).exists():
                # Named in red rather than dropped: a model opened after its data
                # moved should say which files went, not quietly shrink.
                item.setForeground(QColor("#d46a6a"))
                item.setText(item.text() + "  (missing)")
                missing += 1
            listing.addItem(item)
        self._layout.addRow(f"{len(node.paths)} file(s)", listing)

        if missing:
            warning = QLabel(f"{missing} file(s) are no longer where the model expects.")
            warning.setWordWrap(True)
            warning.setStyleSheet("color: #d46a6a; font-size: 11px;")
            self._layout.addRow(warning)

        change = QPushButton("Change files…")
        change.setToolTip("Pick a different folder, or a different set of its files")
        change.clicked.connect(lambda: self.dock.edit_folder_node(self._node_id))
        self._layout.addRow("", change)

    def _build_operator_rows(self, node: ModelNode) -> None:
        spec = self.dock.catalog.spec(node.operator)
        signature = self.dock.catalog.signature(node.operator)

        if signature is None:
            warning = QLabel(f"This build of the engine has no operator called "
                             f"{node.operator}.")
            warning.setWordWrap(True)
            warning.setStyleSheet("color: #d46a6a;")
            self._layout.addRow(warning)
            return

        if spec is not None and spec.description:
            description = QLabel(spec.description)
            description.setWordWrap(True)
            description.setStyleSheet("color: gray; font-size: 11px;")
            self._layout.addRow(description)

        wired = len(self.dock.graph.incoming(node.id))
        if signature[0] == -1:
            # The operators this matters for take as many files as you have, and
            # the folder picker is the only sane way to give them a decade of
            # monthly data. Offered right here, on the node that wants them.
            self._layout.addRow("Inputs", QLabel(
                f"{wired} wired · this operator takes any number"))
            from_folder = QPushButton("Add inputs from a folder…")
            from_folder.clicked.connect(
                lambda: self.dock.add_inputs_from_folder(self._node_id))
            self._layout.addRow("", from_folder)
        elif spec is not None and spec.inputs:
            self._build_input_slot_rows(node, spec, wired)
        elif wired < signature[0]:
            self._layout.addRow("Inputs", QLabel(
                f"{wired} of {signature[0]} wired"))

        parameters = list(node.parameters)
        for index, param in enumerate(spec.params if spec else ()):
            value = parameters[index] if index < len(parameters) else ""
            widget = self._build_param_widget(param, value, index)
            caption = (param.label or param.name) + ("" if not param.optional else "  (optional)")
            self._layout.addRow(caption, widget)
            if param.help:
                widget.setToolTip(param.help)

        self._build_environment_rows(node, spec)

        # ``>= 1`` rather than ``== 1``. A two-output node could not be kept at
        # all while this read ``== 1``, which made ``eof`` unusable in the
        # builder for its actual purpose: nothing downstream may read from it
        # (see ``ModelGraph._validate_node``), so unless a sink or this checkbox
        # names its output, both files go to a temporary and the run leaves
        # nothing behind. This is the affordance ``_output_paths`` sends people
        # to, so it has to exist for the shape that needs it.
        # An operator that reads standard input names no input file, so its
        # ``path`` is the file to *feed* it — the exact mirror of the info
        # operators below, where ``path`` is where the printed answer is kept.
        # Without this row the three Formatted input operators could be dropped
        # on the canvas and never given data.
        if reads_stdin(node.operator):
            note = QLabel("This operator reads its field values from standard "
                          "input rather than from a data file. Choose the text "
                          "file to feed it.")
            note.setWordWrap(True)
            note.setStyleSheet("color: gray; font-size: 11px;")
            self._layout.addRow(note)

            field = QLineEdit(node.path)
            field.setPlaceholderText("Text file of values, e.g. one written by "
                                     "'output'")
            field.editingFinished.connect(
                lambda: self._commit(path=field.text().strip()))
            browse = QPushButton("Browse…")
            browse.clicked.connect(lambda: self._browse_into_text(field))
            row = QWidget()
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(field)
            layout.addWidget(browse)
            self._layout.addRow("Data file", row)

        if signature[1] >= 1:
            keep = QCheckBox("Keep this result as a file")
            keep.setToolTip(
                "Write this step's output where you choose instead of into a "
                "temporary file that is deleted when the run ends"
            )
            keep.setChecked(node.keep_output)
            keep.toggled.connect(lambda checked: self._commit(keep_output=checked))
            self._layout.addRow("", keep)

            if node.keep_output and signature[1] > 1:
                # What the one path they are about to type actually names, since
                # the node writes more than one file and the rest are derived
                # from it. Same rule as ``_output_paths``, said where the path
                # is typed rather than only in the code that applies it.
                slots = operator_outputs(node.operator)
                lines = []
                for index, slot in enumerate(slots):
                    role = slot.role.split("—")[0].strip() or f"output {index + 1}"
                    lines.append(f"the name you type{slot.suffix}  →  {role}"
                                 if index else f"the name you type  →  {role}")
                note = QLabel("This step writes " + str(signature[1])
                              + " files. " + "; ".join(lines) + ".")
                note.setWordWrap(True)
                note.setStyleSheet("color: gray; font-size: 11px;")
                self._layout.addRow(note)

            if node.keep_output:
                field = QLineEdit(node.path)
                field.setPlaceholderText("Where to keep it")
                field.editingFinished.connect(
                    lambda: self._commit(path=field.text().strip()))
                browse = QPushButton("Browse…")
                browse.clicked.connect(lambda: self._browse_into(field, SINK))
                row = QWidget()
                layout = QHBoxLayout(row)
                layout.setContentsMargins(0, 0, 0, 0)
                layout.addWidget(field)
                layout.addWidget(browse)
                self._layout.addRow("File", row)

        elif signature[1] == 0:
            # An info operator writes no file and reports entirely on stdout, so
            # its result is the console — and a console nobody scrolled back
            # through is not a result. This is where it gets kept.
            note = QLabel("This operator prints its answer rather than writing a "
                          "data file. Save that text here if you want to keep it.")
            note.setWordWrap(True)
            note.setStyleSheet("color: gray; font-size: 11px;")
            self._layout.addRow(note)

            field = QLineEdit(node.path)
            field.setPlaceholderText("Leave empty to print to the console only")
            field.editingFinished.connect(
                lambda: self._commit(path=_as_text_path(field.text().strip())))
            browse = QPushButton("Browse…")
            browse.clicked.connect(lambda: self._browse_into_text(field))
            row = QWidget()
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(field)
            layout.addWidget(browse)
            self._layout.addRow("Save output to", row)

            if node.path:
                clear = QPushButton("Do not save it")
                clear.clicked.connect(lambda: self._commit(path=""))
                self._layout.addRow("", clear)

        # CDO's global options for this step. Offered here for the same reason
        # the operator panel offers them, and offered *here* specifically so the
        # two surfaces can do the same things: a pipeline whose import step
        # cannot be given ``-f nc`` writes GRIB into a file called .nc, and the
        # only way round it would have been to run that step outside the model.
        options = QLineEdit(" ".join(node.options))
        options.setPlaceholderText("e.g. -f nc  (optional)")
        options.setToolTip(
            "CDO options that go before the operator name, such as -f nc for "
            "NetCDF output or -z zip for compression.")
        options.editingFinished.connect(
            lambda: self._commit(options=_split_options(options.text())))
        self._layout.addRow("CDO options", options)

    def _build_environment_rows(self, node: ModelNode, spec) -> None:
        """One row per environment variable this operator reads.

        Empty for all but the eight operators of the EOFs section, so every
        other inspector is drawn exactly as it was.

        These are not parameters and are deliberately not laid out as though
        they were: they live in ``node.env`` rather than ``node.parameters``,
        because ``parameters`` is positional and becomes the ``op,a,b`` token.
        A blank field means "leave CDO's default alone" — nothing is exported
        unless a value is chosen, which is why the combo boxes carry an empty
        first entry rather than being pre-set to the default.
        """
        variables = operator_env(node.operator) if spec is not None else ()
        if not variables:
            return

        heading = QLabel(
            "Environment — these change what this step computes, not how it is "
            "spelled. Blank means CDO's default.")
        heading.setWordWrap(True)
        heading.setStyleSheet("color: gray; font-size: 11px;")
        self._layout.addRow(heading)

        current = dict(node.env)
        for variable in variables:
            value = current.get(variable.name, "")
            if variable.kind == "select" and variable.choices:
                widget = QComboBox()
                widget.setEditable(True)
                widget.addItems(["", *variable.choices])
                widget.setCurrentText(value)
                widget.currentTextChanged.connect(
                    lambda text, name=variable.name: self._commit_env(name, text))
            else:
                widget = QLineEdit(value)
                widget.setPlaceholderText(
                    f"default {variable.default}" if variable.default else "")
                if variable.kind == "int":
                    widget.setValidator(QIntValidator())
                elif variable.kind == "float":
                    widget.setValidator(QDoubleValidator())
                widget.editingFinished.connect(
                    lambda name=variable.name, w=widget:
                    self._commit_env(name, w.text()))
            widget.setToolTip(variable.help or variable.name)
            self._layout.addRow(variable.label or variable.name, widget)

    def _commit_env(self, name: str, value: str) -> None:
        """Set or clear one environment override on the current node.

        Clearing removes the pair rather than storing an empty value: CDO reads
        ``CDO_WEIGHT_MODE=`` as a value and not as "unset", so an emptied field
        has to leave nothing behind at all.
        """
        node = self.dock.graph.node(self._node_id) if self._node_id else None
        if node is None:
            return
        value = value.strip()
        pairs = [(key, existing) for key, existing in node.env if key != name]
        if value:
            # Appended rather than inserted in place, then re-sorted into the
            # schema's declaration order, so the stored order matches the order
            # the inspector shows regardless of which field was edited first.
            pairs.append((name, value))
        order = [variable.name for variable in operator_env(node.operator)]
        pairs.sort(key=lambda pair: order.index(pair[0])
                   if pair[0] in order else len(order))
        env = tuple(pairs)
        if env == node.env:
            return
        self.dock.graph.update_node(self._node_id, env=env)
        self.edited.emit(self._node_id)

    def _build_input_slot_rows(self, node: ModelNode, spec, wired: int) -> None:
        """One row per input slot, captioned with what that slot must hold.

        "Input 2" is a true statement that tells a user nothing, and for the
        climate indices it is the dangerous kind of nothing: ``eca_cwfi`` runs
        against any second file whose grid matches and writes plausible, wrong
        numbers, because the file it wants is a 10th-percentile climatology.
        The schema knows that (``OperatorSpec.inputs``); this is where the user
        finds out. The recipe is shown as a selectable command so somebody who
        does not have the file can go and make it.

        Which slot a recipe *fills* and which slot it *reads* are two different
        questions, and they only had one answer while every recipe belonged to
        slot 1 and read slot 0. ``ifthen``'s mask is slot 0 built from slot 1, so
        the caption, the quoted command and the button all take the source slot
        from ``slot.recipe_source`` rather than assuming input 1.
        """
        window = self._window_argument(node, spec)
        # Which ports actually have something in them, rather than how many do.
        # ``wired`` is a count, and a count answers "is slot 2 filled?" wrongly
        # the moment ports are filled out of order — which is exactly what
        # happens here, because the natural way to build a conditional is to
        # wire the data into input 2 first and then go looking for a mask.
        connected = {c.target_port for c in self.dock.graph.incoming(node.id)}
        for index, slot in enumerate(operator_inputs(node.operator), start=1):
            block = QWidget()
            layout = QVBoxLayout(block)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(2)

            filled = (index - 1) in connected
            state = "wired" if filled else "not wired yet"
            role = QLabel(f"{slot.role} — {state}")
            role.setWordWrap(True)
            if not filled:
                role.setStyleSheet("color: #d0a24c;")
            layout.addWidget(role)

            if slot.field:
                field = QLabel(slot.field)
                field.setWordWrap(True)
                field.setStyleSheet("color: gray; font-size: 11px;")
                layout.addWidget(field)

            source_label = f"«input {slot.recipe_source + 1}»"
            recipe = format_recipe(slot.recipe, in1=source_label, n=window)
            if recipe:
                command = QLineEdit(recipe)
                command.setReadOnly(True)
                command.setCursorPosition(0)
                command.setToolTip(
                    f"Build this file from input {slot.recipe_source + 1} "
                    f"with this command")
                command.setStyleSheet("font-family: monospace; font-size: 11px;")
                layout.addWidget(command)

                # A recipe that is one operator over one file is a shape the
                # graph can draw itself, so offer to. Only for a slot that is
                # still empty, and only when the slot the recipe *reads* has
                # something to branch from — asking whether the node has any
                # incoming connection at all would offer the button for
                # ``ifthen`` when only the mask port is wired, and the recipe
                # needs the data port.
                operator, parameters = _single_operator_recipe(slot.recipe)
                if operator and not filled and slot.recipe_source in connected:
                    insert = QPushButton(f"Add a {operator} node and wire it in")
                    insert.clicked.connect(
                        lambda _checked=False, op=operator, args=parameters,
                        into=index - 1, source=slot.recipe_source:
                        self.dock.insert_companion(
                            self._node_id, op, parameters=args,
                            from_port=source, into_port=into))
                    layout.addWidget(insert)

            self._layout.addRow(f"Input {index}", block)

    #: What an operator may call the window its companion recipes share. ``n``
    #: is the ETCCDI bootstrapping spelling; ``nts`` and ``nsets`` are what the
    #: Statistic section's two windowed percentile operators call the same
    #: thing (``ydrunpctl,pn,nts`` and ``timselpctl,pn,nsets``). Matched by
    #: name rather than by position because the window is the *second*
    #: parameter for both of those — the first is the percentile — so an
    #: index-based rule would quote the percentile as the window.
    _WINDOW_PARAM_NAMES = ("n", "nts", "nsets")

    @classmethod
    def _window_argument(cls, node: ModelNode, spec) -> str:
        """The node's own window, so a recipe quotes the one it will run with.

        The ETCCDI bootstrapping indices take their running minimum and maximum
        over the same window their first parameter names, so a recipe showing a
        different one would be wrong for this node specifically. The same is
        true of ``ydrunpctl`` and ``timselpctl``, whose companion files are
        ``ydrunmin,<nts>`` and ``timselmin,<nsets>`` — and for those two the
        window is not optional decoration: dropping it hangs CDO rather than
        failing it. See ``_PCTL_COMPANIONS`` in core/categories.py.
        """
        for index, param in enumerate(spec.params):
            if param.name not in cls._WINDOW_PARAM_NAMES:
                continue
            if index < len(node.parameters) and node.parameters[index].strip():
                return node.parameters[index].strip()
        return "5"

    def _build_param_widget(self, param, value: str, index: int) -> QWidget:
        """One editor, chosen by the schema's ``kind`` hint."""
        if param.kind == "bool":
            # A CDO BOOL is a switch, not free text — see OperatorParam.kind.
            # Committed as the literal CDO takes rather than as "" / "on", so a
            # saved model round-trips through parameter_tokens unchanged.
            box = QCheckBox(param.help or "")
            box.setChecked(str(value).strip().lower() in ("true", "1", "yes", "on"))
            box.setToolTip(param.help or param.label or param.name)
            box.toggled.connect(
                lambda checked, i=index:
                self._commit_parameter(i, "true" if checked else "false"))
            return box

        if param.kind == "select" and param.choices:
            combo = QComboBox()
            combo.setEditable(True)
            combo.addItems(list(param.choices))
            combo.setCurrentText(value)
            combo.currentTextChanged.connect(
                lambda text, i=index: self._commit_parameter(i, text))
            return combo

        # The same widget the operator panel builds, from the same module: two
        # surfaces rendering one parameter differently is what the surface audit
        # exists to catch, and a combo box here would have been a different
        # answer to "how do you pick several of these" from the panel's.
        if param.kind == "multiselect" and param.choices:
            picker = MultiSelectEdit(
                param.choices, value,
                placeholder=param.placeholder or param.name)
            picker.setToolTip(param.help or param.label or param.name)
            picker.textChanged.connect(
                lambda text, i=index: self._commit_parameter(i, text))
            return picker

        if param.kind == "grid":
            combo = QComboBox()
            combo.setEditable(True)
            combo.addItems(list(GRID_PRESETS))
            combo.setCurrentText(value)
            combo.lineEdit().setPlaceholderText(param.placeholder or "grid or file")
            combo.currentTextChanged.connect(
                lambda text, i=index: self._commit_parameter(i, text))
            return combo

        field = QLineEdit(value)
        field.setPlaceholderText(param.placeholder or param.name)
        if param.kind == "int":
            field.setValidator(QIntValidator())
        elif param.kind == "float":
            field.setValidator(QDoubleValidator())
        field.editingFinished.connect(
            lambda i=index, f=field: self._commit_parameter(i, f.text()))

        if param.kind == "expression":
            edit = QToolButton()
            edit.setText("Edit…")
            edit.setToolTip(
                "Open the expression editor: the input file's variables, the "
                "function reference, and a syntax check")
            edit.clicked.connect(
                lambda _checked=False, f=field, i=index:
                self._edit_expression(f, i))
            row = QWidget()
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(field)
            layout.addWidget(edit)
            return row

        if param.kind != "file":
            return field

        # The chooser this parameter's own format asks for, not "All Files (*)"
        # — which is what stood here, and which is only right for the handful of
        # parameters CDO documents no format for. ``remap``'s weights are a
        # SCRIP NetCDF file, ``maskregion``'s regions are ASCII polygons, and
        # ``cmor``'s MIPtable is JSON.
        param_kind = parameter_file_kind(param)
        browse = QToolButton()
        browse.setText("…")
        browse.setToolTip(ft.summary(param_kind))
        browse.clicked.connect(
            lambda _checked=False, k=param_kind:
            self._browse_into_parameter(field, index, file_kind=k))
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(field)
        layout.addWidget(browse)
        return row

    def _edit_expression(self, field: QLineEdit, index: int) -> None:
        """Open the Expr editor on this node's parameter and commit the result.

        The input file is whatever this node is wired to, walked back to the
        source that supplies it — so the editor lists the same variables the
        expression will actually see, even when the node sits several steps
        downstream.
        """
        from .expression_editor import edit_expression

        node = self.dock.graph.node(self._node_id)
        if node is None:
            return

        value = edit_expression(
            self,
            operator=node.operator,
            current=field.text().strip(),
            input_path=self.dock.graph.source_path_for(self._node_id),
            binary=getattr(self.dock.main_window.NCExplorer,
                           "NCExplorer_binary", "cdo"),
        )
        if value is not None:
            field.setText(value)
            self._commit_parameter(index, value)

    # -- committing -----------------------------------------------------
    def _commit(self, **fields) -> None:
        if not self._node_id or self._node_id not in self.dock.graph:
            return
        node = self.dock.graph.node(self._node_id)
        if node is None:
            return
        if all(getattr(node, key) == value for key, value in fields.items()):
            return  # editingFinished fires on focus loss too, unchanged text and all
        self.dock.graph.update_node(self._node_id, **fields)
        self.edited.emit(self._node_id)

    def _commit_parameter(self, index: int, value: str) -> None:
        node = self.dock.graph.node(self._node_id) if self._node_id else None
        if node is None:
            return
        parameters = list(node.parameters)
        while len(parameters) <= index:
            parameters.append("")
        if parameters[index] == value:
            return
        parameters[index] = value
        self.dock.graph.update_node(self._node_id, parameters=tuple(parameters))
        self.edited.emit(self._node_id)

    def _browse_into(self, field: QLineEdit, kind: str) -> None:
        if kind == SOURCE:
            path, _ = QFileDialog.getOpenFileName(
                self, "Choose an input file", field.text(), FILE_FILTER,
                options=QFileDialog.Option.DontUseNativeDialog)
        else:
            # The written formats, which are a smaller set than the read ones:
            # a sink named ``out.cdf`` is silently written as NetCDF4 rather
            # than as what its name says.
            path, _ = QFileDialog.getSaveFileName(
                self, "Choose an output file", field.text(), ft.OUTPUT_FILTER,
                options=QFileDialog.Option.DontUseNativeDialog)
        if path:
            field.setText(path)
            self._commit(path=path)

    def _browse_into_text(self, field: QLineEdit) -> None:
        """Choose where an info operator's printed reading is kept."""
        path, _ = QFileDialog.getSaveFileName(
            self, "Save the printed output as", field.text() or f"output{TEXT_SUFFIX}",
            TEXT_FILTER, options=QFileDialog.Option.DontUseNativeDialog)
        if path:
            path = _as_text_path(path)
            field.setText(path)
            self._commit(path=path)

    def _browse_into_parameter(self, field: QLineEdit, index: int, *,
                               file_kind: str = ft.ANY) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose a file", field.text(), ft.dialog_filter(file_kind),
            options=QFileDialog.Option.DontUseNativeDialog)
        if path:
            field.setText(path)
            self._commit_parameter(index, path)

    def _use_active_layer(self) -> None:
        path = self.dock.active_layer_path()
        if not path:
            QMessageBox.information(self.dock, "Model Builder",
                                    "No layer is selected on the map.")
            return
        self._commit(path=path)
        self.show_node(self._node_id)


# ---------------------------------------------------------------------------
# Picking many inputs at once
# ---------------------------------------------------------------------------

class FolderInputDialog(QDialog):
    """Choose a folder, then choose which of its files to read.

    The variable-arity operators are the ones this exists for. ``merge``,
    ``cat`` and ``mergetime`` routinely take a decade of monthly files, and
    adding thirty source nodes one file dialog at a time is not a workflow —
    which is also why the "Select all" is the point of the dialog rather than a
    convenience on it.

    Listing is delegated to ``core/batch.discover_inputs``, so a folder shows the
    same files here as it would in a batch: sorted, directories excluded, and
    the same glob. Two places disagreeing about what "the .nc files in this
    folder" means is exactly the sort of difference nobody would think to check.
    """

    #: Patterns offered, commonest first. ``*`` is last and deliberate: an
    #: unfiltered listing is occasionally what somebody wants and never what
    #: they want by default.
    PATTERNS = ("*.nc", "*.nc4", "*.grb", "*.grib", "*.nc *.nc4", "*")

    def __init__(self, parent, start_directory: str = "", *, pattern: str = "",
                 selected: Sequence[str] | None = None):
        super().__init__(parent)
        self.setWindowTitle("Choose input files")
        self.setMinimumSize(680, 480)

        self._paths: list[str] = []
        #: Set when re-opening on an existing folder node: only these files are
        #: ticked, so editing a selection does not silently re-tick everything
        #: the user had deliberately left out.
        self._preselected = set(selected) if selected is not None else None

        layout = QVBoxLayout(self)

        intro = QLabel(
            "Every file you tick becomes an input node. If a many-input operator "
            "is selected on the canvas, they are wired into it in the order shown."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("Folder:"))
        self.folder_field = QLineEdit(start_directory)
        self.folder_field.textChanged.connect(self._refresh)
        folder_row.addWidget(self.folder_field, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._choose_folder)
        folder_row.addWidget(browse)
        layout.addLayout(folder_row)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Files matching:"))
        self.pattern_box = QComboBox()
        self.pattern_box.setEditable(True)
        self.pattern_box.addItems(self.PATTERNS)
        if pattern:
            self.pattern_box.setCurrentText(pattern)
        self.pattern_box.currentTextChanged.connect(self._refresh)
        filter_row.addWidget(self.pattern_box)

        self.recursive_box = QCheckBox("Include sub-folders")
        self.recursive_box.toggled.connect(self._refresh)
        filter_row.addWidget(self.recursive_box)
        filter_row.addStretch(1)

        self.select_all_button = QPushButton("Select all")
        self.select_all_button.clicked.connect(lambda: self._set_all(True))
        filter_row.addWidget(self.select_all_button)

        self.select_none_button = QPushButton("Select none")
        self.select_none_button.clicked.connect(lambda: self._set_all(False))
        filter_row.addWidget(self.select_none_button)
        layout.addLayout(filter_row)

        self.file_list = QListWidget()
        self.file_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.file_list.itemChanged.connect(lambda _item: self._refresh_summary())
        layout.addWidget(self.file_list, 1)

        self.summary_label = QLabel()
        self.summary_label.setStyleSheet("color: gray; font-size: 10px;")
        layout.addWidget(self.summary_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._ok_button.setText("Add inputs")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._refresh()

    # ------------------------------------------------------------------
    def _choose_folder(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self, "Select a folder", self.folder_field.text(),
            QFileDialog.Option.DontUseNativeDialog)
        if directory:
            self.folder_field.setText(directory)

    def _refresh(self) -> None:
        """Relist the folder. Everything found starts ticked."""
        from ..core.batch import discover_inputs

        folder = self.folder_field.text().strip()
        pattern = self.pattern_box.currentText().strip() or "*"

        # A space-separated pattern is two globs, which is how somebody naturally
        # writes "the NetCDF files, either extension".
        found: list[str] = []
        for part in pattern.split():
            for path in discover_inputs(folder, part, self.recursive_box.isChecked()):
                if path not in found:
                    found.append(path)
        found.sort()

        self.file_list.blockSignals(True)
        self.file_list.clear()
        for path in found:
            item = QListWidgetItem(self._display(path, folder))
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            ticked = (path in self._preselected
                      if self._preselected is not None else True)
            item.setCheckState(Qt.CheckState.Checked if ticked
                               else Qt.CheckState.Unchecked)
            item.setToolTip(path)
            self.file_list.addItem(item)
        self.file_list.blockSignals(False)

        self._paths = found
        self._refresh_summary()

    def folder(self) -> str:
        """The folder these files were chosen from."""
        return self.folder_field.text().strip()

    def pattern(self) -> str:
        """The glob they were chosen with, so re-opening shows the same listing."""
        return self.pattern_box.currentText().strip()

    @staticmethod
    def _display(path: str, folder: str) -> str:
        """The name, or the relative path when sub-folders are in play."""
        try:
            relative = Path(path).relative_to(folder)
        except ValueError:
            return Path(path).name
        return str(relative)

    def _set_all(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        self.file_list.blockSignals(True)
        for row in range(self.file_list.count()):
            self.file_list.item(row).setCheckState(state)
        self.file_list.blockSignals(False)
        self._refresh_summary()

    def _refresh_summary(self) -> None:
        chosen = len(self.selected_paths())
        if not self._paths:
            folder = self.folder_field.text().strip()
            self.summary_label.setText(
                "Nothing matches in that folder."
                if folder else "Choose a folder to list its files.")
        else:
            self.summary_label.setText(
                f"{chosen} of {len(self._paths)} file(s) selected.")
        self._ok_button.setEnabled(chosen > 0)

    def selected_paths(self) -> list[str]:
        """The ticked files, in the order they are listed."""
        return [
            self.file_list.item(row).data(Qt.ItemDataRole.UserRole)
            for row in range(self.file_list.count())
            if self.file_list.item(row).checkState() == Qt.CheckState.Checked
        ]


# ---------------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------------

class ModelBuilderWindow(QMainWindow):
    """Compose a processing chain visually, then run it as one job.

    A window of its own rather than a dock. The other analysis panels are docks
    because they annotate the map — a plot or a statistics table only means
    anything beside the layer it describes. A model is the opposite: while it is
    being drawn the map is irrelevant, and the canvas wants every pixel it can
    get. Being a real ``QMainWindow`` is also what supplies what a dock cannot:
    an ordinary title bar, and the minimise and maximise that come with it.

    Parented to the main window so it stays associated with it — it is raised
    with the application, closes with it, and never turns up as an orphan in the
    window list — but ``Qt.WindowType.Window`` makes it top-level rather than a
    panel glued to an edge, with the minimise and maximise the window manager
    supplies.
    """

    #: Emitted when the window is shown or hidden, so the menu entry can follow
    #: it. Named to match ``QDockWidget.visibilityChanged``, which is what the
    #: main window's other panels connect to.
    visibilityChanged = pyqtSignal(bool)

    def __init__(self, main_window):
        super().__init__(main_window)
        self.setWindowTitle("Model Builder")
        self.setObjectName("ModelBuilderWindow")
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
            | Qt.WindowType.WindowCloseButtonHint
        )

        self.main_window = main_window
        self.graph = ModelGraph()
        self.catalog = OperatorCatalog.from_integration(
            getattr(main_window, "NCExplorer", None))
        self.runner = ModelRunner(main_window, self)
        self._path = ""
        self._issues: list[ValidationIssue] = []
        self._fused = False

        self._validate_timer = QTimer(self)
        self._validate_timer.setSingleShot(True)
        self._validate_timer.setInterval(VALIDATE_DELAY)
        self._validate_timer.timeout.connect(self._revalidate)

        self._build_ui()

        self.runner.step_started.connect(self._on_step_started)
        self.runner.step_finished.connect(self._on_step_finished)
        self.runner.failed.connect(self._on_failed)
        self.runner.finished.connect(self._on_finished)
        self.runner.cancelled.connect(self._on_cancelled)

        self._revalidate()
        self.hide()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        layout.addLayout(self._build_toolbar())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_palette())

        self.canvas = ModelCanvas(self.graph, self.catalog, self)
        self.canvas.changed.connect(self._on_graph_changed)
        self.canvas.selected.connect(self._on_selected)
        splitter.addWidget(self.canvas)

        splitter.addWidget(self._build_side_panel())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 3)
        splitter.setStretchFactor(2, 1)
        splitter.setSizes([200, 640, 320])
        layout.addWidget(splitter, 1)

        self.status_label = QLabel("Drag an operator onto the canvas to begin.")
        self.status_label.setStyleSheet("color: gray; font-size: 10px;")
        layout.addWidget(self.status_label)

        self.setCentralWidget(container)
        self.resize(1280, 720)

    # ------------------------------------------------------------------
    # Window lifecycle
    # ------------------------------------------------------------------
    def showEvent(self, event):
        super().showEvent(event)
        self.visibilityChanged.emit(True)

    def hideEvent(self, event):
        super().hideEvent(event)
        self.visibilityChanged.emit(False)

    def closeEvent(self, event):
        """Closing hides the window; the model it holds is not thrown away.

        A model lives as long as the project does — it is saved into the ``.ncx``
        and restored from it — so closing this window is closing a view of it,
        not discarding it.
        """
        event.ignore()
        self.hide()

    def _build_toolbar(self) -> QHBoxLayout:
        row = QHBoxLayout()

        add_input = QPushButton("Add input")
        add_input.setToolTip("Add a file to read from (or drop a .nc file on the canvas)")
        add_input.clicked.connect(lambda: self._add_file_node(SOURCE))
        row.addWidget(add_input)

        add_folder = QPushButton("Add inputs from folder…")
        add_folder.setToolTip(
            "Pick a folder and choose which of its files to read — the quick way "
            "to feed a many-input operator like merge or cat")
        add_folder.clicked.connect(self.add_inputs_from_folder)
        row.addWidget(add_folder)

        add_output = QPushButton("Add output")
        add_output.setToolTip("Add a file to write the result to")
        add_output.clicked.connect(lambda: self._add_file_node(SINK))
        row.addWidget(add_output)

        use_layer = QPushButton("Use active layer")
        use_layer.setToolTip("Add an input node for the layer selected on the map")
        use_layer.clicked.connect(self._add_active_layer)
        row.addWidget(use_layer)

        row.addSpacing(12)

        self.from_session_button = QPushButton("From session")
        self.from_session_button.setToolTip(
            "Turn this session's recorded steps into a model you can branch")
        self.from_session_button.clicked.connect(self.build_from_session)
        row.addWidget(self.from_session_button)

        open_button = QPushButton("Open…")
        open_button.clicked.connect(self.open_model)
        row.addWidget(open_button)

        save_button = QPushButton("Save…")
        save_button.clicked.connect(self.save_model_as)
        row.addWidget(save_button)

        export_button = QPushButton("Export ▾")
        export_menu = QMenu(export_button)
        for fmt, label, _filter, _suggestion in EXPORT_FORMATS:
            action = export_menu.addAction(label)
            # default=fmt binds the loop variable per iteration.
            action.triggered.connect(lambda _checked, f=fmt: self.export_as(f))
        export_button.setMenu(export_menu)
        row.addWidget(export_button)

        self.batch_button = QPushButton("Run over a folder…")
        self.batch_button.setToolTip(
            "Hand this model to the batch dialog and apply it to every file in a folder")
        self.batch_button.clicked.connect(self.send_to_batch)
        row.addWidget(self.batch_button)

        row.addStretch(1)

        clear = QPushButton("Clear")
        clear.clicked.connect(self._clear)
        row.addWidget(clear)

        self.run_button = QPushButton("Run model")
        self.run_button.setDefault(True)
        self.run_button.clicked.connect(self.run)
        row.addWidget(self.run_button)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.runner.cancel)
        row.addWidget(self.cancel_button)
        return row

    def _build_palette(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search operators…")
        self.search.setClearButtonEnabled(True)
        self.search.setToolTip(
            "Filter the palette by operator name or by what the operator does"
        )
        self.search.textChanged.connect(self._filter_palette)
        layout.addWidget(self.search)

        self.palette = OperatorTree()
        self.palette.setHeaderHidden(True)
        self.palette.setDragEnabled(True)
        self.palette.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.palette.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.palette.itemDoubleClicked.connect(self._palette_activated)
        layout.addWidget(self.palette, 1)

        hint = QLabel("Double-click or drag onto the canvas")
        hint.setStyleSheet("color: gray; font-size: 10px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._fill_palette()
        return panel

    def _fill_palette(self) -> None:
        """Group the operator index by category.

        ``build_entries`` is the palette's index, reused rather than rebuilt: it
        already prefers the installed catalog over the static one and already
        carries the description and signature this tree wants.
        """
        entries = build_entries(getattr(self.main_window, "NCExplorer", None))
        self._palette_entries = entries

        grouped: dict[NCExplorerCategory, list] = {}
        for entry in entries:
            grouped.setdefault(entry.category, []).append(entry)

        self.palette.clear()
        self._palette_items: dict[str, QTreeWidgetItem] = {}
        for category in NCExplorerCategory:
            members = grouped.get(category)
            if not members:
                continue
            parent = QTreeWidgetItem(self.palette, [category.value])
            try:
                parent.setIcon(0, category_icon(category))
            except KeyError:
                logger.debug("No icon for category %s", category)
            for entry in members:
                label = f"{entry.name}   {entry.signature}"
                tooltip = f"{entry.name} ({entry.signature})\n{entry.description}"
                if entry.unavailable:
                    label += f"   ({entry.unavailable})"
                    tooltip = (f"{entry.name} ({entry.signature})\n"
                               f"{entry.unavailable_detail}")
                child = QTreeWidgetItem(parent, [label])
                child.setData(0, Qt.ItemDataRole.UserRole, entry.name)
                child.setToolTip(0, tooltip)
                if entry.unavailable:
                    # Disabled rather than hidden, for the reason given in
                    # ``toolbar._add_operator_actions``. Drag and selection are
                    # cleared alongside enabled rather than left to follow from
                    # it: this tree is a drag source (see :class:`OperatorTree`)
                    # and a node dropped into a graph is the one way into a
                    # build gap that nothing later catches cheaply — the model
                    # runs the whole pipeline. Stating all three in the flags
                    # is what makes that true by construction instead of by
                    # Qt's selection rules.
                    child.setFlags(child.flags()
                                   & ~Qt.ItemFlag.ItemIsEnabled
                                   & ~Qt.ItemFlag.ItemIsSelectable
                                   & ~Qt.ItemFlag.ItemIsDragEnabled)
                self._palette_items[entry.name] = child

        gated = sum(1 for entry in entries if entry.unavailable)
        logger.debug("Model builder palette indexed %d operators, %d disabled by "
                     "a missing build feature", len(entries), gated)

    def _filter_palette(self, text: str) -> None:
        """Hide what does not match, keeping a category visible if anything in it does.

        Matching is the command palette's ranking rather than a substring test,
        so "tmn" finds ``timmean`` here exactly as it does under Ctrl+K and in
        the toolbar's search box. One search behaviour across all three
        surfaces, since all three are searching the same index.
        """
        needle = text.strip()
        matched = None
        if needle:
            matched = {
                match.entry.name for match in
                rank_entries(self._palette_entries, needle,
                             limit=len(self._palette_entries))
            }

        for index in range(self.palette.topLevelItemCount()):
            parent = self.palette.topLevelItem(index)
            shown = 0
            for row in range(parent.childCount()):
                child = parent.child(row)
                name = child.data(0, Qt.ItemDataRole.UserRole) or ""
                match = matched is None or name in matched
                child.setHidden(not match)
                shown += int(match)
            parent.setHidden(shown == 0)
            parent.setExpanded(bool(needle) and shown > 0)

    def _palette_activated(self, item: QTreeWidgetItem, _column: int) -> None:
        operator = item.data(0, Qt.ItemDataRole.UserRole)
        if operator:
            self.canvas.add_operator(operator)

    def _build_side_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.parameters = ParameterEditor(self)
        self.parameters.edited.connect(self._on_node_edited)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.parameters)
        scroll.setMinimumHeight(160)
        layout.addWidget(QLabel("<b>Selected node</b>"))
        layout.addWidget(scroll, 2)

        layout.addWidget(QLabel("<b>Issues</b>"))
        self.issue_list = QListWidget()
        self.issue_list.setMaximumHeight(120)
        self.issue_list.itemClicked.connect(self._on_issue_clicked)
        self.issue_list.itemDoubleClicked.connect(self._on_issue_activated)
        layout.addWidget(self.issue_list, 1)

        header = QHBoxLayout()
        header.addWidget(QLabel("<b>Commands</b>"))
        header.addStretch(1)
        self.fuse_box = QCheckBox("Chained")
        self.fuse_box.setToolTip(
            "Show the equivalent nested one-liners. These are what you could run "
            "by hand; the model itself runs one operator per step so every "
            "intermediate stays inspectable."
        )
        self.fuse_box.toggled.connect(self._on_fuse_toggled)
        header.addWidget(self.fuse_box)
        layout.addLayout(header)

        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.preview.setStyleSheet("font-family: monospace; font-size: 11px;")
        layout.addWidget(self.preview, 2)
        return panel

    # ------------------------------------------------------------------
    # Reacting to edits
    # ------------------------------------------------------------------
    def _on_graph_changed(self) -> None:
        self._validate_timer.start()

    def _on_selected(self, node_id: str) -> None:
        self.parameters.show_node(node_id)

    def _on_node_edited(self, node_id: str) -> None:
        self.canvas.refresh_node(node_id)
        self.parameters.show_node(node_id)
        self._validate_timer.start()

    def _on_fuse_toggled(self, checked: bool) -> None:
        self._fused = checked
        self._refresh_preview()

    def _revalidate(self) -> None:
        self._issues = self.graph.validate(self.catalog)
        self.canvas.mark_issues(self._issues)

        self.issue_list.clear()
        for issue in self._issues:
            item = QListWidgetItem(
                ("✗ " if issue.is_error else "⚠ ") + issue.message)
            item.setData(Qt.ItemDataRole.UserRole, issue)
            item.setForeground(QColor("#d46a6a") if issue.is_error else QColor("#c8a04a"))
            if issue.suggestion:
                item.setToolTip("Double-click to apply the suggested name")
            self.issue_list.addItem(item)

        self._refresh_preview()
        self._refresh_status()

    def _refresh_preview(self) -> None:
        """Show what the graph compiles to, refusing to guess when it cannot.

        Compiled with a naming allocator rather than the runner's store: a
        preview must not create a temporary file every time a key is pressed,
        and a name that says what the file *is* reads better than one that says
        where it would land.
        """
        if any(issue.is_error for issue in self._issues):
            self.preview.setPlainText(
                "Fix the errors listed above and the commands will appear here.")
            return

        try:
            if self._fused:
                lines = [" ".join(argv) for argv in fused_commands(self.graph, self.catalog)]
            else:
                counter = {"n": 0}

                def name(suffix: str) -> str:
                    counter["n"] += 1
                    return f"<intermediate{counter['n']}{suffix}>"

                lines = [request.command_line()
                         for request in self.graph.compile(name, self.catalog)]
        except Exception as exc:
            logger.debug("Could not build the command preview", exc_info=True)
            self.preview.setPlainText(f"Could not build a preview: {exc}")
            return

        if not lines:
            self.preview.setPlainText("Nothing to run yet.")
            return
        self.preview.setPlainText("\n".join(lines))

    def _refresh_status(self) -> None:
        errors = sum(1 for issue in self._issues if issue.is_error)
        warnings = len(self._issues) - errors
        operators = sum(1 for node in self.graph.nodes if node.kind == OPERATOR)

        if errors:
            summary = (f"{operators} operator(s), {errors} error(s)"
                       + (f" and {warnings} warning(s)" if warnings else "")
                       + " — the run is blocked until the errors are fixed.")
        elif operators:
            summary = (f"{operators} operator(s), ready to run"
                       + (f", {warnings} warning(s)" if warnings else "") + ".")
        else:
            summary = "Drag an operator onto the canvas to begin."
        self.status_label.setText(summary)
        self.run_button.setEnabled(bool(operators) and not errors
                                   and not self.runner.is_running())
        self.batch_button.setEnabled(bool(operators) and not errors)

    def _on_issue_clicked(self, item: QListWidgetItem) -> None:
        issue = item.data(Qt.ItemDataRole.UserRole)
        if issue is not None and issue.node:
            self.canvas.focus_node(issue.node)

    def _on_issue_activated(self, item: QListWidgetItem) -> None:
        """Apply the suggested fix, for the issues that carry one."""
        issue = item.data(Qt.ItemDataRole.UserRole)
        if issue is None or not issue.suggestion or not issue.node:
            return
        self.graph.update_node(issue.node, path=issue.suggestion)
        self.canvas.refresh_node(issue.node)
        self.parameters.show_node(self.canvas.selected_node())
        self._revalidate()

    # ------------------------------------------------------------------
    # Building the graph
    # ------------------------------------------------------------------
    def _add_file_node(self, kind: str) -> None:
        if kind == SOURCE:
            path, _ = QFileDialog.getOpenFileName(
                self, "Choose an input file", "", FILE_FILTER,
                options=QFileDialog.Option.DontUseNativeDialog)
        else:
            path, _ = QFileDialog.getSaveFileName(
                self, "Choose an output file", "", ft.OUTPUT_FILTER,
                options=QFileDialog.Option.DontUseNativeDialog)
        if not path:
            return
        self.canvas.add_file_node(kind, path)

    def add_inputs_from_folder(self, target_id: str = "") -> str:
        """Add **one** folder node holding the chosen files. Returns its id.

        One node and one wire, not one per file. Thirty source boxes fanning into
        a ``mergetime`` is thirty wires to draw, a canvas nobody can read, and
        thirty things to re-point when the data moves — where a folder is a
        single thing to connect onward and a single thing to edit.

        ``target_id`` names the operator to feed; with none given, the selected
        node is used when it can take more inputs.
        """
        dialog = FolderInputDialog(self, self._start_directory())
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return ""

        paths = dialog.selected_paths()
        if not paths:
            return ""

        target = target_id or self._fillable_target()
        node = self.graph.add(
            FOLDER,
            path=dialog.folder(),
            paths=tuple(paths),
            pattern=dialog.pattern(),
            position=(self._free_column(), 0.0),
        )
        if target:
            try:
                self.graph.connect(node.id, 0, target,
                                   len(self.graph.incoming(target)))
            except ModelError as exc:
                # A fixed-arity operator that filled up between the menu opening
                # and the dialog closing. The folder is kept: it is on the canvas
                # and can be wired wherever it was meant to go.
                logger.debug("Could not wire the folder into %s: %s", target, exc)

        self.canvas.rebuild()
        self.canvas.focus_node(node.id)
        self.canvas.fit_to_content()
        self._on_graph_changed()

        wired = f", wired into {self.graph.node(target).title}" if target else ""
        self.main_window.statusBar().showMessage(
            f"Folder added with {len(paths)} file(s){wired}", 5000)
        return node.id

    def insert_companion(self, target_id: str, operator: str, *,
                         parameters: Sequence[str] = (),
                         from_port: int = 0, into_port: int = 1) -> str:
        """Branch one of the target's inputs through ``operator`` into another.

        The ``*arith`` operators do not take two data series. ``ymonsub`` wants a
        time series and a file holding one field per month of the year, and the
        documented way to get the second is to run ``ymonavg`` over the first —
        ``cdo ymonsub infile -ymonavg infile outfile``. That is a shape the graph
        can build exactly: the same source feeds both the operator and the
        statistics node that feeds its second input.

        ``from_port`` and ``into_port`` are what make that shape general instead
        of one hard-coded case. Conditional selection runs backwards: ``ifthen``
        takes the mask in port 0 and the data in port 1, and it is the mask that
        is derived, so the branch reads port 1 and fills port 0. With the old
        fixed 0 → 1 wiring the button would have taken the mask as its source
        and overwritten the data with a mask built from a mask — a graph that
        runs, and is wrong, which is worse than no button at all.

        The source is chosen by port rather than by taking the first incoming
        connection, for the same reason: with only port 1 filled, ``incoming[0]``
        is that connection whatever port it targets, so the old code would have
        branched from whatever happened to be wired.

        ``parameters`` are the new node's own, so ``gtc,0`` arrives as a ``gtc``
        node with "0" already in its field rather than as one the validator
        immediately flags as incomplete.

        Returns the new node's id, or "" when there is nothing to branch from.
        """
        source = next((connection
                       for connection in self.graph.incoming(target_id)
                       if connection.target_port == from_port), None)
        if source is None:
            return ""

        node = self.graph.add(
            OPERATOR, operator=operator,
            parameters=tuple(parameters),
            position=(self._free_column(), 120.0),
        )
        try:
            self.graph.connect(source.source, source.source_port, node.id, 0)
            self.graph.connect(node.id, 0, target_id, into_port)
        except ModelError as exc:
            # Wiring is the whole point of the button, so a node that could not
            # be wired is not worth leaving on the canvas.
            logger.debug("Could not wire %s into %s: %s", operator, target_id, exc)
            self.graph.remove_node(node.id)
            return ""

        self.canvas.rebuild()
        self.canvas.focus_node(node.id)
        self.canvas.fit_to_content()
        self._on_graph_changed()

        target = self.graph.node(target_id)
        self.main_window.statusBar().showMessage(
            f"Added {operator} and wired it into {target.title}", 5000)
        return node.id

    def edit_folder_node(self, node_id: str) -> None:
        """Re-open the picker on an existing folder node, keeping its wires.

        Editing rather than replacing is the point: the connection onward has
        already been drawn, and re-picking the files must not cost the user that.
        """
        node = self.graph.node(node_id)
        if node is None or node.kind != FOLDER:
            return

        dialog = FolderInputDialog(self, node.path, pattern=node.pattern,
                                   selected=node.paths)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        paths = dialog.selected_paths()
        if not paths:
            return

        self.graph.update_node(node_id, path=dialog.folder(), paths=tuple(paths),
                               pattern=dialog.pattern())
        self.canvas.refresh_node(node_id)
        self.parameters.show_node(node_id)
        self._on_graph_changed()
        self.main_window.statusBar().showMessage(
            f"Folder now holds {len(paths)} file(s)", 5000)

    def _fillable_target(self) -> str:
        """The selected operator, if it has room for more inputs.

        Variable-arity operators always have room. A fixed-arity one has room
        only while some of its ports are empty, and offering to fill a full
        ``sub`` would mean silently dropping every file after the first.
        """
        node_id = self.canvas.selected_node()
        node = self.graph.node(node_id) if node_id else None
        if node is None or node.kind != OPERATOR:
            return ""
        nin = (self.catalog.signature(node.operator) or (1, 1))[0]
        if nin == -1:
            return node_id
        return node_id if self.graph.input_count(node_id) < nin else ""

    def _free_column(self) -> float:
        """An x for a new column of sources, left of everything already drawn."""
        positions = [node.position[0] for node in self.graph.nodes]
        return min(positions) - 260.0 if positions else 0.0

    def _start_directory(self) -> str:
        """Where the folder dialog should open: wherever the model already reads."""
        for node in self.graph.nodes:
            if node.kind == SOURCE and node.path:
                return str(Path(node.path).parent)
        active = self.active_layer_path()
        return str(Path(active).parent) if active else str(Path.home())

    def _add_active_layer(self) -> None:
        path = self.active_layer_path()
        if not path:
            QMessageBox.information(self, "Model Builder",
                                    "No layer is selected on the map.")
            return
        self.canvas.add_file_node(SOURCE, path)

    def active_layer_path(self) -> str:
        """The file behind the layer selected on the map, or ""."""
        name = getattr(self.main_window, "current_layer", None)
        canvas = getattr(self.main_window, "geo_canvas", None)
        if not name or canvas is None or name not in getattr(canvas, "layers", {}):
            return ""
        return canvas.layers[name].get("filepath", "") or ""

    def build_from_session(self) -> None:
        """Turn the recorded session into a graph.

        The session log's steps are already ``OperatorRequest``s, so this is
        nearly free — and a recorded chain the user can then branch is the
        gentlest way into an editor they have never opened.
        """
        from ..core.model import graph_from_pipeline

        dock = getattr(self.main_window, "session_dock", None)
        pipeline = [step.request for step in dock.log.steps if step.succeeded] if dock else []
        if not pipeline:
            QMessageBox.information(
                self, "Model Builder",
                "There are no successful steps in this session to build from.")
            return
        if len(self.graph) and not self._confirm_discard("Replace the current model"):
            return
        self.set_graph(graph_from_pipeline(pipeline))

    def _clear(self) -> None:
        if len(self.graph) and not self._confirm_discard("Clear the model"):
            return
        self.set_graph(ModelGraph())

    def _confirm_discard(self, title: str) -> bool:
        answer = QMessageBox.question(
            self, title,
            f"{title}? The current model has {len(self.graph)} node(s) and has "
            "not necessarily been saved.")
        return answer == QMessageBox.StandardButton.Yes

    def set_graph(self, graph: ModelGraph) -> None:
        """Replace the model wholesale — a load, a project restore, or Clear."""
        self.graph = graph
        self.canvas.graph = graph
        self.canvas.rebuild()
        self.canvas.fit_to_content()
        self.parameters.show_node("")
        self._revalidate()

    # ------------------------------------------------------------------
    # Running
    # ------------------------------------------------------------------
    def run(self) -> None:
        if self.runner.is_running():
            return
        if self.main_window.execution.is_running():
            QMessageBox.information(self, "Model Builder",
                                    "Wait for the running operation to finish first.")
            return

        # Revalidated here rather than trusting the debounced copy: an edit made
        # in the last fraction of a second must not be the one that gets past.
        self._issues = self.graph.validate(self.catalog)
        blocking = [issue for issue in self._issues if issue.is_error]
        if blocking:
            self._revalidate()
            QMessageBox.warning(
                self, "Model Builder",
                "This model cannot run yet:\n\n"
                + "\n".join(f"• {issue.message}" for issue in blocking[:6]))
            return

        if not self._settle_extensions():
            return

        requests = self.runner.compile(self.graph, self.catalog)
        if not requests:
            return

        self.canvas.clear_statuses()
        self.main_window.output_console.append(
            f"▶ Running model — {len(requests)} step(s)")
        if not self.runner.run():
            self.main_window.output_console.append("✗ The model could not be started")
            return

        self.run_button.setEnabled(False)
        self.cancel_button.setEnabled(True)

    def _settle_extensions(self) -> bool:
        """Ask about output names the engine would not write as they read.

        False when the user backed out. This is a *stop* rather than a warning in
        the list because of what the mistake costs: the run succeeds, the file
        appears, and it is NetCDF4 under a name that says GRIB or says nothing —
        which nothing downstream can detect and nobody notices until the file is
        opened somewhere else, possibly weeks later. It is not made an error
        during editing because a half-typed name is not a mistake yet.
        """
        issues = [issue for issue in self._issues if issue.kind == EXTENSION]
        if not issues:
            return True

        listing = "\n".join(
            f"• {Path(self.graph.node(issue.node).path).name}  →  "
            f"{Path(issue.suggestion).name}"
            for issue in issues if self.graph.node(issue.node) is not None
        )
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Check the output names")
        box.setText(
            f"{len(issues)} output name(s) have no extension the engine "
            f"recognises.\n\nIt decides the format from the extension and from "
            f"nothing else, so each of these would be written as NetCDF4 under a "
            f"name that says otherwise."
        )
        box.setInformativeText(listing)
        fix = box.addButton("Fix and run", QMessageBox.ButtonRole.AcceptRole)
        anyway = box.addButton("Run anyway", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(fix)
        box.exec()

        clicked = box.clickedButton()
        if clicked is fix:
            self.graph.apply_suggestions(issues)
            for issue in issues:
                self.canvas.refresh_node(issue.node)
            self.parameters.show_node(self.canvas.selected_node())
            self._revalidate()
            return True
        if clicked is anyway:
            logger.info("Running with %d unrecognised output extension(s)", len(issues))
            return True
        return False

    def _on_step_started(self, index: int, total: int, operator: str) -> None:
        self.canvas.set_status(self.runner.node_for(index), RUNNING)
        self.status_label.setText(f"Step {index + 1}/{total}: {operator}")
        self.main_window.statusBar().showMessage(
            f"Model step {index + 1}/{total}: {operator}", 5000)

    def _on_step_finished(self, index: int, _result) -> None:
        self.canvas.set_status(self.runner.node_for(index), DONE)

    def _on_failed(self, index: int, operator: str, message: str) -> None:
        self.canvas.set_status(self.runner.node_for(index), FAILED)
        self._settle()
        self.main_window.output_console.append(
            f"✗ Model stopped at step {index + 1} ({operator}): {message}")
        QMessageBox.critical(
            self, "Model failed",
            f"Step {index + 1} ({operator}) failed, so the run stopped there.\n\n{message}")

    def _on_finished(self, count: int) -> None:
        self._settle()
        self.main_window.output_console.append(f"✓ Model finished — {count} step(s)")
        self._offer_results()

    def _on_cancelled(self) -> None:
        self._settle()
        self.canvas.clear_statuses()
        self.main_window.output_console.append("■ Model run cancelled")

    def _settle(self) -> None:
        self.cancel_button.setEnabled(False)
        self._refresh_status()

    def _offer_results(self) -> None:
        """Hand the outputs to the window the way a single run does.

        Deliberately *not* loaded onto the map here. ``handle_operation_finished``
        sets ``current_output_file`` and enables Visualise rather than drawing the
        result itself, and following that is worth more than the convenience:
        ``GeoCanvas.load_file`` reads and renders the whole field on the calling
        thread, which is the GUI thread, so a model that finishes with four
        outputs would freeze the window for as long as all four take — and this
        is the one module in the feature that had promised not to block.
        """
        produced = [
            node.path for node in self.graph.nodes
            if node.kind == SINK and node.path and Path(node.path).exists()
        ]
        if not produced:
            return

        window = self.main_window
        window.current_output_file = produced[-1]
        window.save_btn.setEnabled(True)
        window.visualize_btn.setEnabled(True)
        for path in produced:
            window.output_console.append(f"Output saved to: {path}")
        window.statusBar().showMessage(
            f"Model finished — {len(produced)} output(s); press Visualise to draw "
            f"{Path(produced[-1]).name}", 8000)

    # ------------------------------------------------------------------
    # Persistence, export and handoff
    # ------------------------------------------------------------------
    def open_model(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open model", "", MODEL_FILTER,
            options=QFileDialog.Option.DontUseNativeDialog)
        if not path:
            return
        try:
            graph = load_model(path)
        except ModelError as exc:
            QMessageBox.warning(self, "Open model", str(exc))
            return
        self._path = path
        self.set_graph(graph)
        self.main_window.statusBar().showMessage(f"Model opened from {path}", 5000)

    def save_model_as(self) -> None:
        suggestion = self._path or str(Path.home() / f"model{MODEL_SUFFIX}")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save model", suggestion, MODEL_FILTER,
            options=QFileDialog.Option.DontUseNativeDialog)
        if not path:
            return
        try:
            self._path = save_model(path, self.graph)
        except ModelError as exc:
            QMessageBox.critical(self, "Save model", str(exc))
            return
        self.main_window.statusBar().showMessage(f"Model saved to {self._path}", 5000)

    def compiled_pipeline(self) -> list:
        """The graph as a list of requests, for a project, a batch or an export.

        Compiled through the runner's own temp store so the intermediate paths
        are real ones — an export the user runs tomorrow needs somewhere to put
        them, and a placeholder name would not be a runnable script.
        """
        if self.graph.has_errors(self.catalog):
            return []
        return self.runner.compile(self.graph, self.catalog)

    def export_pipeline(self) -> list:
        """The same chain, named so an exported artefact runs somewhere else.

        Not :meth:`compiled_pipeline`: that puts intermediates in the temp store,
        which is right for a run here and wrong for a script somebody takes to
        another machine.
        """
        if self.graph.has_errors(self.catalog):
            return []
        return self.graph.compile(portable_allocator(self.graph), self.catalog)

    def export_as(self, fmt: str) -> None:
        """Write the model as a shell script, a Makefile or a notebook."""
        from ..core.session_log import OK, SessionStep, export_for

        for name, _label, file_filter, suggestion in EXPORT_FORMATS:
            if name == fmt:
                break
        else:
            raise ValueError(f"Unknown export format: {fmt}")

        requests = self.export_pipeline()
        if not requests:
            QMessageBox.information(
                self, "Export model",
                "There is nothing to export yet, or the model still has errors.")
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "Export model", suggestion, file_filter,
            options=QFileDialog.Option.DontUseNativeDialog)
        if not path:
            return

        # Wrapped as session steps rather than exported by a fourth exporter:
        # the three in core/session_log.py already render an OperatorRequest, and
        # they all take a Sequence[SessionStep].
        steps = [SessionStep(request=request, status=OK) for request in requests]
        version = getattr(getattr(self.main_window, "session_dock", None),
                          "cdo_version", lambda: "")()
        try:
            text = export_for(fmt, steps, cdo_version=version)
            Path(path).write_text(text, encoding="utf-8")
            if fmt == "shell":
                Path(path).chmod(0o755)
        except OSError as exc:
            logger.error("Could not write %s: %s", path, exc)
            QMessageBox.critical(self, "Export failed", f"Could not write {path}:\n{exc}")
            return
        self.main_window.statusBar().showMessage(f"Model exported to {path}", 5000)

    def send_to_batch(self) -> None:
        """Hand the compiled chain to the batch dialog.

        This is where the feature pays off most: draw the chain once, apply it to
        three hundred files. The dialog already knows how to retarget a list of
        requests onto a folder, so there is nothing to add but the handover.
        """
        requests = self.compiled_pipeline()
        if not requests:
            QMessageBox.information(
                self, "Batch Process",
                "Fix the model's errors before running it over a folder.")
            return
        self.main_window.open_batch_dialog(pipeline=requests,
                                           source="this model")


__all__ = ["ModelBuilderWindow", "ModelCanvas", "ParameterEditor",
           "FolderInputDialog", "NodeItem", "EdgeItem"]
