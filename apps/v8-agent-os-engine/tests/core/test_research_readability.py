from __future__ import annotations

import pytest

from core.tools import research_broker as research
from core.tools.research_readability import has_readable_table


@pytest.mark.parametrize("table", [
    "| 标准号：EXAMPLE 12345-2025 |\n| 中文名称：示例系统内容标识方法 |\n| 标准状态：现行 |\n| 记录类型：标准详情 |",
    "| Parameter | Value |\n| --- | --- |\n| Transport | HTTPS |\n| Retry | bounded |",
])
def test_read_table_is_not_discarded_for_pipe_count(table):
    body = "- 首页\n- 国家标准\n- 联系我们\n" + table + "\n此页面提供记录详情，原始字段应按表格读取。" * 12
    assert has_readable_table(body)
    # The active acquisition path uses this check before reader fallback.
    assert "navigation_or_footer_like_text" not in research._source_noise_reasons(body)


def test_pipe_separated_navigation_is_still_noise():
    body = "Home | About | Privacy | Contact | News | Jobs | Search | Login | Register"
    assert not has_readable_table(body)
    assert "navigation_or_footer_like_text" in research._source_noise_reasons(body)


def test_flattened_label_value_record_remains_readable():
    record = "| Record: EXAMPLE 12345-2025 | Name: Example content labeling | Status: active | Version: final | Scope: public |"
    assert has_readable_table(record)
    assert "navigation_or_footer_like_text" not in research._source_noise_reasons(record)
