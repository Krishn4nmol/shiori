import pytest

from app.evaluation.metrics import (
    ResultView,
    answer_in_language,
    answer_metrics,
    cutoff_sweep,
    is_relevant,
    retrieval_metrics,
)


def view(answerable=True, rank=None, kept=False, answered=False, scores=(), judge=None, ms=100.0):
    return ResultView(
        answerable=answerable,
        relevant_rank=rank,
        kept_relevant=kept,
        answered=answered,
        scores=list(scores),
        latency_ms=ms,
        judge=judge,
    )


def test_relevance_matches_evidence_in_the_right_paper() -> None:
    text = "しょくせい植生被覆率を 四十地点で 測定した。"
    assert is_relevant(text, "doc1", "doc1", "植生被覆率を四十地点で測定した")
    assert not is_relevant(text, "doc2", "doc1", "植生被覆率を四十地点で測定した")
    assert is_relevant("We measured FORTY sites.", "d", None, "forty  sites")
    assert not is_relevant("anything", "d", "d", None)


def test_hit_at_k_and_mrr() -> None:
    results = [view(rank=1), view(rank=2), view(rank=6), view(rank=None)]
    m = retrieval_metrics(results)
    assert m["hit@1"] == 0.25
    assert m["hit@3"] == 0.5
    assert m["hit@5"] == 0.5
    assert m["mrr"] == pytest.approx((1 + 0.5 + 1 / 6 + 0) / 4, abs=1e-4)


def test_coverage_and_rejection() -> None:
    results = [
        view(rank=1, kept=True, answered=True),
        view(rank=1, kept=False, answered=False),  # found, but the cutoff dropped it
        view(answerable=False, answered=False),  # correctly says not found
        view(answerable=False, answered=True),  # would make something up
    ]
    m = retrieval_metrics(results)
    assert (m["answerable"], m["unanswerable"]) == (2, 2)
    assert m["coverage"] == 0.5
    assert m["rejection"] == 0.5


def test_cutoff_sweep_finds_the_trade_off() -> None:
    results = [
        # answerable: relevant passage scored 0.0005 (a fact buried in a long passage)
        view(scores=[(0.0005, True), (0.0001, False)]),
        # answerable: relevant passage scored high
        view(scores=[(0.6, True)]),
        # unanswerable: junk scores
        view(answerable=False, scores=[(0.0002, False), (0.00005, False)]),
    ]
    sweep = cutoff_sweep(results)
    by_cutoff = {p["cutoff"]: p for p in sweep["points"]}
    assert by_cutoff[0.01]["coverage"] == 0.5  # the old cutoff misses the buried fact
    assert by_cutoff[0.0003]["coverage"] == 1.0
    assert by_cutoff[0.0003]["rejection"] == 1.0
    assert by_cutoff[0.0]["rejection"] == 0.0  # no cutoff: always answers something
    assert sweep["best"]["cutoff"] == 0.0003  # the only cutoff that gets both right


def test_sweep_ties_prefer_the_strictest_cutoff_that_loses_nothing() -> None:
    # Near-miss unanswerable question: its passages are on-topic, so no cutoff rejects it.
    results = [
        view(scores=[(0.0005, True)]),
        view(answerable=False, scores=[(0.4, False)]),
    ]
    best = cutoff_sweep(results)["best"]
    assert best["coverage"] == 1.0
    assert best["cutoff"] == 0.0003  # not 0.0: the highest cutoff with the same score


def test_sweep_needs_reranker_scores() -> None:
    assert cutoff_sweep([view(scores=[(None, True)])]) == {"points": [], "best": None}


def test_answer_metrics_from_judge() -> None:
    results = [
        view(judge={"correctness": "correct", "faithful": True, "cited_relevant": True}),
        view(judge={"correctness": "partial", "faithful": False, "cited_relevant": True}),
        view(judge={"correctness": "wrong", "faithful": True, "cited_relevant": False}),
        view(answerable=False, judge={"said_not_found": True}),
        view(),  # not judged (e.g. error)
    ]
    m = answer_metrics(results)
    assert m["judged"] == 4
    assert m["correctness"] == 0.5
    assert m["faithfulness"] == pytest.approx(2 / 3, abs=1e-4)
    assert m["cited_relevant"] == pytest.approx(2 / 3, abs=1e-4)
    assert m["said_not_found"] == 1.0


def test_answer_language_check() -> None:
    assert answer_in_language("en", "Vegetation cover (植生被覆率) was measured at 40 sites [1].")
    assert not answer_in_language("en", "鳥取の砂丘サイトは毎月訪問されていました [1]。")
    assert answer_in_language("ja", "調査は三年間行われました[1]。")
    assert not answer_in_language("ja", "The survey lasted three years [1].")


def test_language_match_metric() -> None:
    results = [
        view(judge={"correctness": "correct", "language_match": True}),
        view(judge={"correctness": "correct", "language_match": False}),
        view(judge={"correctness": "wrong"}),  # fixed "not found" reply: not checked
    ]
    assert answer_metrics(results)["language_match"] == 0.5


def test_english_list_of_japanese_quotes_counts_as_english() -> None:
    answer = "\n".join(
        [
            "The report uses these Japanese terms:",
            "- 植生被覆率：地表を覆う植物の割合 [1]",
            "- 飛砂害：風で運ばれる砂による被害 [1]",
            "- 草原化：砂丘が草で覆われること [2]",
        ]
    )
    assert answer_in_language("en", answer)
