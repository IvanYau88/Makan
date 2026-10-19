# Makan visual assets

These assets come from the approved merged light mockup, `data/makan-merged-mockup/mockups/assets/`, reviewed on 2026-10-19.
The implementation source was supplied read-only at `/home/ivany/firstmate/data/makan-merged-mockup/` during the build.

- `makan-wordmark.svg` is the chosen row-2 `2-lighter-w370.svg` wordmark, copied unchanged from the merged mockup.
  Its outlined letters use Fraunces, covered by [OFL-Fraunces.txt](fonts/OFL-Fraunces.txt).
- `plate-c-87-compact.svg` and `plate-c-unrated-compact.svg` preserve the selected C ceramic rim with compact numerals.
  The numerals are outlined Inter, covered by [LICENSE-inter.txt](fonts/LICENSE-inter.txt).
  `Plate.tsx` uses the same rim geometry for arbitrary numeric scores and the unchanged unrated SVG when no score exists.
- `fonts/inter-opsz.woff2` is the mockup's variable Inter font, self-hosted for all interface text.
  Both font licences are retained in `fonts/`.

Illustrative restaurant placeholders are drawn in `styles.css`, following the mockup's tinted plate shapes.
They do not depict real venues or dishes.
The live map retains its configured tile source and attribution rather than copying the mockup's static map image.
