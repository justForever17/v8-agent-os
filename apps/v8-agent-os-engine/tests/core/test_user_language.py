from core.user_language import infer_preferred_language, normalize_preferred_language


def test_user_language_inference_uses_the_first_non_empty_original_source() -> None:
    assert infer_preferred_language("", "请修复这个问题") == "zh-CN"
    assert infer_preferred_language("最新の結果を確認してください") == "ja"
    assert infer_preferred_language("결과를 확인해 주세요") == "ko"
    assert infer_preferred_language("Проверьте результат") == "ru"


def test_user_language_inference_keeps_english_as_the_stable_default() -> None:
    assert infer_preferred_language("Verify the result") == "en"
    assert infer_preferred_language(None, default="zh-CN") == "zh-CN"


def test_user_language_normalization_accepts_supported_chinese_aliases() -> None:
    assert normalize_preferred_language("zh-cn") == "zh-CN"
    assert normalize_preferred_language("Chinese") == "zh-CN"
    assert normalize_preferred_language("unsupported") == ""
