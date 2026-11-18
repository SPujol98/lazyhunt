# Provenance

Until version 1.x, this extension was built on a snapshot of a third-party
extension, `github.com/Edugs94/linkedin_beacons`, which carries no license.
That code could be used privately but not redistributed, so version 2.0.0
replaced it with a clean-room rewrite (feature `extension-rewrite`, November 2026):

1. A behaviour specification of the old extension, written in our own words
   (`docs/extension-spec.md`), plus characterization tests that drive the
   extension only through Chrome events, runtime messages and page clicks
   (`tests/test_extension_characterization.py`).
2. New code for every file that contained third-party lines: the service
   worker (`background.js` and `core/`), the LinkedIn extractor
   (`portals/linkedin.js`), the popup, the Options page, the shared styles,
   the manifest and the icons. The people who wrote it worked from the
   specification and the tests only, without reading the old files.
3. The files that never contained third-party code were kept: `filter.js`,
   `searches.js`, `scan_report.js`, `slug.js`, `portals/registry.js`,
   `portals/terms.js`, `portals/detail.js` and the other portal adapters.

Contracts kept for compatibility with existing installs are names and data
shapes, not code: the `chrome.storage.local` keys, the runtime message types,
the alarm name and the export file `format` string.

The last version containing the old snapshot is the git tag
`checkpoint/pre-extension-rewrite`.
