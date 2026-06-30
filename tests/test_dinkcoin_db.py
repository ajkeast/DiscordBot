"""Unit tests for DinkCoin database ledger helpers."""

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from utils.db import DataOperations, DatabaseError


@pytest.fixture
def data_ops():
    ops = DataOperations()
    ops.db = MagicMock()
    return ops


def test_get_dink_balance_empty(data_ops):
    data_ops.db.fetch_df.return_value = pd.DataFrame(columns=["balance"])
    assert data_ops.get_dink_balance(123) == 0.0


def test_get_dink_balance_with_value(data_ops):
    data_ops.db.fetch_df.return_value = pd.DataFrame({"balance": [4.25]})
    assert data_ops.get_dink_balance(123) == 4.25


def test_record_dink_mint(data_ops):
    data_ops.record_dink_mint(123, 1.0, "0xabc")
    data_ops.db.execute.assert_called()
    assert data_ops.db.execute.call_count == 2


def test_get_total_dink_circulation(data_ops):
    data_ops.db.fetch_df.return_value = pd.DataFrame({"total": [12.5]})
    assert data_ops.get_total_dink_circulation() == 12.5


def test_apply_dink_transfer_insufficient(data_ops):
    cursor = MagicMock()
    cursor.rowcount = 0
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=cursor)
    cm.__exit__ = MagicMock(return_value=False)
    data_ops.db.cursor.return_value = cm

    with pytest.raises(DatabaseError, match="Insufficient DINK balance"):
        data_ops.apply_dink_transfer(111, 222, 1.0)
