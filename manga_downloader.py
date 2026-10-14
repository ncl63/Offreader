"""Point d'entrée : python manga_downloader.py "URL_DU_CHAPITRE" --format cbz"""

import sys

from offreader.cli import main

if __name__ == "__main__":
    sys.exit(main())
