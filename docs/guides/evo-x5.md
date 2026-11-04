# Déployer sur l'EVO-X5 Pro

GMKtec EVO-X5 Pro : Ryzen AI Max+ PRO 495, Radeon 8065S, 192 Go de LPDDR5X
unifiés. Le profil s'appelle `evo-x5`. Il charge Mistral Small 4 en Q5
(~83 Go, trois morceaux), alias `mistral-small-4`.

L'image CUDA du service `llm` ne voit pas ce GPU. `up` lance
`llama-server` sur l'hôte, en Vulkan, et ne démarre pas ce conteneur.

P-MODE sur Performance (120 W soutenus). Silent et Balanced baissent
l'horloge soutenue.

## Sur la machine

`llama-server` est sur le PATH. Puis, depuis la racine du dépôt :

```text
python scripts/deploy.py gpu-profile evo-x5
python scripts/deploy.py up
```

`gpu-profile` écrit `config/profiles/evo-x5.env` dans `.env`, une fois par
machine. Il ne parle pas à Podman. `up` télécharge les trois shards s'ils
manquent, démarre la pile, et attend `/health` de `llama-server` sur l'hôte.
Il ne démarre pas le conteneur `llm`. `down` arrête les deux.
`python scripts/deploy.py fetch-extra evo-x5` reste disponible à part.

Le profil pose `LLM_SERVE=host`, `LLM_GPU_ARG=0`, le contexte 262 144
(le maximum du modèle, 256 × 1024), le cache `q8_0`, 16 threads.
Le même chiffre est le budget de l'agent. L'embedder et le reranker
restent sur le CPU. Un profil 14B remet `LLM_SERVE=container` et
`LLM_BASE_URL=http://llm:8080`.

`llama-server` doit être sur le PATH. Les trois shards sont sous
`models/UD-Q5_K_M/`. Le serveur écoute sur toutes les interfaces de
cette machine, pour que le conteneur `agent` l'appelle. La page
(`127.0.0.1:8090`) et le MCP (`127.0.0.1:8092`) restent en loopback.
Ne redirige pas le port 8080 sur la box si le modèle ne doit pas être
joignable depuis le réseau.

`--fit` n'est pas dans la commande : sur cet APU il renvoie des experts
sur le CPU. Vulkan est le moteur. `--backend rocm` affiche une sonde
et `--run` s'arrête.

## VS Code

Sur la même machine, après `up` :

```text
python scripts/deploy.py workspace <chemin-de-la-copie>
```

Cela écrit `.vscode/mcp.json`. Le serveur `pumba` est
`http://127.0.0.1:8092/mcp`, avec l'en-tête `X-Workspace` sur cette
copie. Les outils sont `lookup`, `check`, `outline`, `calls`, `types`,
`ask`. Ils ne modifient pas les fichiers.

La page de l'agent est `http://127.0.0.1:8090/ui`. Le jeton est
`AGENT_BEARER_TOKEN`. Une demande d'édition passe par `patch`
(`edit` puis `apply`), sans commit.

Pour que l'éditeur parle au modèle lui-même (Continue.dev), le client
compatible OpenAI utilise `http://127.0.0.1:8080/v1` et l'identifiant
`mistral-small-4`. Le fichier `config.yaml` complet est dans
[VS Code et Continue.dev](vscode.md). C'est l'éditeur qui applique
alors le diff. Le MCP ne le fait pas.
