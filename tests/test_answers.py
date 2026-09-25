"""The committed answer files satisfy the README contract and the policy invariants."""
import json
from pathlib import Path

import pytest

from src.engine import config
from src.engine.validate import validate

CASES = sorted(Path(config.CASES_DIR).glob("HHG-*.json"))


def test_twenty_answer_files():
    assert len(CASES) == 20


@pytest.mark.parametrize("path", CASES, ids=lambda p: p.stem)
def test_contract_without_store(path):
    assert validate(json.loads(path.read_text())) == []


@pytest.mark.skipif(not (config.STORE_DIR / "scored.parquet").exists(), reason="store not built")
def test_contract_with_store_ids_and_amounts():
    from src.engine.runner import load_store
    from src.engine.store import load_case_pack
    from src.engine.validate import validate_dir
    res = validate_dir(config.CASES_DIR, load_store(), load_case_pack())
    assert all(not v for v in res.values()), res


def test_half_legitimate_is_respected_roughly():
    verdicts = [json.loads(p.read_text())["case"]["verdict"] for p in CASES]
    assert 6 <= verdicts.count("legitimate") <= 14
