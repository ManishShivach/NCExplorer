# Copyright (c) 2026 Manish Shivach
# SPDX-License-Identifier: MIT
"""The model graph: deterministic order, honest validation, exact compilation.

Nothing here runs CDO. Compilation is where the logic is and it needs no
subprocess — the one test that does want a real engine is skipped unless one is
installed, and it exists to pin the chaining rules the fusion preview relies on
rather than to test the graph.
"""

import itertools
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from PyQt6.QtCore import Qt

from ncexplorer_toolkit.core.model import (
    ERROR, EXTENSION, FOLDER, OPERATOR, SINK, SOURCE, WARNING, ModelError,
    ModelGraph, OperatorCatalog, fused_commands, graph_from_pipeline, load_model,
    portable_allocator, save_model, suggest_output_path,
)
from ncexplorer_toolkit.core.nc_integration import OUTPUT_EXTENSIONS
from ncexplorer_toolkit.core.session_log import OperatorRequest


@pytest.fixture
def allocator():
    """A deterministic stand-in for the temp store, so commands are assertable."""
    counter = itertools.count(1)
    return lambda suffix: f"/tmp/intermediate{next(counter)}{suffix}"


@pytest.fixture
def real_file(tmp_path):
    """A path that exists, for the source-node rule that checks the disk."""
    path = tmp_path / "input.nc"
    path.write_bytes(b"not really netcdf, but it is on disk")
    return str(path)


def _errors(graph, catalog=None):
    return [issue for issue in graph.validate(catalog) if issue.severity == ERROR]


def _warnings(graph, catalog=None):
    return [issue for issue in graph.validate(catalog) if issue.severity == WARNING]


def _linear(real_file, output="/tmp/result.nc"):
    """source → sellonlatbox → timmean → sink."""
    graph = ModelGraph()
    source = graph.add(SOURCE, path=real_file)
    box = graph.add(OPERATOR, operator="sellonlatbox",
                    parameters=("0", "30", "-10", "10"))
    mean = graph.add(OPERATOR, operator="timmean")
    sink = graph.add(SINK, path=output)
    graph.connect(source.id, 0, box.id, 0)
    graph.connect(box.id, 0, mean.id, 0)
    graph.connect(mean.id, 0, sink.id, 0)
    return graph, source, box, mean, sink


def _diamond(real_file, output="/tmp/anomaly.nc"):
    """One source, two reductions, recombined through ``sub``."""
    graph = ModelGraph()
    source = graph.add(SOURCE, path=real_file)
    mean = graph.add(OPERATOR, operator="timmean")
    minimum = graph.add(OPERATOR, operator="timmin")
    difference = graph.add(OPERATOR, operator="sub")
    sink = graph.add(SINK, path=output)
    graph.connect(source.id, 0, mean.id, 0)
    graph.connect(source.id, 0, minimum.id, 0)
    graph.connect(mean.id, 0, difference.id, 0)
    graph.connect(minimum.id, 0, difference.id, 1)
    graph.connect(difference.id, 0, sink.id, 0)
    return graph, source, mean, minimum, difference, sink


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------

def test_topological_order_is_deterministic(real_file):
    """The same graph must compile to the same commands on every build."""
    orders = []
    for _ in range(8):
        graph, *_ = _diamond(real_file)
        orders.append(graph.topological_order())
    assert len(set(tuple(order) for order in orders)) == 1


def test_topological_order_puts_producers_first(real_file):
    graph, source, mean, minimum, difference, sink = _diamond(real_file)
    order = graph.topological_order()
    assert order.index(source.id) < order.index(mean.id)
    assert order.index(mean.id) < order.index(difference.id)
    assert order.index(minimum.id) < order.index(difference.id)
    assert order.index(difference.id) < order.index(sink.id)


def test_ties_break_by_insertion_order(real_file):
    """Two independent branches always come out in the order they were drawn."""
    graph, source, mean, minimum, difference, sink = _diamond(real_file)
    order = graph.topological_order()
    assert order.index(mean.id) < order.index(minimum.id)


# ---------------------------------------------------------------------------
# Wiring rules
# ---------------------------------------------------------------------------

def test_self_loop_is_refused():
    graph = ModelGraph()
    node = graph.add(OPERATOR, operator="timmean")
    with pytest.raises(ModelError, match="cannot feed itself"):
        graph.connect(node.id, 0, node.id, 0)


def test_cycle_is_refused():
    graph = ModelGraph()
    first = graph.add(OPERATOR, operator="timmean")
    second = graph.add(OPERATOR, operator="sub")
    graph.connect(first.id, 0, second.id, 0)
    with pytest.raises(ModelError, match="loop"):
        graph.connect(second.id, 0, first.id, 0)


def test_occupied_port_is_refused():
    graph = ModelGraph()
    a = graph.add(SOURCE, path="/tmp/a.nc")
    b = graph.add(SOURCE, path="/tmp/b.nc")
    target = graph.add(OPERATOR, operator="sub")
    graph.connect(a.id, 0, target.id, 0)
    with pytest.raises(ModelError, match="already connected"):
        graph.connect(b.id, 0, target.id, 0)


def test_removing_a_node_drops_its_wires(real_file):
    graph, source, box, mean, sink = _linear(real_file)
    assert len(graph.connections) == 3
    graph.remove_node(box.id)
    assert len(graph.connections) == 1
    assert all(box.id not in (c.source, c.target) for c in graph.connections)


def test_a_cycle_loaded_from_a_file_is_reported_not_raised():
    """connect() prevents cycles; from_dict must survive a file that has one."""
    graph = ModelGraph()
    first = graph.add(OPERATOR, operator="timmean")
    second = graph.add(OPERATOR, operator="timmin")
    payload = graph.to_dict()
    payload["connections"] = [
        {"source": first.id, "source_port": 0, "target": second.id, "target_port": 0},
        {"source": second.id, "source_port": 0, "target": first.id, "target_port": 0},
    ]

    loaded = ModelGraph.from_dict(payload)
    issues = _errors(loaded)
    assert len(issues) == 1
    assert "loop" in issues[0].message
    assert issues[0].node in (first.id, second.id)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_unknown_operator_is_an_error():
    graph = ModelGraph()
    node = graph.add(OPERATOR, operator="definitelynotanoperator")
    issues = [i for i in _errors(graph) if i.node == node.id]
    assert issues
    assert "no operator called" in issues[0].message


def test_operator_missing_from_the_installed_build_is_an_error(real_file):
    """The installed CDO wins: an operator it lacks must not be offered."""
    graph = ModelGraph()
    source = graph.add(SOURCE, path=real_file)
    node = graph.add(OPERATOR, operator="timmean")
    graph.connect(source.id, 0, node.id, 0)

    without = OperatorCatalog({"timmin": (1, 1)})
    issues = [i for i in _errors(graph, without) if i.node == node.id]
    assert any("no operator called timmean" in issue.message for issue in issues)


def test_wrong_input_count_is_an_error(real_file):
    graph = ModelGraph()
    source = graph.add(SOURCE, path=real_file)
    node = graph.add(OPERATOR, operator="sub")
    graph.connect(source.id, 0, node.id, 0)

    issues = [i for i in _errors(graph) if i.node == node.id]
    assert any("expected 2 input file(s), got 1" in issue.message for issue in issues)


def test_variable_arity_needs_at_least_one_input():
    graph = ModelGraph()
    node = graph.add(OPERATOR, operator="merge")
    issues = [i for i in _errors(graph) if i.node == node.id]
    assert any("at least 1 input file" in issue.message for issue in issues)


def test_a_gap_in_the_operand_ports_is_an_error(real_file):
    """Port 1 wired with port 0 empty is not a command CDO can be given."""
    graph = ModelGraph()
    source = graph.add(SOURCE, path=real_file)
    node = graph.add(OPERATOR, operator="sub")
    graph.connect(source.id, 0, node.id, 1)

    issues = [i for i in _errors(graph) if i.node == node.id]
    assert any("is empty" in issue.message and "position" in issue.message
               for issue in issues)


def test_info_node_with_an_outgoing_edge_is_an_error(real_file):
    graph = ModelGraph()
    source = graph.add(SOURCE, path=real_file)
    info = graph.add(OPERATOR, operator="ntime")
    after = graph.add(OPERATOR, operator="timmean")
    graph.connect(source.id, 0, info.id, 0)
    graph.connect(info.id, 0, after.id, 0)

    issues = [i for i in _errors(graph) if i.node == info.id]
    assert any("writes no file" in issue.message for issue in issues)


def test_info_node_on_its_own_is_fine(real_file):
    """Info operators are terminal, not forbidden."""
    graph = ModelGraph()
    source = graph.add(SOURCE, path=real_file)
    info = graph.add(OPERATOR, operator="ntime")
    graph.connect(source.id, 0, info.id, 0)
    assert not _errors(graph)


def test_split_node_feeding_an_operator_is_an_error(real_file):
    graph = ModelGraph()
    source = graph.add(SOURCE, path=real_file)
    split = graph.add(OPERATOR, operator="splitmon")
    after = graph.add(OPERATOR, operator="timmean")
    graph.connect(source.id, 0, split.id, 0)
    graph.connect(split.id, 0, after.id, 0)

    issues = [i for i in _errors(graph) if i.node == split.id]
    assert any("nobody can predict" in issue.message for issue in issues)


def test_split_node_feeding_an_output_names_its_prefix(real_file, allocator):
    """A sink after a split is not reading it — it is naming the prefix."""
    graph = ModelGraph()
    source = graph.add(SOURCE, path=real_file)
    split = graph.add(OPERATOR, operator="splitmon")
    sink = graph.add(SINK, path="/tmp/month_")
    graph.connect(source.id, 0, split.id, 0)
    graph.connect(split.id, 0, sink.id, 0)

    assert not _errors(graph)
    request = graph.compile(allocator)[0]
    assert request.is_split
    assert request.output_files == ("/tmp/month_",)


def test_missing_required_parameters_is_an_error(real_file):
    graph, source, box, mean, sink = _linear(real_file)
    graph.update_node(box.id, parameters=())

    issues = [i for i in _errors(graph) if i.node == box.id]
    assert any("needs values for" in issue.message for issue in issues)


def test_trailing_empty_optionals_are_not_an_error(real_file):
    """A blank optional must not read as missing, nor become a trailing comma."""
    graph = ModelGraph()
    source = graph.add(SOURCE, path=real_file)
    node = graph.add(OPERATOR, operator="sellonlatbox",
                     parameters=("0", "30", "-10", "10", ""))
    sink = graph.add(SINK, path="/tmp/out.nc")
    graph.connect(source.id, 0, node.id, 0)
    graph.connect(node.id, 0, sink.id, 0)

    assert not _errors(graph)
    request = graph.compile(lambda suffix: "/tmp/x" + suffix)[0]
    assert request.command_line().startswith("cdo sellonlatbox,0,30,-10,10 ")
    assert ",," not in request.command_line()


def test_a_missing_source_file_is_an_error(tmp_path):
    graph = ModelGraph()
    source = graph.add(SOURCE, path=str(tmp_path / "gone.nc"))
    issues = [i for i in _errors(graph) if i.node == source.id]
    assert any("does not exist" in issue.message for issue in issues)


def test_an_unrecognised_sink_extension_warns_and_suggests(real_file):
    graph, source, box, mean, sink = _linear(real_file, output="/tmp/result.dat")
    warnings = [i for i in _warnings(graph) if i.node == sink.id]
    assert warnings
    assert warnings[0].suggestion == "/tmp/result.nc"
    assert not _errors(graph)


def test_a_branch_that_keeps_nothing_warns(real_file):
    graph = ModelGraph()
    source = graph.add(SOURCE, path=real_file)
    mean = graph.add(OPERATOR, operator="timmean")
    graph.connect(source.id, 0, mean.id, 0)

    warnings = [i for i in _warnings(graph) if i.node == mean.id]
    assert any("leaves no file behind" in issue.message for issue in warnings)


def test_keeping_an_intermediate_silences_that_warning(real_file):
    graph = ModelGraph()
    source = graph.add(SOURCE, path=real_file)
    mean = graph.add(OPERATOR, operator="timmean", keep_output=True,
                     path="/tmp/kept.nc")
    graph.connect(source.id, 0, mean.id, 0)
    assert not [i for i in _warnings(graph) if "leaves no file" in i.message]


def test_an_orphan_node_warns():
    graph = ModelGraph()
    node = graph.add(OPERATOR, operator="timmean")
    assert any("not connected to anything" in issue.message
               for issue in _warnings(graph) if issue.node == node.id)


def test_validate_reports_every_issue_not_just_the_first():
    graph = ModelGraph()
    graph.add(OPERATOR, operator="sub")
    graph.add(OPERATOR, operator="sellonlatbox")
    graph.add(SOURCE, path="/nowhere/at/all.nc")
    assert len(_errors(graph)) >= 3


def test_a_valid_graph_has_no_errors(real_file):
    graph, *_ = _linear(real_file)
    assert not _errors(graph)


# ---------------------------------------------------------------------------
# Compilation
# ---------------------------------------------------------------------------

def test_linear_chain_compiles_exactly(real_file, allocator):
    graph, source, box, mean, sink = _linear(real_file)
    requests = graph.compile(allocator)

    assert requests == [
        OperatorRequest(
            operator="sellonlatbox",
            input_files=(real_file,),
            output_files=("/tmp/intermediate1.nc",),
            parameters=("0", "30", "-10", "10"),
            nin=1, nout=1,
        ),
        OperatorRequest(
            operator="timmean",
            input_files=("/tmp/intermediate1.nc",),
            output_files=("/tmp/result.nc",),
            parameters=(),
            nin=1, nout=1,
        ),
    ]


def test_only_the_final_output_lands_at_a_users_path(real_file, allocator):
    graph, *_ = _linear(real_file)
    requests = graph.compile(allocator)
    written = [path for request in requests for path in request.output_files]
    assert written[-1] == "/tmp/result.nc"
    assert all(path.startswith("/tmp/intermediate") for path in written[:-1])


def test_diamond_reads_the_source_twice_and_writes_the_shared_node_once(
        real_file, allocator):
    graph, source, mean, minimum, difference, sink = _diamond(real_file)
    requests = graph.compile(allocator)

    assert len(requests) == 3
    reads = [request for request in requests if real_file in request.input_files]
    assert len(reads) == 2, "the shared source must be read by both branches"

    writes = [path for request in requests for path in request.output_files]
    assert len(writes) == len(set(writes)), "no path may be written twice"


def test_diamond_operands_arrive_in_target_port_order(real_file, allocator):
    """Getting sub's operands backwards inverts the sign and nothing notices."""
    graph, source, mean, minimum, difference, sink = _diamond(real_file)
    requests = graph.compile(allocator)

    by_operator = {request.operator: request for request in requests}
    mean_output = by_operator["timmean"].output_files[0]
    min_output = by_operator["timmin"].output_files[0]

    assert by_operator["sub"].input_files == (mean_output, min_output)
    assert by_operator["sub"].output_files == ("/tmp/anomaly.nc",)


def test_a_kept_intermediate_is_written_where_the_user_asked(real_file, allocator):
    graph, source, box, mean, sink = _linear(real_file)
    graph.update_node(box.id, keep_output=True, path="/tmp/clipped.nc")
    requests = graph.compile(allocator)

    assert requests[0].output_files == ("/tmp/clipped.nc",)
    assert requests[1].input_files == ("/tmp/clipped.nc",), \
        "the downstream node must read the kept file, not a temporary"


def test_an_info_node_compiles_with_no_output(real_file, allocator):
    graph = ModelGraph()
    source = graph.add(SOURCE, path=real_file)
    info = graph.add(OPERATOR, operator="ntime")
    graph.connect(source.id, 0, info.id, 0)

    request = graph.compile(allocator)[0]
    assert request.output_files == ()
    assert request.nout == 0
    assert not request.produces_file


def test_a_split_node_compiles_to_one_prefix(real_file, allocator):
    graph = ModelGraph()
    source = graph.add(SOURCE, path=real_file)
    split = graph.add(OPERATOR, operator="splitmon")
    graph.connect(source.id, 0, split.id, 0)

    request = graph.compile(allocator)[0]
    assert len(request.output_files) == 1
    assert request.is_split


def test_intermediates_inherit_the_sinks_format(real_file, allocator):
    graph, source, box, mean, sink = _linear(real_file, output="/tmp/result.grb")
    requests = graph.compile(allocator)
    assert requests[0].output_files[0].endswith(".grb")


def test_compilation_is_a_pure_function_of_the_graph(real_file):
    """Two compilations with the same allocator produce the same commands."""
    graph, *_ = _diamond(real_file)
    first = graph.compile(lambda suffix: f"/tmp/fixed{suffix}")
    second = graph.compile(lambda suffix: f"/tmp/fixed{suffix}")
    assert first == second


def test_arity_is_carried_onto_every_request(real_file, allocator):
    """batch, replay and the Makefile exporter all read nin/nout."""
    graph, *_ = _diamond(real_file)
    for request in graph.compile(allocator):
        assert (request.nin, request.nout) != (None, None)
        if request.operator == "sub":
            assert request.nin == 2


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------

def test_round_trip_keeps_everything_including_positions(real_file):
    graph, source, box, mean, sink = _linear(real_file)
    graph.update_node(box.id, position=(120.5, -44.25), label="clip to the tropics")
    graph.update_node(mean.id, keep_output=True, path="/tmp/kept.nc")

    restored = ModelGraph.from_dict(json.loads(json.dumps(graph.to_dict())))

    assert [node.to_dict() for node in restored.nodes] == \
           [node.to_dict() for node in graph.nodes]
    assert restored.connections == graph.connections
    assert restored.node(box.id).position == (120.5, -44.25)
    assert restored.node(box.id).label == "clip to the tropics"
    assert restored.node(mean.id).keep_output is True


def test_load_tolerates_keys_from_a_future_version(real_file):
    graph, *_ = _linear(real_file)
    payload = graph.to_dict()
    payload["some_future_key"] = {"nested": [1, 2, 3]}
    payload["nodes"][0]["a_field_this_build_never_heard_of"] = "hello"

    restored = ModelGraph.from_dict(payload)
    assert len(restored) == len(graph)
    assert restored.topological_order() == graph.topological_order()


def test_a_wire_to_a_missing_node_is_dropped_not_fatal():
    payload = {
        "version": "1.0",
        "nodes": [{"id": "n1", "kind": OPERATOR, "operator": "timmean"}],
        "connections": [{"source": "n1", "source_port": 0,
                         "target": "n99", "target_port": 0}],
    }
    graph = ModelGraph.from_dict(payload)
    assert len(graph) == 1
    assert graph.connections == []


def test_new_ids_are_never_reused_after_a_load(real_file):
    graph, *_ = _linear(real_file)
    issued = {node.id for node in graph.nodes}
    restored = ModelGraph.from_dict(graph.to_dict())
    assert restored.new_id() not in issued


def test_model_file_round_trip(tmp_path, real_file):
    graph, *_ = _linear(real_file)
    path = save_model(str(tmp_path / "study"), graph)
    assert path.endswith(".ncmodel")

    restored = load_model(path)
    assert [node.to_dict() for node in restored.nodes] == \
           [node.to_dict() for node in graph.nodes]


def test_a_model_from_a_newer_schema_is_refused(tmp_path):
    path = tmp_path / "future.ncmodel"
    path.write_text(json.dumps({"version": "9.0", "nodes": [], "connections": []}))
    with pytest.raises(ModelError, match="newer version"):
        load_model(str(path))


def test_a_file_that_is_not_a_model_is_refused(tmp_path):
    path = tmp_path / "notamodel.ncmodel"
    path.write_text(json.dumps({"hello": "world"}))
    with pytest.raises(ModelError, match="not an NCExplorer model"):
        load_model(str(path))


# ---------------------------------------------------------------------------
# Project storage
# ---------------------------------------------------------------------------

def test_a_project_carries_the_graph_and_the_compiled_pipeline(tmp_path, real_file,
                                                               allocator):
    """The graph is for editing; the pipeline is what every existing reader wants."""
    from ncexplorer_toolkit.core.project import (
        ProjectState, load_project, read_pipeline, save_project,
    )

    graph, *_ = _linear(real_file)
    state = ProjectState(pipeline=tuple(graph.compile(allocator)),
                         model=graph.to_dict())
    path = str(tmp_path / "study.ncx")
    save_project(path, state)

    restored = load_project(path)
    assert ModelGraph.from_dict(restored.model).topological_order() == \
           graph.topological_order()
    # The batch dialog reads this and knows nothing about graphs.
    assert len(read_pipeline(path)) == 2


def test_a_project_without_a_model_still_loads(tmp_path, real_file, allocator):
    """A file written before the graph existed must open unchanged."""
    from ncexplorer_toolkit.core.project import (
        ProjectState, load_project, save_project,
    )

    graph, *_ = _linear(real_file)
    path = str(tmp_path / "old.ncx")
    save_project(path, ProjectState(pipeline=tuple(graph.compile(allocator))))

    restored = load_project(path)
    assert restored.model == {}
    assert len(restored.pipeline) == 2


def test_an_export_names_intermediates_beside_the_result(tmp_path, real_file):
    """A script that reads a path under /var/folders runs on one machine only."""
    output = str(tmp_path / "anomaly.nc")
    graph, *_ = _linear(real_file, output=output)

    requests = graph.compile(portable_allocator(graph))
    intermediate = requests[0].output_files[0]

    assert intermediate == str(tmp_path / "anomaly_step1.nc")
    assert requests[1].input_files == (intermediate,)
    assert requests[1].output_files == (output,)


def test_a_portable_export_still_works_with_no_sink(real_file):
    graph = ModelGraph()
    source = graph.add(SOURCE, path=real_file)
    mean = graph.add(OPERATOR, operator="timmean")
    graph.connect(source.id, 0, mean.id, 0)

    request = graph.compile(portable_allocator(graph))[0]
    assert request.output_files[0].endswith("input_step1.nc")


# ---------------------------------------------------------------------------
# From a recorded session
# ---------------------------------------------------------------------------

def test_a_recorded_chain_becomes_a_graph(real_file):
    pipeline = [
        OperatorRequest("sellonlatbox", (real_file,), ("/tmp/step1.nc",),
                        ("0", "30", "-10", "10")),
        OperatorRequest("timmean", ("/tmp/step1.nc",), ("/tmp/step2.nc",)),
    ]
    graph = graph_from_pipeline(pipeline)

    operators = [node for node in graph.nodes if node.kind == OPERATOR]
    assert [node.operator for node in operators] == ["sellonlatbox", "timmean"]
    assert len([node for node in graph.nodes if node.kind == SOURCE]) == 1
    sinks = [node for node in graph.nodes if node.kind == SINK]
    assert len(sinks) == 1 and sinks[0].path == "/tmp/step2.nc"
    # The step that read what the previous one wrote must be wired, not re-sourced.
    assert graph.operands(operators[1].id) == [operators[0].id]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path, source, expected", [
    ("/tmp/out.nc", "", "/tmp/out.nc"),
    ("/tmp/out.grb", "", "/tmp/out.grb"),
    ("/tmp/out", "", "/tmp/out.nc"),
    ("/tmp/out.dat", "", "/tmp/out.nc"),
    ("/tmp/out.dat", "/data/in.grb", "/tmp/out.grb"),
])
def test_output_extensions_follow_the_same_rule_everywhere(path, source, expected):
    assert suggest_output_path(path, source) == expected


# ---------------------------------------------------------------------------
# Fusion — shown, never run
# ---------------------------------------------------------------------------

def test_a_linear_chain_fuses_into_one_command(real_file):
    graph, *_ = _linear(real_file)
    commands = fused_commands(graph)
    assert len(commands) == 1
    assert commands[0][:3] == ["cdo", "timmean", "-sellonlatbox,0,30,-10,10"]
    assert commands[0][-1] == "/tmp/result.nc"


def test_a_diamond_fuses_both_operands_into_the_outer_operator(real_file):
    graph, *_ = _diamond(real_file)
    commands = fused_commands(graph)
    assert len(commands) == 1
    assert commands[0] == ["cdo", "sub", "-timmean", real_file,
                           "-timmin", real_file, "/tmp/anomaly.nc"]


def test_a_kept_intermediate_is_not_fused_away(real_file):
    graph, source, box, mean, sink = _linear(real_file)
    graph.update_node(box.id, keep_output=True, path="/tmp/clipped.nc")
    commands = fused_commands(graph)
    assert len(commands) == 2, "a file the user asked for must still be written"


def test_a_fan_out_is_not_fused(real_file):
    """A node read twice must be computed once, so it cannot become inner."""
    graph = ModelGraph()
    source = graph.add(SOURCE, path=real_file)
    shared = graph.add(OPERATOR, operator="timmean")
    left = graph.add(OPERATOR, operator="timmin")
    right = graph.add(OPERATOR, operator="timmax")
    graph.connect(source.id, 0, shared.id, 0)
    graph.connect(shared.id, 0, left.id, 0)
    graph.connect(shared.id, 0, right.id, 0)

    commands = fused_commands(graph)
    assert len(commands) == 3


def test_info_and_split_nodes_are_never_inner(real_file):
    graph = ModelGraph()
    source = graph.add(SOURCE, path=real_file)
    info = graph.add(OPERATOR, operator="ntime")
    graph.connect(source.id, 0, info.id, 0)

    commands = fused_commands(graph)
    assert commands == [["cdo", "ntime", real_file]]


# ---------------------------------------------------------------------------
# What the installed engine actually does
# ---------------------------------------------------------------------------

cdo_required = pytest.mark.skipif(
    shutil.which("cdo") is None, reason="needs an installed CDO")


@cdo_required
def test_the_installed_engine_agrees_with_the_fusion_rules(tmp_path):
    """Pins the three rules the preview relies on, against the real binary.

    Checked rather than assumed: the rules are what make a chained one-liner
    safe to show somebody, and a build that disagrees would make the preview
    teach the wrong thing. CDO 2.6.0 was verified to reject an inner info
    operator and an inner split operator, and to accept a nested chain.
    """
    xr = pytest.importorskip("xarray")
    numpy = pytest.importorskip("numpy")

    values = numpy.arange(4 * 3 * 4, dtype=float).reshape(4, 3, 4)
    dataset = xr.Dataset(
        {"t2m": (("time", "lat", "lon"), values)},
        coords={
            "time": ("time", numpy.arange(4),
                     {"units": "days since 2000-01-01", "calendar": "standard"}),
            "lat": ("lat", numpy.linspace(-60, 60, 3), {"units": "degrees_north"}),
            "lon": ("lon", numpy.linspace(-135, 135, 4), {"units": "degrees_east"}),
        },
    )
    source = tmp_path / "in.nc"
    dataset.to_netcdf(source)
    dataset.close()

    def run(*arguments):
        return subprocess.run(["cdo", *arguments], capture_output=True, text=True,
                              timeout=120)

    nested = run("timmean", "-timmin", str(source), str(tmp_path / "chained.nc"))
    assert nested.returncode == 0, nested.stderr

    inner_info = run("timmean", "-ntime", str(source), str(tmp_path / "info.nc"))
    assert inner_info.returncode != 0, "an info operator must not be usable inside a chain"

    inner_split = run("timmean", "-splitmon", str(source), "pre_",
                      str(tmp_path / "split.nc"))
    assert inner_split.returncode != 0, "a split operator must not be usable inside a chain"


# ---------------------------------------------------------------------------
# The dock
# ---------------------------------------------------------------------------

def test_the_dock_builds_accepts_a_node_and_validates(qapp, real_file):
    """A smoke test: construction, one node, live validation. No CDO run."""
    from ncexplorer_toolkit.gui.main_window import NCExplorerOperatorGUI

    window = NCExplorerOperatorGUI()
    try:
        dock = window.model_builder
        assert dock.palette.topLevelItemCount() > 0

        source = dock.canvas.graph.add(SOURCE, path=real_file)
        node = dock.canvas.add_operator("timmean")
        sink = dock.canvas.graph.add(SINK, path=str(real_file) + ".out.nc")
        dock.canvas.graph.connect(source.id, 0, node.id, 0)
        dock.canvas.graph.connect(node.id, 0, sink.id, 0)
        dock.canvas.rebuild()
        dock._revalidate()

        assert not [i for i in dock._issues if i.severity == ERROR]
        assert "cdo timmean" in dock.preview.toPlainText()

        dock.fuse_box.setChecked(True)
        assert "cdo timmean" in dock.preview.toPlainText()

        pipeline = dock.compiled_pipeline()
        assert len(pipeline) == 1 and pipeline[0].operator == "timmean"
    finally:
        window.close()


def test_the_palette_search_filters_and_is_fuzzy(qapp):
    """The builder's search box uses the command palette's ranking.

    ``timmean`` contains no "tmn", so the substring filter this replaced found
    nothing for that query. Categories with no surviving member collapse.
    """
    from ncexplorer_toolkit.gui.main_window import NCExplorerOperatorGUI

    window = NCExplorerOperatorGUI()
    try:
        dock = window.model_builder
        dock.search.setText("tmn")

        visible = []
        for index in range(dock.palette.topLevelItemCount()):
            parent = dock.palette.topLevelItem(index)
            if parent.isHidden():
                continue
            for row in range(parent.childCount()):
                child = parent.child(row)
                if not child.isHidden():
                    visible.append(child.data(0, Qt.ItemDataRole.UserRole))

        assert "timmean" in visible
        assert 0 < len(visible) < dock.palette.topLevelItemCount() * 20

        # Clearing brings everything back.
        dock.search.clear()
        assert not dock.palette.topLevelItem(0).isHidden()
    finally:
        window.close()


def test_a_project_of_a_drawn_model_still_offers_a_pipeline(qapp, tmp_path,
                                                            real_file):
    """A model that was drawn rather than run must not store an empty pipeline."""
    from ncexplorer_toolkit.core.project import read_pipeline, save_project
    from ncexplorer_toolkit.gui.main_window import NCExplorerOperatorGUI

    window = NCExplorerOperatorGUI()
    try:
        graph = window.model_builder.graph
        source = graph.add(SOURCE, path=real_file)
        mean = graph.add(OPERATOR, operator="timmean")
        sink = graph.add(SINK, path=str(tmp_path / "out.nc"))
        graph.connect(source.id, 0, mean.id, 0)
        graph.connect(mean.id, 0, sink.id, 0)

        assert not len(window.session_dock.log), "nothing has been run"

        path = str(tmp_path / "drawn.ncx")
        save_project(path, window._capture_project_state())

        # This is what the batch dialog's "from a project file" reads.
        assert [request.operator for request in read_pipeline(path)] == ["timmean"]
    finally:
        window.close()


@pytest.fixture
def folder_of_inputs(tmp_path):
    """A folder of five .nc files, one nested, and one file that is not data."""
    folder = tmp_path / "monthly"
    (folder / "sub").mkdir(parents=True)
    for number in range(1, 6):
        (folder / f"m{number:02d}.nc").write_bytes(b"stand-in")
    (folder / "sub" / "deep.nc").write_bytes(b"stand-in")
    (folder / "notes.txt").write_text("not data")
    return folder


def test_the_folder_dialog_lists_ticks_and_filters(qapp, folder_of_inputs):
    from ncexplorer_toolkit.gui.model_builder import FolderInputDialog

    dialog = FolderInputDialog(None, str(folder_of_inputs))
    try:
        names = [Path(path).name for path in dialog.selected_paths()]
        assert names == ["m01.nc", "m02.nc", "m03.nc", "m04.nc", "m05.nc"]
        assert dialog.file_list.count() == 5, "notes.txt must not match *.nc"

        # Everything found starts ticked: the common case is "all of them".
        assert len(dialog.selected_paths()) == dialog.file_list.count()

        dialog._set_all(False)
        assert dialog.selected_paths() == []
        assert not dialog._ok_button.isEnabled(), "adding nothing is not an action"

        dialog._set_all(True)
        assert len(dialog.selected_paths()) == 5

        dialog.recursive_box.setChecked(True)
        assert "deep.nc" in [Path(p).name for p in dialog.selected_paths()]

        dialog.recursive_box.setChecked(False)
        dialog.pattern_box.setCurrentText("*")
        assert any(path.endswith(".txt") for path in dialog.selected_paths())
    finally:
        dialog.deleteLater()


def test_a_folder_is_one_node_and_one_wire(folder_of_inputs):
    """Thirty source boxes fanning into mergetime is a canvas nobody can read."""
    paths = sorted(str(path) for path in folder_of_inputs.glob("*.nc"))

    graph = ModelGraph()
    folder = graph.add(FOLDER, path=str(folder_of_inputs), paths=tuple(paths),
                       pattern="*.nc")
    merge = graph.add(OPERATOR, operator="mergetime")
    sink = graph.add(SINK, path=str(folder_of_inputs / "merged.nc"))
    graph.connect(folder.id, 0, merge.id, 0)
    graph.connect(merge.id, 0, sink.id, 0)

    assert len(graph.connections) == 2, "one wire, whatever the file count"
    assert len(graph.nodes) == 3

    # One wire, five operands: the arity rules count files, not edges.
    assert graph.input_count(merge.id) == 5
    assert not _errors(graph)

    request = graph.compile(lambda suffix: "/tmp/x" + suffix)[0]
    assert request.input_files == tuple(paths), "operand order is the file order"
    assert request.output_files == (str(folder_of_inputs / "merged.nc"),)


def test_a_folders_file_count_is_what_arity_is_checked_against(folder_of_inputs):
    """Five files into a one-input operator is five inputs, and an error."""
    paths = sorted(str(path) for path in folder_of_inputs.glob("*.nc"))

    graph = ModelGraph()
    folder = graph.add(FOLDER, path=str(folder_of_inputs), paths=tuple(paths))
    mean = graph.add(OPERATOR, operator="timmean")
    graph.connect(folder.id, 0, mean.id, 0)

    issues = [i for i in _errors(graph) if i.node == mean.id]
    assert any("expected 1 input file(s), got 5" in issue.message for issue in issues)


def test_a_folder_naming_files_that_moved_says_which(tmp_path, folder_of_inputs):
    graph = ModelGraph()
    present = str(folder_of_inputs / "m01.nc")
    folder = graph.add(FOLDER, path=str(folder_of_inputs),
                       paths=(present, str(tmp_path / "gone.nc")))

    issues = [i for i in _errors(graph) if i.node == folder.id]
    assert issues
    assert "1 of 2 selected file(s) no longer exist" in issues[0].message
    assert "gone.nc" in issues[0].message


def test_an_empty_folder_node_is_an_error():
    graph = ModelGraph()
    folder = graph.add(FOLDER, path="/tmp/somewhere")
    assert any("no files selected" in issue.message
               for issue in _errors(graph) if issue.node == folder.id)


def test_a_folder_round_trips_with_its_files_and_pattern(folder_of_inputs):
    paths = tuple(sorted(str(path) for path in folder_of_inputs.glob("*.nc")))
    graph = ModelGraph()
    graph.add(FOLDER, path=str(folder_of_inputs), paths=paths, pattern="*.nc",
              label="monthly means")

    restored = ModelGraph.from_dict(json.loads(json.dumps(graph.to_dict())))
    node = restored.nodes[0]
    assert node.kind == FOLDER
    assert node.paths == paths
    assert node.pattern == "*.nc"
    assert node.label == "monthly means"


def test_the_folder_picker_makes_one_node_and_keeps_wires_when_edited(
        qapp, folder_of_inputs, monkeypatch):
    from ncexplorer_toolkit.gui import model_builder as module
    from ncexplorer_toolkit.gui.main_window import NCExplorerOperatorGUI

    paths = sorted(str(path) for path in folder_of_inputs.glob("*.nc"))

    class _Picked:
        """Stands in for the dialog, so no window has to be driven."""

        def __init__(self, chosen):
            self._chosen = chosen

        def exec(self):
            return module.QDialog.DialogCode.Accepted

        def selected_paths(self):
            return list(self._chosen)

        def folder(self):
            return str(folder_of_inputs)

        def pattern(self):
            return "*.nc"

    window = NCExplorerOperatorGUI()
    try:
        builder = window.model_builder
        merge = builder.canvas.add_operator("mergetime")
        builder.canvas.focus_node(merge.id)
        assert builder._fillable_target() == merge.id

        monkeypatch.setattr(module, "FolderInputDialog",
                            lambda *a, **k: _Picked(paths))
        node_id = builder.add_inputs_from_folder()

        assert builder.graph.node(node_id).kind == FOLDER
        assert builder.graph.node(node_id).paths == tuple(paths)
        assert builder.graph.operands(merge.id) == [node_id], "exactly one wire"
        assert builder.graph.input_count(merge.id) == 5

        # Re-picking must not cost the connection that was already drawn.
        monkeypatch.setattr(module, "FolderInputDialog",
                            lambda *a, **k: _Picked(paths[:2]))
        builder.edit_folder_node(node_id)

        assert builder.graph.node(node_id).paths == tuple(paths[:2])
        assert builder.graph.operands(merge.id) == [node_id]
        assert builder.graph.input_count(merge.id) == 2
    finally:
        window.close()


def test_a_full_fixed_arity_operator_is_not_offered_as_a_folder_target(
        qapp, folder_of_inputs):
    """Filling a full `sub` would silently drop every file after the first."""
    from ncexplorer_toolkit.gui.main_window import NCExplorerOperatorGUI

    window = NCExplorerOperatorGUI()
    try:
        builder = window.model_builder
        difference = builder.canvas.add_operator("sub")
        paths = sorted(str(path) for path in folder_of_inputs.glob("*.nc"))

        first = builder.graph.add(SOURCE, path=paths[0])
        builder.graph.connect(first.id, 0, difference.id, 0)
        builder.canvas.focus_node(difference.id)
        assert builder._fillable_target() == difference.id, "one port is still free"

        second = builder.graph.add(SOURCE, path=paths[1])
        builder.graph.connect(second.id, 0, difference.id, 1)
        builder.canvas.focus_node(difference.id)
        assert builder._fillable_target() == "", "a full operator takes no more"
    finally:
        window.close()


# ---------------------------------------------------------------------------
# Output extensions
# ---------------------------------------------------------------------------

def test_every_extension_issue_is_findable_and_carries_its_fix(real_file):
    graph, source, box, mean, sink = _linear(real_file, output="/tmp/result.dat")
    issues = graph.extension_issues()

    assert len(issues) == 1
    assert issues[0].node == sink.id
    assert issues[0].kind == EXTENSION
    assert issues[0].suggestion == "/tmp/result.nc"


def test_a_kept_intermediate_with_a_bad_extension_is_caught(real_file):
    """It is written by the engine *and* read back, so it matters twice over."""
    graph, source, box, mean, sink = _linear(real_file)
    graph.update_node(box.id, keep_output=True, path="/tmp/clipped.dat")

    issues = graph.extension_issues()
    assert [issue.node for issue in issues] == [box.id]
    assert issues[0].suggestion == "/tmp/clipped.nc"


def test_a_kept_intermediate_inherits_the_input_format(tmp_path):
    grib = tmp_path / "in.grb"
    grib.write_bytes(b"stand-in")

    graph = ModelGraph()
    source = graph.add(SOURCE, path=str(grib))
    mean = graph.add(OPERATOR, operator="timmean", keep_output=True,
                     path=str(tmp_path / "kept"))
    sink = graph.add(SINK, path=str(tmp_path / "out.grb"))
    graph.connect(source.id, 0, mean.id, 0)
    graph.connect(mean.id, 0, sink.id, 0)

    issues = graph.extension_issues()
    assert [issue.suggestion for issue in issues] == [str(tmp_path / "kept.grb")]


def test_applying_the_suggestions_clears_them(real_file):
    graph, source, box, mean, sink = _linear(real_file, output="/tmp/result.dat")
    graph.update_node(box.id, keep_output=True, path="/tmp/clipped.dat")

    issues = graph.extension_issues()
    assert len(issues) == 2

    assert graph.apply_suggestions(issues) == 2
    assert graph.extension_issues() == []
    assert graph.node(sink.id).path == "/tmp/result.nc"
    assert graph.node(box.id).path == "/tmp/clipped.nc"


def test_a_corrected_intermediate_is_what_the_next_step_reads(real_file, allocator):
    graph, source, box, mean, sink = _linear(real_file, output="/tmp/result.nc")
    graph.update_node(box.id, keep_output=True, path="/tmp/clipped.dat")
    graph.apply_suggestions(graph.extension_issues())

    requests = graph.compile(allocator)
    assert requests[0].output_files == ("/tmp/clipped.nc",)
    assert requests[1].input_files == ("/tmp/clipped.nc",), \
        "the downstream step must read the corrected name, not the old one"


def test_a_split_operators_prefix_is_not_asked_for_an_extension(real_file):
    """splitmon writes tas_01.nc itself; the target is a prefix, not a file."""
    graph = ModelGraph()
    source = graph.add(SOURCE, path=real_file)
    split = graph.add(OPERATOR, operator="splitmon")
    sink = graph.add(SINK, path="/tmp/month_")
    graph.connect(source.id, 0, split.id, 0)
    graph.connect(split.id, 0, sink.id, 0)

    assert graph.extension_issues() == []
    assert not _errors(graph)


def test_an_info_nodes_text_capture_is_not_checked_as_a_data_format(real_file,
                                                                    tmp_path):
    """A .txt reading is text; the engine's output formats do not apply to it."""
    graph = ModelGraph()
    source = graph.add(SOURCE, path=real_file)
    info = graph.add(OPERATOR, operator="sinfo", path=str(tmp_path / "report.txt"))
    graph.connect(source.id, 0, info.id, 0)

    assert graph.extension_issues() == []
    assert not _errors(graph)


def test_temporary_intermediates_always_get_a_usable_extension(real_file,
                                                                allocator):
    """Nothing the compiler names itself may fall into the same trap."""
    graph, *_ = _diamond(real_file, output="/tmp/anomaly.grb")
    for request in graph.compile(allocator):
        for path in request.output_files:
            assert Path(path).suffix.lower() in OUTPUT_EXTENSIONS


def test_the_run_gate_offers_to_fix_the_names(qapp, tmp_path, real_file,
                                              monkeypatch):
    from ncexplorer_toolkit.gui import model_builder as module
    from ncexplorer_toolkit.gui.main_window import NCExplorerOperatorGUI

    window = NCExplorerOperatorGUI()
    try:
        builder = window.model_builder
        graph = builder.graph
        source = graph.add(SOURCE, path=real_file)
        mean = graph.add(OPERATOR, operator="timmean")
        sink = graph.add(SINK, path=str(tmp_path / "result.dat"))
        graph.connect(source.id, 0, mean.id, 0)
        graph.connect(mean.id, 0, sink.id, 0)
        builder.canvas.rebuild()
        builder._revalidate()

        # No errors, so nothing else is standing between this model and a run.
        assert not [i for i in builder._issues if i.severity == ERROR]

        clicks = {}

        def _fake_exec(box):
            # Chosen by label, not by index: Qt reorders a message box's buttons
            # by role, so buttons() comes back as Fix / Cancel / Run anyway.
            clicks["buttons"] = [button.text() for button in box.buttons()]
            wanted = next(b for b in box.buttons() if b.text() == clicks["choose"])
            box.clickedButton = lambda: wanted

        monkeypatch.setattr(module.QMessageBox, "exec", _fake_exec)

        clicks["choose"] = "Cancel"
        assert builder._settle_extensions() is False, "Cancel must stop the run"
        assert graph.node(sink.id).path.endswith(".dat"), "Cancel changes nothing"
        assert "Fix and run" in clicks["buttons"]
        assert "Run anyway" in clicks["buttons"]

        clicks["choose"] = "Run anyway"
        assert builder._settle_extensions() is True, "the user may overrule this"
        assert graph.node(sink.id).path.endswith(".dat"), "Run anyway changes nothing"

        clicks["choose"] = "Fix and run"
        assert builder._settle_extensions() is True
        assert graph.node(sink.id).path == str(tmp_path / "result.nc")
        assert builder.graph.extension_issues(builder.catalog) == []
    finally:
        window.close()


def test_a_clean_model_passes_the_gate_without_a_dialog(qapp, tmp_path, real_file,
                                                        monkeypatch):
    from ncexplorer_toolkit.gui import model_builder as module
    from ncexplorer_toolkit.gui.main_window import NCExplorerOperatorGUI

    window = NCExplorerOperatorGUI()
    try:
        builder = window.model_builder
        graph = builder.graph
        source = graph.add(SOURCE, path=real_file)
        mean = graph.add(OPERATOR, operator="timmean")
        sink = graph.add(SINK, path=str(tmp_path / "result.nc"))
        graph.connect(source.id, 0, mean.id, 0)
        graph.connect(mean.id, 0, sink.id, 0)
        builder.canvas.rebuild()
        builder._revalidate()

        def _explode(_box):
            raise AssertionError("a clean model must not be interrupted")

        monkeypatch.setattr(module.QMessageBox, "exec", _explode)
        assert builder._settle_extensions() is True
    finally:
        window.close()


# ---------------------------------------------------------------------------
# Keeping what an info operator prints
# ---------------------------------------------------------------------------

def test_an_info_node_compiles_its_path_into_a_stdout_capture(real_file, tmp_path,
                                                              allocator):
    """nout == 0 writes no file, so `path` is where its printed reading goes."""
    report = str(tmp_path / "report.txt")

    graph = ModelGraph()
    source = graph.add(SOURCE, path=real_file)
    info = graph.add(OPERATOR, operator="sinfo", path=report)
    graph.connect(source.id, 0, info.id, 0)

    request = graph.compile(allocator)[0]
    assert request.nout == 0
    assert request.output_files == (), "the engine is given no output target"
    assert request.stdout_file == report
    assert not _errors(graph)


def test_the_capture_is_a_redirection_not_an_argument(real_file, tmp_path,
                                                      allocator):
    """`>` is something the shell does; it must never reach the process argv."""
    report = str(tmp_path / "report.txt")
    graph = ModelGraph()
    source = graph.add(SOURCE, path=real_file)
    info = graph.add(OPERATOR, operator="ntime", path=report)
    graph.connect(source.id, 0, info.id, 0)

    request = graph.compile(allocator)[0]
    assert ">" not in request.arguments()
    assert report not in request.arguments()
    assert request.command_line().endswith(f"> {report}")


def test_an_info_node_without_a_path_captures_nothing(real_file, allocator):
    graph = ModelGraph()
    source = graph.add(SOURCE, path=real_file)
    info = graph.add(OPERATOR, operator="ntime")
    graph.connect(source.id, 0, info.id, 0)

    assert graph.compile(allocator)[0].stdout_file == ""


def test_a_captured_reading_is_written_when_the_step_finishes(tmp_path):
    from ncexplorer_toolkit.core.session_log import (
        OperatorRequest as Request, write_stdout_capture,
    )

    report = tmp_path / "report.txt"
    request = Request("ntime", ("in.nc",), (), nout=0, stdout_file=str(report))

    assert write_stdout_capture(request, "12\n")
    assert report.read_text() == "12\n"

    # A request that asked for nothing must not create a file.
    assert not write_stdout_capture(Request("ntime", ("in.nc",), (), nout=0), "12\n")


def test_a_capture_survives_the_project_round_trip(tmp_path, real_file):
    from ncexplorer_toolkit.core.project import (
        ProjectState, load_project, save_project,
    )

    report = str(tmp_path / "report.txt")
    graph = ModelGraph()
    source = graph.add(SOURCE, path=real_file)
    info = graph.add(OPERATOR, operator="sinfo", path=report)
    graph.connect(source.id, 0, info.id, 0)

    pipeline = tuple(graph.compile(lambda suffix: "/tmp/x" + suffix))
    path = str(tmp_path / "info.ncx")
    save_project(path, ProjectState(pipeline=pipeline, model=graph.to_dict()))

    assert load_project(path).pipeline[0].stdout_file == report


def test_an_exported_script_redirects_the_reading(real_file, tmp_path):
    from ncexplorer_toolkit.core.session_log import (
        OK, SessionStep, export_makefile, export_shell,
    )

    report = str(tmp_path / "report.txt")
    graph = ModelGraph()
    source = graph.add(SOURCE, path=real_file)
    info = graph.add(OPERATOR, operator="ntime", path=report)
    graph.connect(source.id, 0, info.id, 0)

    steps = [SessionStep(request=request, status=OK)
             for request in graph.compile(lambda suffix: "/tmp/x" + suffix)]

    assert f"> {report}" in export_shell(steps)

    makefile = export_makefile(steps)
    # A captured reading is a real file, so make can date-stamp it rather than
    # falling back to a phony target.
    assert report in makefile
    assert ".PHONY: all clean\n" in makefile
    assert f"rm -f {report}" in makefile


def test_a_batch_of_an_info_pipeline_keeps_the_text_extension(tmp_path):
    from ncexplorer_toolkit.core.batch import keeps_text, plan_job
    from ncexplorer_toolkit.core.session_log import OperatorRequest as Request

    pipeline = [Request("sinfo", ("original.nc",), (), nout=0,
                        stdout_file="original.txt")]
    assert keeps_text(pipeline)

    planned = plan_job(pipeline, "/data/june.nc", str(tmp_path / "june.txt"),
                       lambda suffix: str(tmp_path / f"tmp{suffix}"))
    assert planned[0].input_files == ("/data/june.nc",)
    assert planned[0].stdout_file == str(tmp_path / "june.txt")


# ---------------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------------

def test_the_builder_is_a_window_with_the_usual_buttons(qapp):
    """A dock cannot minimise, maximise or go full screen; this has to."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QDockWidget, QMainWindow

    from ncexplorer_toolkit.gui.main_window import NCExplorerOperatorGUI

    window = NCExplorerOperatorGUI()
    try:
        builder = window.model_builder
        assert isinstance(builder, QMainWindow)
        assert not isinstance(builder, QDockWidget)

        flags = builder.windowFlags()
        for hint in (Qt.WindowType.Window,
                     Qt.WindowType.WindowMinimizeButtonHint,
                     Qt.WindowType.WindowMaximizeButtonHint,
                     Qt.WindowType.WindowCloseButtonHint):
            assert flags & hint, f"{hint} missing"
    finally:
        window.close()


def test_there_is_no_full_screen_affordance(qapp):
    """An ordinary window: the window manager's buttons and nothing of our own."""
    from ncexplorer_toolkit.gui.main_window import NCExplorerOperatorGUI

    window = NCExplorerOperatorGUI()
    try:
        builder = window.model_builder
        window.toggle_model_builder_window(True)

        assert not hasattr(builder, "full_screen_button")
        assert not hasattr(builder, "toggle_full_screen")
        assert not builder.isFullScreen()
    finally:
        window.close()


def test_closing_the_window_hides_it_and_keeps_the_model(qapp, real_file):
    """Closing is closing a view; the model belongs to the project."""
    from ncexplorer_toolkit.gui.main_window import NCExplorerOperatorGUI

    window = NCExplorerOperatorGUI()
    try:
        builder = window.model_builder
        builder.canvas.add_operator("timmean")
        window.toggle_model_builder_window(True)
        assert builder.isVisible()

        builder.close()
        assert not builder.isVisible()
        assert len(builder.graph) == 1, "closing must not discard the graph"
        assert not window.menu_bar.model_builder_action.isChecked()
    finally:
        window.close()


def test_the_model_menu_is_top_level_and_view_no_longer_carries_it(qapp):
    from ncexplorer_toolkit.gui.main_window import NCExplorerOperatorGUI

    window = NCExplorerOperatorGUI()
    try:
        titles = [action.text() for action in window.menu_bar.actions()]
        assert titles == ["&File", "&View", "&Layer", "&Model", "&Help"]

        view = next(a.menu() for a in window.menu_bar.actions() if a.text() == "&View")
        assert not any("Model" in (a.text() or "") for a in view.actions())

        model = next(a.menu() for a in window.menu_bar.actions() if a.text() == "&Model")
        entries = [a.text() for a in model.actions() if not a.isSeparator()]
        for expected in ("Model &Builder", "&Open Model...", "&Save Model...",
                         "Build from &Session", "Add Inputs from &Folder...",
                         "&Run Model over a Folder..."):
            assert expected in entries, f"{expected} missing from the Model menu"
    finally:
        window.close()


def test_an_illegal_wire_is_refused_out_loud_not_silently(qapp, monkeypatch):
    """A drop the graph rejects must tell the user why, not quietly do nothing."""
    from ncexplorer_toolkit.gui import model_builder as module

    graph = ModelGraph()
    first = graph.add(OPERATOR, operator="timmean")
    second = graph.add(OPERATOR, operator="timmin")
    graph.connect(first.id, 0, second.id, 0)

    with pytest.raises(ModelError) as raised:
        graph.connect(second.id, 0, first.id, 0)
    reason = str(raised.value)
    assert "loop" in reason

    shown = []
    monkeypatch.setattr(module.QMessageBox, "information",
                        lambda *args, **kwargs: shown.append(args[-1]))
    canvas = module.ModelCanvas(graph, OperatorCatalog())
    try:
        # The canvas surfaces exactly the message ModelGraph raised, which is
        # what keeps the editor and a script driving the same graph in agreement.
        canvas._reject(reason)
        assert shown == [reason]
    finally:
        canvas.deleteLater()


def test_a_toggled_checkbox_outlives_the_form_it_rebuilds(qapp, real_file):
    """Committing from ``toggled`` must not delete the box mid-click.

    "Keep this result as a file" commits from ``toggled``, and the form draws a
    path row only when it is on — so committing it rebuilds the very form the
    box sits in. When that rebuild destroyed the row's widgets outright, Qt
    returned from the signal into ``QCheckBox::nextCheckState`` on freed memory
    and the application died with SIGSEGV under
    ``QAbstractButton::mouseReleaseEvent``. Driven through a real mouse click
    rather than ``setChecked`` because ``setChecked`` never enters the button's
    event handling and so cannot catch this.
    """
    from PyQt6 import sip
    from PyQt6.QtCore import QPoint
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QCheckBox

    from ncexplorer_toolkit.gui.main_window import NCExplorerOperatorGUI

    window = NCExplorerOperatorGUI()
    try:
        dock = window.model_builder
        source = dock.canvas.graph.add(SOURCE, path=real_file)
        node = dock.canvas.add_operator("timmean")
        dock.canvas.graph.connect(source.id, 0, node.id, 0)
        dock.canvas.rebuild()
        dock.parameters.show_node(node.id)
        # The builder is its own window, so showing the main one lays nothing
        # out here — and an unlaid-out checkbox has no rectangle for the click
        # to land in.
        dock.show()
        qapp.processEvents()

        boxes = [box for box in dock.parameters.findChildren(QCheckBox)
                 if "Keep this result" in box.text() and box.isVisible()]
        assert boxes, "the keep-output checkbox should be on an operator's form"
        keep = boxes[0]

        # Clicking the indicator, which is what a user hits.
        QTest.mouseClick(keep, Qt.MouseButton.LeftButton, pos=QPoint(8, 10))

        assert not sip.isdeleted(keep), (
            "the checkbox was destroyed inside its own toggled handler; Qt is "
            "about to return into it")
        assert dock.canvas.graph.node(node.id).keep_output

        # And the rebuild still happened: the path row the toggle asks for.
        qapp.processEvents()
        assert not sip.isdeleted(keep)
    finally:
        window.close()
