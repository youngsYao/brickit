"""Keep four-view builder context while giving the single-view critic one crop.

The upstream viewpoint matcher compares one camera render with one reference.
The adapter is confined to the dedicated worker process and restores the SDK
entry point even on failure; original record metadata and images are unchanged.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .reference_images import ReferenceImageError


class _CritiqueRecord:
    """Pickle-safe Record view; delegated writes remain bound to the real Record."""

    def __init__(self, record, reference: Path):
        self._record = record
        self._reference = reference

    def __getattr__(self, name):
        # object.__getattribute__ avoids recursion while multiprocessing unpickles
        # the proxy before its instance dictionary has been restored.
        return getattr(object.__getattribute__(self, "_record"), name)

    @property
    def prompt_image_path(self) -> Path:
        return self._reference

    def read_meta(self):
        meta = self._record.read_meta()
        return meta.model_copy(update={"prompt": meta.prompt + (
            "\n\nVisual review context: the builder was given a four-view reference sheet. "
            "For this camera-matching and visual-review step, the target image is ONLY "
            "the bottom-right front-right elevated perspective crop of that same subject. "
            "Match and compare this single view, not a grid or four duplicated objects. "
            "The builder still retains the other three reference views."
        )})


@contextmanager
def reference_critique(job_dir: Path) -> Iterator[None]:
    """Use the perspective crop for viewpoint matching/critique, retaining billing."""
    if not (job_dir / "reference.json").is_file():
        yield
        return
    reference = job_dir / "reference-perspective.png"
    if not reference.is_file():
        raise ReferenceImageError("REFERENCE_MISSING", "多视角参考图缺少透视图，请重新生成。")
    import brickwright.driver.loop as loop

    original = loop.critique

    def single_view_critique(record, config, **kwargs):
        return original(_CritiqueRecord(record, reference), config, **kwargs)

    loop.critique = single_view_critique
    try:
        yield
    finally:
        loop.critique = original
