#!/usr/bin/env python3
"""Generate ChaosProbe PowerPoint presentation with architecture & data flow diagrams."""

from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.chart import XL_CHART_TYPE

# ── Colour palette ─────────────────────────────────────────────────
DARK_BG      = RGBColor(0x1B, 0x1B, 0x2F)  # slide background
ACCENT_BLUE  = RGBColor(0x00, 0x96, 0xD6)  # primary accent
ACCENT_GREEN = RGBColor(0x2E, 0xCC, 0x71)  # success / metrics
ACCENT_RED   = RGBColor(0xE7, 0x4C, 0x3C)  # chaos / fault
ACCENT_ORANGE= RGBColor(0xF3, 0x9C, 0x12)  # warning / AI
ACCENT_PURPLE= RGBColor(0x9B, 0x59, 0xB6)  # Neo4j / storage
WHITE         = RGBColor(0xFF, 0xFF, 0xFF)
LIGHT_GRAY   = RGBColor(0xBD, 0xBD, 0xBD)
MID_GRAY     = RGBColor(0x6C, 0x6C, 0x7A)
VERY_DARK    = RGBColor(0x12, 0x12, 0x22)
TRANS_WHITE   = RGBColor(0xF0, 0xF0, 0xF8)

# Box colours per layer
CLR_CLI       = RGBColor(0x34, 0x98, 0xDB)  # blue
CLR_ORCH      = RGBColor(0x1A, 0xBC, 0x9C)  # teal
CLR_CHAOS     = RGBColor(0xE7, 0x4C, 0x3C)  # red
CLR_METRICS   = RGBColor(0x2E, 0xCC, 0x71)  # green
CLR_STORAGE   = RGBColor(0x9B, 0x59, 0xB6)  # purple
CLR_OUTPUT    = RGBColor(0xF3, 0x9C, 0x12)  # orange
CLR_INFRA     = RGBColor(0x7F, 0x8C, 0x8D)  # gray
CLR_AI        = RGBColor(0xE9, 0x1E, 0x63)  # pink

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
        1,  # straight connector
        Inches(x1), Inches(y1), Inches(x2), Inches(y2)
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
        start_idx = 1
    else:
        start_idx = 0

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
        p.level = 0
    return txBox


def add_table(slide, left, top, width, height, rows, cols, data,
              header_color=ACCENT_BLUE, cell_color=VERY_DARK,
              text_color=WHITE, header_text_color=WHITE, font_size=10):
    """data = list of lists, first row is header."""
    tbl_shape = slide.shapes.add_table(rows, cols, Inches(left), Inches(top),
                                        Inches(width), Inches(height))
    tbl = tbl_shape.table
    for r in range(rows):
        for c in range(cols):
            cell = tbl.cell(r, c)
            cell.text = str(data[r][c]) if r < len(data) and c < len(data[r]) else ""
            for paragraph in cell.text_frame.paragraphs:
                paragraph.font.size = Pt(font_size)
                paragraph.font.name = "Calibri"
                if r == 0:
                    paragraph.font.bold = True
                    paragraph.font.color.rgb = header_text_color
                else:
                    paragraph.font.color.rgb = text_color
                paragraph.alignment = PP_ALIGN.LEFT
            fill = cell.fill
            fill.solid()
            fill.fore_color.rgb = header_color if r == 0 else cell_color
            cell.margin_left = Pt(4)
            cell.margin_right = Pt(4)
            cell.margin_top = Pt(2)
            cell.margin_bottom = Pt(2)
    return tbl_shape


def slide_title(slide, title_text, subtitle_text=None):
    add_text_box(slide, 0.6, 0.3, 12, 0.7, title_text,
                 font_size=32, bold=True, color=WHITE)
    # thin accent line
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


# ══════════════════════════════════════════════════════════════════════
# SLIDE 1 — TITLE
# ══════════════════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank
set_slide_bg(slide)

# Large title
add_text_box(slide, 1.5, 1.8, 10.3, 1.2, "ChaosProbe",
             font_size=54, bold=True, color=WHITE, alignment=PP_ALIGN.CENTER)
add_text_box(slide, 1.5, 2.9, 10.3, 0.8,
             "Automated Kubernetes Chaos Testing with AI-Consumable Output",
             font_size=22, color=ACCENT_BLUE, alignment=PP_ALIGN.CENTER)

# Decorative line
dline = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE,
    Inches(4.5), Inches(3.8), Inches(4.3), Pt(3))
dline.fill.solid()
dline.fill.fore_color.rgb = ACCENT_BLUE
dline.line.fill.background()

add_text_box(slide, 1.5, 4.2, 10.3, 0.6,
             "MSc Thesis — University of Amsterdam",
             font_size=18, color=LIGHT_GRAY, alignment=PP_ALIGN.CENTER)
add_text_box(slide, 1.5, 4.8, 10.3, 0.5,
             "April 2026",
             font_size=14, color=MID_GRAY, alignment=PP_ALIGN.CENTER)

# Key metrics boxes at bottom
metrics = [
    ("19,262", "Lines of Code"),
    ("504", "Unit Tests"),
    ("63", "Source Files"),
    ("6", "Strategies"),
]
for i, (val, label) in enumerate(metrics):
    x = 2.5 + i * 2.3
    add_rounded_box(slide, x, 5.6, 1.8, 0.9, VERY_DARK,
                    border_color=ACCENT_BLUE)
    add_text_box(slide, x, 5.6, 1.8, 0.5, val,
                 font_size=22, bold=True, color=ACCENT_BLUE,
                 alignment=PP_ALIGN.CENTER)
    add_text_box(slide, x, 6.05, 1.8, 0.4, label,
                 font_size=11, color=LIGHT_GRAY, alignment=PP_ALIGN.CENTER)


# ══════════════════════════════════════════════════════════════════════
# SLIDE 2 — RESEARCH CONTEXT & MOTIVATION
# ══════════════════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)
slide_title(slide, "Research Context & Motivation")

# Left: Problem Statement
add_bullet_frame(slide, 0.6, 1.6, 5.5, 3.5, [
    "• Microservice resilience depends on pod placement,\n  but Kubernetes' default scheduler is topology-unaware",
    "• Recovery time, inter-service latency, and I/O throughput\n  all degrade under node resource contention",
    "• Existing chaos tools lack structured, ML-ready output",
    "• No feedback loop: test → diagnose → fix → re-test",
], font_size=14, title="Problem Statement", title_color=ACCENT_RED)

# Right: Hypothesis
add_bullet_frame(slide, 7.0, 1.6, 5.5, 3.5, [
    "• Microservice resilience under chaos varies with\n  placement due to node resource contention",
    "• Affected dimensions: pod recovery time, inter-service\n  latency, Redis/disk I/O, CPU/memory, cascade depth",
    "• Structured output enables AI-driven root cause\n  analysis and autonomous remediation",
    "• Graph storage preserves causal relationships\n  lost in flat metrics",
], font_size=14, title="Core Hypothesis", title_color=ACCENT_GREEN)

# Bottom: approach
add_rounded_box(slide, 0.6, 5.3, 12.1, 1.6, VERY_DARK,
                border_color=ACCENT_BLUE)
add_text_box(slide, 0.8, 5.35, 11.7, 0.4, "Approach",
             font_size=18, bold=True, color=ACCENT_BLUE)
add_text_box(slide, 0.8, 5.75, 11.7, 1.1,
    "Deploy → Mutate Placement → Inject Chaos → Collect Multi-Signal Telemetry → "
    "Store in Neo4j Graph → AI Reads & Diagnoses → Fix Manifests → Re-Run & Compare",
    font_size=14, color=LIGHT_GRAY)


# ══════════════════════════════════════════════════════════════════════
# SLIDE 3 — HIGH-LEVEL ARCHITECTURE DIAGRAM
# ══════════════════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)
slide_title(slide, "System Architecture")

# Layer labels on left
layers = [
    (1.5, "CLI Layer",        CLR_CLI),
    (2.5, "Orchestrator",     CLR_ORCH),
    (3.6, "Core Engines",     CLR_CHAOS),
    (4.7, "Metrics & Data",   CLR_METRICS),
    (5.8, "Storage & Output", CLR_STORAGE),
]
for y, label, clr in layers:
    add_text_box(slide, 0.2, y, 1.6, 0.35, label,
                 font_size=11, bold=True, color=clr, alignment=PP_ALIGN.RIGHT)

# ── CLI Row ──
cli_boxes = [
    ("chaosprobe init", 2.2),
    ("chaosprobe run", 4.2),
    ("chaosprobe delete", 6.2),
    ("chaosprobe graph", 8.2),
    ("chaosprobe visualize", 10.0),
]
for name, x in cli_boxes:
    add_rounded_box(slide, x, 1.45, 1.7, 0.4, CLR_CLI, name, 10, WHITE, True)

# ── Orchestrator Row ──
orch_boxes = [
    ("Preflight\nChecks", 2.2),
    ("Strategy\nRunner", 4.0),
    ("Run Phases\nOrchestrator", 5.8),
    ("Prober\nManager", 7.6),
    ("Port-Forward\nLifecycle", 9.4),
]
for name, x in orch_boxes:
    add_rounded_box(slide, x, 2.4, 1.5, 0.6, CLR_ORCH, name, 9, WHITE, True)

# ── Core Engines Row ──
core_boxes = [
    ("Config\nLoader", 2.0),
    ("Topology\nParser", 3.5),
    ("Placement\nEngine", 5.0),
    ("Chaos\nRunner", 6.5),
    ("Load\nGenerator", 8.0),
    ("Result\nCollector", 9.5),
    ("Probe\nBuilder", 11.0),
]
for name, x in core_boxes:
    add_rounded_box(slide, x, 3.5, 1.2, 0.6, CLR_CHAOS, name, 9, WHITE, True)

# ── Metrics & Data Row ──
met_boxes = [
    ("Recovery\nWatcher", 2.0),
    ("Latency\nProber", 3.5),
    ("Resource\nProber", 5.0),
    ("Prometheus\nProber", 6.5),
    ("Redis/Disk\nProber", 8.0),
    ("Anomaly\nLabels", 9.5),
    ("Cascade\nDetection", 11.0),
]
for name, x in met_boxes:
    add_rounded_box(slide, x, 4.6, 1.2, 0.6, CLR_METRICS, name, 9, WHITE, True)

# ── Storage & Output Row ──
stor_boxes = [
    ("Neo4j Graph\nStore", 2.5),
    ("ML Export\n(CSV/Parquet)", 4.5),
    ("Chart\nGenerator", 6.5),
    ("HTML\nReport", 8.5),
    ("AI Analysis\nPipeline", 10.5),
]
for name, x in stor_boxes:
    clr = CLR_STORAGE if x < 5 else (CLR_OUTPUT if x < 10 else CLR_AI)
    add_rounded_box(slide, x, 5.7, 1.5, 0.6, clr, name, 9, WHITE, True)

# ── Vertical arrows between layers ──
for x_off in [3.0, 5.0, 7.0, 9.0]:
    add_arrow(slide, x_off, 1.9, x_off, 2.4, LIGHT_GRAY, Pt(1.5))
    add_arrow(slide, x_off, 3.0, x_off, 3.5, LIGHT_GRAY, Pt(1.5))
    add_arrow(slide, x_off, 4.1, x_off, 4.6, LIGHT_GRAY, Pt(1.5))
    add_arrow(slide, x_off, 5.2, x_off, 5.7, LIGHT_GRAY, Pt(1.5))

# ── Legend ──
legend_items = [
    (CLR_CLI, "CLI"),
    (CLR_ORCH, "Orchestrator"),
    (CLR_CHAOS, "Core Engines"),
    (CLR_METRICS, "Metrics"),
    (CLR_STORAGE, "Storage"),
    (CLR_OUTPUT, "Output"),
    (CLR_AI, "AI Pipeline"),
]
for i, (clr, lbl) in enumerate(legend_items):
    y = 6.6 + (i * 0.12)
    x = 0.3 + (i * 1.7)
    add_rounded_box(slide, x, 6.85, 0.2, 0.2, clr)
    add_text_box(slide, x + 0.25, 6.83, 1.2, 0.25, lbl,
                 font_size=9, color=LIGHT_GRAY)


# ══════════════════════════════════════════════════════════════════════
# SLIDE 4 — INFRASTRUCTURE & CLUSTER TOPOLOGY
# ══════════════════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)
slide_title(slide, "Infrastructure & Cluster Topology")

# Cluster diagram — Proxmox host
add_rounded_box(slide, 0.6, 1.6, 12.1, 5.5, VERY_DARK,
                border_color=MID_GRAY, border_width=Pt(2))
add_text_box(slide, 0.8, 1.65, 3, 0.35, "Proxmox Host",
             font_size=14, bold=True, color=LIGHT_GRAY)

# Control plane
add_rounded_box(slide, 1.0, 2.2, 2.5, 1.8, RGBColor(0x22, 0x33, 0x55),
                border_color=ACCENT_BLUE)
add_text_box(slide, 1.0, 2.2, 2.5, 0.35, "  cp1 — Control Plane",
             font_size=12, bold=True, color=ACCENT_BLUE)
cp_items = [
    "  2 vCPU  •  2 GiB RAM",
    "  K8s API Server",
    "  etcd, scheduler",
    "  controller-manager",
]
add_bullet_frame(slide, 1.1, 2.55, 2.3, 1.4, cp_items,
                 font_size=9, color=LIGHT_GRAY)

# Worker nodes
workers = [
    ("worker1", "2 GiB", 3.8),
    ("worker2", "2 GiB", 6.1),
    ("worker3", "4 GiB", 8.4),
    ("worker4", "4 GiB", 10.8),
]
for name, ram, x in workers:
    add_rounded_box(slide, x, 2.2, 2.0, 1.8, RGBColor(0x22, 0x44, 0x22),
                    border_color=ACCENT_GREEN)
    add_text_box(slide, x, 2.2, 2.0, 0.35, f"  {name}",
                 font_size=12, bold=True, color=ACCENT_GREEN)
    add_bullet_frame(slide, x + 0.1, 2.55, 1.8, 1.4, [
        f"  2 vCPU  •  {ram} RAM",
        "  containerd 1.7.11",
        "  K8s v1.28.6",
    ], font_size=9, color=LIGHT_GRAY)

# Installed components row
add_text_box(slide, 0.8, 4.25, 12, 0.35, "Installed Infrastructure Components",
             font_size=14, bold=True, color=ACCENT_BLUE)

infra_data = [
    ["Namespace", "Component", "Method"],
    ["litmus", "ChaosCenter (frontend, server, auth, MongoDB)", "Helm chart"],
    ["litmus", "Litmus-core operator + CRDs", "Helm chart"],
    ["monitoring", "Prometheus + kube-state-metrics", "Helm chart"],
    ["neo4j", "Neo4j 5-community (512–768Mi)", "K8s Deployment"],
    ["kube-system", "metrics-server", "Official manifest"],
    ["online-boutique", "subscriber, chaos-operator, exporter", "ChaosCenter"],
]
add_table(slide, 0.8, 4.65, 11.5, 2.3, 7, 3, infra_data, font_size=10)


# ══════════════════════════════════════════════════════════════════════
# SLIDE 5 — EXPERIMENT LIFECYCLE (DATA FLOW)
# ══════════════════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)
slide_title(slide, "Experiment Lifecycle — Data Flow")

# Phase boxes in a pipeline
phases = [
    ("1. Configure",    "Load YAML\nParse Topology\nValidate Specs",    CLR_CLI,     0.3),
    ("2. Pre-flight",   "Check Nodes\nClean Stale CRDs\nPort-Forward",  CLR_ORCH,    2.5),
    ("3. Placement",    "Apply Strategy\nPatch nodeSelector\nWait Rollout", CLR_CHAOS, 4.7),
    ("4. Chaos + Metrics", "Run ChaosEngine\nCollect Telemetry\nPhase Markers", CLR_METRICS, 6.9),
    ("5. Collect",      "Results CRDs\nRecovery Cycles\nAnomaly Labels", CLR_METRICS, 9.1),
    ("6. Store & Output","Sync to Neo4j\nML Export\nCharts + HTML",     CLR_STORAGE, 11.3),
]
for title, desc, clr, x in phases:
    add_rounded_box(slide, x, 1.6, 1.9, 1.6, clr, "", 10, WHITE, True,
                    border_color=clr)
    add_text_box(slide, x + 0.05, 1.6, 1.8, 0.35, title,
                 font_size=12, bold=True, color=WHITE)
    add_text_box(slide, x + 0.1, 1.95, 1.7, 1.1, desc,
                 font_size=10, color=TRANS_WHITE)

# Arrows between phases
for i in range(len(phases) - 1):
    x1 = phases[i][3] + 1.9
    x2 = phases[i + 1][3]
    add_arrow(slide, x1, 2.4, x2, 2.4, LIGHT_GRAY, Pt(2))

# Strategy Loop detail
add_rounded_box(slide, 0.3, 3.6, 12.7, 3.5, VERY_DARK,
                border_color=ACCENT_BLUE, border_width=Pt(1.5))
add_text_box(slide, 0.5, 3.65, 12, 0.4, "Strategy Loop — For each of 6 strategies × N iterations",
             font_size=16, bold=True, color=ACCENT_BLUE)

# Inner iteration flow
iter_phases = [
    ("Settle\n(30s)", CLR_ORCH, 0.5),
    ("Start\nProbers", CLR_METRICS, 2.2),
    ("Start\nLocust", CLR_CLI, 3.9),
    ("Pre-Chaos\nBaseline", CLR_METRICS, 5.6),
    ("Run Chaos\n(120s)", CLR_CHAOS, 7.3),
    ("Post-Chaos\nRecovery", CLR_METRICS, 9.0),
    ("Collect &\nSync", CLR_STORAGE, 10.7),
]
for name, clr, x in iter_phases:
    add_rounded_box(slide, x, 4.3, 1.4, 0.8, clr, name, 10, WHITE, True)

for i in range(len(iter_phases) - 1):
    x1 = iter_phases[i][2] + 1.4
    x2 = iter_phases[i + 1][2]
    add_arrow(slide, x1, 4.7, x2, 4.7, LIGHT_GRAY, Pt(1.5))

# Phase timeline
add_text_box(slide, 0.5, 5.35, 12, 0.35, "Phase Timeline",
             font_size=13, bold=True, color=ACCENT_BLUE)
# Pre-chaos bar
add_rounded_box(slide, 0.5, 5.75, 3.5, 0.45, CLR_ORCH,
                "PreChaos (steady state)", 10, WHITE, True)
# During-chaos bar
add_rounded_box(slide, 4.2, 5.75, 4.5, 0.45, CLR_CHAOS,
                "DuringChaos (fault active, 120s)", 10, WHITE, True)
# Post-chaos bar
add_rounded_box(slide, 8.9, 5.75, 3.8, 0.45, CLR_METRICS,
                "PostChaos (recovery period)", 10, WHITE, True)

add_text_box(slide, 0.5, 6.3, 12.5, 0.6,
    "All 5 continuous probers (latency, Redis, disk, resources, Prometheus) + RecoveryWatcher "
    "run as background threads throughout all 3 phases with 2–10s polling intervals.",
    font_size=11, color=MID_GRAY)


# ══════════════════════════════════════════════════════════════════════
# SLIDE 6 — NEO4J GRAPH SCHEMA (DATA RELATIONS DIAGRAM)
# ══════════════════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)
slide_title(slide, "Neo4j Graph Schema — Data Relations")

# Central node: ChaosRun
add_rounded_box(slide, 5.4, 2.8, 2.5, 0.8, CLR_CHAOS,
                "ChaosRun", 16, WHITE, True)

# Surrounding nodes with relationships
nodes = [
    # (label, x, y, color, rel_label, arrow_from_chaos)
    ("PlacementStrategy",   1.5,  1.5, CLR_CLI,     "USED_STRATEGY"),
    ("Deployment",          4.0,  1.3, CLR_ORCH,    "TARGETED_BY"),
    ("RecoveryCycle",       8.5,  1.3, CLR_METRICS, "HAS_RECOVERY_CYCLE"),
    ("ExperimentResult",    9.5,  2.8, CLR_CHAOS,   "HAS_RESULT"),
    ("MetricsPhase",        9.5,  4.2, CLR_METRICS, "HAS_METRICS_PHASE"),
    ("MetricsSample",       8.0,  5.3, CLR_METRICS, "HAS_SAMPLE"),
    ("AnomalyLabel",        5.4,  5.3, CLR_CHAOS,   "HAS_ANOMALY_LABEL"),
    ("CascadeEvent",        2.8,  5.3, CLR_OUTPUT,  "HAS_CASCADE_EVENT"),
    ("PodSnapshot",         1.0,  4.0, CLR_ORCH,    "HAS_POD_SNAPSHOT"),
    ("ContainerLog",        1.0,  2.8, CLR_INFRA,   "HAS_CONTAINER_LOG"),
]

for label, x, y, clr, rel in nodes:
    add_rounded_box(slide, x, y, 1.9, 0.6, clr, label, 10, WHITE, True)
    # Draw line to ChaosRun center
    cx, cy = 6.65, 3.2  # ChaosRun center
    nx, ny = x + 0.95, y + 0.3  # node center
    add_arrow(slide, cx, cy, nx, ny, clr, Pt(1.5))

# Extra relationships
# ProbeResult from ExperimentResult
add_rounded_box(slide, 11.5, 2.8, 1.5, 0.5, RGBColor(0xC0, 0x39, 0x2B),
                "ProbeResult", 9, WHITE, True)
add_arrow(slide, 11.4, 3.05, 11.5, 3.05, CLR_CHAOS, Pt(1.5))

# Service nodes
add_rounded_box(slide, 4.0, 6.3, 1.5, 0.5, CLR_ORCH,
                "Service", 11, WHITE, True)
# AFFECTS from AnomalyLabel
add_arrow(slide, 6.35, 5.6, 4.75, 6.3, CLR_CHAOS, Pt(1.5))
# DEPENDS_ON (self-referencing)
add_text_box(slide, 3.6, 6.85, 2.2, 0.3, "DEPENDS_ON (service→service)",
             font_size=9, color=MID_GRAY)

# K8sNode
add_rounded_box(slide, 7.5, 6.3, 1.5, 0.5, CLR_INFRA,
                "K8sNode", 11, WHITE, True)
# SCHEDULED_ON from Deployment
add_text_box(slide, 7.2, 6.85, 2.0, 0.3, "SCHEDULED_ON, RUNNING_ON",
             font_size=9, color=MID_GRAY)

# Relationship labels
add_text_box(slide, 0.5, 1.6, 1.2, 0.3, "USED_STRATEGY", font_size=8, color=CLR_CLI)
add_text_box(slide, 3.4, 1.0, 1.5, 0.3, "TARGETED_BY", font_size=8, color=CLR_ORCH)
add_text_box(slide, 8.2, 1.0, 2.0, 0.3, "HAS_RECOVERY_CYCLE", font_size=8, color=CLR_METRICS)
add_text_box(slide, 10.3, 2.5, 1.5, 0.3, "HAS_RESULT", font_size=8, color=CLR_CHAOS)
add_text_box(slide, 10.3, 3.95, 2.0, 0.3, "HAS_METRICS_PHASE", font_size=8, color=CLR_METRICS)
add_text_box(slide, 8.5, 5.0, 1.5, 0.3, "HAS_SAMPLE", font_size=8, color=CLR_METRICS)
add_text_box(slide, 5.3, 5.0, 2.0, 0.3, "HAS_ANOMALY_LABEL", font_size=8, color=CLR_CHAOS)
add_text_box(slide, 2.3, 5.0, 2.0, 0.3, "HAS_CASCADE_EVENT", font_size=8, color=CLR_OUTPUT)

# Node count badge
add_rounded_box(slide, 10.0, 6.5, 2.7, 0.6, VERY_DARK,
                "14 Node Types\n15 Relationships", 11, ACCENT_PURPLE, True,
                border_color=ACCENT_PURPLE)

# Name property note
add_text_box(slide, 0.5, 6.85, 9.0, 0.4,
    "All nodes have a display-friendly 'name' property for Neo4j Browser graph visualization "
    "(e.g. 'spread (FAIL)', 'cycle #3 (1464ms)', 'pod-delete (critical)')",
    font_size=10, color=MID_GRAY)


# ══════════════════════════════════════════════════════════════════════
# SLIDE 7 — PLACEMENT STRATEGIES
# ══════════════════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)
slide_title(slide, "Placement Strategies")

# Visual diagrams for each strategy
strat_data = [
    ("Baseline", "Default scheduler\nNo fault injection\n(control group)",
     CLR_ORCH, [(0.4, 0.2), (1.1, 0.2), (0.4, 0.6), (1.1, 0.6)]),
    ("Default", "Default scheduler\nFull chaos injection",
     CLR_CLI, [(0.4, 0.2), (1.1, 0.2), (0.4, 0.6), (1.1, 0.6)]),
    ("Colocate", "ALL pods → single node\nMax contention",
     CLR_CHAOS, [(0.4, 0.2), (0.65, 0.35), (0.4, 0.5), (0.65, 0.65)]),
    ("Spread", "Round-robin across\nall worker nodes",
     CLR_METRICS, [(0.2, 0.4), (0.6, 0.4), (1.0, 0.4), (1.4, 0.4)]),
    ("Random", "Random per-deployment\nReproducible via seed",
     CLR_OUTPUT, [(0.3, 0.2), (1.0, 0.6), (0.3, 0.6), (1.0, 0.2)]),
    ("Antagonistic", "Heavy pods → 1 node\nLight pods → spread",
     CLR_AI, [(0.3, 0.2), (0.55, 0.35), (1.0, 0.4), (1.3, 0.6)]),
]

for i, (name, desc, clr, dots) in enumerate(strat_data):
    col = i % 3
    row = i // 3
    bx = 0.5 + col * 4.2
    by = 1.5 + row * 2.9

    add_rounded_box(slide, bx, by, 3.8, 2.5, VERY_DARK,
                    border_color=clr, border_width=Pt(2))
    add_text_box(slide, bx + 0.1, by + 0.05, 3.6, 0.35, name,
                 font_size=16, bold=True, color=clr)
    add_text_box(slide, bx + 0.1, by + 0.4, 2.0, 0.8, desc,
                 font_size=10, color=LIGHT_GRAY)

    # Mini node diagram
    for dx, dy in dots:
        node_x = bx + 2.0 + dx
        node_y = by + 0.8 + dy
        add_rounded_box(slide, node_x, node_y, 0.25, 0.25, clr)

    # Contention label
    contention_map = {
        "Baseline": "None",
        "Default": "Low",
        "Colocate": "Maximum",
        "Spread": "Minimum",
        "Random": "Variable",
        "Antagonistic": "High",
    }
    cont = contention_map[name]
    cont_clr = (ACCENT_GREEN if cont in ("None", "Low", "Minimum")
                else ACCENT_RED if cont in ("Maximum", "High") else ACCENT_ORANGE)
    add_text_box(slide, bx + 0.1, by + 2.1, 3.6, 0.3,
                 f"Contention: {cont}", font_size=11, bold=True, color=cont_clr)


# ══════════════════════════════════════════════════════════════════════
# SLIDE 8 — METRICS COLLECTION
# ══════════════════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)
slide_title(slide, "Multi-Signal Metrics Collection")

# Probers table
prober_data = [
    ["Prober", "Signal", "Source", "Interval", "Phase Tracking"],
    ["RecoveryWatcher", "Pod deletion→scheduled→ready", "K8s Watch API", "Real-time", "✓"],
    ["LatencyProber", "HTTP route latency + errors", "In-cluster HTTP", "~2s", "✓"],
    ["RedisProber", "Read/write ops/s + latency", "Redis commands", "~10s", "✓"],
    ["DiskProber", "Sequential R/W ops/s + bytes/s", "dd commands", "~10s", "✓"],
    ["ResourceProber", "Node/pod CPU + memory", "K8s Metrics API", "~5s", "✓"],
    ["PrometheusProber", "pod_ready, CPU throttle, net I/O", "PromQL queries", "~10s", "✓"],
]
add_table(slide, 0.5, 1.5, 12.3, 2.8, 7, 5, prober_data, font_size=11)

# Phase explanation
add_text_box(slide, 0.5, 4.5, 12.3, 0.35, "Three-Phase Collection",
             font_size=18, bold=True, color=ACCENT_BLUE)

# Timeline bar
phases_bar = [
    ("PreChaos", "Steady-state baseline\n(30s default)", CLR_ORCH, 0.5, 3.5),
    ("DuringChaos", "Active fault injection\n(120s chaos duration)", CLR_CHAOS, 4.2, 4.5),
    ("PostChaos", "Recovery observation\n(until probers stop)", CLR_METRICS, 8.9, 3.8),
]
for name, desc, clr, x, w in phases_bar:
    add_rounded_box(slide, x, 5.0, w, 0.7, clr, "", 10, WHITE)
    add_text_box(slide, x + 0.1, 5.0, w - 0.2, 0.3, name,
                 font_size=12, bold=True, color=WHITE)
    add_text_box(slide, x + 0.1, 5.3, w - 0.2, 0.4, desc,
                 font_size=9, color=TRANS_WHITE)

# Time alignment note
add_text_box(slide, 0.5, 5.9, 12.3, 0.6,
    "All prober data is aligned into unified time buckets (5s resolution) via timeseries.py, "
    "enabling per-sample ML labeling: each bucket gets anomaly_label = fault_type or 'none'",
    font_size=12, color=MID_GRAY)

# Recovery cycle detail
add_rounded_box(slide, 0.5, 6.5, 5.5, 0.8, VERY_DARK,
                border_color=ACCENT_GREEN)
add_text_box(slide, 0.6, 6.5, 5.3, 0.3, "Recovery Cycle Structure",
             font_size=13, bold=True, color=ACCENT_GREEN)
add_text_box(slide, 0.6, 6.85, 5.3, 0.4,
    "DELETED → PodScheduled → Ready\nTracks: deletion_to_scheduled_ms, scheduled_to_ready_ms, total_recovery_ms",
    font_size=10, color=LIGHT_GRAY)

# Signal hierarchy
add_rounded_box(slide, 6.5, 6.5, 6.3, 0.8, VERY_DARK,
                border_color=ACCENT_ORANGE)
add_text_box(slide, 6.6, 6.5, 6.1, 0.3, "Signal Reliability Hierarchy",
             font_size=13, bold=True, color=ACCENT_ORANGE)
add_text_box(slide, 6.6, 6.85, 6.1, 0.4,
    "HIGH: Recovery cycles, Load generator, Probe verdicts  •  "
    "MEDIUM: Resources, Prometheus  •  CONTROL: Redis  •  LOW: Cascade",
    font_size=10, color=LIGHT_GRAY)


# ══════════════════════════════════════════════════════════════════════
# SLIDE 9 — PROBE DESIGN & RESILIENCE SCORING
# ══════════════════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)
slide_title(slide, "Probe Design & Resilience Scoring")

# Probe table
probe_data = [
    ["Probe", "Type", "Mode", "Tolerance", "Purpose"],
    ["frontend-product-strict", "httpProbe", "Continuous (2s)", "3s, 1 retry", "Confirm disruption"],
    ["frontend-homepage-strict", "httpProbe", "Continuous (2s)", "3s, 1 retry", "Confirm disruption"],
    ["frontend-homepage-moderate", "httpProbe", "Continuous (3s)", "3s, 2 retries", "Fast recovery detection"],
    ["frontend-cart", "httpProbe", "Continuous (4s)", "5s, 2 retries", "Node contention control"],
    ["frontend-homepage-edge", "httpProbe", "Edge (5s)", "15s, 5 retries", "Eventual recovery"],
    ["frontend-healthz", "httpProbe", "Continuous (4s)", "5s, 2 retries", "Node-level pressure"],
]
add_table(slide, 0.3, 1.5, 12.7, 2.8, 7, 5, probe_data, font_size=10)

# Score interpretation
add_text_box(slide, 0.5, 4.5, 12, 0.35, "Resilience Score Interpretation",
             font_size=18, bold=True, color=ACCENT_BLUE)

scores = [
    ("0%",  "All 6 probes failed — total disruption"),
    ("17%", "Only healthz passed — node alive but service down"),
    ("33%", "healthz + edge — eventual recovery only"),
    ("50%", "3 probes passed — moderate resilience"),
    ("67%", "4 probes (tolerant + edge + cart + healthz)"),
    ("83%", "5 probes passed — fast recovery"),
    ("100%","All probes passed — no visible disruption"),
]
for i, (score, meaning) in enumerate(scores):
    x = 0.5
    y = 4.95 + i * 0.3
    # Score badge
    pct = int(score.replace("%", ""))
    badge_clr = (ACCENT_RED if pct <= 33
                 else ACCENT_ORANGE if pct <= 67
                 else ACCENT_GREEN)
    add_rounded_box(slide, x, y, 0.6, 0.25, badge_clr,
                    score, 10, WHITE, True)
    add_text_box(slide, x + 0.7, y, 8, 0.25, meaning,
                 font_size=11, color=LIGHT_GRAY)

# Formula
add_rounded_box(slide, 7.0, 4.95, 5.8, 2.2, VERY_DARK,
                border_color=ACCENT_BLUE)
add_text_box(slide, 7.2, 4.95, 5.4, 0.35, "Scoring Formula",
             font_size=14, bold=True, color=ACCENT_BLUE)
add_text_box(slide, 7.2, 5.35, 5.4, 0.6,
    "score = Σ(probeSuccess% × weight) / Σ(weight)\n"
    "verdict = PASS if all experiments pass, else FAIL\n"
    "Default weight: 1.0 per experiment",
    font_size=11, color=LIGHT_GRAY)

add_text_box(slide, 7.2, 6.0, 5.4, 0.35, "Fix Effectiveness",
             font_size=14, bold=True, color=ACCENT_GREEN)
add_text_box(slide, 7.2, 6.35, 5.4, 0.6,
    "confidence = 0.50 (base) + 0.25 (verdict change)\n"
    "+ min(0.15, Δscore/100) + 0.10 (all improved)\n"
    "fixEffective = verdict FAIL→PASS or Δscore ≥ 20",
    font_size=11, color=LIGHT_GRAY)


# ══════════════════════════════════════════════════════════════════════
# SLIDE 10 — ONLINE BOUTIQUE APPLICATION
# ══════════════════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)
slide_title(slide, "Target Application — Online Boutique")

# Service dependency diagram
add_text_box(slide, 0.5, 1.5, 12, 0.35, "12 Microservices — Service Dependency Graph",
             font_size=16, bold=True, color=ACCENT_BLUE)

# Frontend at top
add_rounded_box(slide, 5.5, 2.1, 2.3, 0.5, CLR_CLI,
                "frontend", 12, WHITE, True)

# Second tier
tier2 = [
    ("productcatalog\nservice", 1.0, CLR_CHAOS),
    ("currency\nservice", 3.5, CLR_ORCH),
    ("cart\nservice", 6.0, CLR_ORCH),
    ("recommendation\nservice", 8.5, CLR_ORCH),
    ("ad\nservice", 11.0, CLR_ORCH),
]
for name, x, clr in tier2:
    add_rounded_box(slide, x, 2.9, 1.8, 0.7, clr, name, 9, WHITE, True)
    add_arrow(slide, 6.65, 2.6, x + 0.9, 2.9, LIGHT_GRAY, Pt(1))

# Third tier
add_rounded_box(slide, 3.5, 4.0, 1.8, 0.6, CLR_ORCH,
                "checkout\nservice", 9, WHITE, True)
add_arrow(slide, 6.65, 2.6, 4.4, 4.0, LIGHT_GRAY, Pt(1))

# Bottom tier from checkout
tier3 = [
    ("email\nservice", 0.5),
    ("payment\nservice", 2.5),
    ("shipping\nservice", 4.5),
]
for name, x in tier3:
    add_rounded_box(slide, x, 4.9, 1.6, 0.6, CLR_ORCH, name, 9, WHITE, True)
    add_arrow(slide, 4.4, 4.6, x + 0.8, 4.9, LIGHT_GRAY, Pt(1))

# Redis
add_rounded_box(slide, 6.5, 4.0, 1.5, 0.5, ACCENT_RED,
                "redis-cart", 10, WHITE, True)
add_arrow(slide, 6.9, 3.6, 7.25, 4.0, LIGHT_GRAY, Pt(1))

# Chaos target highlight
add_rounded_box(slide, 0.7, 2.7, 2.4, 1.1, RGBColor(0,0,0),
                border_color=ACCENT_RED, border_width=Pt(3))
add_text_box(slide, 0.5, 3.85, 2.8, 0.3, "← Chaos Target (pod-delete)",
             font_size=10, bold=True, color=ACCENT_RED)

# productcatalogservice explanation
add_rounded_box(slide, 7.5, 5.8, 5.3, 1.5, VERY_DARK,
                border_color=ACCENT_RED)
add_text_box(slide, 7.6, 5.8, 5.1, 0.35, "Why productcatalogservice?",
             font_size=14, bold=True, color=ACCENT_RED)
add_bullet_frame(slide, 7.7, 6.15, 5.0, 1.1, [
    "• Central dependency for homepage, product pages,\n  recommendations, and search results",
    "• 100% pod deletion: PODS_AFFECTED_PERC=100%,\n  CHAOS_INTERVAL=5s, ~24 deletions per run",
    "• 120s chaos duration with FORCE=true",
], font_size=10, color=LIGHT_GRAY)

# Load profiles
add_rounded_box(slide, 0.5, 5.8, 6.5, 1.5, VERY_DARK,
                border_color=CLR_CLI)
add_text_box(slide, 0.6, 5.8, 6.3, 0.35, "Load Generation Profiles (Locust)",
             font_size=14, bold=True, color=CLR_CLI)
load_data = [
    ["Profile", "Users", "Spawn Rate", "Duration"],
    ["steady", "50", "10/s", "120s"],
    ["ramp", "100", "5/s", "180s"],
    ["spike", "200", "50/s", "90s"],
]
add_table(slide, 0.6, 6.2, 6.2, 1.0, 4, 4, load_data, font_size=10)


# ══════════════════════════════════════════════════════════════════════
# SLIDE 11 — ML/AI PIPELINE
# ══════════════════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)
slide_title(slide, "ML Export & AI Analysis Pipeline")

# ML Export pipeline flow
add_text_box(slide, 0.5, 1.5, 12, 0.35, "ML Dataset Export Pipeline",
             font_size=16, bold=True, color=ACCENT_BLUE)

ml_steps = [
    ("Neo4j\nGraph", CLR_STORAGE, 0.5),
    ("MetricsSample\n+ AnomalyLabel\nJoin", CLR_METRICS, 2.5),
    ("Time-Series\nAlignment\n(5s buckets)", CLR_ORCH, 4.7),
    ("Per-Sample\nAnomaly\nLabeling", CLR_CHAOS, 6.9),
    ("CSV / Parquet\nDataset", CLR_OUTPUT, 9.1),
    ("sklearn /\nPyTorch\nModel", CLR_AI, 11.3),
]
for name, clr, x in ml_steps:
    add_rounded_box(slide, x, 1.95, 1.7, 0.9, clr, name, 10, WHITE, True)

for i in range(len(ml_steps) - 1):
    x1 = ml_steps[i][2] + 1.7
    x2 = ml_steps[i + 1][2]
    add_arrow(slide, x1, 2.4, x2, 2.4, LIGHT_GRAY, Pt(2))

# Output columns
add_rounded_box(slide, 0.5, 3.2, 6.0, 1.5, VERY_DARK,
                border_color=ACCENT_BLUE)
add_text_box(slide, 0.6, 3.2, 5.8, 0.3, "Dataset Columns",
             font_size=13, bold=True, color=ACCENT_BLUE)
add_bullet_frame(slide, 0.7, 3.5, 5.7, 1.2, [
    "Metadata: run_id, timestamp, phase, strategy, resilience_score",
    "Labels: anomaly_label (fault_type or 'none'), overall_verdict",
    "Latency: latency:<route>:ms, latency:<route>:error",
    "Resources: node/pod CPU/memory, pod_count",
    "I/O: redis:<op>:ops_per_s, disk:<op>:bytes_per_s",
    "Recovery: recovery_in_progress, recovery_cycle_id",
], font_size=10, color=LIGHT_GRAY)

# Anomaly label types
add_rounded_box(slide, 6.8, 3.2, 6.0, 1.5, VERY_DARK,
                border_color=ACCENT_RED)
add_text_box(slide, 6.9, 3.2, 5.8, 0.3, "Supported Anomaly Types (13 faults)",
             font_size=13, bold=True, color=ACCENT_RED)
anom_data = [
    ["Category", "Fault Types"],
    ["Availability", "pod-delete, node-drain, kubelet-service-kill"],
    ["Saturation", "cpu-hog, memory-hog, io-stress, disk-fill"],
    ["Network", "loss, latency, corruption, duplication"],
]
add_table(slide, 6.9, 3.55, 5.7, 1.05, 4, 2, anom_data,
          font_size=10, header_color=ACCENT_RED)

# AI Analysis Flow
add_text_box(slide, 0.5, 5.0, 12, 0.35, "AI Analysis — 9-Step Diagnostic Pipeline",
             font_size=16, bold=True, color=CLR_AI)

ai_steps = [
    "1. Fault\nIdentification",
    "2. Root Cause\nAnalysis",
    "3. Impact\nAssessment",
    "4. Temporal\nAnalysis",
    "5. Probe\nAnalysis",
    "6. Diagnosis &\nMitigation",
    "7. Cross-Run\nComparison",
    "8. Cross-Exp.\nPatterns",
    "9. Executive\nSummary",
]
for i, step in enumerate(ai_steps):
    x = 0.3 + i * 1.42
    add_rounded_box(slide, x, 5.5, 1.3, 0.7, CLR_AI, step, 8, WHITE, True)
    if i < len(ai_steps) - 1:
        add_arrow(slide, x + 1.3, 5.85, x + 1.42, 5.85, LIGHT_GRAY, Pt(1.5))

# Autonomous fix loop
add_rounded_box(slide, 0.5, 6.5, 12.3, 0.8, VERY_DARK,
                border_color=CLR_AI, border_width=Pt(2))
add_text_box(slide, 0.7, 6.5, 11.9, 0.3, "Autonomous Fix Loop",
             font_size=14, bold=True, color=CLR_AI)
add_text_box(slide, 0.7, 6.85, 11.9, 0.4,
    "delete → init → verify → run → AI reads results → diagnoses (score=0? probes timing out; "
    "no recovery? ChaosCenter failing) → fixes code/config/cluster → re-run → compare → loop",
    font_size=11, color=LIGHT_GRAY)


# ══════════════════════════════════════════════════════════════════════
# SLIDE 12 — CASCADE DETECTION & GRAPH ANALYSIS
# ══════════════════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)
slide_title(slide, "Cascade Detection & Graph Analysis")

# Cascade detection algorithm
add_rounded_box(slide, 0.5, 1.5, 6.0, 3.3, VERY_DARK,
                border_color=ACCENT_ORANGE)
add_text_box(slide, 0.6, 1.5, 5.8, 0.35, "Cascade Detection Algorithm",
             font_size=16, bold=True, color=ACCENT_ORANGE)
add_bullet_frame(slide, 0.7, 1.9, 5.7, 2.8, [
    "1. Establish pre-chaos baseline per route\n   (mean latency + error rate)",
    "2. During chaos: compare each sample against baseline\n   degraded = latency > baseline × 2.0 OR status ≠ 'ok'",
    "3. Walk time-series chronologically per route:\n   record degradation start/end, peak latency, errors",
    "4. Output: cascadeTimeline with affected routes,\n   timing, and cascadeRatio (affected/total)",
    "5. Identifies fault propagation across service\n   dependency chains (e.g., productcatalog → frontend)",
], font_size=11, color=LIGHT_GRAY)

# Graph analysis functions
add_rounded_box(slide, 6.8, 1.5, 6.0, 3.3, VERY_DARK,
                border_color=ACCENT_PURPLE)
add_text_box(slide, 6.9, 1.5, 5.8, 0.35, "Neo4j Graph Analysis Functions",
             font_size=16, bold=True, color=ACCENT_PURPLE)

graph_funcs = [
    ["Function", "Purpose"],
    ["blast_radius_report()", "Upstream services affected by failure"],
    ["topology_comparison()", "Compare placements across runs"],
    ["colocation_impact()", "Resource contention from co-location"],
    ["critical_path_analysis()", "Longest dependency chain"],
    ["strategy_summary()", "Outcomes grouped by strategy"],
]
add_table(slide, 6.9, 2.0, 5.7, 2.5, 6, 2, graph_funcs,
          font_size=10, header_color=ACCENT_PURPLE)

# Cascade example diagram
add_text_box(slide, 0.5, 5.1, 12, 0.35, "Example: pod-delete on productcatalogservice — Cascade Propagation",
             font_size=14, bold=True, color=ACCENT_ORANGE)

# Timeline
cascade_svc = [
    ("productcatalog", 0.7, CLR_CHAOS, 2.0, 8.5),
    ("frontend", 2.7, CLR_OUTPUT, 3.0, 8.0),
    ("recommendation", 4.7, CLR_OUTPUT, 3.5, 7.5),
    ("checkout", 6.7, CLR_ORCH, 4.0, 6.5),
]
# Time axis
add_arrow(slide, 0.5, 6.9, 12.5, 6.9, LIGHT_GRAY, Pt(1))
add_text_box(slide, 0.5, 6.95, 1, 0.3, "t=0s", font_size=9, color=MID_GRAY)
add_text_box(slide, 3.5, 6.95, 1, 0.3, "t=30s", font_size=9, color=MID_GRAY)
add_text_box(slide, 6.5, 6.95, 1, 0.3, "t=60s", font_size=9, color=MID_GRAY)
add_text_box(slide, 9.5, 6.95, 1, 0.3, "t=90s", font_size=9, color=MID_GRAY)
add_text_box(slide, 12.0, 6.95, 1, 0.3, "t=120s", font_size=9, color=MID_GRAY)

for svc_name, y_off, clr, start_x, end_x in cascade_svc:
    # Service label
    add_text_box(slide, 0.2, 5.35 + y_off * 0.2, 1.8, 0.3, svc_name,
                 font_size=9, color=clr, alignment=PP_ALIGN.RIGHT)
    # Degradation bar
    bar_y = 5.4 + y_off * 0.2
    bar_w = (end_x - start_x) * 0.8
    add_rounded_box(slide, start_x, bar_y, bar_w, 0.2, clr)


# ══════════════════════════════════════════════════════════════════════
# SLIDE 13 — MODULE ARCHITECTURE DETAIL
# ══════════════════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)
slide_title(slide, "Module Architecture — Package Map")

packages = [
    ("config/", "loader.py, topology.py, validator.py",
     "YAML loading, service graph extraction, ChaosEngine validation",
     CLR_CLI, 0.5, 1.5),
    ("collector/", "result_collector.py",
     "ChaosResult CRDs, probe statuses, resilience scoring",
     CLR_ORCH, 0.5, 2.6),
    ("chaos/", "runner.py, manifest.py",
     "ChaosCenter GraphQL: save → trigger → poll experiments",
     CLR_CHAOS, 0.5, 3.7),
    ("metrics/", "base.py, latency.py, recovery.py, resources.py,\nprometheus.py, throughput.py, cascade.py, anomaly_labels.py",
     "6 continuous probers + cascade + anomaly + time-series",
     CLR_METRICS, 0.5, 4.8),
    ("placement/", "strategy.py, mutator.py",
     "4 strategies + K8s nodeSelector patching",
     RGBColor(0xE9, 0x1E, 0x63), 6.8, 1.5),
    ("orchestrator/", "strategy_runner.py, run_phases.py,\nprobers.py, portforward.py, preflight.py",
     "RunContext + phase orchestration + port-forward lifecycle",
     CLR_ORCH, 6.8, 2.6),
    ("storage/", "neo4j_store.py, neo4j_writer.py, neo4j_reader.py",
     "Neo4j graph DB — write 14 node types, read + reconstruct",
     CLR_STORAGE, 6.8, 3.7),
    ("output/", "generator.py, ml_export.py, visualize.py,\ncharts.py, comparison.py",
     "JSON output, ML CSV/Parquet, matplotlib charts, HTML report",
     CLR_OUTPUT, 6.8, 4.8),
]
for name, files, desc, clr, x, y in packages:
    add_rounded_box(slide, x, y, 5.9, 0.9, VERY_DARK,
                    border_color=clr, border_width=Pt(1.5))
    add_text_box(slide, x + 0.1, y, 1.5, 0.3, name,
                 font_size=13, bold=True, color=clr)
    add_text_box(slide, x + 1.5, y, 4.2, 0.3, files,
                 font_size=9, color=LIGHT_GRAY)
    add_text_box(slide, x + 0.1, y + 0.45, 5.7, 0.4, desc,
                 font_size=10, color=MID_GRAY)

# Additional modules
extras = [
    ("loadgen/", "Locust runner", CLR_CLI, 0.5),
    ("probes/", "Rust cmdProbe builder", CLR_CHAOS, 2.5),
    ("graph/", "Neo4j analysis queries", CLR_STORAGE, 4.5),
    ("provisioner/", "Vagrant, K8s, ChaosCenter, Helm", CLR_INFRA, 6.8),
    ("commands/", "10 Click CLI modules", CLR_CLI, 9.5),
]
for name, desc, clr, x in extras:
    add_rounded_box(slide, x, 6.0, 1.8, 0.7, VERY_DARK,
                    border_color=clr)
    add_text_box(slide, x + 0.05, 6.0, 1.7, 0.3, name,
                 font_size=11, bold=True, color=clr)
    add_text_box(slide, x + 0.05, 6.3, 1.7, 0.3, desc,
                 font_size=9, color=LIGHT_GRAY)

# Stats
add_rounded_box(slide, 3.5, 6.9, 6.3, 0.45, VERY_DARK,
                border_color=ACCENT_BLUE)
add_text_box(slide, 3.6, 6.9, 6.1, 0.4,
    "63 source files  •  19,262 lines  •  504 tests  •  34 modules  •  7 packages",
    font_size=12, bold=True, color=ACCENT_BLUE, alignment=PP_ALIGN.CENTER)


# ══════════════════════════════════════════════════════════════════════
# SLIDE 14 — DETAILED DATA FLOW DIAGRAM
# ══════════════════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)
slide_title(slide, "Data Flow Diagram — End-to-End")

# Input layer
add_text_box(slide, 0.3, 1.4, 2.0, 0.3, "INPUT", font_size=12,
             bold=True, color=CLR_CLI)
input_boxes = [
    ("experiment.yaml\n(ChaosEngine specs)", 0.3, 1.7),
    ("deploy/\n(K8s manifests)", 0.3, 2.5),
    ("probes/\n(Rust cmdProbes)", 0.3, 3.3),
]
for name, x, y in input_boxes:
    add_rounded_box(slide, x, y, 2.0, 0.65, CLR_CLI, name, 9, WHITE, True)

# Processing layer
add_text_box(slide, 2.8, 1.4, 2.5, 0.3, "PROCESSING", font_size=12,
             bold=True, color=CLR_ORCH)
proc_boxes = [
    ("Config Loader\n→ Topology", 2.8, 1.7, CLR_ORCH),
    ("Placement Mutator\n→ nodeSelector", 2.8, 2.5, CLR_CHAOS),
    ("ChaosRunner\n→ ChaosCenter API", 2.8, 3.3, CLR_CHAOS),
    ("Load Generator\n→ Locust", 2.8, 4.1, CLR_CLI),
]
for name, x, y, clr in proc_boxes:
    add_rounded_box(slide, x, y, 2.2, 0.65, clr, name, 9, WHITE, True)
    add_arrow(slide, 2.3, y + 0.32, x, y + 0.32, LIGHT_GRAY, Pt(1.5))

# Collection layer
add_text_box(slide, 5.5, 1.4, 2.5, 0.3, "COLLECTION", font_size=12,
             bold=True, color=CLR_METRICS)
coll_boxes = [
    ("RecoveryWatcher\n(K8s Watch)", 5.5, 1.7),
    ("5 Continuous\nProbers (threads)", 5.5, 2.5),
    ("ResultCollector\n(ChaosResult CRDs)", 5.5, 3.3),
    ("MetricsCollector\n(merge all data)", 5.5, 4.1),
    ("AnomalyLabels\n(ground truth)", 5.5, 4.9),
]
for name, x, y in coll_boxes:
    add_rounded_box(slide, x, y, 2.2, 0.65, CLR_METRICS, name, 9, WHITE, True)
    add_arrow(slide, 5.0, y + 0.32, x, y + 0.32, LIGHT_GRAY, Pt(1.5))

# Storage layer
add_text_box(slide, 8.2, 1.4, 2.5, 0.3, "STORAGE", font_size=12,
             bold=True, color=CLR_STORAGE)
add_rounded_box(slide, 8.2, 1.7, 2.0, 1.6, CLR_STORAGE,
                "Neo4j\nGraph Store\n\n14 node types\n15 relationships", 10, WHITE, True)
add_arrow(slide, 7.7, 2.5, 8.2, 2.5, LIGHT_GRAY, Pt(2))

# Output layer
add_text_box(slide, 8.2, 3.6, 2.5, 0.3, "OUTPUT", font_size=12,
             bold=True, color=CLR_OUTPUT)
output_boxes = [
    ("ML Dataset\n(CSV/Parquet)", 8.2, 3.9),
    ("Charts\n(matplotlib)", 8.2, 4.7),
    ("HTML Report\n(summary)", 8.2, 5.5),
]
for name, x, y in output_boxes:
    add_rounded_box(slide, x, y, 2.0, 0.65, CLR_OUTPUT, name, 9, WHITE, True)
    add_arrow(slide, 9.2, 3.3, 9.2, y, CLR_STORAGE, Pt(1.5))

# AI layer
add_text_box(slide, 10.8, 1.4, 2.5, 0.3, "AI FEEDBACK", font_size=12,
             bold=True, color=CLR_AI)
ai_boxes = [
    ("9-Step\nDiagnostic", 10.8, 1.7),
    ("Root Cause\nAnalysis", 10.8, 2.5),
    ("Manifest\nFix", 10.8, 3.3),
    ("Re-Run &\nCompare", 10.8, 4.1),
]
for name, x, y in ai_boxes:
    add_rounded_box(slide, x, y, 1.8, 0.65, CLR_AI, name, 9, WHITE, True)
    if y == 1.7:
        add_arrow(slide, 10.2, y + 0.32, x, y + 0.32, LIGHT_GRAY, Pt(1.5))

# Feedback arrow (AI → Input)
add_arrow(slide, 11.7, 4.75, 11.7, 5.6, CLR_AI, Pt(2))
add_arrow(slide, 11.7, 5.6, 0.7, 5.6, CLR_AI, Pt(2))
add_arrow(slide, 0.7, 5.6, 0.7, 3.95, CLR_AI, Pt(2))
add_text_box(slide, 4.5, 5.65, 4.0, 0.3,
             "← AI Feedback Loop (edit manifests → re-run)",
             font_size=10, bold=True, color=CLR_AI)

# Vertical arrows in AI column
for i in range(len(ai_boxes) - 1):
    y1 = ai_boxes[i][2] + 0.65
    y2 = ai_boxes[i + 1][2]
    add_arrow(slide, 11.7, y1, 11.7, y2, CLR_AI, Pt(1.5))


# ══════════════════════════════════════════════════════════════════════
# SLIDE 15 — KEY DESIGN DECISIONS
# ══════════════════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)
slide_title(slide, "Key Design Decisions")

decisions = [
    ("Neo4j as Sole Data Store",
     "Graph database preserves causal relationships between faults, "
     "recovery cycles, and metrics. Enables Cypher-based blast radius "
     "analysis and dependency-aware queries lost in flat file storage.",
     ACCENT_PURPLE),
    ("ChaosCenter GraphQL (not raw CRDs)",
     "All experiments go through ChaosCenter API for dashboard visibility, "
     "audit trail, and experiment management. Wraps ChaosEngine in Argo Workflow "
     "with automatic RBAC and lifecycle management.",
     CLR_CHAOS),
    ("Background Thread Probers",
     "Continuous probers run as daemon threads with phase markers, enabling "
     "real-time multi-signal collection without blocking the chaos runner. "
     "All probers share ContinuousProberBase for lifecycle management.",
     CLR_METRICS),
    ("Structured AI-Consumable Output",
     "Every output field is designed for LLM consumption: structured JSON schema, "
     "explicit anomaly labels, signal reliability hierarchy, and 9-step diagnostic "
     "prompt that enables autonomous fix loops.",
     CLR_AI),
    ("6-Probe Scoring Granularity",
     "Probes are designed to produce 7 distinct score levels (0–100% in ~17% steps), "
     "each mapping to a specific resilience state. Includes controls for "
     "blast radius validation (cart, healthz probes).",
     ACCENT_ORANGE),
    ("Reproducible Randomness",
     "Random and antagonistic strategies use seeded PRNGs for reproducible "
     "experiments. Same seed → same placement → comparable results across runs.",
     CLR_ORCH),
]

for i, (title, desc, clr) in enumerate(decisions):
    col = i % 2
    row = i // 2
    x = 0.5 + col * 6.4
    y = 1.5 + row * 1.9
    add_rounded_box(slide, x, y, 6.0, 1.6, VERY_DARK,
                    border_color=clr, border_width=Pt(2))
    add_text_box(slide, x + 0.15, y + 0.05, 5.7, 0.35, title,
                 font_size=14, bold=True, color=clr)
    add_text_box(slide, x + 0.15, y + 0.45, 5.7, 1.1, desc,
                 font_size=11, color=LIGHT_GRAY)


# ══════════════════════════════════════════════════════════════════════
# SLIDE 16 — TECHNOLOGY STACK SUMMARY
# ══════════════════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)
slide_title(slide, "Technology Stack")

stack_data = [
    ("Language", "Python 3.10+", CLR_CLI),
    ("CLI", "Click 8.x", CLR_CLI),
    ("Container Runtime", "containerd 1.7.11", CLR_INFRA),
    ("Orchestration", "Kubernetes v1.28.6", CLR_ORCH),
    ("Chaos Engine", "LitmusChaos + ChaosCenter", CLR_CHAOS),
    ("Graph Database", "Neo4j 5-community", CLR_STORAGE),
    ("Monitoring", "Prometheus + kube-state-metrics", CLR_METRICS),
    ("Load Testing", "Locust 2.20+", CLR_CLI),
    ("Visualization", "matplotlib 3.7+", CLR_OUTPUT),
    ("ML Export", "CSV / Parquet (pyarrow)", CLR_AI),
    ("Provisioning", "Kubespray 2.24 / Vagrant + libvirt", CLR_INFRA),
    ("Virtualization", "Proxmox (KVM/QEMU)", CLR_INFRA),
]

for i, (category, tool, clr) in enumerate(stack_data):
    col = i % 3
    row = i // 3
    x = 0.5 + col * 4.2
    y = 1.6 + row * 1.35

    add_rounded_box(slide, x, y, 3.8, 1.1, VERY_DARK,
                    border_color=clr)
    add_text_box(slide, x + 0.15, y + 0.05, 3.5, 0.3, category,
                 font_size=11, color=MID_GRAY)
    add_text_box(slide, x + 0.15, y + 0.4, 3.5, 0.6, tool,
                 font_size=16, bold=True, color=clr)

# Dependencies
add_rounded_box(slide, 0.5, 6.2, 12.1, 1.0, VERY_DARK,
                border_color=ACCENT_BLUE)
add_text_box(slide, 0.7, 6.2, 11.7, 0.3, "Runtime Dependencies",
             font_size=14, bold=True, color=ACCENT_BLUE)
add_text_box(slide, 0.7, 6.55, 11.7, 0.6,
    "kubernetes ≥28.0  •  click ≥8.0  •  pyyaml ≥6.0  •  locust ≥2.20  •  "
    "matplotlib ≥3.7  •  neo4j ≥5.0  •  pyarrow ≥12.0 (optional)  •  "
    "requests  •  urllib3  •  jinja2",
    font_size=12, color=LIGHT_GRAY)


# ══════════════════════════════════════════════════════════════════════
# SLIDE 17 — SUMMARY & FUTURE WORK
# ══════════════════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
set_slide_bg(slide)
slide_title(slide, "Summary & Future Work")

# Key contributions
add_rounded_box(slide, 0.5, 1.5, 5.8, 3.5, VERY_DARK,
                border_color=ACCENT_GREEN)
add_text_box(slide, 0.7, 1.5, 5.4, 0.35, "Key Contributions",
             font_size=18, bold=True, color=ACCENT_GREEN)
add_bullet_frame(slide, 0.7, 1.95, 5.4, 2.9, [
    "• End-to-end chaos testing framework with\n  structured, AI-consumable output",
    "• 6 placement strategies measuring 5 dimensions:\n  recovery, latency, I/O, resources, cascades",
    "• Neo4j graph storage preserving causal\n  relationships for dependency-aware analysis",
    "• Multi-signal telemetry collection (6 probers)\n  with 3-phase tracking and time alignment",
    "• Autonomous AI feedback loop:\n  test → diagnose → fix → re-test → compare",
    "• 13 supported fault types with ground-truth\n  ML anomaly labels for training data",
], font_size=12, color=LIGHT_GRAY)

# Future work
add_rounded_box(slide, 6.8, 1.5, 5.8, 3.5, VERY_DARK,
                border_color=ACCENT_ORANGE)
add_text_box(slide, 7.0, 1.5, 5.4, 0.35, "Future Work",
             font_size=18, bold=True, color=ACCENT_ORANGE)
add_bullet_frame(slide, 7.0, 1.95, 5.4, 2.9, [
    "• Multi-fault injection — concurrent faults\n  for complex failure scenarios",
    "• Larger cluster experiments —\n  scale to 20+ nodes, 100+ services",
    "• ML model training — anomaly detection,\n  latency/recovery/throughput prediction",
    "• Custom placement policies —\n  RL-based optimal scheduling",
    "• Real-world validation —\n  production-like traffic patterns",
    "• Integration with GitOps —\n  automated PR creation for fixes",
], font_size=12, color=LIGHT_GRAY)

# Bottom: core message
add_rounded_box(slide, 0.5, 5.3, 12.1, 1.9, VERY_DARK,
                border_color=ACCENT_BLUE, border_width=Pt(3))
add_text_box(slide, 0.7, 5.35, 11.7, 0.4, "Core Message",
             font_size=20, bold=True, color=ACCENT_BLUE)
add_text_box(slide, 0.7, 5.8, 11.7, 1.2,
    "ChaosProbe bridges the gap between chaos engineering and machine learning by producing "
    "structured, graph-stored telemetry from controlled fault injection experiments. "
    "By systematically varying pod placement strategies and measuring recovery time, "
    "inter-service latency, Redis/disk I/O throughput, resource utilisation, and cascade "
    "propagation, it enables data-driven analysis of microservice resilience — "
    "both by traditional ML models and LLM-based autonomous operators.",
    font_size=15, color=WHITE)


# ══════════════════════════════════════════════════════════════════════
# SAVE
# ══════════════════════════════════════════════════════════════════════
output_path = "/home/yhu02/uva-msc-thesis/ChaosProbe_Presentation.pptx"
prs.save(output_path)
print(f"Presentation saved to: {output_path}")
print(f"Total slides: {len(prs.slides)}")
