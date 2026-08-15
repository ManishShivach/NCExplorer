"""The processing graph: nodes, wires, and every rule about what makes them run.

A model is what the operator form cannot express. The form runs one operator
against one set of files; a real analysis is four of them, and the intermediate
files exist only because the user had to invent names for them. Worse, the shape
that motivates the whole feature — one input fanning out to two reductions and
recombining through ``sub`` — is not a sequence at all, so no amount of "run this
list in order" reaches it.

Four decisions shape the module, and three of them are refusals:

* **The compiled form is a list of :class:`~.session_log.OperatorRequest`, not a
  representation of its own.** That is what the session log records, what a
  project stores, what the batch runner retargets and what all three exporters
  render. Compiling down to it means batch, export and project storage work the
  day the graph does, with no converter in between — and ``core/batch.py``'s own
  docstring explains what a second representation costs.
* **Nodes are immutable.** Editing a parameter replaces the node rather than
  mutating it, which is what makes an undo stack a list of graphs instead of a
  list of hand-written inverse operations, and what stops a widget holding a
  stale half of one.
* **The order is deterministic, not merely correct.** Two runs of one graph must
  produce byte-identical command sequences: reproducibility is the point of the
  project, and a topological sort that shuffles between runs turns an exported
  script into something that only usually matches what was executed. Ties are
  broken by the order nodes were added, which is stable across a save and load.
* **No type checking beyond arity.** Grids, calendars and variable names are
  CDO's business, decided at run time against the actual file. A static guess
  that is wrong is worse than no guess at all: it blocks a command that would
  have worked, and the user has no way to overrule it.

There are no widgets here, and no Qt at all. ``gui/model_builder.py`` is the
whole of the editor, and ``core/model_runner.py`` — which does need ``QObject``
for its signals — is the whole of the execution driver. This module is what a
test, or a script, can exercise on its own.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from ..core.categories import (
    OPERATOR_SCHEMA, OperatorSpec, expected_plot_files, get_operator_spec,
    missing_required_parameters, operator_env, operator_outputs,
    output_parameter_indexes, reads_stdin, writes_images, writes_output_prefix,
)
from .nc_integration import OUTPUT_EXTENSIONS
from .session_log import CDO, OperatorRequest, operator_token

logger = logging.getLogger(__name__)

#: What a node is. A SOURCE contributes a path, a SINK consumes one, and an
#: OPERATOR is the only kind that becomes an ``OperatorRequest``.
SOURCE = "SOURCE"
OPERATOR = "OPERATOR"
SINK = "SINK"
#: A whole folder's worth of chosen files, standing in for that many sources.
#: Thirty separate source nodes wired one at a time into ``mergetime`` is thirty
#: wires to draw and a canvas nobody can read; this is one box and one wire, and
#: the files inside it stay editable afterwards.
FOLDER = "FOLDER"

NODE_KINDS = (SOURCE, OPERATOR, SINK, FOLDER)

#: The kinds that contribute input paths rather than consuming them.
PRODUCER_KINDS = (SOURCE, FOLDER)

#: Severity of a validation issue. An ERROR stops a run; a WARNING does not.
ERROR = "ERROR"
WARNING = "WARNING"

#: ``ValidationIssue.kind`` for a path the engine would not write in the format
#: its name claims. Distinguished from every other warning because it is the one
#: the run is gated on, and because it always carries a fix.
EXTENSION = "extension"

#: Bumped on any change to what :meth:`ModelGraph.to_dict` writes. Read the same
#: way ``core/project.py`` reads its own: a higher *major* is refused because the
#: file may rely on meaning this build does not have, a higher minor is fine
#: because unknown keys are ignored everywhere below.
MODEL_SCHEMA_VERSION = "1.0"

#: Standalone model files, so a graph can be shared without a whole project.
MODEL_SUFFIX = ".ncmodel"

#: Fallback extension for an intermediate whose eventual format is not knowable.
DEFAULT_SUFFIX = ".nc"

#: The one cross-step invariant this file checks; see ``_validate_environment``.
#: Named here rather than inline because the default is a *measurement* — on CDO
#: 2.6.3, an unset CDO_WEIGHT_MODE produced eigenvalue files byte-identical to
#: ``=off`` and different from ``=on`` in all 648 fields — and a measurement
#: deserves somewhere to be recorded and revisited.
_WEIGHT_MODE = "CDO_WEIGHT_MODE"
_WEIGHT_MODE_DEFAULT = "off"

#: The operators that side of the invariant applies to. Spelled out rather than
#: taken from the EOF category, because the question is which operators *compute
#: a decomposition* and which *project onto one* — a distinction the category
#: does not draw, since it holds both.
_EOF_PRODUCERS = frozenset({
    "eof", "eoftime", "eofspatial", "eof3d", "eof3dtime", "eof3dspatial",
})
_EOFCOEFF = frozenset({"eofcoeff", "eofcoeff3d"})

_ID_PATTERN = re.compile(r"^n(\d+)$")


class ModelError(Exception):
    """A graph edit that cannot be made, or a model file that cannot be read."""


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ModelNode:
    """One box on the canvas.

    ``path`` means different things per kind, which is deliberate: a SOURCE's
    input file, a SINK's output file, and — when ``keep_output`` is set — the
    place an OPERATOR's intermediate is materialised instead of being thrown
    away with the temp store.
    """

    id: str
    kind: str
    operator: str = ""
    parameters: tuple[str, ...] = ()
    path: str = ""
    #: FOLDER only: the files chosen out of ``path``, in the order they feed the
    #: operator. Order is stored rather than re-globbed at run time, because a
    #: file added to that folder next week must not silently change what a saved
    #: model does.
    paths: tuple[str, ...] = ()
    #: FOLDER only: the glob the files were chosen with, remembered so that
    #: reopening the picker shows the same listing rather than the default.
    pattern: str = ""
    #: Write this operator's output where the user asked instead of into the
    #: temp store. The downstream node then reads that same path. For an info
    #: operator, which writes no file at all, ``path`` is instead where its
    #: printed reading is kept, and this flag has no meaning.
    keep_output: bool = False
    position: tuple[float, float] = (0.0, 0.0)
    label: str = ""
    #: Environment variables this step runs under, as ``(name, value)`` pairs.
    #: Separate from ``parameters`` because they are not arguments: they never
    #: reach the command line, and ``parameters`` is positional and becomes the
    #: ``op,a,b`` token. Only the EOFs section declares any; see
    #: ``categories.operator_env``.
    env: tuple[tuple[str, str], ...] = ()
    #: CDO's global options for this step, as separate tokens (``("-f", "nc")``).
    #: Separate from ``parameters`` for exactly the reason ``env`` is, and with a
    #: sharper consequence: options go *before* the operator name and parameters
    #: after it, so putting one in the other's tuple builds a command CDO cannot
    #: parse. ``cdo -f nc import_binary demo.ctl out.nc`` is the shape.
    options: tuple[str, ...] = ()

    @property
    def title(self) -> str:
        """What the user calls this node: their caption, or a sensible default."""
        if self.label:
            return self.label
        if self.kind == OPERATOR:
            return self.operator or "operator"
        if self.kind == FOLDER:
            return Path(self.path).name or "folder" if self.path else "folder"
        if self.path:
            return Path(self.path).name
        return "input" if self.kind == SOURCE else "output"

    @property
    def input_paths(self) -> tuple[str, ...]:
        """Every path this node contributes, in order. Empty for a consumer."""
        if self.kind == FOLDER:
            return self.paths
        if self.kind == SOURCE:
            return (self.path,) if self.path else ()
        return ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "operator": self.operator,
            "parameters": list(self.parameters),
            "path": self.path,
            "paths": list(self.paths),
            "pattern": self.pattern,
            "keep_output": self.keep_output,
            "position": list(self.position),
            "label": self.label,
            # Stored as pairs rather than as an object so the order the user
            # sees is the order that comes back, and so a saved model round-trips
            # through JSON without a dict's key order being load-bearing.
            "env": [list(pair) for pair in self.env],
            "options": list(self.options),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ModelNode":
        """Build from a stored dictionary, ignoring anything unrecognised."""
        position = data.get("position") or ()
        try:
            x, y = (float(value) for value in position)
        except (TypeError, ValueError):
            x, y = 0.0, 0.0

        kind = str(data.get("kind", OPERATOR))
        return cls(
            id=str(data.get("id", "")),
            kind=kind if kind in NODE_KINDS else OPERATOR,
            operator=str(data.get("operator", "")),
            parameters=_strings(data.get("parameters")),
            path=str(data.get("path", "")),
            paths=_strings(data.get("paths")),
            pattern=str(data.get("pattern", "")),
            keep_output=bool(data.get("keep_output", False)),
            position=(x, y),
            label=str(data.get("label", "")),
            env=_env_pairs(data.get("env")),
            options=_strings(data.get("options")),
        )


@dataclass(frozen=True, slots=True)
class Connection:
    """One wire, from an output port to a numbered operand slot.

    ``target_port`` is the field that earns its keep: CDO's arguments are
    positional, so wiring the two branches of a difference into ``sub`` the wrong
    way round produces a perfectly valid command with the sign inverted. There is
    no way to notice that downstream, which is why the port is stored rather than
    inferred from the order the user happened to draw the wires.

    ``source_port`` is 0 for everything CDO currently offers, and is kept anyway
    because a handful of its operators are documented as 1 → 2.
    """

    source: str
    source_port: int
    target: str
    target_port: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "source_port": self.source_port,
            "target": self.target,
            "target_port": self.target_port,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Connection":
        def port(key: str) -> int:
            try:
                return max(0, int(data.get(key, 0)))
            except (TypeError, ValueError):
                return 0

        return cls(
            source=str(data.get("source", "")),
            source_port=port("source_port"),
            target=str(data.get("target", "")),
            target_port=port("target_port"),
        )


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """One reason a graph will not run, or one thing about it worth saying.

    ``message`` is written for the user and names the node by its label, because
    the panel shows this text verbatim beside a node id nobody chose.
    """

    severity: str
    node: str
    message: str
    #: A path this issue proposes instead of the node's current one. Set only by
    #: the extension issues, where the panel can offer to apply it in one click.
    suggestion: str = ""
    #: What sort of issue this is, for callers that need to find one kind of them
    #: rather than reading the message. Only :data:`EXTENSION` is distinguished
    #: today, because that is the one the run is gated on.
    kind: str = ""

    @property
    def is_error(self) -> bool:
        return self.severity == ERROR


# ---------------------------------------------------------------------------
# What the installed CDO can actually do
# ---------------------------------------------------------------------------

class OperatorCatalog:
    """Which operators exist, and what shape each one is.

    The installed binary wins over the static schema — the rule
    ``gui/command_palette.build_entries`` already sets, and for the same reason:
    offering an operator this build does not have produces a failure at run time
    that looks like a bug in the model rather than a missing operator.

    Parameter metadata still comes from ``core/categories.py`` either way.
    ``cdo --operators`` reports arity and a one-line description and nothing
    about trailing parameters, so the schema is the only source for those.
    """

    def __init__(self, signatures: Mapping[str, tuple[int, int]] | None = None):
        #: Empty means "no runtime list available"; the schema is then the whole
        #: truth, which is what a headless test wants.
        self._signatures = dict(signatures or {})

    @classmethod
    def from_integration(cls, integration) -> "OperatorCatalog":
        """The catalog of the CDO this process resolved, or a static one."""
        if integration is None:
            return cls()
        try:
            return cls({
                name: tuple(meta["signature"])  # type: ignore[arg-type]
                for name, meta in integration.get_operator_catalog().items()
            })
        except Exception:
            logger.debug("Runtime operator catalog unavailable; using the static schema",
                         exc_info=True)
            return cls()

    def __contains__(self, name: str) -> bool:
        if self._signatures:
            return name in self._signatures
        return name in OPERATOR_SCHEMA

    def signature(self, name: str) -> tuple[int, int] | None:
        """``(nin, nout)`` for one operator, or None if it is not available."""
        if self._signatures:
            found = self._signatures.get(name)
            if found is not None:
                return (int(found[0]), int(found[1]))
            return None
        spec = get_operator_spec(name)
        return (spec.nin, spec.nout) if spec is not None else None

    def spec(self, name: str) -> OperatorSpec | None:
        """The schema entry, for parameters and the category icon."""
        return get_operator_spec(name)

    def names(self) -> list[str]:
        return sorted(self._signatures) if self._signatures else sorted(OPERATOR_SCHEMA)


#: A catalog that knows only the static schema. Handy for tests and for any
#: caller that has no integration to hand.
STATIC_CATALOG = OperatorCatalog()


# ---------------------------------------------------------------------------
# The graph
# ---------------------------------------------------------------------------

class ModelGraph:
    """Nodes and connections, plus every rule about what makes them runnable."""

    def __init__(self) -> None:
        # Insertion-ordered, and that order is load-bearing: it is what breaks
        # ties in the topological sort, so two runs of one graph compile to the
        # same commands.
        self._nodes: dict[str, ModelNode] = {}
        self._connections: list[Connection] = []
        self._counter = 0

    # -- container ------------------------------------------------------
    def __len__(self) -> int:
        return len(self._nodes)

    def __contains__(self, node_id: str) -> bool:
        return node_id in self._nodes

    @property
    def nodes(self) -> list[ModelNode]:
        """A snapshot in insertion order; mutating it changes nothing."""
        return list(self._nodes.values())

    @property
    def connections(self) -> list[Connection]:
        return list(self._connections)

    def node(self, node_id: str) -> ModelNode | None:
        return self._nodes.get(node_id)

    def copy(self) -> "ModelGraph":
        """An independent graph with the same contents — one undo-stack entry."""
        clone = ModelGraph()
        clone._nodes = dict(self._nodes)
        clone._connections = list(self._connections)
        clone._counter = self._counter
        return clone

    def clear(self) -> None:
        self._nodes.clear()
        self._connections.clear()
        # The counter deliberately survives: an id is never reused, so a node
        # deleted and one added afterwards can never be confused in a log.

    # -- editing --------------------------------------------------------
    def new_id(self) -> str:
        """A node id this graph has never issued before."""
        self._counter += 1
        return f"n{self._counter}"

    def add_node(self, node: ModelNode) -> ModelNode:
        """Add one node. Its id must be free."""
        if not node.id:
            raise ModelError("A node needs an id")
        if node.id in self._nodes:
            raise ModelError(f"Node {node.id} already exists")
        if node.kind not in NODE_KINDS:
            raise ModelError(f"Unknown node kind: {node.kind}")
        self._nodes[node.id] = node
        self._counter = max(self._counter, _id_number(node.id))
        return node

    def add(self, kind: str, **fields: Any) -> ModelNode:
        """Add a node under a freshly issued id. Returns it."""
        return self.add_node(ModelNode(id=self.new_id(), kind=kind, **fields))

    def remove_node(self, node_id: str) -> None:
        """Drop one node and every wire touching it. Unknown ids are ignored."""
        if node_id not in self._nodes:
            return
        del self._nodes[node_id]
        self._connections = [
            connection for connection in self._connections
            if connection.source != node_id and connection.target != node_id
        ]

    def replace_node(self, node: ModelNode) -> ModelNode:
        """Swap a node for an edited copy of itself, keeping its place in the order.

        Nodes are frozen, so every edit — a parameter, a position, a label —
        arrives here. Assigning back into the existing key keeps the insertion
        order, which is what stops an edit quietly changing the compiled order.
        """
        if node.id not in self._nodes:
            raise ModelError(f"Node {node.id} does not exist")
        self._nodes[node.id] = node
        return node

    def update_node(self, node_id: str, **fields: Any) -> ModelNode:
        """Replace one node with a copy carrying ``fields``."""
        current = self._nodes.get(node_id)
        if current is None:
            raise ModelError(f"Node {node_id} does not exist")
        return self.replace_node(replace(current, **fields))

    def connect(self, source: str, source_port: int, target: str,
                target_port: int) -> Connection:
        """Wire one output to one operand slot.

        Raises :class:`ModelError` rather than returning False: every rejection
        here is something the editor must show the user a reason for, and a bare
        False leaves it inventing one.
        """
        if source not in self._nodes:
            raise ModelError(f"Node {source} does not exist")
        if target not in self._nodes:
            raise ModelError(f"Node {target} does not exist")
        if source == target:
            raise ModelError("A node cannot feed itself")

        source_node = self._nodes[source]
        target_node = self._nodes[target]
        if source_node.kind == SINK:
            raise ModelError(f"{source_node.title} is an output and produces nothing")
        if target_node.kind in PRODUCER_KINDS:
            raise ModelError(f"{target_node.title} is an input and takes nothing")

        for connection in self._connections:
            if connection.target == target and connection.target_port == target_port:
                raise ModelError(
                    f"Input {target_port + 1} of {target_node.title} is already connected"
                )
            if (connection.source == source and connection.source_port == source_port
                    and connection.target == target):
                raise ModelError(f"{source_node.title} already feeds {target_node.title}")

        if self._reaches(target, source):
            raise ModelError(
                f"That would make a loop: {target_node.title} already feeds "
                f"{source_node.title}"
            )

        connection = Connection(source, source_port, target, target_port)
        self._connections.append(connection)
        return connection

    def disconnect(self, source: str, source_port: int, target: str,
                   target_port: int) -> bool:
        """Remove one wire. False when there was none."""
        wanted = Connection(source, source_port, target, target_port)
        before = len(self._connections)
        self._connections = [c for c in self._connections if c != wanted]
        return len(self._connections) != before

    def disconnect_port(self, target: str, target_port: int) -> bool:
        """Remove whatever is wired into one operand slot."""
        before = len(self._connections)
        self._connections = [
            c for c in self._connections
            if not (c.target == target and c.target_port == target_port)
        ]
        return len(self._connections) != before

    # -- topology -------------------------------------------------------
    def incoming(self, node_id: str) -> list[Connection]:
        """Wires into ``node_id``, in operand order."""
        found = [c for c in self._connections if c.target == node_id]
        found.sort(key=lambda c: (c.target_port, self._index(c.source)))
        return found

    def outgoing(self, node_id: str) -> list[Connection]:
        """Wires out of ``node_id``, in the order they were drawn."""
        return [c for c in self._connections if c.source == node_id]

    def operands(self, node_id: str) -> list[str]:
        """The node ids feeding ``node_id``, in ``target_port`` order.

        Order is the whole point: this list becomes ``input_files``, and CDO
        reads those positionally.
        """
        return [c.source for c in self.incoming(node_id)]

    def input_count(self, node_id: str) -> int:
        """How many *files* feed ``node_id``, not how many wires.

        A folder node is one wire carrying as many operands as it holds, which
        is the whole reason it is worth having — and the number every arity rule
        below has to be stated in terms of.
        """
        total = 0
        for connection in self.incoming(node_id):
            source = self._nodes.get(connection.source)
            if source is None:
                continue
            total += len(source.input_paths) if source.kind == FOLDER else 1
        return total

    def topological_order(self) -> list[str]:
        """Every node, producers before consumers, deterministically.

        Kahn's algorithm, taking the *earliest-added* of the currently available
        nodes each round rather than whichever the queue happens to surface. A
        plain FIFO is also a valid topological order, but not the same one from
        one build of a graph to the next — and an exported script that does not
        match the run it documents is a reproducibility bug, not a detail.

        A graph containing a cycle yields only the part that is not in or below
        it; :meth:`validate` is what reports the cycle itself.
        """
        indegree = {node_id: 0 for node_id in self._nodes}
        for connection in self._connections:
            if connection.target in indegree and connection.source in indegree:
                indegree[connection.target] += 1

        # Insertion index, so "earliest added" is a cheap integer comparison.
        rank = {node_id: index for index, node_id in enumerate(self._nodes)}
        ready = sorted((node_id for node_id, count in indegree.items() if count == 0),
                       key=rank.__getitem__)

        order: list[str] = []
        while ready:
            node_id = ready.pop(0)
            order.append(node_id)
            freed = []
            for connection in self.outgoing(node_id):
                if connection.target not in indegree:
                    continue
                indegree[connection.target] -= 1
                if indegree[connection.target] == 0:
                    freed.append(connection.target)
            if freed:
                ready = sorted(set(ready) | set(freed), key=rank.__getitem__)
        return order

    def find_cycle(self) -> list[str]:
        """One cycle's node ids, in order, or an empty list.

        :meth:`connect` refuses to create a cycle, so this only ever fires on a
        graph that arrived through :meth:`from_dict` — a hand-edited file, or one
        written by something that did not apply the same rule.
        """
        colour: dict[str, int] = {}
        stack: list[str] = []

        def walk(node_id: str) -> list[str]:
            colour[node_id] = 1
            stack.append(node_id)
            for connection in self.outgoing(node_id):
                target = connection.target
                if target not in self._nodes:
                    continue
                state = colour.get(target, 0)
                if state == 1:
                    return stack[stack.index(target):] + [target]
                if state == 0:
                    found = walk(target)
                    if found:
                        return found
            colour[node_id] = 2
            stack.pop()
            return []

        for node_id in self._nodes:
            if colour.get(node_id, 0) == 0:
                found = walk(node_id)
                if found:
                    return found
        return []

    def _reaches(self, start: str, goal: str) -> bool:
        """True when ``goal`` is downstream of ``start``."""
        seen = {start}
        pending = [start]
        while pending:
            current = pending.pop()
            for connection in self.outgoing(current):
                if connection.target == goal:
                    return True
                if connection.target not in seen:
                    seen.add(connection.target)
                    pending.append(connection.target)
        return False

    def descendants(self, node_id: str) -> list[str]:
        """Every node downstream of ``node_id``, nearest first."""
        seen: list[str] = []
        pending = [node_id]
        while pending:
            current = pending.pop(0)
            for connection in self.outgoing(current):
                if connection.target not in seen:
                    seen.append(connection.target)
                    pending.append(connection.target)
        return seen

    def _index(self, node_id: str) -> int:
        try:
            return list(self._nodes).index(node_id)
        except ValueError:
            return len(self._nodes)

    # -- validation -----------------------------------------------------
    def validate(self, catalog: OperatorCatalog | None = None) -> list[ValidationIssue]:
        """Every reason this graph will not run, and every caution about it.

        All of them, never just the first: the panel lists them, and a user who
        fixes one thing only to be told about the next has to run the whole loop
        again for each mistake.
        """
        catalog = catalog or STATIC_CATALOG
        issues: list[ValidationIssue] = []

        cycle = self.find_cycle()
        if cycle:
            names = " → ".join(self._title(node_id) for node_id in cycle)
            issues.append(ValidationIssue(
                ERROR, cycle[0], f"These nodes feed each other in a loop: {names}"))
            # Everything below is stated in terms of a walkable graph, and a
            # cycle makes most of it meaningless. One clear error beats a dozen
            # confusing consequences of it.
            return issues

        for node in self._nodes.values():
            if node.kind == SOURCE:
                issues.extend(self._validate_source(node))
            elif node.kind == FOLDER:
                issues.extend(self._validate_folder(node))
            elif node.kind == SINK:
                issues.extend(self._validate_sink(node, catalog))
            else:
                issues.extend(self._validate_operator(node, catalog))

            if not self.incoming(node.id) and not self.outgoing(node.id):
                issues.append(ValidationIssue(
                    WARNING, node.id,
                    f"{node.title} is not connected to anything"))

        issues.extend(self._validate_branches(catalog))
        # Last, because it is the only check about a *pair* of steps rather than
        # about one node, and it only makes sense on a graph everything else has
        # already been said about.
        issues.extend(self._validate_environment())
        return issues

    def _validate_source(self, node: ModelNode) -> list[ValidationIssue]:
        if not node.path:
            return [ValidationIssue(ERROR, node.id, f"{node.title} has no file chosen")]
        if not os.path.exists(node.path):
            return [ValidationIssue(
                ERROR, node.id, f"{node.title} does not exist")]
        return []

    def _validate_folder(self, node: ModelNode) -> list[ValidationIssue]:
        """A folder node stands or falls on the files it actually holds.

        The chosen list is checked rather than the folder: a model saved last
        month and opened today may name files that have since been moved, and
        saying which ones is far more use than saying the folder looks wrong.
        """
        if not node.paths:
            return [ValidationIssue(
                ERROR, node.id, f"{node.title} has no files selected")]

        missing = [path for path in node.paths if not os.path.exists(path)]
        if not missing:
            return []

        names = ", ".join(Path(path).name for path in missing[:4])
        if len(missing) > 4:
            names += f" and {len(missing) - 4} more"
        return [ValidationIssue(
            ERROR, node.id,
            f"{node.title}: {len(missing)} of {len(node.paths)} selected file(s) "
            f"no longer exist ({names})")]

    def _validate_sink(self, node: ModelNode,
                       catalog: OperatorCatalog) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        wired = self.incoming(node.id)
        if not wired:
            issues.append(ValidationIssue(
                ERROR, node.id, f"{node.title} has nothing wired into it"))
        if not node.path:
            issues.append(ValidationIssue(
                ERROR, node.id, f"{node.title} has no file name"))
            return issues

        if self._writes_prefix(node.id, catalog):
            # A split operator's target is a *prefix*: it becomes tas_01.nc,
            # tas_02.nc and so on, and the engine supplies the extension itself.
            # Demanding one here would be demanding the wrong thing.
            return issues

        issues.extend(self._extension_issue(node, self._upstream_source_path(node.id)))
        return issues

    def _feeds_an_operator(self, node_id: str) -> bool:
        """True when anything downstream of this node would try to *read* it."""
        return any(
            (target := self._nodes.get(connection.target)) is not None
            and target.kind == OPERATOR
            for connection in self.outgoing(node_id)
        )

    def _writes_prefix(self, sink_id: str, catalog: OperatorCatalog) -> bool:
        """True when what feeds this sink names a base rather than a file.

        Asked of :func:`~.categories.writes_output_prefix` rather than of
        ``nout == -1``, which is what stood here. The two agreed for as long as
        the split family was the only thing that treated its output argument as
        a stem; they stopped agreeing when the six Magics plot operators were
        declared, which are ``nout == 1`` and still write
        ``<obase>_<variable>.<device>``. Left on the arity test, a sink fed by
        ``shaded`` collected the extension warning below — "would be written as
        NetCDF4 under a name that says otherwise" — about an obase that is not
        a filename at all and to which CDO appends its own extension.

        This is the same question the execution layer asks in
        ``_resolve_operator_call``, so it is now asked in the same place; a
        second weaker copy of it here is what produced the disagreement.
        """
        for connection in self.incoming(sink_id):
            source = self._nodes.get(connection.source)
            if source is None or source.kind != OPERATOR:
                continue
            if (catalog.signature(source.operator) or (1, 1))[1] == -1:
                return True
            if writes_output_prefix(source.operator, source.parameters):
                return True
        return False

    def _extension_issue(self, node: ModelNode,
                         source_path: str) -> list[ValidationIssue]:
        """Warn when a path's extension is not one the engine writes.

        The format is inferred from the extension and from nothing else, so an
        unrecognised one is silently written as NetCDF4 under a name that claims
        otherwise. The single-run form closes exactly this trap in
        ``_ensure_output_extension``; here it is closed before the run rather
        than during it, and the fix travels with the warning.
        """
        suggested = suggest_output_path(node.path, source_path)
        if suggested == node.path:
            return []
        return [ValidationIssue(
            WARNING, node.id,
            f"{node.title} has no extension the engine recognises, so it would "
            f"be written as NetCDF4 under a name that says otherwise. "
            f"Use {Path(suggested).name} instead?",
            suggestion=suggested, kind=EXTENSION)]

    def _validate_operator(self, node: ModelNode,
                           catalog: OperatorCatalog) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []

        if not node.operator:
            return [ValidationIssue(ERROR, node.id, f"{node.title} has no operator")]

        signature = catalog.signature(node.operator)
        if signature is None:
            issues.append(ValidationIssue(
                ERROR, node.id,
                f"{node.title}: this build of the engine has no operator "
                f"called {node.operator}"))
            return issues

        nin, nout = signature
        wired = self.incoming(node.id)
        # Files, not wires: one folder edge carries as many operands as it has
        # files, and it is the file count the engine's own arity check applies to.
        supplied = self.input_count(node.id)

        # Phrased as _resolve_operator_call phrases it, so the message a user
        # sees before the run matches the one they would have seen after it.
        if nin == -1:
            if supplied < 1:
                issues.append(ValidationIssue(
                    ERROR, node.id, f"{node.title}: expected at least 1 input file"))
        elif supplied != nin:
            issues.append(ValidationIssue(
                ERROR, node.id,
                f"{node.title}: expected {nin} input file(s), got {supplied}"))

        ports = sorted(connection.target_port for connection in wired)
        if ports and ports != list(range(len(ports))):
            missing = [str(port + 1) for port in range(max(ports) + 1) if port not in ports]
            issues.append(ValidationIssue(
                ERROR, node.id,
                f"{node.title}: input {', '.join(missing)} is empty. The engine "
                f"reads operands by position, so a gap is not a valid command"))

        issues.extend(self._validate_parameters(node))

        if (nout >= 1 and node.keep_output and node.path
                and not writes_output_prefix(node.operator, node.parameters)):
            # A kept intermediate is written by the engine *and* read back by
            # whatever follows it, so its extension matters twice over — and it
            # is the one output path nothing was checking, because it is neither
            # a sink nor a temporary.
            #
            # ``nout >= 1`` rather than ``== 1``: a kept two-output node names
            # its first file here and ``_output_paths`` derives the second from
            # it by suffix, so a wrong extension on this path is wrong on both.
            # Checked once, because the fix is the same edit either way.
            #
            # Skipped for a node whose path is a *base*, for the same reason
            # ``_validate_sink`` skips it: CDO appends the extension itself, so
            # demanding one here demands the wrong thing. See ``_writes_prefix``.
            issues.extend(self._extension_issue(
                node, self._upstream_source_path(node.id)))

        if nout == 0 and self.outgoing(node.id):
            issues.append(ValidationIssue(
                ERROR, node.id,
                f"{node.title} writes no file — it only prints — so nothing can "
                f"read from it. Disconnect what follows it"))

        # The same rule for a node that writes a *picture*. ``nout == 1``, so
        # the branch above does not cover it, and every other check here reads
        # that 1 as "one dataset the next node can open".
        #
        # It cannot. The six Magics operators write PostScript, PNG or SVG, and
        # CDO's own manual calls them terminal operators — "These operators can
        # be used as terminal operators and chained with the existing
        # operators", which is a statement about what may come *before* them.
        # A wire out of one produces ``cdo fldmean plot.ps out.nc``, and what a
        # user gets back is a CDI read error about a file the app told them was
        # a result.
        #
        # Decided from the declared ``media`` rather than from a list of
        # operator names, so this covers ``stream`` — which is undocumented and
        # which no list assembled from the manual would have contained.
        #
        # ``_feeds_an_operator`` and not ``outgoing``, which is the difference
        # between this rule and the ``nout == 0`` one above it. An operator that
        # writes nothing may have no wire out of it at all; one that writes a
        # picture must still be wired to a *sink*, because that is how a user
        # names the obase. Only a wire into another operator is the mistake.
        if writes_images(node.operator) and self._feeds_an_operator(node.id):
            issues.append(ValidationIssue(
                ERROR, node.id,
                f"{node.title} writes a picture, not a dataset — nothing can "
                f"read from it. Disconnect the operator that follows it"))

        # A wire *out of* a two-output node, which had no branch here at all —
        # so a graph that could not be compiled into a sensible command reached
        # CDO before anything objected.
        #
        # What a wire out of such a node means: nothing yet. ``Connection`` does
        # carry a ``source_port``, and it round-trips through the saved file —
        # but ``NodeItem._rebuild_ports`` draws one output port per node and
        # hard-codes it to index 0, so every wire out of an operator arrives
        # with ``source_port == 0`` whatever the user meant by it. An edge
        # leaving an ``eof`` node therefore cannot say whether it carries the
        # eigenvalues or the eigenvectors. ``compile`` resolves that by
        # publishing output 0 and only output 0 downstream — which is a
        # defensible default and a terrible thing to do silently, because for
        # every operator in this shape the *interesting* file is output 2. A
        # user who wires eof into a plotting step and gets a 1x1 eigenvalue
        # spectrum has been given the wrong file by a rule nobody told them
        # about.
        #
        # So it is refused, and refused in the words ``nout == -1`` is refused
        # in: state what the node produces, why nothing can read it, and what to
        # do instead. Sending each output to a sink of its own is what
        # ``_output_paths`` assigns in wire order; ``keep_output`` and the
        # per-slot suffixes there are the fallback for the ones left unnamed.
        if nout >= 2 and self._feeds_an_operator(node.id):
            named = self._output_names(node.operator, nout)
            issues.append(ValidationIssue(
                ERROR, node.id,
                f"{node.title} writes {nout} different files"
                f"{f' — {named} —' if named else ''} and a wire cannot say "
                f"which of them it carries, so no operator can read from it. "
                f"Disconnect what follows it, and send the file you want to an "
                f"output or tick 'keep output' to name it"))

        # More sinks than the node has files to put in them. Every sink past the
        # ``nout``-th is ignored by ``_output_paths``, which means a path the
        # user typed is never written and nothing else in the run fails — the
        # same silent-success shape as a wrong companion file, and worth the
        # same treatment.
        #
        # Derived from ``nout``, so it covers the ordinary (n|1) node wired to
        # two sinks as well as the (1|2) one wired to three. A WARNING and not
        # an ERROR: the graph does run, and what it produces is right as far as
        # it goes — one named file is simply missing from it.
        #
        # Phrased as ``_resolve_operator_call`` phrases the same shortfall
        # ("expected 2 output target(s), got 1"), so the sentence read before
        # the run is the sentence that would have been read after it.
        sinks = [connection for connection in self.outgoing(node.id)
                 if (target := self._nodes.get(connection.target)) is not None
                 and target.kind == SINK and target.path]
        if nout >= 1 and len(sinks) > nout:
            ignored = [self._title(connection.target)
                       for connection in sinks[nout:]]
            named = self._output_names(node.operator, nout)
            many = len(ignored) > 1
            issues.append(ValidationIssue(
                WARNING, node.id,
                f"{node.title} expected {nout} output target(s), got "
                f"{len(sinks)}: it writes "
                f"{'one file' if nout == 1 else f'{nout} files'}"
                f"{f' — {named} — so' if named else ', so'} the extra "
                f"{'outputs' if many else 'output'} {', '.join(ignored)} would "
                f"never be written. Delete {'them' if many else 'it'}, or point "
                f"{'them' if many else 'it'} at another step"))

        if nout == -1 and self._feeds_an_operator(node.id):
            # Only an *operator* downstream is the mistake. A sink is not reading
            # the split's output, it is naming the prefix those files are written
            # under, which is the one thing a split node needs somewhere to put.
            issues.append(ValidationIssue(
                ERROR, node.id,
                f"{node.title} splits its input into a number of files nobody can "
                f"predict, so no operator can read from it. Disconnect what "
                f"follows it, or send it to an output instead"))

        return issues

    @staticmethod
    def _output_names(operator: str, nout: int) -> str:
        """What one operator's outputs are called, for a message about them.

        The declared roles when the schema has them — "'a' and 'b'" for
        ``trend``, "'Eigenvalues' and 'EOFs'" for ``eof`` — and "" when it does
        not, which is the honest answer for the four ``(1|2)`` operators nobody
        has described yet and for every ``(n|1)`` one. Callers drop the clause
        rather than printing a placeholder: "'Output File 1' and 'Output File
        2'" names nothing a user did not already know from the count.

        A slot counts as described when it carries a ``field``, which is the
        schema's own distinction — ``operator_outputs`` synthesises the missing
        captions with an empty one. Only the part of the role before the em dash
        is used: the rest is a sentence explaining the file, which reads as a
        caption on a form and not inside a sentence of its own.
        """
        roles = [output.role.split("—")[0].strip()
                 for output in operator_outputs(operator)[:max(nout, 0)]
                 if output.field and output.role]
        return " and ".join(f"'{role}'" for role in roles)

    def _validate_parameters(self, node: ModelNode) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        missing = missing_required_parameters(node.operator, node.parameters)
        if missing:
            issues.append(ValidationIssue(
                ERROR, node.id,
                f"{node.title} needs {'a value' if len(missing) == 1 else 'values'} for "
                f"{', '.join(missing)}"))

        # A parameter the operator *writes* is an output path, and it carries
        # the same trap every other output path in this file is checked for:
        # the engine picks the format from the extension and nothing else, so
        # ``tee,second.dat`` writes NetCDF4 under a name that says it is not.
        #
        # This is the only output a node can have that is neither a sink nor a
        # kept intermediate, so nothing above reaches it. It is not modelled as
        # a second port, and should not be: ``tee`` has one output *stream* —
        # outfile1, the one the graph continues from — and outfile2 is a copy
        # written on the way past. Giving it a port would draw an edge nothing
        # can be wired to.
        # Reported without a ``suggestion``, unlike every other extension
        # warning. ``apply_suggestions`` writes a suggestion into ``node.path``,
        # and this path is not the node's — taking the fix automatically would
        # rename the node's own output to the corrected name of its parameter.
        # The correction is still in the message, for the user to apply to the
        # field it belongs to.
        spec = get_operator_spec(node.operator)
        for index in output_parameter_indexes(node.operator):
            if index >= len(node.parameters):
                continue
            path = str(node.parameters[index]).strip()
            if not path:
                continue
            suggested = suggest_output_path(
                path, self._upstream_source_path(node.id))
            if suggested == path:
                continue
            label = (spec.params[index].label or spec.params[index].name
                     if spec and index < len(spec.params) else f"parameter {index + 1}")
            issues.append(ValidationIssue(
                WARNING, node.id,
                f"{node.title}: {label} has no extension the engine "
                f"recognises, so it would be written as NetCDF4 under a name "
                f"that says otherwise. Use {Path(suggested).name} instead?",
                kind=EXTENSION))
        return issues

    def _validate_environment(self) -> list[ValidationIssue]:
        """Environment settings that have to agree across two steps.

        The one invariant in the catalog that spans *two runs* rather than one.
        The CDO Eofcoeff manual page states that eofcoeff computes a
        **non-weighted** dot product; ``eof`` will happily compute a weighted
        decomposition if ``CDO_WEIGHT_MODE=on``. Set on one and not the other,
        both commands succeed, both files look right, and the coefficients are
        simply not the coefficients of those EOFs.

        Nothing else in this app can express an invariant spanning two
        invocations, and it is checked here rather than deferred because this is
        the one surface where both runs exist as objects at the same time: a
        graph holds the eof node and the eofcoeff node that reads from it, and
        the edge between them is what makes "the same run" a question the code
        can answer instead of a convention the user has to remember. The
        operator form cannot — it knows about one invocation — so there the
        warning lives in the description instead (``_eof_note``), which is the
        honest split.
        """
        issues: list[ValidationIssue] = []
        for node in self._nodes.values():
            if node.kind != OPERATOR or node.operator not in _EOFCOEFF:
                continue
            here = dict(node.env).get(_WEIGHT_MODE, _WEIGHT_MODE_DEFAULT)
            for producer in self._eof_ancestors(node.id):
                there = dict(producer.env).get(
                    _WEIGHT_MODE, _WEIGHT_MODE_DEFAULT)
                if there == here:
                    continue
                issues.append(ValidationIssue(
                    WARNING, node.id,
                    f"{node.title} runs with {_WEIGHT_MODE}={here} but the "
                    f"{producer.operator} it reads from runs with "
                    f"{there}. eofcoeff computes a non-weighted dot product, so "
                    f"the two have to agree or the coefficients will not match "
                    f"the EOFs they are projected onto. Both commands will "
                    f"still succeed"))
        return issues

    def _eof_ancestors(self, node_id: str) -> list[ModelNode]:
        """Every eof node upstream of ``node_id``, nearest first.

        Walked rather than read off the immediate edge because an eof node's
        output can reach eofcoeff through intermediate steps, and the weighting
        it was computed with survives all of them.
        """
        found: list[ModelNode] = []
        seen: set[str] = set()
        queue = [connection.source for connection in self.incoming(node_id)]
        while queue:
            current = queue.pop(0)
            if current in seen:
                continue
            seen.add(current)
            node = self._nodes.get(current)
            if node is None:
                continue
            if node.kind == OPERATOR and node.operator in _EOF_PRODUCERS:
                found.append(node)
            queue.extend(c.source for c in self.incoming(current))
        return found

    def _validate_branches(self, catalog: OperatorCatalog) -> list[ValidationIssue]:
        """Warn about work whose result goes nowhere.

        A node that reaches neither a sink nor an info operator has its output
        written into the temp store and deleted at the end of the run, which is
        almost never what somebody drew it for.
        """
        issues: list[ValidationIssue] = []
        for node in self._nodes.values():
            if node.kind != OPERATOR:
                continue
            if not self.incoming(node.id) and not self.outgoing(node.id):
                continue  # an orphan is already reported, once, as an orphan
            if node.keep_output and node.path:
                continue

            signature = catalog.signature(node.operator)
            if signature is not None and signature[1] not in (1, 2):
                # An info node prints, and a split node writes its own family of
                # files under a prefix. Neither leaves nothing behind.
                #
                # A two-output node is *not* exempt, which is why 2 is excluded
                # from this skip: nothing downstream can read from it (see
                # ``_validate_node``), so if no sink names its first output and
                # ``keep_output`` is unset, both files go to scratch and the
                # branch really does leave nothing behind. That is exactly the
                # case this warning exists for, and it is likelier here than
                # anywhere else — an ``eof`` node is the end of its branch by
                # construction.
                continue

            if not any(self._is_kept(target, catalog)
                       for target in self.descendants(node.id)):
                issues.append(ValidationIssue(
                    WARNING, node.id,
                    f"Nothing keeps what {node.title} produces, so this branch "
                    f"leaves no file behind"))
        return issues

    def _is_kept(self, node_id: str, catalog: OperatorCatalog) -> bool:
        """True when this node is somewhere a branch can usefully end.

        A sink and a kept intermediate leave a file; an info node leaves its
        reading in the output console, which is just as much a result.
        """
        node = self._nodes.get(node_id)
        if node is None:
            return False
        if node.kind == SINK:
            return True
        if node.kind != OPERATOR:
            return False
        if node.keep_output and node.path:
            return True
        return (catalog.signature(node.operator) or (1, 1))[1] == 0

    def extension_issues(self,
                         catalog: OperatorCatalog | None = None) -> list[ValidationIssue]:
        """Every output path the engine would not write in the format it claims.

        Kept apart from the rest so a run can be gated on exactly these: they are
        the issues that produce a *wrong file* rather than a failed command, and
        every one of them carries the correction that fixes it.
        """
        return [issue for issue in self.validate(catalog) if issue.kind == EXTENSION]

    def apply_suggestions(self, issues: Sequence[ValidationIssue]) -> int:
        """Take every fix these issues propose. Returns how many were applied."""
        applied = 0
        for issue in issues:
            if not issue.suggestion or issue.node not in self._nodes:
                continue
            if self._nodes[issue.node].path == issue.suggestion:
                continue
            self.update_node(issue.node, path=issue.suggestion)
            applied += 1
        logger.info("Corrected %d output path(s)", applied)
        return applied

    def source_path_for(self, node_id: str) -> str:
        """The real file that ultimately feeds ``node_id``, or "".

        Public because two callers need it for reasons that have nothing to do
        with each other: extension inheritance, and the expression editor, which
        lists the variables an ``expr`` node will actually see and runs its
        syntax check against them. A node several steps downstream still reads
        data that came from a file, and this is that file.
        """
        return self._upstream_source_path(node_id)

    def _upstream_source_path(self, node_id: str) -> str:
        """The first real input path upstream of a node, for extension inheritance."""
        pending = [connection.source for connection in self.incoming(node_id)]
        seen: set[str] = set()
        while pending:
            current = pending.pop(0)
            if current in seen or current not in self._nodes:
                continue
            seen.add(current)
            node = self._nodes[current]
            if node.input_paths:
                return node.input_paths[0]
            pending.extend(connection.source for connection in self.incoming(current))
        return ""

    def _title(self, node_id: str) -> str:
        node = self._nodes.get(node_id)
        return node.title if node is not None else node_id

    def has_errors(self, catalog: OperatorCatalog | None = None) -> bool:
        return any(issue.is_error for issue in self.validate(catalog))

    # -- compilation ----------------------------------------------------
    def compile(self, temp_path: Callable[[str], str],
                catalog: OperatorCatalog | None = None) -> list[OperatorRequest]:
        """The graph as the command sequence that runs it.

        A pure function of the graph and ``temp_path``, which is what lets a test
        pass a counting allocator and assert on the exact list. ``temp_path``
        takes a suffix and returns a path, the same shape ``core/batch.plan_job``
        expects, so both can be handed one ``TempFileStore``.

        Everything a run writes on the way to its outputs is a temporary, unless
        the user asked for it: a folder filling with intermediates nobody named
        is the mistake ``core/batch.py`` opens by calling out.
        """
        catalog = catalog or STATIC_CATALOG
        requests: list[OperatorRequest] = []

        # Node id → the paths it contributes. A source and an operator contribute
        # one; a folder contributes all of its files, which is what turns one
        # wire into the operand list ``mergetime`` wants.
        produced: dict[str, list[str]] = {}

        for node_id in self.topological_order():
            node = self._nodes[node_id]

            if node.kind in PRODUCER_KINDS:
                produced[node_id] = list(node.input_paths)
                continue
            if node.kind == SINK:
                continue

            nin, nout = catalog.signature(node.operator) or (1, 1)
            inputs: list[str] = []
            for connection in self.incoming(node_id):
                inputs.extend(produced.get(connection.source, [""]))

            outputs = self._output_paths(node, nout, temp_path)
            if outputs:
                # A fan-out node is written once here and read by each consumer
                # from this same path — the whole reason the map is keyed by node
                # rather than by edge.
                produced[node_id] = [outputs[0]]

            requests.append(OperatorRequest(
                operator=node.operator,
                input_files=tuple(inputs),
                output_files=tuple(outputs),
                parameters=_trimmed(node.parameters),
                nin=nin,
                nout=nout,
                # An info operator writes no file, so its ``path`` is where the
                # reading it prints should be kept — nowhere, if it is blank.
                stdout_file=node.path if nout == 0 else "",
                # A step that reads standard input takes its data from the same
                # ``path`` field, for the mirror of the reason above: it names
                # no input file, so its ``path`` is the file to feed it.
                stdin_file=node.path if reads_stdin(node.operator) else "",
                options=node.options,
                env=node.env,
            ))

        logger.debug("Model compiled to %d request(s)", len(requests))
        return requests

    def _output_paths(self, node: ModelNode, nout: int,
                      temp_path: Callable[[str], str]) -> list[str]:
        """Where one operator node writes — every file of it.

        Precedence, and the reasoning for it: a wired sink is the name the user
        typed for this result, so it wins; ``keep_output`` is the name they typed
        for an intermediate they want to inspect; anything else is scratch.

        **What sinks mean for a node with two outputs.** One sink names output
        1, two sinks name outputs 1 and 2, in the order the wires were drawn.
        Each is a different object — ``trend``'s intercept and its slope, an
        ``eof`` run's spectrum and its maps — so mapping several onto one path
        would have CDO refuse the second ("Outputfile already exists") or, worse,
        overwrite the first.

        Assigning in wire order is the only reading that does not silently lose
        a file the user named. It used to stop at the first sink and send the
        rest to scratch, and the cost of that was a graph nobody could see was
        wrong: wire two sinks to ``trend``, get a successful run, and find that
        the file you called ``slope.nc`` does not exist and its contents are in
        ``intercept_slope.nc`` instead.

        Wire order rather than :attr:`Connection.source_port` because the canvas
        cannot yet express the port. ``source_port`` exists on the wire and
        round-trips through the saved file, but ``NodeItem._rebuild_ports``
        draws exactly one output port per node and hard-codes it to index 0, so
        every wire out of an operator arrives here with ``source_port == 0``
        whatever it was meant to carry. When the canvas grows a port per output,
        this should key on it and wire order becomes the tiebreak; until then
        keying on it would sort every sink into slot 0 and lose the file again.

        Sinks past the ``nout``-th are ignored, and ``_validate_operator`` says
        so before the run rather than leaving it to be discovered afterwards.

        Everything not named by a sink is still a real file for the length of
        the run, so a downstream node reading port 0 gets what it asked for; it
        is deleted with the rest of the temp store afterwards. To keep an
        unnamed second file, tick "keep output" and give the node a path — the
        per-slot suffixes below are what stop the two colliding.

        ``nout == -1`` returns one path as it always has: a split operator's
        single target is a *prefix*, and the family of files under it is CDO's
        to name.
        """
        if nout == 0:
            return []

        # One path per output, from here down. ``count`` is 1 for the split
        # case for the reason in the docstring, and for the ordinary (n|1) case
        # this whole method behaves exactly as it did.
        count = 1 if nout == -1 else max(nout, 1)

        def scratch(index: int) -> str:
            suffix = self._intermediate_suffix(node.id)
            # Distinct scratch names per slot. Without this every output of a
            # two-output node would be allocated the same suffix and — with a
            # counting allocator, which is what the tests and the temp store
            # both use — two files that must differ could be handed the same
            # name. ``temp_path`` is free to ignore the stem; the suffix it is
            # given is what it keys on.
            if count == 1:
                return temp_path(suffix)
            return temp_path(f"_{index + 1}{suffix}")

        # In wire order, and at most one per output slot. ``outgoing`` preserves
        # the order the connections were made, which is the order the user drew
        # them and the only ordering they have any way to control today.
        named: list[str] = []
        for connection in self.outgoing(node.id):
            target = self._nodes.get(connection.target)
            if target is not None and target.kind == SINK and target.path:
                named.append(target.path)
                if len(named) == count:
                    break

        if not named and node.keep_output and node.path:
            named = [node.path]

        if not named:
            return [scratch(index) for index in range(count)]

        # Whatever the sinks did not name is derived from the first named path,
        # so a user who goes looking can tell which file is which. Derived from
        # ``named[0]`` rather than from the last one named, so that the same
        # graph produces the same second path whether or not a second sink is
        # present — adding one renames nothing that was already there.
        paths = list(named)
        for index in range(len(named), count):
            paths.append(self._sibling_output(named[0], node, index)
                         or scratch(index))
        return paths

    def _sibling_output(self, primary: str, node: ModelNode,
                        index: int) -> str:
        """Where output ``index`` goes when output 0 was named ``primary``.

        ``/data/eof.nc`` with the eof schema gives ``/data/eof_eofs.nc``, from
        :attr:`~..core.categories.OperatorOutput.suffix`. Written beside the
        file the user named, in the folder they chose, because a second output
        they can find is the whole point of not sending it to scratch.

        Returns "" when the operator declares no suffix for this slot, which
        sends the caller to a temporary — the safe answer, since inventing
        ``_2`` for an undeclared operator would put a file the user did not ask
        for into a folder they did not choose.
        """
        outputs = operator_outputs(node.operator)
        if index >= len(outputs) or not outputs[index].suffix:
            return ""
        stem, extension = os.path.splitext(primary)
        return f"{stem}{outputs[index].suffix}{extension}"

    def _intermediate_suffix(self, node_id: str) -> str:
        """The format an intermediate should carry downstream.

        Taken from the sink this branch eventually reaches, because CDO decides
        the format from the extension: writing a NetCDF intermediate into a chain
        that ends in GRIB would convert twice and lose whatever the first
        conversion could not carry.
        """
        for target in self.descendants(node_id):
            node = self._nodes.get(target)
            if node is None or node.kind != SINK or not node.path:
                continue
            suffix = os.path.splitext(node.path)[1].lower()
            if suffix in OUTPUT_EXTENSIONS:
                return suffix
        return DEFAULT_SUFFIX

    # -- serialisation --------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        """The graph as plain JSON types."""
        return {
            "version": MODEL_SCHEMA_VERSION,
            "nodes": [node.to_dict() for node in self._nodes.values()],
            "connections": [connection.to_dict() for connection in self._connections],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ModelGraph":
        """Rebuild a graph, tolerating a file written by another version.

        Unknown keys are ignored and unusable entries are dropped rather than
        raising, the way ``project.request_from_dict`` does: a model that loses
        one broken wire is far more useful than one that refuses to open.
        """
        graph = cls()
        for item in data.get("nodes") or []:
            if not isinstance(item, Mapping):
                continue
            node = ModelNode.from_dict(item)
            if not node.id or node.id in graph._nodes:
                continue
            graph.add_node(node)

        for item in data.get("connections") or []:
            if not isinstance(item, Mapping):
                continue
            connection = Connection.from_dict(item)
            if connection.source not in graph._nodes or connection.target not in graph._nodes:
                logger.debug("Dropping a wire to a node this model does not have: %s",
                             connection)
                continue
            graph._connections.append(connection)

        return graph


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _strings(values: Any) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, str):
        return (values,)
    try:
        return tuple(str(value) for value in values)
    except TypeError:
        return ()


def _env_pairs(values: Any) -> tuple[tuple[str, str], ...]:
    """Stored environment overrides as ``(name, value)`` pairs.

    Tolerant of both shapes a saved model can hold — the list-of-pairs this
    writes, and a plain object, which is what a hand-edited project file is
    likeliest to contain. Anything else is dropped rather than raised on: a
    model saved by a later version must still open, minus what this one cannot
    read, which is the rule ``from_dict`` follows for every other field.

    Blank names and blank values are dropped here as well as in
    ``run_environment``. It matters in both places for different reasons: there
    it stops an empty string being exported as a value, and here it stops a
    half-filled row being saved and then re-shown as if it had been set.
    """
    if not values:
        return ()
    if isinstance(values, Mapping):
        items = values.items()
    else:
        try:
            items = [(pair[0], pair[1]) for pair in values if len(pair) >= 2]
        except (TypeError, IndexError, KeyError):
            return ()
    pairs = []
    for name, value in items:
        name, value = str(name).strip(), str(value).strip()
        if name and value:
            pairs.append((name, value))
    return tuple(pairs)


def _trimmed(parameters: Sequence[str]) -> tuple[str, ...]:
    """Parameters without their trailing blanks.

    An optional parameter the user left empty must not become a trailing comma;
    ``operator_token`` drops them at render time, and doing it here as well keeps
    what is stored in the request canonical.
    """
    values = list(parameters)
    while values and not values[-1]:
        values.pop()
    return tuple(values)


def _id_number(node_id: str) -> int:
    match = _ID_PATTERN.match(node_id)
    return int(match.group(1)) if match else 0


def suggest_output_path(path: str, source: str = "") -> str:
    """``path`` with an extension the engine recognises.

    Same rule as ``main_window._ensure_output_extension`` and
    ``batch.ensure_output_extension``: inherit the input's extension where it is
    usable, fall back to ``.nc``. Kept identical on purpose — three places
    deciding an output format differently would be three different bugs.
    """
    if not path:
        return path
    stem, extension = os.path.splitext(path)
    if extension.lower() in OUTPUT_EXTENSIONS:
        return path
    source_extension = os.path.splitext(source)[1].lower() if source else ""
    return stem + (source_extension if source_extension in OUTPUT_EXTENSIONS
                   else DEFAULT_SUFFIX)


def command_lines(requests: Sequence[OperatorRequest]) -> list[str]:
    """One shell-quoted invocation per request, for the preview panel."""
    return [request.command_line() for request in requests]


def portable_allocator(graph: "ModelGraph") -> Callable[[str], str]:
    """Name intermediates beside the model's output rather than in the temp store.

    Compiling for a *run* puts intermediates in ``TempFileStore`` so they are
    swept up afterwards. Compiling for an *export* must not: a shell script that
    reads ``/var/folders/…/T/ncexplorer_model_vbkvsn_o.nc`` is tied to one user
    on one machine, and every exported artefact is supposed to run without
    NCExplorer anywhere near it. These names sit next to the result, so the
    script is portable and the Makefile's ``clean`` has something to remove.
    """
    destination = ""
    for node in graph.nodes:
        if node.kind == SINK and node.path:
            destination = node.path
            break
    if not destination:
        for node in graph.nodes:
            if node.input_paths:
                destination = node.input_paths[0]
                break

    directory = os.path.dirname(destination)
    stem = os.path.splitext(os.path.basename(destination))[0] or "model"
    counter = {"step": 0}

    def allocate(suffix: str) -> str:
        counter["step"] += 1
        name = f"{stem}_step{counter['step']}{suffix}"
        return os.path.join(directory, name) if directory else name

    return allocate


# ---------------------------------------------------------------------------
# Native chaining
#
# CDO can nest operators in one process — ``cdo timmean -sellonlatbox,... in out``
# — which never writes the intermediate at all. On a decade of daily data that is
# most of the runtime, so it is worth showing; it is *not* what this module
# executes, and the distinction is deliberate.
#
# ``OperatorRequest`` is [token, *inputs, *outputs] and a fused command is not
# that shape: the inner ``-op`` tokens interleave with their own operands, and
# ``nc_integration._create_input_alias`` would try to treat ``-timmean`` as a path
# to symlink. Rather than widen that contract, fusion is restricted to what is
# *shown* — the preview and the exported artefacts — while execution stays one
# request per node. Each intermediate then remains a real file the user can open
# when a chain misbehaves, which is the thing a fused command takes away.
#
# The rules below were checked against the installed CDO rather than taken on
# trust; ``tests/test_model_builder.py`` records what it actually did.
# ---------------------------------------------------------------------------

def _fusable(graph: ModelGraph, node: ModelNode, catalog: OperatorCatalog) -> bool:
    """Whether ``node``'s output can become an inner ``-op`` of its consumer.

    Every clause is a refusal CDO 2.6.0 itself enforces, except the last:

    * ``nout == 0`` — "Operator has no output, cannot be used with pipes unless
      used first".
    * ``nout == -1`` — "This operator can't be combined with other operators!".
    * more than one consumer, or a consumer that is a sink — the file is wanted
      on disk, so fusing it away would change what the run produces.
    * ``keep_output`` — the user asked for that file by name.
    * ``nin == -1`` is refused here although the installed build accepts it where
      the operand boundary is unambiguous (``cdo timmean -cat a.nc b.nc out.nc``
      is correct, and does average all eight steps). It aborts with "Missing
      inputs" as soon as anything could claim those operands — ``cdo sub -cat
      a.nc b.nc c.nc out.nc`` — and telling those two cases apart at every depth
      is more subtlety than a preview is worth.
    """
    if node.kind != OPERATOR:
        return False
    nin, nout = catalog.signature(node.operator) or (1, 1)
    if nout != 1 or nin == -1 or node.keep_output:
        return False
    consumers = graph.outgoing(node.id)
    if len(consumers) != 1:
        return False
    target = graph.node(consumers[0].target)
    return target is not None and target.kind == OPERATOR


def fused_commands(graph: ModelGraph,
                   catalog: OperatorCatalog | None = None) -> list[list[str]]:
    """The graph as the fewest CDO invocations that express it.

    Returns argv lists, ``cdo`` included. Shown, never run — see the section
    comment above.
    """
    catalog = catalog or STATIC_CATALOG
    fusable = {
        node.id for node in graph.nodes
        if _fusable(graph, node, catalog)
    }

    def operand(node_id: str) -> list[str]:
        node = graph.node(node_id)
        if node is None:
            return [""]
        if node.kind in PRODUCER_KINDS:
            return list(node.input_paths)
        if node.id in fusable:
            return arguments(node)
        return [_display_output(graph, node, catalog)]

    def arguments(node: ModelNode) -> list[str]:
        parts = ["-" + operator_token(node.operator, _trimmed(node.parameters))]
        for connection in graph.incoming(node.id):
            parts.extend(operand(connection.source))
        return parts

    commands: list[list[str]] = []
    for node_id in graph.topological_order():
        node = graph.node(node_id)
        if node is None or node.kind != OPERATOR or node_id in fusable:
            continue
        # The outermost operator is written bare; only inner ones take the dash.
        argv = [CDO, operator_token(node.operator, _trimmed(node.parameters))]
        for connection in graph.incoming(node_id):
            argv.extend(operand(connection.source))
        output = _display_output(graph, node, catalog)
        if output:
            argv.append(output)
        commands.append(argv)
    return commands


def _display_output(graph: ModelGraph, node: ModelNode,
                    catalog: OperatorCatalog) -> str:
    """What to call one node's output in a preview.

    The compiled form allocates a real temporary here. A preview is read before
    anything has run, so it shows a name that says what the file *is* instead of
    a path under ``/var/folders`` that will not exist by the time anyone looks.
    """
    nout = (catalog.signature(node.operator) or (1, 1))[1]
    if nout == 0:
        return ""
    for connection in graph.outgoing(node.id):
        target = graph.node(connection.target)
        if target is not None and target.kind == SINK and target.path:
            return target.path
    if node.keep_output and node.path:
        return node.path
    return f"<{node.title}{graph._intermediate_suffix(node.id)}>"


# ---------------------------------------------------------------------------
# Standalone model files
# ---------------------------------------------------------------------------

def save_model(path: str, graph: ModelGraph) -> str:
    """Write one graph as a ``.ncmodel`` file. Returns the path written.

    Plain JSON rather than a zip: a project is a container that will grow
    thumbnails and exports, but a model is one document, and one document in a
    zip is a format nobody can read with an editor.
    """
    target = ensure_model_suffix(path)
    try:
        Path(target).write_text(json.dumps(graph.to_dict(), indent=2) + "\n",
                                encoding="utf-8")
    except (OSError, TypeError, ValueError) as exc:
        raise ModelError(f"Could not write {target}: {exc}") from exc
    logger.info("Model saved to %s (%d node(s))", target, len(graph))
    return target


def load_model(path: str) -> ModelGraph:
    """Read one ``.ncmodel`` file.

    Raises :class:`ModelError` for a file that is not a model, is damaged, or was
    written by a newer major version — the same three refusals, and the same
    tolerance of a higher minor, that ``core/project.load_project`` applies.
    """
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise ModelError(f"Could not open {Path(path).name}: {exc}") from exc
    except ValueError as exc:
        raise ModelError(f"{Path(path).name} is damaged: {exc}") from exc

    if not isinstance(raw, dict) or "nodes" not in raw:
        raise ModelError(f"{Path(path).name} is not an NCExplorer model.")

    version = str(raw.get("version", "0"))
    if _major(version) > _major(MODEL_SCHEMA_VERSION):
        raise ModelError(
            f"{Path(path).name} was saved by a newer version of NCExplorer "
            f"(model format {version}; this build reads {MODEL_SCHEMA_VERSION}). "
            "Update the application to open it."
        )

    graph = ModelGraph.from_dict(raw)
    logger.info("Model loaded from %s (%d node(s))", path, len(graph))
    return graph


def ensure_model_suffix(path: str) -> str:
    """Give a chosen path the model suffix if the file dialog did not."""
    return path if path.lower().endswith(MODEL_SUFFIX) else path + MODEL_SUFFIX


def _major(version: str) -> int:
    try:
        return int(str(version).split(".")[0])
    except (ValueError, AttributeError):
        return 0


# ---------------------------------------------------------------------------
# Building a graph from something that already exists
# ---------------------------------------------------------------------------

def graph_from_pipeline(pipeline: Iterable[OperatorRequest]) -> ModelGraph:
    """A linear recorded chain as a graph the user can then branch.

    The session log's steps are already ``OperatorRequest``s, so a session that
    has been run by hand is one click from being a model — which is the cheapest
    way into this editor for somebody who has never opened it.

    Wiring is by path: a step reading what an earlier step wrote becomes a wire,
    and every other input becomes a source node. That is the same reasoning
    ``batch.plan_job`` uses, and it is what makes a recorded chain hold together
    without anything having to know its shape in advance.
    """
    graph = ModelGraph()
    by_path: dict[str, str] = {}      # produced path → node id that produces it
    sources: dict[str, str] = {}      # input path → source node id
    wrote: dict[str, str] = {}        # node id → the path that step wrote
    column = 0

    for request in pipeline:
        operator_id = graph.add(
            OPERATOR,
            operator=request.operator,
            parameters=tuple(request.parameters),
            position=(260.0 * (column + 1), 0.0),
        ).id

        for port, path in enumerate(request.input_files):
            producer = by_path.get(path)
            if producer is None:
                if path not in sources:
                    sources[path] = graph.add(
                        SOURCE, path=path,
                        position=(0.0, 120.0 * len(sources)),
                    ).id
                producer = sources[path]
            try:
                graph.connect(producer, 0, operator_id, port)
            except ModelError:
                logger.debug("Could not wire %s into %s", producer, operator_id)

        for path in request.output_files:
            by_path[path] = operator_id
        if request.output_files:
            wrote[operator_id] = request.output_files[-1]
        column += 1

    # Only the last producer gets a sink: everything before it was an
    # intermediate, and turning each into an output would recreate exactly the
    # folder full of by-products the compiler exists to avoid.
    final = _last_producer(graph)
    if final is not None:
        sink = graph.add(SINK, path=wrote.get(final, ""),
                         position=(260.0 * (column + 1), 0.0))
        try:
            graph.connect(final, 0, sink.id, 0)
        except ModelError:
            graph.remove_node(sink.id)

    return graph


def _last_producer(graph: ModelGraph) -> str | None:
    """The last operator node that writes exactly one file, or None."""
    for node_id in reversed(graph.topological_order()):
        node = graph.node(node_id)
        if node is None or node.kind != OPERATOR:
            continue
        spec = get_operator_spec(node.operator)
        if spec is not None and spec.nout == 1:
            return node_id
    return None
