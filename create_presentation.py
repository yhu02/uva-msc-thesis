#!/usr/bin/env python3
"""Generate ChaosProbe PowerPoint presentation.

Restructured slide deck focusing on:
- Research question (not problem statement)
- Placement strategies moved to introduction
- Simplified architecture (max 6 components per area)
- Merged & simplified data flow
- Verified Neo4j schema (14 node types, 18 relationships)
- Expanded contention categories with literature references
- Exact replicable experiment configurations
- Embedded visualization charts from results
- No AI pipeline / autonomous feedback loop (out of scope)
- Focus on impact of placement strategy on performance
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

add_text_box(slide, 1.5, 1.5, 10.3, 1.2, "ChaosProbe",
             font_size=54, bold=True, color=WHITE, alignment=PP_ALIGN.CENTER)
add_text_box(slide, 1.5, 2.7, 10.3, 1.0,
             "Measuring the Impact of Chaos in Differing\n"
             "Placement Strategies within Cloud Systems",
             font_size=24, color=ACCENT_BLUE, alignment=PP_ALIGN.CENTER)

dline = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE,
    Inches(4.5), Inches(3.9), Inches(4.3), Pt(3))
dline.fill.solid()
dline.fill.fore_color.rgb = ACCENT_BLUE
dline.line.fill.background()

add_text_box(slide, 1.5, 4.3, 10.3, 0.6,
             "MSc Thesis — University of Amsterdam",
             font_size=18, color=LIGHT_GRAY, alignment=PP_ALIGN.CENTER)
add_text_box(slide, 1.5, 4.9, 10.3, 0.5,
             "April 2026",
             font_size=14, color=MID_GRAY, alignment=PP_ALIGN.CENTER)

metrics = [
    ("6", "Placement\nStrategies"),
    ("2", "Fault\nTypes"),
    ("6", "Continuous\nProbers"),
    ("504", "Unit\nTests"),
]
for i, (val, label) in enumerate(metrics):
    x = 2.5 + i * 2.3
    add_rounded_box(slide, x, 5.6, 1.8, 1.0, VERY_DARK,
                    border_color=ACCENT_BLUE)
    add_text_box(slide, x, 5.6, 1.8, 0.5, val,
                 font_size=24, bold=True, color=ACCENT_BLUE,
                 alignment=PP_ALIGN.CENTER)
    add_text_box(slide, x, 6.05, 1.8, 0.5, label,
                 font_size=11, color=LIGHT_GRAY, alignment=PP_ALIGN.CENTER)


# ══════════════════════════════════════════════════════════════════════
# SLIDE 2 — RESEARCH QUESTION & MOTIVATION
# ══════════════════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)
slide_title(slide, "Research Question & Motivation")

# Research question
add_rounded_box(slide, 0.6, 1.5, 12.1, 1.2, VERY_DARK,
                border_color=ACCENT_BLUE, border_width=Pt(3))
add_text_box(slide, 0.8, 1.55, 11.7, 0.35, "Research Question",
             font_size=14, bold=True, color=ACCENT_BLUE)
add_text_box(slide, 0.8, 1.95, 11.7, 0.6,
    "How does pod placement topology affect microservice resilience\n"
    "under fault injection in Kubernetes?",
    font_size=20, bold=True, color=WHITE, alignment=PP_ALIGN.CENTER)

# Dimensions
add_text_box(slide, 0.6, 2.9, 12, 0.3, "Measured Dimensions",
             font_size=16, bold=True, color=ACCENT_GREEN)

dimensions = [
    ("Recovery Time", "Pod deletion → scheduled → ready\n(milliseconds per cycle)",
     CLR_CHAOS, 0.6),
    ("Inter-Service Latency", "HTTP route response time\nduring/after fault injection",
     CLR_METRICS, 3.4),
    ("Resource Utilization", "Node/pod CPU & memory\nunder contention",
     CLR_ORCH, 6.2),
    ("I/O Throughput", "Redis ops/s, disk sequential\nread/write bytes/s",
     CLR_OUTPUT, 9.0),
]
for name, desc, clr, x in dimensions:
    add_rounded_box(slide, x, 3.3, 2.5, 1.1, VERY_DARK,
                    border_color=clr)
    add_text_box(slide, x + 0.1, 3.3, 2.3, 0.3, name,
                 font_size=13, bold=True, color=clr)
    add_text_box(slide, x + 0.1, 3.65, 2.3, 0.7, desc,
                 font_size=11, color=LIGHT_GRAY)

# Simplified approach
add_rounded_box(slide, 0.6, 4.7, 12.1, 0.8, VERY_DARK,
                border_color=ACCENT_BLUE)
add_text_box(slide, 0.8, 4.75, 11.7, 0.3, "Approach",
             font_size=16, bold=True, color=ACCENT_BLUE)
approach_steps = [
    ("Deploy", CLR_CLI, 0.8),
    ("Place", CLR_ORCH, 3.0),
    ("Inject", CLR_CHAOS, 5.2),
    ("Measure", CLR_METRICS, 7.4),
    ("Compare", CLR_OUTPUT, 9.6),
]
for name, clr, x in approach_steps:
    add_rounded_box(slide, x, 5.05, 1.8, 0.35, clr, name, 12, WHITE, True)
for i in range(len(approach_steps) - 1):
    x1 = approach_steps[i][2] + 1.8
    x2 = approach_steps[i + 1][2]
    add_arrow(slide, x1, 5.22, x2, 5.22, LIGHT_GRAY, Pt(2))

# Hypotheses
add_rounded_box(slide, 0.6, 5.8, 12.1, 1.3, VERY_DARK,
                border_color=ACCENT_GREEN)
add_text_box(slide, 0.8, 5.8, 11.7, 0.3, "Hypotheses",
             font_size=16, bold=True, color=ACCENT_GREEN)
add_bullet_frame(slide, 0.8, 6.15, 11.7, 0.9, [
    "H1: Colocating all pods on one node causes worst resilience (maximum contention for CPU, memory, I/O, network)",
    "H2: Spreading pods across nodes minimizes contention but may increase cross-node network latency",
    "H3: Baseline (no real fault) should score 100% — any degradation indicates pre-existing instability",
], font_size=12, color=LIGHT_GRAY)


# ══════════════════════════════════════════════════════════════════════
# SLIDE 3 — INTRODUCTION: MICROSERVICES & THE PROBLEM
# ══════════════════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)
slide_title(slide, "Microservices & The Problem")

# What are microservices
add_rounded_box(slide, 0.6, 1.5, 5.8, 2.5, VERY_DARK,
                border_color=CLR_CLI)
add_text_box(slide, 0.8, 1.5, 5.4, 0.35, "What Are Microservices?",
             font_size=16, bold=True, color=CLR_CLI)
add_bullet_frame(slide, 0.8, 1.9, 5.4, 2.0, [
    "• Decomposed application: each service has a\n  single responsibility and own lifecycle",
    "• Communicate via network (HTTP / gRPC / Redis)",
    "• Deployed as containers on Kubernetes",
    "• Independently scalable and deployable",
], font_size=12, color=LIGHT_GRAY)

# The problem
add_rounded_box(slide, 6.8, 1.5, 5.8, 2.5, VERY_DARK,
                border_color=ACCENT_RED)
add_text_box(slide, 7.0, 1.5, 5.4, 0.35, "The Problem",
             font_size=16, bold=True, color=ACCENT_RED)
add_bullet_frame(slide, 7.0, 1.9, 5.4, 2.0, [
    "• Kubernetes scheduler optimizes for resource fit,\n  not for resilience or fault isolation",
    "• Pod placement determines which services share\n  node resources (CPU, memory, disk, network)",
    "• Co-located services suffer correlated failures:\n  one fault cascades to neighbors on the same node",
], font_size=12, color=LIGHT_GRAY)

# What we investigated
add_rounded_box(slide, 0.6, 4.3, 5.8, 1.5, VERY_DARK,
                border_color=ACCENT_ORANGE)
add_text_box(slide, 0.8, 4.3, 5.4, 0.35, "What We Investigated",
             font_size=16, bold=True, color=ACCENT_ORANGE)
add_bullet_frame(slide, 0.8, 4.7, 5.4, 1.0, [
    "• 6 placement strategies × 2 fault types",
    "• Measured: recovery time, latency, resources, I/O",
    "• 12-microservice application (Google Online Boutique)",
], font_size=12, color=LIGHT_GRAY)

# Our solution
add_rounded_box(slide, 6.8, 4.3, 5.8, 1.5, VERY_DARK,
                border_color=ACCENT_GREEN)
add_text_box(slide, 7.0, 4.3, 5.4, 0.35, "Our Proposed Solution",
             font_size=16, bold=True, color=ACCENT_GREEN)
add_bullet_frame(slide, 7.0, 4.7, 5.4, 1.0, [
    "• ChaosProbe: automated framework to systematically\n  test placement strategies under chaos injection",
    "• Collect structured metrics, store in graph DB,\n  compare strategies quantitatively",
], font_size=12, color=LIGHT_GRAY)

# Chaos engineering context
add_rounded_box(slide, 0.6, 6.1, 12.1, 1.1, VERY_DARK,
                border_color=MID_GRAY)
add_text_box(slide, 0.8, 6.1, 11.7, 0.3, "Context: Chaos Engineering",
             font_size=14, bold=True, color=LIGHT_GRAY)
add_text_box(slide, 0.8, 6.45, 11.7, 0.7,
    "Chaos engineering is the discipline of experimenting on a system to build confidence in its "
    "ability to withstand turbulent conditions in production (Basiri et al., 2016). "
    "We extend this by systematically varying pod placement to isolate topology's effect on resilience.",
    font_size=12, color=MID_GRAY)


# ══════════════════════════════════════════════════════════════════════
# SLIDE 4 — PLACEMENT STRATEGIES (moved to introduction)
# ══════════════════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)
slide_title(slide, "Placement Strategies")

strat_data = [
    ("Baseline",
     "Default scheduler\nTrivial fault (1% CPU, 1s)\nControl group — no real disruption",
     CLR_INFRA,
     [(0.4, 0.2), (1.1, 0.2), (0.4, 0.6), (1.1, 0.6)],
     "None", "Expected: 100% score"),
    ("Default",
     "Default K8s scheduler\nFull chaos injection\nNo placement mutation",
     CLR_CLI,
     [(0.4, 0.2), (1.1, 0.2), (0.4, 0.6), (1.1, 0.6)],
     "Low", "Scheduler-determined placement"),
    ("Colocate",
     "ALL pods pinned to single node\nMax resource contention\nWorst-case scenario",
     CLR_CHAOS,
     [(0.4, 0.2), (0.65, 0.35), (0.4, 0.5), (0.65, 0.65)],
     "Maximum", "Expected: worst resilience"),
    ("Spread",
     "Round-robin distribution\nacross all worker nodes\nMinimal per-node contention",
     CLR_METRICS,
     [(0.2, 0.4), (0.6, 0.4), (1.0, 0.4), (1.4, 0.4)],
     "Minimum", "Expected: best resilience"),
    ("Random",
     "Random assignment per pod\nReproducible via seed\nUnpredictable contention",
     CLR_OUTPUT,
     [(0.3, 0.2), (1.0, 0.6), (0.3, 0.6), (1.0, 0.2)],
     "Variable", "Seed-based reproducibility"),
    ("Antagonistic",
     "Heavy pods → 1 node\nLight pods → remaining nodes\nIntentional CPU/mem hotspot",
     ACCENT_PURPLE,
     [(0.3, 0.2), (0.55, 0.35), (1.0, 0.4), (1.3, 0.6)],
     "High", "Resource-weighted placement"),
]

for i, (name, desc, clr, dots, contention, note) in enumerate(strat_data):
    col = i % 3
    row = i // 3
    bx = 0.5 + col * 4.2
    by = 1.5 + row * 2.9

    add_rounded_box(slide, bx, by, 3.8, 2.5, VERY_DARK,
                    border_color=clr, border_width=Pt(2))
    add_text_box(slide, bx + 0.1, by + 0.05, 3.6, 0.35, name,
                 font_size=16, bold=True, color=clr)
    add_text_box(slide, bx + 0.1, by + 0.4, 2.0, 0.9, desc,
                 font_size=11, color=LIGHT_GRAY)

    for dx, dy in dots:
        add_rounded_box(slide, bx + 2.0 + dx, by + 0.8 + dy, 0.25, 0.25, clr)

    cont_clr = (ACCENT_GREEN if contention in ("None", "Low", "Minimum")
                else ACCENT_RED if contention in ("Maximum", "High")
                else ACCENT_ORANGE)
    add_text_box(slide, bx + 0.1, by + 1.85, 3.6, 0.25,
                 f"Contention: {contention}", font_size=11, bold=True, color=cont_clr)
    add_text_box(slide, bx + 0.1, by + 2.1, 3.6, 0.3,
                 note, font_size=10, color=MID_GRAY)


# ══════════════════════════════════════════════════════════════════════
# SLIDE 5 — TARGET APPLICATION: ONLINE BOUTIQUE
# ══════════════════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)
slide_title(slide, "Target Application — Online Boutique")

add_text_box(slide, 0.5, 1.4, 6.0, 0.35,
             "12 Microservices — Service Dependency Graph",
             font_size=16, bold=True, color=ACCENT_BLUE)

# Frontend
add_rounded_box(slide, 3.5, 1.9, 2.0, 0.5, CLR_CLI,
                "frontend", 11, WHITE, True)

# Tier 2
tier2 = [
    ("productcatalog", 0.5, CLR_CHAOS),
    ("currency", 2.1, CLR_ORCH),
    ("cart", 3.7, CLR_ORCH),
    ("recommend.", 5.2, CLR_ORCH),
]
for name, x, clr in tier2:
    add_rounded_box(slide, x, 2.7, 1.3, 0.5, clr, name, 9, WHITE, True)
    add_arrow(slide, 4.5, 2.4, x + 0.65, 2.7, LIGHT_GRAY, Pt(1))

# Checkout
add_rounded_box(slide, 2.2, 3.5, 1.4, 0.5, CLR_ORCH,
                "checkout", 9, WHITE, True)
add_arrow(slide, 4.5, 2.4, 2.9, 3.5, LIGHT_GRAY, Pt(1))

# Redis
add_rounded_box(slide, 4.5, 3.5, 1.5, 0.5, ACCENT_RED,
                "redis-cart", 9, WHITE, True)
add_arrow(slide, 4.35, 3.2, 5.25, 3.5, LIGHT_GRAY, Pt(1))

# Bottom tier
bottom = [("email", 0.5), ("payment", 2.0), ("shipping", 3.5)]
for name, x in bottom:
    add_rounded_box(slide, x, 4.3, 1.3, 0.45, CLR_ORCH, name, 9, WHITE, True)
    add_arrow(slide, 2.9, 4.0, x + 0.65, 4.3, LIGHT_GRAY, Pt(1))

# Chaos target highlight (transparent fill, border only)
highlight = add_rounded_box(slide, 0.3, 2.5, 1.7, 0.9, DARK_BG,
                border_color=ACCENT_RED, border_width=Pt(3))
highlight.fill.background()
add_text_box(slide, 0.3, 3.45, 1.7, 0.3, "Chaos Target",
             font_size=10, bold=True, color=ACCENT_RED, alignment=PP_ALIGN.CENTER)

# Why this target
add_rounded_box(slide, 7.0, 1.5, 5.8, 2.0, VERY_DARK,
                border_color=ACCENT_RED)
add_text_box(slide, 7.2, 1.5, 5.4, 0.3, "Why productcatalogservice?",
             font_size=14, bold=True, color=ACCENT_RED)
add_bullet_frame(slide, 7.2, 1.85, 5.4, 1.5, [
    "• Central dependency: homepage, product pages,\n  recommendations, and search all depend on it",
    "• Maximum blast radius: failure cascades to\n  frontend and all downstream consumers",
    "• Single replica: 100% pod-delete guarantees\n  complete service unavailability during chaos",
], font_size=11, color=LIGHT_GRAY)

# Placement hypothesis
add_rounded_box(slide, 7.0, 3.8, 5.8, 1.8, VERY_DARK,
                border_color=ACCENT_GREEN)
add_text_box(slide, 7.2, 3.8, 5.4, 0.3, "Placement Impact Hypothesis",
             font_size=14, bold=True, color=ACCENT_GREEN)
add_bullet_frame(slide, 7.2, 4.15, 5.4, 1.4, [
    "• Colocate: productcatalog + frontend on same node\n  → correlated failure, worst latency & recovery",
    "• Spread: productcatalog isolated on own node\n  → fault contained, fastest recovery",
    "• Baseline: trivial fault (1% CPU, 1s)\n  → all probes pass, score = 100%",
], font_size=11, color=LIGHT_GRAY)

# Load generation
add_rounded_box(slide, 0.5, 5.8, 5.5, 1.3, VERY_DARK,
                border_color=CLR_CLI)
add_text_box(slide, 0.7, 5.8, 5.1, 0.3, "Load Generation (Locust)",
             font_size=13, bold=True, color=CLR_CLI)
load_data = [
    ["Profile", "Users", "Spawn Rate", "Duration"],
    ["steady", "50", "10/s", "120s"],
]
add_table(slide, 0.7, 6.15, 5.1, 0.7, 2, 4, load_data, font_size=10)

# Additional services note
add_text_box(slide, 7.0, 5.8, 5.8, 0.3, "Additional Services",
             font_size=13, bold=True, color=MID_GRAY)
add_text_box(slide, 7.0, 6.15, 5.8, 0.8,
    "ad-service, loadgenerator (Locust), plus infrastructure:\n"
    "ChaosCenter (litmus namespace), Prometheus (monitoring),\n"
    "Neo4j (neo4j namespace), metrics-server (kube-system)",
    font_size=11, color=MID_GRAY)


# ══════════════════════════════════════════════════════════════════════
# SLIDE 6 — KEY DESIGN DECISIONS (moved earlier)
# ══════════════════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)
slide_title(slide, "Key Design Decisions")

# Contributions banner
add_rounded_box(slide, 0.6, 1.3, 12.1, 0.6, VERY_DARK,
                border_color=ACCENT_BLUE)
add_text_box(slide, 0.8, 1.3, 11.7, 0.5,
    "ChaosProbe contributions: Placement engine, metrics collection framework, result aggregation, "
    "Neo4j graph storage, visualization pipeline  |  Existing tools: LitmusChaos, Prometheus, Neo4j, Locust",
    font_size=13, color=LIGHT_GRAY)

decisions = [
    ("Neo4j Graph Store",
     "Graph DB preserves causal relationships between faults, "
     "recovery cycles, and metrics. Cypher queries enable "
     "blast-radius analysis and topology comparison.",
     ACCENT_PURPLE),
    ("ChaosCenter GraphQL API",
     "All experiments submitted via ChaosCenter for audit "
     "trail and dashboard visibility. Wraps ChaosEngine "
     "in Argo Workflow with RBAC management.",
     CLR_CHAOS),
    ("Background Thread Probers",
     "6 continuous probers run as daemon threads with "
     "phase markers. Non-blocking, real-time collection "
     "via shared ContinuousProberBase lifecycle.",
     CLR_METRICS),
    ("6-Probe Scoring Granularity",
     "Probes produce 7 distinct score levels (0–100%). "
     "Each maps to a resilience state. Includes controls "
     "for blast-radius validation (cart, healthz).",
     ACCENT_ORANGE),
    ("Reproducible Randomness",
     "Random and antagonistic strategies use seeded "
     "PRNGs. Same seed = same placement = comparable "
     "results across experiment runs.",
     CLR_ORCH),
    ("Trivial-Fault Baseline",
     "Baseline swaps pod-delete → pod-cpu-hog (1% CPU, 1s). "
     "Probes execute identically but no pods are killed, "
     "giving a true control measurement.",
     CLR_INFRA),
]

for i, (title, desc, clr) in enumerate(decisions):
    col = i % 2
    row = i // 2
    x = 0.5 + col * 6.4
    y = 2.1 + row * 1.7
    add_rounded_box(slide, x, y, 6.0, 1.4, VERY_DARK,
                    border_color=clr, border_width=Pt(2))
    add_text_box(slide, x + 0.15, y + 0.05, 5.7, 0.3, title,
                 font_size=14, bold=True, color=clr)
    add_text_box(slide, x + 0.15, y + 0.4, 5.7, 0.95, desc,
                 font_size=12, color=LIGHT_GRAY)


# ══════════════════════════════════════════════════════════════════════
# SLIDE 7 — SYSTEM ARCHITECTURE (simplified, ≤6 components per area)
# ══════════════════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)
slide_title(slide, "System Architecture")

# ChaosProbe — left
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
    add_rounded_box(slide, x, y, 2.8, 1.0, VERY_DARK,
                    border_color=clr)
    add_text_box(slide, x + 0.1, y + 0.05, 2.6, 0.4, name,
                 font_size=11, bold=True, color=clr)
    add_text_box(slide, x + 0.1, y + 0.45, 2.6, 0.5, desc,
                 font_size=10, color=LIGHT_GRAY)

# Existing tools — right
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
     "Load generation.\nConfigurable user count, spawn rate.\nHTTP traffic to frontend",
     CLR_CLI, 9.9, 3.0),
    ("Proxmox\n(KVM/QEMU)",
     "Virtualization host.\n5 VMs: 1 control plane +\n4 worker nodes",
     CLR_INFRA, 9.9, 4.2),
]
for name, desc, clr, x, y in infra_components:
    add_rounded_box(slide, x, y, 2.8, 1.0, VERY_DARK,
                    border_color=clr)
    add_text_box(slide, x + 0.1, y + 0.05, 2.6, 0.4, name,
                 font_size=11, bold=True, color=clr)
    add_text_box(slide, x + 0.1, y + 0.45, 2.6, 0.5, desc,
                 font_size=10, color=LIGHT_GRAY)

# Flow arrows
add_arrow(slide, 6.2, 2.3, 6.8, 2.3, ACCENT_BLUE, Pt(2))
add_arrow(slide, 6.2, 3.5, 6.8, 3.5, ACCENT_BLUE, Pt(2))
add_arrow(slide, 6.2, 4.7, 6.8, 4.7, ACCENT_BLUE, Pt(2))
add_text_box(slide, 6.2, 2.05, 0.6, 0.2, "uses", font_size=10, color=MID_GRAY)
add_text_box(slide, 6.2, 3.25, 0.6, 0.2, "queries", font_size=10, color=MID_GRAY)
add_text_box(slide, 6.2, 4.45, 0.6, 0.2, "stores", font_size=10, color=MID_GRAY)

# CLI flow
add_text_box(slide, 0.3, 5.5, 12, 0.8,
    "CLI commands: chaosprobe init (provision cluster) → chaosprobe run (execute experiments) "
    "→ chaosprobe visualize (generate charts) → chaosprobe graph (Neo4j analysis queries)",
    font_size=11, color=MID_GRAY)


# ══════════════════════════════════════════════════════════════════════
# SLIDE 8 — INFRASTRUCTURE & CLUSTER TOPOLOGY
# ══════════════════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)
slide_title(slide, "Infrastructure & Cluster Topology")

# Proxmox host
add_rounded_box(slide, 0.6, 1.5, 12.1, 3.5, VERY_DARK,
                border_color=MID_GRAY, border_width=Pt(2))
add_text_box(slide, 0.8, 1.55, 3, 0.3, "Proxmox Host (KVM/QEMU)",
             font_size=13, bold=True, color=LIGHT_GRAY)

# Control plane
add_rounded_box(slide, 1.0, 2.0, 2.2, 1.5, RGBColor(0x22, 0x33, 0x55),
                border_color=ACCENT_BLUE)
add_text_box(slide, 1.1, 2.0, 2.0, 0.3, "cp1 — Control Plane",
             font_size=11, bold=True, color=ACCENT_BLUE)
add_bullet_frame(slide, 1.1, 2.3, 2.0, 1.1, [
    "2 vCPU • 2 GiB RAM",
    "K8s API Server + etcd",
    "scheduler, controller-mgr",
], font_size=10, color=LIGHT_GRAY)

# Workers
workers = [
    ("worker1", "2 GiB", 3.5),
    ("worker2", "2 GiB", 5.7),
    ("worker3", "4 GiB", 7.9),
    ("worker4", "4 GiB", 10.1),
]
for name, ram, x in workers:
    add_rounded_box(slide, x, 2.0, 1.9, 1.5, RGBColor(0x22, 0x44, 0x22),
                    border_color=ACCENT_GREEN)
    add_text_box(slide, x + 0.1, 2.0, 1.7, 0.3, name,
                 font_size=11, bold=True, color=ACCENT_GREEN)
    add_bullet_frame(slide, x + 0.1, 2.3, 1.7, 1.1, [
        f"2 vCPU • {ram} RAM",
        "containerd 1.7.11",
        "K8s v1.28.6",
    ], font_size=10, color=LIGHT_GRAY)

# Summary line
add_text_box(slide, 1.0, 3.6, 11.0, 0.35,
    "Provisioned via Kubespray 2.24 + Vagrant (libvirt)  |  "
    "CNI: Calico  |  DNS: CoreDNS  |  Total: 10 vCPU, 14 GiB RAM",
    font_size=11, color=MID_GRAY)

# Infrastructure components table
add_text_box(slide, 0.6, 5.2, 12, 0.3, "Installed Infrastructure Components",
             font_size=16, bold=True, color=ACCENT_BLUE)

infra_data = [
    ["Namespace", "Component", "Purpose", "Install Method"],
    ["litmus", "ChaosCenter + operator + CRDs", "Fault injection & experiment mgmt", "Helm chart"],
    ["monitoring", "Prometheus + kube-state-metrics", "Cluster metrics (PromQL)", "Helm chart"],
    ["neo4j", "Neo4j 5-community", "Graph storage (14 node types)", "K8s Deployment"],
    ["kube-system", "metrics-server", "Node/pod CPU & memory API", "Official manifest"],
    ["online-boutique", "12 microservices + subscriber", "Target application", "K8s manifests"],
]
add_table(slide, 0.6, 5.55, 12.1, 1.8, 6, 4, infra_data, font_size=11)


# ══════════════════════════════════════════════════════════════════════
# SLIDE 9 — EXPERIMENT LIFECYCLE & DATA FLOW (merged, simplified)
# ══════════════════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)
slide_title(slide, "Experiment Lifecycle & Data Flow")

# Top pipeline
phases = [
    ("1. Configure",    "Load YAML\nValidate specs",        CLR_CLI,     0.3),
    ("2. Place",        "Apply strategy\nPatch nodeSelector", CLR_ORCH,  2.8),
    ("3. Inject Chaos", "ChaosEngine\nvia ChaosCenter",     CLR_CHAOS,  5.3),
    ("4. Measure",      "6 probers\n+ load generator",      CLR_METRICS, 7.8),
    ("5. Store",        "Neo4j sync\nCharts + export",      CLR_STORAGE, 10.3),
]
for title, desc, clr, x in phases:
    add_rounded_box(slide, x, 1.5, 2.2, 1.2, clr, "", 10, WHITE, True,
                    border_color=clr)
    add_text_box(slide, x + 0.05, 1.5, 2.1, 0.3, title,
                 font_size=12, bold=True, color=WHITE)
    add_text_box(slide, x + 0.1, 1.8, 2.0, 0.8, desc,
                 font_size=10, color=TRANS_WHITE)
for i in range(len(phases) - 1):
    x1 = phases[i][3] + 2.2
    x2 = phases[i + 1][3]
    add_arrow(slide, x1, 2.1, x2, 2.1, LIGHT_GRAY, Pt(2))

# Phase timeline
add_text_box(slide, 0.3, 3.0, 12, 0.3, "Three-Phase Measurement Window",
             font_size=16, bold=True, color=ACCENT_BLUE)
add_rounded_box(slide, 0.3, 3.4, 3.5, 0.5, CLR_ORCH,
                "PreChaos — steady state (30s)", 11, WHITE, True)
add_rounded_box(slide, 4.0, 3.4, 4.5, 0.5, CLR_CHAOS,
                "DuringChaos — fault active (120s)", 11, WHITE, True)
add_rounded_box(slide, 8.7, 3.4, 4.0, 0.5, CLR_METRICS,
                "PostChaos — recovery observation", 11, WHITE, True)

# Data flow columns
add_text_box(slide, 0.3, 4.2, 12, 0.3, "Data Flow",
             font_size=16, bold=True, color=ACCENT_BLUE)

flow_cols = [
    ("Input",
     ["experiment.yaml", "K8s manifests", "probe definitions"],
     CLR_CLI, 0.3),
    ("Processing",
     ["Config → topology", "Placement → nodeSelector", "ChaosRunner → API"],
     CLR_ORCH, 2.8),
    ("Collection",
     ["RecoveryWatcher", "5 continuous probers", "ResultCollector (CRDs)"],
     CLR_METRICS, 5.3),
    ("Storage",
     ["Neo4j (14 node types)", "Per-run metrics", "Anomaly labels"],
     CLR_STORAGE, 7.8),
    ("Output",
     ["9 chart types (PNG)", "HTML summary", "ML dataset (CSV)"],
     CLR_OUTPUT, 10.3),
]
for title, items, clr, x in flow_cols:
    add_text_box(slide, x, 4.5, 2.2, 0.25, title,
                 font_size=11, bold=True, color=clr)
    for j, item in enumerate(items):
        add_rounded_box(slide, x, 4.8 + j * 0.55, 2.2, 0.45, VERY_DARK,
                        item, 10, LIGHT_GRAY, False, border_color=clr)
for i in range(len(flow_cols) - 1):
    x1 = flow_cols[i][3] + 2.2
    x2 = flow_cols[i + 1][3]
    add_arrow(slide, x1, 5.3, x2, 5.3, LIGHT_GRAY, Pt(1.5))

# Loop note
add_text_box(slide, 0.3, 6.5, 12, 0.7,
    "Strategy loop: for each of 6 strategies × N iterations: "
    "settle (30s) → start probers → start Locust → pre-chaos baseline → "
    "run chaos (120s) → post-chaos recovery → collect & sync to Neo4j",
    font_size=11, color=MID_GRAY)


# ══════════════════════════════════════════════════════════════════════
# SLIDE 10 — METRICS COLLECTION: PROBERS
# ══════════════════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)
slide_title(slide, "Metrics Collection — Probers")

add_text_box(slide, 0.5, 1.3, 12, 0.3, "6 Continuous Probers Run as Background Threads",
             font_size=16, bold=True, color=ACCENT_BLUE)

prober_data = [
    ["Prober", "What It Measures", "Data Source", "Interval"],
    ["RecoveryWatcher", "Pod deletion → scheduled → ready (ms)", "K8s Watch API", "Real-time"],
    ["LatencyProber", "HTTP route latency + error rates", "kubectl exec → curl", "~2s"],
    ["ResourceProber", "Node/pod CPU (millicores) + memory", "Metrics API (v1beta1)", "~5s"],
    ["PrometheusProber", "pod_ready, CPU throttle, network I/O", "PromQL queries", "~10s"],
    ["ThroughputProber", "Redis ops/s, disk R/W bytes/s", "redis-cli, dd commands", "~10s"],
]
add_table(slide, 0.3, 1.8, 12.7, 2.8, 6, 4, prober_data, font_size=11)

# How probers work
add_text_box(slide, 0.5, 4.9, 12, 0.3, "Prober Lifecycle",
             font_size=16, bold=True, color=ACCENT_GREEN)
add_bullet_frame(slide, 0.5, 5.3, 12, 2.0, [
    "• All probers extend ContinuousProberBase — shared start/stop lifecycle with phase markers",
    "• Non-blocking: each prober runs in its own daemon thread, collecting in real-time",
    "• Phase-aware: measurements tagged PreChaos / DuringChaos / PostChaos for comparison",
    "• Infrastructure: metrics-server, Prometheus + kube-state-metrics, redis-cart pod, K8s API",
], font_size=13, color=LIGHT_GRAY)


# ══════════════════════════════════════════════════════════════════════
# SLIDE 10b — CONTENTION CATEGORIES
# ══════════════════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)
slide_title(slide, "Contention Categories")

add_text_box(slide, 0.5, 1.3, 12, 0.3,
             "Expected Impact by Placement — with Literature References",
             font_size=16, bold=True, color=ACCENT_ORANGE)

contention_data = [
    ["Category", "Metric", "Expected Colocate Impact", "Literature"],
    ["Node CPU Utilization", "CPU millicores, throttle seconds",
     "Shared cores → CPU throttling under load",
     "Burns et al. (2016) — Borg"],
    ["Memory Pressure", "Working set bytes, OOM events",
     "Shared memory → evictions when overcommitted",
     "Verma et al. (2015) — Borg"],
    ["Inter-Service Latency", "HTTP response time (ms) per route",
     "Network stack contention on shared node",
     "Gan et al. (2019) — DeathStarBench"],
    ["Disk I/O Throughput", "Sequential R/W bytes/s, ops/s",
     "Shared disk bandwidth → degraded throughput",
     "Dean & Barroso (2013) — Tail at Scale"],
    ["Recovery Time", "Deletion → scheduled → ready (ms)",
     "Scheduler contention delays pod placement",
     "Hightower et al. (2017) — K8s Up & Running"],
]
add_table(slide, 0.3, 1.8, 12.7, 3.5, 6, 4, contention_data,
          font_size=11, header_color=ACCENT_ORANGE)

# Key insight
add_rounded_box(slide, 0.5, 5.6, 12.3, 1.3, VERY_DARK,
                border_color=ACCENT_BLUE, border_width=Pt(2))
add_text_box(slide, 0.7, 5.65, 11.9, 0.3, "Key Insight",
             font_size=16, bold=True, color=ACCENT_BLUE)
add_text_box(slide, 0.7, 6.0, 11.9, 0.8,
    "Colocating all pods on a single node maximizes contention across all five categories simultaneously. "
    "Spreading pods across nodes minimizes per-node contention but may introduce cross-node network latency. "
    "These trade-offs are what ChaosProbe quantifies.",
    font_size=13, color=LIGHT_GRAY)


# ══════════════════════════════════════════════════════════════════════
# SLIDE 11 — PROBE DESIGN
# ══════════════════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)
slide_title(slide, "Probe Design")

# Definition
add_rounded_box(slide, 0.5, 1.3, 12.3, 0.8, VERY_DARK,
                border_color=ACCENT_BLUE)
add_text_box(slide, 0.7, 1.35, 11.9, 0.7,
    "A probe is a lightweight health check executed inside the Kubernetes cluster during chaos. "
    "Probes validate whether a service remains reachable and responsive under fault injection. "
    "System health is measured by how many probes pass — each failure indicates degraded resilience.",
    font_size=13, color=LIGHT_GRAY)

# Probe table
probe_data = [
    ["Probe Name", "Mode", "Endpoint", "Timeout", "Retry"],
    ["frontend-product-strict", "Continuous (2s)", "/product/OLJCESPC7Z", "3s", "1"],
    ["frontend-homepage-strict", "Continuous (2s)", "/", "3s", "1"],
    ["frontend-homepage-moderate", "Continuous (3s)", "/", "3s", "2"],
    ["frontend-cart", "Continuous (4s)", "/cart", "5s", "2"],
    ["frontend-homepage-edge", "Edge (5s)", "/", "15s", "5"],
    ["frontend-healthz", "Continuous (4s)", "/_healthz", "5s", "2"],
]
add_table(slide, 0.3, 2.4, 12.7, 2.8, 7, 5, probe_data, font_size=11)

# Sensitivity layers
add_text_box(slide, 0.5, 5.5, 12, 0.3, "Probe Sensitivity Layers",
             font_size=16, bold=True, color=ACCENT_BLUE)
add_bullet_frame(slide, 0.5, 5.9, 6.0, 1.5, [
    "Strict (2s, 1 retry): detects any disruption",
    "Moderate (3–4s, 2 retries): strategy differences emerge",
], font_size=13, color=LIGHT_GRAY)
add_bullet_frame(slide, 6.5, 5.9, 6.0, 1.5, [
    "Edge (5s, 5 retries): validates eventual recovery",
    "Control (healthz, cart): detects node-level contention",
], font_size=13, color=LIGHT_GRAY)


# ══════════════════════════════════════════════════════════════════════
# SLIDE 11b — RESILIENCE SCORING
# ══════════════════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)
slide_title(slide, "Resilience Scoring")

# Formula
add_rounded_box(slide, 0.5, 1.3, 12.3, 0.7, VERY_DARK,
                border_color=ACCENT_BLUE, border_width=Pt(2))
add_text_box(slide, 0.7, 1.35, 11.9, 0.3, "Scoring Formula",
             font_size=16, bold=True, color=ACCENT_BLUE)
add_text_box(slide, 0.7, 1.7, 11.9, 0.25,
    "score = Σ(probeSuccess%) / N   •   verdict = PASS if all probes pass, else FAIL",
    font_size=14, color=WHITE)

# Score interpretation - larger, more readable
add_text_box(slide, 0.5, 2.3, 12, 0.3, "Score Interpretation",
             font_size=16, bold=True, color=ACCENT_ORANGE)

scores = [
    ("0%",   "All 6 probes failed — total disruption"),
    ("17%",  "1 probe passed — node alive but service down"),
    ("33%",  "2 probes — eventual recovery only"),
    ("50%",  "3 probes — moderate resilience"),
    ("67%",  "4 probes — good recovery"),
    ("83%",  "5 probes — fast recovery"),
    ("100%", "All probes passed — no visible disruption"),
]
for i, (score, meaning) in enumerate(scores):
    y = 2.75 + i * 0.45
    pct = int(score.replace("%", ""))
    badge_clr = (ACCENT_RED if pct <= 33
                 else ACCENT_ORANGE if pct <= 67
                 else ACCENT_GREEN)
    add_rounded_box(slide, 0.7, y, 0.8, 0.35, badge_clr,
                    score, 14, WHITE, True)
    add_text_box(slide, 1.7, y, 10.5, 0.35, meaning,
                 font_size=14, color=LIGHT_GRAY)

# Expected results box
add_rounded_box(slide, 0.5, 6.0, 12.3, 1.2, VERY_DARK,
                border_color=ACCENT_GREEN)
add_text_box(slide, 0.7, 6.0, 11.9, 0.3, "Expected Results by Strategy",
             font_size=16, bold=True, color=ACCENT_GREEN)
add_bullet_frame(slide, 0.7, 6.4, 5.5, 0.7, [
    "• Baseline: 100% — trivial fault, no disruption",
    "• Spread: 67–100% — fault isolation limits impact",
], font_size=13, color=LIGHT_GRAY)
add_bullet_frame(slide, 6.5, 6.4, 5.5, 0.7, [
    "• Default: 33–67% — partial isolation by scheduler",
    "• Colocate: 0–33% — all services affected by fault",
], font_size=13, color=LIGHT_GRAY)


# ══════════════════════════════════════════════════════════════════════
# SLIDE 12 — EXPERIMENT CONFIGURATIONS (replicable)
# ══════════════════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)
slide_title(slide, "Experiment Configurations")
add_text_box(slide, 0.6, 1.1, 12, 0.3,
             "All experiments are fully defined and replicable with these exact parameters",
             font_size=13, color=MID_GRAY)

# pod-delete
add_rounded_box(slide, 0.5, 1.5, 5.8, 3.0, VERY_DARK,
                border_color=CLR_CHAOS, border_width=Pt(2))
add_text_box(slide, 0.7, 1.5, 5.4, 0.3, "Experiment 1: pod-delete (Availability)",
             font_size=14, bold=True, color=CLR_CHAOS)
pd_data = [
    ["Parameter", "Value"],
    ["Fault Type", "pod-delete"],
    ["Target", "productcatalogservice (online-boutique)"],
    ["TOTAL_CHAOS_DURATION", "120 seconds"],
    ["CHAOS_INTERVAL", "5 seconds"],
    ["PODS_AFFECTED_PERC", "100%"],
    ["FORCE", "true"],
    ["Probes", "6 httpProbes (see Probe Design slide)"],
]
add_table(slide, 0.7, 1.85, 5.4, 2.5, 8, 2, pd_data,
          font_size=10, header_color=CLR_CHAOS)

# pod-cpu-hog
add_rounded_box(slide, 6.8, 1.5, 5.8, 3.0, VERY_DARK,
                border_color=ACCENT_ORANGE, border_width=Pt(2))
add_text_box(slide, 7.0, 1.5, 5.4, 0.3, "Experiment 2: pod-cpu-hog (Contention)",
             font_size=14, bold=True, color=ACCENT_ORANGE)
ch_data = [
    ["Parameter", "Value"],
    ["Fault Type", "pod-cpu-hog"],
    ["Target", "currencyservice (online-boutique)"],
    ["TOTAL_CHAOS_DURATION", "60 seconds"],
    ["CPU_CORES", "1"],
    ["CPU_LOAD", "100%"],
    ["Probes", "1 httpProbe (frontend-availability)"],
]
add_table(slide, 7.0, 1.85, 5.4, 2.3, 7, 2, ch_data,
          font_size=10, header_color=ACCENT_ORANGE)

# Baseline
add_rounded_box(slide, 0.5, 4.7, 6.0, 1.8, VERY_DARK,
                border_color=CLR_INFRA, border_width=Pt(2))
add_text_box(slide, 0.7, 4.7, 5.6, 0.3, "Baseline Configuration (Control Group)",
             font_size=14, bold=True, color=CLR_INFRA)
add_bullet_frame(slide, 0.7, 5.05, 5.6, 1.3, [
    "• Fault swapped: pod-delete → pod-cpu-hog",
    "• TOTAL_CHAOS_DURATION = 1 second",
    "• CPU_CORES = 0, CPU_LOAD = 1 (1% stress)",
    "• All 6 probes execute identically (same timeouts)",
    "• Expected result: 100% score, 0 recovery cycles",
], font_size=10, color=LIGHT_GRAY)

# Why no recovery time in baseline
add_rounded_box(slide, 6.8, 4.7, 5.8, 1.8, VERY_DARK,
                border_color=ACCENT_GREEN)
add_text_box(slide, 7.0, 4.7, 5.4, 0.3, "Why Baseline Shows No Recovery Time",
             font_size=14, bold=True, color=ACCENT_GREEN)
add_bullet_frame(slide, 7.0, 5.05, 5.4, 1.3, [
    "• pod-cpu-hog does NOT delete pods",
    "• RecoveryWatcher only triggers on pod deletion",
    "• Therefore: 0 recovery cycles, N/A recovery time",
    "• Any non-zero recovery = pre-existing instability",
    "• This validates the control: no fault = no impact",
], font_size=10, color=LIGHT_GRAY)

# Replicate
add_rounded_box(slide, 0.5, 6.7, 12.1, 0.6, VERY_DARK,
                border_color=ACCENT_BLUE)
add_text_box(slide, 0.7, 6.7, 11.7, 0.5,
    "Replicate:  chaosprobe init --scenario placement-experiment.yaml  →  "
    "chaosprobe run --strategies baseline,default,colocate,spread,random,antagonistic --iterations 3  →  "
    "chaosprobe visualize",
    font_size=12, color=LIGHT_GRAY)


# ══════════════════════════════════════════════════════════════════════
# SLIDE 13 — NEO4J GRAPH SCHEMA (verified, cleaner layout)
# ══════════════════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)
slide_title(slide, "Neo4j Graph Schema")

# Source: verified against chaosprobe/storage/neo4j_writer.py (MERGE/CREATE statements)
# 14 node types, 18 relationships

NODE_W, NODE_H = 1.75, 0.45
def node_box(x, y, name, clr, fsize=10, w=NODE_W, h=NODE_H):
    add_rounded_box(slide, x, y, w, h, clr, name, fsize, WHITE, True)

def rel_label(x, y, text, w=1.8, color=MID_GRAY, size=9):
    add_text_box(slide, x, y, w, 0.22, text, font_size=size, color=color)

# ── Section 1: Cluster Topology (top row) ──
add_text_box(slide, 0.3, 1.25, 5.0, 0.25, "Cluster Topology",
             font_size=11, bold=True, color=CLR_INFRA)

# K8sNode ← Deployment → Service (with Service self-loop DEPENDS_ON)
node_box(0.3, 1.55, "K8sNode", CLR_INFRA)
node_box(2.35, 1.55, "Deployment", CLR_ORCH)
node_box(4.4, 1.55, "Service", CLR_ORCH)

# Deployment → K8sNode  (SCHEDULED_ON)
add_arrow(slide, 2.35, 1.78, 2.05, 1.78, CLR_ORCH, Pt(1.5))
rel_label(1.35, 1.30, "SCHEDULED_ON", w=1.15)

# Deployment → Service  (EXPOSES)
add_arrow(slide, 4.1, 1.78, 4.4, 1.78, CLR_ORCH, Pt(1.5))
rel_label(3.35, 1.30, "EXPOSES", w=0.9)

# Service self-loop  (DEPENDS_ON)
add_arrow(slide, 5.3, 1.62, 5.6, 1.45, CLR_ORCH, Pt(1.2))
add_arrow(slide, 5.6, 1.45, 5.6, 1.95, CLR_ORCH, Pt(1.2))
add_arrow(slide, 5.6, 1.95, 5.3, 1.95, CLR_ORCH, Pt(1.2))
rel_label(5.7, 1.63, "DEPENDS_ON", w=1.2)

# ── Section 2: Central ChaosRun + PlacementStrategy ──
node_box(5.55, 3.1, "ChaosRun", CLR_CHAOS, fsize=13, w=2.3, h=0.65)
node_box(2.55, 3.2, "PlacementStrategy", CLR_CLI, fsize=9, w=2.2)

# ChaosRun → PlacementStrategy  (USED_STRATEGY)
add_arrow(slide, 5.55, 3.42, 4.75, 3.42, CLR_CLI, Pt(1.5))
rel_label(4.55, 2.95, "USED_STRATEGY", w=1.5)

# Deployment → ChaosRun  (TARGETED_BY)
add_arrow(slide, 3.1, 2.0, 5.55, 3.18, CLR_CHAOS, Pt(1.5))
rel_label(3.35, 2.45, "TARGETED_BY", w=1.4)

# ── Section 3: Experiment Results (right) ──
add_text_box(slide, 8.2, 1.25, 5.0, 0.25, "Experiment Results",
             font_size=11, bold=True, color=CLR_CHAOS)
node_box(8.2, 1.55, "ExperimentResult", CLR_CHAOS, fsize=9, w=2.2)
node_box(10.7, 1.55, "ProbeResult", ACCENT_RED, fsize=9, w=1.9)

add_arrow(slide, 10.4, 1.78, 10.7, 1.78, CLR_CHAOS, Pt(1.5))
rel_label(10.05, 1.30, "HAS_PROBE", w=1.0)

# ChaosRun → ExperimentResult  (HAS_RESULT)
add_arrow(slide, 7.85, 3.25, 8.2, 1.95, CLR_CHAOS, Pt(1.5))
rel_label(8.0, 2.35, "HAS_RESULT", w=1.2)

# RecoveryCycle
node_box(8.2, 3.2, "RecoveryCycle", CLR_METRICS, fsize=9, w=2.0)
add_arrow(slide, 7.85, 3.42, 8.2, 3.42, CLR_METRICS, Pt(1.5))
rel_label(7.95, 3.65, "HAS_RECOVERY_CYCLE", w=1.9)

# ── Section 4: Metrics & Telemetry (bottom-left) ──
add_text_box(slide, 0.3, 4.35, 6.0, 0.25, "Metrics & Telemetry",
             font_size=11, bold=True, color=CLR_METRICS)

metric_nodes = [
    ("MetricsPhase",   0.3, 4.65, CLR_METRICS, "HAS_METRICS_PHASE"),
    ("MetricsSample",  2.25, 4.65, CLR_METRICS, "HAS_SAMPLE"),
    ("AnomalyLabel",   4.2, 4.65, CLR_CHAOS,   "HAS_ANOMALY_LABEL"),
    ("CascadeEvent",   6.15, 4.65, ACCENT_ORANGE, "HAS_CASCADE_EVENT"),
]
for name, x, y, clr, rel in metric_nodes:
    node_box(x, y, name, clr, fsize=9)
    add_arrow(slide, 6.65, 3.8, x + NODE_W / 2, y, clr, Pt(1.2))
    rel_label(x - 0.05, y + NODE_H + 0.02, rel, w=NODE_W + 0.1, size=8)

# AnomalyLabel → Service  (TARGETS / AFFECTS) — dashed back-reference
add_arrow(slide, 4.95, 4.65, 5.0, 2.05, CLR_CHAOS, Pt(1.0))
rel_label(4.1, 3.55, "TARGETS / AFFECTS", w=1.8, color=LIGHT_GRAY, size=8)

# ── Section 5: Pod State (bottom-right) ──
add_text_box(slide, 8.2, 4.35, 5.0, 0.25, "Pod State",
             font_size=11, bold=True, color=CLR_ORCH)
node_box(8.2, 4.65, "PodSnapshot", CLR_ORCH, fsize=9, w=2.0)
node_box(10.5, 4.65, "ContainerLog", CLR_INFRA, fsize=9, w=2.0)

# ChaosRun → PodSnapshot
add_arrow(slide, 7.85, 3.75, 9.2, 4.65, CLR_ORCH, Pt(1.2))
rel_label(8.2, 5.15, "HAS_POD_SNAPSHOT", w=1.9, size=8)

# PodSnapshot → ContainerLog
add_arrow(slide, 10.2, 4.87, 10.5, 4.87, CLR_INFRA, Pt(1.2))
rel_label(10.4, 5.15, "HAS_CONTAINER_LOG", w=1.9, size=8)

# PodSnapshot cross-refs back to K8sNode + Deployment
rel_label(8.2, 5.42, "PodSnapshot → K8sNode (RUNNING_ON)", w=4.0, size=8, color=LIGHT_GRAY)
rel_label(8.2, 5.62, "PodSnapshot → Deployment (BELONGS_TO)", w=4.0, size=8, color=LIGHT_GRAY)

# ── Schema summary footer (compact, inside box) ──
add_rounded_box(slide, 0.3, 6.05, 7.7, 1.25, VERY_DARK,
                border_color=ACCENT_PURPLE)
add_text_box(slide, 0.45, 6.1, 7.4, 0.3,
             "14 Node Types · 18 Relationships (verified from neo4j_writer.py)",
             font_size=11, bold=True, color=ACCENT_PURPLE)
add_text_box(slide, 0.45, 6.4, 7.4, 0.9,
    "Topology:   K8sNode · Deployment · Service · PlacementStrategy\n"
    "Run:            ChaosRun · ExperimentResult · ProbeResult · RecoveryCycle\n"
    "Telemetry:  MetricsPhase · MetricsSample · AnomalyLabel · CascadeEvent\n"
    "Pod state:  PodSnapshot · ContainerLog",
    font_size=9, color=LIGHT_GRAY)


# ══════════════════════════════════════════════════════════════════════
# SLIDE 14 — RESULTS: VISUALIZATION SUMMARY
# ══════════════════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)
slide_title(slide, "Results — Visualization Summary")

charts_dir = _find_latest_charts_dir()

# Top row: 3 key charts
chart_configs = [
    ("resilience_scores.png", "Resilience Scores by Strategy", 0.3, 1.4, 4.0, 2.6),
    ("latency_by_strategy.png", "Inter-Service Latency by Strategy", 4.6, 1.4, 4.0, 2.6),
    ("recovery_times.png", "Recovery Times by Strategy", 8.9, 1.4, 4.0, 2.6),
]
for filename, label, x, y, w, h in chart_configs:
    img_path = os.path.join(charts_dir, filename) if charts_dir else None
    add_image_or_placeholder(slide, x, y, w, h, img_path,
                             f"[{label}]\nGenerated by:\nchaosprobe visualize")
    add_text_box(slide, x, y + h, w, 0.3, label,
                 font_size=11, bold=True, color=ACCENT_BLUE, alignment=PP_ALIGN.CENTER)

# Bottom row: 3 more charts
chart_configs2 = [
    ("resource_utilization.png", "Resource Utilization (CPU/Memory)", 0.3, 4.5, 4.0, 2.3),
    ("throughput_by_strategy.png", "I/O Throughput by Strategy", 4.6, 4.5, 4.0, 2.3),
    ("latency_degradation.png", "Latency Degradation (Pre vs During)", 8.9, 4.5, 4.0, 2.3),
]
for filename, label, x, y, w, h in chart_configs2:
    img_path = os.path.join(charts_dir, filename) if charts_dir else None
    add_image_or_placeholder(slide, x, y, w, h, img_path,
                             f"[{label}]\nGenerated by:\nchaosprobe visualize")
    add_text_box(slide, x, y + h, w, 0.3, label,
                 font_size=11, bold=True, color=ACCENT_BLUE, alignment=PP_ALIGN.CENTER)

# Key finding
add_text_box(slide, 0.3, 7.1, 12.7, 0.3,
    "Key finding: colocate strategy consistently shows worst inter-service latency and longest "
    "recovery times — this scenario should be avoided in production deployments.",
    font_size=13, bold=True, color=ACCENT_ORANGE)


# ══════════════════════════════════════════════════════════════════════
# SLIDE 15 — INTER-SERVICE LATENCY FOCUS
# ══════════════════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)
slide_title(slide, "Inter-Service Latency During Chaos")

# Large chart
if charts_dir:
    img = os.path.join(charts_dir, "latency_degradation.png")
else:
    img = None
add_image_or_placeholder(slide, 0.5, 1.4, 7.5, 4.5, img,
                         "[Latency Degradation: Pre-Chaos vs During-Chaos]\n\n"
                         "Side-by-side bar chart per strategy showing\n"
                         "HTTP route mean latency increase during fault injection.\n\n"
                         "Generated by: chaosprobe visualize")

# Analysis
add_rounded_box(slide, 8.5, 1.4, 4.3, 2.8, VERY_DARK,
                border_color=ACCENT_RED, border_width=Pt(2))
add_text_box(slide, 8.7, 1.4, 3.9, 0.3, "Key Observations",
             font_size=14, bold=True, color=ACCENT_RED)
add_bullet_frame(slide, 8.7, 1.75, 3.9, 2.3, [
    "• Colocate: highest latency degradation\n  across all routes — worst strategy",
    "• Spread: minimal latency increase —\n  fault isolation contains impact",
    "• Default: moderate — scheduler partially\n  separates dependent services",
    "• Antagonistic: high — heavy pods on\n  same node amplifies contention",
], font_size=10, color=LIGHT_GRAY)

# Why colocate worst
add_rounded_box(slide, 8.5, 4.5, 4.3, 1.5, VERY_DARK,
                border_color=ACCENT_ORANGE)
add_text_box(slide, 8.7, 4.5, 3.9, 0.3, "Why Colocate Performs Worst",
             font_size=13, bold=True, color=ACCENT_ORANGE)
add_bullet_frame(slide, 8.7, 4.85, 3.9, 1.1, [
    "• All 12 services on 1 node: shared CPU,\n  memory, network stack, disk I/O",
    "• Pod deletion causes cascading resource\n  pressure on all co-located services",
    "• Conclusion: this placement should be\n  avoided in production environments",
], font_size=10, color=LIGHT_GRAY)

# Ranking
add_rounded_box(slide, 0.5, 6.2, 12.3, 1.0, VERY_DARK,
                border_color=ACCENT_BLUE)
add_text_box(slide, 0.7, 6.2, 11.9, 0.3,
             "Strategy Ranking by Inter-Service Latency (best → worst)",
             font_size=13, bold=True, color=ACCENT_BLUE)
ranking = [
    ("Spread", ACCENT_GREEN, 0.7),
    ("Default", CLR_CLI, 3.2),
    ("Random", ACCENT_ORANGE, 5.7),
    ("Antagonistic", ACCENT_PURPLE, 8.2),
    ("Colocate", ACCENT_RED, 10.7),
]
for name, clr, x in ranking:
    add_rounded_box(slide, x, 6.55, 2.0, 0.4, clr, name, 12, WHITE, True)
for i in range(len(ranking) - 1):
    x1 = ranking[i][2] + 2.0
    x2 = ranking[i + 1][2]
    add_arrow(slide, x1, 6.75, x2, 6.75, LIGHT_GRAY, Pt(2))


# ══════════════════════════════════════════════════════════════════════
# SLIDE 16 — SUMMARY & FUTURE WORK
# ══════════════════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)
slide_title(slide, "Summary & Future Work")

# Key contributions
add_rounded_box(slide, 0.5, 1.5, 5.8, 3.0, VERY_DARK,
                border_color=ACCENT_GREEN)
add_text_box(slide, 0.7, 1.5, 5.4, 0.35, "Key Contributions",
             font_size=18, bold=True, color=ACCENT_GREEN)
add_bullet_frame(slide, 0.7, 1.95, 5.4, 2.4, [
    "• Systematic evaluation of 6 placement strategies\n"
    "  under fault injection in Kubernetes",
    "• Quantified impact on recovery time, inter-service\n"
    "  latency, resource utilization, and I/O throughput",
    "• Neo4j graph storage preserving causal\n"
    "  relationships for topology-aware analysis",
    "• Reproducible framework: exact configs,\n"
    "  seeded randomness, automated comparison",
], font_size=12, color=LIGHT_GRAY)

# Key findings
add_rounded_box(slide, 6.8, 1.5, 5.8, 3.0, VERY_DARK,
                border_color=ACCENT_BLUE)
add_text_box(slide, 7.0, 1.5, 5.4, 0.35, "Key Findings",
             font_size=18, bold=True, color=ACCENT_BLUE)
add_bullet_frame(slide, 7.0, 1.95, 5.4, 2.4, [
    "• Colocate is consistently the worst strategy:\n"
    "  highest latency, longest recovery, most contention",
    "• Spread provides best fault isolation:\n"
    "  minimal latency degradation under chaos",
    "• Placement topology significantly affects\n"
    "  resilience — not just resource availability",
    "• Baseline validates methodology: trivial fault\n"
    "  produces 100% score as expected",
], font_size=12, color=LIGHT_GRAY)

# Future work
add_rounded_box(slide, 0.5, 4.8, 12.1, 1.3, VERY_DARK,
                border_color=ACCENT_ORANGE)
add_text_box(slide, 0.7, 4.8, 11.7, 0.35, "Future Work",
             font_size=18, bold=True, color=ACCENT_ORANGE)
add_bullet_frame(slide, 0.7, 5.2, 5.4, 0.8, [
    "• Multi-fault injection — concurrent faults\n  for complex failure scenarios",
    "• Larger cluster scale — 20+ nodes, 100+ services",
    "• ML-based anomaly detection on collected dataset",
], font_size=12, color=LIGHT_GRAY)
add_bullet_frame(slide, 6.8, 5.2, 5.4, 0.8, [
    "• Custom placement policies — RL-based scheduling",
    "• Production-like traffic patterns & workloads",
    "• Integration with GitOps for automated remediation",
], font_size=12, color=LIGHT_GRAY)

# Core message
add_rounded_box(slide, 0.5, 6.3, 12.1, 1.0, VERY_DARK,
                border_color=ACCENT_BLUE, border_width=Pt(3))
add_text_box(slide, 0.7, 6.3, 11.7, 0.3, "Core Message",
             font_size=18, bold=True, color=ACCENT_BLUE)
add_text_box(slide, 0.7, 6.65, 11.7, 0.6,
    "Pod placement topology has a measurable and significant impact on microservice resilience "
    "under chaos injection. By systematically varying placement strategies and measuring "
    "recovery time, inter-service latency, and resource utilization, ChaosProbe demonstrates "
    "that topology-aware scheduling is essential for building resilient cloud-native systems.",
    font_size=14, color=WHITE)


# ══════════════════════════════════════════════════════════════════════
# SAVE
# ══════════════════════════════════════════════════════════════════════
output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "ChaosProbe_Presentation.pptx")
prs.save(output_path)
print(f"Presentation saved to: {output_path}")
print(f"Total slides: {len(prs.slides)}")
