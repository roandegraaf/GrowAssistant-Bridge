"""Tests for the installer's pairing command (``python -m app.pair``)."""

from unittest.mock import AsyncMock, patch

import pytest

from app import pair as pair_cli
from app.auth import PairResult


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("K7PQ9", "K7PQ9"), (" k7p-q9 ", "K7PQ9"), ("k7p q9\n", "K7PQ9"), ("--", "")],
)
def test_normalize_code(raw, expected):
    assert pair_cli.normalize_code(raw) == expected


@pytest.mark.parametrize(
    ("result", "exit_code"),
    [
        (PairResult.PAIRED, 0),
        (PairResult.INVALID_CODE, 1),
        (PairResult.UNREACHABLE, 2),
        (PairResult.FAILED, 3),
    ],
)
def test_exit_code_follows_the_pairing_result(result, exit_code, capsys):
    with patch.object(pair_cli, "pair", AsyncMock(return_value=result)) as pair:
        assert pair_cli.main(["k7p-q9"]) == exit_code
    pair.assert_awaited_once_with("K7PQ9")
    assert capsys.readouterr().out.strip()


def test_rejects_missing_or_empty_code():
    with patch.object(pair_cli, "pair", AsyncMock()) as pair:
        assert pair_cli.main([]) == 3
        assert pair_cli.main(["--"]) == 1
    pair.assert_not_awaited()
