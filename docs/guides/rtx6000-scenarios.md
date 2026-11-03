# Scénarios RTX 6000

À lancer sur la machine qui a la RTX 6000, pas sur la 4080 de 16 Go.
Le but est de comparer des profils, pas de revérifier le câblage MCP.

Le script `scripts/host/profile_campaign.py` reprend ces scénarios. Sans
`--run`, il affiche seulement les profils qui correspondent à `nvidia-smi`.
Avec `--run`, il applique chaque profil, recrée `llm`, envoie S0–S5 à
l'agent et écrit `state/profile-campaign.json`. Ce n'est pas un job GitHub
Actions.

```text
python scripts/host/profile_campaign.py
python scripts/host/profile_campaign.py --run --workspace D:/chemin/vers/sample
```

Les étapes `lookup`, `check`, `outline`, `calls` et `types` de
[Tests fonctionnels](prompt-tests-fonctionnels.md) ne passent pas par le
modèle de chat. On les fait **une fois**, avant la campagne, pour confirmer
que la pile répond. Ensuite chaque profil passe par la page
`http://127.0.0.1:8090/ui`, une conversation neuve par scénario.

## Quel profil, selon `nvidia-smi`

Lire `memory.total`, pas le gestionnaire des tâches.

| `memory.total` | Profils à comparer | Les autres |
| --- | --- | --- |
| ~24576 MiB | `24g`, puis `24g-30b` | `48g*` ne tient pas |
| ~46068 MiB (ECC) ou 49152 | `48g`, `48g-30b`, `48g-32b` | `24g-30b` est inutile ici : le Q8 est le cas fort |
| ~16376 MiB | aucun de cette page | rester sur `16g` |

`24g` et `48g` sont le même 14B. Les comparer entre eux mesure le KV
(`q8_0` contre `f16`) et la place, pas un autre modèle. Le gain attendu
est `24g` contre `24g-30b`, ou `48g` contre `48g-30b` et `48g-32b`.

## Avant chaque profil

```text
python scripts/deploy.py fetch-extra 24g-30b
python scripts/deploy.py gpu-profile 24g-30b
```

`fetch-extra` seulement pour `24g-30b`, `48g-30b`, `48g-32b`.
`16g`, `24g` et `48g` utilisent le 14B déjà téléchargé par `up`.

Si `EMBEDDER_GPU_ARG` change, reconstruire l’embedder, puis recréer
`llm`, `embedder` et `reranker`. Sinon recréer `llm` suffit.
Attendre que `http://127.0.0.1:8080/v1/models` montre l’identifiant
du profil (nom du fichier sans `.gguf`). Un 0.5B ou un 14B encore
chargé invalide le scénario.

Workspace : `sample`, déjà indexé. Remettre `sample/` propre entre
deux scénarios qui touchent un diff (`git checkout -- sample` ou
équivalent). Ne pas commit.

## S0 — ça tient

À faire pour chaque profil, avant S1. Un échec arrête ce profil.

1. `http://127.0.0.1:8080/health` répond.
2. `/v1/models` affiche le bon identifiant.
3. Sur `/ui`, envoyer `Réponds exactement : pong`.
4. Pendant la réponse, `nvidia-smi` : mémoire utilisée, et si le
   processus déborde sur la mémoire système.

| Résultat | Suite |
| --- | --- |
| healthy, bon modèle, réponse `pong`, pas de débordement | S1 à S5 |
| `llm` ne devient pas healthy, ou le processus déborde | profil éliminé. Sur 24 Go, revenir à `24g` |

Noter les Mio utilisés. C’est le seul chiffre qui départage `24g` et
`48g` pour le même 14B.

## S1 — outil, pas un avis

Conversation neuve. Coller :

```text
Lance uniquement le cas ctest known_bug. Utilise les outils. Ne devine pas le résultat.
```

Réussi : un appel d’outil `test` avec le filtre `known_bug`, puis une
réponse qui dit que ces cas échouent et qui cite un nom `TEST_CASE` du
journal. Raté : une liste de bugs sans appel d’outil, ou un JSON
d’outil écrit dans le texte.

## S2 — citation

Conversation neuve. Coller :

```text
Le code de sample montre-t-il que 2+3*4 vaut 14 ? Cite path:ligne. Si le code ne le montre pas, dis-le.
```

Réussi : cite `tests/calculator_test.cpp` (le `REQUIRE` qui attend 14)
et dit que `src/calculator.cpp` ne calcule pas cette précédence. Le
`ParseExpression` actuel traite `+` et `*` au même niveau, donc
`2+3*4` y vaut 20. Raté : affirmer que le programme donne 14, citer un
fichier absent, ou écrire un appel d’outil dans le texte.

## S3 — absence

Conversation neuve. Coller :

```text
Où est déclaré Zorglub42 ? Si le dépôt ne le contient pas, dis-le.
```

Réussi : le dépôt ne le contient pas. Raté : un chemin ou un symbole
inventé.

## S4 — diff qui s’applique

Conversation neuve. Coller :

```text
Propose un seul diff unifié pour que le test « known_bug: multiplication binds tighter than addition » passe. Ne modifie pas le test. N'applique pas le diff.
```

Enregistrer le bloc `diff` dans un fichier. Dans `sample/` :

```text
git apply --check correctif.diff
```

Réussi : `--check` passe, et le hunk touche `src/calculator.cpp`, pas
`tests/calculator_test.cpp`. Raté : le diff ne s’applique pas, le test
est modifié pour attendre 20, ou il n’y a pas de bloc `diff`.

Ne pas appliquer le diff pour la suite. Remettre l’arbre propre si un
essai l’a modifié.

## S5 — deux fichiers, un diff

Conversation neuve. Coller :

```text
Ajoute int Version() qui retourne 1 : déclaration dans src/calculator.hpp, définition dans src/calculator.cpp. Un seul diff unifié. N'applique pas le diff.
```

Même vérification : `git apply --check`. Réussi si les deux fichiers
sont dans le diff, la déclaration et la définition concordent, et
`--check` passe. Raté sinon.

## Compte-rendu

Une ligne par profil qui a passé S0. Les autres : une ligne `éliminé`
et la raison (healthy, débordement, mauvais modèle chargé).

| Profil | Mio | S1 | S2 | S3 | S4 | S5 |
| --- | --- | --- | --- | --- | --- | --- |
| | | ok ou raté | | | | |

Le profil retenu est celui qui a le plus de `ok` sur S1–S5 sans
débordement. À égalité, garder le plus rapide à répondre et le moins
de Mio. `48g` ne gagne pas sur `24g` si les cinq scénarios sont
identiques : c’est le même 14B.
