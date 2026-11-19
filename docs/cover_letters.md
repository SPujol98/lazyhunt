# Cover letters

The ✉️ button writes one letter per offer, in the offer's language (es, en, ca).

## PDF

Choosing a letter (✅ button) saves it on the offer and sends it as a PDF too, for portals that ask for the letter as a file; `/cartabase` does the same for the generic letter. One A4 page (a second one only if the text does not fit at 10 pt): your name and contact line from the identity claims, today's date in the letter's language, then the letter verbatim, in the CV font (`cv_font`). File: `<output>/<id>_<company>/<file_slug>_Carta_Presentacion.pdf` (`Cover_Letter` in English, `Carta_Presentacio` in Catalan), next to the offer's CV; the generic one goes to `<output>/base_letter_<lang>/`. If the PDF fails, the letter is still saved and the bot says why.

## Structure

The letter is the profile's **base letter** plus three generated parts:

1. Salutation (base) and paragraph 1: one generated linking sentence (at most 25 words), then the base's fixed paragraph.
2. Paragraph 2, generated: why this company (offer plus researched facts, 50-70 words).
3. Paragraph 3, generated: why this role (2-3 offer requirements tied to real claims, 50-80 words).
4. Closing and sign-off (base).

Total length: 250-350 words. Code assembles the letter; the model never sees the base as something to rewrite, and the reviewer pass (`cover_letter_review`) can only edit the three generated parts, so the base text always comes out byte-identical.

## The base letter (profile data)

`cover_letter_base.toml` in the profile directory (legacy owner layout: `data/cover_letter_base.toml`), one table per language `[es]`, `[en]`, `[ca]` (all three required):

| key | meaning |
| --- | --- |
| `salutation`, `salutation_generic` | template with `{company}` / `{company_de}` (`de X`, Catalan `d'X`), and the line used when the company is unknown |
| `salutation_formal`, `salutation_formal_generic` | optional, for corporate companies |
| `paragraph1` | fixed text that follows the linking sentence |
| `closing`, `closing_formal` | fixed closing (the formal one is optional) |
| `signoff` | sign-off lines, name included |
| `open_salutation`, `open_paragraph` | generic letter for portal profiles (`/cartabase [es|en|ca]`, alias `/base`): this salutation, `paragraph1` without the linking sentence, this fixed paragraph, `closing`, `signoff`; no model call |

A missing file or language fails loudly (no fallback to another person's text). The demo profile ships its own (`profiles/demo/cover_letter_base.toml`).

## Register

The model returns `tone: "formal" | "cercano"`. `formal` only for clearly corporate companies (bank, insurer, consultancy, multinational, public body); anything else reads as `cercano`. In Spanish, formal means the "Estimado equipo de selección de ..." salutation, "ustedes" in the generated parts and the formal closing; cercano means "Hola equipo de ...", "vosotros". The validator rejects the wrong pronoun for the chosen tone.

## Company research

The company is searched on the web through the same path `/prep` uses. Only facts with an http(s) URL are used. The result is cached per normalized company name for 30 days (`app_state` key `letter_company_facts:<name>`), so a company is researched once per 30 days; failed or unsupported searches are not cached. After the letter the bot sends the facts with their URLs ("Fuentes"), or says paragraph 2 rests only on the offer when nothing reliable was found.

## Checks

Banned phrases, dashes, language, sentence-length variance, triads and "no solo... sino" apply to the generated parts only (the base is approved text). Generic filler ("empresa líder", "cultura") is rejected. A failing set of parts is regenerated once; if it still fails the letter is sent with a warning.
