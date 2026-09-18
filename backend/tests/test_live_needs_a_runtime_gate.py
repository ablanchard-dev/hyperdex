"""Construire un client LIVE demande un geste explicite à l'exécution, pas seulement du code.

Le README disait, honnêtement : *« The order-submission client contains a live branch, but it
is not wired into any entry point **and is not gated by a runtime flag** »*. La première moitié
est tenue par `test_paper_only_is_guarded.py`, qui lit les points d'entrée à l'AST. La seconde
était un aveu sans réponse.

Ce que ça laissait ouvert : la garde statique juge les points d'entrée du dépôt. Un script neuf,
un notebook, un `python -c`, une session d'agent qui veut « juste tester » — rien de tout ça ne
passe devant elle, et `ExchangeClient(dry_run=False, exchange=sdk)` envoie de vrais ordres avec de
l'argent réel. Une garde qui vit dans les tests protège le dépôt ; elle ne protège pas la machine.

Le garde-fou est **fail-closed** et vit dans le constructeur, seul passage obligé de toute voie
live : sans `HYPERDEX_ALLOW_LIVE` posée à la valeur exacte attendue, le client live n'existe pas.
Le mode paper, lui, n'est jamais gêné — une protection qui rend la recherche pénible finit
désactivée.
"""
from __future__ import annotations

import pytest

from app.services.execution.exchange import (
    ExchangeClient,
    LIVE_ENV,
    LIVE_AVEU,
    _exiger_l_aveu_live,
)


class FauxInfo:
    """Assez d'API pour construire le client, rien de plus : aucun réseau."""


class FauxExchange:
    """Le SDK authentifié — ici un objet vide, on ne va jamais jusqu'à l'ordre."""


def test_le_mode_paper_ne_demande_rien(monkeypatch):
    monkeypatch.delenv(LIVE_ENV, raising=False)
    client = ExchangeClient(dry_run=True, info=FauxInfo())
    assert client.dry_run is True


def test_sans_la_variable_le_client_live_ne_se_construit_pas(monkeypatch):
    monkeypatch.delenv(LIVE_ENV, raising=False)
    with pytest.raises(RuntimeError) as e:
        ExchangeClient(dry_run=False, info=FauxInfo(), exchange=FauxExchange())
    assert LIVE_ENV in str(e.value), "le message doit dire QUOI poser, sinon il fait deviner"


@pytest.mark.parametrize(
    "valeur",
    ["", "0", "false", "1", "true", "yes", "oui", "LIVE", LIVE_AVEU.upper(), " " + LIVE_AVEU],
)
def test_une_valeur_approchante_ne_suffit_pas(monkeypatch, valeur):
    """`=1` est ce qu'on tape sans réfléchir, et c'est précisément ce qu'on ne veut pas.

    La valeur attendue est une phrase qu'on n'écrit pas par accident ni par habitude.
    """
    monkeypatch.setenv(LIVE_ENV, valeur)
    with pytest.raises(RuntimeError):
        ExchangeClient(dry_run=False, info=FauxInfo(), exchange=FauxExchange())


def test_l_aveu_exact_ouvre_le_garde_fou(monkeypatch):
    """Le garde-fou doit pouvoir s'ouvrir : sinon ce n'est pas une porte, c'est un mur, et le
    prochain qui en a besoin le supprimera au lieu de le franchir.

    🔴 On appelle la porte, **on ne construit pas de client live**. `test_paper_only_is_guarded`
    lit tout le dépôt à l'AST et refuse `dry_run=False` hors d'un `pytest.raises` — y compris
    ici. Exempter les tests aurait été facile, et faux : un test est exactement l'endroit où
    quelqu'un construirait un client live « juste pour voir ». La garde statique reste donc au
    maximum de sa sévérité, et ce test vise plus juste : il teste la porte, pas le client.

    Que le constructeur APPELLE bien cette porte est prouvé par les tests ci-dessus, qui
    construisent avec `dry_run=False` et exigent le refus — et par mutation : retirer l'appel
    dans `__init__` en fait tomber onze.
    """
    monkeypatch.setenv(LIVE_ENV, LIVE_AVEU)
    _exiger_l_aveu_live(dry_run=False)   # ne lève pas


def test_la_garde_du_SDK_passe_toujours_en_premier(monkeypatch):
    """Même avec l'aveu posé, un live sans SDK authentifié reste un ValueError, pas un client.

    Sans ce test, on aurait pu remplacer l'ancienne garde par la nouvelle au lieu de l'ajouter.
    """
    monkeypatch.setenv(LIVE_ENV, LIVE_AVEU)
    with pytest.raises(ValueError):
        ExchangeClient(dry_run=False, info=FauxInfo(), exchange=None)
