"""Petites fonctions de mise en page pour python-pptx (16:9)."""
import re

from lxml import etree
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LABEL_POSITION, XL_LEGEND_POSITION
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

NAVY = RGBColor(0x0B, 0x1F, 0x33)
INK = RGBColor(0x1F, 0x29, 0x37)
MUTED = RGBColor(0x6B, 0x72, 0x80)
TEAL = RGBColor(0x0F, 0x76, 0x6E)
TEAL_L = RGBColor(0xE3, 0xF2, 0xF0)
AMBER = RGBColor(0xB4, 0x53, 0x09)
AMBER_L = RGBColor(0xFE, 0xF3, 0xC7)
RED = RGBColor(0xB9, 0x1C, 0x1C)
RED_L = RGBColor(0xFD, 0xE8, 0xE8)
GREEN = RGBColor(0x16, 0x65, 0x34)
GREEN_L = RGBColor(0xDC, 0xF2, 0xE3)
GRAY_L = RGBColor(0xF3, 0xF4, 0xF6)
LINE = RGBColor(0xD1, 0xD5, 0xDB)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
FONT = "Calibri"
MONO = "Consolas"

SW, SH = 13.333, 7.5
FOOTER_TXT = "Pumba · IA locale et harness · réflexion du 12 novembre 2026"


def new_prs():
    prs = Presentation()
    prs.slide_width = Inches(SW)
    prs.slide_height = Inches(SH)
    return prs


def blank(prs):
    return prs.slides.add_slide(prs.slide_layouts[6])


# --------------------------------------------------------------- texte
_TOKEN = re.compile(r"(\*\*.+?\*\*|`.+?`)")


def _runs(par, text, size, color, bold, italic=False):
    for part in _TOKEN.split(text):
        if not part:
            continue
        r = par.add_run()
        f = r.font
        f.size = Pt(size)
        f.italic = italic
        if part.startswith("**"):
            inner = part[2:-2]
            if "`" in inner:
                # gras contenant du code : on découpe et on garde le gras
                par._p.remove(r._r)
                for sub in re.split(r"(`.+?`)", inner):
                    if not sub:
                        continue
                    rr = par.add_run()
                    rr.font.size = Pt(size)
                    rr.font.bold = True
                    if sub.startswith("`"):
                        rr.text = sub[1:-1]
                        rr.font.name = MONO
                        rr.font.size = Pt(size * 0.92)
                        rr.font.color.rgb = TEAL if color in (INK, MUTED) else color
                    else:
                        rr.text = sub
                        rr.font.name = FONT
                        rr.font.color.rgb = color
                continue
            r.text = inner
            f.bold = True
            f.name = FONT
            f.color.rgb = color
        elif part.startswith("`"):
            r.text = part[1:-1]
            f.bold = bold
            f.name = MONO
            f.size = Pt(size * 0.92)
            f.color.rgb = TEAL if color in (INK, MUTED) else color
        else:
            r.text = part
            f.bold = bold
            f.name = FONT
            f.color.rgb = color


def _bullet(par, char, indent_in, level_in=0.0):
    pPr = par._p.get_or_add_pPr()
    pPr.set("marL", str(int(Inches(indent_in + level_in))))
    pPr.set("indent", str(-int(Inches(indent_in))))
    for tag in ("a:buNone", "a:buChar", "a:buFont", "a:buAutoNum"):
        for el in pPr.findall(qn(tag)):
            pPr.remove(el)
    if char:
        bf = etree.SubElement(pPr, qn("a:buFont"))
        bf.set("typeface", "Arial")
        bc = etree.SubElement(pPr, qn("a:buChar"))
        bc.set("char", char)
    else:
        etree.SubElement(pPr, qn("a:buNone"))


def text(slide, x, y, w, h, paras, size=14, color=INK, bold=False, align=PP_ALIGN.LEFT,
         anchor=MSO_ANCHOR.TOP, margin=0.05, spacing=1.0, after=4, shape=None):
    """paras : str | list[str | dict(t, b=bullet, size, color, bold, after, italic, lvl)]."""
    if shape is None:
        shape = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = shape.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = Inches(margin)
    tf.margin_top = tf.margin_bottom = Inches(margin * 0.6)
    tf.vertical_anchor = anchor
    if isinstance(paras, str):
        paras = [paras]
    first = True
    for p in paras:
        d = {"t": p} if isinstance(p, str) else p
        par = tf.paragraphs[0] if first else tf.add_paragraph()
        first = False
        par.alignment = d.get("align", align)
        par.line_spacing = d.get("spacing", spacing)
        par.space_after = Pt(d.get("after", after))
        sz = d.get("size", size)
        _runs(par, d["t"], sz, d.get("color", color), d.get("bold", bold), d.get("italic", False))
        if d.get("b"):
            _bullet(par, d["b"] if isinstance(d["b"], str) else "•", 0.2, 0.22 * d.get("lvl", 0))
    return shape


def box(slide, x, y, w, h, fill=GRAY_L, line=None, radius=0.06, line_w=1.0, shape_type=None):
    st = shape_type or (MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE)
    s = slide.shapes.add_shape(st, Inches(x), Inches(y), Inches(w), Inches(h))
    if radius and st == MSO_SHAPE.ROUNDED_RECTANGLE:
        s.adjustments[0] = radius
    if fill is None:
        s.fill.background()
    else:
        s.fill.solid()
        s.fill.fore_color.rgb = fill
    if line is None:
        s.line.fill.background()
    else:
        s.line.color.rgb = line
        s.line.width = Pt(line_w)
    s.shadow.inherit = False
    return s


def card(slide, x, y, w, h, title, body, fill=GRAY_L, accent=TEAL, title_size=16, size=13.5,
         title_color=None, line=None):
    box(slide, x, y, w, h, fill=fill, line=line)
    box(slide, x, y + 0.08, 0.07, h - 0.16, fill=accent, radius=0)
    text(slide, x + 0.2, y + 0.1, w - 0.35, 0.45, title, size=title_size, bold=True,
         color=title_color or accent)
    if body:
        text(slide, x + 0.2, y + 0.55, w - 0.35, h - 0.62, body, size=size, after=5)


def pill(slide, x, y, w, h, label, fill=TEAL, color=WHITE, size=12, bold=True):
    s = box(slide, x, y, w, h, fill=fill, radius=0.5)
    text(slide, 0, 0, 0, 0, label, size=size, color=color, bold=bold, align=PP_ALIGN.CENTER,
         anchor=MSO_ANCHOR.MIDDLE, margin=0.04, shape=s, after=0)
    return s


def arrow(slide, x1, y1, x2, y2, color=MUTED, w=1.5):
    c = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1), Inches(x2),
                                   Inches(y2))
    c.line.color.rgb = color
    c.line.width = Pt(w)
    ln = c.line._get_or_add_ln()
    tail = etree.SubElement(ln, qn("a:tailEnd"))
    tail.set("type", "triangle")
    tail.set("w", "med")
    tail.set("len", "med")
    return c


def hline(slide, x, y, w, color=LINE, weight=1.0):
    c = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x), Inches(y), Inches(x + w),
                                   Inches(y))
    c.line.color.rgb = color
    c.line.width = Pt(weight)
    return c


# ------------------------------------------------------------- gabarit
def frame(prs, kicker, title, num, notes=None):
    s = blank(prs)
    text(s, 0.6, 0.38, 9, 0.3, kicker.upper(), size=11.5, bold=True, color=TEAL)
    text(s, 0.6, 0.66, 12.1, 1.05, title, size=26, bold=True, color=NAVY, spacing=0.92)
    hline(s, 0.6, 7.0, 12.13)
    text(s, 0.6, 7.04, 9, 0.3, FOOTER_TXT, size=10, color=MUTED)
    text(s, 11.7, 7.04, 1.03, 0.3, str(num), size=10, color=MUTED, align=PP_ALIGN.RIGHT)
    if notes:
        s.notes_slide.notes_text_frame.text = notes
    return s


def takeaway(slide, label, y=6.18, h=0.62, fill=NAVY, color=WHITE, size=14.5):
    box(slide, 0.6, y, 12.13, h, fill=fill)
    text(slide, 0.8, y, 11.75, h, label, size=size, color=color, bold=True,
         anchor=MSO_ANCHOR.MIDDLE, after=0)


# ------------------------------------------------------------- tableau
def table(slide, x, y, widths, rows, size=12, header_fill=NAVY, row_h=0.4, header_h=0.4,
          zebra=True, first_col_bold=True, fills=None):
    n_rows, n_cols = len(rows), len(widths)
    gs = slide.shapes.add_table(n_rows, n_cols, Inches(x), Inches(y), Inches(sum(widths)),
                                Inches(header_h + row_h * (n_rows - 1)))
    tbl = gs.table
    # retire le style par défaut (bandes bleues)
    tblPr = tbl._tbl.tblPr
    tblPr.set("firstRow", "0")
    tblPr.set("bandRow", "0")
    for i, wd in enumerate(widths):
        tbl.columns[i].width = Inches(wd)
    for r in range(n_rows):
        tbl.rows[r].height = Inches(header_h if r == 0 else row_h)
        for c in range(n_cols):
            cell = tbl.cell(r, c)
            cell.margin_left = cell.margin_right = Inches(0.08)
            cell.margin_top = cell.margin_bottom = Inches(0.04)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            val = rows[r][c]
            tf = cell.text_frame
            tf.word_wrap = True
            par = tf.paragraphs[0]
            if r == 0:
                cell.fill.solid()
                cell.fill.fore_color.rgb = header_fill
                _runs(par, val, size, WHITE, True)
            else:
                cell.fill.solid()
                f = None
                if fills and (r, c) in fills:
                    f = fills[(r, c)]
                elif zebra and r % 2 == 0:
                    f = GRAY_L
                cell.fill.fore_color.rgb = f or WHITE
                _runs(par, val, size, INK, bold=(c == 0 and first_col_bold))
    return gs


# ------------------------------------------------------------- graphiques
def style_chart(chart, size=12, legend=True):
    chart.font.size = Pt(size)
    chart.font.name = FONT
    chart.font.color.rgb = INK
    chart.has_legend = legend
    if legend:
        chart.legend.position = XL_LEGEND_POSITION.BOTTOM
        chart.legend.include_in_layout = False
        chart.legend.font.size = Pt(size)
    chart.has_title = False


def color_series(series, rgb):
    series.format.fill.solid()
    series.format.fill.fore_color.rgb = rgb
    series.invert_if_negative = False
