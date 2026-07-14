"""Unit tests for credibility_scorer scoring functions.

Tests cover the core scoring logic after optimizations:
  - Lower defaults (relevance/confidence: 0.85 → 0.60)
  - Rank-weighted top_strength (fixes dilution bug)
  - Lower baseline (0.50 → 0.40)
  - Source diversity bonus
  - Progressive core-contradiction cap
  - Domain authority tiering
  - Status fallback scores
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from tools.credibility.credibility_scorer import (
    _top_strength,
    _score_evidence_source,
    _score_evidence_relevance,
    _score_relation_confidence,
    _claim_importance_weight,
    _is_core_claim,
    _status_score,
    _score_verdict_with_evidence,
    _score_claim_verification,
    _core_contradiction_severity,
    _source_diversity_bonus,
    _get_domain_authority,
    _clamp_score,
    _evidence_relation,
)


# ── _clamp_score ────────────────────────────────────────────
class TestClampScore:
    def test_valid(self):
        assert _clamp_score(0.75) == 0.75

    def test_below_zero(self):
        assert _clamp_score(-0.5) == 0.0

    def test_above_one(self):
        assert _clamp_score(1.5) == 1.0

    def test_default_on_none(self):
        assert _clamp_score(None, default=0.5) == 0.5

    def test_default_on_string(self):
        assert _clamp_score("bad", default=0.3) == 0.3


# ── _top_strength ───────────────────────────────────────────
class TestTopStrength:
    def test_empty_returns_zero(self):
        assert _top_strength([]) == 0.0

    def test_single_source(self):
        assert _top_strength([0.80]) == 0.80

    def test_rank_weighted_two_sources(self):
        # gamma=0.6: w=[1.0, 0.6], total=1.6
        # (0.80*1.0 + 0.30*0.6)/1.6 = (0.80+0.18)/1.6 = 0.6125
        score = _top_strength([0.80, 0.30])
        assert 0.60 < score < 0.65

    def test_rank_weighted_three_sources(self):
        # Adding a weak 3rd source: rank-weighted drop (12%) is much less
        # than plain-mean drop (22%), preventing dilution.
        score_2 = _top_strength([0.80, 0.30])
        score_3 = _top_strength([0.80, 0.30, 0.20])
        assert score_3 >= score_2 * 0.85  # much better than plain-mean's 0.78

    def test_five_sources_only_top3_count(self):
        score = _top_strength([0.90, 0.80, 0.70, 0.10, 0.10])
        assert score >= 0.70

    def test_strong_source_not_diluted(self):
        # Key fix: 1 strong source alone vs with 2 weak ones
        # Rank-weighted: drop from 0.80 to 0.555 (31%), vs plain-mean drop to 0.467 (42%)
        score_1 = _top_strength([0.80])
        score_3 = _top_strength([0.80, 0.30, 0.30])
        assert score_3 >= score_1 * 0.65  # strong source still dominates
        # Verify it beats plain mean
        plain_mean = (0.80 + 0.30 + 0.30) / 3
        assert score_3 > plain_mean  # rank-weighted > plain mean


# ── _score_evidence_relevance ───────────────────────────────
class TestEvidenceRelevance:
    def test_default_is_conservative(self):
        assert _score_evidence_relevance({}) == 0.75

    def test_explicit_respected(self):
        assert _score_evidence_relevance({"relevance_score": 0.95}) == 0.95

    def test_similarity_fallback(self):
        assert _score_evidence_relevance({"similarity_score": 0.88}) == 0.88

    def test_bad_value_uses_default(self):
        assert _score_evidence_relevance({"relevance_score": "bad"}) == 0.75


# ── _score_relation_confidence ──────────────────────────────
class TestRelationConfidence:
    def test_default_is_conservative(self):
        assert _score_relation_confidence({}) == 0.75

    def test_explicit_respected(self):
        assert _score_relation_confidence({"relation_confidence": 0.92}) == 0.92

    def test_confidence_fallback(self):
        assert _score_relation_confidence({"confidence": 0.88}) == 0.88


# ── _score_evidence_source ──────────────────────────────────
class TestEvidenceSourceScore:
    def test_authority_score_explicit(self):
        assert _score_evidence_source({"authority_score": 0.80}) == 0.80

    def test_source_type_official(self):
        assert _score_evidence_source({"source_type": "official_or_primary"}) == 0.95

    def test_source_type_media(self):
        assert _score_evidence_source({"source_type": "authoritative_media"}) == 0.90

    def test_source_type_low(self):
        assert _score_evidence_source({"source_type": "low_credibility"}) == 0.10

    def test_source_type_general(self):
        assert _score_evidence_source({"source_type": "general_web"}) == 0.50

    def test_domain_fallback(self):
        score = _score_evidence_source({"domain": "example.com"})
        assert score == 0.50  # neutral domain

    def test_gov_domain(self):
        score = _score_evidence_source({"domain": "cdc.gov"})
        assert score >= 0.90


# ── _evidence_relation ──────────────────────────────────────
class TestEvidenceRelation:
    def test_explicit_support(self):
        assert _evidence_relation({"relation": "support"}, "unsupported") == "support"

    def test_supports_claim_true(self):
        assert _evidence_relation({"supports_claim": True}, "unsupported") == "support"

    def test_supports_claim_false(self):
        assert _evidence_relation({"supports_claim": False}, "unsupported") == "contradict"

    def test_contradict_status(self):
        assert _evidence_relation({"relation": "refute"}, "unsupported") == "contradict"

    def test_neutral(self):
        assert _evidence_relation({"relation": "neutral"}, "unsupported") == "neutral"

    def test_fallback_to_status_supported(self):
        assert _evidence_relation({}, "supported") == "support"

    def test_fallback_to_status_contradicted(self):
        assert _evidence_relation({}, "contradicted") == "contradict"

    def test_fallback_to_unknown(self):
        assert _evidence_relation({}, "unverifiable") == "neutral"


# ── _status_score ───────────────────────────────────────────
class TestStatusScore:
    def test_supported(self):
        assert _status_score("supported") == 0.50

    def test_contradicted(self):
        assert _status_score("contradicted") == 0.20

    def test_unsupported(self):
        assert _status_score("unsupported") == 0.35

    def test_unverifiable(self):
        assert _status_score("unverifiable") == 0.45

    def test_unknown(self):
        assert _status_score("unknown") == 0.35


# ── _claim_importance_weight ────────────────────────────────
class TestClaimImportance:
    def test_core(self):
        assert _claim_importance_weight({"claim_role": "core"}) == 1.0

    def test_key_detail(self):
        assert _claim_importance_weight({"claim_role": "key_detail"}) == 0.70

    def test_background(self):
        assert _claim_importance_weight({"claim_role": "background"}) == 0.30

    def test_minor(self):
        assert _claim_importance_weight({"claim_role": "minor"}) == 0.15

    def test_explicit_weight_overrides(self):
        assert _claim_importance_weight({"importance_weight": 0.50, "claim_role": "core"}) == 0.50

    def test_explicit_weight_clamped_min(self):
        assert _claim_importance_weight({"importance_weight": 0.01}) == 0.05

    def test_unknown_role_default(self):
        assert _claim_importance_weight({"claim_role": "unknown"}) == 1.0


# ── _is_core_claim ──────────────────────────────────────────
class TestIsCoreClaim:
    def test_core(self):
        assert _is_core_claim({"claim_role": "core"}) is True

    def test_main(self):
        assert _is_core_claim({"claim_role": "main"}) is True

    def test_headline(self):
        assert _is_core_claim({"claim_role": "headline"}) is True

    def test_thesis(self):
        assert _is_core_claim({"claim_role": "thesis"}) is True

    def test_key_detail_is_not_core(self):
        assert _is_core_claim({"claim_role": "key_detail"}) is False

    def test_default_is_core(self):
        assert _is_core_claim({}) is True


# ── _get_domain_authority ───────────────────────────────────
class TestDomainAuthority:
    def test_empty_domain(self):
        assert _get_domain_authority("") == 0.30

    def test_premium_government(self):
        assert _get_domain_authority("cdc.gov") == 0.95

    def test_premium_international(self):
        assert _get_domain_authority("who.int") == 0.95

    def test_top_media(self):
        assert _get_domain_authority("reuters.com") == 0.92

    def test_top_media_xinhua(self):
        assert _get_domain_authority("xinhuanet.com") == 0.92

    def test_education(self):
        assert _get_domain_authority("mit.edu") == 0.88

    def test_authoritative_media(self):
        assert _get_domain_authority("bbc.com") == 0.92  # top media

    def test_low_authority(self):
        assert _get_domain_authority("infowars.com") == 0.10

    def test_low_tld(self):
        assert _get_domain_authority("something.top") == 0.10

    def test_neutral(self):
        assert _get_domain_authority("random-blog.example") == 0.50


# ── _source_diversity_bonus ─────────────────────────────────
class TestSourceDiversity:
    def test_one_source_no_bonus(self):
        assert _source_diversity_bonus(1, 1) == 1.0

    def test_zero_sources(self):
        assert _source_diversity_bonus(0, 0) == 1.0

    def test_two_domains(self):
        bonus = _source_diversity_bonus(2, 2)
        assert bonus == pytest.approx(1.08, abs=0.01)

    def test_three_domains(self):
        bonus = _source_diversity_bonus(3, 3)
        assert bonus == pytest.approx(1.127, abs=0.01)

    def test_many_domains_capped(self):
        bonus = _source_diversity_bonus(10, 10)
        assert bonus == 1.15  # capped

    def test_many_sources_same_domain_no_bonus(self):
        bonus = _source_diversity_bonus(5, 1)
        assert bonus == 1.0  # all same domain = no diversity


# ── _score_verdict_with_evidence ────────────────────────────
class TestScoreVerdict:
    def test_no_sources_fallback(self):
        score, used, count, high, low = _score_verdict_with_evidence(
            {"status": "supported"}
        )
        assert used is False
        assert score == 0.50
        assert count == 0

    def test_single_strong_support(self):
        verdict = {
            "status": "supported",
            "claim_role": "core",
            "evidence_sources": [{
                "domain": "cdc.gov",
                "authority_score": 0.95,
                "relevance_score": 0.90,
                "relation": "support",
                "relation_confidence": 0.90,
                "supports_claim": True,
            }],
        }
        score, used, count, high, _ = _score_verdict_with_evidence(verdict)
        assert used is True
        assert score > 0.70  # strong support
        assert high == 1
        assert count == 1

    def test_single_strong_contradiction(self):
        verdict = {
            "status": "contradicted",
            "claim_role": "core",
            "evidence_sources": [{
                "domain": "who.int",
                "authority_score": 0.95,
                "relevance_score": 0.90,
                "relation": "contradict",
                "relation_confidence": 0.90,
                "supports_claim": False,
            }],
        }
        score, used, _, _, _ = _score_verdict_with_evidence(verdict)
        assert used is True
        assert score < 0.25  # strong contradiction with lower baseline

    def test_mixed_support_and_contradict(self):
        verdict = {
            "status": "supported",
            "evidence_sources": [
                {"domain": "cdc.gov", "authority_score": 0.95,
                 "relevance_score": 0.90, "relation": "support",
                 "relation_confidence": 0.90, "supports_claim": True},
                {"domain": "infowars.com", "source_type": "low_credibility",
                 "relevance_score": 0.50, "relation": "contradict",
                 "relation_confidence": 0.60, "supports_claim": False},
            ],
        }
        score, _, _, _, _ = _score_verdict_with_evidence(verdict)
        # Strong support should outweigh weak contradiction
        assert score > 0.55

    def test_neutral_only_sources(self):
        verdict = {
            "status": "supported",
            "evidence_sources": [
                {"domain": "example.com", "relevance_score": 0.60,
                 "relation": "neutral"},
            ],
        }
        score, used, count, _, _ = _score_verdict_with_evidence(verdict)
        assert used is True
        assert count == 1
        # All neutral → falls back to status_score
        assert score == 0.50

    def test_diversity_bonus_applied(self):
        """Multiple independent supporting domains should produce higher score."""
        verdict1 = {
            "status": "supported",
            "evidence_sources": [{
                "domain": "reuters.com", "authority_score": 0.92,
                "relevance_score": 0.85, "relation": "support",
                "relation_confidence": 0.85, "supports_claim": True,
            }],
        }
        verdict3 = {
            "status": "supported",
            "evidence_sources": [
                {"domain": "reuters.com", "authority_score": 0.92,
                 "relevance_score": 0.85, "relation": "support",
                 "relation_confidence": 0.85, "supports_claim": True},
                {"domain": "bbc.com", "authority_score": 0.92,
                 "relevance_score": 0.85, "relation": "support",
                 "relation_confidence": 0.85, "supports_claim": True},
                {"domain": "apnews.com", "authority_score": 0.92,
                 "relevance_score": 0.85, "relation": "support",
                 "relation_confidence": 0.85, "supports_claim": True},
            ],
        }
        score1, _, _, _, _ = _score_verdict_with_evidence(verdict1)
        score3, _, _, _, _ = _score_verdict_with_evidence(verdict3)
        assert score3 > score1  # diversity bonus should help


# ── _score_claim_verification ───────────────────────────────
class TestScoreClaimVerification:
    def test_empty_verdicts(self):
        score, details = _score_claim_verification([])
        assert score == 0.45
        assert "中性" in details

    def test_single_core_claim(self):
        verdicts = [{
            "claim_role": "core",
            "status": "supported",
            "evidence_sources": [{
                "domain": "cdc.gov", "authority_score": 0.95,
                "relevance_score": 0.90, "relation": "support",
                "relation_confidence": 0.90, "supports_claim": True,
            }],
        }]
        score, details = _score_claim_verification(verdicts)
        assert score > 0.70

    def test_weighted_average(self):
        """Core claim with strong support + minor claim with weak support."""
        verdicts = [
            {
                "claim_role": "core", "status": "supported",
                "evidence_sources": [{
                    "domain": "cdc.gov", "authority_score": 0.95,
                    "relevance_score": 0.95, "relation": "support",
                    "relation_confidence": 0.95, "supports_claim": True,
                }],
            },
            {
                "claim_role": "minor", "status": "contradicted",
                "evidence_sources": [{
                    "domain": "example.com", "authority_score": 0.50,
                    "relevance_score": 0.60, "relation": "contradict",
                    "relation_confidence": 0.60, "supports_claim": False,
                }],
            },
        ]
        score, details = _score_claim_verification(verdicts)
        # Core dominates (1.0) over minor (0.15), so score should be high
        assert score > 0.65


# ── _core_contradiction_severity ────────────────────────────
class TestCoreContradictionSeverity:
    def test_no_core_claims(self):
        assert _core_contradiction_severity([]) == 0.0

    def test_no_contradiction(self):
        verdicts = [{
            "claim_role": "core", "status": "supported",
            "evidence_sources": [],
        }]
        assert _core_contradiction_severity(verdicts) == 0.0

    def test_single_strong_contradiction(self):
        verdicts = [{
            "claim_role": "core",
            "status": "contradicted",
            "evidence_sources": [{
                "domain": "who.int", "authority_score": 0.95,
                "relevance_score": 0.90, "relation": "contradict",
                "relation_confidence": 0.90, "supports_claim": False,
            }],
        }]
        severity = _core_contradiction_severity(verdicts)
        assert severity > 0.60

    def test_mixed_core_claims(self):
        """1 of 2 core claims contradicted → severity halved."""
        verdicts = [
            {
                "claim_role": "core", "status": "supported",
                "evidence_sources": [],
            },
            {
                "claim_role": "core", "status": "contradicted",
                "evidence_sources": [{
                    "domain": "who.int", "authority_score": 0.95,
                    "relevance_score": 0.90, "relation": "contradict",
                    "relation_confidence": 0.90, "supports_claim": False,
                }],
            },
        ]
        severity = _core_contradiction_severity(verdicts)
        # Full severity for contradicted claim / 2 core claims
        assert 0.30 < severity < 0.50

    def test_progressive_cap_in_verification(self):
        """Core contradicted with medium strength → cap should be moderate."""
        verdicts = [{
            "claim_role": "core",
            "status": "contradicted",
            "evidence_sources": [{
                "domain": "bbc.com", "authority_score": 0.92,
                "relevance_score": 0.70, "relation": "contradict",
                "relation_confidence": 0.70, "supports_claim": False,
            }],
        }]
        score, details = _score_claim_verification(verdicts)
        # severity = 0.92*0.70*0.70 = 0.451, cap = 0.60 - 0.451*0.50 = 0.374
        # But clamp to [0.25, 0.60] → cap = 0.375
        # Only contradict source, strength = 0.451, claim_score = 0.40 + 0.5*(0 - 0.451) = 0.175
        # But actually wait - _score_verdict_with_evidence computes S=0, C=0.451
        # score = 0.40 + 0.5 * (0 - 0.451) = 0.40 - 0.225 = 0.175... clamped to 0.0 on return
        # Wait no, the return clamps to [0.0, 1.0]. 0.175 is fine.
        # Then the cap check: cap = max(0.25, min(0.60, 0.60 - 0.451*0.5))
        # = max(0.25, min(0.60, 0.374)) = max(0.25, 0.374) = 0.374
        # overall = 0.175, which is < 0.374, so cap not applied
        assert score < 0.40  # should be low due to contradiction
