"""Diaporama d’équipe — apprendre le vocabulaire en parcourant le dépôt.

Chaque mot est défini au moment où il sert, puis relié à un fichier,
à une décision, et aux plafonds qui le bornent.

Design : briefing (grille, une couleur d’accent, titres en insight).
"""

from __future__ import annotations

from pathlib import Path

from lxml import etree
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

# --- identité visuelle -------------------------------------------------------
INK = RGBColor(0x0E, 0x15, 0x25)
MUTED = RGBColor(0x5A, 0x65, 0x73)
RULE = RGBColor(0xE4, 0xE8, 0xEE)
PAPER = RGBColor(0xFF, 0xFF, 0xFF)
WASH = RGBColor(0xF4, 0xF6, 0xF9)
ACCENT = RGBColor(0x1F, 0x4E, 0x8C)
ACCENT_SOFT = RGBColor(0xE7, 0xEE, 0xF6)
DARK = RGBColor(0x0B, 0x12, 0x20)
DARK_2 = RGBColor(0x14, 0x1C, 0x2E)
GOLD = RGBColor(0xC4, 0xA3, 0x5A)
GOOD = RGBColor(0x1F, 0x6F, 0x5B)
BAD = RGBColor(0x8F, 0x2D, 0x2D)
CODE_BG = RGBColor(0x12, 0x1A, 0x28)
CODE_FG = RGBColor(0xD7, 0xE0, 0xEA)
CODE_DIM = RGBColor(0x7E, 0x8B, 0x9A)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)

FONT = "Segoe UI"
MONO = "Consolas"

W = Inches(13.333)
H = Inches(7.5)
MX = Inches(0.78)
CONTENT_W = Inches(11.77)
FOOTER_Y = Inches(7.12)

prs = Presentation()
prs.slide_width = W
prs.slide_height = H
BLANK = prs.slide_layouts[6]
prs.core_properties.title = "Vocabulaire, dépôt, décisions, limites"
prs.core_properties.subject = "Session équipe — principes, configuration, plafonds"
prs.core_properties.author = "Ingénierie"


def _typeface(run, name: str) -> None:
    run.font.name = name
    rPr = run._r.get_or_add_rPr()
    for tag in ("a:latin", "a:ea", "a:cs"):
        el = rPr.find(qn(tag))
        if el is None:
            el = etree.SubElement(rPr, qn(tag))
        el.set("typeface", name)


def run(p, text, *, size=16, bold=False, color=INK, font=FONT, italic=False):
    r = p.add_run()
    r.text = text
    r.font.size = Pt(size)
    r.font.bold = bold
    r.font.italic = italic
    r.font.color.rgb = color
    r.font.superscript = False
    r.font.subscript = False
    _typeface(r, font)
    return r


def tb(slide, l, t, w, h, anchor="t"):
    sh = slide.shapes.add_textbox(l, t, w, h)
    tf = sh.text_frame
    tf.word_wrap = True
    tf.auto_size = None
    try:
        tf._txBody.bodyPr.set("anchor", {"t": "t", "ctr": "ctr", "b": "b"}[anchor])
    except Exception:
        pass
    return tf


def txt(slide, l, t, w, h, text, *, size=16, bold=False, color=INK, font=FONT, align=PP_ALIGN.LEFT, anchor="t", italic=False):
    tf = tb(slide, l, t, w, h, anchor=anchor)
    p = tf.paragraphs[0]
    p.alignment = align
    p.space_after = Pt(0)
    run(p, text, size=size, bold=bold, color=color, font=font, italic=italic)
    return tf


def para(tf, text, *, size=16, bold=False, color=INK, font=FONT, align=PP_ALIGN.LEFT, space_before=0, space_after=0, italic=False):
    p = tf.add_paragraph()
    p.alignment = align
    p.space_before = Pt(space_before)
    p.space_after = Pt(space_after)
    run(p, text, size=size, bold=bold, color=color, font=font, italic=italic)
    return p


def shape(slide, l, t, w, h, fill, *, line=None, rounded=False, radius=0.04):
    kind = MSO_SHAPE.ROUNDED_RECTANGLE if rounded else MSO_SHAPE.RECTANGLE
    sh = slide.shapes.add_shape(kind, l, t, w, h)
    sh.fill.solid()
    sh.fill.fore_color.rgb = fill
    if line is None:
        sh.line.fill.background()
    else:
        sh.line.color.rgb = line
        sh.line.width = Pt(1.0)
    if rounded:
        try:
            sh.adjustments[0] = radius
        except Exception:
            pass
    sh.shadow.inherit = False
    return sh


def hairline(slide, l, t, w, color=RULE, weight=0.75):
    return shape(slide, l, t, w, Pt(weight), color)


def inset_shape_text(sh, *, pad=0.16, anchor="t"):
    tf = sh.text_frame
    tf.word_wrap = True
    try:
        tf._txBody.bodyPr.set("anchor", anchor)
    except Exception:
        pass
    emu = str(int(Inches(pad)))
    for k in ("lIns", "rIns", "tIns", "bIns"):
        tf._txBody.bodyPr.set(k, emu)
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.LEFT
    return tf


def notes(slide, text: str) -> None:
    slide.notes_slide.notes_text_frame.text = text


def paper(slide, color=PAPER):
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = color


def header(slide, kicker: str, title: str) -> None:
    shape(slide, Inches(0), Inches(0), W, Pt(4), ACCENT)
    txt(slide, MX, Inches(0.26), CONTENT_W, Inches(0.26), kicker.upper(), size=11, bold=True, color=ACCENT)
    txt(slide, MX, Inches(0.50), CONTENT_W, Inches(0.72), title, size=26, bold=True, color=INK)
    hairline(slide, MX, Inches(1.28), CONTENT_W)


def footer(slide, n: int, total: int, chapter: str) -> None:
    hairline(slide, MX, FOOTER_Y, CONTENT_W)
    txt(slide, MX, Inches(7.18), Inches(8.6), Inches(0.26), f"{chapter}   ·   assistant de code local", size=10, color=MUTED)
    txt(slide, Inches(10.7), Inches(7.18), Inches(1.85), Inches(0.26), f"{n:02d}  /  {total:02d}", size=10, color=MUTED, align=PP_ALIGN.RIGHT)


def pill(slide, l, t, n: str, fill=ACCENT):
    sh = shape(slide, l, t, Inches(0.38), Inches(0.38), fill, rounded=True, radius=0.5)
    tf = sh.text_frame
    tf.word_wrap = False
    try:
        tf._txBody.bodyPr.set("anchor", "ctr")
    except Exception:
        pass
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    run(p, n, size=12, bold=True, color=WHITE)
    return sh


def bands(slide, rows):
    h, gap = 1.62, 0.14
    for i, (label, body) in enumerate(rows):
        y = Inches(1.42 + i * (h + gap))
        shape(slide, MX, y, CONTENT_W, Inches(h), WASH)
        txt(slide, Inches(1.06), y + Inches(0.16), Inches(11.2), Inches(0.32), label, size=14, bold=True, color=ACCENT)
        txt(slide, Inches(1.06), y + Inches(0.54), Inches(11.2), Inches(0.92), body, size=16, color=INK)


def trio(slide, items, *, top=1.48, height=5.15):
    for i, (title, body) in enumerate(items):
        x = MX + Inches(i * 4.02)
        y = Inches(top)
        shape(slide, x, y, Inches(3.85), Inches(height), WASH)
        txt(slide, x + Inches(0.24), y + Inches(0.24), Inches(3.38), Inches(0.7), title, size=16, bold=True, color=ACCENT)
        txt(slide, x + Inches(0.24), y + Inches(1.05), Inches(3.38), Inches(height - 1.3), body, size=15, color=INK)


def grid(slide, items, *, cols=2, top=1.40, row_h=1.38, card_h=1.26, title_mono=True):
    for i, (title, body) in enumerate(items):
        col, row = i % cols, i // cols
        x = MX + Inches(col * 6.02)
        y = Inches(top + row * row_h)
        shape(slide, x, y, Inches(5.82), Inches(card_h), WASH)
        txt(
            slide, x + Inches(0.24), y + Inches(0.16), Inches(5.35), Inches(0.34),
            title, size=14, bold=True, color=ACCENT, font=MONO if title_mono else FONT,
        )
        txt(slide, x + Inches(0.24), y + Inches(0.54), Inches(5.35), Inches(0.62), body, size=14, color=INK)


def pairs(slide, rows, *, top=1.36, step=0.58, left_w=4.35):
    for i, (left, right) in enumerate(rows):
        y = Inches(top + i * step)
        txt(slide, MX, y, Inches(left_w), Inches(0.46), left, size=14, bold=True, color=ACCENT, font=MONO)
        txt(slide, MX + Inches(left_w + 0.15), y, Inches(11.6 - left_w), Inches(0.46), right, size=14, color=INK)
        if i < len(rows) - 1:
            hairline(slide, MX, y + Inches(0.48), CONTENT_W)


def table(slide, headers, rows, *, top=1.62, row_h=0.82, cols=None, mono_col=2):
    if cols is None:
        cols = (
            (Inches(0.95), Inches(3.15)),
            (Inches(4.25), Inches(4.7)),
            (Inches(9.1), Inches(3.25)),
        )
    for (x, w), label in zip(cols, headers):
        txt(slide, x, Inches(top - 0.30), w, Inches(0.26), label, size=11, bold=True, color=MUTED)
    for i, triple in enumerate(rows):
        y = Inches(top + i * row_h)
        if i % 2 == 0:
            shape(slide, MX, y, CONTENT_W, Inches(row_h - 0.06), WASH)
        for col, ((x, w), cell) in enumerate(zip(cols, triple)):
            txt(
                slide, x, y + Inches(0.16), w, Inches(row_h - 0.28), cell,
                size=13 if col else 14,
                bold=(col == 0),
                color=ACCENT if col == mono_col else INK,
                font=MONO if col == mono_col else FONT,
            )


def s_section(num: str, title: str, line: str, chapter: str):
    s = prs.slides.add_slide(BLANK)
    paper(s, DARK)
    shape(s, Inches(0), Inches(0), W, Pt(4), GOLD)
    txt(s, MX, Inches(2.15), CONTENT_W, Inches(0.5), num, size=18, bold=True, color=GOLD)
    txt(s, MX, Inches(2.7), CONTENT_W, Inches(1.1), title, size=36, bold=True, color=WHITE)
    shape(s, MX, Inches(4.0), Inches(1.2), Pt(3), GOLD)
    txt(s, MX, Inches(4.3), CONTENT_W, Inches(0.9), line, size=18, color=RGBColor(0xB7, 0xC2, 0xD0))
    notes(s, f"Pause d’une seconde. Enchaîner {chapter}. Faire répéter le titre du chapitre.")
    return s, None


def s_title():
    s = prs.slides.add_slide(BLANK)
    paper(s, DARK)
    shape(s, Inches(0), Inches(0), W, Pt(4), GOLD)
    txt(s, MX, Inches(1.45), CONTENT_W, Inches(0.32), "SESSION INTERNE   ·   ÉQUIPE INGÉNIERIE", size=12, bold=True, color=GOLD)
    tf = tb(s, MX, Inches(1.95), CONTENT_W, Inches(1.7))
    p = tf.paragraphs[0]
    run(p, "Apprendre les mots,", size=40, bold=True, color=WHITE)
    para(tf, "puis le dépôt.", size=40, bold=True, color=WHITE, space_before=4)
    shape(s, MX, Inches(4.05), Inches(1.35), Pt(3), GOLD)
    txt(
        s, MX, Inches(4.35), Inches(11.2), Inches(1.35),
        "Principes, vocabulaire, chaque pièce, les décisions.\nEt la configuration : les plafonds qu’on règle, les refus qu’on ne règle pas.",
        size=18, color=RGBColor(0xB7, 0xC2, 0xD0),
    )
    txt(s, MX, Inches(6.85), CONTENT_W, Inches(0.28), "local_llm    ·    Python, C, CMake", size=12, bold=True, color=RGBColor(0x6B, 0x7A, 0x8C))
    notes(
        s,
        "Ouverture. Dire : on n’évite pas le vocabulaire, on l’apprend. Chaque mot sera défini, "
        "montré dans un fichier, et rattaché à une décision. Annoncer que le chapitre configuration "
        "et limites ne se saute pas : c’est le mode d’emploi. Environ 40 minutes. Les notes portent le script.",
    )
    return s, None


def s_agenda():
    s = prs.slides.add_slide(BLANK)
    paper(s)
    header(s, "Parcours", "Six temps. Les mots d’abord, les plafonds avant la pratique.")
    items = [
        ("01", "Les principes", "LLM, token, contexte. Deux règles qui fixent le dépôt."),
        ("02", "Le vocabulaire", "RAG, embedding, tool calling, dans l’ordre d’une question."),
        ("03", "Le dépôt", "Services, réseaux, modèles, outils, dossiers."),
        ("04", "Les décisions", "Pourquoi ce choix, et dans quel fichier c’est écrit."),
        ("05", "Config et limites", "Ce qui se règle dans .env, et ce qui reste un refus."),
        ("06", "La pratique", "Lancer, modifier un plafond, première journée."),
    ]
    for i, (num, title, body) in enumerate(items):
        y = Inches(1.40 + i * 0.90)
        txt(s, MX, y, Inches(0.9), Inches(0.36), num, size=18, bold=True, color=ACCENT)
        txt(s, Inches(1.85), y, Inches(10.4), Inches(0.32), title, size=18, bold=True, color=INK)
        txt(s, Inches(1.85), y + Inches(0.34), Inches(10.4), Inches(0.36), body, size=14, color=MUTED)
    notes(s, "Si le temps serre, raccourcir le chapitre 03, pas le 05. Les plafonds sont le mode d’emploi.")
    return s, "Parcours"


def s_product():
    s = prs.slides.add_slide(BLANK)
    paper(s)
    header(s, "Le produit", "On lui colle des extraits. Il peut demander une action nommée.")
    txt(
        s, MX, Inches(1.42), CONTENT_W, Inches(0.7),
        "Le code reste sur la machine. On ne lui envoie pas l’archive.\nLes noms de ces pièces arrivent au chapitre suivant. Ici, seulement ce que le programme fait.",
        size=16, color=INK,
    )

    def col(x, fill, label, label_color, lines, line_color, bar):
        shape(s, x, Inches(2.30), Inches(5.7), Inches(4.35), fill)
        shape(s, x, Inches(2.30), Inches(5.7), Pt(4), bar)
        txt(s, x + Inches(0.28), Inches(2.52), Inches(5.15), Inches(0.36), label, size=14, bold=True, color=label_color)
        tf = tb(s, x + Inches(0.28), Inches(3.05), Inches(5.15), Inches(3.3))
        for i, line in enumerate(lines):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            p.space_after = Pt(10)
            run(p, line, size=16, color=line_color)

    col(MX, WASH, "Il peut", GOOD, [
        "Lire un extrait borné",
        "Compiler un fichier",
        "Lancer ctest",
        "Interroger clangd",
        "Lire git ou svn",
    ], INK, GOOD)
    col(Inches(6.85), DARK_2, "Il ne peut pas", BAD, [
        "Ouvrir un terminal",
        "Écrire dans l’arbre",
        "Sortir sur Internet",
        "Committer",
        "Avaler tout le dépôt",
    ], WHITE, BAD)
    notes(s, "Phrase d’architecture. Revenir ici si une question part dans le jargon avant qu’on ait posé le mot.")
    return s, "01  Principes"


def s_rules():
    s = prs.slides.add_slide(BLANK)
    paper(s)
    header(s, "Deux contraintes", "Elles expliquent presque chaque fichier du dépôt.")
    shape(s, MX, Inches(1.48), Inches(5.7), Inches(5.15), WASH)
    txt(s, Inches(1.08), Inches(1.72), Inches(0.7), Inches(0.4), "01", size=18, bold=True, color=ACCENT)
    txt(s, Inches(1.8), Inches(1.72), Inches(4.3), Inches(0.4), "Hors ligne", size=20, bold=True, color=INK)
    txt(
        s, Inches(1.08), Inches(2.4), Inches(5.1), Inches(3.8),
        "Aucun envoi du code chez un fournisseur.\nTout tourne en conteneurs, sur la machine.\n\nLe seul accès Internet est le profil setup : télécharger les poids une fois. Ensuite, plus rien ne sort.",
        size=16, color=INK,
    )
    shape(s, Inches(6.85), Inches(1.48), Inches(5.7), Inches(5.15), WASH)
    txt(s, Inches(7.15), Inches(1.72), Inches(0.7), Inches(0.4), "02", size=18, bold=True, color=ACCENT)
    txt(s, Inches(7.85), Inches(1.72), Inches(4.4), Inches(0.4), "Pas de shell", size=20, bold=True, color=INK)
    txt(
        s, Inches(7.15), Inches(2.4), Inches(5.1), Inches(3.8),
        "Quand le modèle propose « compile ce fichier », on traite la proposition comme une requête non fiable.\n\nNom dans une liste. Arguments validés. Chemins recadrés. Liste d’arguments, jamais system().",
        size=16, color=INK,
    )
    notes(s, "Ces deux phrases justifient les trois réseaux, tools/, et le montage :ro. Le chapitre limites y revient avec les chiffres.")
    return s, "01  Principes"


def s_llm():
    s = prs.slides.add_slide(BLANK)
    paper(s)
    header(s, "LLM  ·  Large Language Model", "Il continue un texte. Il ne compile pas.")
    shape(s, MX, Inches(1.46), CONTENT_W, Inches(1.15), WASH)
    txt(
        s, Inches(1.06), Inches(1.64), Inches(11.2), Inches(0.8),
        "Inférence : utiliser des poids déjà entraînés. Ici, le service s’appelle llm.\nIl émet du texte, parfois du JSON. Il n’ouvre aucun fichier.",
        size=16, color=INK,
    )
    shape(s, MX, Inches(2.78), Inches(5.7), Inches(2.15), WASH)
    txt(s, Inches(1.06), Inches(2.94), Inches(5.15), Inches(0.32), "Compilateur", size=15, bold=True, color=GOOD)
    txt(s, Inches(1.06), Inches(3.36), Inches(5.15), Inches(1.35), "Même source, même binaire. Des types.\nIl lit le fichier qu’on lui donne.", size=15, color=INK)
    shape(s, Inches(6.85), Inches(2.78), Inches(5.7), Inches(2.15), ACCENT_SOFT)
    txt(s, Inches(7.13), Inches(2.94), Inches(5.15), Inches(0.32), "LLM", size=15, bold=True, color=ACCENT)
    txt(s, Inches(7.13), Inches(3.36), Inches(5.15), Inches(1.35), "Même question, un autre texte. Pas de types.\nIl peut affirmer faux, d’un ton sûr.", size=15, color=INK)
    sh = shape(s, MX, Inches(5.12), CONTENT_W, Inches(1.5), CODE_BG)
    tf = inset_shape_text(sh, pad=0.26)
    p = tf.paragraphs[0]
    run(p, "system(texte_du_llm);                      //  non", size=16, color=CODE_DIM, font=MONO)
    p2 = para(tf, "tool_call(nom_autorise, args_valides);   //  oui", size=16, color=CODE_FG, font=MONO, space_before=8)
    p2.alignment = PP_ALIGN.LEFT
    notes(
        s,
        "Faire écrire LLM au tableau, et le mot inférence. Dans le code : service llm, "
        "fichier de poids monté en lecture seule. Tool calling est le chapitre suivant : ici, seulement le contraste.",
    )
    return s, "01  Principes"


def s_context():
    s = prs.slides.add_slide(BLANK)
    paper(s)
    header(s, "Token  ·  contexte", "La fenêtre est un budget. Pas le dépôt.")
    trio(s, [
        ("Token", "Un sous-mot. C’est l’unité de LLM_CTX_SIZE. On ne compte ni les fichiers, ni les lignes."),
        ("Contexte", "La fenêtre : 32 768 tokens. Quelques dizaines de fichiers. Pas un arbre de dix mille."),
        ("Conséquence", "On sélectionne des extraits. Les journaux d’outils sont plafonnés. Les chiffres sont au chapitre 05."),
    ])
    notes(s, "Ne pas s’attarder sur le découpage BPE. Token = unité de budget. Annoncer que 32 768 n’est pas le budget utile.")
    return s, "01  Principes"


def s_weights():
    s = prs.slides.add_slide(BLANK)
    paper(s)
    header(s, "GGUF  ·  quantization", "Des poids chargés. Pas un réentraînement.")
    trio(s, [
        ("GGUF", "Format de fichier de llama.cpp. Monté en lecture seule dans llm, embedder et reranker."),
        ("Quantization", "Moins de bits par poids. Le chat est en Q5_K_M, environ 9,8 Go. L’embedding est en Q8."),
        ("Décision", "Sous Q5, le JSON des tool calls casse plus souvent. Le cache KV en q8_0 fait tenir 32 768 tokens sur 16 Go."),
    ])
    notes(
        s,
        "Quantization = compression des poids, avec une perte. Q5_K_M est épinglé dans CONTRACTS.md pour la fiabilité "
        "du tool calling, pas pour la taille seule. Le 7B entraîné ne se charge pas sur ce 14B : on y revient.",
    )
    return s, "02  Vocabulaire"


def s_rag():
    s = prs.slides.add_slide(BLANK)
    paper(s)
    header(s, "RAG  ·  Retrieval-Augmented Generation", "Chercher des extraits, puis laisser rédiger.")
    shape(s, MX, Inches(1.48), Inches(5.7), Inches(5.15), WASH)
    txt(s, Inches(1.08), Inches(1.72), Inches(5.15), Inches(0.4), "Fine-tuning", size=18, bold=True, color=INK)
    txt(
        s, Inches(1.08), Inches(2.35), Inches(5.15), Inches(3.9),
        "Réentraîner les poids sur le dépôt.\n\nUn commit de plus, et le modèle est périmé.\n\nQLoRA existe dans le dépôt, sur une base 7B. Ce n’est pas le chemin de la réponse.",
        size=16, color=INK,
    )
    shape(s, Inches(6.85), Inches(1.48), Inches(5.7), Inches(5.15), DARK)
    txt(s, Inches(7.13), Inches(1.72), Inches(5.15), Inches(0.4), "RAG", size=18, bold=True, color=GOLD)
    txt(
        s, Inches(7.13), Inches(2.35), Inches(5.15), Inches(3.9),
        "Retrieval : aller chercher.\nAugmented : coller les chunks dans le contexte.\nGeneration : le LLM rédige.\n\nMettre à jour, c’est réindexer.\nLe fichier est agent/rag.py.",
        size=16, color=WHITE,
    )
    notes(s, "Faire épeler RAG. Retrieval, Augmented, Generation. Montrer que le fine-tuning est l’autre solution, présente mais hors du chemin.")
    return s, "02  Vocabulaire"


def s_chunk():
    s = prs.slides.add_slide(BLANK)
    paper(s)
    header(s, "Chunk  ·  tree-sitter", "Un chunk est une fonction, pas 50 lignes.")
    steps = [
        ("1", "Parcourir", "C++, en-têtes, CMake, Markdown, YAML, JSON, TOML."),
        ("2", "Découper", "tree-sitter : une fonction, une classe. Un chunk = une idée."),
        ("3", "Ranger", "Chemin, lignes, symbole, hash. Payload à côté du vecteur."),
        ("4", "Retrouver", "À la question, cinq chunks entrent dans la fenêtre."),
    ]
    for i, (n, title, body) in enumerate(steps):
        x = MX + Inches(i * 3.02)
        shape(s, x, Inches(1.48), Inches(2.88), Inches(3.15), WASH)
        pill(s, x + Inches(0.18), Inches(1.68), n)
        txt(s, x + Inches(0.66), Inches(1.7), Inches(2.05), Inches(0.36), title, size=15, bold=True, color=INK)
        txt(s, x + Inches(0.18), Inches(2.28), Inches(2.52), Inches(2.05), body, size=14, color=MUTED)
    txt(
        s, MX, Inches(4.85), CONTENT_W, Inches(1.85),
        "Un bloc à cheval sur deux fonctions se retrouve mal : l’embedding ne porte plus une idée.\n\nindexer est un job unique, pas un serveur. Le corpus par défaut est sample/, pas app/.",
        size=16, color=INK,
    )
    notes(s, "Ouvrir indexer/chunker.py si on est devant le dépôt. Tombstones et skip par hash : un fichier inchangé n’est pas ré-embedé.")
    return s, "02  Vocabulaire"


def s_embedding():
    s = prs.slides.add_slide(BLANK)
    paper(s)
    header(s, "Embedding  ·  vecteur", "768 nombres. Proches, pas égaux.")
    shape(s, MX, Inches(1.48), Inches(5.7), Inches(3.35), WASH)
    txt(s, Inches(1.08), Inches(1.68), Inches(5.15), Inches(0.36), "Un hash", size=16, bold=True, color=INK)
    txt(s, Inches(1.08), Inches(2.2), Inches(5.15), Inches(2.3), "Répond : égal, ou pas.\nUtile pour savoir si le chunk a changé.\nInutile pour une paraphrase.", size=16, color=INK)
    shape(s, Inches(6.85), Inches(1.48), Inches(5.7), Inches(3.35), DARK)
    txt(s, Inches(7.13), Inches(1.68), Inches(5.15), Inches(0.36), "Un embedding", size=16, bold=True, color=GOLD)
    txt(s, Inches(7.13), Inches(2.2), Inches(5.15), Inches(2.3), "Liste de 768 floats. Similarité cosinus.\n« flag absent » peut retrouver\nparse_arguments. Service : embedder.", size=16, color=WHITE)
    shape(s, MX, Inches(5.02), CONTENT_W, Inches(1.6), ACCENT_SOFT)
    txt(s, Inches(1.06), Inches(5.18), Inches(11.2), Inches(0.3), "Décision  ·  préfixes nomic", size=13, bold=True, color=ACCENT)
    txt(
        s, Inches(1.06), Inches(5.52), Inches(11.2), Inches(0.9),
        "search_query: devant la question, search_document: devant le chunk.\nLes inverser ne lève pas d’erreur. La recherche devient mauvaise. rag.py fige le préfixe de requête.",
        size=15, color=INK,
    )
    notes(s, "Qdrant stocke le vecteur et le payload. Collection par défaut : code_chunks. Cosinus, pas distance euclidienne à expliquer longtemps.")
    return s, "02  Vocabulaire"


def s_hybrid():
    s = prs.slides.add_slide(BLANK)
    paper(s)
    header(s, "Dense  ·  BM25  ·  reranker", "Sens et mots, puis cinq extraits.")
    trio(s, [
        ("Dense", "Top 20 par cosinus. La paraphrase en français. Le service embedder, puis Qdrant."),
        ("BM25", "Top 20 par les mots exacts. m_bufferSize, CMAKE_BUILD_TYPE. Les identifiants C++ vivent ici."),
        ("RRF, puis reranker", "Reciprocal Rank Fusion mélange les deux classements. bge-reranker-v2-m3 n’en garde que 5."),
    ], height=3.55)
    shape(s, MX, Inches(5.2), CONTENT_W, Inches(1.42), DARK)
    txt(
        s, Inches(1.06), Inches(5.42), Inches(11.2), Inches(1.05),
        "RERANKER_BASE_URL vide : on tronque l’ordre fusionné, sans second modèle.\nUne collection pas encore reconstruite retombe sur le dense seul. Le reranker réordonne. Il ne réécrit pas la question.",
        size=15, color=WHITE,
    )
    notes(s, "RRF = fusion des rangs, pas des scores bruts. Cross-encoder : le reranker lit question et chunk ensemble. CPU par défaut, pour laisser la carte au chat.")
    return s, "02  Vocabulaire"


def s_calling():
    s = prs.slides.add_slide(BLANK)
    paper(s)
    header(s, "Tool calling  ·  function calling", "Un nom dans une liste. Jamais un shell.")
    txt(
        s, MX, Inches(1.40), CONTENT_W, Inches(0.55),
        "Le LLM renvoie un JSON {nom, arguments}. agent valide (pydantic, clés inconnues rejetées). tools exécute un argv.",
        size=15, color=INK,
    )
    groups = [
        ("Construire", "build, test, compile"),
        ("Lire", "fichier, search, liste"),
        ("Qualité", "tidy, format, cppcheck"),
        ("Histoires", "git / svn, lecture"),
        ("Symboles", "clangd"),
    ]
    for i, (title, body) in enumerate(groups):
        x = MX + Inches(i * 2.4)
        shape(s, x, Inches(2.08), Inches(2.25), Inches(1.45), WASH)
        txt(s, x + Inches(0.1), Inches(2.2), Inches(2.05), Inches(0.36), title, size=13, bold=True, color=ACCENT, align=PP_ALIGN.CENTER)
        txt(s, x + Inches(0.1), Inches(2.58), Inches(2.05), Inches(0.75), body, size=13, color=INK, align=PP_ALIGN.CENTER)
    sh = shape(s, MX, Inches(3.72), CONTENT_W, Inches(2.9), CODE_BG)
    tf = inset_shape_text(sh, pad=0.26)
    p = tf.paragraphs[0]
    run(p, "argv = [\"rg\", \"--fixed-strings\", pattern, str(racine)]", size=15, color=CODE_FG, font=MONO)
    p2 = para(tf, "subprocess.run(argv, shell=False, timeout=…)", size=15, color=CODE_FG, font=MONO, space_before=8)
    p2.alignment = PP_ALIGN.LEFT
    p3 = para(tf, "contrat : services/common/tool_models.py    ·    23 noms, zéro /exec", size=14, color=CODE_DIM, font=MONO, space_before=12)
    p3.alignment = PP_ALIGN.LEFT
    notes(s, "JSON pourri : un essai, puis réponse en texte. Boucle dans tool_loop.py, plafond 8. Le chapitre limites donne les caps par outil.")
    return s, "02  Vocabulaire"


def s_flow():
    s = prs.slides.add_slide(BLANK)
    paper(s)
    header(s, "Gate  ·  direct ou code", "Cinq étapes. Les mots de la question.")
    steps = [
        ("1", "Question", "Page, CLI\nou éditeur"),
        ("2", "Gate", "direct : stop\ncode : suite"),
        ("3", "RAG", "20 + 20\npuis top 5"),
        ("4", "Tool loop", "Au plus 8\npuis du texte"),
        ("5", "Réponse", "Texte +\nsources"),
    ]
    gap = Inches(0.18)
    box_w = Inches(2.2)
    for i, (n, title, body) in enumerate(steps):
        x = MX + i * (box_w + gap)
        shape(s, x, Inches(1.46), box_w, Inches(2.35), WASH)
        pill(s, x + Inches(0.16), Inches(1.62), n)
        txt(s, x + Inches(0.6), Inches(1.64), Inches(1.45), Inches(0.34), title, size=14, bold=True, color=INK)
        txt(s, x + Inches(0.16), Inches(2.18), Inches(1.9), Inches(1.4), body, size=14, color=MUTED)
    shape(s, MX, Inches(4.05), Inches(5.7), Inches(2.55), WASH)
    txt(s, Inches(1.06), Inches(4.22), Inches(5.2), Inches(0.32), "direct", size=15, bold=True, color=GOOD)
    txt(s, Inches(1.06), Inches(4.64), Inches(5.2), Inches(1.7), "Salut, calcul, hors dépôt.\nPas d’embedding. Pas de tools.\nUn seul appel au LLM.", size=15, color=INK)
    shape(s, Inches(6.85), Inches(4.05), Inches(5.7), Inches(2.55), ACCENT_SOFT)
    txt(s, Inches(7.13), Inches(4.22), Inches(5.2), Inches(0.32), "code", size=15, bold=True, color=ACCENT)
    txt(s, Inches(7.13), Inches(4.64), Inches(5.2), Inches(1.7), "Fichier, symbole, CMake, test,\nou suite déjà sur le code.\nRAG, tools, puis texte.", size=15, color=INK)
    notes(
        s,
        "gate.py. En doute, ou si le classement échoue : code. Fichiers non commités relus devant les chunks, "
        "pas réécrits dans Qdrant. Repo map si la question porte sur la forme de l’arbre. SSE : route, rag, tool_call, token, done.",
    )
    return s, "02  Vocabulaire"


def s_processes():
    s = prs.slides.add_slide(BLANK)
    paper(s)
    header(s, "Services", "Huit processus. Un seul parle à tous.")
    grid(s, [
        ("agent", "Orchestrateur. Gate, RAG, tool loop, budget de tokens, SSE."),
        ("llm", "Inférence du chat. API OpenAI. Ne voit ni le disque ni tools."),
        ("embedder", "Embeddings nomic, 768 dimensions, préfixes obligatoires."),
        ("reranker", "Cross-encoder bge. Réordonne 20 candidats, en garde 5."),
        ("qdrant", "Base vectorielle. Une collection par URL distante."),
        ("indexer", "Job unique. tree-sitter, embeddings, upsert. Pas un serveur."),
        ("tools", "Les 23 tool calls. Dépôt en lecture seule, argv, cgroups."),
        ("mcp · app · ui", "Prise éditeur, client C++, page. Trois portes, loopback."),
    ])
    notes(s, "ui-proxy est nginx : il publie la page parce qu’agent est sur un réseau internal. app/ n’est pas le corpus. sample/ l’est, par défaut.")
    return s, "03  Dépôt"


def s_networks():
    s = prs.slides.add_slide(BLANK)
    paper(s)
    header(s, "Réseaux", "Trois filets. Un seul pont.")
    nets = [
        ("Entrée", "client-net", "Page, client C++,\nMCP. Ils voient agent.\nPas Qdrant, pas tools."),
        ("Cerveau", "core-net", "llm, embedder,\nreranker, Qdrant.\nPas le bac à sable."),
        ("Bac à sable", "tools-net", "cmake, git, clangd.\nInvisibles depuis\nle LLM."),
    ]
    for i, (title, net, body) in enumerate(nets):
        x = MX + Inches(i * 4.02)
        shape(s, x, Inches(1.46), Inches(3.85), Inches(3.35), WASH)
        txt(s, x + Inches(0.24), Inches(1.64), Inches(3.4), Inches(0.34), title, size=16, bold=True, color=INK)
        txt(s, x + Inches(0.24), Inches(2.04), Inches(3.4), Inches(0.32), net, size=13, color=ACCENT, font=MONO)
        txt(s, x + Inches(0.24), Inches(2.5), Inches(3.4), Inches(2.0), body, size=15, color=MUTED)
    shape(s, MX, Inches(5.02), CONTENT_W, Inches(1.6), DARK)
    txt(
        s, Inches(1.06), Inches(5.24), Inches(11.2), Inches(1.2),
        "agent est le seul processus sur plus d’un réseau. internal: true : pas de route vers l’extérieur.\nUn tool call inventé par le LLM n’a personne à qui parler. tools ne lit pas les embeddings.",
        size=15, color=WHITE,
    )
    notes(s, "La menace principale est le LLM, pas Internet. D’où le bearer malgré le réseau interne. publish-net ne sert qu’au loopback : 8080, 8090, 8092.")
    return s, "03  Dépôt"


def s_models():
    s = prs.slides.add_slide(BLANK)
    paper(s)
    header(s, "Modèles", "Cinq fichiers. Un seul répond.")
    table(
        s,
        ("Rôle", "Fichier", "Pourquoi celui-là"),
        [
            ("Chat", "14B Instruct, Q5_K_M", "Tool calling. LoRA 7B incompatible."),
            ("Embedding", "nomic v1.5, Q8, 768-d", "Préfixes query / document."),
            ("Reranker", "bge-reranker-v2-m3, Q8", "CPU. Il réordonne, il ne rédige pas."),
            ("Smoke", "0.5B, Q4_K_M", "Profil test. Jamais l’utilisateur."),
            ("Train", "7B Instruct, safetensors", "QLoRA. 200 pas, rank 16."),
        ],
        row_h=0.78,
    )
    txt(
        s, MX, Inches(5.7), CONTENT_W, Inches(1.05),
        "LLM_ROUTER=1 : la page charge un autre GGUF de ./models/ sans rebuild. Un seul modèle en mémoire (LLM_MODELS_MAX=1). L’adaptateur QLoRA ne se charge pas sur le 14B qui répond.",
        size=14, color=INK,
    )
    notes(s, "TRAIN_MAX_SEQ_LENGTH=2048, TRAIN_LORA_ALPHA=32. L’entraînement n’est pas une limite de la fenêtre de chat. Le montrer pour que personne ne « mette le LoRA » sur le 14B.")
    return s, "03  Dépôt"


def s_catalog_tools():
    s = prs.slides.add_slide(BLANK)
    paper(s)
    header(s, "Les 23 tools", "Vingt-trois noms. Aucun exec.")
    grid(s, [
        ("build · test · compile_file", "CMake, ctest -R, un translation unit."),
        ("tidy · format · cppcheck", "Lint, dry-run, analyse statique. Rien n’est écrit."),
        ("read_file · search · list_files", "Fenêtre de lignes, ripgrep, liste de chemins."),
        ("git_log · blame · diff · show", "Lecture seule. Références filtrées."),
        ("svn_status · diff · log", "Lecture seule sur la copie montée."),
        ("lsp_*", "definition, references, hover, symbols, outline, calls, types."),
    ], top=1.46, row_h=1.55, card_h=1.42, title_mono=False)
    txt(
        s, MX, Inches(6.2), CONTENT_W, Inches(0.7),
        "Le contrat est unique : tool_models.py. agent en dérive le JSON schema. Un test le compare à l’OpenAPI de tools.",
        size=14, color=INK,
    )
    notes(s, "Compter à voix haute si quelqu’un doute du 23. lsp fait 7 noms. Git 4, svn 3, le reste 9. 7+4+3+9=23.")
    return s, "03  Dépôt"


def s_doors():
    s = prs.slides.add_slide(BLANK)
    paper(s)
    header(s, "MCP  ·  Model Context Protocol", "Deux portes. Une seule rédaction.")
    trio(s, [
        (":8090  la page", "agent entier. RAG, tools, le 14B rédige. Jeton dans l’onglet. SSE au fil de l’eau."),
        (":8092  MCP", "Faits : lookup, check, outline, calls, types. ask juge en 400 tokens, sans tools. L’éditeur rédige."),
        (":8080  le LLM seul", "API OpenAI. Continue parle ici. Pas de RAG, pas de tools. Ne pas enchaîner avec ask."),
    ], height=3.7)
    shape(s, MX, Inches(5.35), CONTENT_W, Inches(1.28), DARK)
    txt(
        s, Inches(1.06), Inches(5.55), Inches(11.2), Inches(0.95),
        "Décision. Une seule rédaction. Depuis Cursor, le modèle de l’éditeur écrit à partir des faits.\nEnchaîner ask derrière, c’est réécrire la même réponse. X-Workspace choisit la copie.",
        size=15, color=WHITE,
    )
    notes(s, "MCP = prise standard, pas un protocole maison. ask ne cherche pas et ne compile pas. Les faits, c’est lookup et clangd.")
    return s, "03  Dépôt"


def s_folders():
    s = prs.slides.add_slide(BLANK)
    paper(s)
    header(s, "Où ouvrir", "Un verbe, un dossier.")
    pairs(s, [
        ("services/agent/", "Gate, RAG, tool loop, prompts, token_budget"),
        ("services/tools/", "Handlers, path_safety, argv, shell=False"),
        ("services/indexer/", "Chunks tree-sitter, embeddings, upsert"),
        ("services/common/", "tool_models.py — le contrat partagé"),
        ("services/mcp/", "lookup, check, outline, calls, types, ask"),
        ("app/", "Client C++ llm-client. Pas le corpus."),
        ("sample/", "Calculatrice buggée. CODEBASE_PATH par défaut."),
        ("tests/", "pytest du service touché. 11 ctest dans app/."),
        ("CONTRACTS.md", "Modèles, ports, variables, plafonds — la loi"),
    ])
    notes(s, "Geste : nommer le verbe avant d’ouvrir. sample/ n’est pas app/. CONTRACTS.md dès qu’un changement traverse deux services.")
    return s, "03  Dépôt"


def s_around():
    s = prs.slides.add_slide(BLANK)
    paper(s)
    header(s, "Autour du chemin", "Ce qui existe, et qui ne répond pas à la question.")
    pairs(s, [
        ("compose.yaml", "Trois réseaux internal, loopback, cgroups, profils"),
        ("containers/", "Une image par service. Non-root, cap_drop ALL"),
        ("models/", "GGUF servis. Adapters QLoRA à part"),
        ("agent/rules/", "Consignes par glob, collées au prompt"),
        ("state/workspaces.json", "Copies. Une collection Qdrant par URL"),
        ("scripts/deploy.py", "up, workspace, ask, doctor, rotate-token, train"),
        ("eval_rag.py / eval_tools.py", "Le bon fichier est-il retrouvé ? Trois scénarios"),
        ("Profils", "setup (Internet une fois), train, test, docs, obs"),
        ("SECURITY.md", "Threat model : le LLM est l’entrée non fiable"),
    ])
    notes(s, "docs = mkdocs :8000. obs = Prometheus + Grafana. devtools n’est sur aucun des trois réseaux. fetch-models est le seul egress.")
    return s, "03  Dépôt"


def s_why_retrieval():
    s = prs.slides.add_slide(BLANK)
    paper(s)
    header(s, "Pourquoi le retrieval", "On réindexe. On ne réentraîne pas.")
    table(s, ("Choix", "Principe", "Où"), [
        ("RAG, pas le fine-tune", "Un commit se réindexe. On cite le fichier.", "rag.py"),
        ("Chunks tree-sitter", "L’embedding porte une idée entière.", "chunker.py"),
        ("Dense + BM25 + RRF", "Identifiant et paraphrase, ensemble.", "rag.py"),
        ("Reranker, top 5", "Il réordonne. Il ne réécrit pas.", "bge-reranker"),
        ("Q5_K_M sur le 14B", "Le JSON des tool calls tient.", "CONTRACTS.md"),
    ])
    notes(s, "Relier chaque ligne à un mot du chapitre 02. La décision est le « pourquoi », le mot était le « quoi ».")
    return s, "04  Décisions"


def s_why_safety():
    s = prs.slides.add_slide(BLANK)
    paper(s)
    header(s, "Pourquoi ces gardes", "Le modèle est une entrée non fiable.")
    table(s, ("Choix", "Principe", "Où"), [
        ("Pas de shell", "argv, allow-list, path_safety.", "handlers/"),
        ("Lecture seule", "Le diff se copie. L’écriture n’est pas tranchée.", "montage :ro"),
        ("Trois réseaux", "llm ne joint pas tools, ni l’extérieur.", "compose.yaml"),
        ("Bearer", "Le réseau interne n’authentifie pas.", "AGENT_BEARER_TOKEN"),
        ("Boucle à 8", "Un LLM outillé ne s’arrête pas seul.", "tool_loop.py"),
    ])
    notes(s, "Analogie : on ne concatène pas une saisie dans bash. Le bearer se régénère avec deploy.py rotate-token.")
    return s, "04  Décisions"


def s_why_product():
    s = prs.slides.add_slide(BLANK)
    paper(s)
    header(s, "Pourquoi cette forme", "Séparer les rôles, pas les empiler.")
    table(s, ("Choix", "Principe", "Où"), [
        ("Collection par URL", "Deux tickets partagent l’index.", "workspaces.json"),
        ("Fichiers sales", "Relus devant les chunks. Pas d’upsert.", "agent"),
        ("Deux portes", "L’éditeur rédige, ou le 14B. Pas les deux.", "MCP ask"),
        ("Faits et jugement", "lookup renvoie des lignes. ask juge.", "mcp"),
        ("QLoRA à part", "Adaptateur 7B. Il ne charge pas le 14B.", "profil train"),
    ])
    notes(s, "workspace <chemin> enregistre. --update réindexe. La deuxième copie du même remote ne réindexe pas.")
    return s, "04  Décisions"


def s_config_frame():
    s = prs.slides.add_slide(BLANK)
    paper(s)
    header(s, "Deux familles", "Un réglage se change. Un refus, non.")
    bands(s, [
        ("Réglage", "Il vit dans .env. On le change, on recrée le service. Fenêtre, boucle, reranker, qui a le GPU. doctor le relit."),
        ("Refus", "Montage :ro, outil absent, réseau internal, pas de shell. Ce n’est pas une variable. Le changer est un choix de produit."),
        ("Une source", "Copier .env.example vers .env. Compose injecte ces noms. Aucun hôte n’est en dur. L’ordre d’un plafond : CONTRACTS.md, puis .env.example, puis le test d’alignement."),
    ])
    notes(
        s,
        "Slide charnière. Faire répéter : réglage / refus. Ne pas laisser croire qu’on « active l’écriture » avec une variable. "
        "deploy.py doctor vérifie token, SHA256, GPU, cohérence du build. up ne réécrit pas LLM_GPU_ARG dans .env.",
    )
    return s, "05  Config"


def s_knobs():
    s = prs.slides.add_slide(BLANK)
    paper(s)
    header(s, "Les réglages", "Sept noms. Le reste a un défaut voulu.")
    table(
        s,
        ("Variable", "Défaut", "Ce que ça change"),
        [
            ("CODEBASE_PATH", "./sample", "Arbre monté :ro. Le jouet, par défaut."),
            ("LLM_CTX_SIZE", "32768", "Fenêtre dure. Pas un cap de VRAM."),
            ("AGENT_CONTEXT_BUDGET_TOKENS", "28544", "Compression avant la fenêtre."),
            ("AGENT_TOOL_LOOP_MAX_ITERATIONS", "8", "La boucle de tool calls s’arrête."),
            ("AGENT_REFLECT", "0", "1 = second passage, il efface."),
            ("AGENT_GATE_CLASSIFY", "1", "Appel court si les règles hésitent."),
            ("RERANKER_BASE_URL", "défini", "Vide : pas de second tri."),
        ],
        top=1.66,
        row_h=0.72,
        cols=(
            (Inches(0.95), Inches(4.55)),
            (Inches(5.6), Inches(1.7)),
            (Inches(7.45), Inches(5.0)),
        ),
        mono_col=0,
    )
    notes(
        s,
        "AGENT_MAX_CONCURRENT=2 (le reste : 503). TTL conversation 3600 s, 8 tours, mémoire seulement. "
        "TOOLS_CMAKE_PRESET=release. TOOLS_BUILD_TIMEOUT_SECONDS=14400 sur chaque subprocess. "
        "Montrer .env.example, pas une liste exhaustive.",
    )
    return s, "05  Config"


def s_ctx_limits():
    s = prs.slides.add_slide(BLANK)
    paper(s)
    header(s, "Fenêtre", "32 768 tokens. Le budget est plus bas.")
    shape(s, MX, Inches(1.48), Inches(4.15), Inches(4.15), DARK)
    txt(s, Inches(1.05), Inches(1.85), Inches(3.7), Inches(0.85), "32 768", size=40, bold=True, color=WHITE)
    txt(s, Inches(1.05), Inches(2.75), Inches(3.7), Inches(0.35), "tokens", size=16, color=GOLD)
    txt(s, Inches(1.05), Inches(3.25), Inches(3.7), Inches(0.4), "LLM_CTX_SIZE", size=14, color=GOLD, font=MONO)
    txt(s, Inches(1.05), Inches(3.9), Inches(3.7), Inches(1.3), "La fenêtre du modèle.\nPas la taille du dépôt.", size=15, color=WHITE)
    rights = [
        ("3 200", "Template de chat et schémas d’outils. Réservés, pas mesurés dans le prompt."),
        ("1 024", "Réserve pour la réponse. AGENT_CONTEXT_REPLY_RESERVE."),
        ("28 544", "Budget mesuré. Au-delà : vieux tool results raccourcis, puis tours pliés en une note."),
        ("8 tours · 1 h", "Mémoire seulement. Rootfs read-only de agent. Pas de disque."),
    ]
    for i, (n, body) in enumerate(rights):
        y = Inches(1.48 + i * 1.04)
        shape(s, Inches(5.15), y, Inches(7.4), Inches(0.96), WASH)
        txt(s, Inches(5.35), y + Inches(0.1), Inches(7.0), Inches(0.28), n, size=14, bold=True, color=ACCENT)
        txt(s, Inches(5.35), y + Inches(0.4), Inches(7.0), Inches(0.48), body, size=12, color=INK)
    shape(s, MX, Inches(5.8), CONTENT_W, Inches(0.85), ACCENT_SOFT)
    txt(
        s, Inches(1.02), Inches(5.92), Inches(11.3), Inches(0.62),
        "Deux autres budgets, 16 000 caractères chacun : rules/*.md selon les globs, et la repo map (AGENT_REPO_MAP_CHARS) si la question porte sur la forme de l’arbre.",
        size=13, color=INK,
    )
    notes(
        s,
        "token_budget.py. La page montre l’événement context (used / window). Si le serveur refuse encore la taille, "
        "une compression de plus et un retry. Le résumé garde chemins, symboles, décisions, résultats d’outils.",
    )
    return s, "05  Config"


def s_caps():
    s = prs.slides.add_slide(BLANK)
    paper(s)
    header(s, "Plafonds d’action", "Chaque appel est borné. La boucle aussi.")
    pairs(s, [
        ("read_file", "400 lignes. Fichier au-delà de 256 KiB : 413."),
        ("search", "Défaut 50 matches, plafond 100."),
        ("list_files", "Défaut 200 chemins, plafond 500."),
        ("git / svn", "200 lignes. Références filtrées."),
        ("clangd", "40 emplacements. Hover borné."),
        ("tool loop", "8 tours. JSON invalide : un essai, puis texte."),
        ("concurrence", "2 chats. Au-delà, 503."),
        ("timeouts", "Outil : 14 400 s. Question entière : 172 800 s."),
        ("ask (MCP)", "400 tokens. Pas de schéma d’outils."),
    ], left_w=2.5)
    notes(s, "Ces caps sont dans tool_models.py et les handlers, pas seulement dans la doc. Les bouger = contrat, donc CONTRACTS.md et le test anti-dérive.")
    return s, "05  Config"


def s_gpu():
    s = prs.slides.add_slide(BLANK)
    paper(s)
    header(s, "VRAM", "Sans mémoire, la fenêtre ne grandit pas.")
    trio(s, [
        ("Cache KV q8_0", "LLM_CACHE_TYPE_K et V. Environ moitié d’un cache f16. C’est ce qui fait tenir 32 768 du 14B Q5 sur 16 Go."),
        ("Un seul slot", "LLM_PARALLEL=1. Marge laissée sur la carte : 2 048 Mio. Cache RAM plafonné à 4 096 Mio. LLM_NGL=auto : autant de couches que la carte peut prendre."),
        ("Qui a le GPU", "Le chat d’abord. RERANKER_NGL=0. EMBEDDER_GPU_ARG=0 laisse l’embedder sur CPU si la carte est juste."),
    ], height=3.55)
    shape(s, MX, Inches(5.2), CONTENT_W, Inches(1.42), DARK)
    txt(
        s, Inches(1.06), Inches(5.4), Inches(11.2), Inches(1.05),
        "python scripts/host/context_limits.py — ce qui tient pour le GGUF du .env.\nUn Q4 rentre plus facilement et appelle les tools moins bien. Élargir LLM_CTX_SIZE sans ce script ne crée pas de VRAM.",
        size=15, color=WHITE,
    )
    notes(s, "Le 14B a été entraîné à 32 768 : monter au-delà ne gagne pas du contexte appris. Si un swap de modèle échoue, le routeur le dit et l’ancien modèle n’est plus chargé tant qu’un modèle qui tient n’est pas choisi.")
    return s, "05  Config"


def s_scope():
    s = prs.slides.add_slide(BLANK)
    paper(s)
    header(s, "Refus", "Ces limites ne sont pas des variables.")
    items = [
        ("Ça n’écrit pas", "Montage :ro. Pas d’apply_patch. Qui écrit, où, avec quelle relecture : non tranché."),
        ("Ça n’a pas le Web", "Pas de fetch_url. Pas de cppreference embarqué. Rester hors ligne est le choix."),
        ("Ça ne surveille pas Windows", "inotify via WSL ne voit pas l’hôte. Après un changement : workspace --update, ou index."),
        ("Ça n’est pas un oracle", "Le LLM se trompe. Les tools vérifient. AGENT_REFLECT=0 : la relecture est éteinte."),
        ("Le retrieval reste ouvert", "Sur un gros arbre, le top 5 peut préférer un preset au symbole nommé."),
        ("L’exploitation reste ouverte", "Images épinglées par tag, pas par digest. Pas de snapshot Qdrant."),
    ]
    for i, (title, body) in enumerate(items):
        col, row = i % 2, i // 2
        x = MX + Inches(col * 6.02)
        y = Inches(1.46 + row * 1.75)
        shape(s, x, y, Inches(5.82), Inches(1.6), WASH)
        shape(s, x, y, Pt(4), Inches(1.6), BAD)
        txt(s, x + Inches(0.28), y + Inches(0.16), Inches(5.3), Inches(0.34), title, size=15, bold=True, color=INK)
        txt(s, x + Inches(0.28), y + Inches(0.56), Inches(5.3), Inches(0.88), body, size=13, color=MUTED)
    notes(s, "Ne pas s’excuser du :ro. sample/ est un réglage (CODEBASE_PATH), pas un refus : un workspace enregistre le vrai dépôt. Le LoRA 7B est une limite du modèle servi, déjà dite.")
    return s, "05  Config"


def s_daily():
    s = prs.slides.add_slide(BLANK)
    paper(s)
    header(s, "Demain matin", "Une commande. Trois portes loopback.")
    sh = shape(s, MX, Inches(1.46), CONTENT_W, Inches(2.05), CODE_BG)
    tf = inset_shape_text(sh, pad=0.24)
    lines = [
        "python scripts/deploy.py up",
        "python scripts/deploy.py ask \"How does the indexer chunk files?\"",
        "python scripts/deploy.py workspace d:/svn/mon_projet",
        "python scripts/deploy.py doctor",
    ]
    for i, line in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.space_before = Pt(0 if i == 0 else 4)
        run(p, line, size=15, color=CODE_FG if i < 3 else CODE_DIM, font=MONO)
    ports = [
        (":8090/ui", "La page. RAG, tools, le 14B rédige."),
        (":8092", "MCP. Faits, et ask court."),
        (":8080", "Le LLM seul, si l’éditeur rédige."),
    ]
    for i, (port, body) in enumerate(ports):
        x = MX + Inches(i * 4.02)
        shape(s, x, Inches(3.7), Inches(3.85), Inches(1.35), WASH)
        txt(s, x + Inches(0.2), Inches(3.82), Inches(3.45), Inches(0.32), port, size=15, bold=True, color=INK, font=MONO)
        txt(s, x + Inches(0.2), Inches(4.2), Inches(3.45), Inches(0.65), body, size=13, color=MUTED)
    txt(
        s, MX, Inches(5.25), CONTENT_W, Inches(1.45),
        "workspace enregistre une copie. Deux tickets du même remote partagent la collection. Les fichiers non commités sont relus, pas upsertés. Après un gros changement : workspace --update. index ne vise que le corpus par défaut, sample/.",
        size=14, color=INK,
    )
    notes(s, "Windows : Podman sans machine continue dans WSL, distro local-llm. doctor avant d’incriminer le modèle. Ne pas indexer app/.")
    return s, "06  Pratique"


def s_map():
    s = prs.slides.add_slide(BLANK)
    paper(s)
    header(s, "Modifier", "Intention, fichiers, preuve.")
    table(s, ("Intention", "Fichiers", "Preuve"), [
        ("Ajouter un tool", "tool_models.py · handlers/", "pytest tests/tools tests/common"),
        ("Changer le ton", "prompts.py · rules/*.md", "test_prompts.py"),
        ("Gate direct / code", "agent/gate.py", "test_gate.py"),
        ("Mauvais chunks", "rag.py · chunker.py", "test_rag.py"),
        ("Autre dépôt", "deploy.py workspace", "une question, page ou éditeur"),
        ("Un plafond", "CONTRACTS.md puis .env.example", "test d’alignement, doctor"),
    ], row_h=0.78, mono_col=1)
    notes(s, "La ligne « un plafond » est le geste du chapitre 05. agent ne recopie pas le schéma des tools : il le dérive de tool_models.py.")
    return s, "06  Pratique"


def s_recipe():
    s = prs.slides.add_slide(BLANK)
    paper(s)
    header(s, "Ajouter un tool", "Copier un handler. Pas un shell.")
    steps = [
        ("1", "Handler", "handlers/<nom>.py\nvalidate, argv"),
        ("2", "Contrat", "tool_models.py\nArgs + TOOL_SPECS"),
        ("3", "Route", "tools/main.py\ndef, pas async def"),
        ("4", "Consignes", "prompts.py\nciter le nom"),
        ("5", "Tests", "rejet avant\nsubprocess"),
        ("6", "Filet", "tests/common\ncontrat = OpenAPI"),
        ("7", "Docs", "tools-api.md\nCONTRACTS si plafond"),
        ("8", "Santé", "async gèle /health\npendant cmake"),
    ]
    for i, (n, title, body) in enumerate(steps):
        col, row = i % 4, i // 4
        x = MX + Inches(col * 3.02)
        y = Inches(1.48 + row * 2.6)
        shape(s, x, y, Inches(2.88), Inches(2.42), WASH)
        pill(s, x + Inches(0.16), y + Inches(0.18), n)
        txt(s, x + Inches(0.62), y + Inches(0.2), Inches(2.1), Inches(0.34), title, size=14, bold=True, color=INK)
        txt(s, x + Inches(0.16), y + Inches(0.75), Inches(2.55), Inches(1.45), body, size=14, color=MUTED)
    notes(s, "Guide : docs/guides/adding-a-new-tool.md. Pièges : async def, f-string dans une commande, chemin sans path_safety. Un plafond neuf passe par CONTRACTS.md.")
    return s, "06  Pratique"


def s_day():
    s = prs.slides.add_slide(BLANK)
    paper(s)
    header(s, "Première journée", "Quatre heures. Un seul levier.")
    steps = [
        ("A", "20 min", "Relire les mots, puis docs/concepts/rag-explained.md — le mécanisme avec les vrais noms."),
        ("B", "20 min", "CONTRACTS.md : Models, Networks, Environment. Repérer les plafonds, ne pas les mémoriser."),
        ("C", "1 h", "agent/main.py, execute_turn. Puis token_budget.py : fenêtre, réserve, compression."),
        ("D", "le reste", "Casser un test exprès (shell=True). Voir le filet. Puis un PR d’un seul levier."),
    ]
    for i, (letter, when, what) in enumerate(steps):
        y = Inches(1.48 + i * 1.28)
        pill(s, MX, y + Inches(0.22), letter)
        txt(s, Inches(1.4), y + Inches(0.08), Inches(1.7), Inches(0.32), when, size=13, bold=True, color=ACCENT)
        txt(s, Inches(1.4), y + Inches(0.46), Inches(10.9), Inches(0.6), what, size=16, color=INK)
    notes(s, "Definition of done : un collègue relit le PR avec la table « intention / fichiers / preuve » sans visio. indexing-pipeline.md et tool-calling.md le lendemain, pas le jour même.")
    return s, "06  Pratique"


def s_lexicon():
    s = prs.slides.add_slide(BLANK)
    paper(s)
    header(s, "À emporter", "Les mots, une fois qu’on les a vus.")
    rows = [
        ("LLM", "Continue un texte. Inférence = poids déjà là."),
        ("Token / contexte", "Sous-mot. Fenêtre 32 768, budget 28 544."),
        ("GGUF / Q5", "Fichier de poids. Sous Q5, le JSON casse."),
        ("RAG", "Chercher des chunks, puis rédiger."),
        ("Chunk", "Fonction ou classe, via tree-sitter."),
        ("Embedding", "768 floats, cosinus. Pas un hash."),
        ("BM25 / RRF", "Mots exacts, puis fusion avec le dense."),
        ("Reranker", "20 candidats, 5 gardés. CPU."),
        ("Tool calling", "Nom validé, puis argv. Jamais un shell."),
        ("MCP", "Faits éditeur. ask = 400 tokens, sans tools."),
        ("QLoRA", "Entraînement 7B. Hors du 14B servi."),
        ("Gate", "direct sans index. code avec RAG et tools."),
        ("Réglage", ".env. CONTRACTS.md fige le nom."),
        ("Refus", ":ro, pas de shell, pas de Web. Pas une variable."),
    ]
    for i, (term, gloss) in enumerate(rows):
        col, row = i % 2, i // 2
        x = MX + Inches(col * 6.02)
        y = Inches(1.42 + row * 0.74)
        if row % 2 == 0:
            shape(s, x, y, Inches(5.85), Inches(0.68), WASH)
        txt(s, x + Inches(0.16), y + Inches(0.14), Inches(2.15), Inches(0.4), term, size=13, bold=True, color=INK, font=MONO)
        txt(s, x + Inches(2.35), y + Inches(0.14), Inches(3.35), Inches(0.4), gloss, size=12, color=MUTED)
    notes(s, "Diapo à photographier. Si un mot ne revient pas, revenir à son slide, pas à cette liste.")
    return s, "06  Pratique"


def s_close():
    s = prs.slides.add_slide(BLANK)
    paper(s, DARK)
    shape(s, Inches(0), Inches(0), W, Pt(4), GOLD)
    txt(s, MX, Inches(0.4), CONTENT_W, Inches(0.28), "POUR CONTINUER", size=12, bold=True, color=GOLD)
    txt(s, MX, Inches(0.75), CONTENT_W, Inches(0.85), "Les mots sont les noms\ndu code. Les plafonds sont la loi.", size=26, bold=True, color=WHITE)
    shape(s, MX, Inches(1.8), Inches(1.2), Pt(3), GOLD)
    docs = [
        ("Le retrieval, en mécanisme", "docs/concepts/rag-explained.md"),
        ("Les chunks", "docs/concepts/indexing-pipeline.md"),
        ("La boucle d’outils", "docs/concepts/tool-calling.md"),
        ("Noms, ports, plafonds", "CONTRACTS.md"),
        ("S’en servir, et les limites", "docs/guides/using-the-assistant.md"),
        ("Changer le GGUF ou le KV", "docs/guides/swapping-the-model.md"),
    ]
    for i, (why, path) in enumerate(docs):
        col, row = i % 2, i // 2
        x = MX + Inches(col * 6.05)
        y = Inches(2.15 + row * 1.15)
        txt(s, x, y, Inches(5.7), Inches(0.28), why, size=14, color=RGBColor(0xB7, 0xC2, 0xD0))
        txt(s, x, y + Inches(0.32), Inches(5.7), Inches(0.4), path, size=13, bold=True, color=GOLD, font=MONO)
    txt(s, MX, Inches(6.7), CONTENT_W, Inches(0.3), "podman compose --profile docs up    →    http://localhost:8000", size=13, color=RGBColor(0x6B, 0x7A, 0x8C), font=MONO)
    notes(s, "Clore sur la distinction réglage / refus. Prochaine session = pairing sur un tool ou sur un plafond, pas un second cours. SECURITY.md et stack-presentation.md si on creuse.")
    return s, None


def main():
    built: list[tuple] = []
    built.append(s_title())
    built.append(s_agenda())
    built.append(s_section("01", "Les principes", "Deux règles, puis les mots sans lesquels le reste ne tient pas.", "01"))
    built.append(s_product())
    built.append(s_rules())
    built.append(s_llm())
    built.append(s_context())
    built.append(s_section("02", "Le vocabulaire", "Chaque mot arrive au moment où la question en a besoin.", "02"))
    built.append(s_weights())
    built.append(s_rag())
    built.append(s_chunk())
    built.append(s_embedding())
    built.append(s_hybrid())
    built.append(s_calling())
    built.append(s_flow())
    built.append(s_section("03", "Le dépôt", "Les noms du code sont les mots qu’on vient d’apprendre.", "03"))
    built.append(s_processes())
    built.append(s_networks())
    built.append(s_models())
    built.append(s_catalog_tools())
    built.append(s_doors())
    built.append(s_folders())
    built.append(s_around())
    built.append(s_section("04", "Les décisions", "Chaque fichier existe parce qu’un autre choix a été écarté.", "04"))
    built.append(s_why_retrieval())
    built.append(s_why_safety())
    built.append(s_why_product())
    built.append(s_section("05", "Configuration et limites", "Un réglage se change dans .env. Un refus, non.", "05"))
    built.append(s_config_frame())
    built.append(s_knobs())
    built.append(s_ctx_limits())
    built.append(s_caps())
    built.append(s_gpu())
    built.append(s_scope())
    built.append(s_section("06", "La pratique", "Lancer, modifier un plafond, premier levier.", "06"))
    built.append(s_daily())
    built.append(s_map())
    built.append(s_recipe())
    built.append(s_day())
    built.append(s_lexicon())
    built.append(s_close())

    total = len(built)
    for i, (sl, chapter) in enumerate(built, start=1):
        if chapter:
            footer(sl, i, total, chapter)

    out = Path(__file__).resolve().parent / "fondamentaux-outils-ia.pptx"
    prs.save(str(out))
    print(f"Wrote {out} ({total} slides)")


if __name__ == "__main__":
    main()
