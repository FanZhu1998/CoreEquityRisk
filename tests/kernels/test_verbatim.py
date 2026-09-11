"""The reference kernels and their tests must stay identical to blueprint Appendix A (D-002)."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _appendix_block(title: str) -> str:
    bp = (ROOT / "docs" / "BLUEPRINT.md").read_text(encoding="utf-8")
    start = bp.index("```python\n", bp.index(title)) + len("```python\n")
    return bp[start:bp.index("\n```", start)] + "\n"


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8").replace("\r\n", "\n")


def test_reference_kernels_are_appendix_a1_verbatim():
    assert _read("eqrisk/kernels/reference.py") == _appendix_block("### A.1")


def test_kernel_tests_are_appendix_a2_with_package_import():
    expected = _appendix_block("### A.2").replace(
        "from reference_kernels import (", "from eqrisk.kernels import (")
    assert _read("tests/kernels/test_reference_kernels.py") == expected
