"""Reading and writing ``.ncx`` project files.

A project is what turns NCExplorer from a tool you set up again every morning
into one that remembers where you were: which files are on the map, how they are
coloured, which timestep is showing, where the view is pointed, and what has
been run.

Three decisions worth stating:

* **A zip container, not a bare JSON file.** Nothing inside needs zip today —
  ``manifest.json`` and ``project.json`` would both fit in one document. It is
  chosen for what comes next: a thumbnail, an exported script, a copy of a small
  input. Adding those to a zip is a new entry; adding them to a JSON file is a
  new format. Both members come from the standard library, so the file format
  costs no dependency.
* **Every source path is stored twice, absolutely and relative to the project.**
  A project mailed to a colleague, or moved with its data to another disk, keeps
  working through the relative path; a project whose data lives somewhere fixed
  and far away keeps working through the absolute one. The relative path is
  tried first, because when both resolve, the copy sitting beside the project is
  the one the user means.
* **A missing file is not a failed load.** Loading is partial by design: the
  layers that resolve are returned along with a list of the ones that did not,
  so the caller can report exactly which files are gone and let the user
  relocate or drop them. Refusing the whole project because one layer of nine
  moved would be the least useful possible response.

The module holds no Qt imports at all — it maps between plain dictionaries and
:class:`ProjectState`, and ``gui/main_window.py`` owns everything about turning
that state back into a window.
"""

from __future__ import annotations

import json
import logging
import os
import zipfile
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from ..__version__ import APP_NAME, __version__
from .session_log import OperatorRequest

logger = logging.getLogger(__name__)

PROJECT_SUFFIX = ".ncx"

#: Bumped on any change to what the members below contain. The major part is
#: the compatibility promise: a reader refuses a file whose major is higher than
#: its own, because that file may rely on meaning this build does not have.
#: A higher minor is fine — unknown keys are ignored everywhere.
#:
#: 1.1 added ``model``, the processing graph. Deliberately a *minor* bump: the
#: key is additive, a 1.0 reader ignores it and still finds the ``pipeline`` it
#: knows, and making every file this build writes unreadable by the previous one
#: would buy nothing.
SCHEMA_VERSION = "1.1"

MANIFEST_MEMBER = "manifest.json"
STATE_MEMBER = "project.json"

#: The NetCDF fields a project stores. Only the *selection* — everything else
#: ``NetCDFProperties`` holds (the variable list, the time axis, the global
#: attributes) is read back out of the file when the layer loads, and storing a
#: copy would both bloat the project with thousands of timestamps and risk
#: overwriting freshly-read metadata with a stale duplicate of it.
NETCDF_SELECTION_KEYS = (
    "current_variable",
    "current_time_index",
    "current_band",
    "time_dimension",
    "band_dimension",
)


class ProjectError(Exception):
    """A project file that cannot be read at all."""


def schema_major(version: str) -> int:
    """The major part of a schema version string; 0 when it makes no sense."""
    try:
        return int(str(version).split(".")[0])
    except (ValueError, AttributeError):
        return 0


# ---------------------------------------------------------------------------
# The state itself
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class LayerState:
    """One layer as a project remembers it.

    ``style`` and ``netcdf`` are the dictionaries ``LayerStyleProperties`` and
    ``NetCDFProperties`` already produce. Passing those through whole, rather
    than picking out a handful of fields, means a property added to either class
    is saved from that day on without this module hearing about it.
    """

    name: str
    source_path: str = ""
    relative_path: str = ""
    layer_type: str = ""
    visible: bool = True
    zorder: float = 0.0
    style: dict[str, Any] = field(default_factory=dict)
    netcdf: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "source_path": self.source_path,
            "relative_path": self.relative_path,
            "layer_type": self.layer_type,
            "visible": self.visible,
            "zorder": self.zorder,
            "style": self.style,
            "netcdf": self.netcdf,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LayerState":
        """Build from a stored dictionary, ignoring anything unrecognised."""
        return cls(
            name=str(data.get("name", "")),
            source_path=str(data.get("source_path", "")),
            relative_path=str(data.get("relative_path", "")),
            layer_type=str(data.get("layer_type", "")),
            visible=bool(data.get("visible", True)),
            zorder=float(data.get("zorder", 0.0) or 0.0),
            style=dict(data.get("style") or {}),
            netcdf=dict(data.get("netcdf") or {}),
        )


@dataclass(frozen=True, slots=True)
class CanvasState:
    """Where the map was pointed and what was drawn over it."""

    extent: tuple[float, float, float, float] = (-180.0, 180.0, -90.0, 90.0)
    projection: str = "PlateCarree"
    basemap: str = "None"
    graticule: bool = False
    scalebar: bool = False
    colorbar: bool = False
    colorbar_position: str = "right"
    theme: str = "light"

    def to_dict(self) -> dict[str, Any]:
        return {
            "extent": list(self.extent),
            "projection": self.projection,
            "basemap": self.basemap,
            "graticule": self.graticule,
            "scalebar": self.scalebar,
            "colorbar": self.colorbar,
            "colorbar_position": self.colorbar_position,
            "theme": self.theme,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CanvasState":
        extent = data.get("extent") or []
        default = cls()
        try:
            bounds = tuple(float(value) for value in extent)
        except (TypeError, ValueError):
            bounds = default.extent
        if len(bounds) != 4:
            bounds = default.extent
        return cls(
            extent=bounds,  # type: ignore[arg-type]
            projection=str(data.get("projection", default.projection)),
            basemap=str(data.get("basemap", default.basemap)),
            graticule=bool(data.get("graticule", default.graticule)),
            scalebar=bool(data.get("scalebar", default.scalebar)),
            colorbar=bool(data.get("colorbar", default.colorbar)),
            colorbar_position=str(data.get("colorbar_position", default.colorbar_position)),
            theme=str(data.get("theme", default.theme)),
        )


@dataclass(frozen=True, slots=True)
class ProjectState:
    """Everything a project file holds, ready to be written or applied."""

    layers: tuple[LayerState, ...] = ()
    canvas: CanvasState = field(default_factory=CanvasState)
    pipeline: tuple[OperatorRequest, ...] = ()
    #: The model builder's graph, as ``ModelGraph.to_dict()`` wrote it. Stored
    #: *alongside* ``pipeline`` rather than instead of it: the graph is strictly
    #: the richer of the two — it carries branches, node positions and captions
    #: a linear list cannot express — but every existing reader, the batch
    #: dialog's included, wants the compiled list and should keep finding it.
    #: Kept as a plain dictionary so this module needs no import from
    #: ``core/model.py``, which is what stops a project load depending on the
    #: model builder being present.
    model: dict[str, Any] = field(default_factory=dict)
    ui: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    modified_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "layers": [layer.to_dict() for layer in self.layers],
            "canvas": self.canvas.to_dict(),
            "pipeline": [request_to_dict(request) for request in self.pipeline],
            "model": self.model,
            "ui": self.ui,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProjectState":
        model = data.get("model")
        return cls(
            layers=tuple(
                LayerState.from_dict(item)
                for item in (data.get("layers") or [])
                if isinstance(item, dict)
            ),
            canvas=CanvasState.from_dict(data.get("canvas") or {}),
            pipeline=tuple(
                request_from_dict(item)
                for item in (data.get("pipeline") or [])
                if isinstance(item, dict)
            ),
            model=dict(model) if isinstance(model, dict) else {},
            ui=dict(data.get("ui") or {}),
        )


# ---------------------------------------------------------------------------
# Pipeline serialisation
# ---------------------------------------------------------------------------

def request_to_dict(request: OperatorRequest) -> dict[str, Any]:
    """One recorded operation as plain JSON types."""
    return {
        "operator": request.operator,
        "input_files": list(request.input_files),
        "output_files": list(request.output_files),
        "parameters": list(request.parameters),
        "nin": request.nin,
        "nout": request.nout,
        "stdout_file": request.stdout_file,
    }


def request_from_dict(data: dict[str, Any]) -> OperatorRequest:
    """Rebuild one operation, tolerating a file written by another version."""
    def strings(key: str) -> tuple[str, ...]:
        values = data.get(key) or []
        if isinstance(values, str):
            values = [values]
        return tuple(str(value) for value in values)

    def count(key: str, fallback: int) -> int:
        try:
            return int(data.get(key, fallback))
        except (TypeError, ValueError):
            return fallback

    return OperatorRequest(
        operator=str(data.get("operator", "")),
        input_files=strings("input_files"),
        output_files=strings("output_files"),
        parameters=strings("parameters"),
        nin=count("nin", 1),
        nout=count("nout", 1),
        stdout_file=str(data.get("stdout_file", "")),
    )


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def relativise(source: str, project_path: str) -> str:
    """``source`` relative to the project file, or "" when that is impossible.

    Windows puts files on separate drives with no relative path between them,
    and ``os.path.relpath`` raises rather than returning something unusable.
    An empty string simply means the absolute path is the only way back.
    """
    if not source or not project_path:
        return ""
    try:
        return os.path.relpath(os.path.abspath(source),
                               os.path.dirname(os.path.abspath(project_path)))
    except ValueError:
        return ""


def resolve_source(layer: LayerState, project_path: str) -> str:
    """Where this layer's file actually is now, or "" if it cannot be found.

    Relative first: a project and its data copied together to another machine
    should open there, and in that situation the absolute path still points at
    the original machine's layout — where a *different* file may well be
    sitting by now.
    """
    base = os.path.dirname(os.path.abspath(project_path)) if project_path else ""
    if layer.relative_path and base:
        candidate = os.path.normpath(os.path.join(base, layer.relative_path))
        if os.path.exists(candidate):
            return candidate
    if layer.source_path and os.path.exists(layer.source_path):
        return layer.source_path
    return ""


def resolve_layers(state: ProjectState,
                   project_path: str) -> tuple[list[tuple[LayerState, str]], list[LayerState]]:
    """Split a project's layers into the ones that were found and the ones lost."""
    found: list[tuple[LayerState, str]] = []
    missing: list[LayerState] = []
    for layer in state.layers:
        path = resolve_source(layer, project_path)
        if path:
            found.append((layer, path))
        else:
            missing.append(layer)
    return found, missing


# ---------------------------------------------------------------------------
# Saving and loading
# ---------------------------------------------------------------------------

def build_manifest(state: ProjectState, created_at: str = "") -> dict[str, Any]:
    """The metadata member: version, application, and the two timestamps."""
    now = datetime.now().isoformat(timespec="seconds")
    return {
        "schema_version": SCHEMA_VERSION,
        "application": APP_NAME,
        "app_version": __version__,
        "created_at": created_at or state.created_at or now,
        "modified_at": now,
    }


def save_project(path: str, state: ProjectState) -> ProjectState:
    """Write ``state`` to ``path``. Returns the state with its timestamps set.

    Source paths are relativised against ``path`` here rather than by the
    caller, because "relative to the project" has no meaning until the project
    has a location — which, for Save As, is only decided at this moment.
    """
    located = replace(state, layers=tuple(
        replace(layer, relative_path=relativise(layer.source_path, path))
        for layer in state.layers
    ))

    manifest = build_manifest(located)
    saved = replace(located,
                    created_at=manifest["created_at"],
                    modified_at=manifest["modified_at"])

    target = Path(path)
    # Written to a sibling and moved into place: an error part-way through would
    # otherwise leave a truncated zip where a working project used to be, and
    # the project a user loses that way is the one they had just spent the day
    # on. The move is atomic, so the file on disk is only ever the old project
    # or the complete new one.
    temporary = target.with_name(target.name + ".part")
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(MANIFEST_MEMBER, json.dumps(manifest, indent=2))
            # default=str for the same reason LayerPropertyManager uses it: a
            # style or selection value can arrive as a numpy scalar, and losing
            # a whole project to "Object of type float32 is not JSON
            # serializable" would be an absurd way to lose a day's work.
            archive.writestr(STATE_MEMBER,
                             json.dumps(saved.to_dict(), indent=2, default=str))
        os.replace(temporary, target)
    except (OSError, TypeError, ValueError) as exc:
        raise ProjectError(f"Could not write {path}: {exc}") from exc
    finally:
        # A successful move leaves nothing here, so this only ever cleans up
        # after a failure — and it is a ``finally`` rather than an ``except``
        # so that a failure of *any* kind takes the half-written sibling with
        # it. Leaving ``study.ncx.part`` lying beside the project only invites
        # someone to try opening it.
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            logger.debug("Could not remove the partial project %s", temporary)

    logger.info("Project saved to %s (%d layer(s), %d step(s))",
                path, len(saved.layers), len(saved.pipeline))
    return saved


def _read_members(path: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """The manifest and state dictionaries out of one project file."""
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            if MANIFEST_MEMBER not in names or STATE_MEMBER not in names:
                raise ProjectError(
                    f"{Path(path).name} is not an NCExplorer project: "
                    f"it has no {MANIFEST_MEMBER}."
                )
            manifest = json.loads(archive.read(MANIFEST_MEMBER).decode("utf-8"))
            state = json.loads(archive.read(STATE_MEMBER).decode("utf-8"))
    except ProjectError:
        raise
    except (OSError, zipfile.BadZipFile) as exc:
        raise ProjectError(f"Could not open {Path(path).name}: {exc}") from exc
    except (ValueError, KeyError) as exc:
        raise ProjectError(f"{Path(path).name} is damaged: {exc}") from exc

    if not isinstance(manifest, dict) or not isinstance(state, dict):
        raise ProjectError(f"{Path(path).name} is damaged: unexpected contents.")
    return manifest, state


def load_project(path: str) -> ProjectState:
    """Read one project file.

    Raises :class:`ProjectError` for a file that is not a project, is damaged,
    or was written by a newer major version of the format. A missing *layer*
    raises nothing at all — see :func:`resolve_layers`.
    """
    manifest, raw = _read_members(path)

    version = str(manifest.get("schema_version", "0"))
    if schema_major(version) > schema_major(SCHEMA_VERSION):
        raise ProjectError(
            f"{Path(path).name} was saved by a newer version of {APP_NAME} "
            f"(project format {version}; this build reads {SCHEMA_VERSION}). "
            "Update the application to open it."
        )

    state = ProjectState.from_dict(raw)
    state = replace(state,
                    created_at=str(manifest.get("created_at", "")),
                    modified_at=str(manifest.get("modified_at", "")))
    logger.info("Project loaded from %s (%d layer(s), %d step(s))",
                path, len(state.layers), len(state.pipeline))
    return state


def read_pipeline(path: str) -> list[OperatorRequest]:
    """Just the pipeline out of a project file, for the batch dialog."""
    return list(load_project(path).pipeline)


def suggest_project_path(directory: str, name: str = "project") -> str:
    """A default file name for Save As, with the right suffix."""
    stem = name or "project"
    if not stem.endswith(PROJECT_SUFFIX):
        stem += PROJECT_SUFFIX
    return str(Path(directory or Path.home()) / stem)


def ensure_suffix(path: str) -> str:
    """Give a chosen path the project suffix if the file dialog did not."""
    return path if path.lower().endswith(PROJECT_SUFFIX) else path + PROJECT_SUFFIX


def describe(state: ProjectState) -> str:
    """One line summarising a project, for a status bar."""
    return (f"{len(state.layers)} layer(s), {len(state.pipeline)} recorded step(s)"
            + (f", saved {state.modified_at}" if state.modified_at else ""))


def pipeline_from_steps(steps: Sequence) -> tuple[OperatorRequest, ...]:
    """The successful requests out of a sequence of session steps.

    Failed and cancelled steps are dropped: what is stored is a recipe, and a
    step that did not work is not part of one.
    """
    return tuple(step.request for step in steps if getattr(step, "succeeded", False))
