"""The original owner's persona text: the DEFAULTS of the prompt persona.

The judge rubric/anchors/facts and the cover-letter branch emphasis and
career-change rule describe one specific candidate (profile-extraction E4).
They live here, apart from the generic engine in ``judge.py`` and
``cover_letter.py``, and a profile replaces them file by file
(``judge/facts.md``, ``judge/anchors.md``, ``judge/rubric_persona.md`` and
``preferences.toml`` ``[cover_letter]``; see ``cva/profile_persona.py``).
With no profile these strings are used verbatim, so the rendered prompts are
byte-identical to what the bot always sent (``tests/test_prompt_persona.py``).
"""

from typing import Final

from cva.match.persona import CoverLetterPersona, JudgePersona, RubricPersona

OWNER_JUDGE_PERSONA: Final[JudgePersona] = JudgePersona(
    facts=(
        "Candidate timeline (do not contradict it): an FP Dual internship "
        "(9 months) + freelance development (10 months) + a coding school "
        "(current) - total professional experience is well under 2 years "
        "(~1.7)."
    ),
    named_anchors=(
        "- AI Engineer at a data consultancy -> score 55, apply_with_caveats "
        "(RAG/LLM core fit; soft wall: under the requested 2 years in AI/ML "
        "roles; a cloud ecosystem as a plus, not core).\n"
        "- AI Engineer at a media group -> score 35, discard (quantitative "
        "degree with no 'or equivalent'; 3+ years applied ML; classic ML "
        "libraries and a cloud platform as hard walls despite a real NLP/RAG "
        "project fit).\n"
        "- IA Developer at an IT services firm -> score 10, discard (titled "
        "'IA Developer' but the body is a Senior Full-Stack role with 7+ "
        "years, .NET/Angular/Terraform as the core stack).\n"
        "- Junior AI Engineer at an IT integrator (described only) -> score "
        "76, apply: FP/vocational training explicitly accepted, 1-2 years, "
        "RAG/LLM/FastAPI/Django as the role's core.\n"
        "- Python developer at a start-up (described only) -> score 74, "
        "apply: junior/FP explicitly welcome, Python/testing as the role's "
        "core, a regional language requested."
    ),
    rubric=RubricPersona(
        manual_analyses="exactly like a careful human reviewer would",
        experience_rule=(
            "Required years of experience up to and including ~3 years (e.g. "
            "'2-3 años', '3+ years') is a MINOR penalty ONLY - it is NEVER a wall "
            "and NEVER by itself a reason to discard: junior candidates normally "
            "apply to offers asking for up to 3 years. Years required well above "
            "3 (4+ years) IS a hard wall."
        ),
        degree_rule=(
            "A degree requirement phrased with ANY equivalence wording ('o "
            "similar', 'or equivalent (experience/qualification)', 'Formación en "
            "X o similar') ACCEPTS the candidate's FP Superior - it is NOT a wall. "
            "A degree requirement with NO such equivalence wording IS a hard "
            "wall."
        ),
        lacking_core_rule=(
            "Using AI coding tools (Claude Code, Cursor, Codex and similar) in the "
            "role's daily workflow is a SMALL PLUS ONLY - it can NEVER outweigh a "
            "daily core the candidate lacks (full-stack development with frontend "
            "frameworks, cloud-platform engineering, Java/.NET, or BI/data-analyst "
            "work). A role whose daily core is full-stack frontend or cloud "
            "infrastructure STAYS LOW-SCORED even when it praises AI-assisted or "
            "AI-native development - that praise is never a substitute for the "
            "missing core."
        ),
        hard_walls=(
            "A hard wall is ONLY one of these four cases - never any other "
            "requirement, however strict it sounds:\n"
            "1. A degree requirement with NO equivalence wording (see above).\n"
            "2. Years of required experience of 4+ (see above; up to and "
            "including ~3 years is a minor penalty only, never a wall).\n"
            "3. Seniority or technical leadership of a team (Senior/Lead/"
            "Principal, 'technical reference', explicit technical leadership of "
            "the team, managing or mentoring team members, strategy with "
            "management responsibilities).\n"
            "4. A mandatory stack with ZERO evidence in the claims that is the "
            "role's DAILY CORE - what most of the working week is spent on (e.g. "
            "Java/Spring, .NET, Azure/cloud-platform engineering as the core job, "
            "Power BI/BI reporting, classic ML model training as the main job, "
            "training or fine-tuning your own models - LoRA/QLoRA/PEFT or "
            "otherwise - as the main job). This holds in mixed roles too: when "
            "that stack is the daily core and the candidate's AI/LLM strength is "
            "only a secondary part of the job (prototypes, a nice-to-have, a "
            "minor bullet), it is still a HARD wall. Classic ML or a cloud "
            "platform counts as the DAILY CORE only when the responsibilities "
            "clearly make it most of the working week (e.g. the role is about "
            "training/tuning predictive models, or cloud-platform engineering IS "
            "the job) - a generic 'develop and deploy ML models' responsibility "
            "line, a framework listed as one option among several, or a cloud "
            "platform mentioned in the requirements do NOT by themselves make it "
            "the daily core, especially when the role instead centers on "
            "LLM/GenAI work (chatbots, agents, RAG)."
        ),
        soft_walls=(
            "EVERYTHING ELSE is a SOFT wall (lower the score a bit, NEVER force "
            "discard by itself) - this explicitly includes: a specific tool the "
            "candidate lacks that is merely mentioned or listed as a plus; a "
            "cloud platform (AWS/Azure/GCP) even when it is stated as a "
            "requirement rather than a plus, as long as it is not the role's "
            "daily core; a specific framework required, ESPECIALLY when it is "
            "listed as one option among several (e.g. 'TensorFlow, PyTorch or "
            "Scikit-learn'); a generic 'develop and deploy ML models' "
            "responsibility line that is not backed by a clearly ML-centric role; "
            "a new business domain the candidate has not worked in before (e.g. "
            "marketing, healthcare, an industrial vertical); and anything "
            "phrased as 'nice to have' / 'a plus' / merely mentioned."
        ),
        positive_signals=(
            "Positive signals: junior-friendly wording, FP/vocational training "
            "explicitly accepted, personal/own projects counted as experience, "
            "self-taught paths accepted, RAG/LLM/evaluation/structured "
            "outputs/function calling as the role's core (the candidate's "
            "strongest area), Catalan requested (native), few applicants or a "
            "fresh posting."
        ),
    ),
)

OWNER_COVER_LETTER_PERSONA: Final[CoverLetterPersona] = CoverLetterPersona(
    branch_emphasis={
        "AI": (
            "AI branch: emphasise applied AI work (LLM systems, RAG, evaluation, "
            "constrained decoding, getting models to production), taking only the "
            "matching claims."
        ),
        "BACKEND": (
            "BACKEND branch: emphasise backend engineering (Python, Django/FastAPI, "
            "APIs, SQL, real client delivery), mentioning LLM work only as a plus, "
            "taking only the matching claims."
        ),
    },
    career_change=(
        "The career change (software development to applied AI) takes ONE "
        "sentence, framed as a reason with evidence of learning speed (a coding "
        "school, projects built from scratch). No apology, no 'nueva pasión'."
    ),
)
