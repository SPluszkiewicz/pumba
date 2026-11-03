"""Journal d'entiers. Les tests du dossier sont le contrat.

`close_day` somme les lignes. Un total impair monte au pair suivant.
Une liste vide vaut 0. L'arrondi est encore dans `close_day` : la tâche
`refactor` demande de le sortir, les autres tâches le laissent là.
"""

from __future__ import annotations


def version() -> int:
    return 1


def close_day(lines: list[int]) -> int:
    if not lines:
        return 0
    total = 0
    for line in lines:
        total += line
    if total % 2:
        total += 1
    return total
