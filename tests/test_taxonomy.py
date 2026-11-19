from __future__ import annotations

import pytest

from jobpulse.taxonomy import Taxonomy


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("k8s", "Kubernetes"),
        ("K8S", "Kubernetes"),
        ("postgres", "PostgreSQL"),
        ("golang", "Go"),
        ("Go", "Go"),
        ("PyTorch", "PyTorch"),
        ("github actions", "CI/CD"),
        ("Large Language Models", "LLMs"),
        ("Python programming", "Python"),  # qualifier around exactly one known skill
        ("C++", "C++"),
        ("underwater basket weaving", None),
        ("", None),
    ],
)
def test_canonical(tax: Taxonomy, name: str, expected: str | None) -> None:
    assert tax.canonical(name) == expected


def test_find_handles_symbols_and_boundaries(tax: Taxonomy) -> None:
    text = "Experience with C, C++ or C#; Node.js and .NET; REST APIs on AWS."
    found = tax.find(text)
    assert {"C", "C++", "C#", "Node.js", ".NET", "REST APIs", "AWS"} <= found


def test_short_exact_names_need_list_context(tax: Taxonomy) -> None:
    assert "Go" not in tax.find("Go beyond what is expected. We go fast.")
    assert "Go" in tax.find("Proficient in Python, Go, or Java")
    assert "R" not in tax.find("R&D team. Our R team.")  # no list punctuation next to it
    assert "R" in tax.find("Statistics with R, Python")


def test_ambiguous_words_do_not_false_match(tax: Taxonomy) -> None:
    text = "Spring 2027 term. Networking events. Rest of the team. Attach your CV. Embedded in a squad."
    assert tax.find(text) == set()


def test_longest_alias_wins(tax: Taxonomy) -> None:
    assert tax.find("React Native apps") >= {"React Native"}
    assert tax.find("Retrieval-Augmented Generation") == {"RAG"}
