"""Real SDK geometry and physics checks; no model API calls."""

import math

import bricklayer as bl
import numpy as np
import pytest
from bricklayer.geometry import Mat3, Pose, Position

from server.exporter import ExportRejected, LDU_SCALE, export_project, verify_build


def export(build):
    return export_project(build, job_id="f" * 32, prompt="test", size="small", style="classic", source_image="", max_parts=80)


def stack():
    build = bl.LegoBuild()
    build.add(bl.Element("3001", bl.Color.RED), (0, 0, 0))
    build.add(bl.Element("3001", bl.Color.BLUE), (0, 0, 1))
    return build


def xyz_matrix(x, y, z):
    cx, sx, cy, sy, cz, sz = math.cos(x), math.sin(x), math.cos(y), math.sin(y), math.cos(z), math.sin(z)
    return np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]]) @ np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]]) @ np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])


def test_stack_uses_real_catalog_and_exact_mesh():
    project = export(stack())
    assert project["generation"]["validation"]["ok"] is True
    assert len(project["pieces"]) == 2
    assert len(project["parts"]) == 1
    part = project["parts"][0]
    assert part["id"] == "ldraw-3001" and part["shape"] == "mesh"
    assert len(part["mesh"]["positions"]) > 100
    points = np.array(part["mesh"]["positions"]).reshape(-1, 3)
    np.testing.assert_allclose(points.max(axis=0) + points.min(axis=0), 0, atol=1e-12)
    np.testing.assert_allclose(np.ptp(points, axis=0), [part["w"] * .58, part["h"], part["d"] * .58])
    assert project["pieces"][0]["colorCode"] == 4
    assert project["pieces"][1]["colorCode"] == 1
    assert project["generation"]["previewGeometry"] == "inset_collision_mesh"
    project["pieces"][0]["x"] = 999
    assert project["originalPieces"][0]["x"] != 999


@pytest.mark.parametrize("angles", [(0.2, 0.5, -0.7), (0.3, math.pi / 2, .8)])
def test_nested_rotated_slope_keeps_every_world_vertex(angles):
    # A slope exercises the SDK's raw-part offset rotation and seating offset.
    sub = bl.LegoBuild()
    sub.add(bl.Element("3039", bl.Color.RED), (0, 0, 0))
    build = bl.LegoBuild()
    build.add_raw(sub, Pose(Mat3.from_array(xyz_matrix(*angles)), Position(3, 2, 2)))
    build = bl.translate_to_ground(build)
    project = export(build)
    piece, part = project["pieces"][0], project["parts"][0]
    points = np.array(part["mesh"]["positions"]).reshape(-1, 3)
    rotation = xyz_matrix(piece["rx"], piece["ry"], piece["rz"])
    actual = points @ rotation.T + [piece["x"], piece["y"], piece["z"]]
    body = build.flattened_placements[0].collision_body
    expected = (body.collider.mesh.vertices @ body.rotation.T + body.translation) @ np.diag([1, -1, -1]) * LDU_SCALE
    np.testing.assert_allclose(actual, expected, atol=1e-8)


@pytest.mark.parametrize("failure", ["empty", "collision", "floating", "disconnected", "limit"])
def test_hard_physical_acceptance_cannot_be_waived(failure):
    build = bl.LegoBuild()
    if failure != "empty":
        element = bl.Element("3001", bl.Color.RED)
        build.add(element)
        if failure == "collision":
            build.add(element)
        if failure == "floating":
            build.add(element, (0, 0, 3))
        if failure == "disconnected":
            build.add(element, (10, 0, 0))
    with pytest.raises(ExportRejected) as error:
        verify_build(build, 0 if failure == "limit" else 80)
    assert error.value.code == {"empty": "PART_LIMIT", "collision": "COLLISION", "floating": "FLOATING", "disconnected": "DISCONNECTED", "limit": "PART_LIMIT"}[failure]


def test_ldraw_round_trip_keeps_exact_poses():
    from bricklayer.io import from_ldraw, to_ldraw

    build = stack()
    original, reimported = export(build), export(from_ldraw(to_ldraw(build)))
    for left, right in zip(original["pieces"], reimported["pieces"]):
        assert left == right


@pytest.mark.parametrize("translation,code", [((0, 0, 100), "UNSUPPORTED_ORIGIN"), ((1000, 0, 0), "SCENE_TOO_LARGE")])
def test_reject_unsupported_scene_origins(translation, code):
    build = bl.LegoBuild()
    build.add(bl.Element("3001", bl.Color.RED), translation)
    with pytest.raises(ExportRejected) as error:
        export(build)
    assert error.value.code == code
