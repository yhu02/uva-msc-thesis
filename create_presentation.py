#!/usr/bin/env python3
"""Generate ChaosProbe thesis defense PowerPoint presentation.

Slide structure follows standard thesis defense format:
  1. Title
  2. Background & Motivation
  3. Research Question & Hypotheses (H1–H6)
  4. Related Work
  5. Placement Strategies (independent variable)
  6. Experimental Setup
  7. ChaosProbe Framework (architecture + lifecycle)
  8. Measurement Design (probers + probes + scoring)
  9. Results — H1: the score cannot rank placements
 10. Results — H2: a kernel reconvergence signature moves
 11. Results — H3: the mechanism does not reach the user
 12. Results — H4 & H5: load contention + a graph predictor
 13. Results — H6: the latency/availability trade-off
 14. Negative Findings & Literature Predictions
 15. Threats to Validity
 16. Conclusion & Future Work
 17. Questions
"""

import glob
import os

from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.enum.shapes import MSO_SHAPE

# ── Colour palette ─────────────────────────────────────────────────
DARK_BG      = RGBColor(0x1B, 0x1B, 0x2F)
ACCENT_BLUE  = RGBColor(0x00, 0x96, 0xD6)
ACCENT_GREEN = RGBColor(0x2E, 0xCC, 0x71)
ACCENT_RED   = RGBColor(0xE7, 0x4C, 0x3C)
ACCENT_ORANGE= RGBColor(0xF3, 0x9C, 0x12)
ACCENT_PURPLE= RGBColor(0x9B, 0x59, 0xB6)
WHITE         = RGBColor(0xFF, 0xFF, 0xFF)
LIGHT_GRAY   = RGBColor(0xBD, 0xBD, 0xBD)
MID_GRAY     = RGBColor(0x90, 0x90, 0xA0)
VERY_DARK    = RGBColor(0x12, 0x12, 0x22)
TRANS_WHITE   = RGBColor(0xF0, 0xF0, 0xF8)

CLR_CLI       = RGBColor(0x34, 0x98, 0xDB)
CLR_ORCH      = RGBColor(0x1A, 0xBC, 0x9C)
CLR_CHAOS     = RGBColor(0xE7, 0x4C, 0x3C)
CLR_METRICS   = RGBColor(0x2E, 0xCC, 0x71)
CLR_STORAGE   = RGBColor(0x9B, 0x59, 0xB6)
CLR_OUTPUT    = RGBColor(0xF3, 0x9C, 0x12)
CLR_INFRA     = RGBColor(0x7F, 0x8C, 0x8D)

prs = Presentation()
prs.slide_width  = Inches(13.333)
prs.slide_height = Inches(7.5)


# ── Helper functions ────────────────────────────────────────────────
def set_slide_bg(slide, color=DARK_BG):
    bg = slide.background
    fill = bg.fill
    fill.solid()
    fill.fore_color.rgb = color


def add_text_box(slide, left, top, width, height, text, font_size=14,
                 bold=False, color=WHITE, alignment=PP_ALIGN.LEFT,
                 font_name="Calibri"):
    txBox = slide.shapes.add_textbox(Inches(left), Inches(top),
                                     Inches(width), Inches(height))
    tf = txBox.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = text
    p.font.size = Pt(font_size)
    p.font.bold = bold
    p.font.color.rgb = color
    p.font.name = font_name
    p.alignment = alignment
    return txBox


def add_rounded_box(slide, left, top, width, height, fill_color,
                    text="", font_size=11, text_color=WHITE, bold=False,
                    border_color=None, border_width=Pt(1)):
    shape = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE,
        Inches(left), Inches(top), Inches(width), Inches(height)
    )
    shape.fill.solid()
    shape.fill.fore_color.rgb = fill_color
    if border_color:
        shape.line.color.rgb = border_color
        shape.line.width = border_width
    else:
        shape.line.fill.background()
    if text:
        tf = shape.text_frame
        tf.word_wrap = True
        tf.paragraphs[0].alignment = PP_ALIGN.CENTER
        p = tf.paragraphs[0]
        p.text = text
        p.font.size = Pt(font_size)
        p.font.color.rgb = text_color
        p.font.bold = bold
        p.font.name = "Calibri"
        tf.margin_left = Pt(4)
        tf.margin_right = Pt(4)
        tf.margin_top = Pt(2)
        tf.margin_bottom = Pt(2)
    return shape


def add_arrow(slide, x1, y1, x2, y2, color=LIGHT_GRAY, width=Pt(2)):
    connector = slide.shapes.add_connector(
        1, Inches(x1), Inches(y1), Inches(x2), Inches(y2)
    )
    connector.line.color.rgb = color
    connector.line.width = width
    return connector


def add_bullet_frame(slide, left, top, width, height, items,
                     font_size=13, color=LIGHT_GRAY, title=None,
                     title_size=16, title_color=ACCENT_BLUE):
    txBox = slide.shapes.add_textbox(Inches(left), Inches(top),
                                     Inches(width), Inches(height))
    tf = txBox.text_frame
    tf.word_wrap = True
    if title:
        p = tf.paragraphs[0]
        p.text = title
        p.font.size = Pt(title_size)
        p.font.bold = True
        p.font.color.rgb = title_color
        p.font.name = "Calibri"
        p.space_after = Pt(6)
    for i, item in enumerate(items):
        if i == 0 and not title:
            p = tf.paragraphs[0]
        else:
            p = tf.add_paragraph()
        p.text = item
        p.font.size = Pt(font_size)
        p.font.color.rgb = color
        p.font.name = "Calibri"
        p.space_before = Pt(2)
        p.space_after = Pt(2)
    return txBox


def add_table(slide, left, top, width, height, rows, cols, data,
              header_color=ACCENT_BLUE, cell_color=None,
              text_color=WHITE, header_text_color=WHITE, font_size=10):
    if cell_color is None:
        cell_color = RGBColor(0x2A, 0x2A, 0x3E)
    tbl_shape = slide.shapes.add_table(rows, cols, Inches(left), Inches(top),
                                        Inches(width), Inches(height))
    tbl = tbl_shape.table

    row_height = Inches(height / rows)
    for ri in range(rows):
        tbl.rows[ri].height = row_height

    for r in range(rows):
        for c in range(cols):
            cell = tbl.cell(r, c)
            cell_text = str(data[r][c]) if r < len(data) and c < len(data[r]) else ""
            tf = cell.text_frame
            tf.word_wrap = True
            tf.clear()
            p = tf.paragraphs[0]
            p.alignment = PP_ALIGN.LEFT
            run = p.add_run()
            run.text = cell_text
            run.font.size = Pt(font_size)
            run.font.name = "Calibri"
            run.font.bold = (r == 0)
            run.font.color.rgb = header_text_color if r == 0 else text_color
            fill = cell.fill
            fill.solid()
            fill.fore_color.rgb = header_color if r == 0 else cell_color
            cell.margin_left = Pt(6)
            cell.margin_right = Pt(6)
            cell.margin_top = Pt(3)
            cell.margin_bottom = Pt(3)
    return tbl_shape


def slide_title(slide, title_text, subtitle_text=None):
    add_text_box(slide, 0.6, 0.3, 12, 0.7, title_text,
                 font_size=32, bold=True, color=WHITE)
    line = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE,
        Inches(0.6), Inches(0.95), Inches(3), Pt(3)
    )
    line.fill.solid()
    line.fill.fore_color.rgb = ACCENT_BLUE
    line.line.fill.background()
    if subtitle_text:
        add_text_box(slide, 0.6, 1.1, 12, 0.5, subtitle_text,
                     font_size=16, color=LIGHT_GRAY)


def _find_latest_charts_dir():
    """Find the most recent results directory containing charts.

    Searches both the default ``results/`` output dir and the isolated
    ``campaign-results/`` dir (where multi-session campaign runs land), and picks
    the most recently modified so the deck tracks the latest run regardless of
    which scheme produced it.
    """
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chaosprobe")
    dirs = []
    for base in ("results", "campaign-results"):
        dirs += glob.glob(os.path.join(root, base, "*", "charts"))
    return max(dirs, key=os.path.getmtime) if dirs else None


def add_image_or_placeholder(slide, left, top, width, height,
                             image_path, placeholder_text):
    """Add an image if it exists, otherwise add a placeholder box."""
    if image_path and os.path.exists(image_path):
        slide.shapes.add_picture(image_path, Inches(left), Inches(top),
                                 Inches(width), Inches(height))
    else:
        add_rounded_box(slide, left, top, width, height, VERY_DARK,
                        placeholder_text, 11, MID_GRAY, False,
                        border_color=MID_GRAY)


# ══════════════════════════════════════════════════════════════════════
# SLIDE 1 — TITLE
# ══════════════════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)

add_text_box(slide, 1.5, 1.8, 10.3, 1.2, "ChaosProbe",
             font_size=54, bold=True, color=WHITE, alignment=PP_ALIGN.CENTER)
add_text_box(slide, 1.5, 3.0, 10.3, 1.0,
             "Measuring the Impact of Chaos in Differing\n"
             "Placement Strategies within Cloud Systems",
             font_size=24, color=ACCENT_BLUE, alignment=PP_ALIGN.CENTER)

dline = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE,
    Inches(4.5), Inches(4.2), Inches(4.3), Pt(3))
dline.fill.solid()
dline.fill.fore_color.rgb = ACCENT_BLUE
dline.line.fill.background()

add_text_box(slide, 1.5, 4.6, 10.3, 0.6,
             "MSc Thesis Defense — University of Amsterdam",
             font_size=20, color=LIGHT_GRAY, alignment=PP_ALIGN.CENTER)
add_text_box(slide, 1.5, 5.3, 10.3, 0.5,
             "April 2026",
             font_size=16, color=MID_GRAY, alignment=PP_ALIGN.CENTER)


# ══════════════════════════════════════════════════════════════════════
# SLIDE 2 — BACKGROUND & MOTIVATION
# ══════════════════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)
slide_title(slide, "Background & Motivation")

# Microservices context — left
add_rounded_box(slide, 0.6, 1.5, 5.8, 2.0, VERY_DARK,
                border_color=CLR_CLI)
add_text_box(slide, 0.8, 1.5, 5.4, 0.35, "Microservice Architecture",
             font_size=16, bold=True, color=CLR_CLI)
add_bullet_frame(slide, 0.8, 1.9, 5.4, 1.5, [
    "• Decomposed services with independent lifecycles",
    "• Communicate via network (HTTP / gRPC / Redis)",
    "• Deployed as containers orchestrated by Kubernetes",
    "• Independently scalable — but failures can cascade",
], font_size=12, color=LIGHT_GRAY)

# The problem — right
add_rounded_box(slide, 6.8, 1.5, 5.8, 2.0, VERY_DARK,
                border_color=ACCENT_RED)
add_text_box(slide, 7.0, 1.5, 5.4, 0.35, "The Placement Problem",
             font_size=16, bold=True, color=ACCENT_RED)
add_bullet_frame(slide, 7.0, 1.9, 5.4, 1.5, [
    "• K8s scheduler optimizes for resource fit,\n  not for resilience or fault isolation",
    "• Pod placement determines which services share\n  node resources (CPU, memory, disk, network)",
    "• Co-located services suffer correlated failures",
], font_size=12, color=LIGHT_GRAY)

# Chaos engineering
add_rounded_box(slide, 0.6, 3.8, 5.8, 1.6, VERY_DARK,
                border_color=ACCENT_ORANGE)
add_text_box(slide, 0.8, 3.8, 5.4, 0.35, "Chaos Engineering",
             font_size=16, bold=True, color=ACCENT_ORANGE)
add_bullet_frame(slide, 0.8, 4.2, 5.4, 1.1, [
    "• Discipline of experimenting on systems to build\n  confidence in resilience (Basiri et al., 2016)",
    "• Controlled fault injection reveals weaknesses\n  before they manifest in production",
], font_size=12, color=LIGHT_GRAY)

# The gap
add_rounded_box(slide, 6.8, 3.8, 5.8, 1.6, VERY_DARK,
                border_color=ACCENT_GREEN)
add_text_box(slide, 7.0, 3.8, 5.4, 0.35, "Research Gap",
             font_size=16, bold=True, color=ACCENT_GREEN)
add_bullet_frame(slide, 7.0, 4.2, 5.4, 1.1, [
    "• Existing work studies placement OR resilience,\n  but rarely their interaction under fault injection",
    "• No systematic framework to quantify how\n  placement topology affects chaos resilience",
], font_size=12, color=LIGHT_GRAY)

# Approach
add_rounded_box(slide, 0.6, 5.7, 12.1, 1.5, VERY_DARK,
                border_color=ACCENT_BLUE, border_width=Pt(2))
add_text_box(slide, 0.8, 5.7, 11.7, 0.35, "Our Approach",
             font_size=16, bold=True, color=ACCENT_BLUE)
add_text_box(slide, 0.8, 6.1, 11.7, 0.5,
    "ChaosProbe: an automated framework that systematically varies pod placement strategies, "
    "injects faults via LitmusChaos, and measures the response at three layers — aggregate score, "
    "kernel/network mechanism, and user-visible outcome — to establish at which layer placement "
    "effects appear under each fault class.",
    font_size=13, color=LIGHT_GRAY)

# Pipeline arrows
approach_steps = [
    ("Deploy", CLR_CLI, 0.8),
    ("Place", CLR_ORCH, 3.3),
    ("Inject", CLR_CHAOS, 5.8),
    ("Measure", CLR_METRICS, 8.3),
    ("Compare", CLR_OUTPUT, 10.8),
]
for name, clr, x in approach_steps:
    add_rounded_box(slide, x, 6.7, 2.0, 0.35, clr, name, 12, WHITE, True)
for i in range(len(approach_steps) - 1):
    x1 = approach_steps[i][2] + 2.0
    x2 = approach_steps[i + 1][2]
    add_arrow(slide, x1, 6.87, x2, 6.87, LIGHT_GRAY, Pt(2))


# ══════════════════════════════════════════════════════════════════════
# SLIDE 3 — RESEARCH QUESTION & HYPOTHESES
# ══════════════════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)
slide_title(slide, "Research Question & Hypotheses")

# Research question — compact
add_rounded_box(slide, 0.6, 1.3, 12.1, 0.85, VERY_DARK,
                border_color=ACCENT_BLUE, border_width=Pt(3))
add_text_box(slide, 0.8, 1.4, 11.7, 0.65,
    "Under which fault classes does pod placement measurably affect mechanism-level behaviour and "
    "user-visible outcomes — and when do aggregate resilience scores obscure those effects?",
    font_size=15, bold=True, color=WHITE, alignment=PP_ALIGN.CENTER)

# Theme group labels (above each fault-class card group)
group_labels = [
    ("Churn (pod-delete)", ACCENT_RED, 0.35, 2.55),
    ("Load contention", ACCENT_ORANGE, 6.65, 2.55),
    ("Node failure", ACCENT_PURPLE, 10.85, 2.55),
]
for label, clr, x, y in group_labels:
    add_text_box(slide, x, y, 4, 0.25, label,
                 font_size=11, bold=True, color=clr)

# Six hypotheses H1-H6 — single row, grouped by fault class. The study is a
# fault-class x measurement-layer matrix: the score layer is blind (H1), the
# mechanism layer moves (H2, H4) without reaching the user (H3), and the
# availability layer is where placement bites users — predictably (H5, H6).
hypotheses = [
    # Churn (pod-delete) — H1-H3 (red)
    ("H1", "The score cannot rank placements",
     "ICC_strategy = 0.033 (CI 0.014-0.178): only 3.3% of score "
     "variance is between-strategy (7 sessions, 147 iterations). "
     "Focal colocate 64.0 vs spread 74.3 (d = 0.46) needs 73 "
     "iters/strategy for 80% power; MDE at n=3 is ~51 points.",
     ACCENT_RED),
    ("H2", "Placement moves a reconvergence signature",
     "Churn flushes conntrack state: spread 38.5% vs colocate 2.7% "
     "median; spread > colocate in 7/7 sessions (sign p = .016, "
     "Wilcoxon p = .023). CPU throttling corroborates (colocate "
     "lowest, 6/7).",
     ACCENT_RED),
    ("H3", "The mechanism is decoupled from the user",
     "Flush vs dependent-route p95: ρ = 0.07 (n.s., TOST-decoupled) "
     "while the control route shows ρ = 0.29* — a run-level-confound "
     "signature, not dependency-specific user impact.",
     ACCENT_RED),
    # Load contention — H4-H5 (orange)
    ("H4", "Under load, mechanism only",
     "East-west effect replicates: colocate ~1.3-1.4x lower "
     "inter-service p95 than spread (two i=4 batches). The "
     "user-layer effect did not survive replication — no "
     "user-visible placement claim under load.",
     ACCENT_ORANGE),
    ("H5", "A graph metric predicts the east-west tail",
     "Cross-node call fraction (dependency graph + placement, "
     "pre-chaos) vs east-west p95: ρ = 0.79 (n = 8, p < .05). "
     "Coarse — separates node-local from spreading placements — "
     "but makes the Neo4j graph analytically load-bearing.",
     ACCENT_ORANGE),
    # Node failure — H6 (purple)
    ("H6", "Latency vs availability trade-off",
     "Node drain: colocate loses 11/11 services (100% blast, "
     "~10.3 s recovery) vs spread 2/11 (18%, ~2.6 s); two "
     "doctor-clean batches. The co-location that wins H5's "
     "latency loses H6's availability.",
     ACCENT_PURPLE),
]

# Layout: single row of 6 cards, 2.05" wide × 3.7" tall.
card_w, card_h = 2.05, 3.7
col_x = [0.35 + i * (card_w + 0.05) for i in range(6)]
row_y = [2.9]

for i, (label, title, desc, clr) in enumerate(hypotheses):
    x, y = col_x[i], row_y[0]
    # Card border box
    add_rounded_box(slide, x, y, card_w, card_h, VERY_DARK,
                    border_color=clr, border_width=Pt(2))
    # Coloured header strip with the H label
    add_rounded_box(slide, x, y, card_w, 0.35, clr, label,
                    13, WHITE, True)
    # Title
    add_text_box(slide, x + 0.1, y + 0.4, card_w - 0.2, 0.4, title,
                 font_size=11, bold=True, color=clr)
    # Description
    add_text_box(slide, x + 0.1, y + 0.85, card_w - 0.2, card_h - 0.95, desc,
                 font_size=9, color=LIGHT_GRAY)

# Footer
add_text_box(slide, 0.6, 7.05, 12.1, 0.35,
    "Each hypothesis is falsifiable from ChaosProbe-collected data. The literature "
    "predictions L1-L3 are kept as context: inapplicable in this regime, not refuted.",
    font_size=10, color=MID_GRAY, alignment=PP_ALIGN.CENTER)


# ══════════════════════════════════════════════════════════════════════
# SLIDE 4 — RELATED WORK
# ══════════════════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)
slide_title(slide, "Related Work")

# Three columns: Placement, Chaos Eng, Gap
add_rounded_box(slide, 0.5, 1.5, 3.8, 4.5, VERY_DARK,
                border_color=CLR_ORCH, border_width=Pt(2))
add_text_box(slide, 0.7, 1.55, 3.4, 0.35, "Pod Placement & Scheduling",
             font_size=14, bold=True, color=CLR_ORCH)
add_bullet_frame(slide, 0.7, 2.0, 3.4, 3.8, [
    "• Borg (Verma et al., 2015)\n  Resource-aware bin-packing",
    "• Medea (Garefalakis et al., 2018)\n  Topology spread constraints",
    "• DeathStarBench (Gan et al., 2019)\n  Dependency-graph-aware placement",
    "• Mars (2011); Delimitrou (2014)\n  Contention-aware co-scheduling",
    "• Liu et al. (arXiv 2507.16109, 2025)\n  Cloud-edge placement under churn",
], font_size=11, color=LIGHT_GRAY)

add_rounded_box(slide, 4.6, 1.5, 3.8, 4.5, VERY_DARK,
                border_color=CLR_CHAOS, border_width=Pt(2))
add_text_box(slide, 4.8, 1.55, 3.4, 0.35, "Chaos Engineering",
             font_size=14, bold=True, color=CLR_CHAOS)
add_bullet_frame(slide, 4.8, 2.0, 3.4, 3.8, [
    "• Principles of Chaos Engineering\n  (Basiri et al., 2016)",
    "• LitmusChaos — CNCF sandbox\n  ChaosEngine CRDs + ChaosCenter",
    "• Chaos Monkey (Netflix, 2011)\n  Random instance termination",
    "• Mutiny! K8s churn injection\n  (DSN 2024) — closest peer",
    "• Tail at Scale (Dean & Barroso,\n  2013) — latency sensitivity",
], font_size=11, color=LIGHT_GRAY)

add_rounded_box(slide, 8.7, 1.5, 3.8, 4.5, VERY_DARK,
                border_color=ACCENT_BLUE, border_width=Pt(2))
add_text_box(slide, 8.9, 1.55, 3.4, 0.35, "Gap — Our Contribution",
             font_size=14, bold=True, color=ACCENT_BLUE)
add_bullet_frame(slide, 8.9, 2.0, 3.4, 3.8, [
    "• Existing work studies placement\n  OR resilience — not their interaction",
    "• No framework systematically varies\n  placement under controlled chaos",
    "• Missing: quantitative comparison\n  across multiple dimensions",
    "• ChaosProbe bridges this gap:\n  8 placements × 3 fault classes,\n  layered metrics",
    "• Graph storage preserves causal\n  topology for analysis",
], font_size=11, color=LIGHT_GRAY)

# Contention categories table
add_text_box(slide, 0.5, 6.3, 12, 0.3,
             "Expected Contention by Placement (Literature Predictions L1–L3 — tested as context)",
             font_size=14, bold=True, color=ACCENT_ORANGE)
contention_data = [
    ["Category", "Metric", "Colocate Impact", "Key Reference"],
    ["CPU Contention", "Millicores, throttle seconds",
     "Shared cores → throttling", "Burns et al. (2016)"],
    ["Memory Pressure", "Working set bytes",
     "Shared memory → evictions", "Verma et al. (2015)"],
    ["Network Latency", "HTTP response time (ms)",
     "Shared network stack", "Gan et al. (2019)"],
    ["Disk I/O", "Sequential R/W bytes/s",
     "Shared bandwidth", "Dean & Barroso (2013)"],
]
add_table(slide, 0.5, 6.55, 12.3, 0.85, 5, 4, contention_data,
          font_size=9, header_color=ACCENT_ORANGE)


# ══════════════════════════════════════════════════════════════════════
# SLIDE 5 — PLACEMENT STRATEGIES (independent variable)
# ══════════════════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)
slide_title(slide, "Placement Strategies",
            subtitle_text="Independent variable: 6 strategies + baseline + default scheduler")

strat_data = [
    ("Baseline",
     "Null-injection control\nDefault scheduler + trivial fault\n(1% CPU, 1s) — validates methodology",
     CLR_INFRA,
     [(0.4, 0.2), (1.1, 0.2), (0.4, 0.6), (1.1, 0.6)],
     "None (control)", "Expected: 100% score",
     "Basiri et al., IEEE SW 2016"),
    ("Default",
     "Default K8s scheduler\nFull chaos injection\nScheduler-determined placement",
     CLR_CLI,
     [(0.4, 0.2), (1.1, 0.2), (0.4, 0.6), (1.1, 0.6)],
     "Scheduler-set", "Placement null hypothesis",
     "Burns et al., ACM Queue 2016"),
    ("Colocate",
     "All pods pinned to a single node\nvia nodeSelector (hostname)\nMaximal co-location",
     CLR_CHAOS,
     [(0.4, 0.2), (0.65, 0.35), (0.4, 0.5), (0.65, 0.65)],
     "Maximum", "Expected: worst resilience",
     "Mars 2011; Delimitrou 2014"),
    ("Spread",
     "Even distribution across workers\nvia per-node nodeSelector\nMinimal per-node contention",
     CLR_METRICS,
     [(0.2, 0.4), (0.6, 0.4), (1.0, 0.4), (1.4, 0.4)],
     "Minimum", "Expected: best isolation",
     "Medea (Garefalakis 2018)"),
    ("Random",
     "Seeded random assignment\nReproducible null baseline\nfor topology effects",
     CLR_OUTPUT,
     [(0.3, 0.2), (1.0, 0.6), (0.3, 0.6), (1.0, 0.2)],
     "Variable", "Seeded; reproducible",
     "Sparrow (Ousterhout SOSP 2013)"),
    ("Adversarial",
     "Heavy pods → single node (worst-fit)\nLight pods → remaining nodes\nIntentional CPU/mem hotspot",
     ACCENT_PURPLE,
     [(0.3, 0.2), (0.55, 0.35), (1.0, 0.4), (1.3, 0.6)],
     "High (asymmetric)", "Resource-weighted hotspot",
     "Worst-fit; Cortez 2017"),
    ("Best-fit",
     "Pack into fewest nodes\n(bin-packing decreasing)\nBorg-style resource scoring",
     ACCENT_GREEN,
     [(0.3, 0.2), (0.55, 0.35), (0.4, 0.55), (1.25, 0.4)],
     "Moderate (packed)", "Minimizes nodes used",
     "Borg (Verma 2015; Burns 2016)"),
    ("Dependency-aware",
     "Co-locate communicating services\nvia service-graph partitioning\n(BFS from entry-point root)",
     ACCENT_BLUE,
     [(0.3, 0.25), (0.55, 0.4), (1.1, 0.3), (1.3, 0.55)],
     "Moderate (grouped)", "Preserves service-graph edges",
     "DeathStarBench (Gan ASPLOS 2019)"),
]

for i, (name, desc, clr, dots, contention, note, cite) in enumerate(strat_data):
    col = i % 4
    row = i // 4
    cw, ch = 3.05, 2.4
    bx = 0.25 + col * 3.25
    by = 1.7 + row * 2.7

    add_rounded_box(slide, bx, by, cw, ch, VERY_DARK,
                    border_color=clr, border_width=Pt(2))
    add_text_box(slide, bx + 0.08, by + 0.05, cw - 0.16, 0.32, name,
                 font_size=13, bold=True, color=clr)
    add_text_box(slide, bx + 0.08, by + 0.38, 1.45, 1.10, desc,
                 font_size=8, color=LIGHT_GRAY)

    for dx, dy in dots:
        add_rounded_box(slide, bx + 1.55 + dx * 0.85, by + 0.50 + dy * 0.70,
                        0.18, 0.18, clr)

    cont_clr = (ACCENT_GREEN if contention in ("None (control)", "Minimum")
                else ACCENT_RED if contention in ("Maximum", "High (asymmetric)")
                else ACCENT_ORANGE)
    add_text_box(slide, bx + 0.08, by + 1.55, cw - 0.16, 0.22,
                 f"Contention: {contention}", font_size=9, bold=True, color=cont_clr)
    add_text_box(slide, bx + 0.08, by + 1.78, cw - 0.16, 0.22,
                 note, font_size=8, color=MID_GRAY)
    add_text_box(slide, bx + 0.08, by + 2.02, cw - 0.16, 0.32,
                 cite, font_size=7, color=ACCENT_BLUE)

add_text_box(slide, 0.25, 6.95, 12.85, 0.5,
             "Analytic weight: the churn findings (H2 conntrack reconvergence, H3 decoupling) rest on the colocate vs. "
             "spread locality contrast. All 8 placements enter the load matrix — H5's cross-node fraction is computed per "
             "strategy (n = 8) — and H6 contrasts the two extremes (colocate vs. spread) under node drain; a gradient run "
             "over the intermediate placements extends that two-point contrast.",
             font_size=8, color=MID_GRAY)


# ══════════════════════════════════════════════════════════════════════
# SLIDE 6 — EXPERIMENTAL SETUP
# ══════════════════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)
slide_title(slide, "Experimental Setup")

# Target application — left
add_rounded_box(slide, 0.5, 1.5, 6.0, 3.5, VERY_DARK,
                border_color=ACCENT_BLUE, border_width=Pt(2))
add_text_box(slide, 0.7, 1.5, 5.6, 0.3, "Target Application — Google Online Boutique",
             font_size=14, bold=True, color=ACCENT_BLUE)

# Service dependency graph (compact)
add_rounded_box(slide, 2.8, 2.0, 1.8, 0.4, CLR_CLI,
                "frontend", 10, WHITE, True)
tier2 = [("productcatalog", 0.7), ("currency", 2.2), ("cart", 3.7), ("recommend", 5.2)]
for name, x in tier2:
    add_rounded_box(slide, x, 2.6, 1.2, 0.35, CLR_ORCH, name, 8, WHITE, True)
    add_arrow(slide, 3.7, 2.4, x + 0.6, 2.6, LIGHT_GRAY, Pt(1))
add_rounded_box(slide, 1.7, 3.2, 1.2, 0.35, CLR_ORCH, "checkout", 8, WHITE, True)
add_arrow(slide, 3.7, 2.4, 2.3, 3.2, LIGHT_GRAY, Pt(1))
add_rounded_box(slide, 3.5, 3.2, 1.2, 0.35, ACCENT_RED, "redis-cart", 8, WHITE, True)
add_arrow(slide, 3.4, 2.95, 4.1, 3.2, LIGHT_GRAY, Pt(1))
bottom = [("email", 0.7), ("payment", 2.2), ("shipping", 3.7)]
for name, x in bottom:
    add_rounded_box(slide, x, 3.8, 1.2, 0.35, CLR_ORCH, name, 8, WHITE, True)
    add_arrow(slide, 2.3, 3.55, x + 0.6, 3.8, LIGHT_GRAY, Pt(1))
# adservice — called directly by frontend (not via checkout)
add_rounded_box(slide, 5.2, 3.8, 1.2, 0.35, CLR_ORCH, "ad", 8, WHITE, True)
add_arrow(slide, 3.7, 2.4, 5.8, 3.8, LIGHT_GRAY, Pt(1))

add_text_box(slide, 0.7, 4.3, 5.6, 0.6,
    "11 services (10 polyglot microservices + Redis)\n"
    "Single replica per service — 100% pod-delete = full unavailability",
    font_size=10, color=MID_GRAY)

# Cluster topology — right
add_rounded_box(slide, 6.8, 1.5, 5.8, 2.2, VERY_DARK,
                border_color=CLR_INFRA, border_width=Pt(2))
add_text_box(slide, 7.0, 1.5, 5.4, 0.3, "Cluster Topology (Vagrant / libvirt)",
             font_size=14, bold=True, color=CLR_INFRA)

# Node boxes
add_rounded_box(slide, 7.0, 1.9, 1.7, 0.7, RGBColor(0x22, 0x33, 0x55),
                border_color=ACCENT_BLUE)
add_text_box(slide, 7.1, 1.9, 1.5, 0.2, "cp1", font_size=10, bold=True, color=ACCENT_BLUE)
add_text_box(slide, 7.1, 2.15, 1.5, 0.4, "2 vCPU\n12 GiB", font_size=9, color=LIGHT_GRAY)

worker_specs = [("w1", "4 GiB", 8.8), ("w2", "4 GiB", 9.7),
                ("w3", "4 GiB", 10.6), ("w4", "4 GiB", 11.5)]
for name, ram, x in worker_specs:
    add_rounded_box(slide, x, 1.9, 0.8, 0.7, RGBColor(0x22, 0x44, 0x22),
                    border_color=ACCENT_GREEN)
    add_text_box(slide, x + 0.05, 1.9, 0.7, 0.2, name,
                 font_size=9, bold=True, color=ACCENT_GREEN)
    add_text_box(slide, x + 0.05, 2.15, 0.7, 0.4, f"2 vCPU\n{ram}",
                 font_size=8, color=LIGHT_GRAY)

add_text_box(slide, 7.0, 2.7, 5.4, 0.3,
    "K8s v1.28.6 • Calico CNI • containerd 1.7.11 • Total: 10 vCPU, 28 GiB",
    font_size=9, color=MID_GRAY)

# Experiment configurations — bottom
add_rounded_box(slide, 0.5, 5.2, 6.0, 2.1, VERY_DARK,
                border_color=CLR_CHAOS, border_width=Pt(2))
add_text_box(slide, 0.7, 5.2, 5.6, 0.3, "Three Fault Classes × Placement",
             font_size=14, bold=True, color=CLR_CHAOS)
exp_data = [
    ["", "Churn (pod-delete)", "Load (Locust spike)", "Node failure (drain)"],
    ["Stressor", "Kill target every 15s, 120s", "200-user sustained spike", "Drain the target's node"],
    ["Target", "productcatalogservice", "whole app under load", "node hosting the target"],
    ["Layer read", "Score + mechanism + user", "East-west + user routes", "Blast radius + recovery"],
    ["Hypotheses", "H1–H3", "H4–H5", "H6"],
]
add_table(slide, 0.7, 5.55, 5.6, 1.6, 5, 4, exp_data,
          font_size=8, header_color=CLR_CHAOS)

# Baseline + infrastructure — right bottom
add_rounded_box(slide, 6.8, 3.9, 5.8, 1.1, VERY_DARK,
                border_color=CLR_INFRA, border_width=Pt(2))
add_text_box(slide, 7.0, 3.9, 5.4, 0.3, "Baseline — Methodology Control",
             font_size=13, bold=True, color=CLR_INFRA)
add_text_box(slide, 7.0, 4.25, 5.4, 0.6,
    "Swaps pod-delete → trivial pod-cpu-hog (DURATION=1s, CPU_LOAD=1%).\n"
    "Validates probes & scoring: expected 100%, 0 recovery cycles — any drift\n"
    "indicates pre-existing instability rather than placement effects.",
    font_size=10, color=LIGHT_GRAY)

add_rounded_box(slide, 6.8, 5.2, 5.8, 2.1, VERY_DARK,
                border_color=CLR_STORAGE, border_width=Pt(2))
add_text_box(slide, 7.0, 5.2, 5.4, 0.3, "Infrastructure Components",
             font_size=14, bold=True, color=CLR_STORAGE)
infra_data = [
    ["Component", "Purpose"],
    ["LitmusChaos + ChaosCenter", "Fault injection engine (Helm)"],
    ["Prometheus + kube-state-metrics", "Cluster metrics (PromQL)"],
    ["Neo4j 5 Community", "Graph storage (14 node types, 18 rels)"],
    ["Locust (steady: 50 users, 10/s)", "Load generation (120s)"],
]
add_table(slide, 7.0, 5.55, 5.4, 1.6, 5, 2, infra_data,
          font_size=9, header_color=CLR_STORAGE)


# ══════════════════════════════════════════════════════════════════════
# SLIDE 7 — CHAOSPOBE FRAMEWORK
# ══════════════════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)
slide_title(slide, "ChaosProbe — System Architecture")

# ChaosProbe components — left
add_text_box(slide, 0.3, 1.4, 6.0, 0.3, "ChaosProbe (our contribution)",
             font_size=16, bold=True, color=ACCENT_BLUE)

cp_components = [
    ("Placement\nEngine",
     "6 strategies: mutate nodeSelector\nper deployment to target nodes",
     CLR_ORCH, 0.3, 1.8),
    ("Metrics\nCollection",
     "6 continuous probers (threads):\nrecovery, latency, resources,\nRedis, disk, Prometheus",
     CLR_METRICS, 0.3, 3.0),
    ("Result\nAggregation",
     "ChaosResult CRDs, probe verdicts,\nresilience scoring, phase tracking",
     CLR_OUTPUT, 0.3, 4.2),
    ("Orchestrator",
     "Strategy runner, run phases,\npreflight checks, port-forward",
     CLR_CLI, 3.4, 1.8),
    ("Graph\nStorage",
     "Neo4j writer/reader: 14 node types,\n18 relationships, Cypher queries",
     CLR_STORAGE, 3.4, 3.0),
    ("Visualization",
     "matplotlib charts, HTML report,\nML export (CSV/Parquet)",
     CLR_OUTPUT, 3.4, 4.2),
]
for name, desc, clr, x, y in cp_components:
    add_rounded_box(slide, x, y, 2.8, 1.12, VERY_DARK,
                    border_color=clr)
    add_text_box(slide, x + 0.1, y + 0.05, 2.6, 0.4, name,
                 font_size=11, bold=True, color=clr)
    add_text_box(slide, x + 0.1, y + 0.45, 2.6, 0.62, desc,
                 font_size=9, color=LIGHT_GRAY)

# External infrastructure — right
add_text_box(slide, 6.8, 1.4, 6.0, 0.3, "Infrastructure (existing tools)",
             font_size=16, bold=True, color=CLR_INFRA)

infra_components = [
    ("LitmusChaos +\nChaosCenter",
     "Fault injection engine.\nChaosEngine CRDs, Argo Workflows,\nChaosCenter dashboard + API",
     CLR_CHAOS, 6.8, 1.8),
    ("Prometheus +\nkube-state-metrics",
     "Cluster monitoring.\nPromQL queries for pod_ready,\nCPU throttle, memory, network",
     CLR_METRICS, 6.8, 3.0),
    ("Neo4j 5\nCommunity",
     "Graph database.\n14 node types, 18 relationships.\nCypher query language",
     CLR_STORAGE, 6.8, 4.2),
    ("Kubernetes\nv1.28.6",
     "Container orchestration.\ncontainerd runtime, Metrics API,\nWatch API for recovery tracking",
     CLR_ORCH, 9.9, 1.8),
    ("Locust",
     "Load generation.\nConfigurable user count, spawn rate.\nHTTP traffic to entry-point service",
     CLR_CLI, 9.9, 3.0),
    ("Vagrant\n(libvirt/KVM)",
     "VM provisioning.\n5 VMs: 1 control plane +\n4 worker nodes",
     CLR_INFRA, 9.9, 4.2),
]
for name, desc, clr, x, y in infra_components:
    add_rounded_box(slide, x, y, 2.8, 1.12, VERY_DARK,
                    border_color=clr)
    add_text_box(slide, x + 0.1, y + 0.05, 2.6, 0.4, name,
                 font_size=11, bold=True, color=clr)
    add_text_box(slide, x + 0.1, y + 0.45, 2.6, 0.62, desc,
                 font_size=9, color=LIGHT_GRAY)

# Flow arrows
add_arrow(slide, 6.2, 2.3, 6.8, 2.3, ACCENT_BLUE, Pt(2))
add_arrow(slide, 6.2, 3.5, 6.8, 3.5, ACCENT_BLUE, Pt(2))
add_arrow(slide, 6.2, 4.7, 6.8, 4.7, ACCENT_BLUE, Pt(2))
add_text_box(slide, 6.2, 2.05, 0.6, 0.2, "uses", font_size=10, color=MID_GRAY)
add_text_box(slide, 6.2, 3.25, 0.6, 0.2, "queries", font_size=10, color=MID_GRAY)
add_text_box(slide, 6.2, 4.45, 0.6, 0.2, "stores", font_size=10, color=MID_GRAY)

# Experiment lifecycle — bottom
add_text_box(slide, 0.3, 5.5, 12, 0.3, "Experiment Lifecycle",
             font_size=16, bold=True, color=ACCENT_BLUE)

phases = [
    ("1. Configure",    "Load YAML\nValidate specs",        CLR_CLI,     0.3),
    ("2. Place",        "Apply strategy\nPatch nodeSelector", CLR_ORCH,  2.8),
    ("3. Inject Chaos", "ChaosEngine\nvia ChaosCenter",     CLR_CHAOS,  5.3),
    ("4. Measure",      "6 probers\n+ load generator",      CLR_METRICS, 7.8),
    ("5. Store",        "Neo4j sync\nCharts + export",      CLR_STORAGE, 10.3),
]
for title, desc, clr, x in phases:
    add_rounded_box(slide, x, 5.9, 2.2, 1.0, clr, "", 10, WHITE, True,
                    border_color=clr)
    add_text_box(slide, x + 0.05, 5.9, 2.1, 0.3, title,
                 font_size=11, bold=True, color=WHITE)
    add_text_box(slide, x + 0.1, 6.2, 2.0, 0.6, desc,
                 font_size=10, color=TRANS_WHITE)
for i in range(len(phases) - 1):
    x1 = phases[i][3] + 2.2
    x2 = phases[i + 1][3]
    add_arrow(slide, x1, 6.4, x2, 6.4, LIGHT_GRAY, Pt(2))


# ══════════════════════════════════════════════════════════════════════
# SLIDE 8 — MEASUREMENT DESIGN
# ══════════════════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)
slide_title(slide, "Measurement Design")

# Three-phase timeline
add_text_box(slide, 0.5, 1.3, 12, 0.3, "Three-Phase Measurement Window",
             font_size=16, bold=True, color=ACCENT_BLUE)
add_rounded_box(slide, 0.5, 1.65, 3.5, 0.45, CLR_ORCH,
                "PreChaos — steady state (60s)", 11, WHITE, True)
add_rounded_box(slide, 4.2, 1.65, 4.5, 0.45, CLR_CHAOS,
                "DuringChaos — fault active (120s)", 11, WHITE, True)
add_rounded_box(slide, 8.9, 1.65, 3.8, 0.45, CLR_METRICS,
                "PostChaos — recovery sampling (60s)", 11, WHITE, True)

# Probers table — left
add_text_box(slide, 0.5, 2.4, 7, 0.3, "6 Continuous Probers (Background Threads)",
             font_size=14, bold=True, color=CLR_METRICS)
prober_data = [
    ["Prober", "What It Measures", "Data Source", "Interval"],
    ["RecoveryWatcher", "deletion→scheduled (d2s) + scheduled→ready (s2r) split", "K8s Watch API", "Real-time"],
    ["LatencyProber", "HTTP route latency + error rates + per-pod stddev", "kubectl exec → python3/wget", "3.5s"],
    ["RedisProber", "Redis ops/s (GET/SET throughput)", "kubectl exec → redis-cli", "10s"],
    ["DiskProber", "Sequential disk R/W bytes/s", "kubectl exec → dd", "10s"],
    ["ResourceProber", "Node/pod CPU (millicores) + memory (used-nodes only)", "Metrics API (v1beta1)", "5s"],
    ["PrometheusProber (app)", "pod_ready, CPU/memory, throttle, net rx", "PromQL queries", "10s"],
    ["PrometheusProber (churn)", "kube-proxy SLO p99, CoreDNS p99, conntrack, TCP retrans", "PromQL — SIG-Scalability metrics", "10s"],
]
add_table(slide, 0.3, 2.8, 7.5, 2.4, 8, 4, prober_data, font_size=9)

# Probes + scoring — right
add_text_box(slide, 8.2, 2.4, 5, 0.3, "Resilience Probes (LitmusChaos)",
             font_size=14, bold=True, color=ACCENT_ORANGE)

probe_summary = [
    ["Probe", "Mode (interval)", "Timeout / retries"],
    ["frontend-product-strict", "Continuous (2s)", "3s, 1 retry"],
    ["frontend-homepage-strict", "Continuous (2s)", "3s, 1 retry"],
    ["frontend-homepage-moderate", "Continuous (5s)", "3s, 4 retries"],
    ["frontend-product-moderate", "Continuous (5s)", "3s, 4 retries"],
    ["frontend-cart", "Continuous (6s)", "5s, 4 retries"],
    ["frontend-homepage-loose", "Continuous (6s)", "5s, 4 retries"],
    ["frontend-healthz", "Continuous (4s)", "5s, 3 retries"],
]
add_table(slide, 8.2, 2.8, 4.8, 2.1, 8, 3, probe_summary,
          font_size=9, header_color=ACCENT_ORANGE)
add_text_box(slide, 8.2, 4.95, 4.8, 0.4,
    "+ 5 Rust cmdProbes: check-redis, check-http-latency,\n"
    "  check-dns-latency, check-tcp-connect, check-cart-flow",
    font_size=9, color=LIGHT_GRAY)

# Scoring — bottom
add_text_box(slide, 0.5, 5.5, 12, 0.3, "Resilience Scoring",
             font_size=16, bold=True, color=ACCENT_BLUE)

add_rounded_box(slide, 0.5, 5.9, 5.5, 0.5, VERY_DARK,
                border_color=ACCENT_BLUE, border_width=Pt(2))
add_text_box(slide, 0.7, 5.95, 5.1, 0.4,
    "mean = Σ(probeSuccess%)/N · also report p25, harmonic mean, 95% bootstrap CI",
    font_size=11, bold=True, color=WHITE)

# Score scale
scores_compact = [
    ("0%", ACCENT_RED), ("17%", ACCENT_RED), ("33%", ACCENT_RED),
    ("50%", ACCENT_ORANGE), ("67%", ACCENT_ORANGE),
    ("83%", ACCENT_GREEN), ("100%", ACCENT_GREEN),
]
for j, (pct, clr) in enumerate(scores_compact):
    add_rounded_box(slide, 6.5 + j * 0.95, 5.9, 0.8, 0.5, clr,
                    pct, 12, WHITE, True)
add_text_box(slide, 6.5, 6.45, 6.7, 0.3,
             "Total disruption  ←                              →  No visible impact",
             font_size=10, color=MID_GRAY, alignment=PP_ALIGN.CENTER)

# Key design points
add_bullet_frame(slide, 0.5, 6.6, 6.0, 0.8, [
    "• Bootstrap 95% CI on the mean + Holm-Bonferroni-adjusted pairwise Mann-Whitney U",
    "• Recovery time split: scheduler-stall (d2s) vs container start-up (s2r)",
], font_size=10, color=LIGHT_GRAY)
add_bullet_frame(slide, 6.8, 6.6, 6.0, 0.8, [
    "• kube-proxy SLO + CoreDNS p99 + conntrack measured directly (SIG-Scalability)",
    "• Heterogeneity confound: score vs host-node RAM scatter (Threats-to-Validity)",
], font_size=10, color=LIGHT_GRAY)


# ══════════════════════════════════════════════════════════════════════
# SLIDE 9 — RESULTS: H1 — THE SCORE CANNOT RANK PLACEMENTS
# ══════════════════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)
slide_title(slide, "Results — H1: The Score Cannot Rank Placements")

charts_dir = _find_latest_charts_dir()

# Score distribution across runs — the H1 chart (overlapping boxes = no ranking)
img_path = os.path.join(charts_dir, "score_distribution.png") if charts_dir else None
add_image_or_placeholder(slide, 0.5, 1.4, 7.5, 4.5, img_path,
                         "[Resilience Score Distribution Across Sessions]\n\n"
                         "Box plot of resilience score (0–100%) per\n"
                         "strategy across the churn sessions — the boxes\n"
                         "overlap, so the score cannot rank placements.\n\n"
                         "Generated by: scripts/distribution_charts.py")

# Key observations — right
add_rounded_box(slide, 8.5, 1.4, 4.3, 4.5, VERY_DARK,
                border_color=ACCENT_BLUE, border_width=Pt(2))
add_text_box(slide, 8.7, 1.45, 3.9, 0.3, "Key Observations",
             font_size=16, bold=True, color=ACCENT_BLUE)
add_bullet_frame(slide, 8.7, 1.85, 3.9, 3.8, [
    "• Baseline: 100% (stddev 0) —\n  methodology control holds",
    "• ICC_strategy = 0.033, 95% CI [0.014, 0.178]:\n  only 3.3% of score variance is\n  between-strategy",
    "• Evidence base: 7 independent sessions,\n  147 churn iterations",
    "• Focal contrast colocate 64.0 vs spread 74.3\n  (d = 0.46) → 73 iterations/strategy\n  needed for 80% power",
    "• At the n = 3 actually run, the minimum\n  detectable effect is ≈ 51 score points",
    "• So the aggregate score cannot rank\n  placements — the signal is elsewhere",
], font_size=10, color=LIGHT_GRAY)

# Hypothesis check
add_rounded_box(slide, 0.5, 6.2, 12.3, 1.0, VERY_DARK,
                border_color=ACCENT_ORANGE, border_width=Pt(2))
add_text_box(slide, 0.7, 6.2, 11.9, 0.3, "H1 — the aggregate score cannot rank placement strategies",
             font_size=14, bold=True, color=ACCENT_ORANGE)
add_text_box(slide, 0.7, 6.55, 4.0, 0.6,
    "L1 (colocate = worst): N/A",
    font_size=12, bold=True, color=ACCENT_ORANGE)
add_text_box(slide, 4.8, 6.55, 4.0, 0.6,
    "L2 (spread = best): N/A",
    font_size=12, bold=True, color=ACCENT_ORANGE)
add_text_box(slide, 8.9, 6.55, 4.0, 0.6,
    "L3 (recovery → score): N/A",
    font_size=12, bold=True, color=ACCENT_ORANGE)
add_text_box(slide, 0.7, 6.95, 11.9, 0.3,
    "The score's between-strategy variance is too small a fraction to adjudicate L1–L3 — "
    "they are inapplicable in this regime, not refuted. The signal lives at the mechanism layer (next slides).",
    font_size=10, color=LIGHT_GRAY)


# ══════════════════════════════════════════════════════════════════════
# SLIDE 10 — RESULTS: H2 — A KERNEL RECONVERGENCE SIGNATURE MOVES
# ══════════════════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)
slide_title(slide, "Results — H2: A Kernel Reconvergence Signature Moves")

# Mechanism distribution chart — left
img_path = os.path.join(charts_dir, "mechanism_distribution.png") if charts_dir else None
add_image_or_placeholder(slide, 0.5, 1.4, 7.5, 4.5, img_path,
                         "[Conntrack Flush Distribution by Strategy]\n\n"
                         "Box plots per strategy across churn sessions:\n"
                         "conntrack flush % during the kill cycle —\n"
                         "spread high, colocate near zero.\n\n"
                         "Generated by: scripts/distribution_charts.py")

# Key numbers — right
add_rounded_box(slide, 8.5, 1.4, 4.3, 4.5, VERY_DARK,
                border_color=CLR_OUTPUT, border_width=Pt(2))
add_text_box(slide, 8.7, 1.45, 3.9, 0.3, "Conntrack Flush (per session)",
             font_size=15, bold=True, color=CLR_OUTPUT)
add_bullet_frame(slide, 8.7, 1.85, 3.9, 3.8, [
    "• Median flush during the kill cycle:\n  spread 38.5% vs colocate 2.7%",
    "• spread > colocate in 7 / 7 sessions",
    "• Sign test p = 0.0156;\n  Wilcoxon signed-rank p = 0.0225",
    "• Mechanistic reading: churn tears down\n  cross-node flows that must reconverge;\n  node-local paths are spared",
    "• Corroborating only: CPU throttling —\n  colocate lowest in 6 / 7 sessions\n  (weaker; lead with conntrack)",
], font_size=10, color=LIGHT_GRAY)

# Attribution caution — bottom
add_rounded_box(slide, 0.5, 6.2, 12.3, 1.0, VERY_DARK,
                border_color=ACCENT_ORANGE, border_width=Pt(2))
add_text_box(slide, 0.7, 6.2, 11.9, 0.3,
             "H2 — a reconvergence signature, deliberately not attributed to a specific code path",
             font_size=14, bold=True, color=ACCENT_ORANGE)
add_text_box(slide, 0.7, 6.55, 11.9, 0.6,
    "The flush is reported as a measured kernel/network reconvergence signature of the kill cycle. "
    "It is not attributed to kube-proxy's active conntrack-flush path: upstream that path is UDP-only, "
    "while this workload is TCP/gRPC — re-attribution of the exact mechanism is pending.",
    font_size=10, color=LIGHT_GRAY)


# ══════════════════════════════════════════════════════════════════════
# SLIDE 11 — RESULTS: H3 — THE MECHANISM DOES NOT REACH THE USER
# ══════════════════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)
slide_title(slide, "Results — H3: The Mechanism Does Not Reach the User")

# Latency degradation chart — left (the user layer)
img_path = os.path.join(charts_dir, "latency_degradation.png") if charts_dir else None
add_image_or_placeholder(slide, 0.5, 1.4, 7.5, 4.5, img_path,
                         "[Latency Degradation: Pre vs During Chaos]\n\n"
                         "Grouped bar chart: HTTP route latency\n"
                         "pre-chaos vs during-chaos per strategy —\n"
                         "the user layer the mechanism never moves.\n\n"
                         "Generated by: chaosprobe visualize")

# Decoupling evidence — right
add_rounded_box(slide, 8.5, 1.4, 4.3, 4.5, VERY_DARK,
                border_color=ACCENT_GREEN, border_width=Pt(2))
add_text_box(slide, 8.7, 1.45, 3.9, 0.3, "Decoupling Evidence",
             font_size=16, bold=True, color=ACCENT_GREEN)
add_bullet_frame(slide, 8.7, 1.85, 3.9, 3.8, [
    "• Conntrack flush → dependent-route p95:\n  ρ = 0.07 (n.s.) — and TOST declares it\n  statistically equivalent to zero",
    "• Control route (does NOT depend on the\n  target): ρ = 0.29* — the correlation is\n  stronger where it shouldn't exist",
    "• That pattern is the signature of a\n  run-level confound, not causation",
    "• No dependency-specific propagation of\n  the mechanism to the user layer",
], font_size=10, color=LIGHT_GRAY)

# Synthesis — bottom
add_rounded_box(slide, 0.3, 6.2, 6.2, 1.1, VERY_DARK,
                border_color=ACCENT_BLUE)
add_text_box(slide, 0.5, 6.2, 5.8, 0.3, "The layered churn story (H1–H3)",
             font_size=14, bold=True, color=ACCENT_BLUE)
add_text_box(slide, 0.5, 6.55, 5.8, 0.6,
    "The score is blind (H1); the kernel layer moves with placement (H2); "
    "the user layer does not follow (H3). Three layers, three different answers.",
    font_size=10, color=LIGHT_GRAY)

add_rounded_box(slide, 6.8, 6.2, 6.2, 1.1, VERY_DARK,
                border_color=ACCENT_ORANGE)
add_text_box(slide, 7.0, 6.2, 5.8, 0.3, "Operator reading (bounded)",
             font_size=14, bold=True, color=ACCENT_ORANGE)
add_text_box(slide, 7.0, 6.55, 5.8, 0.6,
    "For churn faults on single-replica services in this setup, pod placement "
    "is not a user-visible resilience lever — the killed pod is simply gone.",
    font_size=10, color=LIGHT_GRAY)


# ══════════════════════════════════════════════════════════════════════
# SLIDE 12 — RESULTS: H4 & H5 — LOAD CONTENTION + A GRAPH PREDICTOR
# ══════════════════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)
slide_title(slide, "Results — H4 & H5: Load Contention and a Graph Predictor")

# H4 — left
add_rounded_box(slide, 0.5, 1.4, 6.0, 4.4, VERY_DARK,
                border_color=ACCENT_ORANGE, border_width=Pt(2))
add_text_box(slide, 0.7, 1.45, 5.6, 0.3,
             "H4 — the mechanism replicates; the user layer does not",
             font_size=14, bold=True, color=ACCENT_ORANGE)
add_bullet_frame(slide, 0.7, 1.85, 5.6, 3.8, [
    "• 200-user Locust spike: the app is genuinely\n  resource-bound (hog faults never get there)",
    "• East-west inter-service p95: colocate ~1.3–1.4×\n  lower than spread, in both i = 4 batches\n  (median ratio 1.39× and 1.36×)",
    "• Co-location keeps inter-service calls node-local;\n  spread routes every call across the network —\n  the bottleneck under load",
    "• User-facing routes: a ~2× reading in batch A\n  collapsed to ~1.1× in the clean batch, with no\n  dependency specificity",
    "• So: no user-visible placement effect is claimed\n  under load — the mechanism effect is the finding",
], font_size=10, color=LIGHT_GRAY)

# H5 — right: cross-node fraction table + correlation
add_rounded_box(slide, 6.8, 1.4, 6.0, 4.4, VERY_DARK,
                border_color=ACCENT_GREEN, border_width=Pt(2))
add_text_box(slide, 7.0, 1.45, 5.6, 0.3,
             "H5 — cross-node call fraction predicts the east-west tail",
             font_size=14, bold=True, color=ACCENT_GREEN)
h5_data = [
    ["Strategy", "Cross-node fraction", "East-west p95 (ms)"],
    ["colocate", "0.00", "33.9"],
    ["best-fit", "0.13", "35.3"],
    ["dependency-aware", "0.73", "42.6"],
    ["spread", "0.73", "43.5"],
    ["random", "0.80", "43.9"],
    ["default", "0.78", "45.5"],
]
add_table(slide, 7.0, 1.85, 5.6, 2.5, 7, 3, h5_data,
          font_size=9, header_color=ACCENT_GREEN)
add_bullet_frame(slide, 7.0, 4.45, 5.6, 1.3, [
    "• Spearman ρ = 0.79 (n = 8 strategies, p < 0.05)",
    "• Computable from the dependency graph + placement\n  before any chaos — the Neo4j graph becomes\n  analytically load-bearing, not mere storage",
], font_size=10, color=LIGHT_GRAY)

# Framing — bottom
add_rounded_box(slide, 0.5, 6.0, 12.3, 1.2, VERY_DARK,
                border_color=ACCENT_BLUE, border_width=Pt(2))
add_text_box(slide, 0.7, 6.0, 11.9, 0.3,
             "Framing: empirical validation of a static pre-chaos predictor — and a coarse one",
             font_size=13, bold=True, color=ACCENT_BLUE)
add_text_box(slide, 0.7, 6.35, 11.9, 0.8,
    "Locality-as-objective already belongs to the literature (NetMARKS, graph-partitioning schedulers); the "
    "contribution here is validating the graph-derived fraction against measured during-load tails. The correlation "
    "is coarse — it separates the two node-local placements (colocate, best-fit) from the six spreading ones, which "
    "cluster at 0.70–0.80 — and single-batch. Note dependency-aware's partition did not co-locate as intended (0.73, spread-like).",
    font_size=10, color=LIGHT_GRAY)


# ══════════════════════════════════════════════════════════════════════
# SLIDE 13 — RESULTS: H6 — THE LATENCY/AVAILABILITY TRADE-OFF
# ══════════════════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)
slide_title(slide, "Results — H6: The Latency/Availability Trade-Off")

# Blast radius table — left
add_rounded_box(slide, 0.5, 1.4, 6.5, 4.4, VERY_DARK,
                border_color=ACCENT_RED, border_width=Pt(2))
add_text_box(slide, 0.7, 1.45, 6.1, 0.3,
             "Node drain on the target's node (third fault class)",
             font_size=14, bold=True, color=ACCENT_RED)
h6_data = [
    ["Placement", "On drained node", "Blast radius", "Target recovery"],
    ["colocate", "11 / 11 services", "11 — whole app offline (100%)", "~10.3 s"],
    ["spread", "2 / 11 services", "2 services (18%)", "~2.6 s"],
]
add_table(slide, 0.7, 1.85, 6.1, 1.3, 3, 4, h6_data,
          font_size=9, header_color=ACCENT_RED)
add_bullet_frame(slide, 0.7, 3.35, 6.1, 2.4, [
    "• Blast radius = services at 0 ready endpoints at the\n  outage trough, read from EndpointSlice snapshots —\n  not the score (a drain leaves every probe Unknown; H1)",
    "• Observed blast equals the placement-predicted blast\n  in every iteration, across two doctor-clean batches",
    "• Recovery scales with concentration too: 11 evicted\n  pods reschedule at once vs 2 — ~4× slower",
], font_size=10, color=LIGHT_GRAY)

# Trade-off framing — right
add_rounded_box(slide, 7.3, 1.4, 5.5, 4.4, VERY_DARK,
                border_color=ACCENT_PURPLE, border_width=Pt(2))
add_text_box(slide, 7.5, 1.45, 5.1, 0.3,
             "One placement property, two opposing consequences",
             font_size=14, bold=True, color=ACCENT_PURPLE)
add_bullet_frame(slide, 7.5, 1.85, 5.1, 3.8, [
    "• colocate: best east-west tail (H5: 33.9 ms,\n  lowest) AND worst node-failure blast\n  (100% outage) — spread is the mirror",
    "• H5 is the latency face, H6 the availability\n  face of the same co-location metric",
    "• Where placement DOES bite users in this\n  study is availability under node failure —\n  and it is predictable from the placement",
    "• Quantification of a known qualitative\n  trade-off (cell-based architectures), not\n  a discovery",
    "• Two-point contrast (the extremes); a\n  6-strategy gradient run extends this",
], font_size=10, color=LIGHT_GRAY)

# Bottom synthesis
add_rounded_box(slide, 0.5, 6.0, 12.3, 1.2, VERY_DARK,
                border_color=ACCENT_BLUE, border_width=Pt(3))
add_text_box(slide, 0.7, 6.15, 11.9, 0.9,
    "The same co-location that wins H5's latency loses H6's availability. Placement is not 'good' or 'bad' — "
    "it trades a measured east-west latency benefit against a measured node-failure blast radius, and both faces "
    "are computable from the dependency graph before any chaos.",
    font_size=12, bold=True, color=WHITE, alignment=PP_ALIGN.CENTER)


# ══════════════════════════════════════════════════════════════════════
# SLIDE 14 — NEGATIVE FINDINGS & LITERATURE PREDICTIONS
# ══════════════════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)
slide_title(slide, "Negative Findings — Why the Obvious Experiments Are Wrong")

# Hog faults absorbed — left
add_rounded_box(slide, 0.5, 1.4, 6.0, 2.6, VERY_DARK,
                border_color=ACCENT_RED, border_width=Pt(2))
add_text_box(slide, 0.7, 1.45, 5.6, 0.3, "Hog faults are absorbed, not felt",
             font_size=14, bold=True, color=ACCENT_RED)
add_bullet_frame(slide, 0.7, 1.85, 5.6, 2.0, [
    "• pod-cpu-hog: CFS-capped at the 200m container\n  limit — the hog throttles itself, not the app",
    "• node-cpu-hog: loads the node, but CPU requests\n  keep the light app pods responsive",
    "• Both scored 100 with the app fully up — the\n  'obvious' contention experiments measure nothing",
    "• Contention only bites when the app is genuinely\n  resource-bound — i.e. under load (H4)",
], font_size=10, color=LIGHT_GRAY)

# Memory hog self-evicts — right
add_rounded_box(slide, 6.8, 1.4, 6.0, 2.6, VERY_DARK,
                border_color=ACCENT_ORANGE, border_width=Pt(2))
add_text_box(slide, 7.0, 1.45, 5.6, 0.3, "node-memory-hog evicts itself first",
             font_size=14, bold=True, color=ACCENT_ORANGE)
add_bullet_frame(slide, 7.0, 1.85, 5.6, 2.0, [
    "• On 4 GiB workers, the kubelet evicts the\n  LitmusChaos helper pod before any app pod\n  feels memory pressure",
    "• The experiment kills its own instrument, not\n  the application (LitmusChaos issue #3397)",
    "• Negative findings like these bound the fault\n  taxonomy: they tell you which experiments\n  measure the app and which measure the harness",
], font_size=10, color=LIGHT_GRAY)

# L1-L3 — bottom
add_rounded_box(slide, 0.5, 4.3, 12.3, 2.9, VERY_DARK,
                border_color=ACCENT_BLUE, border_width=Pt(2))
add_text_box(slide, 0.7, 4.35, 11.9, 0.3,
             "Literature predictions L1–L3 — inapplicable in this regime, not refuted",
             font_size=14, bold=True, color=ACCENT_BLUE)
l_preds = [
    ("L1", "Colocate is the worst placement",
     "The score cannot adjudicate it (H1), and the churn\n"
     "mechanism points the other way: colocate flushes\n"
     "the least conntrack state and throttles least."),
    ("L2", "Spread isolates faults best",
     "Under churn, spreading maximises the cross-node\n"
     "flows the kill cycle tears down (H2) — and under\n"
     "node drain spread does win availability (H6)."),
    ("L3", "Recovery time predicts resilience",
     "The recovery decomposition (d2s/s2r) is unstable\n"
     "run-to-run and the score is too noisy to predict —\n"
     "no stable relationship on either side."),
]
for i, (label, title, body) in enumerate(l_preds):
    x = 0.7 + i * 4.05
    add_rounded_box(slide, x, 4.75, 0.55, 0.45, ACCENT_BLUE, label, 12, WHITE, True)
    add_text_box(slide, x + 0.65, 4.75, 3.3, 0.5, title,
                 font_size=11, bold=True, color=WHITE)
    add_text_box(slide, x, 5.35, 3.85, 1.3, body,
                 font_size=10, color=LIGHT_GRAY)
add_text_box(slide, 0.7, 6.75, 11.9, 0.4,
    "These predictions come from contention regimes the churn fault class never enters — they are "
    "inapplicable here, which is itself a fault-class-specific result, not a refutation of the literature.",
    font_size=10, color=MID_GRAY)


# ══════════════════════════════════════════════════════════════════════
# SLIDE 15 — THREATS TO VALIDITY
# ══════════════════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)
slide_title(slide, "Threats to Validity")

threats = [
    ("Internal Validity", ACCENT_RED, [
        ("Single application",
         "Results based on Google Online Boutique — a representative but single microservice "
         "benchmark. Other application topologies may yield different results."),
        ("Single replica per service",
         "100% pod-delete guarantees unavailability, but production systems typically run "
         "multiple replicas. Results represent worst-case single-replica scenarios."),
        ("Virtualized environment",
         "Vagrant/libvirt (KVM/QEMU) introduces virtualization overhead. Bare-metal "
         "clusters may show different performance characteristics, especially for I/O metrics."),
        ("Uneven contrast coverage",
         "The churn findings (H2, H3) rest on the colocate-vs-spread locality contrast; H5 covers all "
         "8 placements but is single-batch; H6 is a two-point contrast of the extremes — a 6-strategy "
         "gradient run extends it. Intermediate placements are not yet validated per-hypothesis."),
    ]),
    ("External Validity", ACCENT_ORANGE, [
        ("Cluster scale",
         "5-node cluster (1 control plane @ 12 GiB + 4 uniform 4-GiB workers, 10 vCPU, 28 GiB). "
         "Larger clusters may show different placement effects."),
        ("Fault classes",
         "Three classes tested: churn (pod-delete), load contention (Locust spike), node failure "
         "(drain). Hog faults are absorbed by cgroup limits (negative finding); network partitions "
         "and disk faults remain untested and may behave differently."),
        ("Traffic pattern",
         "Steady-state load (50 users, 10/s) for churn; a 200-user spike for the load regime. "
         "Production-like traffic patterns may affect results differently."),
        ("Metric portability",
         "PSI requires cgroup-v2, Felix requires Calico, etcd_debugging_* is K8s-version-"
         "fragile. metricAvailability surfaces which metrics were collected per run."),
    ]),
]

for col, (category, clr, items) in enumerate(threats):
    x = 0.5 + col * 6.4
    add_rounded_box(slide, x, 1.5, 6.0, 5.5, VERY_DARK,
                    border_color=clr, border_width=Pt(2))
    add_text_box(slide, x + 0.2, 1.55, 5.6, 0.35, category,
                 font_size=16, bold=True, color=clr)

    spacing = 1.6 if len(items) <= 3 else 1.3
    desc_height = 1.0 if len(items) <= 3 else 0.85
    for j, (threat_title, threat_desc) in enumerate(items):
        ty = 2.1 + j * spacing
        add_text_box(slide, x + 0.2, ty, 5.6, 0.3, threat_title,
                     font_size=13, bold=True, color=WHITE)
        add_text_box(slide, x + 0.2, ty + 0.35, 5.6, desc_height, threat_desc,
                     font_size=11, color=LIGHT_GRAY)


# ══════════════════════════════════════════════════════════════════════
# SLIDE 16 — CONCLUSION & FUTURE WORK
# ══════════════════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)
slide_title(slide, "Conclusion & Future Work")

# Key contributions
add_rounded_box(slide, 0.5, 1.5, 5.8, 3.2, VERY_DARK,
                border_color=ACCENT_GREEN)
add_text_box(slide, 0.7, 1.5, 5.4, 0.35, "Contributions",
             font_size=18, bold=True, color=ACCENT_GREEN)
add_bullet_frame(slide, 0.7, 1.95, 5.4, 2.6, [
    "• ChaosProbe framework: automated placement-\n"
    "  aware chaos evaluation for Kubernetes",
    "• A fault-class × measurement-layer study:\n"
    "  churn, load contention, node failure across\n"
    "  score, mechanism, and user layers (H1–H6)",
    "• The Neo4j dependency graph made analytically\n"
    "  load-bearing: a pre-chaos placement predictor (H5)",
    "• Statistical & provenance discipline: ICC variance\n"
    "  partition, TOST, power analysis, doctor-gated runs",
], font_size=12, color=LIGHT_GRAY)

# Key findings
add_rounded_box(slide, 6.8, 1.5, 5.8, 3.2, VERY_DARK,
                border_color=ACCENT_BLUE)
add_text_box(slide, 7.0, 1.5, 5.4, 0.35, "Key Findings",
             font_size=18, bold=True, color=ACCENT_BLUE)
add_bullet_frame(slide, 7.0, 1.95, 5.4, 2.6, [
    "• H1: the aggregate score cannot rank placements\n"
    "  (3.3% between-strategy variance)",
    "• H2 + H4: placement moves mechanism-layer\n"
    "  signals in both churn and load regimes",
    "• H3: those mechanisms do not reach the user\n"
    "  (TOST-decoupled; control-route confound)",
    "• H5 + H6: placement bites users at the availability\n"
    "  layer — and both faces of the trade-off are\n"
    "  predictable from the dependency graph",
], font_size=12, color=LIGHT_GRAY)

# Future work
add_rounded_box(slide, 0.5, 4.9, 12.1, 1.6, VERY_DARK,
                border_color=ACCENT_ORANGE)
add_text_box(slide, 0.7, 4.9, 11.7, 0.35, "Future Work",
             font_size=16, bold=True, color=ACCENT_ORANGE)
add_bullet_frame(slide, 0.7, 5.3, 5.6, 1.15, [
    "• Multi-replica anti-affinity — the production question\n  this single-replica design structurally excludes",
    "• H6 gradient: intermediate placements between the\n  extremes (6-strategy gradient run in flight)",
    "• Larger clusters, other CNIs / kube-proxy modes,\n  production-like traffic",
], font_size=11, color=LIGHT_GRAY)
add_bullet_frame(slide, 6.7, 5.3, 5.7, 1.15, [
    "• Re-attribute the conntrack flush to its exact code\n  path (upstream active flush is UDP-only; this\n  workload is TCP/gRPC)",
    "• More load batches — does any user-layer effect\n  survive replication?",
    "• Scheduler integration of the cross-node-fraction\n  predictor (H5)",
], font_size=11, color=LIGHT_GRAY)

# Core message
add_rounded_box(slide, 0.5, 6.7, 12.1, 0.7, VERY_DARK,
                border_color=ACCENT_BLUE, border_width=Pt(3))
add_text_box(slide, 0.7, 6.75, 11.7, 0.6,
    "A single score is blind to placement (H1). Placement acts at the mechanism layer (H2, H4) without reaching "
    "the user (H3); where it does reach users is availability under node failure — predictably (H5 + H6).",
    font_size=13, bold=True, color=WHITE, alignment=PP_ALIGN.CENTER)


# ══════════════════════════════════════════════════════════════════════
# SLIDE 17 — QUESTIONS
# ══════════════════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)

add_text_box(slide, 1.5, 2.0, 10.3, 1.2, "Thank You",
             font_size=48, bold=True, color=WHITE, alignment=PP_ALIGN.CENTER)

dline = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE,
    Inches(5.0), Inches(3.3), Inches(3.3), Pt(3))
dline.fill.solid()
dline.fill.fore_color.rgb = ACCENT_BLUE
dline.line.fill.background()

add_text_box(slide, 1.5, 3.7, 10.3, 0.8, "Questions?",
             font_size=36, color=ACCENT_BLUE, alignment=PP_ALIGN.CENTER)

# Summary badges at bottom
summary_items = [
    ("8", "Placement\nStrategies", CLR_ORCH),
    ("3", "Fault\nClasses", CLR_METRICS),
    ("H1–H6", "Hypotheses\nTested", ACCENT_BLUE),
    ("ρ = 0.79", "Graph\nPredictor", ACCENT_GREEN),
]
for i, (val, label, clr) in enumerate(summary_items):
    x = 2.5 + i * 2.3
    add_rounded_box(slide, x, 5.2, 1.8, 1.0, VERY_DARK,
                    border_color=clr)
    add_text_box(slide, x, 5.2, 1.8, 0.5, val,
                 font_size=24, bold=True, color=clr,
                 alignment=PP_ALIGN.CENTER)
    add_text_box(slide, x, 5.65, 1.8, 0.5, label,
                 font_size=11, color=LIGHT_GRAY, alignment=PP_ALIGN.CENTER)


# ══════════════════════════════════════════════════════════════════════
# SAVE
# ══════════════════════════════════════════════════════════════════════
output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "ChaosProbe_Presentation.pptx")
prs.save(output_path)
print(f"Presentation saved to: {output_path}")
print(f"Total slides: {len(prs.slides)}")