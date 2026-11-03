# Migration vers les serveurs MCP externes

Le client (Cursor, VS Code, Claude Code) assemble plusieurs serveurs. Ce dépôt garde le modèle local, la compilation et les tests. La navigation, la lecture de fichiers, Git, Subversion et la recherche par le sens passent par des serveurs déjà maintenus, lancés sur le poste, dans le dossier de la copie.

| Rôle aujourd’hui | Serveur cible | Où il tourne |
|---|---|---|
| `outline`, `calls`, `types`, et les autres appels clangd | [Serena](https://github.com/oraios/serena) | Le poste, sur la copie ouverte |
| `read_file`, `list_files`, `search` | Serveur MCP Filesystem | Le poste, sur la copie ouverte |
| `git_log`, `git_blame`, `git_diff`, `git_show` | Serveur MCP Git | Le poste, si la copie a un `.git` |
| `svn_status`, `svn_diff`, `svn_log` | [svn-mcp](https://github.com/WillBrock/svn-mcp/) | Le poste, si la copie a un `.svn` |
| `lookup` et l’index Qdrant vus par l’éditeur | [Claude Context](https://github.com/zilliztech/claude-context) | Milvus + embedder, index de la copie du poste |
| `check` (`build`, `test`, `compile`) et `ask` | MCP `local-llm` de ce dépôt | La machine qui a le GPU |

Serena, le filesystem, Git et svn-mcp lisent les fichiers du poste. Une copie arrêtée sur une ancienne révision et des modifications non commitées sont donc l’arbre réel, sans index central à aligner.

## Ce qui reste dans ce dépôt

- Le conteneur `llm` et la page `http://127.0.0.1:8090/ui`.
- `check` : CMake, ctest, compilation d’un fichier. Aucun des serveurs ci-dessus ne les fournit.
- `ask` : jugement court et cité, sans schéma d’outils.
- Les outils internes (`/svn/*`, `/git/*`, `/lsp/*`, `/read-file`) tant que la page `/ui` s’en sert. Les retirer n’est utile qu’après avoir cessé de les proposer au modèle local. Voir la dernière section.

## 1. Configuration écrite dans chaque copie

`python scripts/deploy.py workspace` écrit aujourd’hui `.vscode/mcp.json` avec `local-llm` et `atlassian`.

| Fichier | Mise à jour |
|---|---|
| `scripts/host/workspaces.py` (`install_vscode_mcp`, `ATLASSIAN_MCP_SERVER`) | Ajouter `serena`, `filesystem`, `git`, `svn` et `claude-context` à côté de `local-llm`. Le chemin `X-Workspace` reste sur `local-llm` seulement. Les autres serveurs reçoivent le dossier de la copie comme racine. |
| `tests/scripts/test_workspaces.py` | Le test qui pose `X-Workspace` doit accepter les nouveaux serveurs et vérifier qu’un second appel ne les efface pas. |
| `.vscode/mcp.json` et `sample/.vscode/mcp.json` | Même forme, pour la copie de travail de ce dépôt et pour `sample/`. |
| `.cursor/mcp.json` s’il est versionné | Cursor attend `mcpServers`, pas `servers`. Deux fichiers, ou un générateur qui écrit les deux. |

Forme VS Code à produire :

```json
{
  "servers": {
    "local-llm": {
      "type": "http",
      "url": "http://127.0.0.1:8092/mcp",
      "headers": { "X-Workspace": "D:/svn/ticket-142" }
    },
    "serena": {
      "type": "stdio",
      "command": "uvx",
      "args": ["--from", "serena-agent", "serena", "start-mcp-server", "--project", "D:/svn/ticket-142"]
    },
    "filesystem": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "D:/svn/ticket-142"]
    },
    "git": {
      "type": "stdio",
      "command": "uvx",
      "args": ["mcp-server-git", "--repository", "D:/svn/ticket-142"]
    },
    "svn": {
      "type": "stdio",
      "command": "uvx",
      "args": ["svn-mcp", "--root", "D:/svn/ticket-142"]
    }
  }
}
```

Les arguments exacts de `svn-mcp` et de Serena se vérifient sur leur README au moment du branchement. `git` ne se déclare que si la copie contient `.git`. `svn` ne se déclare que si elle contient `.svn`.

Claude Context a besoin d’un Milvus et d’un embedder (Ollama en local). Son entrée MCP pointe vers ce Milvus, et l’index se construit sur le dossier du poste (`index_codebase`), pas sur la collection Qdrant de ce dépôt.

## 2. MCP `local-llm` : retirer les outils couverts ailleurs

L’éditeur ne doit plus voir deux outils pour le même geste.

| Fichier | Mise à jour |
|---|---|
| `services/mcp/server.py` | Retirer `outline`, `calls`, `types`. Retirer `lookup` quand Claude Context répond à la recherche. Garder `check` et `ask`. Ajuster le texte `instructions` du serveur. |
| `services/mcp/agent_client.py` | Retirer `lsp_fact_agent`, `lookup_agent`, `format_lookup`, `format_fact` une fois plus appelés. |
| `tests/mcp/test_server.py` | Les cas `outline`, `calls`, `types`, `lookup` partent avec les outils. Garder les cas `check` et `ask`. |
| `tests/mcp/test_agent_client.py` | Même découpe. |
| `CONTRACTS.md` | La section MCP ne liste plus ces outils. Les routes HTTP de `tools` restent tant que `/ui` les utilise. |
| `docs/reference/mcp.md` | Décrire les serveurs voisins et le fait que `local-llm` ne fait plus que `check` et `ask`. |
| `docs/guides/vscode.md` | Montrer le `mcp.json` complet. |
| `docs/guides/using-the-assistant.md` | La liste des outils MCP de l’éditeur devient la table du haut de ce guide. |
| `docs/guides/prompt-tests-fonctionnels.md` | Les étapes 1 et 2 (`lookup`) visent Claude Context. Les étapes 4, 5 et 6 (`outline`, `calls`, `types`) visent Serena. L’étape 3 (`check`) et l’étape 7 (`ask`) restent sur `local-llm`. L’étape 9 (fichier sale) se vérifie sur Serena ou le filesystem, qui lisent le fichier du poste. |

## 3. Modèle local (`/ui`) : deuxième temps

La page `/ui` ne passe pas par les serveurs MCP du poste. Elle appelle encore `services/agent`, qui appelle `services/tools`. Tant que cette page doit continuer à répondre, on ne supprime pas les handlers.

Quand le modèle local doit lui aussi s’arrêter de réimplémenter ces gestes, les fichiers suivants changent ensemble. Le schéma, le prompt et les règles nomment les mêmes outils. Un test vérifie qu’ils restent alignés.

| Fichier | Outils à retirer du schéma présenté au modèle |
|---|---|
| `services/common/tool_models.py` | `svn_status`, `svn_diff`, `svn_log`, `git_log`, `git_blame`, `git_diff`, `git_show`, `read_file`, `list_files`, `search`, `lsp_definition`, `lsp_references`, `lsp_hover`, `lsp_symbols`, `lsp_outline`, `lsp_calls`, `lsp_types` |
| `services/agent/tools_schema.py` | Le commentaire qui énumère ces noms |
| `services/agent/prompts.py` | Les consignes qui citent ces noms |
| `services/agent/rules/vcs.md` | La règle Git / SVN |
| `services/agent/main.py` | Les routes proxy devenues sans appelant |
| `services/tools/main.py` | Les endpoints `/svn/*`, `/git/*`, `/lsp/*`, `/read-file`, `/list-files`, `/search` |
| `services/tools/handlers/svn.py` et les handlers git, lsp, lecture de fichiers | Le code devenu sans route |
| `services/agent/working_copy.py` et `services/agent/checkouts.py` | L’extrait « fichier sale » devant l’index. Sur le poste, Serena et le filesystem lisent déjà ces octets. |
| `containers/tools/Containerfile` | Le paquet `subversion` ne sert plus si plus aucune route ne lance `svn`. |
| `services/tools/main.py` (`REQUIRED_BINARIES`) | Retirer `svn` et `git` du contrôle de santé s’ils ne sont plus appelés. |

À garder dans ce schéma : `build`, `test`, `compile_file`, `lint_clang_tidy`, `format_check`, `static_analysis`.

Tests à réécrire dans le même temps : `tests/tools/test_main_api.py`, `tests/agent/test_working_copy.py`, les tests d’agent qui attendent un appel `svn_*`, `git_*` ou `lsp_*`, et `tests/scripts/test_deploy.py` pour les chemins SVN qui ne concernent plus que le montage.

`CONTRACTS.md`, `docs/reference/tools-api.md`, `docs/reference/agent-api.md`, `docs/concepts/tool-calling.md` et `docs/architecture/stack-presentation.md` décrivent ces routes. Ils se mettent à jour avec le code. Un test du dépôt vérifie que `CONTRACTS.md`, `.env.example` et `compose.yaml` restent alignés.

## 4. Index Qdrant, seulement si Claude Context remplace `lookup`

Claude Context a son propre stock (Milvus) et son propre embedder. Le brancher sans retirer Qdrant fait deux index du même arbre.

| Fichier ou service | Mise à jour |
|---|---|
| `python scripts/deploy.py workspace` et `--update` | Cessent d’être la commande d’index pour l’éditeur. L’index de Claude Context se lance sur le poste (`index_codebase`). |
| `services/indexer/`, service `qdrant`, `embedder`, `reranker` | Restent pour `/ui` et pour `ask` tant que le modèle local retrieve encore. Ils partent seulement si `/ui` n’utilise plus cet index. |
| `docs/guides/reindexing-the-codebase.md`, `docs/guides/eval-checkout.md`, `docs/concepts/indexing-pipeline.md`, `docs/concepts/rag-explained.md` | Décrivent l’index qui reste. S’il part, ces pages partent avec lui. |
| `state/workspaces.json` et les montages `/checkouts/<id>` | Restent nécessaires à `check`, qui compile la copie montée sur la machine du GPU. Un poste distant ne peut lancer `check` que si cette machine voit son dossier, ou si `check` est limité aux copies présentes sur cette machine. |

## 5. Ordre

1. Étendre `install_vscode_mcp` et tenir les tests de `tests/scripts/test_workspaces.py`.
2. Vérifier à la main, sur une copie Git et sur une copie SVN, que Serena, le filesystem et le serveur de VCS répondent depuis l’éditeur.
3. Retirer du MCP `local-llm` les outils alors couverts, puis adapter `docs/guides/prompt-tests-fonctionnels.md`.
4. Brancher Claude Context et son Milvus. Retirer `lookup` du MCP `local-llm` ensuite.
5. Décider si `/ui` continue d’utiliser les outils internes. Si oui, s’arrêter là. Si non, appliquer la section 3 en un seul changement, schéma, prompts, routes et tests ensemble.
