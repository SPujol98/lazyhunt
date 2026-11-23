"""Tailored-CV strings: the preview (``cva.cvgen.tailor``) and the Telegram
flow (``tailor.bot.*``, bot.py); keys ``tailor.``."""

from typing import Final

EN: Final[dict[str, str]] = {
    "tailor.section.experience": "Experience",
    "tailor.section.profile": "Profile",
    "tailor.section.skills": "Skills",
    "tailor.section.education": "Education",
    "tailor.section.languages": "Languages",
    "tailor.change.moved_top": "{section}: «{item}» moves to the top",
    "tailor.change.reordered": "{section}: order adjusted to this offer",
    "tailor.change.headline": "Headline: {skills}",
    "tailor.change.swap": "«{old}» → «{new}»",
    "tailor.change.new_synonym": "🆕 new synonym: «{old}» → «{new}»",
    "tailor.part.order": "order",
    "tailor.part.headline": "headline",
    "tailor.part.substitution": "word swap",
    "tailor.dropped": "Dropped ({part}, {ref}): {reason}",
    "tailor.reason.unknown_section": "that section is not on your CV",
    "tailor.reason.not_in_section": "that item is not in this section",
    "tailor.reason.duplicate": "repeated",
    "tailor.reason.order_completed": "kept after the reordered items",
    "tailor.reason.not_a_skill": "not a skill on your CV",
    "tailor.reason.limit": "over the limit of changes",
    "tailor.reason.unknown_item": "that item is not on your CV",
    "tailor.reason.from_not_in_text": "the original word is not in that item",
    "tailor.reason.not_a_word_swap": "only single words or short terms can be swapped",
    "tailor.reason.same_text": "nothing would change",
    "tailor.reason.language": "the offer is in another language than the CV",
    "tailor.reason.to_not_in_offer": "the new word is not in the offer",
    "tailor.reason.to_not_synonym": "the new word is not an approved synonym of that item",
    "tailor.reason.whitelist": "it would add words that are not in your claims",
    "tailor.reason.synonym_number": "a new word cannot bring in numbers or years",
    "tailor.reason.synonym_other_claim": (
        "that word belongs to another item of your profile"
    ),
    "tailor.reason.synonym_title": "a new word cannot be a job title or a degree",
    "tailor.reason.synonym_technology": (
        "a new word cannot be a tool, technology or company"
    ),
    "tailor.reason.synonym_name": "the offer writes it as a name, not a common word",
    "tailor.reason.synonym_not_soft": (
        "new synonyms are only offered for soft skills or language level "
        "wording with the same meaning"
    ),
    "tailor.reason.unsafe_text": (
        "only plain words can be swapped (no symbols, other alphabets or "
        "hidden characters)"
    ),
    "tailor.reason.from_number": "numbers, dates and counts are never changed",
    "tailor.reason.from_in_lead": "the role and the company are never changed",
    "tailor.reason.from_proper_noun": "names are never changed",
    "tailor.bot.button.adapt": "✨ Adapt to this offer",
    "tailor.bot.button.use": "✅ Use this CV",
    "tailor.bot.button.remove": "↩️ Remove change {n}",
    "tailor.bot.button.discard": "Discard",
    "tailor.bot.preparing": "Preparing an adapted CV…",
    "tailor.bot.preview": (
        "✨ Adapted CV for offer #{id}, starting from your base CV. "
        "Proposed changes:\n{changes}"
    ),
    "tailor.bot.preview_note": "Nothing is final until you press «✅ Use this CV».",
    "tailor.bot.new_synonym_note": (
        "🆕 = a new synonym, your call: if you press «✅ Use this CV» it is "
        "saved to your profile (synonyms.json), next time it applies on its "
        "own and it also helps match offers. Remove it to keep your word."
    ),
    "tailor.bot.dropped.one": "{n} suggestion dropped: not in your profile",
    "tailor.bot.dropped.other": "{n} suggestions dropped: not in your profile",
    "tailor.bot.no_changes": "Your base CV already fits; nothing to change.",
    "tailor.bot.nothing_left": "No changes left; your base CV stays as it is.",
    "tailor.bot.failed": (
        "Could not prepare the adapted CV right now; your base CV is unchanged. "
        "Try again later."
    ),
    "tailor.bot.discarded": "Adapted CV discarded; your base CV stays as it is.",
    "tailor.bot.expired": (
        "This adapted CV is no longer available; press «✨ Adapt to this offer» again."
    ),
    "tailor.bot.not_adaptable": (
        "This base CV cannot be adapted: only CVs lazyhunt created can (see /cvs)."
    ),
    "tailor.bot.unsafe": (
        "The adapted CV would contain words that are not in your profile, so it "
        "was not made. Your base CV is unchanged."
    ),
    "tailor.bot.too_long": (
        "The adapted CV would be longer than your base CV, so it was not made. "
        "Remove a change or keep the base CV."
    ),
    "tailor.bot.adapted_caption": (
        "✨ Adapted CV for this offer. Your base CV is still above and "
        "«🔄 Redo CV» sends it again."
    ),
}
ES: Final[dict[str, str]] = {
    "tailor.section.experience": "Experiencia",
    "tailor.section.profile": "Perfil",
    "tailor.section.skills": "Habilidades",
    "tailor.section.education": "Formación",
    "tailor.section.languages": "Idiomas",
    "tailor.change.moved_top": "{section}: «{item}» pasa arriba del todo",
    "tailor.change.reordered": "{section}: orden ajustado a esta oferta",
    "tailor.change.headline": "Titular: {skills}",
    "tailor.change.swap": "«{old}» → «{new}»",
    "tailor.change.new_synonym": "🆕 nuevo sinónimo: «{old}» → «{new}»",
    "tailor.part.order": "orden",
    "tailor.part.headline": "titular",
    "tailor.part.substitution": "cambio de palabra",
    "tailor.dropped": "Descartado ({part}, {ref}): {reason}",
    "tailor.reason.unknown_section": "esa sección no está en tu CV",
    "tailor.reason.not_in_section": "ese elemento no está en esta sección",
    "tailor.reason.duplicate": "repetido",
    "tailor.reason.order_completed": "se mantiene tras los elementos reordenados",
    "tailor.reason.not_a_skill": "no es una habilidad de tu CV",
    "tailor.reason.limit": "supera el límite de cambios",
    "tailor.reason.unknown_item": "ese elemento no está en tu CV",
    "tailor.reason.from_not_in_text": "la palabra original no está en ese elemento",
    "tailor.reason.not_a_word_swap": "solo se pueden cambiar palabras o términos cortos",
    "tailor.reason.same_text": "no cambiaría nada",
    "tailor.reason.language": "la oferta está en otro idioma que el CV",
    "tailor.reason.to_not_in_offer": "la palabra nueva no aparece en la oferta",
    "tailor.reason.to_not_synonym": "la palabra nueva no es un sinónimo aprobado de ese elemento",
    "tailor.reason.whitelist": "añadiría palabras que no están en tus claims",
    "tailor.reason.synonym_number": "una palabra nueva no puede añadir números ni años",
    "tailor.reason.synonym_other_claim": (
        "esa palabra pertenece a otro elemento de tu perfil"
    ),
    "tailor.reason.synonym_title": (
        "una palabra nueva no puede ser un puesto ni una titulación"
    ),
    "tailor.reason.synonym_technology": (
        "una palabra nueva no puede ser una herramienta, tecnología o empresa"
    ),
    "tailor.reason.synonym_name": (
        "la oferta la escribe como un nombre, no como una palabra común"
    ),
    "tailor.reason.synonym_not_soft": (
        "solo se proponen sinónimos nuevos de habilidades blandas o del nivel "
        "de idioma, con el mismo significado"
    ),
    "tailor.reason.unsafe_text": (
        "solo se pueden cambiar palabras normales (sin símbolos, otros "
        "alfabetos ni caracteres ocultos)"
    ),
    "tailor.reason.from_number": "los números, fechas y cantidades nunca se cambian",
    "tailor.reason.from_in_lead": "el puesto y la empresa nunca se cambian",
    "tailor.reason.from_proper_noun": "los nombres nunca se cambian",
    "tailor.bot.button.adapt": "✨ Adaptar a esta oferta",
    "tailor.bot.button.use": "✅ Usar este CV",
    "tailor.bot.button.remove": "↩️ Quitar cambio {n}",
    "tailor.bot.button.discard": "Descartar",
    "tailor.bot.preparing": "Preparando un CV adaptado…",
    "tailor.bot.preview": (
        "✨ CV adaptado para la oferta #{id}, a partir de tu CV base. "
        "Cambios propuestos:\n{changes}"
    ),
    "tailor.bot.preview_note": "Nada es definitivo hasta que pulses «✅ Usar este CV».",
    "tailor.bot.new_synonym_note": (
        "🆕 = un sinónimo nuevo, tú decides: si pulsas «✅ Usar este CV» se "
        "guarda en tu perfil (synonyms.json), la próxima vez se aplica solo y "
        "también ayuda a encajar ofertas. Quítalo para mantener tu palabra."
    ),
    "tailor.bot.dropped.one": "{n} sugerencia descartada: no está en tu perfil",
    "tailor.bot.dropped.other": "{n} sugerencias descartadas: no están en tu perfil",
    "tailor.bot.no_changes": "Tu CV base ya encaja; no hay nada que cambiar.",
    "tailor.bot.nothing_left": "No quedan cambios; tu CV base se queda como está.",
    "tailor.bot.failed": (
        "No he podido preparar el CV adaptado ahora; tu CV base no cambia. "
        "Inténtalo más tarde."
    ),
    "tailor.bot.discarded": "CV adaptado descartado; tu CV base se queda como está.",
    "tailor.bot.expired": (
        "Este CV adaptado ya no está disponible; pulsa «✨ Adaptar a esta oferta» "
        "otra vez."
    ),
    "tailor.bot.not_adaptable": (
        "Este CV base no se puede adaptar: solo los CVs creados con lazyhunt "
        "(mira /cvs)."
    ),
    "tailor.bot.unsafe": (
        "El CV adaptado tendría palabras que no están en tu perfil, así que no se "
        "ha creado. Tu CV base no cambia."
    ),
    "tailor.bot.too_long": (
        "El CV adaptado ocuparía más que tu CV base, así que no se ha creado. "
        "Quita algún cambio o quédate con el CV base."
    ),
    "tailor.bot.adapted_caption": (
        "✨ CV adaptado a esta oferta. Tu CV base sigue arriba y «🔄 Rehacer CV» "
        "lo vuelve a enviar."
    ),
}
