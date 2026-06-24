from app.services.tts import _parse_lines, clean_spoken_text


def test_stage_directions_are_not_spoken():
    dialogue = "老周：（抬头，推了推眼镜）小姐，寄东西吗？\n女孩：【微微喘气】嗯，这些信……"

    assert _parse_lines(dialogue) == [
        ("老周", "小姐，寄东西吗？"),
        ("女孩", "嗯，这些信……"),
    ]


def test_ascii_parentheses_and_multiple_directions_are_removed():
    assert clean_spoken_text("(低声)别怕。[停顿]我在。") == "别怕。我在。"


def test_direction_only_line_is_skipped():
    assert _parse_lines("旁白：（沉默）") == []
