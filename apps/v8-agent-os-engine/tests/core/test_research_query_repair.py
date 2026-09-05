import pytest

from core.tools.research_query_repair import focused_discovery_query


@pytest.mark.parametrize(("query", "expected"), [
    ("人工智能生成合成内容标识办法 全文 网信办 施行日期", '"人工智能生成合成内容标识办法"'),
    ("生成式人工智能服务管理暂行办法 全文 2023", '"生成式人工智能服务管理暂行办法"'),
    ("site:example.gov.cn 《演示管理办法》 原文", '"演示管理办法" site:example.gov.cn'),
    ("某种开发框架更新说明 v3.2 API 用法", '"某种开发框架更新说明" v3.2'),
    ("《第一份管理规定》《第二份管理规定》 适用关系", ""),
    ("人工智能生成合成内容标识办法", ""),
    ('"人工智能生成合成内容标识办法"', ""),
    ("LangGraph interrupt resume documentation", ""),
    ("请检查问题，然后输出结论。", ""),
])
def test_query_repair_uses_only_existing_names_and_preserves_explicit_constraints(query, expected):
    assert focused_discovery_query(query) == expected
