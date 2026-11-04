# Roadmap — bibliothèques externes

Ce qui se remplace par un dépôt externe, ce qui reste, dans quel ordre, et ce qui avance en parallèle. La liste des fichiers est dans [Migration vers les serveurs MCP externes](migration-mcp-externe.md).

L’éditeur assemble les serveurs. Tant qu’un outil interne répond encore, on ajoute le serveur externe à côté. On retire l’outil interne seulement après une vérification sur une copie Git et une copie Subversion.

## Ce qui ne se remplace pas

| Pièce | Rôle |
|---|---|
| `llm`, `agent`, page `/ui` | Modèle local et boucle d’outils |
| `build`, `test`, `compile_file` | CMake, ctest, compilation d’un fichier |
| `lint_clang_tidy`, `format_check`, `static_analysis` | Analyse dans l’image `tools` |
| MCP `check` et `ask` | Journal de build et jugement court cité |
| Qdrant, indexeur, embedder, reranker | Recherche de la page `/ui`, tant qu’elle retrieve |

Qdrant, `llama.cpp` et le SDK MCP sont déjà des projets externes. Les changer ne retire pas de code dans `services/`.

## Ce qui se remplace

| Ici | Dépôt | Piste |
|---|---|---|
| Outils `lsp_*`, MCP `outline`, `calls`, `types` | [Serena](https://github.com/oraios/serena) | A |
| `read_file`, `list_files`, `search` | Serena (`read_file`, `list_dir`, `find_file`, `search_for_pattern`) | A |
| `git_log`, `git_blame`, `git_diff`, `git_show` | [mcp-server-git](https://github.com/modelcontextprotocol/servers/tree/main/src/git) | B |
| `svn_status`, `svn_diff`, `svn_log` | [svn-mcp](https://github.com/WillBrock/svn-mcp/) | C |
| `lookup` pour l’éditeur seulement | [Claude Context](https://github.com/zilliztech/claude-context) | D, optionnelle |

Serena lit les fichiers. Le serveur Git et svn-mcp restent nécessaires pour l’historique. Claude Context ne remplace pas Serena : il ajoute une recherche par le sens, avec son propre Milvus.

## Ordre

```text
Piste A  Serena          ─┐
Piste B  Git             ─┼─ en parallèle, rien n’est retiré
Piste C  Subversion      ─┘
         │
         ▼
Étape 2  Retirer du MCP pumba les outils déjà couverts
         (A d’abord ; B et C ne retirent rien du MCP, ils n’y sont pas)
         │
         ▼
Étape 3  Décision : la page /ui garde ses outils internes, ou non
         │
         ├─ elle les garde → on s’arrête
         │
         └─ elle les lâche → un seul changement : schéma, prompts,
            routes, tests, CONTRACTS.md
```

La piste D (Claude Context) peut démarrer en même temps que A, B et C. On ne retire `lookup` du MCP `pumba`, ni Qdrant, qu’une fois D vérifiée. Qdrant reste tant que `/ui` s’en sert.

## Étape 1 — ajouter, en parallèle

Trois configurations indépendantes. Chacune se vérifie seule. Aucune ne modifie `services/`.

| Piste | Livrable | Vérification |
|---|---|---|
| A | Serena en HTTP sur une copie du dépôt, URL dans `.vscode/mcp.json` et `.cursor/mcp.json` | `get_symbols_overview` et `find_symbol` sur un `.hpp` ; `search_for_pattern` sur une chaîne connue |
| B | Serveur Git, seulement si la copie a un `.git` | `git_log` ou l’outil équivalent sur un fichier du dépôt |
| C | svn-mcp, seulement si la copie a un `.svn` | `svn status` et un diff contre `BASE` |

`scripts/host/workspaces.py` écrit ces entrées à côté de `pumba`. `tests/scripts/test_workspaces.py` vérifie qu’un second `workspace` ne les efface pas. Les arguments de ligne de commande se prennent sur le README de chaque dépôt au moment du branchement.

Les outils d’écriture de Serena restent inutilisés : ils modifieraient la copie commune.

## Étape 2 — retirer du MCP `pumba`

Dépend de A. B et C n’ont rien à retirer ici : ce MCP n’expose pas Git ni Subversion.

| Retrait | Condition |
|---|---|
| `outline`, `calls`, `types` | A répond sur Git et sur Subversion |
| `lookup` | D répond, et on accepte que l’éditeur ne passe plus par Qdrant |

Fichiers : `services/mcp/server.py`, `services/mcp/agent_client.py`, `tests/mcp/`, `CONTRACTS.md`, `docs/reference/mcp.md`, `docs/guides/prompt-tests-fonctionnels.md`.

`check` et `ask` restent.

## Étape 3 — outils internes de `/ui`

Cette étape est entière ou elle ne se fait pas. Le schéma (`services/common/tool_models.py`), les prompts (`services/agent/prompts.py`, `services/agent/rules/vcs.md`) et les routes (`services/tools/main.py`, `services/agent/main.py`) sont tenus alignés par les tests. On ne retire pas une route pendant qu’une autre piste ajoute un serveur.

On retire ensemble : `svn_*`, `git_*`, `read_file`, `list_files`, `search`, `lsp_*`, et l’extrait de fichiers sales (`services/agent/working_copy.py`). On garde `build`, `test`, `compile_file`, `lint_clang_tidy`, `format_check`, `static_analysis`.

Qdrant, l’indexeur, l’embedder et le reranker ne font pas partie de ce retrait. Ils partent seulement si `/ui` n’interroge plus l’index.

## Ce qui n’avance pas en parallèle

- L’étape 2 et l’étape 1 de la même piste : on ne retire `outline` avant d’avoir vu Serena répondre.
- L’étape 3 et n’importe quel autre changement du schéma d’outils.
- Deux index pour le même geste : Claude Context branché sans décision sur `lookup` laisse Qdrant et Milvus répondre tous les deux.
