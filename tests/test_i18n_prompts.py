"""UI-language LLM output (bot-i18n T5): judge rationale, interview prep, STAR."""

from cva.claimsdb import Claim, ClaimsDB
from cva.match.interview_prep import (
    PrepInputs,
    build_facts_prompt,
    build_prep_prompt,
)
from cva.match.judge import build_system_prompt
from cva.star import (
    edit_keyboard,
    field_label,
    field_question,
    question_message,
    story_message,
)
from cva.tracker import STAR_PLACEHOLDER, StarStory


def _claims() -> ClaimsDB:
    claim = Claim(
        id="exp-a-role", category="experience", text="Backend developer", tags=[]
    )
    return ClaimsDB(claims=[claim], by_id={claim.id: claim})


def _inputs() -> PrepInputs:
    return PrepInputs(
        stage="rrhh",
        title="Dev",
        company="Acme",
        posting_text="text",
        cover_letter=None,
        cv_variant=None,
        judge=None,
        claims_digest="exp-a-role: Backend developer",
        stories=[],
        notes=[],
        salary_text="",
    )


def _story() -> StarStory:
    return StarStory(
        id=3,
        title="Migration",
        source_claim_ids=[],
        updated_at="2026-01-01",
        situation="s",
        task=STAR_PLACEHOLDER,
        action="a",
        result=STAR_PLACEHOLDER,
        use_for="u",
        complete=False,
    )


def test_judge_prompt_rationale_language_follows_ui_language() -> None:
    en = build_system_prompt(_claims(), lang="en")
    es = build_system_prompt(_claims(), lang="es")
    assert en.endswith("rationale MUST be written in English.")
    assert es.endswith("rationale MUST be written in Spanish.")
    tail = "rationale MUST be written in "
    assert en.rsplit(tail, 1)[0] == es.rsplit(tail, 1)[0]  # nothing else changes
    assert build_system_prompt(_claims()) == es  # transitional default


def test_prep_prompts_language_follows_ui_language() -> None:
    en, _ = build_prep_prompt(_inputs(), "en")
    es, user_es = build_prep_prompt(_inputs(), "es")
    assert "in English (the candidate reads English)" in en
    assert "it is likely" in en and "es probable" not in en
    assert "in Spanish (the candidate reads Spanish)" in es
    assert "'es probable'" in es
    assert build_prep_prompt(_inputs()) == (es, user_es)
    facts_en, _ = build_facts_prompt(company="A", position="P", lang="en")
    facts_es, _ = build_facts_prompt(company="A", position="P", lang="es")
    assert "each fact in English." in facts_en
    assert "each fact in Spanish." in facts_es


def test_star_texts_follow_ui_language() -> None:
    assert field_question("task", "en") == "Task (your responsibility)"
    assert field_question("task", "es") == "Tarea (tu responsabilidad)"
    assert question_message(2, 5, "task", "en").startswith("2/5 · Task")
    assert field_label("use_for", "en") == "what it is good for"
    assert field_label("use_for", "es") == "para qué sirve"
    assert field_label("unknown", "en") == "unknown"
    en = story_message(_story(), "en")
    assert "Situation: s" in en and "Missing: task, result" in en
    assert "✏️ incomplete" in en
    es = story_message(_story())
    assert "Situación: s" in es and "Falta: tarea, resultado" in es
    labels = [b.text for b in edit_keyboard(3, "en").inline_keyboard[0]]
    assert labels == ["Situation", "Task", "Action"]
    assert edit_keyboard(3).inline_keyboard[1][1].callback_data == "st:f:3:4"
