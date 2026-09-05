import copy

import pytest

from core.tools.research_writer_integrity import unbound_section_urls
from core.tools.research_broker import _architect_section_issues


@pytest.mark.parametrize("text", [
    "查询入口为 https://registry.example.gov.cn（联系电话另见公告）。 [S1]",
    "查询入口为 [登记系统](https://registry.example.gov.cn)。 [S1]",
    "查询入口为 <https://registry.example.gov.cn>。 [S1]",
    "来源 [公告](https://source.example/notice) 记录了登记入口。 [S1]",
])
def test_bound_url_is_a_fact_and_does_not_trigger_deletion_or_rejection(text):
    task = {
        "targetMaxChars": 1000, "requiredCitationKeys": ["S1"],
        "assignedClaims": [{
            "claimId": "C1", "claim": "公告记录了登记入口。",
            "evidenceExcerpt": "查询入口为 https://registry.example.gov.cn（联系电话另见公告）。",
            "supportingSources": [{"citationKey": "S1", "url": "https://source.example/notice"}],
        }],
    }
    before = copy.deepcopy(task)
    assert unbound_section_urls(text, task) == []
    assert "section_url_not_in_evidence" not in _architect_section_issues(text, task, complete=True)
    assert task == before


def test_unknown_url_is_reported_without_rewriting_neighboring_facts():
    text = "入口为 https://invented.example。原始入口仍需核实。[S1]"
    task = {"targetMaxChars": 1000, "requiredCitationKeys": ["S1"]}
    assert unbound_section_urls(text, task) == ["https://invented.example"]
    assert "section_url_not_in_evidence" in _architect_section_issues(text, task, complete=True)
    assert text.endswith("原始入口仍需核实。[S1]")
