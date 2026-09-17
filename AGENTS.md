<!-- OPENWIKI:START -->

## OpenWiki

This repository has a generated `openwiki/` evidence index. It is optional just-in-time context, not required startup reading.

- Treat source code and tests as authoritative. A brief's unknowns and review items are verification gaps, not automatic requirements.
- Prefer the narrowest quiet validation that proves the changed behavior. Preserve complete failure output.

The scheduled OpenWiki GitHub Actions workflow refreshes the repository wiki. Do not hand-edit generated OpenWiki pages unless explicitly asked; prefer updating source code/docs and letting OpenWiki regenerate.

<!-- OPENWIKI:END -->

## Writing conventions

Use ASCII punctuation in documentation and code comments. No em dashes, en
dashes or ellipsis characters - write `-` and `...` instead. The Go and Python
sources already follow this; documentation should match.

This applies to generated pages too. `scripts/build-docs.sh` enforces it when
building the docs site, but the `openwiki/` sources should not need fixing up.
