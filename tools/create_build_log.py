from datetime import date
from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "FaultLine Build Log.docx"

BLUE = RGBColor(0x2E, 0x74, 0xB5)
DARK_BLUE = RGBColor(0x1F, 0x4D, 0x78)
MUTED = RGBColor(0x66, 0x66, 0x66)
INK = RGBColor(0x00, 0x00, 0x00)
FILL = "E8EEF5"


def set_cell_shading(cell, fill):
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = tc_pr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        tc_pr.append(shd)
    shd.set(qn("w:fill"), fill)


def set_cell_margins(cell, top=80, start=120, bottom=80, end=120):
    tc_pr = cell._tc.get_or_add_tcPr()
    tc_mar = tc_pr.first_child_found_in("w:tcMar")
    if tc_mar is None:
        tc_mar = OxmlElement("w:tcMar")
        tc_pr.append(tc_mar)
    for edge, value in (("top", top), ("start", start), ("bottom", bottom), ("end", end)):
        node = tc_mar.find(qn(f"w:{edge}"))
        if node is None:
            node = OxmlElement(f"w:{edge}")
            tc_mar.append(node)
        node.set(qn("w:w"), str(value))
        node.set(qn("w:type"), "dxa")


def set_table_width(table, widths):
    table.alignment = WD_TABLE_ALIGNMENT.LEFT
    table.autofit = False
    tbl_pr = table._tbl.tblPr
    tbl_w = tbl_pr.find(qn("w:tblW"))
    if tbl_w is None:
        tbl_w = OxmlElement("w:tblW")
        tbl_pr.append(tbl_w)
    tbl_w.set(qn("w:w"), "9360")
    tbl_w.set(qn("w:type"), "dxa")

    tbl_ind = tbl_pr.find(qn("w:tblInd"))
    if tbl_ind is None:
        tbl_ind = OxmlElement("w:tblInd")
        tbl_pr.append(tbl_ind)
    tbl_ind.set(qn("w:w"), "120")
    tbl_ind.set(qn("w:type"), "dxa")

    grid = table._tbl.tblGrid
    if grid is None:
        grid = OxmlElement("w:tblGrid")
        table._tbl.insert(0, grid)
    for child in list(grid):
        grid.remove(child)
    for width in widths:
        col = OxmlElement("w:gridCol")
        col.set(qn("w:w"), str(width))
        grid.append(col)

    for row in table.rows:
        for idx, cell in enumerate(row.cells):
            cell.width = Inches(widths[idx] / 1440)
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            set_cell_margins(cell)
            tc_pr = cell._tc.get_or_add_tcPr()
            tc_w = tc_pr.find(qn("w:tcW"))
            if tc_w is None:
                tc_w = OxmlElement("w:tcW")
                tc_pr.append(tc_w)
            tc_w.set(qn("w:w"), str(widths[idx]))
            tc_w.set(qn("w:type"), "dxa")


def style_run(run, size=11, color=INK, bold=False, italic=False):
    run.font.name = "Calibri"
    run._element.rPr.rFonts.set(qn("w:ascii"), "Calibri")
    run._element.rPr.rFonts.set(qn("w:hAnsi"), "Calibri")
    run.font.size = Pt(size)
    run.font.color.rgb = color
    run.bold = bold
    run.italic = italic


def add_para(doc, text="", style=None, after=6, before=0, line_spacing=1.25):
    p = doc.add_paragraph(style=style)
    p.paragraph_format.space_before = Pt(before)
    p.paragraph_format.space_after = Pt(after)
    p.paragraph_format.line_spacing = line_spacing
    if text:
        run = p.add_run(text)
        style_run(run)
    return p


def add_bullet(doc, text):
    p = doc.add_paragraph(style="List Bullet")
    p.paragraph_format.space_after = Pt(4)
    p.paragraph_format.line_spacing = 1.25
    run = p.add_run(text)
    style_run(run)


def add_table(doc, headers, rows, widths):
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    hdr = table.rows[0].cells
    for idx, text in enumerate(headers):
        set_cell_shading(hdr[idx], FILL)
        p = hdr[idx].paragraphs[0]
        p.paragraph_format.space_after = Pt(0)
        run = p.add_run(text)
        style_run(run, bold=True, color=DARK_BLUE)
    for row in rows:
        cells = table.add_row().cells
        for idx, text in enumerate(row):
            p = cells[idx].paragraphs[0]
            p.paragraph_format.space_after = Pt(0)
            run = p.add_run(text)
            style_run(run)
    set_table_width(table, widths)
    add_para(doc, "", after=6)
    return table


def configure_styles(doc):
    section = doc.sections[0]
    section.page_width = Inches(8.5)
    section.page_height = Inches(11)
    section.top_margin = Inches(1)
    section.right_margin = Inches(1)
    section.bottom_margin = Inches(1)
    section.left_margin = Inches(1)
    section.header_distance = Inches(0.492)
    section.footer_distance = Inches(0.492)

    styles = doc.styles
    normal = styles["Normal"]
    normal.font.name = "Calibri"
    normal._element.rPr.rFonts.set(qn("w:ascii"), "Calibri")
    normal._element.rPr.rFonts.set(qn("w:hAnsi"), "Calibri")
    normal.font.size = Pt(11)
    normal.paragraph_format.space_after = Pt(6)
    normal.paragraph_format.line_spacing = 1.25

    for name, size, color, before, after in (
        ("Heading 1", 16, BLUE, 18, 10),
        ("Heading 2", 13, BLUE, 14, 7),
        ("Heading 3", 12, DARK_BLUE, 10, 5),
    ):
        style = styles[name]
        style.font.name = "Calibri"
        style._element.rPr.rFonts.set(qn("w:ascii"), "Calibri")
        style._element.rPr.rFonts.set(qn("w:hAnsi"), "Calibri")
        style.font.size = Pt(size)
        style.font.color.rgb = color
        style.font.bold = True
        style.paragraph_format.space_before = Pt(before)
        style.paragraph_format.space_after = Pt(after)
        style.paragraph_format.line_spacing = 1.25


def build():
    doc = Document()
    configure_styles(doc)

    title = doc.add_paragraph()
    title.paragraph_format.space_after = Pt(4)
    title.alignment = WD_ALIGN_PARAGRAPH.LEFT
    run = title.add_run("FaultLine Build Log")
    style_run(run, size=24, color=INK, bold=True)

    subtitle = add_para(
        doc,
        "Running engineering record for the layered build of the FaultLine AI incident-response evaluation arena.",
        after=12,
    )
    subtitle.runs[0].font.color.rgb = MUTED

    add_table(
        doc,
        ["Field", "Value"],
        [
            ("Project", "FaultLine"),
            ("Repository", "schonhux/FaultLine-"),
            ("Build Method", "Layered delivery: Layer 0 through Layer 6, each with a hard exit criterion"),
            ("Current Layer", "Layer 0 - Instrumented ShopGrid"),
            ("Preset", "compact_reference_guide"),
        ],
        [1800, 7560],
    )

    doc.add_heading("Project Premise", level=1)
    add_para(
        doc,
        "FaultLine is a reproducible benchmark for measuring how accurately, efficiently, and safely AI agents diagnose and remediate incidents in a real distributed system. ShopGrid is the instrumented fixture that produces honest telemetry; the benchmark, evaluation harness, safety model, and record/replay discipline are the real product.",
    )

    doc.add_heading("Layer Discipline", level=1)
    for item in [
        "Do not start a layer until the previous layer's exit criterion is demonstrably met.",
        "No public performance claim belongs in README or resume material until Layer 4 measures it.",
        "All faults must be deterministic, dormant by default, and activated through the typed fault API.",
        "The agent must only observe ShopGrid through the MCP tool layer once Layer 3 begins.",
        "This build log should be updated at each layer boundary and for major mid-layer decisions.",
    ]:
        add_bullet(doc, item)

    doc.add_heading("Branch Strategy", level=1)
    add_table(
        doc,
        ["Layer", "Branch", "Purpose"],
        [
            ("0", "codex/layer-0-instrumented-shopgrid", "Complete observable ShopGrid fixture and Layer 0 exit proof."),
            ("1", "codex/layer-1-manual-incidents", "Trigger faults manually, prove telemetry separability, write runbooks."),
            ("2", "codex/layer-2-scenario-runner", "Build repeatable scenario lifecycle/control plane."),
            ("3", "codex/layer-3-readonly-agent-mcp", "Build read-only MCP tools, LangGraph agent, ledger, record/replay."),
            ("4", "codex/layer-4-evaluation-harness", "Add scorers, baselines, batch runner, measured comparison reports."),
            ("5", "codex/layer-5-guarded-remediation", "Add policy-gated write tools, approval, recovery verification."),
            ("6", "codex/layer-6-console-docs-demo", "Add console, docs, CI smoke path, demo polish."),
        ],
        [900, 3150, 5310],
    )

    doc.add_heading(f"Entry - {date.today().isoformat()} - Layer 0 Continuation", level=1)
    add_para(
        doc,
        "Starting point: the workspace had the Rust workspace, shared crate, checkout, catalog, docs, Compose skeleton, and the db-pool-exhaustion scenario. Gateway, notifications, trafficgen, service Dockerfiles, ClickHouse wiring, seed schemas, and the root engineering guide were missing or incomplete.",
    )

    doc.add_heading("Built In This Slice", level=2)
    for item in [
        "Added gateway service with public product/checkout routes, request IDs, API-key guard, rate limit, and trace-propagating service calls.",
        "Added notifications service consuming orders.created with a dormant pause_consumer hook and queue.consumer_lag gauge.",
        "Added deterministic trafficgen with seed and RPS controls.",
        "Added service Dockerfiles and .dockerignore for one-command Compose builds.",
        "Rewired OTel Collector to export traces, metrics, and logs into ClickHouse.",
        "Added Postgres product/order seed schema and ClickHouse deployment_events registry seed.",
        "Aligned README, Makefile, and scenario fault_config with the Rust/ClickHouse architecture.",
    ]:
        add_bullet(doc, item)

    doc.add_heading("Verification Status", level=2)
    add_table(
        doc,
        ["Check", "Result"],
        [
            ("docker compose config --quiet", "Passed"),
            ("cargo fmt/check/test", "Blocked: cargo/rustc are not installed on the local PATH."),
            ("docker compose build gateway", "Blocked: Docker daemon is not running; buildx cache write also requires Docker access."),
            ("Layer 0 exit criterion", "Not complete yet. Needs live make up proof and ClickHouse telemetry queries."),
        ],
        [3000, 6360],
    )

    doc.add_heading("Remaining Before Layer 0 Exit", level=2)
    for item in [
        "Run cargo fmt, cargo check, and cargo test once Rust tooling or Docker build is available.",
        "Run make up and verify steady traffic through gateway -> checkout -> catalog -> Postgres and checkout -> Redpanda -> notifications.",
        "Query ClickHouse for a distributed trace spanning the services.",
        "Query ClickHouse for RED metrics plus db.pool, cache, and queue gauges.",
        "Exercise each dormant fault endpoint and reset endpoint without leaving faults enabled.",
    ]:
        add_bullet(doc, item)

    doc.add_section(WD_SECTION.NEW_PAGE)
    doc.add_heading("Layer Exit Log", level=1)
    add_para(
        doc,
        "Layer exits are intentionally blank until their criteria are proven. Future entries should include exact command output summaries, query examples, and any contract changes.",
    )
    add_table(
        doc,
        ["Layer", "Exit Status", "Evidence"],
        [
            ("0", "Open", "Awaiting live Compose run and ClickHouse telemetry verification."),
            ("1", "Not started", ""),
            ("2", "Not started", ""),
            ("3", "Not started", ""),
            ("4", "Not started", ""),
            ("5", "Not started", ""),
            ("6", "Not started", ""),
        ],
        [900, 1800, 6660],
    )

    doc.save(OUTPUT)


if __name__ == "__main__":
    build()
