"""Adaptateurs spécifiques à un site, prioritaires sur les stratégies génériques.

Pour prendre en charge un lecteur que les stratégies génériques analysent mal :

1. créer un module dans ce dossier, par exemple ``exemple_site.py`` ;
2. y définir une sous-classe de ``Adapter`` :

    from offreader.adapters import Adapter, register
    from offreader.models import Extraction, Mode, Planche

    @register
    class ExempleSite(Adapter):
        name = "exemple-site"

        @classmethod
        def matches(cls, url: str) -> bool:
            return "lecteur.exemple.org" in url

        async def extract(self) -> Extraction:
            urls = await self.page.evaluate("() => window.chapterImages")
            return Extraction(planches=[Planche(i, u) for i, u in enumerate(urls, 1)],
                              mode=Mode.VERTICAL, title=await self.title())

3. l'importer dans ``_load_builtin`` ci-dessous.

Un adaptateur reçoit la même session navigateur que les stratégies génériques
et renvoie la même représentation ``Extraction`` : téléchargement et export
fonctionnent sans modification.
"""

from __future__ import annotations

from ..strategies.base import Strategy

_REGISTRY: list[type["Adapter"]] = []


class Adapter(Strategy):
    name = "adaptateur"

    @classmethod
    def matches(cls, url: str) -> bool:
        raise NotImplementedError


def register(cls: type[Adapter]) -> type[Adapter]:
    _REGISTRY.append(cls)
    return cls


def find_adapter(url: str) -> type[Adapter] | None:
    _load_builtin()
    for cls in _REGISTRY:
        if cls.matches(url):
            return cls
    return None


def _load_builtin() -> None:
    # Importer ici les adaptateurs fournis (aucun pour l'instant).
    pass
