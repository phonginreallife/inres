# docs

## architecture.html

Interactive system diagram - pan/zoom, search, relationship tracing, light/dark,
and PNG/SVG export. Self-contained; open it directly in a browser.

`architecture.json` is its source. Edit that, then regenerate:

```bash
node ~/.claude/skills/archify/bin/archify.mjs deliver architecture \
  docs/architecture.json docs/architecture.html --quality showcase
```

Without the source checked in, changing one box means rebuilding the whole
diagram by hand.

Regeneration writes `architecture.visual-check.*` screenshots and a JSON
receipt beside the output. They are verification evidence, not artefacts -
`.gitignore` keeps them out of the repository.

## The site

These files are published as a Jekyll site at
<https://phonginreallife.github.io/inres/>, built and deployed by
[`.github/workflows/pages.yaml`](../.github/workflows/pages.yaml) on every push
to `main`.

### One-time setup

**Pages must be enabled by a repository admin before the first deploy can
succeed:**

> Settings → Pages → Build and deployment → **Source: GitHub Actions**

The workflow cannot do this for you. Creating a Pages site requires
`administration` permission on the repository, and that is not a scope
`GITHUB_TOKEN` can be granted - the workflow `permissions:` block has no such
key. `actions/configure-pages` with `enablement: true` therefore fails with
`Resource not accessible by integration`. Once the setting above is flipped the
workflow reads the existing configuration and deploys unattended.

### Where the content comes from

`docs/wiki/` is **generated** - do not edit it, and do not commit it.
[`scripts/build-docs.sh`](../scripts/build-docs.sh) derives it from `openwiki/`
at build time, adding the navigation front matter the theme needs and rewriting
`.md` links to `.html`. Keeping one copy in git means the wiki and the site
cannot drift, and because the workflow also triggers on `openwiki/**`, the
scheduled OpenWiki refresh redeploys the site on its own.

To change a wiki page, change the code it describes and let OpenWiki
regenerate. Hand edits are overwritten on the next build.

`index.md` (landing page) and `diagram.md` (embeds `architecture.html`) are
hand-written and live here directly.

### Building locally

```bash
./scripts/build-docs.sh          # generate docs/wiki from openwiki
cd docs && bundle install && bundle exec jekyll serve
```

Jekyll 4 needs Ruby >= 3.0. If the system Ruby is older, build in a container:

```bash
./scripts/build-docs.sh
docker run --rm -e LANG=C.UTF-8 -e LC_ALL=C.UTF-8 \
  -v "$PWD/docs":/srv/jekyll -w /srv/jekyll -p 4000:4000 ruby:3.3-alpine sh -c '
    apk add --no-cache build-base git openssl-dev zlib-dev &&
    bundle install && bundle exec jekyll serve --host 0.0.0.0 --baseurl ""'
```

`LANG`/`LC_ALL` matter: without a UTF-8 locale Jekyll reads em dashes and
typographic quotes as Latin-1 and the pages come out as mojibake. `--baseurl ""`
is for local preview only - the deployed site is served from `/inres`.
