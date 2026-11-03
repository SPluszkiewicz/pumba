# Comparaison d'agents sur l'EVO-X5 Pro

À lancer sur le GMKtec EVO-X5 Pro (Ryzen AI Max+ PRO 495, Radeon 8065S, 192 Go
LPDDR5X unifiés). Même dépôt, même commit, mêmes tâches. Trois runs minimum
par case. L'écart d'une case est le max moins le min de ses runs. S'il dépasse
l'écart entre deux cases, le statut est `ne pas choisir`.

Le pack qui ne bat pas la checklist ne gagne pas sa place. La couche C ne la
lui rend pas.

```text
python scripts/host/agent_compare.py plan
python scripts/deploy.py fetch-extra evo-x5
python scripts/deploy.py gpu-profile evo-x5
python scripts/host/evo_x5_serve.py
python scripts/host/evo_x5_serve.py --reasoning high
```

`plan` nomme, pour chaque chiffre, la décision qu'il change. Ce n'est pas un
job GitHub Actions. Les tests du dépôt couvrent le score, pas la machine.

## La machine

P-MODE sur Performance (120 W soutenus). Silent et Balanced changent les
minutes : le score écarte ces runs.

Le serveur est `llama-server` sur l'hôte, Vulkan, en écoute sur
`127.0.0.1:8080` seulement. L'image CUDA du service `llm` ne voit pas le
Radeon. Ne démarre pas ce service : il prendrait le port sans le GPU.

Le fichier est le Q5 en trois morceaux (~83 Go), alias `mistral-small-4`.
119 milliards de paramètres, 6,5 milliards actifs. Contexte 32 768, cache
`q8_0`, 16 threads (les cœurs physiques, pas les 32 logiques). Pas de
`--fit` : sur cet APU il renvoie des experts sur le CPU, et les minutes
mesurent alors le débordement.

`--reasoning none` fixe la température à 0,1. `--reasoning high` la fixe à
0,7. C'est la paire décrite par Mistral. Le dédoublement ne se fait qu'une
fois : local, MCP allumé. Les couches B et C restent sur `none`.

`--backend rocm` affiche une sonde et le score la refuse. Un second moteur
déplacerait les minutes sans déplacer l'agent.

L'API Mistral utilise l'identifiant `mistral-small-2603`,
`reasoning_effort=none`, température 0,1. Le modèle Copilot est celui que
le client affiche : son nom est noté dans le run, et il est le même sur
tous les runs Copilot. `reasoning_effort` y vaut `none`, `high` ou
`absent`, et ne change pas entre les deux cases Copilot d'une paire.

## Trois couches

A. Même hôte, aucune skill. Sept cases : local sans MCP, local avec MCP,
le même local avec MCP en `high`, l'API sans MCP, l'API avec MCP, Copilot
sans MCP, Copilot avec MCP. Le `high` est le seul dédoublement.

B. Un seul modèle : `mistral-small-4` local, MCP allumé, `reasoning none`.
Six conduites : agent nu, `AGENTS.md`, Plan puis exécution, poteto-mode
(le modèle de chaque sous-agent est le même sur les trois runs), chaîne
Pocock, checklist de quinze lignes.

C. Cinq piles, sans en relancer une déjà couverte : local+MCP,
local+MCP+checklist, Copilot+MCP+poteto-mode, Copilot+MCP+Pocock,
local+MCP+poteto-mode.

`prepare` détache un worktree à côté du dépôt (`../local_llm-compare/trees`),
pas dedans : un ré-index du dépôt principal ne doit pas avaler les runs.
Le journal du run est `state/compare/logs/`.

## Tâches

Les textes sont ceux de `scripts/host/agent_compare.py`. Ils ne nomment pas
d'outil. L'éditeur n'a que le worktree. Le MCP, quand il est allumé, indexe
le dépôt principal.

| Tâche | Ce que le score regarde |
| --- | --- |
| `fait-externe` | La dernière ligne non vide de `campaign/external/ticket-441.md`. Ce fichier n'est pas dans le commit. |
| `bug` | `close_day([10, 20])` redevient 30, et la commande du prompt est collée en échec puis en succès. |
| `flou` | Le contrat du journal tient. Les questions et les minutes sont le chiffre. |
| `refactor` | `rounded` porte l'arrondi, `close_day` n'a plus le `% 2`. |
| `revue` | Le diff soumis n'est pas appliqué. Le contrat tient. |
| `question` | La réponse dit que la liste vide retourne 0. Un appel MCP est un échec de précision. |
| `quelques-lignes` | `version()` retourne 2. Le reste du contrat tient. |

`campaign/external/ticket-441.md` reste sur le disque du dépôt principal et
ne se commit pas. Un `git show` depuis le worktree ne le trouve pas.
S'il manque, le score de `fait-externe` s'arrête.

Avant la couche A, ré-indexe le dépôt principal une fois, avec ce fichier
présent. Les étapes `lookup` et `check` de
[Tests fonctionnels](prompt-tests-fonctionnels.md) se font aussi une fois,
pour voir que le MCP répond. Elles ne sont pas des cases.

## Chiffres

| Chiffre | Décision |
| --- | --- |
| `auto_ok` | Livrer ou non le changement de cette case. |
| `diff_hors_perimetre` | Accepter ou refuser le diff, selon les lignes hors du fichier désigné. |
| `mcp_rappel` | Laisser l'accès externe allumé pour un fait hors de l'arbre. |
| `mcp_precision` | Couper l'accès externe sur une question dont le code suffit. Un appel y est un échec. |
| `preuve` | Croire un test vert sans la commande collée en échec puis en succès. |
| `questions` | Laisser cette conduite attendre une personne. |
| `question_minutes` | Compter le temps humain dans le coût de la case. |
| `skill_tokens` | Imputer le texte du pack, à part de la tâche. |
| `task_tokens` | Imputer la tâche, à part du pack. |
| `minutes` | Garder la case sur cet hôte, selon le temps total. |

`skill_tokens` des fichiers (`AGENTS.md`, `CHECKLIST.md`, `SPEC.md`) est
compté par le score, quatre caractères pour un token. poteto-mode n'est pas
dans ce dépôt : son chiffre est celui du client (`skill_tokens` dans
`run.json`). Comparer les deux sources donne `ne pas choisir` sur ce
chiffre seulement. La place, elle, ne se joue pas sur les tokens.

Une tâche d'auto perdue contre la checklist refuse la place, même si la
moyenne est meilleure. À auto égal, la place se joue sur moins de lignes
hors périmètre. L'égalité ne donne pas la place.

## Répétition sur la 4080

Les mêmes cases, les mêmes tâches, le même score. Le modèle local de cette
machine est le 14B du profil `16g` (`Qwen2.5-Coder-14B-Instruct-Q5_K_M`,
CUDA). Le tableau que tu gardes est celui de l'EVO-X5, avec
`mistral-small-4`. Les deux dossiers ne se mélangent pas : `decide` s'arrête
s'il voit deux hôtes.

```text
python scripts/deploy.py gpu-profile 16g
python scripts/host/agent_compare.py prepare --host 4080 --cell local-mcp --task question --run 1 --commit HEAD
python scripts/host/agent_compare.py decide state/compare/logs/4080
```

Sur l'EVO-X5, `--host evo-x5` (la valeur par défaut) et
`state/compare/logs/evo-x5`.

## Lancer une case

```text
python scripts/host/agent_compare.py prepare --cell local-mcp --task fait-externe --run 1 --commit HEAD
```

Ouvre le worktree imprimé. Colle `PROMPT.md`. Remplis `run.json` : la
réponse, le diff (`git add -N -A` puis `git diff HEAD`), les appels MCP,
la preuve, les questions, les minutes, les tokens de la tâche. Pour
Copilot, le nom affiché. Pour poteto, le tableau des sous-agents. Pour
Pocock, `scripted` ou `implement-tdd`.

Les réponses Pocock sont dans le dossier du journal, pas dans le worktree.
Si la question n'y est pas, on n'invente pas une réponse humaine : implement,
puis tdd, sur `SPEC.md`.

```text
python scripts/host/agent_compare.py score --run state/compare/logs/<dossier>/run.json --worktree <worktree>
python scripts/host/agent_compare.py decide state/compare/logs
```

`score` écrit `metrics`. `decide` lit dans cet ordre : l'effet MCP sur
`fait-externe` (même modèle, sans skill), l'effet du pack (même modèle,
même MCP), puis l'interaction. Le dédoublement `high` est imprimé avec A
et ne déplace pas B ni C.

Un run Copilot dont le nom change, un poteto dont les sous-agents changent,
un Pocock qui mélange `scripted` et `implement-tdd`, ou deux commits dans
le même dossier : `ne pas choisir`.

À la fin : `git worktree remove` sur chaque arbre. Pas de commit.
