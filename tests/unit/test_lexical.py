"""离线词法打分：不依赖向量空间，只比文本。

背景：库里分块可能是任何模型算的向量，离线查询用字符袋向量去比余弦恒为 0.05，
无论阈值多低都拒答。词法打分直接比文本，与索引里的向量无关。
"""

from __future__ import annotations

from app.modules.retrieval.lexical import lexical_score


def test_related_question_scores_above_offline_threshold():
    score = lexical_score("什么是递归", "递归是指函数直接或间接调用自身，递归必须有终止条件。")
    assert score >= 0.10, f"相关问题的分数应过线，实际 {score}"


def test_unrelated_question_scores_near_zero():
    score = lexical_score("红烧肉怎么做", "红黑树的插入需要旋转与变色，保持近似平衡。")
    assert score < 0.05, f"无关问题不应过线，实际 {score}"


def test_stopwords_and_punctuation_do_not_inflate():
    """虚词与标点几乎出现在每个块里，不能贡献相似度。"""
    plain = lexical_score("递归", "递归调用自身")
    noisy = lexical_score("什么是递归？", "的 了 是 吗，。！？ 递归调用自身")
    assert noisy <= plain * 1.35


def test_digits_are_ignored():
    """分块里满是页码与编号，数字不应成为命中理由。"""
    assert lexical_score("2024", "第 2024 页 目录 编号") < 0.05


def test_empty_inputs_are_safe():
    assert lexical_score("", "任意文本") == 0.0
    assert lexical_score("问题", "") == 0.0
