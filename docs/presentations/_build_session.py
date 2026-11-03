"""Session équipe — assistant de code local.

Le parcours reste celui qui a été figé. Chaque slide porte le mécanisme,
la mise en place et le chiffre : les notes ne sont pas le cours.
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
SLATE = RGBColor(0xB7, 0xC2, 0xD0)

FONT = "Segoe UI"
MONO = "Consolas"

W = Inches(13.333)
H = Inches(7.5)
MX = Inches(0.72)
CONTENT_W = Inches(11.9)
FOOTER_Y = Inches(7.12)

prs = Presentation()
prs.slide_width = W
prs.slide_height = H
BLANK = prs.slide_layouts[6]
prs.core_properties.title = "Assistant de code local — session équipe"
prs.core_properties.subject = "Objectif, contrat, usage, anatomie d'une question"
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


def txt(slide, l, t, w, h, text, *, size=16, bold=False, color=INK, font=FONT, align=PP_ALIGN.LEFT, anchor="t"):
    tf = tb(slide, l, t, w, h, anchor=anchor)
    p = tf.paragraphs[0]
    p.alignment = align
    p.space_after = Pt(0)
    run(p, text, size=size, bold=bold, color=color, font=font)
    return tf


def para(tf, text, *, size=16, bold=False, color=INK, font=FONT, align=PP_ALIGN.LEFT, space_before=0):
    p = tf.add_paragraph()
    p.alignment = align
    p.space_before = Pt(space_before)
    p.space_after = Pt(0)
    run(p, text, size=size, bold=bold, color=color, font=font)
    return p


def shape(slide, l, t, w, h, fill, *, line=None):
    sh = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, l, t, w, h)
    sh.fill.solid()
    sh.fill.fore_color.rgb = fill
    if line is None:
        sh.line.fill.background()
    else:
        sh.line.color.rgb = line
        sh.line.width = Pt(1.0)
    sh.shadow.inherit = False
    return sh


def hairline(slide, l, t, w, color=RULE):
    return shape(slide, l, t, w, Pt(0.75), color)


def notes(slide, text: str) -> None:
    slide.notes_slide.notes_text_frame.text = text


def paper(slide, color=PAPER):
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = color


def header(slide, kicker: str, title: str) -> None:
    shape(slide, Inches(0), Inches(0), W, Pt(4), ACCENT)
    txt(slide, MX, Inches(0.28), CONTENT_W, Inches(0.26), kicker.upper(), size=12, bold=True, color=ACCENT)
    txt(slide, MX, Inches(0.52), CONTENT_W, Inches(0.62), title, size=26, bold=True, color=INK)
    hairline(slide, MX, Inches(1.24), CONTENT_W)


def footer(slide, n: int, total: int, chapter: str) -> None:
    hairline(slide, MX, FOOTER_Y, CONTENT_W)
    txt(slide, MX, Inches(7.18), Inches(8.8), Inches(0.26), chapter, size=11, color=MUTED)
    txt(slide, Inches(10.6), Inches(7.18), Inches(2.0), Inches(0.26), f"{n:02d}  /  {total:02d}", size=11, color=MUTED, align=PP_ALIGN.RIGHT)


def card(slide, x, y, w, h, fill=WASH):
    return shape(slide, x, y, w, h, fill)


def label(slide, x, y, w, text, *, color=ACCENT, size=13):
    txt(slide, x, y, w, Inches(0.32), text, size=size, bold=True, color=color)


def body(slide, x, y, w, h, text, *, size=15, color=INK):
    txt(slide, x, y, w, h, text, size=size, color=color)



def fact_rows(slide, rows, *, top=1.40, step=1.02, term_w=2.35, size=14):
    """Terme à gauche, explication sur deux lignes à droite."""
    for i, (term, text) in enumerate(rows):
        y = top + i * step
        txt(slide, MX, Inches(y), Inches(term_w), Inches(0.78), term, size=size, bold=True, color=ACCENT, font=MONO if term[:1].islower() or term.startswith(":") else FONT)
        txt(slide, MX + Inches(term_w + 0.2), Inches(y), Inches(11.55 - term_w), Inches(0.88), text, size=size, color=INK)
        if i < len(rows) - 1:
            hairline(slide, MX, Inches(y + step - 0.1), CONTENT_W)


def s_title():
    s = prs.slides.add_slide(BLANK)
    paper(s, DARK)
    shape(s, Inches(0), Inches(0), W, Pt(4), GOLD)
    txt(s, MX, Inches(1.7), CONTENT_W, Inches(0.3), "SESSION ÉQUIPE", size=13, bold=True, color=GOLD)
    txt(s, MX, Inches(2.15), CONTENT_W, Inches(0.8), "Assistant de code local", size=40, bold=True, color=WHITE)
    shape(s, MX, Inches(3.2), Inches(1.4), Pt(3), GOLD)
    txt(
        s, MX, Inches(3.5), Inches(11.3), Inches(1.6),
        "Ce qu’une question déclenche vraiment.\nLes règles qui tiennent tout le dépôt, les plafonds,\net comment on mesure ce que la machine permet.",
        size=20, color=SLATE,
    )
    txt(s, MX, Inches(6.7), CONTENT_W, Inches(0.3), "Python, C, CMake    ·    le détail est sur les slides", size=14, color=RGBColor(0x6B, 0x7A, 0x8C))
    notes(s, "Le cours est sur les slides. Les notes ne font que préciser un fichier ou un piège. Environ 40 minutes.")
    return s, None


def s_agenda():
    s = prs.slides.add_slide(BLANK)
    paper(s)
    header(s, "Parcours", "On suit une question. Chaque arrêt explique le mécanisme.")
    items = [
        ("01", "Objectif et contrat", "Ce qu’une question produit. Ce qui est épinglé, et pourquoi chaque règle existe."),
        ("02", "S’en servir", "up, dans l’ordre. Puis qui rédige derrière la page, MCP, et le port 8080."),
        ("03", "Anatomie", "Modèle, tokens, fenêtre, index, recherche, les 23 outils, la boucle."),
        ("04", "Machine et réglages", "Qui peut parler à qui. L’effet de changer une variable. Les refus."),
        ("05", "Mesurer, étendre", "Ce que les chiffres disent. L’ordre dans lequel on branche la suite."),
    ]
    for i, (num, title, line) in enumerate(items):
        y = Inches(1.48 + i * 1.05)
        txt(s, MX, y, Inches(0.9), Inches(0.36), num, size=18, bold=True, color=ACCENT)
        txt(s, Inches(1.75), y, Inches(10.5), Inches(0.34), title, size=20, bold=True, color=INK)
        txt(s, Inches(1.75), y + Inches(0.38), Inches(10.5), Inches(0.45), line, size=15, color=MUTED)
    notes(s, "Si le temps manque, on ne saute pas Tokens ni Mesurer : c’est là qu’on comprend la limite matérielle.")
    return s, "Parcours"


def s_objectif():
    s = prs.slides.add_slide(BLANK)
    paper(s)
    header(s, "Objectif", "Une question. Une vérification. Le code ne sort pas.")
    card(s, MX, Inches(1.40), CONTENT_W, Inches(0.7), ACCENT_SOFT)
    txt(s, Inches(0.92), Inches(1.52), CONTENT_W, Inches(0.48), "Exemple  —  « Où gère-t-on un flag absent, dans sample/ ? »", size=16, bold=True, color=INK)
    fact_rows(s, [
        ("Porte", "gate.py voit un sujet de code. La route est code. Un « bonjour » ou un calcul part en direct : pas d’index, pas d’outil, un seul appel au modèle."),
        ("Extraits", "La question est cherchée. Cinq morceaux reviennent, avec chemin et lignes. On vise la fonction, pas les 4 000 fichiers."),
        ("Vérification", "Le modèle peut demander read_file ou un symbole clangd. Il ne lance pas la commande : un autre processus le fait, et renvoie un journal borné."),
        ("Retour", "Texte, sources (fichier et lignes), route. La page affiche en plus le remplissage de la fenêtre. Rien n’est écrit dans l’arbre, rien n’est envoyé dehors."),
    ], top=2.25, step=1.12, term_w=2.15)
    notes(s, "sample/ est une calculatrice avec des bugs volontaires. Le même chemin vaut pour une copie enregistrée avec workspace. Ce n’est pas un IDE et pas un compilateur : le modèle se trompe, l’outil constate.")
    return s, "01   Objectif"


def s_contrat():
    s = prs.slides.add_slide(BLANK)
    paper(s)
    header(s, "Le contrat", "Ce qui est épinglé, et pourquoi la règle existe.")
    card(s, MX, Inches(1.40), Inches(5.85), Inches(5.25))
    label(s, Inches(0.92), Inches(1.52), Inches(5.4), "Figé dans CONTRACTS.md", size=15, color=INK)
    pinned = [
        ("Chat", "14B Instruct, Q5_K_M, ~9,8 Go"),
        ("Embedding", "nomic Q8, 768 dimensions"),
        ("Reranker", "bge Q8, sur CPU"),
        ("Ports", "8090 page, 8092 MCP, 8080 modèle"),
        ("Fenêtre", "32 768 tokens, budget 28 544"),
        ("Boucle", "8 tours. Sorties plafonnées."),
    ]
    for i, (k, v) in enumerate(pinned):
        y = Inches(2.0 + i * 0.68)
        txt(s, Inches(0.92), y, Inches(1.7), Inches(0.4), k, size=14, bold=True, color=ACCENT)
        txt(s, Inches(2.65), y, Inches(3.6), Inches(0.4), v, size=14, color=INK)
    card(s, Inches(6.75), Inches(1.40), Inches(5.85), Inches(5.25), DARK)
    label(s, Inches(7.0), Inches(1.52), Inches(5.3), "La règle, et sa raison", color=GOLD, size=15)
    rules = [
        ("Hors ligne", "Le code ne part pas. Internet : fetch-models, une fois, SHA256."),
        ("Pas de shell", "Le texte du modèle est une entrée non fiable. argv seulement."),
        ("Lecture seule", "Écrire n’est pas tranché. On copie un diff."),
        ("3 réseaux", "Le modèle ne peut pas joindre les outils, ni sortir."),
        ("Jeton", "Le réseau interne n’authentifie personne."),
        ("Un schéma", "tool_models.py. Les deux services ne divergent pas."),
    ]
    for i, (k, v) in enumerate(rules):
        y = Inches(2.0 + i * 0.68)
        txt(s, Inches(7.0), y, Inches(5.3), Inches(0.28), k, size=13, bold=True, color=WHITE)
        txt(s, Inches(7.0), y + Inches(0.26), Inches(5.3), Inches(0.36), v, size=12, color=SLATE)
    notes(s, "Changer un plafond : CONTRACTS.md, puis .env.example, puis le test qui vérifie l’alignement. Aucun hôte n’est en dur. rotate-token régénère le bearer et recrée agent, app et mcp.")
    return s, "01   Objectif"


def s_deploy():
    s = prs.slides.add_slide(BLANK)
    paper(s)
    header(s, "Déployer", "Ce que up fait, dans l’ordre.")
    fact_rows(s, [
        ("GPU", "Sonde et CDI NVIDIA. Si ça échoue, overlay CPU pour cette commande seulement. LLM_GPU_ARG n’est pas réécrit dans .env."),
        ("Poids", "Profil setup, fetch-models. Seul moment où un conteneur a Internet. SHA256 faux : arrêt. Ensuite plus aucune sortie."),
        ("Pile", "Build, up, attente healthy : llm, embedder, reranker, qdrant, agent, tools, mcp, la page."),
        ("Index", "Profil index, un passage sur sample/, sauf --skip-index. Ce n’est pas un service qui reste allumé."),
        ("workspace", "Lit l’URL git ou la racine svn. Collection repo_<hash>. La 1re copie indexe. La 2e enregistre le chemin. --update est incrémental."),
    ], top=1.42, step=1.05, term_w=1.9)
    notes(s, "doctor ensuite : Podman, .env, jeton, SHA256 des GGUF, cohérence de l’image GPU avec .env, santé. ask pose une question, chat ouvre le multi-tour. sample/ est le jouet. Le métier est la copie workspace.")
    return s, "02   Usage"


def s_portes():
    s = prs.slides.add_slide(BLANK)
    paper(s)
    header(s, "Trois portes", "Qui rédige, et quel pipeline tourne.")
    doors = [
        (":8090", "La page", "ui-proxy vers agent. Flux SSE : route, rag, tool_call, tool_result, token, context, done. Porte, recherche, outils, le 14B rédige. Jeton dans l’onglet."),
        (":8092", "MCP", "Prise éditeur. Faits : lookup, check, outline, calls, types. ask juge en 400 tokens, sans outils, et cite chemin:ligne. L’éditeur rédige. X-Workspace choisit la copie."),
        (":8080", "Modèle seul", "llama-server, /v1/chat/completions. Pas d’agent, pas de Qdrant, pas d’outils. Continue parle ici. Enchaîner ask derrière, c’est faire rédiger deux fois."),
    ]
    for i, (port, title, text) in enumerate(doors):
        y = Inches(1.45 + i * 1.7)
        card(s, MX, y, CONTENT_W, Inches(1.55))
        txt(s, Inches(0.95), y + Inches(0.18), Inches(1.6), Inches(0.4), port, size=18, bold=True, color=ACCENT, font=MONO)
        txt(s, Inches(2.6), y + Inches(0.18), Inches(9.6), Inches(0.36), title, size=16, bold=True, color=INK)
        txt(s, Inches(2.6), y + Inches(0.58), Inches(9.6), Inches(0.8), text, size=14, color=INK)
    notes(s, "Les trois ports sont publiés sur 127.0.0.1 seulement. Embedder et Qdrant ne le sont pas. MCP = Model Context Protocol, FastMCP en HTTP. Pas d’extension éditeur maison.")
    return s, "02   Usage"


def s_modele():
    s = prs.slides.add_slide(BLANK)
    paper(s)
    header(s, "Le modèle", "Inférence : des poids gelés. Un seul fichier rédige.")
    fact_rows(s, [
        ("Inférence", "Un passage avant. On ne réentraîne pas pour répondre. llm ne voit ni le disque du dépôt, ni le bac à sable. API OpenAI via llama.cpp."),
        ("GGUF", "Le format de fichier de llama.cpp. Monté en lecture seule. Q5_K_M ≈ 5 bits par poids, ~9,8 Go. Sous Q5, le JSON des tool calls casse plus souvent."),
        ("Chat", "Qwen2.5-Coder-14B-Instruct. C’est lui qui rédige. LLM_ROUTER=1 : la page charge un autre GGUF de ./models/. Un seul à la fois."),
        ("Les autres", "nomic Q8 calcule les 768 nombres. bge Q8 réordonne, sur CPU. Le 0,5B Q4 ne sert qu’au smoke test. Le 7B safetensors ne sert qu’au QLoRA."),
        ("QLoRA", "Rank 16, 200 pas, séquences de 2 048. L’adaptateur est entraîné sur le 7B. Il ne se charge pas sur le 14B qui répond. Le chemin de la réponse n’en dépend pas."),
    ], top=1.40, step=1.05, term_w=1.7)
    notes(s, "La carte va au chat : LLM_NGL=auto, autant de couches que la VRAM prend, le reste en CPU. RERANKER_NGL=0. EMBEDDER_GPU_ARG=0 si la carte est juste. Le choix Q5 est un choix de fiabilité du JSON, pas seulement de taille.")
    return s, "03   Anatomie"


def s_tokens():
    s = prs.slides.add_slide(BLANK)
    paper(s)
    header(s, "Tokens", "Local retire le réseau. Pas la mémoire du prompt.")
    fact_rows(s, [
        ("Token", "Sous-mot. Le modèle ne lit ni des fichiers ni des caractères. parse_arguments peut faire plusieurs tokens. LLM_CTX_SIZE compte des tokens."),
        ("Ce qui change", "Pas d’envoi du code, pas de facture, pas de quota d’un fournisseur. Les poids restent sur le poste. fetch-models est le seul passage Internet."),
        ("Ce qui reste", "Ce 14B a été entraîné sur 32 768 tokens. Au-delà, il n’a pas appris à s’en servir. Allonger la fenêtre ne crée pas cette compétence."),
        ("Cache KV", "Chaque token du prompt stocke clés et valeurs, par couche, en VRAM. Les poids sont fixes. Le cache grossit avec le prompt. C’est lui qui sature la carte."),
        ("q8_0", "Cache en 8 bits au lieu de 16. Environ moitié moins de VRAM. C’est ce qui fait tenir 32 768 du 14B Q5 sur 16 Go. f16 ne tient pas. LLM_PARALLEL=1 : un slot, sinon le cache est multiplié."),
    ], top=1.40, step=1.05, term_w=2.05)
    notes(s, "Dire la phrase : être en local ne donne pas un contexte infini. context_limits.py affiche, pour le GGUF du .env, combien de tokens tiennent selon le type de cache, plafonné par la longueur d’entraînement. On ne monte pas CTX_SIZE sans ce script.")
    return s, "03   Anatomie"


def s_fenetre():
    s = prs.slides.add_slide(BLANK)
    paper(s)
    header(s, "La fenêtre", "Le compte, puis l’ordre dans lequel on coupe.")
    nums = [
        ("32 768", "Fenêtre dure, LLM_CTX_SIZE. Un prompt plus long est raccourci avant l’appel."),
        ("− 3 200", "Template de chat et schémas d’outils. llama-server les compte. Notre mesure du texte, non."),
        ("− 1 024", "Réserve pour la réponse. Sans ça, le modèle n’a plus de place pour écrire."),
        ("= 28 544", "Budget mesuré, AGENT_CONTEXT_BUDGET_TOKENS. C’est le seuil de compression, pas la fenêtre."),
    ]
    for i, (n, text) in enumerate(nums):
        x = MX + Inches(i * 3.05)
        card(s, x, Inches(1.42), Inches(2.92), Inches(2.15))
        txt(s, x + Inches(0.16), Inches(1.54), Inches(2.6), Inches(0.45), n, size=20, bold=True, color=ACCENT)
        txt(s, x + Inches(0.16), Inches(2.1), Inches(2.6), Inches(1.25), text, size=13, color=INK)
    fact_rows(s, [
        ("Puis", "Vieux résultats d’outils réduits à une ligne. Ensuite les tours anciens pliés en une note : chemins, symboles, décisions. Si le serveur refuse encore : une compression de plus, et un essai."),
        ("Aussi", "Jusqu’à 16 000 caractères de règles dont le glob matche. 16 000 pour la carte de signatures, seulement si la question porte sur la forme de l’arbre."),
        ("Mémoire", "8 tours, TTL une heure, en RAM. Le rootfs de agent est en lecture seule : la conversation n’est pas un fichier."),
    ], top=3.75, step=1.02, term_w=1.55)
    notes(s, "Fichier : services/agent/token_budget.py. L’événement SSE context porte used, window, ratio. La page s’en sert pour la jauge. On ne jette pas la question : on jette d’abord les journaux déjà lus.")
    return s, "03   Anatomie"


def s_index():
    s = prs.slides.add_slide(BLANK)
    paper(s)
    header(s, "Index", "Ce que le job écrit dans Qdrant.")
    fact_rows(s, [
        ("Job", "Profil index, un passage, pas un serveur. Il lit CODEBASE_PATH monté en /workspace/app. Il ignore build/, CMakeFiles/, les métadonnées VCS, node_modules."),
        ("Chunk", "tree-sitter. En C++ : fonction, classe, méthode. Ailleurs : section, cible CMake, document YAML ou JSON. Un paquet de 50 lignes mélange deux idées : l’embedding tombe à côté."),
        ("Embedding", "POST /v1/embeddings, préfixe search_document:, 768 floats. Proximité cosinus, pas égalité. Le hash du fichier sert seulement à ne pas recalculer ce qui n’a pas changé."),
        ("Point", "Vecteur dense, vecteur sparse BM25, et le payload : chemin, lignes, symbole, langue. Upsert. Un fichier supprimé est retiré. Une collection par URL distante."),
        ("Corpus", "Par défaut sample/, la calculatrice buggée, pas app/. Deux tickets du même remote partagent la collection. La deuxième copie ne réindexe pas."),
    ], top=1.40, step=1.05, term_w=1.85)
    notes(s, "chunker.py pour le C++, text_chunker.py pour le reste, embeddings_client.py pour le préfixe. Le watch --watch ne voit pas les éditions Windows à travers le bind mount WSL. On y revient dans les limites.")
    return s, "03   Anatomie"


def s_rag():
    s = prs.slides.add_slide(BLANK)
    paper(s)
    header(s, "RAG", "De la question aux cinq extraits. rag.py.")
    fact_rows(s, [
        ("Mot", "Retrieval-Augmented Generation. On va chercher des chunks, on les colle dans la fenêtre, le modèle rédige. On réindexe après un commit. On ne réentraîne pas."),
        ("Porte", "Avant la recherche. direct : salut ou calcul, zéro embed, zéro outil. code : fichier, symbole, CMake, build, test, ou une suite. Le doute reste en code."),
        ("Deux listes", "Préfixe search_query:, puis 20 par le cosinus (dense) et 20 par les mots exacts (BM25). L’identifiant C++ vit dans BM25. La paraphrase vit dans le dense."),
        ("RRF", "Fusion par le rang, pas par une moyenne de scores. Un bon rang d’un seul côté suffit à rester candidat. Qdrant renvoie 20 fusionnés."),
        ("Top 5", "bge relit la question avec chaque candidat (cross-encoder), extrait coupé à 4 000 caractères, et n’en garde que 5. S’il ne répond pas : on tronque l’ordre fusionné."),
    ], top=1.40, step=1.05, term_w=1.85)
    notes(s, "PREFETCH_K=20, RERANK_CANDIDATES=20, TOP_K=5 dans rag.py. Collection encore non hybride : repli dense seul, sans erreur dans la réponse. AGENT_GATE_CLASSIFY=1 : un appel JSON court quand les règles ne tranchent pas. Les fichiers non commités sont relus devant ces 5, pas écrits dans Qdrant.")
    return s, "03   Anatomie"


def s_limites_rag():
    s = prs.slides.add_slide(BLANK)
    paper(s)
    header(s, "Limites", "Où les cinq extraits se trompent, et quoi faire.")
    fact_rows(s, [
        ("Gros arbre", "Le top 5 peut préférer un CMakePresets.json au symbole nommé. C’est le trou qui change les réponses. eval_rag.py le mesure : le bon fichier est-il dans sources."),
        ("Non commité", "Relu sur cette copie, placé devant les chunks. Pas upserté. Une autre copie du même remote ne le voit pas."),
        ("Préfixes", "search_query: à la question, search_document: à l’index. Les inverser ne lève pas d’exception. Les voisins deviennent faux."),
        ("Reranker", "URL vide, ou service muet : on garde la tête de la fusion. Ce n’est pas le même ordre qu’une relecture question contre chunk."),
        ("Windows", "inotify ne traverse pas l’hôte, WSL et le bind mount. Après une édition : workspace --update. index ne vise que sample/."),
    ], top=1.40, step=1.05, term_w=2.05)
    notes(s, "Ne pas promettre que le watch finira par marcher sur Windows. Le contournement est la commande. Le chantier retrieval est le premier de la roadmap, avant toute écriture.")
    return s, "03   Anatomie"


def s_tools_build():
    s = prs.slides.add_slide(BLANK)
    paper(s)
    header(s, "Outils  ·  1 / 2", "Construire, contrôler, lire. Neuf noms.")
    fact_rows(s, [
        ("build", "cmake --preset puis build, dans un dossier jeté. Le preset est dans une liste. Timeout 14 400 s, comme toutes les commandes."),
        ("test", "Le même build, puis ctest. filter devient ctest -R. La regex du filtre est elle aussi filtrée."),
        ("compile_file", "Un seul fichier source. Le configure est mis en cache par racine et par preset. C’est le bon outil pour un translation unit, pas build."),
        ("clang-tidy / format / cppcheck", "lint_clang_tidy, format_check, static_analysis. Ils rapportent. format est un dry-run. Aucun ne modifie le fichier."),
        ("read / search / list", "read_file : 400 lignes, fichier > 256 KiB refusé. search : ripgrep, 50 par défaut, 100 max. list_files : chemins triés, .gitignore, 500 max."),
    ], top=1.40, step=1.05, term_w=3.15, size=13)
    notes(s, "Le modèle choisit le nom. tools exécute une liste d’arguments, shell=False. Clés inconnues rejetées par pydantic. Le schéma vu par le modèle est dérivé de tool_models.py, le même que l’OpenAPI.")
    return s, "03   Anatomie"


def s_tools_lsp():
    s = prs.slides.add_slide(BLANK)
    paper(s)
    header(s, "Outils  ·  2 / 2", "Historique et symboles. Quatorze noms. Lecture seule.")
    fact_rows(s, [
        ("git_log / blame", "Qui a changé, et quelle ligne. log plafonne le nombre de commits. Les deux plafonnent à 200 lignes. Références filtrées."),
        ("git_diff / show", "Diff entre deux réfs, ou le contenu d’un commit. Même plafond de lignes. Pas d’écriture, pas de commit."),
        ("svn_status / diff", "État de la copie de travail, ou le diff contre BASE. Il faut un .svn. Utile sans réindexer."),
        ("svn_log", "Historique d’un chemin. Peut joindre le serveur Subversion du dépôt. Pas l’Internet général. 200 lignes."),
        ("clangd, 7 noms", "definition, references, hover, symbols, outline, calls, types. Préférer definition à search pour une surcharge ou un template. 40 emplacements. outline et calls sont des faits, pas un commentaire."),
    ], top=1.40, step=1.05, term_w=2.55, size=14)
    notes(s, "Total 3+3+3 + 4 + 3 + 7 = 23. lsp_outline renvoie name kind line. lsp_calls et lsp_types renvoient name path:line, direction in/out ou super/sub. Positions 1-based. Fichier clangd au plus 256 KiB.")
    return s, "03   Anatomie"


def s_boucle():
    s = prs.slides.add_slide(BLANK)
    paper(s)
    header(s, "La boucle", "Pourquoi huit tours, et comment un tour se termine.")
    fact_rows(s, [
        ("Le risque", "Avec des outils et l’ordre de vérifier, le modèle recommence : build, recherche, build. Sans borne, la requête ne revient pas."),
        ("Un tour", "On envoie l’historique et les schémas. Pas d’appel d’outil : c’est la réponse. Appel valide : tools l’exécute, le journal revient dans l’historique."),
        ("JSON faux", "Premier échec : on demande une correction. Deuxième échec : réponse en texte, plus d’outil. Les clés hors contrat sont refusées avant toute commande."),
        ("Huit", "AGENT_TOOL_LOOP_MAX_ITERATIONS. Au huitième tour, texte seulement. Toute question a une fin. ask, côté MCP, ne reçoit pas ces schémas."),
        ("À côté", "Deux chats en parallèle. Le troisième prend 503. Chaque commande a 14 400 s. La question entière, côté client, a un timeout bien plus long : 172 800 s."),
    ], top=1.40, step=1.05, term_w=1.7)
    notes(s, "tool_loop.py. Le plafond d’itérations est le filet. Les plafonds par outil (400, 100, 500, 200, 40) sont l’autre filet : même un tour « réussi » ne peut pas renvoyer le dépôt. Changer l’un ou l’autre passe par CONTRACTS.md.")
    return s, "03   Anatomie"


def s_machine():
    s = prs.slides.add_slide(BLANK)
    paper(s)
    header(s, "La machine", "Qui a le droit de parler à qui.")
    nets = [
        ("client-net", "Page, client C++, MCP.", "Ils joignent agent. Pas Qdrant. Pas tools. Publication loopback seulement."),
        ("core-net", "llm, embedder, reranker, Qdrant.", "llm ne monte pas le dépôt. indexer n’est là que le temps du job."),
        ("tools-net", "Le bac à sable.", "cmake, git, clangd. cgroups, non-root, capabilities retirées, rootfs en lecture seule."),
    ]
    for i, (net, who, why) in enumerate(nets):
        x = MX + Inches(i * 4.05)
        card(s, x, Inches(1.42), Inches(3.9), Inches(3.15))
        txt(s, x + Inches(0.2), Inches(1.56), Inches(3.5), Inches(0.36), net, size=16, bold=True, color=ACCENT, font=MONO)
        txt(s, x + Inches(0.2), Inches(2.05), Inches(3.5), Inches(0.7), who, size=15, bold=True, color=INK)
        txt(s, x + Inches(0.2), Inches(2.8), Inches(3.5), Inches(1.5), why, size=14, color=MUTED)
    card(s, MX, Inches(4.75), CONTENT_W, Inches(1.9), DARK)
    txt(
        s, Inches(0.96), Inches(4.95), Inches(11.4), Inches(1.5),
        "agent est le seul sur les trois réseaux. internal: true : aucune route vers l’extérieur.\nUn tool call inventé n’a pas de destinataire chez llm. Le jeton reste exigé : le réseau n’authentifie pas.\nLa menace traitée dans SECURITY.md est le modèle, pas un attaquant sur Internet.",
        size=15, color=WHITE,
    )
    notes(s, "ui-proxy existe parce qu’un conteneur seulement internal ne publie pas un port hôte sous Docker Desktop. llm et mcp rejoignent publish-net pour la même raison, en loopback. devtools n’est attaché à aucun des trois réseaux.")
    return s, "04   Machine"


def s_reglages():
    s = prs.slides.add_slide(BLANK)
    paper(s)
    header(s, "Réglages", "Ce que changer la variable change vraiment.")
    fact_rows(s, [
        ("CODEBASE_PATH", "L’arbre monté en lecture seule pour l’index et les outils. ./sample est le jouet. Le dépôt de travail passe par workspace, pas par cette variable seule."),
        ("LLM_CTX_SIZE", "La fenêtre dure. La monter sans mémoire vidéo ne crée pas de place. context_limits.py d’abord. Le 14B n’a pas été entraîné au-delà de 32 768."),
        ("Budget 28 544", "Trop bas : on compresse alors qu’il restait de la place. Trop haut : llama-server rejette le prompt. Il laisse la réserve de réponse et le template."),
        ("Boucle à 8", "Plus haut, une question peut ne plus revenir. Plus bas, le modèle n’a plus le temps de lire puis de compiler."),
        ("Reranker, NGL, reflect", "URL vide : pas de relecture des candidats. NGL=auto : le surplus de couches va sur CPU. REFLECT=1 : un second passage qui ne fait qu’effacer, éteint par défaut."),
    ], top=1.40, step=0.92, term_w=2.45, size=13)
    txt(
        s, MX, Inches(6.15), CONTENT_W, Inches(0.75),
        "Refus, aucune de ces variables ne les ouvre : écrire dans l’arbre, aller sur le Web, un shell, voir les éditions Windows sans relancer l’index.",
        size=14, color=INK,
    )
    notes(s, "AGENT_GATE_CLASSIFY=1 est le classement court quand les règles hésitent. Le mettre à 0 laisse le doute en code, ce qui est déjà le repli. Source unique : .env copié depuis .env.example. doctor relit, il ne lève pas un refus.")
    return s, "04   Machine"


def s_emporter():
    s = prs.slides.add_slide(BLANK)
    paper(s)
    header(s, "À emporter", "Ce que l’on peut faire. Ce que l’on ne peut pas.")
    card(s, MX, Inches(1.42), Inches(5.85), Inches(5.2))
    label(s, Inches(0.95), Inches(1.58), Inches(5.3), "On peut", size=16, color=GOOD)
    can = [
        "Interroger un dépôt C++ qui reste sur le poste.",
        "Recevoir fichier et lignes, pas une affirmation nue.",
        "Faire compiler, tester, ou résoudre un symbole.",
        "Lire git et svn sans les laisser écrire.",
        "Changer de copie en réindexant, pas en réentraînant.",
        "Passer par la page, l’éditeur, ou le modèle seul.",
    ]
    for i, line in enumerate(can):
        txt(s, Inches(0.95), Inches(2.15 + i * 0.65), Inches(5.4), Inches(0.55), line, size=15, color=INK)
    card(s, Inches(6.75), Inches(1.42), Inches(5.85), Inches(5.2), DARK)
    label(s, Inches(7.0), Inches(1.58), Inches(5.3), "On ne peut pas", color=GOLD, size=16)
    cannot = [
        "Écrire, committer, ou appliquer un patch.",
        "Sortir sur le Web ou embarquer cppreference.",
        "Mettre l’arbre dans la fenêtre de 32 768.",
        "Faire confiance au top 5 sans le mesurer.",
        "Charger le LoRA 7B sur le 14B qui répond.",
        "Ouvrir un shell en ajoutant une variable.",
    ]
    for i, line in enumerate(cannot):
        txt(s, Inches(7.0), Inches(2.15 + i * 0.65), Inches(5.3), Inches(0.55), line, size=15, color=WHITE)
    notes(s, "Slide à laisser. Elle reprend l’objectif avec les limites apprises depuis. Si une ligne surprend, revenir à la slide du mécanisme, pas inventer une exception.")
    return s, "05   Suite"


def s_mesurer():
    s = prs.slides.add_slide(BLANK)
    paper(s)
    header(s, "Mesurer", "Ce que le chiffre dit, et ce qu’il ne dit pas.")
    fact_rows(s, [
        ("eval_rag.py", "Questions dorées. Succès : le bon fichier ou le bon symbole est dans sources. Ça ne note pas la prose. Exit non nul si ça rate. C’est la mesure du trou du top 5."),
        ("eval_tools.py", "Scénarios : build, compile_file, test avec filtre. Succès : le premier appel a le bon nom et les bons arguments. À relancer après un changement de GGUF, de quantization ou de prompt."),
        ("Campagne", "Les questions fonctionnelles, une porte après l’autre. On sépare un fait MCP d’une réponse de la page. Le ressenti sur un exemple ne remplace pas la campagne."),
        ("Matériel", "context_limits.py, sur le GGUF du .env : tokens tenables selon le cache KV, plafonnés par l’entraînement. 14B Q5, q8_0, 16 Go : 32 768. En dessous, c’est la carte, pas « le modèle est mauvais »."),
        ("doctor", "Avant d’incriminer le modèle : Podman, jeton, SHA256, image GPU cohérente avec .env, services healthy. up ne corrige pas .env tout seul."),
    ], top=1.40, step=1.05, term_w=2.15)
    notes(s, "La CI lance ruff, mypy, pytest et ctest. Le smoke eval_rag est hors pull request. Les trois scénarios d’outils sont dans eval_tools_scenarios.json. La campagne éditeur est docs/guides/prompt-tests-fonctionnels.md.")
    return s, "05   Suite"


def s_etendre():
    s = prs.slides.add_slide(BLANK)
    paper(s)
    header(s, "Étendre", "Mesurer d’abord. Brancher dans cet ordre.")
    fact_rows(s, [
        ("1. Retrieval", "Sur une vraie copie, pas sur sample/. eval_rag plus la campagne. Tant que le top 5 rate le symbole, élargir les outils ou écrire ne corrige pas la réponse."),
        ("2. Matériel", "Si context_limits dit que 32 768 ne tient pas : on ne force pas la fenêtre. Des couches passent en CPU, ou on baisse le contexte, et on le note comme limite de la carte."),
        ("3. Écriture", "Toujours en lecture seule. À trancher : qui écrit, dans quelle copie, avec quelle relecture. Après les mesures. Un mauvais extrait plus un patch empire."),
        ("4. Interfaces", "Déjà là : page, MCP pour Cursor et VS Code, port 8080 pour un client OpenAI. Pas d’extension maison. Une prise Jira ou Confluence serait hors conteneurs, et non indexée."),
        ("5. Exploitation", "Digests d’images, snapshot Qdrant. Ça ne change pas une réponse. Ça change une reprise. L’ordre daté est dans docs/roadmap.md."),
    ], top=1.40, step=1.05, term_w=2.25)
    notes(s, "Clore là-dessus. Prochaine séance : une copie réelle, eval_rag, context_limits, pas un second cours. svn est déjà un outil. Le serveur svn-mcp externe, s’il existe, reste en lecture seule.")
    return s, "05   Suite"


def s_close():
    s = prs.slides.add_slide(BLANK)
    paper(s, DARK)
    shape(s, Inches(0), Inches(0), W, Pt(4), GOLD)
    txt(s, MX, Inches(0.45), CONTENT_W, Inches(0.28), "OÙ C’EST ÉCRIT", size=13, bold=True, color=GOLD)
    txt(s, MX, Inches(0.85), CONTENT_W, Inches(0.9), "Les slides tiennent le fil.\nLe dépôt tient le détail.", size=28, bold=True, color=WHITE)
    docs = [
        ("Recherche, préfixes, top 5", "docs/concepts/rag-explained.md"),
        ("Chunks et job d’index", "docs/concepts/indexing-pipeline.md"),
        ("Boucle et schémas d’outils", "docs/concepts/tool-calling.md"),
        ("Noms, ports, plafonds", "CONTRACTS.md"),
        ("S’en servir, limites du quotidien", "docs/guides/using-the-assistant.md"),
        ("Ordre de la suite", "docs/roadmap.md"),
    ]
    for i, (why, path) in enumerate(docs):
        col, row = i % 2, i // 2
        x = MX + Inches(col * 6.1)
        y = Inches(2.15 + row * 1.35)
        txt(s, x, y, Inches(5.7), Inches(0.35), why, size=15, color=SLATE)
        txt(s, x, y + Inches(0.38), Inches(5.7), Inches(0.4), path, size=15, bold=True, color=GOLD, font=MONO)
    notes(s, "SECURITY.md si on creuse la menace. swapping-the-model.md et troubleshooting-gpu.md si la carte ne charge pas.")
    return s, None


def main():
    built = [
        s_title(),
        s_agenda(),
        s_objectif(),
        s_contrat(),
        s_deploy(),
        s_portes(),
        s_modele(),
        s_tokens(),
        s_fenetre(),
        s_index(),
        s_rag(),
        s_limites_rag(),
        s_tools_build(),
        s_tools_lsp(),
        s_boucle(),
        s_machine(),
        s_reglages(),
        s_emporter(),
        s_mesurer(),
        s_etendre(),
        s_close(),
    ]
    total = len(built)
    for i, (slide, chapter) in enumerate(built, start=1):
        if chapter:
            footer(slide, i, total, chapter)
    out = Path(__file__).resolve().parent / "assistant-local.pptx"
    prs.save(str(out))
    print(f"Wrote {out} ({total} slides)")


if __name__ == "__main__":
    main()
