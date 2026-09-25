"""Query normalisation and candidate fusion: pure, so tested exhaustively."""

from __future__ import annotations

import uuid

import pytest

from orbit.domain.errors import ValidationError
from orbit.domain.retrieval import (
    MAX_DOCUMENT_FILTER,
    MAX_QUERY_CHARACTERS,
    Candidate,
    MatchedBy,
    RankedList,
    RetrievalMethod,
    SearchFilters,
    SearchQuery,
    min_max_fusion,
    normalize_query,
    reciprocal_rank_fusion,
)

L, S = RetrievalMethod.LEXICAL, RetrievalMethod.SEMANTIC


def _ids(n: int) -> list[uuid.UUID]:
    return [uuid.UUID(int=i + 1) for i in range(n)]


def _ranked(
    method: RetrievalMethod, ids: list[uuid.UUID], scores: list[float] | None = None
) -> RankedList:
    scores = scores or [1.0 / (i + 1) for i in range(len(ids))]
    return RankedList(method, tuple(Candidate(i, s) for i, s in zip(ids, scores, strict=True)))


class TestNormalizeQuery:
    def test_whitespace_is_collapsed_and_trimmed(self) -> None:
        assert normalize_query("  parental \t leave\n\nweeks  ") == "parental leave weeks"

    def test_compatibility_forms_are_folded(self) -> None:
        # Full-width letters and the "ﬁ" ligature, as a PDF often produces them.
        assert normalize_query("\uff25\uff32\uff32-4012 \ufb01le") == "ERR-4012 file"

    def test_invisible_characters_cannot_split_a_keyword(self) -> None:
        assert normalize_query("pass\u200bword ro\x00tation") == "password rotation"

    def test_case_and_punctuation_are_left_to_the_retrievers(self) -> None:
        assert normalize_query("What is ERR-4012?") == "What is ERR-4012?"

    @pytest.mark.parametrize("raw", ["", "   ", "\u200b\u200b", "\x00\n"])
    def test_an_empty_query_is_refused(self, raw: str) -> None:
        with pytest.raises(ValidationError):
            normalize_query(raw)

    def test_an_oversized_query_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            normalize_query("x" * (MAX_QUERY_CHARACTERS + 1))


class TestQueryValidation:
    @pytest.mark.parametrize("limit", [0, 51, -1])
    def test_limit_is_bounded(self, limit: int) -> None:
        with pytest.raises(ValidationError):
            SearchQuery(text="q", limit=limit)

    def test_an_empty_document_filter_is_refused_rather_than_meaning_everything(self) -> None:
        with pytest.raises(ValidationError):
            SearchFilters(document_ids=frozenset())

    def test_the_document_filter_is_bounded(self) -> None:
        with pytest.raises(ValidationError):
            SearchFilters(document_ids=frozenset(_ids(MAX_DOCUMENT_FILTER + 1)))


class TestReciprocalRankFusion:
    def test_scores_follow_the_formula(self) -> None:
        a, b, c = _ids(3)
        fused = reciprocal_rank_fusion([_ranked(L, [a, b]), _ranked(S, [b, c])], k=60)
        scores = {f.chunk_id: f.fused_score for f in fused}
        assert scores[a] == pytest.approx(1 / 61)
        assert scores[b] == pytest.approx(1 / 62 + 1 / 61)
        assert scores[c] == pytest.approx(1 / 62)
        assert [f.chunk_id for f in fused] == [b, a, c]

    def test_agreement_between_retrievers_beats_a_single_first_place(self) -> None:
        winner, lexical_only, semantic_only = _ids(3)
        fused = reciprocal_rank_fusion(
            [
                _ranked(L, [lexical_only, winner]),
                _ranked(S, [semantic_only, winner]),
            ]
        )
        assert fused[0].chunk_id == winner
        assert fused[0].matched_by is MatchedBy.BOTH

    def test_raw_scores_are_ignored_only_positions_count(self) -> None:
        a, b = _ids(2)
        tiny = reciprocal_rank_fusion([_ranked(L, [a, b], [0.0001, 0.00005])])
        huge = reciprocal_rank_fusion([_ranked(L, [a, b], [900.0, 1.0])])
        assert [f.fused_score for f in tiny] == [f.fused_score for f in huge]

    def test_each_result_keeps_every_retrievers_rank_and_native_score(self) -> None:
        a, b = _ids(2)
        fused = reciprocal_rank_fusion([_ranked(L, [a, b], [0.4, 0.2]), _ranked(S, [b], [0.83])])
        (b_fused,) = [f for f in fused if f.chunk_id == b]
        lexical, semantic = b_fused.from_method(L), b_fused.from_method(S)
        assert lexical is not None and (lexical.rank, lexical.score) == (2, 0.2)
        assert semantic is not None and (semantic.rank, semantic.score) == (1, 0.83)
        (a_fused,) = [f for f in fused if f.chunk_id == a]
        assert a_fused.from_method(S) is None
        assert a_fused.matched_by is MatchedBy.LEXICAL

    def test_a_single_list_keeps_its_order(self) -> None:
        ids = _ids(10)
        assert [f.chunk_id for f in reciprocal_rank_fusion([_ranked(S, ids)])] == ids

    def test_ties_break_deterministically(self) -> None:
        a, b = _ids(2)
        # a is first lexically, b first semantically: identical RRF scores.
        first = reciprocal_rank_fusion([_ranked(L, [a, b]), _ranked(S, [b, a])])
        second = reciprocal_rank_fusion([_ranked(S, [b, a]), _ranked(L, [a, b])])
        assert first[0].fused_score == first[1].fused_score
        assert [f.chunk_id for f in first] == [f.chunk_id for f in second] == [a, b]

    def test_a_duplicate_within_one_list_counts_once(self) -> None:
        a, b = _ids(2)
        fused = reciprocal_rank_fusion([_ranked(L, [a, a, b], [0.9, 0.9, 0.5])])
        assert [(f.chunk_id, f.from_method(L).rank) for f in fused] == [(a, 1), (b, 2)]  # type: ignore[union-attr]

    def test_empty_lists_fuse_to_nothing(self) -> None:
        assert reciprocal_rank_fusion([_ranked(L, []), _ranked(S, [])]) == []

    def test_k_must_be_positive(self) -> None:
        with pytest.raises(ValueError):
            reciprocal_rank_fusion([], k=0)


class TestMinMaxFusion:
    def test_scores_are_rescaled_per_list_then_weighted(self) -> None:
        a, b, c = _ids(3)
        fused = min_max_fusion(
            [_ranked(L, [a, b], [0.8, 0.2]), _ranked(S, [c, a], [0.9, 0.5])],
            weights={L: 0.5, S: 0.5},
        )
        scores = {f.chunk_id: f.fused_score for f in fused}
        assert scores[a] == pytest.approx(0.5 * 1.0 + 0.5 * 0.0)
        assert scores[b] == pytest.approx(0.0)
        assert scores[c] == pytest.approx(0.5)

    def test_a_list_of_equal_scores_maps_to_one(self) -> None:
        (a,) = _ids(1)
        (fused,) = min_max_fusion([_ranked(L, [a], [0.3])], weights={L: 1.0})
        assert fused.fused_score == 1.0
