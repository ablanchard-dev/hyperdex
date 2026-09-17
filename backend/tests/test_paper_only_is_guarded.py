"""La promesse « paper-only » du README est-elle tenue par le code, ou seulement écrite ?

Le README ouvre sur un encadré de STATUT, et c'est la phrase qui engage le plus ce dépôt :

    > Status: paper-only / research. [...] The order-submission client
    > (`execution/exchange.py`) contains a live branch, but it is not wired into any
    > entry point [...] the only runner (`scripts/p2/launch_paper.py`) forces
    > `dry_run=True`.

Mesuré le 17/09/2026 : **aucun des 55 tests ne vérifiait ça.** Le seul contrôle existant
est `scripts/p2/_smoke_dry_run.py` — un script manuel, préfixé d'un `_`, que pytest ne
collecte pas. Quelqu'un (ou un futur remaniement) pouvait câbler la branche live sans
qu'une seule ligne rouge n'apparaisse, sur un dépôt qui parle d'argent réel.

Le garde-fou est STRUCTUREL, sur l'AST : toute construction d'`ExchangeClient` hors du
module qui le définit doit passer `dry_run=True` **écrit littéralement**. Une variable,
un `not x`, un défaut implicite : refusé. On ne demande pas au lecteur de suivre le flot.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1]
# Le module qui DÉFINIT le client a le droit de parler des deux modes : c'est son travail.
DEFINITION = RACINE / "app" / "services" / "execution" / "exchange.py"


def _sources(racine: Path) -> list[Path]:
    return [
        p for p in racine.rglob("*.py")
        if ".venv" not in p.parts and "site-packages" not in p.parts
    ]


def _noeuds_sous_refus(arbre: ast.AST) -> set[int]:
    """Les nœuds écrits pour être REFUSÉS : corps d'un `try:` ou d'un `pytest.raises`.

    `_smoke_dry_run.py` construit un client live sans SDK pour vérifier que la
    construction **lève** — c'est une preuve du garde-fou, pas un câblage. Les compter
    comme une faute aurait forcé à supprimer la seule vérification négative existante :
    le garde-fou aurait alors affaibli le produit au lieu de le tenir.
    """
    dedans: set[int] = set()
    for n in ast.walk(arbre):
        corps = None
        if isinstance(n, ast.Try):
            corps = n.body
        elif isinstance(n, ast.With):
            appels = [i.context_expr for i in n.items if isinstance(i.context_expr, ast.Call)]
            if any(ast.unparse(a.func).endswith("raises") for a in appels):
                corps = n.body
        if corps:
            for bloc in corps:
                for sous in ast.walk(bloc):
                    dedans.add(id(sous))
    return dedans


def constructions_live(racine: Path) -> list[str]:
    """Les constructions d'ExchangeClient qui ne sont pas explicitement paper.

    Rend une ligne de description par faute, vide si la promesse tient.
    """
    fautes: list[str] = []
    for chemin in _sources(racine):
        if chemin.resolve() == DEFINITION.resolve():
            continue
        arbre = ast.parse(chemin.read_text(encoding="utf-8"), filename=str(chemin))
        refuses = _noeuds_sous_refus(arbre)
        for noeud in ast.walk(arbre):
            if id(noeud) in refuses:
                continue
            if not isinstance(noeud, ast.Call):
                continue
            cible = noeud.func
            nom = cible.id if isinstance(cible, ast.Name) else (
                cible.attr if isinstance(cible, ast.Attribute) else None)
            if nom != "ExchangeClient":
                continue
            kw = {k.arg: k.value for k in noeud.keywords if k.arg}
            valeur = kw.get("dry_run")
            paper = isinstance(valeur, ast.Constant) and valeur.value is True
            if not paper:
                dit = ast.unparse(valeur) if valeur is not None else "<absent>"
                fautes.append(
                    f"{chemin.relative_to(racine)}:{noeud.lineno} dry_run={dit}")
    return fautes


def test_aucun_point_d_entree_ne_construit_un_client_live():
    fautes = constructions_live(RACINE)
    assert not fautes, (
        "Le README promet paper-only ; ces constructions ne le sont pas :\n  "
        + "\n  ".join(fautes))


def test_le_garde_fou_voit_une_branche_live_cablee(tmp_path):
    """Un garde-fou qui ne tombe jamais est décoratif.

    On lui donne à lire une copie de l'arborescence où UN point d'entrée passe en live,
    et il doit la nommer. Sans ça, le test ci-dessus prouverait surtout que rien ne
    s'appelle `ExchangeClient`.
    """
    faux = tmp_path / "app"
    faux.mkdir()
    (faux / "runner_mute.py").write_text(
        "from x import ExchangeClient\n"
        "client = ExchangeClient(dry_run=False, info=None, exchange=None)\n",
        encoding="utf-8")

    fautes = constructions_live(tmp_path)

    assert len(fautes) == 1, fautes
    assert "runner_mute.py" in fautes[0]
    assert "dry_run=False" in fautes[0]


@pytest.mark.parametrize("ecrit", [
    "dry_run=paper_mode",       # une variable : il faudrait suivre le flot pour savoir
    "dry_run=not live",         # une expression : pareil
    "info=None",                # absent : le défaut n'est pas une promesse
])
def test_seul_un_True_ecrit_en_toutes_lettres_compte(tmp_path, ecrit):
    faux = tmp_path / "app"
    faux.mkdir()
    (faux / "runner_mute.py").write_text(
        f"from x import ExchangeClient\nc = ExchangeClient({ecrit})\n", encoding="utf-8")

    assert constructions_live(tmp_path), f"{ecrit} devrait etre refuse"
