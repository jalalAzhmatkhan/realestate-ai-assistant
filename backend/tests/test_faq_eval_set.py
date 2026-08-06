"""Structural validation for the hand-authored FAQ retrieval eval set (B25a).

Checkpoint: ``Documentation/audits/2026-08-06-rag-observability-and-faithfulness.md``,
decision 2. This file is `backend/seed_data/faq_eval_set.json` — ``paraphrase`` and
``negative`` cases only. The ``identity`` tier is generated at evaluation run time (B25b)
from `faq.json` itself and is intentionally absent here.

What this guards, and why each check exists:

* **Schema/shape** — a malformed case would fail loudly the moment B25b's evaluation
  runner loads this file, so this is mostly a fast, readable failure point during
  authoring rather than new coverage.
* **``expected_faq_ids`` reference real ids** — a typo'd id (e.g. ``faq-01`` for
  ``faq-001``) would silently make every correct retrieval of that entry look like a
  miss, with nothing else able to catch it. This is the one check that protects the
  metric's correctness, not just the file's syntax.
* **≤3 consecutive words reused from the source question** — mechanical proxy for "this
  is a genuine paraphrase, not the original question with one word swapped". Exactly 3
  is allowed (the checkpoint's own worked example sits at the limit); 4+ is not.
* **Case cap** — keeps the file from growing into a slow, unreviewable eval set.

What this deliberately does NOT check: whether a label is *correct* (i.e. whether the
FAQ entry actually answers the paraphrased query, or whether a negative case is truly
unanswerable by the corpus). That is a judgment call made once, by hand, at authoring
time — see the task's own framing: an invented label is the one failure mode no test
can catch after the fact.
"""

import json
import re
from pathlib import Path

import pytest

from .conftest import SEED_DATA_DIR

EVAL_SET_PATH = Path(SEED_DATA_DIR) / "faq_eval_set.json"
FAQ_CORPUS_PATH = Path(SEED_DATA_DIR) / "faq.json"

MAX_CASES = 60
MAX_CONSECUTIVE_OVERLAP = 3
VALID_TIERS = {"paraphrase", "negative"}


def _tokenize(text: str) -> list[str]:
    """Lowercase word tokens, punctuation stripped. Good enough for a consecutive-run
    check; this is not meant to be linguistically precise, only mechanical."""
    return re.findall(r"[a-z0-9']+", text.lower())


def _max_consecutive_overlap(query: str, source_question: str) -> int:
    """Longest run of tokens that appears, in the same order, contiguously in both
    strings. O(n*m) over short question-length strings, which is all this ever sees."""
    q_tokens = _tokenize(query)
    s_tokens = _tokenize(source_question)
    best = 0
    for i in range(len(q_tokens)):
        for j in range(len(s_tokens)):
            run = 0
            while (
                i + run < len(q_tokens)
                and j + run < len(s_tokens)
                and q_tokens[i + run] == s_tokens[j + run]
            ):
                run += 1
            best = max(best, run)
    return best


@pytest.fixture(scope="module")
def eval_set() -> dict:
    return json.loads(EVAL_SET_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def faq_by_id() -> dict:
    entries = json.loads(FAQ_CORPUS_PATH.read_text(encoding="utf-8"))
    return {entry["id"]: entry for entry in entries}


def test_top_level_shape(eval_set):
    assert eval_set["version"] == "2026-08-06.1"
    assert isinstance(eval_set["cases"], list)
    assert eval_set["cases"], "eval set must not be empty"


def test_case_count_is_under_the_cap(eval_set):
    assert len(eval_set["cases"]) <= MAX_CASES, (
        f"{len(eval_set['cases'])} cases exceeds the {MAX_CASES}-case file cap"
    )


def test_case_ids_are_unique(eval_set):
    ids = [case["id"] for case in eval_set["cases"]]
    assert len(ids) == len(set(ids)), "duplicate case ids present"


def test_only_paraphrase_and_negative_tiers_are_authored(eval_set):
    """The `identity` tier belongs to B25b's runtime generation, not this file — this
    guards against someone hand-adding identity cases (which would then be duplicated
    when the runner also generates them)."""
    tiers = {case["tier"] for case in eval_set["cases"]}
    assert tiers <= VALID_TIERS, f"unexpected tier(s) in authored file: {tiers - VALID_TIERS}"


def test_every_case_has_the_required_fields(eval_set):
    required = {"id", "tier", "query", "expected_faq_ids"}
    for case in eval_set["cases"]:
        missing = required - case.keys()
        assert not missing, f"case {case.get('id', '<no id>')} missing fields: {missing}"
        assert isinstance(case["query"], str) and case["query"].strip(), (
            f"case {case['id']}: query must be a non-empty string"
        )
        assert isinstance(case["expected_faq_ids"], list), (
            f"case {case['id']}: expected_faq_ids must be a list"
        )


def test_negative_cases_have_no_expected_ids(eval_set):
    for case in eval_set["cases"]:
        if case["tier"] == "negative":
            assert case["expected_faq_ids"] == [], (
                f"case {case['id']}: negative cases must have expected_faq_ids: []"
            )


def test_paraphrase_cases_have_at_least_one_expected_id(eval_set):
    for case in eval_set["cases"]:
        if case["tier"] == "paraphrase":
            assert case["expected_faq_ids"], (
                f"case {case['id']}: paraphrase cases must reference at least one FAQ id"
            )


def test_every_expected_id_exists_in_the_faq_corpus(eval_set, faq_by_id):
    """The check that actually protects the metric: a typo'd id here silently makes a
    correct retrieval look like a miss, invisibly, with no other test able to catch it."""
    for case in eval_set["cases"]:
        for faq_id in case["expected_faq_ids"]:
            assert faq_id in faq_by_id, (
                f"case {case['id']}: expected_faq_ids references unknown id {faq_id!r} "
                "(typo, or the corpus changed since this case was authored)"
            )


def test_paraphrase_cases_do_not_reuse_more_than_three_consecutive_words(eval_set, faq_by_id):
    for case in eval_set["cases"]:
        if case["tier"] != "paraphrase":
            continue
        for faq_id in case["expected_faq_ids"]:
            source_question = faq_by_id[faq_id]["question"]
            overlap = _max_consecutive_overlap(case["query"], source_question)
            assert overlap <= MAX_CONSECUTIVE_OVERLAP, (
                f"case {case['id']}: reuses {overlap} consecutive words from "
                f"{faq_id}'s question ({source_question!r}) — max allowed is "
                f"{MAX_CONSECUTIVE_OVERLAP}"
            )


def test_the_worked_example_from_the_authoring_rule_sits_at_the_allowed_limit():
    """Pins the checker's own calibration against the checkpoint's worked example:
    ``"What documents do I need to rent a property?"`` -> ``"what papers do I have to
    bring to rent a place"`` is the *allowed* case (overlap == 3, "to rent a"), not a
    violation. If this ever starts failing, the tokenizer/window logic changed meaning,
    not the fixture data."""
    overlap = _max_consecutive_overlap(
        "what papers do I have to bring to rent a place",
        "What documents do I need to rent a property?",
    )
    assert overlap == MAX_CONSECUTIVE_OVERLAP


@pytest.mark.parametrize("expected_tier_count", [{"paraphrase": 29, "negative": 8}])
def test_tier_counts_match_the_authored_distribution(eval_set, expected_tier_count):
    """Documents the exact distribution as-authored (not just '~28 / ~8' from the task)
    so a future edit to this file has to touch this assertion deliberately rather than
    silently drifting the tier balance."""
    counts: dict[str, int] = {}
    for case in eval_set["cases"]:
        counts[case["tier"]] = counts.get(case["tier"], 0) + 1
    assert counts == expected_tier_count
