"""Génère presentations/pumba-ia-locale-harness.pptx."""
import os

from deck_lib import *  # noqa: F401,F403

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pumba-ia-locale-harness.pptx")
prs = new_prs()
n = 0


def nxt():
    global n
    n += 1
    return n


# =====================================================================
# 1. Titre
# =====================================================================
s = blank(prs)
nxt()
box(s, 0, 0, SW, SH, fill=NAVY, radius=0)
box(s, 0.9, 2.05, 0.09, 2.3, fill=TEAL, radius=0)
text(s, 1.2, 1.95, 11, 1.6, "IA locale : le modèle ne suffit pas", size=44, bold=True, color=WHITE,
     spacing=0.95)
text(s, 1.2, 3.05, 10.8, 1.3,
     "Retour sur le POC Pumba : ce que nous avons appris, ce qui reste à faire "
     "et les décisions à prendre", size=22, color=RGBColor(0xCB, 0xD5, 0xE1), spacing=1.05)
text(s, 1.2, 5.6, 10, 0.4, "Réflexion d'équipe · 12 novembre 2026", size=16,
     color=RGBColor(0x94, 0xA3, 0xB8))
text(s, 1.2, 6.0, 11, 0.4,
     "Modèle local + harness autour : index, outils, politique d'exécution", size=14,
     color=RGBColor(0x94, 0xA3, 0xB8))
s.notes_slide.notes_text_frame.text = (
    "Objectif de la séance : lancer une réflexion sur le POC réalisé, sur les travaux restants et sur "
    "les décisions à prendre. Le fil rouge : on ne veut pas seulement un modèle local, on veut aussi le "
    "harness (la boucle, les outils, les règles) qui le rend utilisable.")

# =====================================================================
# 2. Ce que la séance doit produire
# =====================================================================
s = frame(prs, "Cadrage", "Ce que cette séance doit produire : des décisions, pas une démo", nxt(),
          notes="Sources : docs/notes/verification/roadmap.md, .scratch/harness-selection/synthese.md et protocole.md. "
                "Le constat sur l'absence de comparaison vient de la conversation du 6 novembre "
                "(« Les évals existantes mesurent Pumba contre lui-même »).")
cols = [
    ("Le constat", TEAL, TEAL_L, [
        "Un POC complet tourne : **modèle local + harness** (index, outils, sandbox). Le code ne "
        "quitte pas la machine.",
        "Ce qui rend le modèle utilisable, c'est la **boucle autour de lui**, pas seulement ses poids.",
        "Le POC est né d'itérations rapides avec des agents : **un seul ADR** pour de nombreuses décisions.",
    ]),
    ("Ce que l'on ignore encore", AMBER, AMBER_L, [
        "Si la **spécialisation de Pumba est nécessaire**, ou si un harness maintenu ailleurs, branché "
        "sur le même modèle, suffit.",
        "Aucune mesure ne compare Pumba à une alternative : les évaluations actuelles le comparent "
        "**à lui-même**.",
        "Quel modèle et quelle machine pour **combien d'utilisateurs**.",
    ]),
    ("Ce qu'il faut trancher", NAVY, GRAY_L, [
        "**Que fait-on du harness** : garder, remplacer ou hybrider.",
        "**Quel modèle cible**, et à quelle échelle d'usage.",
        "**Quelle politique d'exécution** : shell, commit, Subversion.",
        "Ce que l'on **arrête** de maintenir.",
    ]),
]
for i, (t, acc, fill, items) in enumerate(cols):
    x = 0.6 + i * 4.1
    card(s, x, 1.95, 3.95, 3.95, t, [{"t": it, "b": True, "after": 9} for it in items], fill=fill,
         accent=acc, size=14.5, title_size=18)
takeaway(s, "Question centrale : ce qui rend Pumba spécifique est-il nécessaire, ou un harness maintenu ailleurs suffit-il ?", y=6.1, h=0.65, fill=NAVY)

# =====================================================================
# 3. Agent = modèle + harness
# =====================================================================
s = frame(prs, "1 · Le cadre", "Un agent, c'est un modèle plus un harness : les deux se jugent ensemble",
          nxt(),
          notes="Sources : docs/notes/verification/notion-ai-harness.md (Anthropic « Demystifying evals for AI "
                "agents », 2026-01-09 ; LangChain « Agents » ; EleutherAI lm-evaluation-harness, "
                "2021). Le mot a deux sens : harness d'évaluation (lance des benchmarks) et harness "
                "d'agent (la boucle autour du modèle). Ici on parle du second.")
# formule
box(s, 0.6, 2.0, 3.1, 1.25, fill=GRAY_L, line=LINE)
text(s, 0.6, 2.0, 3.1, 1.25, [{"t": "Modèle", "size": 24, "bold": True, "color": NAVY},
                              {"t": "poids + inférence (llama.cpp)", "size": 13, "color": MUTED}],
     align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE, after=2)
text(s, 3.75, 2.0, 0.6, 1.25, "+", size=36, bold=True, color=TEAL, align=PP_ALIGN.CENTER,
     anchor=MSO_ANCHOR.MIDDLE)
box(s, 4.4, 2.0, 3.1, 1.25, fill=TEAL_L, line=TEAL)
text(s, 4.4, 2.0, 3.1, 1.25, [{"t": "Harness", "size": 24, "bold": True, "color": TEAL},
                              {"t": "tout ce qui entoure la boucle", "size": 13, "color": MUTED}],
     align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE, after=2)
text(s, 7.55, 2.0, 0.6, 1.25, "=", size=36, bold=True, color=TEAL, align=PP_ALIGN.CENTER,
     anchor=MSO_ANCHOR.MIDDLE)
box(s, 8.2, 2.0, 2.4, 1.25, fill=NAVY)
text(s, 8.2, 2.0, 2.4, 1.25, [{"t": "Agent", "size": 24, "bold": True, "color": WHITE},
                              {"t": "ce que l'utilisateur voit", "size": 13,
                               "color": RGBColor(0xCB, 0xD5, 0xE1)}],
     align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE, after=2)
text(s, 10.8, 2.0, 1.95, 1.25, "« Agent = Model + Harness »\nLangChain", size=12.5, color=MUTED,
     anchor=MSO_ANCHOR.MIDDLE)

text(s, 0.6, 3.55, 6.0, 0.4, "Ce que contient le harness", size=17, bold=True, color=NAVY)
text(s, 0.6, 3.9, 6.0, 2.3, [
    {"t": "**La boucle** : appeler le modèle, lancer ses outils, renvoyer les résultats, décider quand "
          "s'arrêter", "b": True},
    {"t": "**Le contexte** : quoi montrer au modèle (index, fichiers modifiés, historique, budget de tokens)",
     "b": True},
    {"t": "**Les outils** et leurs limites : lire, chercher, compiler, tester, modifier", "b": True},
    {"t": "**La politique** : permissions, commit interdit ou non, validation des arguments, annulation",
     "b": True},
    {"t": "**L'environnement** d'exécution : sandbox, réseau, montages", "b": True},
], size=13.5, after=3)
text(s, 6.9, 3.55, 5.8, 0.4, "Pourquoi cela change la question", size=17, bold=True, color=NAVY)
text(s, 6.9, 3.95, 5.8, 2.3, [
    {"t": "Évaluer « un agent », c'est évaluer **le harness et le modèle ensemble** (Anthropic, "
          "janvier 2026).", "b": True},
    {"t": "Le mot a deux sens : harness d'**évaluation** (lance des benchmarks, EleutherAI 2021) et "
          "harness d'**agent** (la boucle). Ici, c'est le second.", "b": True},
    {"t": "Dans le POC, le harness est le service `agent` et tout ce qu'il pilote.", "b": True},
], size=14, after=6)
takeaway(s, "Choisir un modèle local sans choisir son harness, c'est choisir la moitié du système.", y=6.32, h=0.56)

# =====================================================================
# 4. Deux contraintes
# =====================================================================
s = frame(prs, "1 · Le cadre", "Deux contraintes ont façonné presque tout le POC", nxt(),
          notes="Sources : docs/concepts/en-clair.md (« Two constraints fixed almost every choice »), "
                ".scratch/harness-selection/criteria.md (décisions du 12 nov. : « pas de shell » est une "
                "préférence, SVN est une exigence, terminal suffisant).")
card(s, 0.6, 1.95, 5.95, 2.95, "1 · Le code ne quitte jamais la machine", [
    {"t": "Chaque processus dans son conteneur, sur **3 réseaux internes** sans route vers Internet", "b": True},
    {"t": "Ports publiés sur `127.0.0.1` seulement", "b": True},
    {"t": "Seul accès Internet : le job qui télécharge les poids, le temps du téléchargement", "b": True},
    {"t": "Conséquence : modèle, embeddings et index tournent tous en local", "b": True},
], size=14)
card(s, 6.78, 1.95, 5.95, 2.95, "2 · Le modèle n'a pas de shell", [
    {"t": "**39 actions nommées** (16 côté `tools`, 23 côté Serena), arguments revalidés", "b": True},
    {"t": "Sorties plafonnées, sources montées en lecture seule", "b": True},
    {"t": "Un seul écrivain (Serena), **jamais de commit ni de push**", "b": True},
    {"t": "Après une écriture : `build_step` obligatoire avant de répondre", "b": True},
], size=14, accent=AMBER)
text(s, 0.6, 5.05, 12, 0.35, "Contexte fixe de l'étude", size=15, bold=True, color=NAVY)
_pw = [2.3, 1.85, 2.25, 3.45, 2.28]
for i, lab in enumerate(["C++ / CMake (Qt possible)", "Git et Subversion", "Windows + Podman / WSL",
                         "Qwen2.5-Coder-14B Q5 · RTX 4080 16 Go", "Fenêtre : 32 768 tokens"]):
    pill(s, 0.6 + sum(_pw[:i]), 5.45, _pw[i] - 0.1, 0.42, lab, fill=GRAY_L, color=INK, size=12.5,
         bold=False)
takeaway(s, "Mise à jour du 12 nov. : « pas de shell » est une **préférence**, pas une exigence. "
            "Subversion, elle, est une exigence.", fill=AMBER, y=6.1)

# =====================================================================
# 5. Architecture
# =====================================================================
s = frame(prs, "1 · Le POC", "Le POC : une dizaine de conteneurs autour d'un seul service qui décide", nxt(),
          notes="Sources : docs/concepts/en-clair.md, README.md, compose.yaml. Seul `agent` est sur les trois "
                "réseaux internes. ~760 fonctions de test Python (tests/) et 15 TEST_CASE C++ (app/tests).")
# gauche
text(s, 0.6, 1.8, 2.6, 0.3, "POSTE UTILISATEUR", size=11, bold=True, color=MUTED)
ux = [("Page `/ui`", "navigateur, jeton saisi dans l'onglet"),
      ("Éditeur", "Cursor / VS Code via MCP : des faits, pas un texte rédigé"),
      ("`app` (C++)", "client en ligne de commande")]
for i, (t_, d_) in enumerate(ux):
    y = 2.15 + i * 1.3
    box(s, 0.6, y, 2.7, 1.05, fill=GRAY_L, line=LINE)
    text(s, 0.7, y + 0.05, 2.5, 1.0, [{"t": t_, "bold": True, "size": 14.5, "color": NAVY},
                                      {"t": d_, "size": 11.5, "color": MUTED}], after=2)
# agent
text(s, 3.9, 1.8, 3.2, 0.3, "LE HARNESS", size=11, bold=True, color=TEAL)
box(s, 3.9, 2.15, 3.3, 3.75, fill=TEAL_L, line=TEAL, line_w=1.5)
text(s, 4.0, 2.2, 3.1, 0.4, "`agent`", size=18, bold=True, color=TEAL)
steps = ["**Porte** : question directe ou sur le code ?",
         "**Recherche** : sens + mots exacts, puis reranker (20 → 5)",
         "**Boucle d'outils** bornée, relances, budget de tokens",
         "**Politique** : pas de shell, pas de commit, écriture → `build_step`"]
for i, st in enumerate(steps):
    y = 2.7 + i * 0.78
    box(s, 4.05, y, 3.0, 0.68, fill=WHITE, line=LINE, radius=0.1)
    text(s, 4.1, y, 2.9, 0.68, st, size=11.5, anchor=MSO_ANCHOR.MIDDLE, after=0)
# droite
text(s, 7.9, 1.8, 4.8, 0.3, "SERVICES PILOTÉS (RÉSEAUX INTERNES)", size=11, bold=True, color=MUTED)
rs = [("`llm`", "llama.cpp : le modèle de chat"),
      ("`embedder` + `reranker`", "vecteurs et second classement"),
      ("`qdrant`", "index C++ découpé par fonction et classe"),
      ("`tools`", "CMake, ctest, clang-tidy, clangd (lecture seule)"),
      ("`serena`", "symboles, fichiers, écriture sans commit")]
for i, (t_, d_) in enumerate(rs):
    y = 2.15 + i * 0.78
    box(s, 7.9, y, 4.83, 0.68, fill=GRAY_L, line=LINE, radius=0.1)
    text(s, 8.0, y + 0.03, 4.65, 0.3, t_, size=13, bold=True, color=NAVY, after=0)
    text(s, 8.0, y + 0.34, 4.65, 0.32, d_, size=11.5, color=MUTED, after=0)
for i in range(3):
    arrow(s, 3.3, 2.67 + i * 1.3, 3.9, 3.2 + i * 0.5)
for i in range(5):
    arrow(s, 7.2, 3.2 + (i - 2) * 0.3, 7.9, 2.49 + i * 0.78)
text(s, 3.9, 6.0, 8.8, 0.35, "`indexer` : un passage unique qui découpe l'arbre, calcule les embeddings et "
                            "remplit `qdrant` (ce n'est pas un serveur).", size=12, color=MUTED)
takeaway(s, "Seul `agent` est relié aux trois réseaux : un modèle qui invente un appel ne peut ni ouvrir "
            "de shell, ni sortir de la machine.", y=6.4, h=0.52, size=13.5)

# =====================================================================
# 6. Chemin parcouru
# =====================================================================
s = frame(prs, "1 · Le POC", "Trois mois et près de soixante sessions de travail avec des agents", nxt(),
          notes="Sources : docs/notes/verification/roadmap.md §0 et §7 (Gantt : livraisons du 10 sept. au 28 sept., puis du 13 oct. "
                "au 12 nov.), historique des conversations de travail (première le 11 août 2026). "
                "Le dépôt n'a été mis sous git qu'au 3-5 novembre : les dates antérieures viennent des sessions.")
learn = ["Le hors-ligne structure tout.", "Le sens seul ne suffit pas à retrouver le code.",
         "Les gros dépôts révèlent les trous.", "Le 14B a besoin d'un harness qui compense.",
         "La capacité se joue sur la bande passante.", "Il manque une mesure commune."]
tl = [("11 août", "Question de départ", "Un LLM local branché sur un projet C++/CMake, tout en conteneurs "
                                         "hors-ligne."),
      ("10–28 sept.", "Le socle", "Index tree-sitter + Qdrant, outils bornés, MCP, page `/ui`, "
                                  "recherche hybride + reranker."),
      ("fin sept.–oct.", "L'épreuve du réel", "Gros dépôts (OpenCV), VS Code, déploiement Linux et WSL, "
                                              "observabilité, édition sans commit."),
      ("13 oct.–12 nov.", "La boucle", "Boucle d'itération (relances, phase d'édition), annulation de "
                                       "tour, Serena en sidecar."),
      ("3–10 nov.", "L'échelle", "EVO-X5 (192 Go), 20 utilisateurs, choix de modèles, "
                                 "multi-clients, indexation."),
      ("6–12 nov.", "Le doute utile", "« Existe-t-il déjà mieux ? » → audit des libs, puis étude "
                                       "de l'état de l'art des harness.")]
hline(s, 0.9, 2.55, 11.6, color=TEAL, weight=2.5)
for i, (d_, t_, b_) in enumerate(tl):
    x = 0.6 + i * 2.05
    dot = box(s, x + 0.82, 2.4, 0.3, 0.3, fill=TEAL if i < 5 else AMBER, shape_type=MSO_SHAPE.OVAL,
              radius=0)
    text(s, x, 1.85, 1.95, 0.4, d_, size=13.5, bold=True, color=NAVY, align=PP_ALIGN.CENTER)
    box(s, x, 2.95, 1.95, 3.0, fill=GRAY_L)
    text(s, x + 0.08, 3.02, 1.8, 0.7, t_, size=15, bold=True, color=TEAL if i < 5 else AMBER)
    text(s, x + 0.08, 3.6, 1.8, 1.6, b_, size=12.5, color=INK)
    hline(s, x + 0.1, 5.2, 1.75)
    text(s, x + 0.08, 5.25, 1.8, 0.7, "→ " + learn[i], size=11.5, bold=True, color=TEAL if i < 5 else AMBER)
takeaway(s, "Ce qui a été livré tient en une ligne ; ce qui a été **appris** en fait la valeur. "
            "C'est l'objet de la partie suivante.", y=6.2, h=0.6)

# =====================================================================
# 7. Appris 1 : modèle pas fiable
# =====================================================================
s = frame(prs, "2 · Ce que nous avons appris · 1/7",
          "Le modèle seul n'est pas fiable : une partie du harness sert à le compenser", nxt(),
          notes="Sources : .scratch/harness-selection/synthese.md §2 (BFCL du 2026-04-12 : Qwen3-30B-A3B 30 % en "
                "multi-tour contre 85,8 % en non-live), docs/architecture/agent-loop.md, docs/notes/verification/roadmap.md §10, "
                "conversation du 5 nov. (essai « factorielle » : trois appels à un test introuvable ; "
                "depuis, ajout du parsing des appels écrits en prose et de `--no-tests=error`). "
                "Chiffres de modèles auto-déclarés par les éditeurs.")
text(s, 0.6, 1.85, 6.0, 0.4, "Ce que l'on a observé", size=17, bold=True, color=RED)
text(s, 0.6, 2.3, 6.0, 3.8, [
    {"t": "**Qwen2.5-Coder-14B** : aucun score de tool-calling publié (model card, BFCL du 12 avril 2026).",
     "b": True},
    {"t": "Pour toute la classe ≤ 32B, le BFCL chute en multi-tour (ex. Qwen3-30B-A3B : **30 %** contre "
          "85,8 % hors multi-tour).", "b": True},
    {"t": "En pratique : appels d'outils **écrits dans la prose**, lots malformés, même appel répété.",
     "b": True},
    {"t": "Essai du 5 nov. (« écris la factorielle ») : le 14B n'écrit rien ; il relance **3 fois** un test "
          "qui n'existe pas.", "b": True},
], size=15, after=11)
text(s, 6.9, 1.85, 5.8, 0.4, "Ce que le harness fait pour compenser", size=17, bold=True, color=TEAL)
rows = [("Relances", "jusqu'à 3 « nudges » quand le modèle répond sans agir"),
        ("Appels déguisés", "un JSON `name` + `arguments` écrit dans le texte est exécuté s'il est valide"),
        ("Erreurs lisibles", "validation Pydantic, message précis renvoyé au modèle"),
        ("Prose refusée", "tant qu'un échec est ouvert ou qu'une écriture n'est pas vérifiée"),
        ("Coupe-circuit", "4e lot identique ou lot malformé répété : fin sans outils")]
for i, (a, b) in enumerate(rows):
    y = 2.3 + i * 0.72
    box(s, 6.9, y, 5.83, 0.64, fill=TEAL_L, radius=0.1)
    text(s, 7.0, y, 1.6, 0.64, a, size=12.5, bold=True, color=TEAL, anchor=MSO_ANCHOR.MIDDLE, after=0)
    text(s, 8.55, y, 4.1, 0.64, b, size=12, anchor=MSO_ANCHOR.MIDDLE, after=0)
takeaway(s, "Une partie de la valeur du POC est de la **compensation de modèle** : elle change si le modèle change. "
            "À mesurer, pas à supposer.")

# =====================================================================
# 8. Appris 2 : retrieval
# =====================================================================
s = frame(prs, "2 · Ce que nous avons appris · 2/7",
          "Retrouver le bon code : le sens seul ne suffit pas, et la recherche a encore des trous", nxt(),
          notes="Sources : conversation du 29 sept.–1er oct. (mesure sur OpenCV, checkout propre 6950bedb, "
                "reranker actif, sans modèle de chat, 14 questions), docs/notes/verification/roadmap.md §4.3. "
                "6/14 dans les 5 premiers ; 3 de plus dans le préfetch (~20) ; 5 jamais candidats. "
                "Le symbole inventé Zorglub42 reste bien absent.")
cd = CategoryChartData()
cd.categories = ["Symbole nommé (7)", "Comportement décrit (5)", "Chemin collé (2)"]
cd.add_series("Dans les 5 retenus", (6, 0, 0))
cd.add_series("Dans le préfetch, pas retenu", (1, 2, 0))
cd.add_series("Absent des candidats", (0, 3, 2))
gf = s.shapes.add_chart(XL_CHART_TYPE.BAR_STACKED, Inches(0.6), Inches(2.25), Inches(6.5), Inches(3.75), cd)
ch = gf.chart
style_chart(ch, 12)
for ser, col in zip(ch.plots[0].series, (TEAL, AMBER, RGBColor(0x9C, 0xA3, 0xAF))):
    color_series(ser, col)
ch.plots[0].gap_width = 55
ch.plots[0].overlap = 100
ch.plots[0].has_data_labels = True
dl = ch.plots[0].data_labels
dl.font.size = Pt(13)
dl.font.bold = True
dl.font.color.rgb = WHITE
dl.number_format = '0;;'
dl.number_format_is_linked = False
ch.category_axis.reverse_order = True
ch.category_axis.tick_labels.font.size = Pt(12.5)
ch.category_axis.format.line.color.rgb = LINE
ch.value_axis.visible = False
ch.value_axis.has_major_gridlines = False
text(s, 0.6, 1.85, 6.5, 0.4, "OpenCV, 14 questions : où atterrit la bonne réponse ?", size=15, bold=True,
     color=NAVY)
text(s, 7.4, 1.85, 5.3, 0.4, "Ce que cela a changé dans la conception", size=17, bold=True, color=TEAL)
text(s, 7.4, 2.3, 5.3, 3.8, [
    {"t": "**Découpage par fonction et classe**, pas par blocs de 50 lignes : un embedding n'a de sens que "
          "sur une idée entière.", "b": True},
    {"t": "**Deux recherches** (sens + mots exacts) fusionnées, puis un **reranker** qui garde 5 sur 20.",
     "b": True},
    {"t": "Fichiers modifiés non commités **relus en direct** et placés devant l'index.", "b": True},
    {"t": "Premières indexations : des fichiers échouaient, plus grands que le contexte de l'embedder.",
     "b": True},
    {"t": "Constat honnête : « où est déclaré X ? » marche ; **décrire un comportement** ou coller un chemin, "
          "non.", "b": True, "color": AMBER},
], size=13.5, after=7)
takeaway(s, "C'est le premier chantier ouvert de la roadmap : la qualité de la recherche sur un grand arbre.")

# =====================================================================
# 9. Appris 3 : faits vs jugement (MCP)
# =====================================================================
s = frame(prs, "2 · Ce que nous avons appris · 3/7",
          "Donner des faits à l'éditeur, pas un second rédacteur : séparer les faits du jugement", nxt(),
          notes="Sources : docs/notes/verification/roadmap.md §8.4 et §8.6, docs/guides/vscode.md, docs/notes/contracts/mcp.md. "
                "Campagne de 16 questions dans un seul message Copilot : 6/16. Les 16 réponses portaient "
                "« Sources: » : l'éditeur n'appelait que `ask`.")
table(s, 0.6, 1.9, [1.5, 4.0, 3.2], [
    ["Outil MCP", "Ce qu'il renvoie", "Ce qu'il ne fait pas"],
    ["`lookup`", "chemin, lignes, symbole, court extrait de l'index", "aucun modèle de chat"],
    ["`check`", "journal borné de `build`, `test` ou `compile_file`", "aucun avis sur le résultat"],
    ["`calls` · `types`", "appelants, hiérarchie de types (clangd)", "pas de phrase sur l'héritage"],
    ["`ask`", "400 tokens, cite `path:ligne` ou dit que le code ne le montre pas",
     "ne cherche pas, ne compile pas, aucun appel d'outil"],
], size=12.5, row_h=0.62, header_h=0.4)
box(s, 9.7, 1.9, 3.03, 3.2, fill=AMBER_L)
text(s, 9.8, 1.95, 2.85, 3.1, [
    {"t": "6 / 16", "size": 34, "bold": True, "color": AMBER},
    {"t": "bonnes réponses sur la campagne dans Copilot : les 16 citaient « Sources: », l'éditeur "
          "n'appelait que `ask`, et le 14B y écrivait du JSON d'outil dans le texte.", "size": 12.5},
], after=4)
text(s, 0.6, 5.2, 12, 0.35, "Correctifs", size=15, bold=True, color=TEAL)
text(s, 0.6, 5.55, 12.1, 0.7, [
    {"t": "`ask` sans schéma d'outils · chemins cités lus directement · `CMakePresets.json` et `.vscode/` écartés "
          "du top 5 sauf s'ils sont nommés · **un seul rédacteur** : l'éditeur écrit, ou le modèle local, "
          "jamais l'un après l'autre.", "size": 13.5},
], after=0)
takeaway(s, "Règle apprise : l'outil dit **ce qu'il voit**, le rédacteur dit **ce que cela veut dire**.", y=6.3, h=0.55)

# =====================================================================
# 10. Appris 4 : sécurité
# =====================================================================
s = frame(prs, "2 · Ce que nous avons appris · 4/7",
          "La sécurité par construction fonctionne, et nous en connaissons les limites", nxt(),
          notes="Sources : docs/architecture/security.md, SECURITY.md, docs/notes/verification/role-llm-local-harness-"
                "multi-machines.md (écoute 0.0.0.0 sur l'hôte EVO), conversation du 11 oct. (revue du code : "
                "build/test exécutent le CMakeLists du dépôt ; passe de relecture désactivée par défaut), "
                "docs/notes/verification/roadmap.md §11.")
table(s, 0.6, 1.9, [2.2, 5.0, 4.93], [
    ["Risque", "Parade dans le POC", "Limite connue"],
    ["Code qui sort", "3 réseaux internes, aucune route sortante ; un seul job a Internet (poids)",
     "Sur l'EVO-X5, le serveur d'inférence écoute sur `0.0.0.0` de l'hôte, hors conteneur"],
    ["Commande arbitraire", "39 actions nommées, arguments validés deux fois, sorties plafonnées",
     "`build` et `test` exécutent le `CMakeLists.txt` et les tests du dépôt : contenus par l'isolation seule"],
    ["Écriture non voulue", "Un seul écrivain, jamais de commit ; écriture → `build_step` avant réponse",
     "Serena ne suit pas encore la copie choisie (`workspace`) : fixée à `CODEBASE_PATH`"],
    ["Réponse inventée", "Prose refusée tant que le cycle est ouvert ; `ask` cite ou avoue l'absence",
     "Le 14B peut encore inventer une ligne absente du prompt"],
], size=13, row_h=0.86, header_h=0.4)
takeaway(s, "Interdire le shell a un coût (il faut une action nommée par geste) et un bénéfice réel. "
            "Reste à décider s'il est **exigé** ou seulement **préféré**.", fill=AMBER, y=6.18)

# =====================================================================
# 11. Appris 5 : réutiliser
# =====================================================================
s = frame(prs, "2 · Ce que nous avons appris · 5/7",
          "Réutiliser plutôt que réécrire, et vérifier que « maintenu ailleurs » est vrai", nxt(),
          notes="Sources : docs/notes/verification/roadmap.md §0 « Principle: reuse before rewriting » et §6, docs/adr/0001-serena-"
                "sidecar.md, conversation du 6 nov. (survey des licences et de la maintenance). "
                "Serena v2 = GPL-3.0 : acceptable car produit interne jamais distribué, lancé en processus séparé.")
text(s, 0.6, 1.85, 5.9, 0.4, "Ce que nous avons remplacé par du maintenu", size=17, bold=True, color=TEAL)
text(s, 0.6, 2.3, 5.9, 3.7, [
    {"t": "**SDK** `openai` à la place d'appels HTTP faits main ; **pydantic** pour générer les schémas d'outils ; "
          "**structlog** ; `hf_hub_download` ; **Catch2** pour les tests C++.", "b": True},
    {"t": "**Serena en sidecar** (ADR-0001) à la place du clangd maison : retrait de `lsp_definition`, "
          "`lsp_references`, `lsp_hover`, `lsp_symbols`, `lsp_outline`, `list_files`, `search` et du writer `patch`.",
     "b": True},
    {"t": "Bénéfice cherché : monter de version **sans toucher à la boucle** (tag d'image), plutôt que "
          "redevenir mainteneur.", "b": True},
], size=13.5, after=8)
text(s, 6.9, 1.85, 5.8, 0.4, "« Maintenu ailleurs » : à vérifier chaque fois", size=17, bold=True, color=AMBER)
table(s, 6.9, 2.3, [1.75, 4.08], [
    ["Candidat", "Ce que l'audit a trouvé"],
    ["Claude Context", "cloud par défaut (OpenAI + Zilliz) : écarté"],
    ["svn-mcp", "1 étoile, dernière release janv. 2026"],
    ["Continue", "dépôt « plus maintenu »"],
    ["mcp-server-git", "sait **committer** : pire que le binaire `git`"],
    ["Serena", "GPL-3.0 : acceptable en interne, en processus séparé"],
], size=12, row_h=0.56, header_h=0.38, header_fill=AMBER)
takeaway(s, "Un sidecar ne dispense pas d'évaluer sa maintenance, sa licence et ses réglages par défaut.")

# =====================================================================
# 12. Appris 6 : mesurer
# =====================================================================
s = frame(prs, "2 · Ce que nous avons appris · 6/7",
          "Mesurer : nos évaluations comparent Pumba à lui-même, jamais à une alternative", nxt(),
          notes="Sources : docs/notes/verification/eval-qwen-mistral-deploy.md (section « What the evaluation already "
                "measures »), docs/guides/testing.md, docs/notes/verification/functional-checks.md, "
                ".scratch/harness-selection/protocole.md §3.")
table(s, 0.6, 1.9, [2.7, 4.6, 4.83], [
    ["Mesure existante", "Ce qu'elle prouve", "Ce qu'elle ne prouve pas"],
    ["`deploy.py eval`", "où atterrit le fichier attendu (top 5, préfetch, absent)", "rien sur le modèle de chat"],
    ["`eval_rag.py`", "fichier et symbole attendus dans les sources de `/chat`", "ni durée, ni nombre de tokens"],
    ["`eval_tools.py` (3 scénarios)", "le premier outil appelé et ses arguments",
     "le résultat final ; une exploration raisonnable (`list_dir`) compte comme un échec"],
    ["`functional_check` (25 vérifs)", "la pile répond de bout en bout sur `sample/`", "la qualité sur un vrai dépôt"],
    ["`profile_campaign` (S0 à S5)", "durée et réussite par profil de machine", "pas de juge de texte libre, pas de TTFT"],
], size=13, row_h=0.66, header_h=0.4)
takeaway(s, "Pour décider « garder ou remplacer », il manque une **mesure commune** : mêmes tâches, mêmes modèles, "
            "plusieurs harness.", y=6.2)

# =====================================================================
# 13. Appris 7 : travailler avec des agents
# =====================================================================
s = frame(prs, "2 · Ce que nous avons appris · 7/7",
          "Travailler avec des agents : ce qui a marché, et le coût caché", nxt(),
          notes="Sources : conversations du 6 au 12 nov. (grilling, spec Serena, 11 tickets, étude harness, "
                "découverte de l'implémentation Serena déjà dans l'arbre de travail : 160 chemins modifiés non "
                "commités), docs/notes/verification/roadmap.md §11, notes de recherche datées « Last verified ».")
card(s, 0.6, 1.95, 5.95, 4.05, "Ce qui a marché", [
    {"t": "**Grill avant rapport** : un arbre de décisions fermé avant d'écrire quoi que ce soit.", "b": True},
    {"t": "**Spec → tickets → implémentation** en parallèle (11 tickets pour Serena).", "b": True},
    {"t": "**Revues de conception itérées** avant de coder.", "b": True},
    {"t": "**Recherches avec sources primaires** et la mention « non vérifié » plutôt qu'une supposition ; "
          "notes datées.", "b": True},
    {"t": "Audits de documentation par **5 agents en parallèle**.", "b": True},
], size=14.5, fill=GREEN_L, accent=GREEN)
card(s, 6.78, 1.95, 5.95, 4.05, "Ce qui a coincé", [
    {"t": "**État non partagé** : la spec Serena était suspendue, une autre session l'avait déjà "
          "implémentée (160 chemins non commités).", "b": True},
    {"t": "**Documents qui se contredisent** (Serena d'un côté, Filesystem MCP de l'autre).", "b": True},
    {"t": "**Documentation qui décrit un état dépassé** : « le code et les tests font foi ».", "b": True},
    {"t": "Un dépôt devenu **très grand** : cohérence code, docs, tickets et décisions à maintenir.", "b": True},
], size=14.5, fill=RED_L, accent=RED)
takeaway(s, "Les agents accélèrent la production ; le goulot devient la **cohérence** et les **décisions écrites** "
            "(ADR, glossaire, statuts).", y=6.2)

# =====================================================================
# 14. Existe-t-il mieux ? méthode
# =====================================================================
s = frame(prs, "3 · Existe-t-il déjà mieux ?",
          "L'hypothèse à tester : un harness maintenu ailleurs, branché sur un modèle local, pourrait suffire",
          nxt(),
          notes="Sources : conversation du 6 nov. (« Existe-t-il déjà des API qui font mieux ? ») puis du 12 nov. "
                "(hypothèse de l'utilisateur), .scratch/harness-selection/criteria.md et notes A, B, C. "
                "Environ 30 candidats : 10 terminal, 10 éditeur/web, 9 cadres, plus Refact et Pochi cités en synthèse.")
text(s, 0.6, 1.85, 5.2, 0.4, "Méthode", size=17, bold=True, color=TEAL)
text(s, 0.6, 2.3, 5.2, 3.8, [
    {"t": "6 nov. : audit du code maison → **Serena** en sidecar pour la mécanique (LSP, fichiers).", "b": True},
    {"t": "12 nov. : « la spécialisation n'est peut-être pas nécessaire » → **vraie étude de l'état de l'art**, "
          "pas deux candidats choisis au hasard.", "b": True},
    {"t": "**3 agents de recherche** en parallèle, une grille commune de 17 critères.", "b": True},
    {"t": "Sources primaires uniquement ; « non vérifié » plutôt qu'une supposition ; **rien exécuté** à ce stade.",
     "b": True},
], size=13.5, after=8)
text(s, 0.6, 5.0, 5.2, 0.35, "Quatre portes éliminatoires", size=15, bold=True, color=NAVY)
text(s, 0.6, 5.35, 5.3, 0.9, [
    {"t": "**G1** endpoint OpenAI-compatible local · **G2** sans compte ni réseau obligatoire, télémétrie "
          "désactivable · **G3** licence libre · **G4** maintenu depuis moins de 6 mois", "size": 12.5}], after=0)
fam = [("Terminal · 10", "OpenCode, Aider, Goose, Crush, Plandex, Qwen Code, Codex CLI, Gemini CLI, Pi, "
                         "mini-swe-agent"),
       ("Éditeur et web · 10+", "Cline, Roo Code, Kilo Code, Continue, llama.vscode, Zed, Void, Tabby, "
                                "OpenHands, Open WebUI, Refact, Pochi"),
       ("Cadres pour construire · 9", "**OpenHarness (HKUDS)**, pydantic-ai, smolagents, LangGraph, "
                                      "Strands, Agno, Mastra, ADK, OpenAI Agents SDK")]
for i, (t_, b_) in enumerate(fam):
    y = 1.95 + i * 1.4
    box(s, 6.2, y, 6.53, 1.25, fill=GRAY_L)
    box(s, 6.2, y + 0.08, 0.07, 1.09, fill=TEAL, radius=0)
    text(s, 6.4, y + 0.05, 6.2, 0.4, t_, size=15, bold=True, color=TEAL)
    text(s, 6.4, y + 0.45, 6.2, 0.8, b_, size=12.5)
takeaway(s, "Pourquoi tant de candidats : le choix ne doit pas dépendre de ce que l'on connaissait déjà.", y=6.25, h=0.55)

# =====================================================================
# 15. Portes
# =====================================================================
s = frame(prs, "3 · Existe-t-il déjà mieux ?", "Après les quatre portes : huit sortent, une quinzaine passe avec des réglages",
          nxt(),
          notes="Source : .scratch/harness-selection/synthese.md §1 et §6. Crush : FSL-1.1-MIT, accepté par "
                "l'utilisateur le 12 nov. (usage interne en entreprise).")
text(s, 0.6, 1.85, 4.2, 0.4, "Éliminés", size=17, bold=True, color=RED)
table(s, 0.6, 2.3, [1.55, 2.75], [
    ["Candidat", "Motif"],
    ["Gemini CLI", "G1 : pas de fournisseur OpenAI-compatible"],
    ["Plandex", "G4 : dernier commit 2025-10-03"],
    ["Roo Code", "arrêté le 15 mai 2026"],
    ["Void", "déprécié"],
    ["Continue", "« no longer actively maintained »"],
    ["Tabby", "interface web sous licence Enterprise"],
    ["SWE-agent v1", "remplacé par mini-swe-agent"],
    ["Open WebUI", "licence à clause de marque ; MCP sans stdio"],
], size=11, row_h=0.41, header_h=0.36, header_fill=RED)
text(s, 5.1, 1.85, 3.6, 0.4, "Passent, avec réglages", size=17, bold=True, color=GREEN)
text(s, 5.1, 2.3, 3.55, 3.7, [
    {"t": "OpenCode · Aider · Goose · Qwen Code · Pi", "b": True},
    {"t": "Cline · Kilo Code · Zoo Code · Refact · Zed · Pochi · llama.vscode", "b": True},
    {"t": "OpenHands (sandbox Docker)", "b": True},
    {"t": "Codex CLI (API Responses seulement)", "b": True},
    {"t": "Cadres Python : pydantic-ai, smolagents, LangGraph, Strands, Agno, Mastra, ADK, OpenHarness…", "b": True},
    {"t": "**Crush** : FSL-1.1-MIT, accepté pour un usage interne", "b": True, "color": AMBER},
], size=12.5, after=6)
text(s, 8.95, 1.85, 3.8, 0.4, "Risques par défaut", size=17, bold=True, color=AMBER)
text(s, 8.95, 2.3, 3.78, 3.8, [
    {"t": "**OpenCode** : tout autorisé, shell hors projet compris", "b": True},
    {"t": "**Goose** : mode autonome par défaut", "b": True},
    {"t": "**Pochi** : tout autorisé", "b": True},
    {"t": "**Pi** : ni approbation ni sandbox", "b": True},
    {"t": "**Qwen Code** : statistiques d'usage vers Alibaba", "b": True},
    {"t": "**Cline** : télémétrie ; le mode CLI one-shot est « yolo »", "b": True},
    {"t": "**Kilo Code** : télémétrie PostHog", "b": True},
], size=12.5, after=6)
takeaway(s, "« Passe » ne veut pas dire « convient » : aucun de ces projets n'a été exécuté sur notre pile.", y=6.25, h=0.55)

# =====================================================================
# 16. OpenHarness / ohmo
# =====================================================================
s = frame(prs, "3 · Existe-t-il déjà mieux ?",
          "OpenHarness (HKUDS) et son agent « ohmo » : un cadre pour construire, pas un remplaçant de Pumba",
          nxt(),
          notes="Sources : .scratch/harness-selection/notes/C-cadres-et-toolcalling.md §1.1 (code lu : "
                "openai_client.py L228-236 et L405-420, engine/query.py, permissions/modes.py, hooks/schemas.py, "
                "mcp/client.py). Les faits sur « ohmo » (agent personnel : branches, tests, PR) viennent du "
                "README, lu lors de la conversation du 6 nov. ; le code de ohmo n'a pas été relu. "
                "Homonymes : autonomous-ai/openharness, AgentBoardTT/openharness, fahd09/openharness.")
card(s, 0.6, 1.85, 3.95, 3.65, "Ce que c'est", [
    {"t": "Framework d'agent **Python** (CLI `oh`, TUI), **MIT**", "b": True},
    {"t": "43 outils, skills, plugins, mémoire, multi-agents", "b": True},
    {"t": "**ohmo** : agent personnel bâti dessus ; crée des branches, lance des tests, ouvre des PR (README)",
     "b": True},
    {"t": "v0.1.9 (7 mai 2026) · dernier commit 4 juin : **~5 mois sans activité**", "b": True},
], size=12.5, title_size=16)
card(s, 4.69, 1.85, 3.95, 3.65, "Ce qui est compatible", [
    {"t": "Endpoint OpenAI-compatible (`oh provider add --base-url`)", "b": True},
    {"t": "Client MCP : stdio et HTTP", "b": True},
    {"t": "Validation Pydantic des entrées ; modes `default` / `auto` / `plan`", "b": True},
    {"t": "Hooks `PreToolUse` : un hook peut bloquer un outil → piste pour imposer écriture → `build_step` "
          "(déduit du code, **non testé**)", "b": True},
], size=12.5, title_size=16, fill=GREEN_L, accent=GREEN)
card(s, 8.78, 1.85, 3.95, 3.65, "Ce qui bloque ou reste inconnu", [
    {"t": "JSON d'arguments illisible **remplacé en silence par `{}`** : grave avec un petit modèle", "b": True},
    {"t": "Outil `Bash` présent ; commits et PR dans ohmo : contraire à notre règle", "b": True},
    {"t": "Non vérifié : Subversion, clangd, annulation de tour, Serena, tenue avec `llama-server`", "b": True},
], size=12.5, title_size=16, fill=RED_L, accent=RED)
text(s, 0.6, 5.6, 12.1, 0.5, "**Attention aux homonymes** : `autonomous-ai/openharness` (lance Claude Code ou Codex dans des "
                             "panneaux tmux), `AgentBoardTT/openharness` (CLI avec Bash), `fahd09/openharness` "
                             "(assistant TypeScript, 19 outils).", size=12, color=MUTED)
takeaway(s, "Verdict d'étude : au mieux un candidat pour remplacer **seulement `tool_loop`**. Ni la sandbox, ni "
            "l'index, ni Subversion.", fill=AMBER, y=6.25, h=0.58, size=14)

# =====================================================================
# 17. Matrice d'écarts
# =====================================================================
s = frame(prs, "3 · Existe-t-il déjà mieux ?", "Ce que les harness couvrent, et ce qu'aucun ne couvre", nxt(),
          notes="Sources : .scratch/harness-selection/synthese.md §3 à §5 et protocole.md. Aider : 69,2 % de "
                "réussite et 100 % de réponses bien formées en format `whole` sur Qwen2.5-Coder-14B "
                "(benchmark d'édition, 2024) ; il n'utilise pas le tool-calling. Refact et Kilo Code : "
                "documentaire, non testés.")
G, A_, R_ = GREEN_L, AMBER_L, RED_L
table(s, 0.6, 1.85, [2.65, 2.9, 4.25, 2.33], [
    ["Besoin", "Pumba aujourd'hui", "Ce que couvrent les candidats", "Verdict"],
    ["Subversion (lecture)", "wrappers `git` / `svn` en argv", "aucun des candidats ne la mentionne", "à écrire ou garder"],
    ["Interdire commit et push", "structurel : jamais",
     "Aider commit par défaut ; ailleurs le shell permet `git commit`", "config + test T8"],
    ["Isolation, sandbox", "conteneurs, réseaux internes",
     "OpenHands (Docker, mais avec un shell) ; les autres : approbations seulement", "écart réel"],
    ["Phase écriture → `build_step`", "règle dans la boucle", "aucun ; hooks possibles (OpenHarness)", "à construire"],
    ["Intelligence C++ (clangd)", "Serena en sidecar", "intégrée : Crush, Kilo Code ; OpenCode V1 oui, V2 non",
     "comblé des deux côtés"],
    ["Index sémantique C++ local", "Qdrant + tree-sitter + reranker", "Refact, Kilo Code (non testés) ; Aider : repo map",
     "à tester"],
    ["Tool-calling, petit modèle", "relances, appels déguisés relus", "Aider contourne par des formats d'édition en texte",
     "différenciateur possible"],
], size=11.5, row_h=0.5, header_h=0.38,
      fills={(1, 3): R_, (2, 3): A_, (3, 3): R_, (4, 3): R_, (5, 3): G, (6, 3): A_, (7, 3): A_})
takeaway(s, "Ce que Pumba fait et que personne ne livre : Subversion, sandbox sans shell, phase d'écriture vérifiée.",
         y=6.25, h=0.55)

# =====================================================================
# 18. Le modèle change la question
# =====================================================================
s = frame(prs, "3 · Existe-t-il déjà mieux ?",
          "Le fait qui change la question : comparer des harness sur le seul 14B mesure autant le modèle que le harness",
          nxt(),
          notes="Sources : .scratch/harness-selection/protocole.md §2b (RTX 4080 16 Go, llama.cpp b10216, contexte "
                "32 768, KV q8_0 ; Pumba avec Serena branché ; checkout ./sample ; score = premier outil appelé ; "
                "5 passes de 3 scénarios). Durées non comparables : outils exécutés différents.")
cd = CategoryChartData()
cd.categories = ["does_it_build", "compile_one_file", "run_one_test"]
cd.add_series("Qwen2.5-Coder-14B Q5_K_M", (5, 5, 5))
cd.add_series("gpt-oss-20b MXFP4", (5, 2, 3))
gf = s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(0.6), Inches(2.3), Inches(6.2), Inches(3.7), cd)
ch = gf.chart
style_chart(ch, 12)
color_series(ch.plots[0].series[0], TEAL)
color_series(ch.plots[0].series[1], AMBER)
ch.plots[0].gap_width = 70
ch.plots[0].overlap = -10
ch.plots[0].has_data_labels = True
ch.plots[0].data_labels.font.size = Pt(13)
ch.plots[0].data_labels.font.bold = True
ch.plots[0].data_labels.number_format = '0"/5"'
ch.plots[0].data_labels.number_format_is_linked = False
ch.plots[0].data_labels.position = XL_LABEL_POSITION.OUTSIDE_END
ch.value_axis.maximum_scale = 6
ch.value_axis.minimum_scale = 0
ch.value_axis.visible = False
ch.value_axis.has_major_gridlines = False
ch.category_axis.tick_labels.font.size = Pt(12)
ch.category_axis.format.line.color.rgb = LINE
text(s, 0.6, 1.9, 6.2, 0.4, "Pumba seul, 5 passes : le bon premier outil, par scénario", size=15, bold=True, color=NAVY)
text(s, 7.1, 1.9, 5.6, 0.4, "Total : 14B **15/15** · gpt-oss-20b **10/15**", size=15, color=NAVY, bold=True)
text(s, 7.1, 2.35, 5.6, 2.3, [
    {"t": "gpt-oss-20b : **appel natif correct** sur llama.cpp b10216, 172 tokens/s, 12,96 Go de VRAM. "
          "Le format Harmony passe sans monter de version.", "b": True},
    {"t": "Trois fois sur cinq il commence par `list_dir` : une exploration raisonnable, punie par le score strict.",
     "b": True},
    {"t": "Deux fois il n'appelle **aucun outil** : cause inconnue.", "b": True},
], size=13, after=6)
box(s, 7.1, 4.55, 5.63, 1.45, fill=AMBER_L)
text(s, 7.2, 4.6, 5.45, 1.4, [
    {"t": "À ne pas conclure", "bold": True, "color": AMBER, "size": 13.5},
    {"t": "La boucle est réglée pour le 14B ; 3 scénarios, premier outil seulement ; checkout `sample/`. "
          "Cela ne dit pas que gpt-oss est moins bon en général.", "size": 12.5},
], after=3)
takeaway(s, "Il faut tester **chaque harness avec au moins deux modèles**, sinon on mélange deux variables.", y=6.25, h=0.55)

# =====================================================================
# 19. Protocole de test
# =====================================================================
s = frame(prs, "3 · Existe-t-il déjà mieux ?", "Le test prévu pour trancher : un protocole rédigé, pas encore lancé", nxt(),
          notes="Source : .scratch/harness-selection/protocole.md §1 à §6. Seule la ligne « Pumba seul, deux "
                "modèles » est exécutée (§2b). Reste : wrapper svn_* en lecture seule, script de rejeu, puis "
                "les harness un par un.")
text(s, 0.6, 1.85, 3.9, 0.4, "Candidats et modèles", size=16, bold=True, color=TEAL)
text(s, 0.6, 2.25, 3.9, 3.9, [
    {"t": "**Référence** : Pumba tel quel", "b": True},
    {"t": "OpenCode V2 et Goose, avec Serena", "b": True},
    {"t": "Kilo Code, Crush (clangd intégré)", "b": True},
    {"t": "Aider (sans tool-calling)", "b": True},
    {"t": "`pydantic-ai` autour de la politique de Pumba (annexe)", "b": True},
    {"t": "Modèles : **A** Qwen2.5-Coder-14B · **B** gpt-oss-20b · **B bis** Devstral-Small-2-24B (juste sur 16 Go)",
     "b": True, "after": 6},
    {"t": "Tous avec des wrappers `svn_*` en lecture seule.", "color": MUTED, "size": 12},
], size=12.5, after=5)
table(s, 4.7, 1.9, [0.6, 3.5], [
    ["Id", "Tâche (3 essais par couple)"],
    ["T1–3", "les 3 scénarios d'outils existants"],
    ["T4", "question de recherche, réponse sourcée"],
    ["T5", "« liste les appelants de X »"],
    ["T6", "petite modif + compilation du fichier"],
    ["T7", "`svn log` sur une copie SVN"],
    ["T8", "demande de `git commit` ou de suppression hors projet"],
], size=11.5, row_h=0.5, header_h=0.36)
box(s, 9.0, 1.9, 3.73, 4.2, fill=NAVY)
text(s, 9.15, 1.97, 3.45, 4.1, [
    {"t": "Un harness remplace l'agent maison si…", "bold": True, "size": 14, "color": WHITE, "after": 8},
    {"t": "T1–T5 et T7 réussissent **au moins aussi souvent** que Pumba", "b": True, "color": WHITE, "size": 12.5},
    {"t": "T8 : **aucune action hors politique**, avec une configuration de permissions documentée", "b": True,
     "color": WHITE, "size": 12.5},
    {"t": "Les écarts restants (écriture → `build_step`, annulation) se comblent par **configuration ou hook, "
          "pas par un fork**", "b": True, "color": WHITE, "size": 12.5, "after": 8},
    {"t": "Sinon : on garde l'agent maison.", "bold": True, "color": RGBColor(0x5E, 0xEA, 0xD4), "size": 13},
], after=6)
takeaway(s, "Statut : seule la ligne « Pumba seul, deux modèles » est exécutée. Les harness n'ont pas encore tourné.",
         fill=AMBER, y=6.25, h=0.55)

# =====================================================================
# 20. Reste à faire
# =====================================================================
s = frame(prs, "4 · Reste à faire", "Ce qui reste à faire : qualité, exploitation, usage à plusieurs", nxt(),
          notes="Sources : docs/notes/verification/roadmap.md §1, §5, §7, §11 (durées indicatives : qualité de recherche 10 j, copie "
                "suivie par Serena 5 j, digests et snapshot 4 j, une personne), docs/notes/verification/"
                "clients-paralleles-manquants.md, indexation-gros-depot.md, indexeur-au-commit.md, "
                "settings-comprehensibilite.md, .scratch/serena-sidecar/issues/08.")
cols = [
    ("Qualité", TEAL, TEAL_L, [
        "**Recherche sur grand arbre** : 6/14 sur OpenCV (gain élevé, ~10 j)",
        "**Serena suit la copie choisie** : sidecar fixé à `CODEBASE_PATH`, pas de `/cancel` vers Serena (~5 j)",
        "**Ticket 08** : essai réel sur Git **et** SVN (`ready-for-human`)",
        "Tenue du protocole MCP sur 16 questions (§8.6)",
    ]),
    ("Exploitation", AMBER, AMBER_L, [
        "Digests d'images, dépendances avec hash, snapshot Qdrant (~4 j)",
        "**Indexation lente** : aucune mesure de durée ; embeddings fichier par fichier, sans parallélisme",
        "**Indexation au commit** : aucun hook ; `--watch` peu fiable via WSL",
        "**Réglages `.env`** : des clés qui semblent tout piloter et ne le font pas",
    ]),
    ("Usage à plusieurs", NAVY, GRAY_L, [
        "**Un seul slot de génération** alors que l'agent admet 2 `/chat`",
        "**Un seul jeton**, ni compte, ni quota ; historique sans propriétaire",
        "Accès hors loopback : tunnel SSH ou relais `expose` par adresse IPv4",
        "Copies de travail partagées par chemin, pas par utilisateur",
    ]),
]
for i, (t_, acc, fill, items) in enumerate(cols):
    x = 0.6 + i * 4.1
    card(s, x, 1.9, 3.95, 4.25, t_, [{"t": it, "b": True, "after": 10} for it in items], fill=fill, accent=acc,
         size=13.5, title_size=17)
takeaway(s, "Recherche, exploitation et multi-utilisateurs survivent à un changement de harness ; la boucle et Serena en dépendent.", y=6.25, h=0.55)

# =====================================================================
# 21. Capacité
# =====================================================================
s = frame(prs, "4 · Reste à faire", "Dimensionnement : 20 développeurs sur l'EVO-X5 dépassent le confort avec les deux modèles étudiés",
          nxt(),
          notes="Source : docs/notes/verification/evo-x5-modele-20-utilisateurs.md (calcul du 7 nov., non mesuré). "
                "Qwen3-Coder-30B-A3B Q4_K_M, 20 slots : ~6,8 tok/s par utilisateur ; seuil de 10 tok/s tenu pour "
                "13 slots (~10,5 tok/s). Mistral Small 4 UD-Q5_K_M : ~2,8 tok/s pour 20 slots ; seuil tenu pour "
                "5 slots. Machine : 192 Go de mémoire unifiée ; limite = bande passante mémoire, pas la capacité.")
cd = CategoryChartData()
cd.categories = ["Mistral Small 4\n(profil actuel)", "Qwen3-Coder-30B-A3B\n(recommandation de la note)", "Objectif"]
cd.add_series("Utilisateurs", (5, 13, 20))
gf = s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(0.6), Inches(2.3), Inches(6.3), Inches(3.75), cd)
ch = gf.chart
style_chart(ch, 12, legend=False)
ser = ch.plots[0].series[0]
color_series(ser, TEAL)
for idx, col in enumerate((AMBER, TEAL, RGBColor(0x9C, 0xA3, 0xAF))):
    pt = ser.points[idx]
    pt.format.fill.solid()
    pt.format.fill.fore_color.rgb = col
ch.plots[0].gap_width = 60
ch.plots[0].has_data_labels = True
ch.plots[0].data_labels.font.size = Pt(15)
ch.plots[0].data_labels.font.bold = True
ch.plots[0].data_labels.position = XL_LABEL_POSITION.OUTSIDE_END
ch.value_axis.visible = False
ch.value_axis.has_major_gridlines = False
ch.value_axis.maximum_scale = 24
ch.value_axis.minimum_scale = 0
ch.category_axis.tick_labels.font.size = Pt(12)
ch.category_axis.format.line.color.rgb = LINE
text(s, 0.6, 1.9, 6.3, 0.4, "Utilisateurs simultanés qui gardent ≥ 10 tokens/s chacun", size=15, bold=True, color=NAVY)
text(s, 7.2, 1.9, 5.5, 0.4, "Ce que cela implique", size=17, bold=True, color=TEAL)
text(s, 7.2, 2.35, 5.5, 3.8, [
    {"t": "À 20 utilisateurs : **~6,8 tok/s** chacun avec Qwen3-Coder-30B-A3B, **~2,8** avec Mistral Small 4.",
     "b": True},
    {"t": "La mémoire (192 Go) ne limite pas ; la **bande passante** oui.", "b": True},
    {"t": "Un utilisateur qui fait des tâches complexes consomme bien plus qu'un autre : le seuil est un plancher.",
     "b": True},
    {"t": "Qwen3.8-Flash-Next demande un `llama-server` plus récent que `b10216` (architecture `qwen4_exp`).",
     "b": True},
    {"t": "Chiffres **calculés** (note du 7 nov.), pas mesurés sur la machine.", "color": AMBER, "bold": True,
     "after": 0},
], size=13, after=7)
takeaway(s, "Avant de choisir un modèle pour tous, fixer le nombre d'utilisateurs visé et le débit jugé confortable.", y=6.25, h=0.55)

# =====================================================================
# 22. Décisions 1/2
# =====================================================================
s = frame(prs, "4 · Décisions", "Trois décisions structurantes, avec ce qui les éclairerait", nxt(),
          notes="Les « pistes proposées » sont des propositions à discuter, pas des conclusions des notes de "
                "recherche (qui s'interdisent toute recommandation). Elles s'appuient sur .scratch/harness-selection/"
                "protocole.md et docs/notes/verification/*.")
decs = [
    ("D1 · Que fait-on du harness ?", TEAL, [
        "**A** Garder Pumba + Serena (état actuel)",
        "**B** Harness maintenu + Serena + wrappers `svn_*`",
        "**C** Hybride : cadre maintenu pour `tool_loop`, politique conservée",
    ], "Le protocole T1–T8 sur au moins deux harness et deux modèles.",
     "Ne pas investir davantage dans `tool_loop` avant la mesure."),
    ("D2 · Quel modèle cible ?", AMBER, [
        "**16 Go** : Qwen2.5-Coder-14B (actuel) · gpt-oss-20b · Devstral-24B (juste)",
        "**24 Go et plus** : Qwen3.8-27B, Qwen3-Coder-30B-A3B",
        "**EVO-X5** : Mistral Small 4 · Qwen3-Coder-30B-A3B · Qwen3.8-Flash-Next (llama.cpp à monter)",
    ], "Comparaison à harness constant ; le nombre d'utilisateurs visé (D4).",
     "Figer un modèle B de référence pour l'étude, sans changer le défaut."),
    ("D3 · Que laisse-t-on faire au modèle ?", NAVY, [
        "**Actions nommées seulement** (actuel)",
        "**Shell avec approbation** par commande",
        "**Shell dans une sandbox** (type OpenHands)",
    ], "Test T8 : aucune action hors politique, quel que soit le choix.",
     "Exiger une configuration de permissions documentée et testée, même avec shell."),
]
for i, (t_, acc, opts, light, prop) in enumerate(decs):
    x = 0.6 + i * 4.1
    box(s, x, 1.85, 3.95, 4.9, fill=GRAY_L)
    box(s, x, 1.85, 3.95, 0.55, fill=acc, radius=0)
    text(s, x + 0.1, 1.85, 3.8, 0.55, t_, size=14.5, bold=True, color=WHITE, anchor=MSO_ANCHOR.MIDDLE, after=0)
    text(s, x + 0.12, 2.5, 3.7, 0.3, "OPTIONS", size=10.5, bold=True, color=MUTED)
    text(s, x + 0.12, 2.78, 3.7, 1.85, [{"t": o, "b": True, "after": 3} for o in opts], size=12)
    text(s, x + 0.12, 4.62, 3.7, 0.3, "CE QUI ÉCLAIRERAIT", size=10.5, bold=True, color=MUTED)
    text(s, x + 0.12, 4.88, 3.7, 0.8, light, size=12)
    text(s, x + 0.12, 5.68, 3.7, 0.3, "PISTE PROPOSÉE", size=10.5, bold=True, color=acc)
    text(s, x + 0.12, 5.94, 3.7, 0.8, prop, size=12, bold=True, color=acc)

# =====================================================================
# 23. Décisions 2/2
# =====================================================================
s = frame(prs, "4 · Décisions", "Trois autres décisions, et ce qui est déjà tranché", nxt(),
          notes="Sources : .scratch/harness-selection/criteria.md (décisions du 12 nov.), conversation du 6 nov. "
                "(licence GPL en interne, pas de Cursor, `/ui` conservé), docs/guides/fine-tuning-the-codebase.md "
                "(l'adaptateur QLoRA part d'une autre base que le 14B servi), docs/notes/verification/roadmap.md §9 (le service `patch` "
                "tourne encore mais le modèle ne peut plus le nommer).")
decs = [
    ("D4 · Quel périmètre d'usage ?", TEAL, [
        "Un poste par développeur, ou un **serveur partagé** ?",
        "Combien d'utilisateurs, avec quels comptes, quotas et copies de travail ?",
        "Débit jugé confortable (10 tok/s ?)",
    ]),
    ("D5 · Qu'appelle-t-on « assez bon » ?", AMBER, [
        "Définir les tâches réelles et le critère de réussite **avant** de comparer",
        "Scénarios multi-étapes, pas seulement le premier outil",
        "Qui juge, et sur quel dépôt (plus gros que `sample/`)",
    ]),
    ("D6 · Que retire-t-on ?", NAVY, [
        "Le service **`patch`** tourne encore, alors que le modèle ne peut plus le nommer",
        "L'**entraînement QLoRA** produit un adaptateur pour une autre base que le 14B servi",
        "Les documents qui décrivent un état dépassé",
    ]),
]
for i, (t_, acc, items) in enumerate(decs):
    x = 0.6 + i * 4.1
    box(s, x, 1.85, 3.95, 2.95, fill=GRAY_L)
    box(s, x, 1.85, 3.95, 0.55, fill=acc, radius=0)
    text(s, x + 0.1, 1.85, 3.8, 0.55, t_, size=14.5, bold=True, color=WHITE, anchor=MSO_ANCHOR.MIDDLE, after=0)
    text(s, x + 0.12, 2.5, 3.7, 2.3, [{"t": o, "b": True, "after": 6} for o in items], size=12.5)
box(s, 0.6, 5.0, 12.13, 1.7, fill=GREEN_L)
text(s, 0.8, 5.05, 3, 0.4, "Déjà tranché", size=15, bold=True, color=GREEN)
text(s, 0.8, 5.45, 5.7, 1.25, [
    {"t": "Produit **interne, jamais distribué** : GPL (Serena) et FSL (Crush) acceptables", "b": True},
    {"t": "**Subversion est requise** ; un terminal suffit (pas de page web partagée)", "b": True},
    {"t": "« Pas de shell » = préférence, pas exigence", "b": True},
], size=12.5, after=3)
text(s, 6.8, 5.45, 5.8, 1.25, [
    {"t": "Rester **local** : ni Cursor, ni cloud, ni Claude Context", "b": True},
    {"t": "Serena en **sidecar** (ADR-0001)", "b": True},
    {"t": "Chaque harness testé avec **deux modèles**", "b": True},
], size=12.5, after=3)

# =====================================================================
# 24. Séquence
# =====================================================================
s = frame(prs, "4 · Suite", "Proposition de séquence : mesurer d'abord, décider ensuite", nxt(),
          notes="Proposition à discuter. Les durées de la roadmap (qualité de recherche 10 j, copie suivie par "
                "Serena 5 j, digests et snapshot 4 j, une personne) sont celles de docs/notes/verification/roadmap.md §7.")
steps = [
    ("1 · Préparer le test", "Wrapper `svn_*` en lecture seule ; script de rejeu par harness, tâche et modèle ; "
                             "tâches T4–T8 écrites avec leur critère."),
    ("2 · Mesurer", "Pumba en référence, puis au moins deux harness, avec deux modèles. Journaliser réussite, "
                    "tours, appels rejetés, actions hors politique."),
    ("3 · Décider D1–D3", "Réunion sur données : garder, remplacer ou hybrider ; modèle de référence ; politique "
                          "d'exécution."),
    ("4 · Dérouler", "Si Pumba reste : recherche sur grand arbre, Serena et copie suivie. Si harness : porter les "
                     "règles par hooks, garder wrappers et sandbox."),
]
for i, (t_, b_) in enumerate(steps):
    x = 0.6 + i * 3.08
    shp = box(s, x, 2.0, 2.95, 0.7, fill=TEAL if i < 3 else NAVY, shape_type=MSO_SHAPE.PENTAGON, radius=0)
    text(s, x + 0.1, 2.0, 2.6, 0.7, t_, size=14.5, bold=True, color=WHITE, anchor=MSO_ANCHOR.MIDDLE, after=0)
    box(s, x, 2.85, 2.85, 2.4, fill=GRAY_L)
    text(s, x + 0.1, 2.95, 2.65, 2.3, b_, size=14.5)
box(s, 0.6, 5.45, 12.13, 0.7, fill=AMBER_L)
text(s, 0.8, 5.45, 11.8, 0.7, "**En parallèle, quel que soit le résultat** : ticket 08 (essai réel Git et SVN), sécurité "
                              "d'exploitation (digests, hash, snapshot), nombre d'utilisateurs visé (D4).",
     size=13, anchor=MSO_ANCHOR.MIDDLE, after=0)
takeaway(s, "Question pour la séance : acceptons-nous de **geler** les évolutions du harness maison jusqu'à la mesure ?",
         y=6.3, h=0.55, size=14)

# =====================================================================
# 25. Annexe : limites
# =====================================================================
s = frame(prs, "Annexe", "Limites de cette synthèse : ce qui n'est pas établi", nxt(),
          notes="Cette diapo est volontairement explicite : plusieurs conclusions de l'étude des harness sont "
                "documentaires. Les chiffres de modèles sont des auto-déclarations d'éditeurs.")
table(s, 0.6, 1.9, [3.6, 8.53], [
    ["Sujet", "Limite"],
    ["Étude des harness", "Documentaire : README, docs, code lu à un commit précis. **Aucun harness n'a été exécuté** "
                          "sur notre pile."],
    ["Chiffres de modèles", "Auto-déclarés par les éditeurs ; Aider est le seul à publier des mesures sur le 14B "
                            "(édition, 2024), sans tool-calling."],
    ["Mesure Pumba du 12 nov.", "3 scénarios, premier outil seulement, checkout `sample/`, 5 passes ; durées "
                                "non comparables (outils exécutés différents)."],
    ["Recherche (OpenCV)", "14 questions, sans modèle de chat, un seul dépôt."],
    ["Capacité 20 utilisateurs", "Calculée à partir de la bande passante, non mesurée sur la machine."],
    ["ohmo", "Décrit d'après le README ; son code n'a pas été relu."],
    ["Chronologie", "Reconstituée à partir des sessions de travail et de la roadmap ; le dépôt n'est sous git que "
                    "depuis début novembre."],
], size=12, row_h=0.56, header_h=0.38)

# =====================================================================
# 26. Annexe : sources
# =====================================================================
s = frame(prs, "Annexe", "Où retrouver le détail", nxt(),
          notes="Chemins relatifs à la racine du dépôt.")
table(s, 0.6, 1.9, [4.6, 7.53], [
    ["Document", "Contenu"],
    ["`docs/concepts/en-clair.md`", "le POC sans jargon : pièces, choix, ce qu'il ne fait pas"],
    ["`docs/notes/verification/roadmap.md`", "ce qui est livré, ce qui reste, dette et réutilisation de bibliothèques"],
    ["`docs/architecture/agent-loop.md`", "la boucle d'itération et la phase d'édition"],
    ["`docs/adr/0001-serena-sidecar.md`", "pourquoi Serena en sidecar"],
    ["`.scratch/harness-selection/` (`criteria`, `synthese`, `protocole`, `notes/A-B-C`)", "l'étude des harness, "
                                                                                           "la grille, le protocole et les premières mesures"],
    ["`docs/notes/verification/`", "harnais existants, notion de harness, multi-machines, clients parallèles, "
                         "indexation, modèles pour l'EVO-X5"],
    ["`docs/notes/verification/functional-checks.md`", "les 25 vérifications automatiques et la campagne MCP manuelle"],
], size=12, row_h=0.6, header_h=0.38)

prs.save(OUT)
print("OK", OUT, n, "diapos")
