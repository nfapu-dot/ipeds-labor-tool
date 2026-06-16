"""
tests/test_v1_regression.py — locks v1 CLI output against the Phase 0 baseline.

Runs `python src/main.py --state CA` to a temp directory and compares every
sheet in the resulting workbook against tests/fixtures/v1_baseline.xlsx.

Run standalone:
    python3 tests/test_v1_regression.py

Or under pytest (if installed):
    python3 -m pytest tests/test_v1_regression.py -v

Exit code 0 = pass; non-zero = at least one drift detected.

This test is the safety net for any change touching v1's data path (loader,
joiner, aggregator, reporter, or anything shared via core/). If it fails after
a refactor, revert and investigate — never update the baseline to match new
output without explicit user approval.
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BASELINE = PROJECT_ROOT / 'tests' / 'fixtures' / 'v1_baseline.xlsx'
SRC = PROJECT_ROOT / 'src'

# The exact command that produced the baseline. Changing this invalidates the
# baseline — regenerate before changing.
BASELINE_CMD_ARGS = ['--state', 'CA']


def _run_v1(output_dir: Path) -> Path:
    """Invoke the v1 CLI to output_dir; return the path of the produced .xlsx."""
    cmd = [
        sys.executable, str(SRC / 'main.py'),
        *BASELINE_CMD_ARGS,
        '--output', str(output_dir),
    ]
    env_pythonpath = str(SRC)
    result = subprocess.run(
        cmd,
        input='y\n',
        capture_output=True,
        text=True,
        env={'PYTHONPATH': env_pythonpath, 'PATH': _shell_path()},
        timeout=600,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f'v1 CLI exited with {result.returncode}\n'
            f'stdout:\n{result.stdout}\n'
            f'stderr:\n{result.stderr}'
        )
    produced = sorted(output_dir.glob('*.xlsx'))
    if len(produced) != 1:
        raise RuntimeError(
            f'expected 1 xlsx in {output_dir}; got {len(produced)}: {produced}'
        )
    return produced[0]


def _shell_path() -> str:
    import os
    return os.environ.get('PATH', '/usr/local/bin:/usr/bin:/bin')


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _compare_workbooks(new: Path, baseline: Path) -> list[str]:
    """Return a list of human-readable drift messages. Empty list = identical."""
    drifts: list[str] = []

    new_xl = pd.ExcelFile(new)
    base_xl = pd.ExcelFile(baseline)

    if new_xl.sheet_names != base_xl.sheet_names:
        drifts.append(
            f'sheet list differs:\n'
            f'  new:      {new_xl.sheet_names}\n'
            f'  baseline: {base_xl.sheet_names}'
        )
        return drifts

    for sheet in base_xl.sheet_names:
        n = pd.read_excel(new, sheet_name=sheet)
        b = pd.read_excel(baseline, sheet_name=sheet)
        if n.shape != b.shape:
            drifts.append(
                f'sheet {sheet!r}: shape differs (new {n.shape} vs baseline {b.shape})'
            )
            continue
        if list(n.columns) != list(b.columns):
            drifts.append(
                f'sheet {sheet!r}: column order/names differ\n'
                f'  new:      {list(n.columns)}\n'
                f'  baseline: {list(b.columns)}'
            )
            continue
        if not n.equals(b):
            # Find first row+col with a real difference for the report.
            mismatched_cols = [
                c for c in n.columns
                if not n[c].equals(b[c])
            ]
            drifts.append(
                f'sheet {sheet!r}: content differs in columns {mismatched_cols}'
            )

    return drifts


def test_v1_regression() -> None:
    """
    Re-run the v1 CLI and assert the resulting workbook is identical to the
    frozen Phase 0 baseline. pytest-compatible signature (no args, asserts).
    """
    assert BASELINE.exists(), f'baseline missing: {BASELINE}'

    with tempfile.TemporaryDirectory(prefix='ipeds_v1_regression_') as tmp:
        out_dir = Path(tmp)
        produced = _run_v1(out_dir)

        # Fast-path: byte-identical xlsx → guaranteed pass.
        if _sha256(produced) == _sha256(BASELINE):
            return

        # Otherwise compare cell content sheet-by-sheet. xlsx can drift on
        # internal metadata (timestamps, theme refs) without any data change,
        # so byte-identical is a fast-path, not a requirement.
        drifts = _compare_workbooks(produced, BASELINE)
        assert not drifts, (
            'v1 output drifted from baseline:\n  ' + '\n  '.join(drifts)
        )


if __name__ == '__main__':
    try:
        test_v1_regression()
    except AssertionError as e:
        print(f'FAIL: {e}', file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f'ERROR: {type(e).__name__}: {e}', file=sys.stderr)
        sys.exit(2)
    print('PASS: v1 output matches tests/fixtures/v1_baseline.xlsx')
