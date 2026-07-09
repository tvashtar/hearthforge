from dm_engine.content.markdown_sections import parse_sections

SAMPLE = """\
Using Ability Scores
====================

Six abilities provide a quick description.

Ability Checks
--------------

An ability check tests talent and training.

### Contests

Sometimes efforts are directly opposed.

#### Typical DCs

| Task | DC |
|------|----|
| Easy | 10 |
"""


def test_parses_setext_and_atx_headings():
    sections = parse_sections(SAMPLE, source="06 mechanics.md")
    paths = [s.heading_path for s in sections]
    assert paths == [
        "Using Ability Scores",
        "Using Ability Scores > Ability Checks",
        "Using Ability Scores > Ability Checks > Contests",
        "Using Ability Scores > Ability Checks > Contests > Typical DCs",
    ]


def test_bodies_attach_to_their_heading():
    sections = {s.heading: s for s in parse_sections(SAMPLE, source="x.md")}
    assert "Six abilities" in sections["Using Ability Scores"].body
    assert "directly opposed" in sections["Contests"].body
    assert "| Easy | 10 |" in sections["Typical DCs"].body


def test_table_separators_are_not_headings():
    sections = parse_sections(SAMPLE, source="x.md")
    assert all("---" not in s.heading for s in sections)
    assert len(sections) == 4
