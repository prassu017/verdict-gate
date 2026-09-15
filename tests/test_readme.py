from pathlib import Path

README = Path(__file__).resolve().parent.parent / "README.md"


def test_readme_is_at_most_500_words():
    assert len(README.read_text(encoding="utf-8").split()) <= 500
