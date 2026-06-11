"""Tests for src.review_store (rule parsing + review persistence + summary)."""

from pathlib import Path

import pytest

from src.review_store import (
    load_reviews,
    parse_guideline_rules,
    save_review,
    summarize_reviews,
)

REPO_PROMPTS = Path(__file__).resolve().parent.parent / "prompts"


def test_parse_rules_from_real_v1_guideline():
    rules = parse_guideline_rules("v1", REPO_PROMPTS)
    assert [r["id"] for r in rules] == [str(i) for i in range(1, 10)]
    assert rules[0]["title"].startswith("Atomicity")
    assert rules[5]["title"].lower().startswith("conditional")


def test_parse_rules_missing_version():
    assert parse_guideline_rules("nope_does_not_exist", REPO_PROMPTS) == []


def test_save_load_delete_roundtrip(tmp_path):
    root = tmp_path / "reviews"
    out = save_review(root, "doc1", "fa|fb", {
        "rules": ["6", "2", "2"],
        "note": "conditional split",
        "resolution": "agree",
        "label_at_review": "granularity",
        "annotators": ["m1", "m2"],
    })
    assert out["n_reviews"] == 1
    assert out["record"]["rules"] == ["2", "6"]          # deduped + sorted

    loaded = load_reviews(root, "doc1")
    assert "fa|fb" in loaded
    assert loaded["fa|fb"]["label_at_review"] == "granularity"

    out = save_review(root, "doc1", "fa|fb", {"delete": True})
    assert out["deleted"] is True
    assert load_reviews(root, "doc1") == {}


def test_invalid_resolution_rejected(tmp_path):
    with pytest.raises(ValueError):
        save_review(tmp_path, "doc1", "k", {"resolution": "whatever"})
    # relabel with a valid target label is fine
    save_review(tmp_path, "doc1", "k", {"resolution": "relabel:contradiction"})


def test_summarize_across_docs(tmp_path):
    root = tmp_path / "reviews"
    save_review(root, "doc1", "p1", {"rules": ["6"], "note": "split conditional",
                                     "label_at_review": "granularity"})
    save_review(root, "doc1", "p2", {"rules": ["6", "2"], "note": "",
                                     "label_at_review": "contradiction"})
    save_review(root, "doc2", "p3", {"rules": [], "note": "noise",
                                     "label_at_review": "redundancy",
                                     "resolution": "dismiss"})
    out = summarize_reviews(root, prompts_dir=REPO_PROMPTS, guideline_version="v1")
    assert out["n_reviews"] == 3
    assert out["n_docs"] == 2
    assert out["n_unattributed"] == 1
    top = out["rules"][0]
    assert top["id"] == "6" and top["n_conflicts"] == 2
    assert top["by_label"] == {"granularity": 1, "contradiction": 1}
    assert top["title"]                                  # resolved from v1 file
    assert any(r["id"] == "2" and r["n_conflicts"] == 1 for r in out["rules"])
