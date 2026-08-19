# Copyright (c) 2026 Manish Shivach
# SPDX-License-Identifier: MIT
"""Saved output must actually contain the overlays.

save_map() uses bbox_inches='tight', which recomputes the figure bounds from the
artists present — a good reason to check the rendered bytes rather than just
trusting that the artists exist.
"""


def load(canvas, path):
    canvas.load_netcdf(path)
    return next(iter(canvas.layers))


def save(canvas, path):
    canvas.save_map(str(path), dpi=72)
    assert path.exists(), "save_map produced no file"
    assert path.stat().st_size > 0, "save_map produced an empty file"
    return path.read_bytes()


def test_colorbar_changes_the_saved_output(canvas, nc_standard, tmp_path):
    load(canvas, nc_standard)

    canvas.colorbar_manager.set_visible(False)
    without = save(canvas, tmp_path / "no_colorbar.png")

    canvas.colorbar_manager.set_visible(True)
    with_bar = save(canvas, tmp_path / "with_colorbar.png")

    assert without != with_bar, "the colorbar is not reaching the rendered output"


def test_scalebar_changes_the_saved_output(canvas, nc_standard, tmp_path):
    load(canvas, nc_standard)

    canvas.scalebar_manager.set_visible(False)
    without = save(canvas, tmp_path / "no_scalebar.png")

    canvas.scalebar_manager.set_visible(True)
    with_bar = save(canvas, tmp_path / "with_scalebar.png")

    assert without != with_bar, "the scale bar is not reaching the rendered output"


def test_graticule_changes_the_saved_output(canvas, nc_standard, tmp_path):
    load(canvas, nc_standard)

    canvas.set_graticule(False)
    without = save(canvas, tmp_path / "no_graticule.png")

    canvas.set_graticule(True)
    with_lines = save(canvas, tmp_path / "with_graticule.png")

    assert without != with_lines, "the graticule is not reaching the rendered output"


def test_all_overlays_together_save(canvas, nc_standard, tmp_path):
    load(canvas, nc_standard)
    canvas.colorbar_manager.set_visible(True)
    canvas.scalebar_manager.set_visible(True)
    canvas.set_graticule(True)

    data = save(canvas, tmp_path / "everything.png")

    assert data.startswith(b"\x89PNG")


def test_every_colorbar_position_saves(canvas, nc_standard, tmp_path):
    load(canvas, nc_standard)
    canvas.colorbar_manager.set_visible(True)

    sizes = {}
    for position in canvas.colorbar_manager.POSITIONS:
        canvas.colorbar_manager.set_position(position)
        path = tmp_path / f"cb_{position}.png"
        sizes[position] = len(save(canvas, path))

    assert len(sizes) == 4
