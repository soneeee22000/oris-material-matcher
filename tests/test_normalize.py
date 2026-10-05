import pytest

from oris_matcher.domain.normalize import normalize

FULLWIDTH_ABC = "".join(chr(code) for code in (0xFF21, 0xFF22, 0xFF23))
NO_BREAK_SPACE = chr(0xA0)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", ""),
        ("   ", ""),
        ("Concrete C30/37", "concrete c30/37"),
        ("  Béton\tC30/37 \n XC4 ", "béton c30/37 xc4"),
        ("BÉTON", "béton"),
        ("12,5 m³", "12.5 m3"),
        ("0,45 kg/m²", "0.45 kg/m2"),
        ("a , b", "a , b"),
        ("C30/37, XC4", "c30/37, xc4"),
        ("Ø 100", "ø 100"),
        ("∅100", "ø100"),
        ("⌀ 25", "ø 25"),
        ("Pipe ø160", "pipe ø160"),
        (FULLWIDTH_ABC, "abc"),
        ("ﬁlter", "filter"),
        (f"a{NO_BREAK_SPACE}b", "a b"),
        ("Straße", "strasse"),
        ("1,2,3", "1.2.3"),
    ],
)
def test_normalize(text: str, expected: str) -> None:
    assert normalize(text) == expected


@pytest.mark.parametrize("text", ["  Béton ∅100 12,5 m³ ", f"{FULLWIDTH_ABC}  x", "Straße Ø"])
def test_normalize_is_idempotent(text: str) -> None:
    assert normalize(normalize(text)) == normalize(text)
