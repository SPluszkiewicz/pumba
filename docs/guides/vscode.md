# VS Code et Continue.dev

Ce que fait chaque pièce, sans jargon :
[Comprendre sans bagage](../concepts/en-clair.md). Le démarrage sur
l'EVO-X5 est dans [Déployer sur l'EVO-X5](evo-x5.md).

## Avant d'ouvrir l'éditeur

La pile tourne (`python scripts/deploy.py up`), puis la copie de travail
est enregistrée :

```text
python scripts/deploy.py workspace D:/svn/ticket-142
```

Cette commande écrit `.vscode/mcp.json` dans cette copie, pose l'en-tête
`X-Workspace`, et indexe le dépôt distant une fois.

Si l'éditeur est sur une autre machine que celle qui sert le modèle,
ouvre un tunnel SSH depuis le poste où tourne VS Code :

```text
ssh -L 8090:127.0.0.1:8090 -L 8092:127.0.0.1:8092 -L 8080:127.0.0.1:8080 utilisateur@machine-serveur
```

Toutes les adresses ci-dessous restent alors `127.0.0.1`.

## 1. Continue.dev : le modèle local et le MCP

Continue lit `~/.continue/config.yaml` (sous Windows :
`%USERPROFILE%\.continue\config.yaml`), ou `.continue/assistants/` dans
le dossier ouvert. Le format actuel est YAML.

Sur l'EVO-X5, l'identifiant du modèle est `mistral-small-4`. Sur un
profil NVIDIA 14B, c'est `Qwen2.5-Coder-14B-Instruct-Q5_K_M`.

```yaml
name: pumba
version: 1.0.0
schema: v1

models:
  - name: Mistral Small 4
    provider: openai
    model: mistral-small-4
    apiBase: http://127.0.0.1:8080/v1
    apiKey: local
    roles:
      - chat
      - edit
      - apply
    capabilities:
      - tool_use

mcpServers:
  - name: pumba
    type: streamable-http
    url: http://127.0.0.1:8092/mcp
    requestOptions:
      headers:
        X-Workspace: D:/svn/ticket-142
```

`apiKey` doit seulement être non vide. `X-Workspace` est le chemin de la
copie sur la machine où tourne la pile. Les outils MCP ne sont appelés
qu'en mode **Agent** de Continue. C'est Continue qui modifie les
fichiers dans l'éditeur ; le MCP ne lui renvoie que des faits.

Si une ancienne installation a encore `~/.continue/config.json`, garde
un seul des deux fichiers : `config.yaml` prend le dessus.

## 2. VS Code (Copilot) : le MCP seul

`.vscode/mcp.json` est déjà écrit par `deploy.py workspace` :

```json
{
  "servers": {
    "pumba": {
      "type": "http",
      "url": "http://127.0.0.1:8092/mcp",
      "headers": {
        "X-Workspace": "D:/svn/ticket-142"
      }
    }
  }
}
```

Copilot rédige et applique ses diffs. Il appelle `pumba` pour lire
l'index et les outils C++ :

| Outil | Ce qu'il renvoie |
| --- | --- |
| `lookup` | Extrait de l'index : chemin, lignes, symbole. Les fichiers modifiés (`svn`/`git status`) passent en tête, marqués `[working copy]`. |
| `check` | Journal `build`, `test` ou `compile`. |
| `outline` | Symboles d'un fichier (`nom kind ligne`). |
| `calls` | Appelants et appelés (`nom chemin:ligne`). |
| `types` | Types de base et dérivés (`nom chemin:ligne`). |
| `ask` | Jugement court et cité (400 tokens). À coller tel quel. |

La lecture de fichiers, la recherche plein texte et l'historique Git ou
Subversion viennent des serveurs MCP
[Filesystem](https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem),
[Git](https://github.com/modelcontextprotocol/servers/tree/main/src/git)
et [svn-mcp](https://github.com/WillBrock/svn-mcp/). Jira et Confluence
passent par [mcp-atlassian](https://github.com/sooperset/mcp-atlassian)
via `atlassian.env` (copie de `atlassian.env.example`).

## 3. Qui modifie les fichiers

Une seule écriture par changement :

- Dans VS Code (Continue ou Copilot), l'éditeur applique le diff. Le MCP
  `pumba` n'expose ni `edit` ni `apply`.
- Sur la page `http://127.0.0.1:8090/ui`, l'agent passe par `patch`
  (`edit` puis `apply`), sur la machine où tourne la pile, sans commit.

L'embedder et Qdrant restent dans le réseau compose et ne sont pas
publiés. Le conteneur `llm` NVIDIA reste sur `127.0.0.1`. Sur `evo-x5`,
le processus hôte écoute `0.0.0.0` pour que le conteneur `agent`
l'appelle ; l'éditeur utilise toujours `127.0.0.1`.
