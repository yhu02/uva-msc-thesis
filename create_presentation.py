#!/usr/bin/env python3
"""Generate ChaosProbe thesis defense PowerPoint presentation.

Slide structure follows standard thesis defense format:
  1. Title
  2. Background & Motivation
  3. Research Question & Hypotheses
  4. Related Work
  5. Placement Strategies (independent variable)
  6. Experimental Setup
  7. ChaosProbe Framework (architecture + lifecycle)
  8. Measurement Design (probers + probes + scoring)
  9. Results — Resilience Scores
 10. Results — Recovery Time & Latency
 11. Results — Resources & Throughput
 12. Discussion (hypothesis evaluation)
 13. Threats to Validity
 14. Conclusion & Future Work
 15. Questions
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
    """Find the most recent results directory containing charts."""
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "chaosprobe", "results")
    dirs = sorted(glob.glob(os.path.join(base, "*/charts")), reverse=True)
    return dirs[0] if dirs else None


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
    "injects faults via LitmusChaos, and measures recovery time, inter-service latency, "
    "resource utilization, and I/O throughput to quantify placement's impact on resilience.",
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
    "How do multi-dimensional metrics decompose chaos response across pod-placement strategies in Kubernetes?",
    font_size=16, bold=True, color=WHITE, alignment=PP_ALIGN.CENTER)

# Theme group labels (above each theme's card group)
group_labels = [
    ("Primary results — metrics", ACCENT_RED, 0.35, 2.55),
    ("Methodology", ACCENT_BLUE, 6.65, 2.55),
    ("Context & support", ACCENT_ORANGE, 8.75, 2.55),
]
for label, clr, x, y in group_labels:
    add_text_box(slide, x, y, 4, 0.25, label,
                 font_size=11, bold=True, color=clr)

# Six hypotheses — single row. The thesis is built on the primary-source
# metrics (M1-M3), not the resilience score: those reproduce across the 13
# collected runs. The score is demoted to the decoupling finding (M4).
hypotheses = [
    # Primary results — reproducible metric outcomes (M1-M3, red)
    ("M1", "Spreading flushes conn-state",
     "Primary metric. Under churn, spread/default flush 36-39% of "
     "node conntrack entries; colocate stays flat (−1.6%). 12/12 "
     "runs. The reproducible fault-response signal.",
     ACCENT_RED),
    ("M2", "Co-location lowers CPU contention",
     "Colocate throttles less than default/spread (during-chaos rate "
     "1.54 vs 1.90, 1.94), with lower CPU usage/pressure. 11/13 runs. "
     "Contention does not scale with density under churn.",
     ACCENT_RED),
    ("M3", "'Spread is safer' is refuted",
     "Both reproducible metrics favour co-location under churn. The "
     "literature's spread-isolation prescription is refuted at the "
     "metric level — not merely unmeasurable on the score.",
     ACCENT_RED),
    # Methodology (M4, blue)
    ("M4", "Score decoupled from metrics",
     "Where conntrack & CPU reproduce (≥11/13), the resilience "
     "score does NOT (33-89 across runs). The binary-probe score is "
     "a lossy instrument; the metrics are the reliable outcome.",
     ACCENT_BLUE),
    # Context & support (S1-S2, orange/purple)
    ("S1", "Mechanism signal is churn-specific",
     "cpu-hog (n=2) does not reproduce: one run scored ~100 across "
     "strategies, one saw many iterations fail (33-67, σ≈58). The "
     "score is noisy under both faults; the reproducible M1/M2 signal "
     "is churn-specific.",
     ACCENT_ORANGE),
    ("S2", "Recovery split is unstable",
     "The d2s/s2r split is run-dependent: app-startup dominates in "
     "some runs (84-96%), the scheduling term in others (up to ~78%). "
     "Recovery decomposition is not a stable placement signal.",
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
    # Coloured header strip with the M/S label
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
    "Each hypothesis is testable from ChaosProbe-collected metrics; "
    "the data column reports current evidence.",
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
    "• ChaosProbe bridges this gap:\n  6 strategies × 2 faults × 4 metrics",
    "• Graph storage preserves causal\n  topology for analysis",
], font_size=11, color=LIGHT_GRAY)

# Contention categories table
add_text_box(slide, 0.5, 6.3, 12, 0.3,
             "Expected Contention by Placement (Literature-Informed Hypotheses)",
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
             "Analytic weight: the reproducible findings (M1 conntrack flush, M2 CPU throttling) rest on the colocate "
             "vs. spread/default locality contrast. Random, adversarial, best-fit & dependency-aware are a generality "
             "check — designed for the contention hypothesis, present in only half the run set, they widen the placements "
             "the noisy score still can't rank (M4) and await the cpu-hog contention matrix.",
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
add_text_box(slide, 0.7, 5.2, 5.6, 0.3, "Multi-Fault Placement Matrix",
             font_size=14, bold=True, color=CLR_CHAOS)
exp_data = [
    ["", "Churn (pod-delete)", "Contention (pod-cpu-hog)"],
    ["Target", "productcatalogservice", "productcatalogservice"],
    ["Duration", "120s (CHAOS_INTERVAL=15s)", "120s (1 core, 100% load)"],
    ["Probes", "7 httpProbes + 5 cmdProbes", "Same probe set"],
    ["Role", "Tests churn-class story", "Tests if literature returns"],
]
add_table(slide, 0.7, 5.55, 5.6, 1.6, 5, 3, exp_data,
          font_size=9, header_color=CLR_CHAOS)

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
# SLIDE 9 — RESULTS: RESILIENCE SCORES
# ══════════════════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)
slide_title(slide, "Results — Why the Score Is Demoted (M4)")

charts_dir = _find_latest_charts_dir()

# Score distribution across runs — the M4 chart (overlapping boxes = no ranking)
img_path = os.path.join(charts_dir, "score_distribution.png") if charts_dir else None
add_image_or_placeholder(slide, 0.5, 1.4, 7.5, 4.5, img_path,
                         "[Resilience Score Distribution Across Runs]\n\n"
                         "Box plot of resilience score (0–100%) per\n"
                         "strategy across all churn runs — the boxes\n"
                         "overlap, so the score cannot rank placements.\n\n"
                         "Generated by: scripts/distribution_charts.py")

# Key observations — right
add_rounded_box(slide, 8.5, 1.4, 4.3, 4.5, VERY_DARK,
                border_color=ACCENT_BLUE, border_width=Pt(2))
add_text_box(slide, 8.7, 1.45, 3.9, 0.3, "Key Observations",
             font_size=16, bold=True, color=ACCENT_BLUE)
add_bullet_frame(slide, 8.7, 1.85, 3.9, 3.8, [
    "• Baseline: 100% (stddev 0) —\n  methodology control holds",
    "• The score does NOT reproduce across the 13\n  churn runs (≥3 iters, post-fix, baseline=100):",
    "    colocate 49.7–83  (mean 69.5)\n    spread   33–88.7  (mean 70.5)\n    default  33–83    (mean 58.9)",
    "• Within-strategy stddev (11–17) dwarfs\n  the colocate-vs-spread gap (~1 point)",
    "• So the aggregate score cannot rank\n  placements — the signal is elsewhere",
], font_size=10, color=LIGHT_GRAY)

# Hypothesis check
add_rounded_box(slide, 0.5, 6.2, 12.3, 1.0, VERY_DARK,
                border_color=ACCENT_ORANGE, border_width=Pt(2))
add_text_box(slide, 0.7, 6.2, 11.9, 0.3, "M4 — the score is decoupled from the reproducible metrics",
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
    "The aggregate score is not reproducible run-to-run, so it cannot adjudicate L1–L3. "
    "They are resolved instead at the mechanism layer (next slides), where the signal IS stable.",
    font_size=10, color=LIGHT_GRAY)


# ══════════════════════════════════════════════════════════════════════
# SLIDE 10 — RESULTS: RECOVERY TIME & LATENCY
# ══════════════════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)
slide_title(slide, "Results — Recovery Time & Latency")

# Recovery times chart — left
img_path = os.path.join(charts_dir, "recovery_times.png") if charts_dir else None
add_image_or_placeholder(slide, 0.3, 1.4, 6.2, 3.5, img_path,
                         "[Recovery Times by Strategy]\n\n"
                         "Box plot: pod deletion → ready (ms)\n"
                         "per placement strategy.\n\n"
                         "Generated by: chaosprobe visualize")
add_text_box(slide, 0.3, 4.95, 6.2, 0.3, "Recovery Time (pod deletion → ready)",
             font_size=12, bold=True, color=ACCENT_BLUE, alignment=PP_ALIGN.CENTER)

# Latency degradation chart — right
img_path = os.path.join(charts_dir, "latency_degradation.png") if charts_dir else None
add_image_or_placeholder(slide, 6.8, 1.4, 6.2, 3.5, img_path,
                         "[Latency Degradation: Pre vs During Chaos]\n\n"
                         "Grouped bar chart: HTTP route latency\n"
                         "pre-chaos vs during-chaos per strategy.\n\n"
                         "Generated by: chaosprobe visualize")
add_text_box(slide, 6.8, 4.95, 6.2, 0.3, "Latency Degradation (Pre-Chaos vs During-Chaos)",
             font_size=12, bold=True, color=ACCENT_BLUE, alignment=PP_ALIGN.CENTER)

# Analysis — bottom
add_rounded_box(slide, 0.3, 5.5, 6.2, 1.8, VERY_DARK,
                border_color=ACCENT_RED)
add_text_box(slide, 0.5, 5.5, 5.8, 0.3, "Recovery split is unstable (S2)",
             font_size=14, bold=True, color=ACCENT_RED)
add_bullet_frame(slide, 0.5, 5.85, 5.8, 1.3, [
    "• Recovery = deletion→scheduled + scheduled→ready;\n  their split is run-dependent",
    "• app-startup dominates in some runs (84–96%),\n  the scheduling term in others (up to ~78%)",
    "• Either way recovery rank is noise run-to-run — it\n  does not track the placement story (refutes L3)",
], font_size=11, color=LIGHT_GRAY)

add_rounded_box(slide, 6.8, 5.5, 6.2, 1.8, VERY_DARK,
                border_color=ACCENT_ORANGE)
add_text_box(slide, 7.0, 5.5, 5.8, 0.3, "The fault signal is in the tail",
             font_size=14, bold=True, color=ACCENT_ORANGE)
add_bullet_frame(slide, 7.0, 5.85, 5.8, 1.3, [
    "• Targeted route during chaos: mean 231 ms but\n  p95 619 ms and max 3334 ms — tail is 14× the mean",
    "• Routes not depending on the target stay flat at\n  70–110 ms — impact is route-specific, not node-wide",
    "• Mean-based SLOs would miss the fault entirely\n  (Dean & Barroso, Tail at Scale)",
], font_size=11, color=LIGHT_GRAY)


# ══════════════════════════════════════════════════════════════════════
# SLIDE 11 — RESULTS: RESOURCES & THROUGHPUT
# ══════════════════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)
slide_title(slide, "Results — Primary Metrics: Conntrack & CPU (M1, M2)")

# Mechanism distribution — left: the actual M1 (conntrack) + M2 (throttle) signal
img_path = os.path.join(charts_dir, "mechanism_distribution.png") if charts_dir else None
add_image_or_placeholder(slide, 0.3, 1.4, 6.2, 3.5, img_path,
                         "[Conntrack flush & CPU throttle distributions]\n\n"
                         "Box plots per strategy across churn runs:\n"
                         "conntrack flush % (M1) and during-chaos\n"
                         "throttle rate (M2) — tight, reproducible.\n\n"
                         "Generated by: scripts/distribution_charts.py")
add_text_box(slide, 0.3, 4.95, 6.2, 0.3, "Conntrack Churn & CPU Throttling (M1, M2)",
             font_size=12, bold=True, color=ACCENT_BLUE, alignment=PP_ALIGN.CENTER)

# Throughput chart — right
img_path = os.path.join(charts_dir, "throughput_by_strategy.png") if charts_dir else None
add_image_or_placeholder(slide, 6.8, 1.4, 6.2, 3.5, img_path,
                         "[I/O Throughput by Strategy]\n\n"
                         "Redis ops/s and disk R/W bytes/s\n"
                         "per placement strategy.\n\n"
                         "Generated by: chaosprobe visualize")
add_text_box(slide, 6.8, 4.95, 6.2, 0.3, "I/O Throughput (Redis ops/s, Disk bytes/s)",
             font_size=12, bold=True, color=ACCENT_BLUE, alignment=PP_ALIGN.CENTER)

# Analysis — bottom
add_rounded_box(slide, 0.3, 5.5, 6.2, 1.8, VERY_DARK,
                border_color=CLR_METRICS)
add_text_box(slide, 0.5, 5.5, 5.8, 0.3, "M2 — co-location lowers CPU contention",
             font_size=14, bold=True, color=CLR_METRICS)
add_bullet_frame(slide, 0.5, 5.85, 5.8, 1.3, [
    "• Densest placement (colocate) throttles less than\n  default/spread — during-chaos rate 1.54 vs 1.90, 1.94",
    "• colocate < default in 11 of 13 runs — reproducible",
    "• Opposite of Bubble-Up's dense=more-contention; the\n  contention model does not fit a churn fault",
], font_size=11, color=LIGHT_GRAY)

add_rounded_box(slide, 6.8, 5.5, 6.2, 1.8, VERY_DARK,
                border_color=CLR_OUTPUT)
add_text_box(slide, 7.0, 5.5, 5.8, 0.3, "M1 — spreading flushes conn-state",
             font_size=14, bold=True, color=CLR_OUTPUT)
add_bullet_frame(slide, 7.0, 5.85, 5.8, 1.3, [
    "• Spread/default flush 36–39% of node conntrack\n  entries during churn; colocate stays flat (−1.6%)",
    "• Reproducible in all 12 runs measured — the primary,\n  large-effect fault-response metric",
    "• Spreading maximises the cross-node flows pod churn\n  tears down — both metrics favour co-location (M3)",
], font_size=11, color=LIGHT_GRAY)


# ══════════════════════════════════════════════════════════════════════
# SLIDE 12 — DISCUSSION
# ══════════════════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)
slide_title(slide, "Discussion")

# Strategy comparison heatmap — left
img_path = os.path.join(charts_dir, "strategy_comparison_heatmap.png") if charts_dir else None
add_image_or_placeholder(slide, 0.3, 1.4, 6.5, 3.5, img_path,
                         "[Strategy Comparison Heatmap]\n\n"
                         "All thesis dimensions normalised to 0-1.\n"
                         "Green = better, Red = worse.\n\n"
                         "Generated by: chaosprobe visualize")

# Hypothesis evaluation — right
add_text_box(slide, 7.2, 1.4, 5.5, 0.3, "Hypothesis Evaluation",
             font_size=16, bold=True, color=ACCENT_BLUE)

hyp_results = [
    ("L1", "Colocate = worst resilience",
     "Score can't adjudicate (colocate 49.7–83\n"
     "across runs). Mechanism: colocate throttles\n"
     "LEAST and flushes no conn-state — not worst.",
     ACCENT_ORANGE, "Mech."),
    ("L2", "Spread = best fault isolation",
     "Score can't adjudicate (spread 33–88.7).\n"
     "Mechanism: spread flushes 28–52% of conntrack\n"
     "under churn — it amplifies, not isolates.",
     ACCENT_ORANGE, "Mech."),
    ("L3", "Recovery time predicts score",
     "Refuted structurally: the d2s/s2r split is\n"
     "unstable run-to-run and the score is\n"
     "non-reproducible. No stable relationship.",
     ACCENT_RED, "Refuted"),
]

for i, (label, title, explanation, clr, verdict) in enumerate(hyp_results):
    y = 1.85 + i * 1.1
    add_rounded_box(slide, 7.2, y, 0.7, 0.8, clr, label, 14, WHITE, True)
    add_rounded_box(slide, 8.1, y, 4.6, 0.8, VERY_DARK,
                    border_color=clr, border_width=Pt(2))
    add_text_box(slide, 8.25, y + 0.02, 3.3, 0.25, title,
                 font_size=12, bold=True, color=clr)
    add_text_box(slide, 8.25, y + 0.3, 3.3, 0.45, explanation,
                 font_size=10, color=LIGHT_GRAY)
    add_rounded_box(slide, 11.5, y + 0.2, 1.1, 0.35, clr,
                    verdict, 10, WHITE, True)

# Key insights
add_text_box(slide, 0.5, 5.6, 12, 0.3, "Key Insights",
             font_size=18, bold=True, color=ACCENT_ORANGE)

add_rounded_box(slide, 0.5, 6.0, 3.9, 1.2, VERY_DARK,
                border_color=ACCENT_RED)
add_text_box(slide, 0.7, 6.0, 3.5, 0.3, "M4 — score is the wrong instrument",
             font_size=13, bold=True, color=ACCENT_RED)
add_text_box(slide, 0.7, 6.3, 3.5, 0.8,
    "The aggregate resilience score is not "
    "reproducible: the same strategy spans "
    "33–89 across 13 runs. It cannot rank "
    "placements — the binary-probe scoring "
    "discards the signal.",
    font_size=10, color=LIGHT_GRAY)

add_rounded_box(slide, 4.7, 6.0, 3.9, 1.2, VERY_DARK,
                border_color=ACCENT_BLUE)
add_text_box(slide, 4.9, 6.0, 3.5, 0.3, "M1/M2 — metrics reproduce",
             font_size=13, bold=True, color=ACCENT_BLUE)
add_text_box(slide, 4.9, 6.3, 3.5, 0.8,
    "Where the score is noise, the kernel/network "
    "metrics are stable: conntrack flush (spread ≫ "
    "colocate, 12/12 runs) and CPU throttling "
    "(colocate < default, 11/13). These carry the "
    "placement signal the score loses.",
    font_size=10, color=LIGHT_GRAY)

add_rounded_box(slide, 8.9, 6.0, 3.9, 1.2, VERY_DARK,
                border_color=ACCENT_GREEN)
add_text_box(slide, 9.1, 6.0, 3.5, 0.3, "M3 — churn-driven, not contention",
             font_size=13, bold=True, color=ACCENT_GREEN)
add_text_box(slide, 9.1, 6.3, 3.5, 0.8,
    "Pod-delete tears down cross-node conntrack "
    "state (measured directly). Cross-node hops "
    "exposed to the kill cycle become failure "
    "surfaces; co-located, same-node paths stay "
    "kernel-local and are spared.",
    font_size=10, color=LIGHT_GRAY)


# ══════════════════════════════════════════════════════════════════════
# SLIDE 13 — THREATS TO VALIDITY
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
        ("Uneven strategy coverage",
         "Reproducible findings rest on 3 configs (colocate, spread, default). The 4 contention "
         "strategies (random, adversarial, best-fit, dependency-aware) appear in half the runs and no "
         "reproducible finding — a generality check, not validated signals, pending more cpu-hog runs."),
    ]),
    ("External Validity", ACCENT_ORANGE, [
        ("Cluster scale",
         "5-node cluster (1 control plane @ 12 GiB + 4 uniform 4-GiB workers, 10 vCPU, 28 GiB). "
         "Larger clusters may show different placement effects."),
        ("Fault types",
         "Only pod-delete and pod-cpu-hog tested. Network partitions, disk faults, "
         "and memory pressure may reveal different strategy rankings."),
        ("Traffic pattern",
         "Steady-state load (50 users, 10/s). Bursty, ramping, or production-like "
         "traffic patterns may affect results differently."),
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
# SLIDE 14 — CONCLUSION & FUTURE WORK
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
    "  aware chaos testing for Kubernetes",
    "• Systematic evaluation of 6 placement strategies\n"
    "  under 2 fault types across 4 metric dimensions",
    "• Neo4j graph storage preserving causal\n"
    "  relationships for topology-aware analysis",
    "• Reproducible methodology: seeded randomness,\n"
    "  exact configs, automated comparison pipeline",
], font_size=12, color=LIGHT_GRAY)

# Key findings
add_rounded_box(slide, 6.8, 1.5, 5.8, 3.2, VERY_DARK,
                border_color=ACCENT_BLUE)
add_text_box(slide, 7.0, 1.5, 5.4, 0.35, "Key Findings",
             font_size=18, bold=True, color=ACCENT_BLUE)
add_bullet_frame(slide, 7.0, 1.95, 5.4, 2.6, [
    "• Aggregate resilience score is NOT\n"
    "  reproducible (strategy spans 33–89/13 runs)",
    "• But the mechanism layer IS: conntrack flush\n"
    "  (spread≫colocate, 12/12) + throttling (11/13)",
    "• Mechanism: pod-delete is churn-based, not\n"
    "  contention — co-location keeps paths local",
    "• Recovery's d2s/s2r split is unstable run-to-run,\n"
    "  so it can't predict the outcome",
], font_size=12, color=LIGHT_GRAY)

# Future work
add_rounded_box(slide, 0.5, 4.9, 12.1, 1.6, VERY_DARK,
                border_color=ACCENT_ORANGE)
add_text_box(slide, 0.7, 4.9, 11.7, 0.35, "Future Work",
             font_size=16, bold=True, color=ACCENT_ORANGE)
add_bullet_frame(slide, 0.7, 5.3, 5.6, 1.15, [
    "• Multi-replica services — does locality still win\n  when restart can happen on a peer pod?",
    "• Larger cluster (20+ nodes), production-like traffic\n  & service-mesh instrumentation (Istio/Linkerd)",
    "• Memory- and network-fault classes beyond CPU",
], font_size=11, color=LIGHT_GRAY)
add_bullet_frame(slide, 6.7, 5.3, 5.7, 1.15, [
    "• Per-fault-class placement guidance — choose strategy\n  by expected fault type, not by general 'best practice'",
    "• ML-based anomaly detection on collected dataset\n  (the Neo4j store is already structured for it)",
    "• Per-fault-class extensions to Borg / Medea schedulers",
], font_size=11, color=LIGHT_GRAY)

# Core message
add_rounded_box(slide, 0.5, 6.7, 12.1, 0.7, VERY_DARK,
                border_color=ACCENT_BLUE, border_width=Pt(3))
add_text_box(slide, 0.7, 6.75, 11.7, 0.6,
    "Placement-vs-resilience intuition from the literature is fault-class-specific. Under churn-based faults "
    "(pod-delete), co-location wins because it minimises cross-node disruption during the kill cycle.",
    font_size=14, bold=True, color=WHITE, alignment=PP_ALIGN.CENTER)


# ══════════════════════════════════════════════════════════════════════
# SLIDE 15 — QUESTIONS
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
    ("6", "Placement\nStrategies", CLR_ORCH),
    ("4", "Metric\nDimensions", CLR_METRICS),
    ("3/3", "Refuted at\nmechanism layer", ACCENT_RED),
    ("1", "Unified\nMechanism", ACCENT_BLUE),
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