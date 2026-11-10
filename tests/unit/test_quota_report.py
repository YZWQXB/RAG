"""配额余量：不仅要能拦住超额，还要让用户在撞墙前看到余量。"""

from __future__ import annotations

import pytest

from app.modules.quota.service import usage_entry


def test_entry_reports_remaining_and_ratio():
    entry = usage_entry(used=30, limit=100, warn_ratio=0.8)
    assert entry == {
        "used": 30,
        "limit": 100,
        "remaining": 70,
        "ratio": 0.3,
        "state": "ok",
    }


def test_entry_warns_at_threshold():
    assert usage_entry(used=80, limit=100, warn_ratio=0.8)["state"] == "warning"
    assert usage_entry(used=79, limit=100, warn_ratio=0.8)["state"] == "ok"


def test_entry_exceeded_clamps_remaining():
    entry = usage_entry(used=120, limit=100, warn_ratio=0.8)
    assert entry["state"] == "exceeded"
    assert entry["remaining"] == 0
    assert entry["ratio"] == 1.2


def test_entry_unlimited_metric():
    """limit=0 表示不限量（例如 free 套餐不含该指标时）。"""
    entry = usage_entry(used=500, limit=0, warn_ratio=0.8)
    assert entry["state"] == "ok"
    assert entry["ratio"] == 0.0


@pytest.mark.parametrize("used,limit", [(0, 0), (0, 10), (10, 10), (5, 3)])
def test_entry_never_negative(used: int, limit: int):
    entry = usage_entry(used=used, limit=limit, warn_ratio=0.8)
    assert entry["remaining"] >= 0
    assert entry["ratio"] >= 0
