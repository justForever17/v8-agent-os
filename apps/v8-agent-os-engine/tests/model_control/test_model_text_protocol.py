import pytest

from core.model_text_protocol import NativeToolTextGuard, has_native_tool_text


@pytest.mark.parametrize("marker", ['<tool_call>', ']<]minimax[>[', ']<]provider_name[>['])
def test_every_split_boundary_keeps_text_but_quarantines_protocol(marker):
    raw = '正文已验证。' + marker + '<invoke name="update_todo">private arguments'
    for split in range(len(raw) + 1):
        guard = NativeToolTextGuard()
        text = guard.feed(raw[:split]) + guard.feed(raw[split:]) + guard.feed('', final=True)
        assert text == '正文已验证。' and guard.leaked


@pytest.mark.parametrize("raw", ['[来源](https://example.test)', 'a < b; x]',
    '示例：`<tool_call>` 不能执行。', '```xml\n<tool_call><invoke name="test"/></tool_call>\n```\n正常解释。',
    '``literal ` and ]<]minimax[>[``', '正常 Markdown 末尾 `'])
def test_code_examples_and_partial_punctuation_are_preserved(raw):
    for split in range(len(raw) + 1):
        guard = NativeToolTextGuard()
        assert guard.feed(raw[:split]) + guard.feed(raw[split:]) + guard.feed('', final=True) == raw
        assert not guard.leaked
    assert not has_native_tool_text(raw)


@pytest.mark.parametrize('raw', ['~~~xml\n<tool_call><invoke name="test"/></tool_call>\n~~~\n说明',
    '示例：\n\n    <tool_call>literal code\n\n正文', '> ```xml\n> <tool_call>literal\n> ```\n',
    '```xml\n<tool_call>literal\n````\n正常正文'])
def test_markdown_block_code_forms_are_not_tool_failures(raw):
    for split in range(len(raw) + 1):
        guard = NativeToolTextGuard()
        assert guard.feed(raw[:split]) + guard.feed(raw[split:]) + guard.feed('', final=True) == raw
        assert not guard.leaked


@pytest.mark.parametrize('prefix', ['普通转义反引号 \\` 不是代码。', '```xml\ncode\n````\n', '~~~\ncode\n~~~~\n'])
def test_escaping_or_longer_fence_close_cannot_hide_a_later_leak(prefix):
    raw = prefix + '<tool_call><invoke name="lookup">args'
    for split in range(len(raw) + 1):
        guard = NativeToolTextGuard()
        assert guard.feed(raw[:split]) + guard.feed(raw[split:]) + guard.feed('', final=True) == prefix
        assert guard.leaked
