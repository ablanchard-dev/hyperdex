# ─────────────────────────────────────────────────────────────────────────────────────────
# « la suite n'a besoin ni du reseau ni d'une cle » — tenu ici, pas seulement ecrit.
#
# Mesure du 18/09/2026 : 84 tests passent avec TOUTE sortie reseau bloquee et les variables
# de cle retirees de l'environnement. C'etait vrai, et tenu par rien. Un test qui appellerait
# une API passerait au vert sur une machine connectee et casserait chez qui clone.
# # Depot d'argent reel : un test qui atteindrait l'API Hyperliquid, meme en lecture, lierait la suite a un service distant et a ses limites de debit.
#
# ⚠️ CE CONFTEST NE COUVRE PAS TOUT LE DEPOT. La CI lance DEUX jobs : celui-ci
# (`working-directory: backend`, 86 tests) et un second dans `backend/scripts/p2`
# (`test_dsr_pbo.py`, 11 tests) — une seconde racine que `pytest` depuis `backend/` ne
# collecte pas. Elle a son propre conftest, avec le meme blocage. Mesure du 18/09/2026 :
# 97 tests au total passent hors-ligne.
#
# La boucle locale reste ouverte : la couverture et le debogueur s'en servent, et l'interdire
# testerait pytest plutot que le produit.
#
# La PREUVE que ce blocage mord vit dans `test_la_suite_ne_sort_pas_du_poste.py` — un fichier que pytest COLLECTE.
# Le meme controle ecrit ici ne tournerait jamais, et le compte de tests ne bougerait pas.
import socket as _socket

_CONNECT = _socket.socket.connect
_CONNECT_EX = _socket.socket.connect_ex


class SortieReseauInterdite(RuntimeError):
    """Un test a tente de sortir. La suite n'est pas censee en avoir besoin."""


def _est_local(adresse) -> bool:
    try:
        hote = adresse[0]
    except (TypeError, IndexError):
        return False
    return hote in {"127.0.0.1", "::1", "localhost", ""}


def _refuser(vrai):
    def _appel(self, adresse, *a, **kw):
        if not _est_local(adresse):
            raise SortieReseauInterdite(f"sortie reseau vers {adresse!r}")
        return vrai(self, adresse, *a, **kw)
    return _appel


_socket.socket.connect = _refuser(_CONNECT)
_socket.socket.connect_ex = _refuser(_CONNECT_EX)
