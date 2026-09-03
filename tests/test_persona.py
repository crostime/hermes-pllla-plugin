from pllla_bridge.pairing import Identity
from pllla_bridge.persona import (
    PERSONA_MARKER,
    build_soul_markdown,
    install_persona,
    soul_file_is_ours,
)

IDENTITY = Identity(name="Hermes Bot", bio="A careful helper.", system_prompt="Answer briefly.")


def test_soul_markdown_carries_the_marker_and_the_persona():
    soul = build_soul_markdown(IDENTITY)
    assert soul.startswith("# SOUL.md - Hermes Bot\n")
    assert PERSONA_MARKER in soul
    assert "You are Hermes Bot, a PLLLA agent." in soul
    assert soul.rstrip().endswith("Answer briefly.")


def test_ownership_rule_missing_default_or_marked_is_ours_hand_edited_is_not():
    assert soul_file_is_ours(None)
    assert soul_file_is_ours("You are Hermes Agent, built by Nous Research. Be direct…")
    assert soul_file_is_ours("# SOUL.md\n\n<!-- pllla:persona -->\nold persona")
    assert not soul_file_is_ours("# My own soul\nI like cats.")


def test_install_persona_writes_only_when_ours_and_only_when_changed(tmp_path):
    assert install_persona(tmp_path, IDENTITY) is True
    written = (tmp_path / "SOUL.md").read_text(encoding="utf-8")
    assert PERSONA_MARKER in written
    # 같은 내용이면 다시 쓰지 않는다.
    assert install_persona(tmp_path, IDENTITY) is False
    # 사람이 손본 파일은 건드리지 않는다.
    (tmp_path / "SOUL.md").write_text("# Mine\nhands off", encoding="utf-8")
    assert install_persona(tmp_path, IDENTITY) is False
    assert (tmp_path / "SOUL.md").read_text(encoding="utf-8") == "# Mine\nhands off"
