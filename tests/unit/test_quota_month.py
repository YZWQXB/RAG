"""配额月切分必须按业务时区，而不是 UTC。

UTC 月初对应北京时间每月 1 日 08:00，用户会在"月初还能超额用一会儿"
和"月底提前 8 小时被卡"之间反复踩坑。
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.modules.quota.service import _month_key


def test_month_key_uses_shanghai_boundary():
    # UTC 2026-10-31 17:30 == 北京时间 2026-11-01 01:30
    moment = datetime(2026, 10, 31, 17, 30, tzinfo=UTC)
    assert _month_key(moment) == "202611"


def test_month_key_before_boundary_stays_previous_month():
    # UTC 2026-10-31 15:30 == 北京时间 2026-10-31 23:30
    moment = datetime(2026, 10, 31, 15, 30, tzinfo=UTC)
    assert _month_key(moment) == "202610"
