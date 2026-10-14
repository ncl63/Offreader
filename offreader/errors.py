"""Exceptions métier. Chaque message est destiné à être lu par l'utilisateur."""


class OffreaderError(Exception):
    """Erreur générique, affichée telle quelle à l'utilisateur."""


class BrowserError(OffreaderError):
    """Impossible de lancer Chromium ou d'ouvrir la page."""


class NoImagesFound(OffreaderError):
    """Aucune planche n'a pu être identifiée dans la page."""


class DownloadError(OffreaderError):
    """Le téléchargement d'une planche a échoué définitivement."""


class CorruptImage(OffreaderError):
    """Les octets reçus ne forment pas une image complète et valide."""


class IncompleteChapter(OffreaderError):
    """Toutes les planches n'ont pas pu être récupérées."""
