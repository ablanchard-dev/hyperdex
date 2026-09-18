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


# ── La porte, pas le nom de la poignée. ──────────────────────────────────────────────────────
#
# ⚠️ 18/09/2026 — `constructions_live` ci-dessous reconnaît UN NOM DE CLASSE : `ExchangeClient`.
# C'est une orthographe, pas une capacité. Le contournement n'est même pas de renommer la
# classe : c'est de **sauter le wrapper**. L'en-tête d'`exchange.py` le dit lui-même —
# `dry_run=False → Exchange.market_open() → vrai fill HL`. Qui obtient un `Exchange` du SDK
# place des ordres, sans jamais écrire `ExchangeClient`, et sans passer par le garde-fou
# d'exécution ajouté le même jour dans son constructeur.
#
# Mesure du 18/09/2026 : `hyperliquid.exchange` n'est importé QUE dans `exchange.py`, et
# `market_open` n'y est appelé qu'une fois. Partout ailleurs, c'est `hyperliquid.info.Info` —
# le client en LECTURE SEULE, qui ne peut rien envoyer. Aucun appel HTTP brut vers
# `api.hyperliquid.xyz` non plus. Le dépôt est discipliné ; rien ne le tenait.
#
# 🔑 On garde donc la PORTE : on ne peut pas appeler `market_open` sans obtenir un `Exchange`,
# et on ne l'obtient qu'en l'important. Une règle binaire, sans faux positif possible — et qui
# vaut pour tout ce que le SDK sait faire de dangereux (`order`, `bulk_orders`, `usd_transfer`,
# `withdraw_from_bridge`), y compris ce qu'il saura faire demain.
MODULE_QUI_ORDONNE = "hyperliquid.exchange"

# Le seul module dont c'est le métier. Relatif à `backend/`, en barres obliques.
PORTES_AUTORISEES = {
    "app/services/execution/exchange.py":
        "le client d'exécution : c'est lui qui distingue paper et live, et depuis le 18/09 "
        "son constructeur exige un aveu explicite dans l'environnement pour le mode live",
}


def _importe_le_module_qui_ordonne(arbre: ast.AST) -> list[str]:
    """Les lignes où ce fichier obtient de quoi envoyer un ordre."""
    lignes: list[str] = []
    for n in ast.walk(arbre):
        if isinstance(n, ast.ImportFrom) and (n.module or "").startswith(MODULE_QUI_ORDONNE):
            lignes.append(f"{n.lineno} from {n.module} import …")
        elif isinstance(n, ast.Import):
            for a in n.names:
                if a.name.startswith(MODULE_QUI_ORDONNE):
                    lignes.append(f"{n.lineno} import {a.name}")
    return lignes


def portes_vers_l_api_d_ordres(racine: Path) -> list[str]:
    """Les fichiers qui savent obtenir l'API d'ordres du SDK, hors porte autorisée."""
    trouves: list[str] = []
    for chemin in _sources(racine):
        rel = chemin.relative_to(racine).as_posix()
        if rel in PORTES_AUTORISEES:
            continue
        arbre = ast.parse(chemin.read_text(encoding="utf-8"), filename=str(chemin))
        trouves += [f"{rel}:{ligne}" for ligne in _importe_le_module_qui_ordonne(arbre)]
    return sorted(trouves)


def test_une_seule_porte_mene_a_l_api_d_ordres_du_SDK():
    """Le garde qui manquait : on ne place pas d'ordre sans obtenir un `Exchange`.

    `constructions_live` ne voyait qu'un nom de classe ; celui-ci tient la capacité. Un fichier
    qui importe `hyperliquid.exchange` peut envoyer des ordres, quel que soit le nom qu'il donne
    à ses objets — et sans passer par le garde-fou d'exécution du constructeur.
    """
    hors_porte = portes_vers_l_api_d_ordres(RACINE)
    assert not hors_porte, (
        "Le README annonce paper-only. Ces fichiers peuvent obtenir l'API d'ordres du SDK "
        "sans passer par le client d'exécution :\n  " + "\n  ".join(hors_porte)
    )


def test_la_porte_autorisee_existe_et_porte_sa_raison():
    """Une permission qui ne vise plus rien est une permission oubliée."""
    assert PORTES_AUTORISEES, "sans porte nommée, le test du dessus passerait en ne gardant rien"
    for rel, raison in PORTES_AUTORISEES.items():
        chemin = RACINE / rel
        assert chemin.exists(), f"{rel} a disparu : la permission ne vise plus rien"
        assert raison.strip(), f"{rel} est autorisé sans raison écrite"
        # La raison invoquée tient sur une propriété vérifiable, pas sur ma parole.
        assert "LIVE_ENV" in chemin.read_text(encoding="utf-8"), (
            f"{rel} n'exige plus d'aveu explicite pour le mode live : la raison ne tient plus"
        )


@pytest.mark.parametrize(
    "code",
    [
        "from hyperliquid.exchange import Exchange",
        "from hyperliquid.exchange import Exchange as E",
        "import hyperliquid.exchange",
        "from hyperliquid.exchange.something import X",
    ],
)
def test_le_garde_voit_chaque_facon_d_ouvrir_la_porte(code, tmp_path):
    (tmp_path / "quelque_part.py").write_text(code + "\n", encoding="utf-8")
    assert portes_vers_l_api_d_ordres(tmp_path), f"aveugle sur : {code}"


@pytest.mark.parametrize(
    "code,pourquoi",
    [
        ("from hyperliquid.info import Info", "lecture seule : Info n'envoie rien"),
        ("from hyperliquid.utils import constants", "des constantes"),
        ("from hyperliquid.utils.error import ClientError", "un type d'erreur"),
        ("import hyperliquid", "le paquet seul n'ouvre aucune porte"),
    ],
)
def test_le_garde_n_accuse_pas_la_lecture_seule(code, pourquoi, tmp_path):
    """Le contrepoids : tout le dépôt lit le carnet d'ordres. Refuser ça le casserait."""
    (tmp_path / "quelque_part.py").write_text(code + "\n", encoding="utf-8")
    assert not portes_vers_l_api_d_ordres(tmp_path), f"faux positif ({pourquoi}) : {code}"


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
