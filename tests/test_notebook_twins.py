"""The two gold notebooks are one notebook stored twice; CI keeps them that way.

`03b_gold_safe.ipynb` and `03b_gold_safe_analytics.ipynb` exist because the accelerator
supports two deployment shapes: everything in one workspace, or Gold split into an Analytics
workspace so analysts can be granted the star without ever being granted PHI. The *behaviour*
is identical in both.

They were forked once, and drifted. The PHI-Raw copy kept re-reading ``active_profile`` after
the Analytics copy had been changed to inherit the profile from the run context, and it
carried a ``year_of()`` bug — testing a profile *name* rather than the rule — that had
already been fixed in its twin. Both notebooks ran. Neither raised. They just produced
different gold, and which one you got depended on which file you happened to open.

That is the whole argument against forking a pipeline to serve a variant: the fork is free
on day one and is paid for silently, forever. So the difference is confined to the markdown
cell that explains *which* copy you are looking at, and this test holds the line.
"""

from __future__ import annotations

import io
import json
import tokenize
from pathlib import Path

import pytest

NOTEBOOKS = Path(__file__).resolve().parents[1] / "notebooks"
TWINS = ("03b_gold_safe.ipynb", "03b_gold_safe_analytics.ipynb")


def _code_cells(name: str) -> list[str]:
    nb = json.loads((NOTEBOOKS / name).read_text(encoding="utf-8"))
    return ["".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code"]


def _executable_source(name: str) -> str:
    """The notebook with comments and string literals removed.

    Both notebooks *document* the anti-patterns below at length — that is the point of the
    comments. A naive substring search over the raw source therefore fails on the very
    explanation of why the code is correct. Strip the prose and search what actually runs.
    """
    out: list[str] = []
    for body in _code_cells(name):
        readline = io.StringIO(body).readline
        out.extend(
            tok.string
            for tok in tokenize.generate_tokens(readline)
            if tok.type not in (tokenize.COMMENT, tokenize.STRING)
        )
    return "\n".join(out)


def test_the_gold_twins_share_every_line_of_code() -> None:
    """Identical code, character for character. Configuration is what may differ, not logic.

    If this fails, the fix is almost never "update the assertion". It is to move whatever
    genuinely differs into the PARAMETERS cell — which is a code cell too, and is therefore
    also covered here, so the parameters must differ only in their *values* being editable,
    not in their code.
    """
    one, two = (_code_cells(n) for n in TWINS)
    assert len(one) == len(two), (
        f"{TWINS[0]} has {len(one)} code cells, {TWINS[1]} has {len(two)} — "
        "a cell was added to one twin and not the other"
    )
    for i, (a, b) in enumerate(zip(one, two, strict=True)):
        assert a == b, f"code cell {i} has diverged between {TWINS[0]} and {TWINS[1]}"


@pytest.mark.parametrize("name", TWINS)
def test_neither_twin_re_reads_the_active_profile(name: str) -> None:
    """The de-identification method is inherited from the 02b run, never re-chosen here.

    Re-reading ``active_profile`` in gold means the YAML can be edited between the de-id run
    and the build, so gold gets assembled under one method's assumptions from data produced
    under another's — with no error, because both steps succeed. This is the exact defect the
    fork was hiding.
    """
    assert "active_profile" not in _executable_source(name), (
        f"{name} reads active_profile; it must take the profile from silver_deid_run_context"
    )
    assert "silver_deid_run_context" in "\n".join(_code_cells(name))


@pytest.mark.parametrize("name", TWINS)
def test_neither_twin_decides_a_date_format_from_the_profile_name(name: str) -> None:
    """Ask the rulebook what a column emits; a profile name is a label, not a fact.

    ``PROFILE == "safe_harbor"`` looked correct and was not: ``safe_harbor_strict`` also
    generalizes dates, so it fell down the date-shift branch, where ``F.year()`` over an int
    year returns nulls. One of our profile names is famously misnamed, which is precisely why
    behaviour must never be keyed to one.
    """
    code = _executable_source(name)
    assert "safe_harbor" not in code, (
        f"{name} branches on a literal profile name; ask the rulebook for the strategy instead"
    )
    assert "_emits_year_int" in code
