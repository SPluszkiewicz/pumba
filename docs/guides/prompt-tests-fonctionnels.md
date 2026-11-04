# Prompt — tests fonctionnels

Exécution automatique (23 vérifications de bout en bout) contre la pile en marche :

```bash
python scripts/deploy.py check
```

Le script `scripts/host/functional_check.py` vérifie automatiquement :

1. **Portes hôtes & CSP `/ui`** (`GET /health` sur `agent`, `mcp`, `llm` et `GET /ui`).
2. **Authentification Bearer** (`401` sans jeton et avec jeton invalide).
3. **Isolation des workspaces** (`400` sur un `X-Workspace` non enregistré).
4. **Catalogue de modèles** (`GET /models`).
5. **`lookup` — déclaration** (`Evaluate` dans `src/calculator.hpp:23` en tête, sans fichiers parasites).
6. **`lookup` — trace hybride + reranker** (`include_candidates=True`).
7. **`lookup` — symbole absent** (`Zorglub42` n'invente aucun symbole).
8. **Lecture bornée (`/read`)** (fenêtre de lignes `start_line`..`end_line`).
9. **Blocage de traversée de chemin** (`../../etc/passwd` rejeté).
10. **`check` — compilation unitaire (`compile_file`)** (`src/calculator.cpp`, code de sortie `0`).
11. **`check` — build CMake complet (`build`)** (preset `release`, code de sortie `0`).
12. **`check` — filtre `ctest` (`test`)** (`filter=known_bug` échoue avec le journal de test).
13. **LSP `outline`** (`src/calculator.hpp` : `CalcError` et `Evaluate`).
14. **LSP `calls`** (`src/calculator.cpp:118:5` : appelants dans `tests/calculator_test.cpp`).
15. **LSP `types`** (`src/calculator.hpp:18:7` : `runtime_error` dans `stdexcept`).
16. **Protocole MCP Streamable HTTP (`:8092/mcp`)** (`tools/list` + `tools/call` sur les 6 outils : `lookup`, `check`, `outline`, `calls`, `types`, `ask`).
17. **`/chat` — route directe** (`2+2` sans RAG).
18. **`/chat` — route code avec citations** (`Evaluate` cite `src/calculator.hpp:23`).
19. **`/chat` — mémoire multi-tour** (`conversation_id` conservé d'un tour à l'autre).
20. **`/chat` — jugement borné sans outils** (`enable_tools=False`, citations vérifiées).
21. **`/chat` — boucle d'appel d'outils (`tool_calls`)** (le modèle appelle `list_files` et exploite le résultat).
22. **Flux SSE `/chat/stream` + `/chat/cancel`** (événements `meta`, `route`, `context`, `token`, `done`).
23. **Overlay `[working copy]`** (modification locale non commitée dans `sample/src/calculator.hpp` détectée immédiatement devant l'index puis restaurée).

---

Pour tester manuellement depuis un client MCP (Cursor, VS Code, Continue), copie le bloc ci-dessous. Une étape par message. Recopie le texte de l'outil. N'invente pas un résultat.

---

Tu testes l'outil local de ce dépôt, pas le calculateur. Le dépôt sous test est `sample/` (calculateur entier). La stack doit déjà répondre. Si un service est injoignable, tu t'arrêtes et tu le dis.

## Avant toute question

1. Vérifie que ces URL répondent sur la machine hôte :
   - `http://127.0.0.1:8090/ui`
   - `http://127.0.0.1:8092/mcp`
   - `http://127.0.0.1:8080/v1/models`
2. Le workspace MCP est `sample` (en-tête `X-Workspace` ou `python scripts/deploy.py workspace` pointé sur `sample/`).
3. Les outils cochés sont `lookup`, `check`, `outline`, `calls`, `types`, `ask`. S'il n'y a qu'`ask`, arrête-toi : le test ne mesure rien.

## Règle de passage

À chaque étape, appelle **un seul** outil, avec les arguments indiqués, et recopie sa sortie telle quelle. Une phrase qui raconte, un symbole inventé, ou un passage par `ask` alors que l'étape nomme un autre outil : l'étape est **ratée**. Arrête la campagne au premier raté de ce type. Les étapes suivantes ne diraient plus rien.

`ask` ne sert qu'à l'étape qui le nomme. Il ne cherche pas, ne compile pas, ne liste pas de symboles.

## Étapes

**1. lookup — déclaration.** Outil `lookup`. Question telle quelle : `où est déclarée Evaluate ?`

Attendu : `src/calculator.hpp` et la ligne de `int Evaluate(std::string_view expression);` (ligne 23). Le corps `int Evaluate` dans `src/calculator.cpp` (vers la ligne 118) n'est pas la déclaration. Aucun fichier `.vscode` ni `CMakePresets.json` en tête si la question ne les nomme pas.

**2. lookup — absent.** Outil `lookup`. Question telle quelle : `où est déclaré Zorglub42 ?`

Attendu : aucun symbole, aucun fichier inventé. Une absence claire.

**3. check — oracle.** Outil `check`, action `test`, filter `known_bug`.

Attendu : un journal de tests (ctest), pas un résumé. Les cas `known_bug:` échouent sur le code actuel. Si la sortie est une liste rédigée de bugs sans `test exit` ni nom de `TEST_CASE`, c'est raté.

**4. outline.** Outil `outline`, path `src/calculator.hpp`.

Attendu : des lignes `nom kind ligne`, dont `CalcError` et `Evaluate`, avec un numéro de ligne. Pas un paragraphe. Si la sortie dit que `compile_commands.json` manque ou que le configure CMake a échoué, note **clangd indisponible** et continue seulement les étapes qui ne sont pas clangd (5 et 6 sont clangd : saute-les et marque-les non exécutées).

**5. calls.** Outil `calls`, path `src/calculator.cpp`, line `118`, character `5`, direction `both`.

Attendu : des lignes `nom path:ligne` (appelants ou appelés), ou une absence explicite. Pas une description de la fonction.

**6. types.** Outil `types`, path `src/calculator.hpp`, line `18`, character `7`, direction `both`.

Attendu : `CalcError` relié à `std::runtime_error`, avec un chemin et une ligne. Pas une phrase sur l'héritage sans position.

**7. ask — jugement borné.** Outil `ask` seulement. Question telle quelle : `Le code de sample montre-t-il que 2+3*4 vaut 14 ? Cite path:ligne. Si le code ne le montre pas, dis-le.`

Attendu : au plus une réponse courte, des citations `path:ligne` présentes dans le dépôt, ou la phrase que le code ne le montre pas. Pas d'appel d'outil dans le texte, pas de JSON `lsp_*` ou `compile_file`, pas de diff inventé, pas de `Sources:` qui cite un fichier absent. Le corps actuel calcule `2+3*4` comme 20 ; un `ask` qui affirme 14 sans ligne du dépôt qui le prouve est raté.

**8. Page /ui.** Ouvre `http://127.0.0.1:8090/ui`. Envoie : `Que vaut 2+2 ?`

Attendu : la trace affiche une route, puis des tokens au fil de l'eau, puis une réponse. Un bloc qui n'apparaît qu'à la fin est raté. Une question de code (`Où est déclarée Evaluate ?`) doit montrer la recherche d'index avant les tokens.

**9. Fichier sale.** Dans `sample/src/calculator.hpp`, ajoute une ligne de commentaire en tête, sans commit. Relance l'étape 1.

Attendu : l'extrait de `calculator.hpp` est marqué `[working copy]` et contient ce commentaire. L'index partagé ne suffit pas s'il montre l'ancien texte.

Remets le fichier comme avant (`git checkout` ou équivalent) à la fin de cette étape.

## Compte-rendu

Tableau, une ligne par étape :

| Étape | Statut | Preuve |
|---|---|---|
| 1 … 9 | ok, raté, ou non exécuté | Une ligne citée de la sortie outil, ou l'erreur exacte |

Statuts autorisés : `ok`, `raté`, `non exécuté`. Pas d'autre colonne. Pas de correctif proposé dans ce compte-rendu.
