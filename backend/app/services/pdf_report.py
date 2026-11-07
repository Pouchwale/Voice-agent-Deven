"""PDF call report (fpdf2 + HarfBuzz shaping so Hindi/Devanagari renders correctly)."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fpdf import FPDF, FontFace
from fpdf.enums import XPos, YPos
from PIL import Image

from ..models import Call
from .reporting import IST, Range, ist

ASSETS = Path(__file__).resolve().parent.parent / "assets"
FONTS = ASSETS / "fonts"

BRAND = (0, 149, 213)
PRIMARY = (0, 119, 179)
NAVY = (11, 59, 92)
FG = (15, 23, 42)
MUTED = (100, 116, 139)
FAINT = (148, 163, 184)
LINE = (226, 232, 240)
SUBTLE = (241, 245, 249)
TINT = (230, 244, 251)
TIER = {"hot": BRAND, "warm": (102, 191, 230), "cold": FAINT, "unknown": LINE}
MAX_CALL_ROWS = 500      # "All calls" table rows in the PDF
MAX_LEAD_ROWS = 200      # hot / warm lead tables
MAX_TRANSCRIPTS = 100    # transcripts appendix


def _mmss(sec: float | None) -> str:
    if sec is None:
        return "–"
    s = int(round(sec))
    return f"{s // 60}:{s % 60:02d}"


class ReportPDF(FPDF):
    def __init__(self, title: str, subtitle: str):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.title_text = title
        self.subtitle = subtitle
        self.set_margins(14, 14, 14)
        self.set_auto_page_break(True, margin=16)
        # Static weights (pre-instanced from the Google variable fonts): loading variable fonts
        # with `variations=` cost ~5 s per report.
        self.add_font("Inter", "", str(FONTS / "Inter-Regular.ttf"))
        self.add_font("Inter", "B", str(FONTS / "Inter-SemiBold.ttf"))
        self.add_font("Hanken", "", str(FONTS / "HankenGrotesk-SemiBold.ttf"))
        self.add_font("Hanken", "B", str(FONTS / "HankenGrotesk-Bold.ttf"))
        self.add_font("Deva", "", str(FONTS / "NotoSansDevanagari-Regular.ttf"))
        self.add_font("Deva", "B", str(FONTS / "NotoSansDevanagari-SemiBold.ttf"))
        self.set_text_shaping(True)
        self.set_fallback_fonts(["Deva"])  # Hindi words inside English/Latin text
        self.set_title(title)
        self.set_creator("Voice Agent")

    # ---------------------------------------------------------- page chrome
    def header(self) -> None:
        if self.page_no() == 1:
            self.set_fill_color(*BRAND)
            self.rect(0, 0, self.w, 30, style="F")
            logo = ASSETS / "pouchwale_logo.webp"
            if logo.exists():
                self.image(Image.open(logo), x=14, y=6, h=18)
            self.set_xy(70, 8)
            self.set_text_color(255, 255, 255)
            self.set_font("Hanken", "B", 18)
            self.cell(self.w - 84, 8, self.title_text, align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            self.set_x(70)
            self.set_font("Inter", "", 9.5)
            self.cell(self.w - 84, 6, self.subtitle, align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            self.set_y(38)
        else:
            self.set_font("Inter", "", 8)
            self.set_text_color(*MUTED)
            self.cell(0, 6, f"{self.title_text}  ·  {self.subtitle}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            self.set_draw_color(*LINE)
            self.line(14, self.get_y(), self.w - 14, self.get_y())
            self.ln(4)
        self.set_text_color(*FG)

    def footer(self) -> None:
        self.set_y(-11)
        self.set_font("Inter", "", 8)
        self.set_text_color(*MUTED)
        self.cell(0, 6, f"Page {self.page_no()}/{{nb}}", align="R")

    # ---------------------------------------------------------- building blocks
    @property
    def content_w(self) -> float:
        return self.w - self.l_margin - self.r_margin

    def ensure_space(self, h: float) -> None:
        if self.get_y() + h > self.h - self.b_margin:
            self.add_page()

    def section(self, title: str, note: str | None = None) -> None:
        self.ensure_space(22)
        self.ln(2)
        self.set_font("Hanken", "B", 12.5)
        self.set_text_color(*FG)
        self.cell(0, 7, title, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        if note:
            self.set_font("Inter", "", 8.5)
            self.set_text_color(*MUTED)
            self.cell(0, 5, note, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_text_color(*FG)
        self.ln(1.5)

    def kpis(self, items: list[tuple[str, str, str]]) -> None:
        """Grid of (label, value, sub) tiles, 4 per row."""
        cols, gap, h = 4, 3, 19
        w = (self.content_w - gap * (cols - 1)) / cols
        for i, (label, value, sub) in enumerate(items):
            if i % cols == 0:
                self.ensure_space(h + gap)
                y = self.get_y()
            x = self.l_margin + (i % cols) * (w + gap)
            self.set_draw_color(*LINE)
            self.set_fill_color(255, 255, 255)
            self.rect(x, y, w, h, style="DF", round_corners=True, corner_radius=2)
            self.set_xy(x + 3, y + 2.5)
            self.set_font("Inter", "", 7.5)
            self.set_text_color(*MUTED)
            self.cell(w - 6, 4, label)
            self.set_xy(x + 3, y + 7)
            self.set_font("Hanken", "B", 14)
            self.set_text_color(*FG)
            self.cell(w - 6, 7, value)
            if sub:
                self.set_xy(x + 3, y + 13.5)
                self.set_font("Inter", "", 7)
                self.set_text_color(*MUTED)
                self.cell(w - 6, 4, sub)
            if i % cols == cols - 1 or i == len(items) - 1:
                self.set_y(y + h + gap)
        self.set_text_color(*FG)

    def hbars(self, rows: list[tuple[str, int, tuple[int, int, int]]], total: int | None = None,
              label_w: float = 52) -> None:
        """Horizontal bars: (label, value, colour)."""
        if not rows:
            return self.empty("No data for this period.")
        top = max([v for _, v, _ in rows] + [1])
        bar_w = self.content_w - label_w - 26
        for label, value, color in rows:
            self.ensure_space(7)
            y = self.get_y()
            self.set_font("Inter", "", 8.5)
            self.set_text_color(*FG)
            self.cell(label_w, 6, label)
            self.set_fill_color(*SUBTLE)
            self.rect(self.l_margin + label_w, y + 1.2, bar_w, 3.6, style="F")
            if value:
                self.set_fill_color(*color)
                self.rect(self.l_margin + label_w, y + 1.2, max(0.8, bar_w * value / top), 3.6, style="F")
            self.set_xy(self.l_margin + label_w + bar_w + 2, y)
            pct = f"  {value * 100 / total:.0f}%" if total else ""
            self.set_font("Inter", "B", 8.5)
            self.cell(24, 6, f"{value:,}{pct}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(1)

    def columns(self, labels: list[str], series: list[tuple[str, list[int], tuple[int, int, int]]],
                height: float = 38, every: int = 1) -> None:
        """Grouped vertical bars with a legend. series = (name, values, colour)."""
        self.ensure_space(height + 16)
        x0, y0 = self.l_margin + 8, self.get_y() + 2
        w = self.content_w - 8
        top = max([v for _, vals, _ in series for v in vals] + [1])
        # gridlines + axis labels
        self.set_font("Inter", "", 6.5)
        self.set_draw_color(*LINE)
        for frac in (0, 0.5, 1):
            gy = y0 + height - height * frac
            self.line(x0, gy, x0 + w, gy)
            self.set_xy(self.l_margin, gy - 2)
            self.set_text_color(*MUTED)
            self.cell(7, 4, f"{round(top * frac):,}", align="R")
        n = max(len(labels), 1)
        slot = w / n
        bw = min(6.0, slot * 0.8 / max(len(series), 1))
        for i, lbl in enumerate(labels):
            gx = x0 + i * slot + (slot - bw * len(series)) / 2
            for j, (_, vals, color) in enumerate(series):
                v = vals[i]
                if v:
                    bh = height * v / top
                    self.set_fill_color(*color)
                    self.rect(gx + j * bw, y0 + height - bh, bw * 0.92, bh, style="F")
            if i % every == 0:
                self.set_xy(x0 + i * slot, y0 + height + 0.8)
                self.set_text_color(*MUTED)
                self.cell(slot * every, 4, lbl, align="L" if every > 1 else "C")
        # legend
        self.set_xy(x0, y0 + height + 6)
        for name, _, color in series:
            self.set_fill_color(*color)
            self.rect(self.get_x(), self.get_y() + 1.3, 2.6, 2.6, style="F")
            self.set_x(self.get_x() + 3.6)
            self.set_text_color(*FG)
            self.cell(self.get_string_width(name) + 6, 5, name)
        self.set_y(y0 + height + 13)
        self.set_text_color(*FG)

    def empty(self, text: str) -> None:
        self.set_font("Inter", "", 9)
        self.set_text_color(*MUTED)
        self.cell(0, 7, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_text_color(*FG)

    def table(self, headings: list[str], rows: list[list[str]], widths: tuple, size: float = 8) -> None:
        if not rows:
            return self.empty("Nothing in this period.")
        self.set_font("Inter", "", size)
        self.set_draw_color(*LINE)
        self.set_fill_color(255, 255, 255)  # tables must not inherit the last chart colour
        self.set_text_color(*FG)
        heading = FontFace(emphasis="BOLD", color=FG, fill_color=SUBTLE)
        with super().table(col_widths=widths, headings_style=heading, line_height=size * 0.55,
                           borders_layout="HORIZONTAL_LINES", text_align="LEFT", padding=1.4,
                           cell_fill_color=(248, 250, 252), cell_fill_mode="ROWS") as t:
            t.row(headings)
            for r in rows:
                t.row([str(x) if x is not None else "" for x in r])
        self.ln(2)


def build_report(calls: list[Call], analytics: dict, rng: Range, *, campaign_name: str | None,
                 include_transcripts: bool, company: str) -> bytes:
    sub = f"{rng.label()}" + (f"  ·  Campaign: {campaign_name}" if campaign_name else "")
    pdf = ReportPDF("Call report", sub)
    pdf.alias_nb_pages()
    pdf.add_page()
    t = analytics["totals"]

    pdf.set_font("Inter", "", 8.5)
    pdf.set_text_color(*MUTED)
    pdf.cell(0, 5, f"{company} · Voice Agent · generated {datetime.now(IST).strftime('%d %b %Y, %I:%M %p')} IST",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(2)

    lat = f"{t['avg_latency_ms'] / 1000:.1f} s" if t["avg_latency_ms"] else "–"
    pdf.section("Overview")
    pdf.kpis([
        ("Dialed", f"{t['dialed']:,}", ""),
        ("Picked up", f"{t['connected']:,}", f"{t['connect_rate'] * 100:.0f}% connect rate"),
        ("Conversations", f"{t['conversations']:,}", f"{t['talk_minutes']:,} talk minutes"),
        ("Avg rating", f"{t['avg_rating']}/10" if t["avg_rating"] is not None else "–", f"{t['qualified']} qualified"),
        ("Hot", f"{t['hot']:,}", "strong interest"),
        ("Warm", f"{t['warm']:,}", "normal interest"),
        ("Cold", f"{t['cold']:,}", "cut / not interested"),
        ("Avg response", lat, f"p90 {t['p90_latency_ms'] / 1000:.1f} s" if t["p90_latency_ms"] else ""),
    ])

    dialed = t["dialed"] or None
    pdf.section("Funnel", "Share of dialed calls at each step")
    pdf.hbars([(f["label"], f["count"], NAVY if f["stage"] == "hot" else BRAND if f["stage"] != "dialed" else FAINT)
               for f in analytics["funnel"]], total=dialed)

    days = analytics["by_day"]
    pdf.section("Calls per day", "Dialed vs picked up (IST)")
    every = max(1, len(days) // 10)
    pdf.columns([d["date"][5:] for d in days],
                [("Dialed", [d["dialed"] for d in days], FAINT), ("Picked up", [d["connected"] for d in days], BRAND)],
                every=every)

    pdf.section("Lead interest", "Calls by interest after AI scoring")
    i = analytics["interest"]
    pdf.hbars([("Hot", i["hot"], TIER["hot"]), ("Warm", i["warm"], TIER["warm"]), ("Cold", i["cold"], TIER["cold"]),
               ("Not scored", i["unknown"], TIER["unknown"])], total=dialed)

    pdf.section("Rating distribution", "1 = cold, 10 = ready to buy")
    pdf.columns([str(r["rating"]) for r in analytics["rating_histogram"]],
                [("Calls", [r["count"] for r in analytics["rating_histogram"]], BRAND)], height=30)

    hours = analytics["by_hour"]
    pdf.section("Best time to call", "Dialed vs picked up by hour of day (IST)")
    pdf.columns([f"{h['hour']:02d}" for h in hours],
                [("Dialed", [h["dialed"] for h in hours], FAINT), ("Picked up", [h["connected"] for h in hours], BRAND)],
                height=30, every=2)

    pdf.section("Why calls ended")
    pdf.hbars([(r["reason"], r["count"], FAINT) for r in analytics["end_reasons"]], total=dialed)
    if analytics["objections"]:
        pdf.section("Top objections")
        pdf.hbars([(o["label"], o["count"], PRIMARY) for o in analytics["objections"][:8]])
    if analytics["campaigns"]:
        pdf.section("Campaigns")
        pdf.table(["Campaign", "Dialed", "Picked up", "Hot", "Warm", "Cold", "Avg rating"],
                  [[c["name"], c["dialed"], c["connected"], c["hot"], c["warm"], c["cold"],
                    c["avg_rating"] if c["avg_rating"] is not None else "–"] for c in analytics["campaigns"]],
                  widths=(60, 18, 20, 14, 14, 14, 22))

    def lead_rows(tier: str) -> list[list[str]]:
        best: dict[int, Call] = {}
        for c in calls:  # latest call per lead in this tier
            if c.interest == tier and c.lead_id:
                best[c.lead_id] = c
        rows = sorted(best.values(), key=lambda c: -(c.rating or 0))[:MAX_LEAD_ROWS]
        return [[(c.lead.name if c.lead else "") or "—", c.lead.phone if c.lead else (c.to_number or ""),
                 (c.lead.company if c.lead else "") or "", f"{c.rating}/10" if c.rating else "–",
                 (c.next_action or c.summary or "")[:160], ist(c.created_at).strftime("%d %b")] for c in rows]

    def lead_note(tier: str, label: str) -> str:
        n = len({c.lead_id for c in calls if c.interest == tier and c.lead_id})
        return label if n <= MAX_LEAD_ROWS else f"{label} (top {MAX_LEAD_ROWS} of {n:,} by rating; leads CSV has all)"

    pdf.add_page()
    pdf.section("Hot leads", lead_note("hot", "Strong interest: call these first"))
    pdf.table(["Name", "Phone", "Company", "Rating", "Next action", "Called"], lead_rows("hot"),
              widths=(30, 26, 28, 13, 70, 15))
    pdf.section("Warm leads", lead_note("warm", "Normal interest: follow up"))
    pdf.table(["Name", "Phone", "Company", "Rating", "Next action", "Called"], lead_rows("warm"),
              widths=(30, 26, 28, 13, 70, 15))

    # Caps keep a year-long report to seconds, not minutes; the calls CSV always has every row.
    shown = calls[-MAX_CALL_ROWS:]
    note = (f"{len(calls):,} calls" if len(calls) <= MAX_CALL_ROWS
            else f"latest {MAX_CALL_ROWS:,} of {len(calls):,} calls — download the calls CSV for all rows")
    pdf.section("All calls", note)
    pdf.table(["When (IST)", "Lead", "Phone", "Interest", "Rating", "Length", "Summary"],
              [[ist(c.created_at).strftime("%d %b %H:%M"), (c.lead.name if c.lead else "") or "—",
                c.lead.phone if c.lead else (c.to_number or ""), c.interest.title() if c.interest != "unknown" else "–",
                f"{c.rating}/10" if c.rating else "–", _mmss(c.duration_sec), (c.summary or "")[:220]]
               for c in shown],
              widths=(20, 26, 25, 15, 13, 12, 71), size=7.5)

    if include_transcripts:
        talked = [c for c in calls if any(tt.role == "user" for tt in c.turns)]
        if len(talked) > MAX_TRANSCRIPTS:  # hottest conversations first
            talked = sorted(talked, key=lambda c: -(c.rating or 0))[:MAX_TRANSCRIPTS]
        if talked:
            pdf.add_page()
            pdf.section("Transcripts", f"{len(talked)} conversations"
                        + (" (top rated; the calls CSV has every transcript)" if len(talked) == MAX_TRANSCRIPTS else ""))
            for c in talked:
                pdf.ensure_space(20)
                pdf.set_font("Inter", "B", 9)
                who = (c.lead.name if c.lead else "") or (c.lead.phone if c.lead else c.to_number) or "Test call"
                pdf.cell(0, 6, f"{who} · {ist(c.created_at).strftime('%d %b %Y %H:%M')} · "
                               f"{c.interest.title()} {f'{c.rating}/10' if c.rating else ''}",
                         new_x=XPos.LMARGIN, new_y=YPos.NEXT)
                for tt in c.turns:
                    if tt.role not in ("agent", "user") or not tt.text.strip():
                        continue
                    pdf.set_font("Inter", "B", 8)
                    pdf.set_text_color(*(PRIMARY if tt.role == "agent" else NAVY))
                    pdf.cell(18, 5, "Agent" if tt.role == "agent" else "Customer")
                    pdf.set_font("Inter", "", 8)
                    pdf.set_text_color(*FG)
                    pdf.multi_cell(0, 5, tt.text + ("  (interrupted)" if tt.interrupted else ""),
                                   new_x=XPos.LMARGIN, new_y=YPos.NEXT)
                pdf.ln(2)
                pdf.set_draw_color(*LINE)
                pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
                pdf.ln(2)

    return bytes(pdf.output())
