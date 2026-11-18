# cvgen — render machinery (v3 part B)

> LEGACY since fixed-base-cvs: the bot no longer renders masters. Delivery
> copies the owner's fixed PDFs via `base_pdfs.py` (`cv_base_pdfs/`). The
> engine, masters, plan and validators below are kept (unused by delivery);
> the claims validator and their tests still use them.

The render engine is `engine.py`: a faithful port of the OWNER's fpdf2
generator (`cvgen/generate.py`). The font family comes from the
`cv_font` setting through `fonts.py`: `carlito` (default; SIL OFL 1.1,
metric-compatible with Calibri, `cvgen/fonts/carlito/` with its `OFL.txt`)
or `calibri` (Microsoft's proprietary files `cvgen/fonts/calibri*.ttf`,
present only on machines that own them). Callers pass it as
`render(..., font=settings.cv_font)`; the demo PDFs built by
`scripts/build_demo_pdfs.py` always use Carlito. The
format is frozen (CAMBIOS_CVS.md section 2) — A4, margins 15.0/14.2/10.5mm,
`c_margin=0`, dates left-aligned at the x=141mm tab, links blue +
underlined, exact pt rhythm per element.

`templates/` (Jinja2 + WeasyPrint + Carlito) was REMOVED in this change:
WeasyPrint/Jinja2 are no longer dependencies, and `cv_template/` remains
on disk read-only as reference only — it takes no part in rendering.
