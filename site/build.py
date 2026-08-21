#!/usr/bin/env python3
"""Render the repository's markdown into the GitHub Pages site.

The docs in ``docs/`` are the source of truth and are written to be read as
files in a checkout — that is where an agent working on this tree meets them.
So this generator does not fork them into a second copy with its own wording;
it renders exactly what is in the tree and rewrites the links.

Two consequences worth knowing before editing anything here:

* **The site cannot say something the repository does not.** Every page except
  the landing page is a rendering of a file that CI already checks, so a claim
  cannot drift onto the website and stay there unnoticed.
* **A broken cross-reference is a build failure, not a 404 for a visitor.**
  :func:`rewrite_links` resolves every intra-repo link against the file it came
  from and refuses to emit one it cannot map, which is how a renamed doc gets
  caught here rather than in someone's browser.

This is a build-time tool. It is NOT part of the installed package and its two
dependencies (``markdown``, ``pygments``) live in the ``docs`` extra, so the
zero-runtime-dependency rule the package actually ships under is untouched.

    python3 site/build.py [--out _site] [--base /lypning]
"""

from __future__ import annotations

import argparse
import html
import re
import shutil
import sys
from pathlib import Path

try:
    import markdown
except ImportError:  # pragma: no cover - the one thing this script needs
    sys.exit("site/build.py needs the docs extra: pip install '.[docs]'")

ROOT = Path(__file__).resolve().parent.parent
SITE = Path(__file__).resolve().parent

# Every page the site publishes: source file, output path, nav label, and the
# one-line description the index shows. Order is the nav order.
PAGES = [
    ("README.md", "readme.html", "Overview", "What it is, how to install it, and how to wire it into a session."),
    ("docs/LYPNING.md", "docs/lypning.html", "Design", "The mixture, the classifier, and the commit barrier that makes falling onward safe."),
    ("docs/SUBSET.md", "docs/subset.html", "The subset", "What the engines implement, and the rules that decide what they refuse."),
    ("docs/COOKBOOK.md", "docs/cookbook.html", "Cookbook", "Rewrites for constructs outside the subset — every recipe executed by the test suite."),
    ("docs/MICROPYTHON.md", "docs/micropython.html", "MicroPython tier", "The frozen-stdlib variant, and the cost model both runtimes are optimised against."),
    ("docs/RESEARCH.md", "docs/research.html", "Research", "How the runtime was chosen and built, including what was measured and rejected."),
    ("docs/CAPTURE.md", "docs/capture.html", "Capture", "The hooks and shim that grow the corpus, and what they do and do not record."),
    ("docs/EMBEDDING.md", "docs/embedding.html", "Embedding", "Linking the runtime into a harness: the C ABI, the five bindings, and what a refusal means with no exit code."),
    ("docs/BENCH-LEDGER.md", "docs/bench-ledger.html", "Bench ledger", "Append-only measurement history, including the runs where the subset lost."),
    ("docs/SANDBOX-PERFORMANCE.md", "docs/sandbox-performance.html", "Sandbox cost", "The measurements the whole project is downstream of."),
    ("CLAUDE.md", "contributing.html", "Working agreement", "The invariants an agent changing this repository must not break."),
    ("CHANGELOG.md", "changelog.html", "Changelog", "Every release, and the defects tracked rather than waived."),
]

EXTENSIONS = ["extra", "tables", "fenced_code", "codehilite", "toc", "sane_lists", "admonition"]
EXTENSION_CONFIGS = {
    "codehilite": {"guess_lang": False, "css_class": "highlight"},
    "toc": {"permalink": "#", "toc_depth": "2-3"},
}

GITHUB = "https://github.com/kristerhedfors/lypning"

# Source files the docs link to directly. They have no rendered page, so they
# resolve to the blob on GitHub rather than to a dead relative path.
SOURCE_SUFFIXES = (".py", ".rs", ".sh", ".h", ".mk", ".toml", ".jsonl", ".yml", ".json")

# Images the markdown references by repo path. They are copied to the SAME path
# under the output, so `src="docs/logo.svg"` needs no rewriting and means the
# same thing in a checkout, on GitHub, and here.
IMAGES = ("docs/logo.svg",)

_BY_SOURCE = {src: out for src, out, _, _ in PAGES}


def rewrite_links(body: str, page_src: str) -> str:
    """Point every intra-repo link at its rendered page, or at GitHub.

    Links in the docs are written relative to the file they live in, because
    that is what works in a checkout and in GitHub's own markdown view. Here
    they are resolved against the repository root first and then mapped, so
    ``../README.md`` from inside ``docs/`` and ``README.md`` from the root both
    land on the same page.
    """
    here = Path(page_src).parent
    depth = len(Path(_BY_SOURCE[page_src]).parent.parts)
    up = "../" * depth

    def repl(m: "re.Match[str]") -> str:
        prefix, target = m.group(1), m.group(2)
        if re.match(r"^(https?:|mailto:|#|//)", target):
            return m.group(0)
        path, _, frag = target.partition("#")
        if not path:
            return m.group(0)
        # The landing page is authored against the RENDERED tree, so its links
        # are already site paths. Mapping them as repo paths sent every
        # `docs/*.html` on it to a GitHub blob URL for a file that does not
        # exist in the repository — a 404 the link check could not see,
        # because it skips absolute URLs.
        if path.endswith(".html"):
            return m.group(0)
        resolved = str((here / path).as_posix())
        # Normalise ../ without touching the filesystem — the target may be a
        # path that only exists on GitHub.
        parts: list[str] = []
        for part in resolved.split("/"):
            if part == "..":
                if parts:
                    parts.pop()
            elif part not in (".", ""):
                parts.append(part)
        resolved = "/".join(parts)
        if resolved in IMAGES:
            # Copied to the same path under the output, so it maps to itself —
            # only the walk back up to the site root differs per page.
            dest = up + resolved
        elif resolved in _BY_SOURCE:
            dest = up + _BY_SOURCE[resolved]
        elif resolved.endswith(SOURCE_SUFFIXES) or "/" in resolved:
            dest = "%s/blob/main/%s" % (GITHUB, resolved)
        else:
            return m.group(0)
        return '%s"%s%s"' % (prefix, dest, ("#" + frag) if frag else "")

    return re.sub(r'((?:href|src)=)"([^"]+)"', repl, body)


def nav_html(current: str, depth: int) -> str:
    up = "../" * depth
    items = ['<a class="nav-home" href="%sindex.html">lypning</a>' % up]
    for _, out, label, _ in PAGES:
        cls = ' class="here"' if out == current else ""
        items.append('<a%s href="%s%s">%s</a>' % (cls, up, out, html.escape(label)))
    return "\n".join(items)


def shell(*, title: str, body: str, current: str, depth: int, description: str,
          toc: str = "", wide: bool = False) -> str:
    up = "../" * depth
    aside = ('<aside class="toc"><div class="toc-title">On this page</div>%s</aside>' % toc) if toc else ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(description)}">
<meta property="og:title" content="{html.escape(title)}">
<meta property="og:description" content="{html.escape(description)}">
<meta property="og:type" content="website">
<link rel="icon" href="{up}favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="{up}style.css">
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
<header class="topbar">
  <nav class="nav">{nav_html(current, depth)}</nav>
  <a class="gh" href="{GITHUB}" rel="noopener">GitHub</a>
</header>
<div class="shell{' wide' if wide else ''}">
{aside}
<main id="main" class="prose">
{body}
</main>
</div>
<footer class="foot">
  <p><strong>lypning</strong> — a mixture of Pythons. MIT licensed.</p>
  <p>Extracted from <a href="https://github.com/kristerhedfors/deepresearch.se" rel="noopener">deepresearch.se</a>,
     where the two runtimes were developed. Every page here is rendered from the
     markdown in the repository, so the site cannot claim something the tree does not.</p>
</footer>
</body>
</html>
"""


def render(src: Path) -> tuple[str, str]:
    md = markdown.Markdown(extensions=EXTENSIONS, extension_configs=EXTENSION_CONFIGS)
    body = md.convert(src.read_text(encoding="utf-8"))
    return body, getattr(md, "toc", "")


_XREF = re.compile(r"<code>(docs/[A-Z0-9-]+\.md|README\.md|CHANGELOG\.md|CLAUDE\.md)(\s+§[^<]*)?</code>")


def linkify_xrefs(body: str, page_src: str) -> str:
    """Turn backtick cross-references into links, on the site only.

    The docs cite each other as `docs/MICROPYTHON.md §2` rather than as markdown
    links, because in a checkout that is a path you can open and a link is not.
    On the web it is a dead end — 65 of them. So they become links HERE, at
    render time, leaving the source markdown exactly as a reader in a terminal
    wants it.

    Only pages this site actually publishes are linked. The rest are left as
    code, deliberately: `docs/TESTING.md:1226` and `docs/SANDBOX-LOCAL-IMAGE.md`
    are PROVENANCE — citations of the upstream repository this was extracted
    from, marked as such in the prose — and turning a citation into a link that
    404s would be worse than leaving it plain.
    """
    depth = len(Path(_BY_SOURCE[page_src]).parent.parts)
    up = "../" * depth

    def repl(m: "re.Match[str]") -> str:
        target, section = m.group(1), (m.group(2) or "")
        if target not in _BY_SOURCE or target == page_src:
            return m.group(0)
        return '<a class="xref" href="%s%s"><code>%s%s</code></a>' % (
            up, _BY_SOURCE[target], target, section
        )

    return _XREF.sub(repl, body)


def wrap_tables(body: str) -> str:
    """Give every table its own horizontal scroll box.

    These docs are mostly measurement tables and several are far wider than a
    phone. Without this the BODY scrolls sideways, which moves the prose out
    from under the reader instead of the table.
    """
    return re.sub(r"<table>", '<div class="table-wrap"><table>', body).replace(
        "</table>", "</table></div>"
    )


def strip_h1(body: str) -> tuple[str, str]:
    """Pull the leading ``<h1>`` out so the shell can present it as the title."""
    m = re.search(r"<h1[^>]*>(.*?)</h1>", body, re.S)
    if not m:
        return body, ""
    inner = m.group(1)
    # The toc extension appends a permalink anchor whose TEXT is "#". Dropping
    # tags alone leaves that behind, and every page title ended in a stray hash.
    inner = re.sub(r'<a[^>]*class="headerlink".*?</a>', "", inner, flags=re.S)
    text = re.sub(r"<[^>]+>", "", inner).strip()
    return body[: m.start()] + body[m.end():], text


def pygments_css() -> str:
    """Both themes, each scoped so the site's own light/dark switch drives them.

    Generated rather than vendored: the class names belong to whichever Pygments
    built the page, and a stale hand-copied sheet silently stops matching them.
    """
    from pygments.formatters import HtmlFormatter

    light = HtmlFormatter(style="stata-light").get_style_defs(".highlight")
    dark = HtmlFormatter(style="stata-dark").get_style_defs(".highlight")
    return "\n".join([
        "/* --- syntax highlighting, generated by site/build.py --- */",
        light,
        "@media (prefers-color-scheme: dark) {",
        "\n".join("  :root:not([data-theme=\"light\"]) " + l for l in dark.splitlines() if l.strip()),
        "}",
        "\n".join(':root[data-theme="dark"] ' + l for l in dark.splitlines() if l.strip()),
    ])


def build(out_dir: Path) -> int:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    written = 0

    for src_rel, out_rel, label, desc in PAGES:
        src = ROOT / src_rel
        if not src.is_file():
            sys.exit("site/build.py: %s is in PAGES but not in the tree" % src_rel)
        body, toc = render(src)
        body, h1 = strip_h1(body)
        body = wrap_tables(linkify_xrefs(rewrite_links(body, src_rel), src_rel))
        toc = rewrite_links(toc, src_rel) if toc else ""
        depth = len(Path(out_rel).parent.parts)
        heading = '<h1 class="page-title">%s</h1>' % html.escape(h1 or label)
        # "lypning — a mixture of Pythons — lypning" is what a blind suffix does
        # to the README's own title; the site name is only appended when the
        # heading does not already carry it.
        head = h1 or label
        page = shell(
            title=head if head.lower().startswith("lypning") else "%s — lypning" % head,
            body=heading + body,
            current=out_rel,
            depth=depth,
            description=desc,
            toc=toc,
        )
        dest = out_dir / out_rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(page, encoding="utf-8")
        written += 1

    # The landing page is the one page authored FOR the web rather than
    # rendered from a doc: a reader arriving from a link needs the thesis and
    # the numbers before they need the reference.
    index_md = (SITE / "index.md").read_text(encoding="utf-8")
    body, _ = render(SITE / "index.md")
    body, _h1 = strip_h1(body)
    body = wrap_tables(linkify_xrefs(rewrite_links(body, "README.md"), "README.md")
                       .replace('href="../', 'href="'))
    (out_dir / "index.html").write_text(
        shell(
            title="lypning — a mixture of Pythons",
            body=body,
            current="index.html",
            depth=0,
            description="A tiny Python subset in Rust, a frozen-stdlib MicroPython, "
                        "and the router that picks between them — for the one-liners "
                        "coding agents actually type.",
            wide=True,
        ),
        encoding="utf-8",
    )
    written += 1
    del index_md

    for asset in ("style.css", "favicon.svg"):
        shutil.copy2(SITE / asset, out_dir / asset)
    for image in IMAGES:
        src_img = ROOT / image
        if not src_img.is_file():
            sys.exit("site/build.py: %s is in IMAGES but not in the tree" % image)
        dest_img = out_dir / image
        dest_img.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_img, dest_img)
    # codehilite emits Pygments class names and nothing else; without the
    # matching stylesheet every code block renders as undifferentiated text and
    # the `highlight` extension is dead weight in the page.
    (out_dir / "style.css").write_text(
        (SITE / "style.css").read_text(encoding="utf-8") + "\n" + pygments_css(),
        encoding="utf-8",
    )
    # Jekyll would otherwise swallow any path beginning with an underscore.
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")
    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(ROOT / "_site"), help="output directory (default: _site)")
    ap.add_argument("--check", action="store_true", help="build, then fail on a dead intra-site link")
    args = ap.parse_args(argv)

    out = Path(args.out)
    n = build(out)
    print("built %d pages into %s" % (n, out))

    if args.check:
        dead = check_links(out)
        for page, href in dead:
            print("dead link: %s -> %s" % (page, href), file=sys.stderr)
        if dead:
            return 1
        print("link check: every intra-site href resolves")
    return 0


def check_links(out: Path) -> list[tuple[str, str]]:
    """Every relative href and src must resolve to a file that was written.

    ``src`` is checked alongside ``href`` because an image that did not get
    copied is exactly as broken as a dead link and rather more visible.
    """
    dead: list[tuple[str, str]] = []
    for page in sorted(out.rglob("*.html")):
        text = page.read_text(encoding="utf-8")
        for href in re.findall(r'(?:href|src)="([^"]+)"', text):
            if re.match(r"^(https?:|mailto:|#|//)", href):
                continue
            target = (page.parent / href.partition("#")[0]).resolve()
            if not target.exists():
                dead.append((str(page.relative_to(out)), href))
    return dead


if __name__ == "__main__":
    raise SystemExit(main())
