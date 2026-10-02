#!/usr/bin/env python3
"""Draw the figures of ``docs/MODEL-TRAINING.md`` as standalone SVG files.

    python3 site/figures.py            # rewrites docs/img/training-*.svg

The figures are files, not inline markup, because the document has three
readers — a checkout, GitHub's markdown view and the site — and an ``<img>`` of
an SVG is the one form all three render. Each file carries its own light and
dark colours under ``prefers-color-scheme``, so it follows the reader's theme
without the page's stylesheet.

Every number drawn here is quoted, with its run and date, in the prose beside
the figure (root ``CLAUDE.md``, invariant 3). This script is the drawing, not
the source: change a number in the document first, then here, then re-run.
Stdlib only, like everything else in the tree.
"""

from __future__ import annotations

import html
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "docs" / "img"

FONT = 'ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif'
MONO = 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace'

# Site tokens (site/style.css) for diagrams; the dataviz reference palette's
# blue/red diverging pair for the two charts, each stepped for its surface.
STYLE = """
<style>
  .bg   { fill: #fbfbfa; }
  .box  { fill: #f2f1ee; stroke: #d9d6cf; stroke-width: 1; }
  .hot  { fill: #f7ebe3; stroke: #9a4a20; stroke-width: 1.2; }
  .lane { fill: none; stroke: #e2e0da; stroke-width: 1; stroke-dasharray: 3 4; }
  .rule { stroke: #e2e0da; stroke-width: 1; }
  .axis { stroke: #b9b5ad; stroke-width: 1; }
  .ref  { stroke: #9a4a20; stroke-width: 1.5; stroke-dasharray: 5 4; }
  .wire { stroke: #8d8981; stroke-width: 1.4; fill: none; }
  .head { fill: #8d8981; }
  .acc  { fill: #9a4a20; }
  .t    { fill: #1f1e1c; }
  .m    { fill: #6b6862; }
  .f    { fill: #8d8981; }
  .a    { fill: #9a4a20; }
  .s1   { fill: #2a78d6; }
  .s1l  { stroke: #2a78d6; stroke-width: 2; }
  .s2   { fill: #e34948; }
  .band1 { fill: #eef4fc; }
  .band2 { fill: #f7ebe3; }
  .band3 { fill: #edf6f1; }
  @media (prefers-color-scheme: dark) {
    .bg   { fill: #16151a; }
    .box  { fill: #1e1d23; stroke: #3a3842; }
    .hot  { fill: #2b201b; stroke: #e0885a; }
    .lane { stroke: #2e2c34; }
    .rule { stroke: #2e2c34; }
    .axis { stroke: #5a5662; }
    .ref  { stroke: #e0885a; }
    .wire { stroke: #837e75; }
    .head { fill: #837e75; }
    .acc  { fill: #e0885a; }
    .t    { fill: #e8e6e1; }
    .m    { fill: #a9a49b; }
    .f    { fill: #837e75; }
    .a    { fill: #e0885a; }
    .s1   { fill: #3987e5; }
    .s1l  { stroke: #3987e5; }
    .s2   { fill: #e66767; }
    .band1 { fill: #1b2230; }
    .band2 { fill: #2b201b; }
    .band3 { fill: #19251f; }
  }
  text { font-family: %s; }
  .mono { font-family: %s; }
</style>
""" % (FONT, MONO)


def esc(s: str) -> str:
    return html.escape(s, quote=True)


class Svg:
    def __init__(self, w: int, h: int, title: str, desc: str) -> None:
        self.w, self.h = w, h
        self.parts = [
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d" '
            'role="img" aria-labelledby="t d">' % (w, h, w, h),
            "<title id=\"t\">%s</title>" % esc(title),
            "<desc id=\"d\">%s</desc>" % esc(desc),
            STYLE.strip(),
            '<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
            'markerHeight="7" orient="auto-start-reverse"><path class="head" d="M0,0 L10,5 L0,10 z"/>'
            "</marker></defs>",
            '<rect class="bg" x="0" y="0" width="%d" height="%d" rx="10"/>' % (w, h),
        ]

    def add(self, s: str) -> None:
        self.parts.append(s)

    def text(self, x: float, y: float, s: str, cls: str = "t", size: float = 12,
             anchor: str = "start", weight: int = 400, mono: bool = False) -> None:
        c = cls + (" mono" if mono else "")
        self.add('<text class="%s" x="%.1f" y="%.1f" font-size="%s" text-anchor="%s"%s>%s</text>'
                 % (c, x, y, size, anchor, ' font-weight="%d"' % weight if weight != 400 else "", esc(s)))

    def box(self, x: float, y: float, w: float, h: float, cls: str = "box", rx: int = 8) -> None:
        self.add('<rect class="%s" x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="%d"/>'
                 % (cls, x, y, w, h, rx))

    def card(self, x: float, y: float, w: float, h: float, title: str, lines, cls: str = "box",
             step: str = "", center: bool = True, size: float = 11.5) -> None:
        self.box(x, y, w, h, cls)
        cx = x + w / 2 if center else x + 12
        anchor = "middle" if center else "start"
        ty = y + 22
        if step:
            self.text(cx, ty - 2, step, "a", 10.5, anchor, 700)
            ty += 15
        self.text(cx, ty, title, "t", 13, anchor, 700)
        for i, line in enumerate(lines):
            self.text(cx, ty + 19 + i * 16, line, "m", size, anchor)

    def arrow(self, pts, dashed: bool = False) -> None:
        d = "M" + " L".join("%.1f,%.1f" % p for p in pts)
        self.add('<path class="wire" d="%s" marker-end="url(#ah)"%s/>'
                 % (d, ' stroke-dasharray="4 4"' if dashed else ""))

    def line(self, x1, y1, x2, y2, cls="rule") -> None:
        self.add('<line class="%s" x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f"/>' % (cls, x1, y1, x2, y2))

    def save(self, name: str) -> Path:
        self.parts.append("</svg>")
        path = OUT / name
        path.write_text("\n".join(self.parts) + "\n", encoding="utf-8")
        return path


# --------------------------------------------------------------------------- 1

def loop() -> Svg:
    s = Svg(980, 330, "The data-to-training loop",
            "Seven stages: observe, snapshot, review, verify, train, evaluate, feed back. "
            "The last stage returns to the first through engine work and a new bundle.")
    W, G, X0, Y = 124, 16, 16, 64
    stages = [
        ("1", "Observe", ["capture hooks", "and PATH shim", "record, never rerun"]),
        ("2", "Snapshot", ["exact source bytes", "one SHA-256 each", "owner-only files"]),
        ("3", "Review", ["task, intent, rights", "lineage, tests", "executes nothing"]),
        ("4", "Verify", ["CPython twice,", "then lypning-l", "sealed bundle"]),
        ("5", "Train", ["SFT, LoRA r16", "probe, then GRPO", "only on signal"]),
        ("6", "Evaluate", ["dev selects", "test and eval-2", "paired, locked"]),
        ("7", "Feed back", ["refusals, bugs", "into engine work", "and next bundle"]),
    ]
    groups = [("DATA", 0, 2), ("VERIFY", 3, 3), ("LEARN", 4, 4), ("DECIDE", 5, 6)]
    for label, a, b in groups:
        x1 = X0 + a * (W + G) - 4
        x2 = X0 + b * (W + G) + W + 4
        s.add('<rect class="lane" x="%.1f" y="30" width="%.1f" height="186" rx="10"/>' % (x1, x2 - x1))
        s.text((x1 + x2) / 2, 48, label, "f", 10.5, "middle", 700)
    for i, (n, title, lines) in enumerate(stages):
        x = X0 + i * (W + G)
        s.card(x, Y, W, 138, title, lines, "hot" if title in ("Verify", "Train") else "box",
               step="STEP " + n)
        if i < len(stages) - 1:
            s.arrow([(x + W + 1, Y + 69), (x + W + G - 1, Y + 69)])
    # the return path
    last = X0 + 6 * (W + G) + W / 2
    first = X0 + W / 2
    s.arrow([(last, Y + 139), (last, 262), (first, 262), (first, Y + 141)], dashed=True)
    s.text(490, 256, "a new engine is a new arm: re-verify labels, build a new bundle,",
           "m", 12, "middle")
    s.text(490, 281, "never mutate a running experiment's tests, populations or split",
           "m", 12, "middle")
    s.text(16, 318, "Source: training/DATA_PRODUCTION.md, “One loop, distinct evidence and decisions”.",
           "f", 10.5)
    return s


# --------------------------------------------------------------------------- 2

def routes() -> Svg:
    s = Svg(980, 470, "Three routes into a training bank",
            "Authored, generate-and-adapt and capture-tier cases become schema-3 cases, are "
            "verified by training-prepare, and are split by connected component into train, "
            "dev and test, beside a separate eval-2 benchmark bank.")
    srcs = [
        ("Authored bank (v2)", ["two independent implementations", "must agree under execution",
                                "evidence: differential oracle"], "box"),
        ("Generate-and-adapt (v3)", ["the model proposes and answers k×,", "rules repair what L refuses",
                                     "evidence: self-consistency"], "box"),
        ("Capture tier", ["programs agents really ran,", "static quality gate, tier A only",
                          "its own bank, never merged"], "box"),
    ]
    for i, (t, lines, cls) in enumerate(srcs):
        y = 30 + i * 140
        s.card(16, y, 262, 112, t, lines, cls)
        s.arrow([(279, y + 56), (318, 220)])
    s.card(320, 128, 196, 184, "schema-3 case", [
        "task  ·  reference", "≥3 inputs, ≥2 outputs", "population: coverage", "or fallback-control",
        "source group  ·  capabilities", "review record"], "box")
    s.arrow([(517, 220), (556, 220)])
    s.card(558, 128, 200, 184, "training-prepare", [
        "CPython, run twice", "then the pinned lypning-l", "coverage: native on all", "control: refused on all",
        "a mismatch blocks", "→ sealed, hashed bundle"], "hot")
    s.arrow([(759, 220), (796, 220)])
    # splits
    s.text(880, 58, "split by connected", "f", 10.5, "middle", 700)
    s.text(880, 72, "component, per population", "f", 10.5, "middle", 700)
    for i, (t, sub) in enumerate([("train", "SFT targets, RL prompts"), ("dev", "checkpoint selection"),
                                  ("test", "one locked read")]):
        y = 86 + i * 72
        s.card(798, y, 166, 60, t, [sub], "box", size=11)
    s.add('<rect class="lane" x="790" y="40" width="182" height="272" rx="10"/>')
    s.card(558, 352, 406, 92, "eval-2 benchmark bank", [
        "capability-disjoint from training, pre-registered before a draw,",
        "scored at k = 16 against base on the same prompts"], "box")
    s.text(16, 458, "Sources: training/README.md “Bank v3”, training/DATA_PRODUCTION.md “Capture tier”, "
           "training/TRAINING.md “Data admission”, training/EVAL2.md.", "f", 10.5)
    return s


# --------------------------------------------------------------------------- 3

def funnel() -> Svg:
    rows = [
        ("Bash commands in the capture log", 7992),
        ("commands that carry a program", 4904),
        ("program occurrences", 5426),
        ("distinct programs", 5046),
        ("distinct, quality tier A", 204),
        ("tier A, uncontaminated: selected", 179),
    ]
    s = Svg(980, 300, "Capture yield, 2026-09-22",
            "Horizontal bars: 7,992 Bash commands, 4,904 with a program, 5,426 program "
            "occurrences, 5,046 distinct programs, 204 distinct tier A, 179 selected.")
    X, W, Y0, H, GAP = 268, 600, 34, 26, 12
    top = max(v for _, v in rows)
    for k in range(0, 9, 2):
        x = X + W * (k * 1000) / top
        if k * 1000 <= top:
            s.line(x, Y0 - 8, x, Y0 + len(rows) * (H + GAP) - GAP + 4, "rule")
            s.text(x, Y0 + len(rows) * (H + GAP) + 10, "{:,}".format(k * 1000), "f", 10.5, "middle")
    for i, (label, v) in enumerate(rows):
        y = Y0 + i * (H + GAP)
        w = max(3.0, W * v / top)
        s.text(X - 12, y + H / 2 + 4, label, "t", 12.5, "end")
        # 4px rounded data end, square at the baseline
        s.add('<path class="s1" d="M%.1f,%.1f h%.1f a4,4 0 0 1 4,4 v%.1f a4,4 0 0 1 -4,4 h-%.1f z"/>'
              % (X, y, w - 4, H - 8, w - 4))
        s.text(X + w + 8, y + H / 2 + 4, "{:,}".format(v), "t", 12.5, weight=700)
    s.text(16, 286, "Source: pipeline.capture_export over the live log (7,997 lines), measured "
           "2026-09-22; training/DATA_PRODUCTION.md “Measured yield”.", "f", 10.5)
    return s


# --------------------------------------------------------------------------- 4

def verdict() -> Svg:
    s = Svg(980, 430, "How one draw is graded",
            "Correctness on CPython is checked first; only a correct draw is run on lypning-l. "
            "Native and identical scores 1; a valid refusal scores 0.25 on coverage and 1 on "
            "control; a disagreement scores 0 and is kept as an engine witness.")
    s.card(16, 150, 180, 92, "one draw", ["the model's program,", "every test input"], "box")
    s.arrow([(197, 196), (236, 196)])
    s.card(238, 138, 216, 116, "1 · CPython first", ["right stdout on every input,", "empty stderr, exit 0,",
                                                    "same verdict twice?"], "hot")
    # failure branches from step 1
    s.arrow([(346, 137), (346, 92)])
    s.card(238, 18, 216, 72, "wrong → reward 0", ["incorrect, exception, timeout,", "no code, missing EOS"], "box",
           size=11)
    s.arrow([(346, 255), (346, 306)])
    s.card(238, 308, 216, 72, "unstable → abort", ["an unstable oracle or harness", "failure stops the run"], "box",
           size=11)
    s.arrow([(455, 196), (494, 196)])
    s.card(496, 138, 196, 116, "2 · then lypning-l", ["the same inputs,", "the engine the arm", "is pinned to, by hash"], "hot")
    outs = [
        ("native, identical", "coverage 1  ·  control 1", 18),
        ("valid refusal (exit 90)", "coverage 0.25  ·  control 1", 162),
        ("runs, disagrees", "0, kept as an engine witness;", 306),
    ]
    for title, sub, y in outs:
        s.arrow([(693, 196), (730, y + 36)])
        lines = [sub] + (["abort past 1% of a run's draws"] if title.startswith("runs") else [])
        s.card(732, y, 232, 72 if len(lines) == 1 else 88, title, lines, "box", size=11)
    s.text(16, 418, "Source: training/TRAINING.md “Verification and learning”. No legality-only bonus, "
           "syntax credit, import penalty or length reward.", "f", 10.5)
    return s


# --------------------------------------------------------------------------- 5

def stages() -> Svg:
    s = Svg(980, 300, "Training stages and the gate in front of each",
            "Base control, then supervised fine-tuning with LoRA, then a train-only probe that "
            "admits GRPO, then post-hoc checkpoint selection on dev, then one locked evaluation.")
    W, G, X0, Y = 141, 20, 16, 40
    cards = [
        ("Base", ["Qwen3.8-27B, pinned", "thinking off", "the control arm"], "box"),
        ("SFT", ["LoRA r16 α32, BF16", "assistant tokens + EOS", "family, then case"], "hot"),
        ("Probe", ["every train case × 4", "train split only", "admits RL or not"], "box"),
        ("GRPO", ["Dr.GRPO, LR 5e-6", "4 generations/prompt", "β = 0, truncs masked"], "hot"),
        ("Select", ["on dev, post hoc", "correct-and-native", "never stops a dose"], "box"),
        ("Read", ["test and eval-2", "paired with base", "k = 16 on eval-2"], "box"),
    ]
    gates = ["same decoding", "≥1,000 cases", "≥2 groups with", "every checkpoint", "gate A: −2pp", "lower bound"]
    gates2 = ["for every arm", "≥50k sup. tokens", "distinct rewards", "saved, step 0 too", "and retention", "above +3pp"]
    for i, (t, lines, cls) in enumerate(cards):
        x = X0 + i * (W + G)
        s.card(x, Y, W, 118, t, lines, cls)
        if i < len(cards) - 1:
            s.arrow([(x + W + 1, Y + 59), (x + W + G - 1, Y + 59)])
        s.text(x + W / 2, Y + 148, "GATE", "a", 10, "middle", 700)
        s.text(x + W / 2, Y + 166, gates[i], "m", 11.5, "middle")
        s.text(x + W / 2, Y + 182, gates2[i], "m", 11.5, "middle")
    s.line(16, 248, 964, 248, "rule")
    s.text(16, 268, "Decoding everywhere: temperature 0.7 · top-p 0.8 · top-k 20 · min-p 0 · presence "
           "penalty 0 · one beam · thinking disabled", "m", 11.5)
    s.text(16, 288, "Source: training/TRAINING.md, training/STATUS.md §10 (floors enforced by "
           "train_verified.preflight and run()).", "f", 10.5)
    return s


# --------------------------------------------------------------------------- 6

def delta() -> Svg:
    s = Svg(980, 250, "Seed 1111: change in correct-and-native, SFT minus base",
            "Eval-2: +5.30 percentage points, 95% interval +2.22 to +8.88. Test: +3.41, interval "
            "-1.35 to +9.68. The pre-registered bar is a lower bound above +3; neither clears it.")
    X0, X1, lo, hi = 150, 900, -4.0, 12.0
    def px(v: float) -> float:
        return X0 + (v - lo) / (hi - lo) * (X1 - X0)
    top, bot = 46, 168
    for v in range(-4, 13, 2):
        s.line(px(v), top - 6, px(v), bot, "rule")
        s.text(px(v), bot + 18, ("%+d" % v if v else "0") + " pp", "f", 10.5, "middle")
    s.line(px(0), top - 6, px(0), bot, "axis")
    s.add('<line class="ref" x1="%.1f" y1="%d" x2="%.1f" y2="%d"/>' % (px(3), top - 14, px(3), bot))
    s.text(px(3) + 6, top - 18, "pre-registered bar: lower bound above +3pp", "a", 11, weight=700)
    rows = [("eval-2", "803 cases · k = 16", 5.30, 2.22, 8.88),
            ("test", "315 cases · k = 4", 3.41, -1.35, 9.68)]
    for i, (name, sub, est, a, b) in enumerate(rows):
        y = 78 + i * 56
        s.text(X0 - 14, y + 1, name, "t", 13, "end", 700)
        s.text(X0 - 14, y + 17, sub, "m", 11, "end")
        s.add('<line class="s1l" x1="%.1f" y1="%d" x2="%.1f" y2="%d"/>' % (px(a), y, px(b), y))
        for e in (a, b):
            s.add('<line class="s1l" x1="%.1f" y1="%d" x2="%.1f" y2="%d"/>' % (px(e), y - 6, px(e), y + 6))
        s.add('<circle class="s1" cx="%.1f" cy="%d" r="6"/>'
              % (px(est), y))
        s.text(px(b) + 10, y + 4, "%+.2f  [%+.2f, %+.2f]" % (est, a, b), "t", 12, weight=700)
    s.text(16, 236, "HF job 6ab7b0b76b030d633f693e48, read 2026-09-26; paired family-component bootstrap, "
           "2,000 resamples; one seed, one replicate.", "f", 10.5)
    return s


def statuses() -> Svg:
    rows = [("correct-native", 8758, 9296), ("correct-fallback", 917, 350),
            ("correct-control", 1701, 1672), ("incorrect", 1423, 1508), ("no-code", 49, 22)]
    s = Svg(980, 280, "Seed 1111 on eval-2: where the draws moved",
            "Change in eval-2 draws per status, SFT minus base, 12,848 draws per arm: correct-native "
            "+538, correct-fallback -567, correct-control -29, incorrect +85, no-code -27.")
    X0, X1, lim = 380, 940, 600
    mid = (X0 + X1) / 2
    def px(v: float) -> float:
        return mid + v / lim * (X1 - X0) / 2
    top, H, GAP = 40, 24, 14
    bot = top + len(rows) * (H + GAP) - GAP
    for v in range(-600, 601, 200):
        s.line(px(v), top - 8, px(v), bot + 4, "rule")
        s.text(px(v), bot + 20, "%+d" % v if v else "0", "f", 10.5, "middle")
    s.line(mid, top - 8, mid, bot + 4, "axis")
    for i, (name, base, sft) in enumerate(rows):
        y = top + i * (H + GAP)
        d = sft - base
        s.text(190, y + H / 2 + 4, name, "t", 12.5, "end", 700)
        s.text(330, y + H / 2 + 4, "{:,} → {:,}".format(base, sft), "m", 11.5, "end")
        w = abs(px(d) - mid)
        if d > 0:
            s.add('<path class="s1" d="M%.1f,%.1f h%.1f a4,4 0 0 1 4,4 v%.1f a4,4 0 0 1 -4,4 h-%.1f z"/>'
                  % (mid, y, w - 4, H - 8, w - 4))
            s.text(mid + w + 8, y + H / 2 + 4, "%+d" % d, "t", 12, weight=700)
        else:
            s.add('<path class="s2" d="M%.1f,%.1f h-%.1f a4,4 0 0 0 -4,4 v%.1f a4,4 0 0 0 4,4 h%.1f z"/>'
                  % (mid, y, w - 4, H - 8, w - 4))
            s.text(mid - w - 8, y + H / 2 + 4, "%+d" % d, "t", 12, "end", 700)
    # legend
    s.add('<rect class="s2" x="560" y="12" width="12" height="12" rx="3"/>')
    s.text(578, 22, "fewer draws under SFT", "m", 11.5)
    s.add('<rect class="s1" x="738" y="12" width="12" height="12" rx="3"/>')
    s.text(756, 22, "more draws under SFT", "m", 11.5)
    s.text(16, 266, "HF job 6ab7b0b76b030d633f693e48, read 2026-09-26; eval-2, 803 cases, 12,848 draws per arm; "
           "training/reports/2026-09-26-seed1111-finish-read.md §2.", "f", 10.5)
    return s


# --------------------------------------------------------------------------- 7

def timeline() -> Svg:
    events = [
        ("09-11", "Target switched to Qwen/Qwen3.8-27B (dense, 55.6 GB BF16)", 1),
        ("09-13", "Baseline of record on eval-1: first-draft pass@1 0.4257, k = 16", 1),
        ("09-14", "Two matched LoRA rounds, both null on ΔSLR (−1.00pp, −0.21pp)", 1),
        ("09-15", "Eval-2 pre-registered before any draw; round-02 plumbing smoke on HF", 2),
        ("09-16", "First 27B pilot: eval-2 base arm blocked at 384 / 1,200 draws", 2),
        ("09-18", "Bank v2 admits 1,120 training cases on the split the gates use", 2),
        ("09-19", "A missing User-Agent fixed: 7,967 generated candidates in one run", 2),
        ("09-21", "Seed 1111 on bank v3: both stages select step 0, the selector is blind", 3),
        ("09-22", "The instrument fixed: selection, macro scope, LoRA recipe (PRs #97–#101)", 3),
        ("09-23", "Positive control flat: no distillation route; too few pairs for a preference arm", 3),
        ("09-25", "Seed 1111 finish runs out of CUDA memory in the eval-2 prefill", 3),
        ("09-26", "Seed 1111 finish completes: eval-2 +5.30pp; training paused for the engine", 3),
    ]
    row = 30
    s = Svg(980, 40 + row * (len(events) - 1) + 56, "The training programme, 2026-09-11 to 2026-09-26",
            "Twelve dated milestones in three phases: first rounds on the rewrite benchmark, "
            "instrument and data, and round-02 on bank v3.")
    phases = {1: ("FIRST ROUNDS", "band1"), 2: ("INSTRUMENT AND DATA", "band2"), 3: ("ROUND-02 ON BANK V3", "band3")}
    y0 = 40
    for p, (name, band) in phases.items():
        idx = [i for i, e in enumerate(events) if e[2] == p]
        ya = y0 + idx[0] * row - 12
        yb = y0 + idx[-1] * row + 14
        s.add('<rect class="%s" x="16" y="%.1f" width="948" height="%.1f" rx="8"/>' % (band, ya, yb - ya))
        s.text(950, ya + 16, name, "f", 10, "end", 700)
    s.line(110, y0 - 8, 110, y0 + (len(events) - 1) * row + 8, "axis")
    for i, (date, label, _p) in enumerate(events):
        y = y0 + i * row
        s.text(96, y + 4, "2026-" + date, "m", 11.5, "end", mono=True)
        last = i == len(events) - 1
        s.add('<circle class="%s" cx="110" cy="%d" r="%d"/>' % ("acc" if last else "head", y, 6 if last else 4))
        s.text(126, y + 4, label, "t", 12.5, weight=700 if last else 400)
    s.text(16, s.h - 14, "Sources: training/STATUS.md §0 and §2, training/PLAN.md, training/START_NEXT_ROUND.md, "
           "CHANGELOG.md. Each number's run id is in the prose.", "f", 10.5)
    return s


FIGURES = {
    "training-loop.svg": loop,
    "training-routes.svg": routes,
    "training-capture.svg": funnel,
    "training-verdict.svg": verdict,
    "training-stages.svg": stages,
    "training-delta.svg": delta,
    "training-statuses.svg": statuses,
    "training-timeline.svg": timeline,
}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, make in FIGURES.items():
        print(make().save(name).relative_to(OUT.parent.parent))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
