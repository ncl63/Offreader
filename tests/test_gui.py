"""Interface graphique : réactivité, états, erreurs, annulation, fermeture.

Nécessite tkinter, customtkinter et un affichage (sous Linux : xvfb-run).
"""

import os
import sys
import time

import pytest

tk = pytest.importorskip("tkinter")
pytest.importorskip("customtkinter")
if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
    pytest.skip("aucun affichage disponible (lancer sous xvfb-run)", allow_module_level=True)

from offreader.gui import App  # noqa: E402

from .conftest import fast_config  # noqa: E402


@pytest.fixture
def app(tmp_path):
    opened = []
    a = App(config_factory=lambda: fast_config(tmp_path), opener=opened.append)
    a.opened = opened
    yield a
    try:
        if a.job:
            a.job.cancel()
            a.job.join(15)
        a.destroy()
    except tk.TclError:
        pass  # déjà fermée


def pump(app, until, timeout=120):
    """Fait tourner la boucle Tk ; renvoie la durée maximale d'un tour de boucle."""
    worst, end = 0.0, time.monotonic() + timeout
    while time.monotonic() < end:
        t = time.monotonic()
        app.update()
        worst = max(worst, time.monotonic() - t)
        if until():
            return worst
        time.sleep(0.01)
    raise AssertionError("délai dépassé")


def idle(app):
    return app.job is not None and not app.job.running and app.job.messages.empty() \
        and app.main_button.cget("text") == "Télécharger"


def test_download_keeps_ui_responsive(app, server):
    app.url_entry.insert("1.0", server.url("/sites/slow.html"))
    app.main_button.invoke()
    assert app.main_button.cget("text") == "Annuler"
    assert app.open_button.cget("state") == "disabled"
    seen_pages = set()
    ticks = []

    def tick():  # minuterie Tk : prouve que la boucle d'événements tourne pendant le travail
        ticks.append(time.monotonic())
        if app.job and app.job.running:
            app.after(50, tick)
    app.after(50, tick)

    def done():
        seen_pages.add(app.pages_label.cget("text"))
        return idle(app)
    worst = pump(app, done)
    assert worst < 0.5, f"interface bloquée {worst:.2f}s"
    gaps = [b - a for a, b in zip(ticks, ticks[1:])]
    assert len(ticks) > 20 and max(gaps) < 0.6
    assert app.status_label.cget("text") == "Terminé"
    assert app.pages_label.cget("text") == "Pages : 8 / 8"
    assert any(t.startswith("Pages : ") and t != "Pages : 8 / 8" for t in seen_pages)  # progression visible
    assert app.progress.get() == pytest.approx(1.0)
    assert app.error_label.cget("text") == ""
    assert app.open_button.cget("state") == "normal"
    app.open_button.invoke()
    assert app.opened == [app.output] and app.output.suffix == ".cbz" and app.output.is_file()


def test_images_format(app, server, tmp_path):
    app.url_entry.insert("1.0", server.url("/sites/vertical.html"))
    app.format_var.set("Images")
    app.main_button.invoke()
    pump(app, lambda: idle(app))
    assert app.output.is_dir() and len(list(app.output.glob("[0-9]*.*"))) == 8


def test_invalid_url_shows_error_without_job(app):
    app.url_entry.insert("1.0", "pas une adresse")
    app.main_button.invoke()
    assert app.job is None
    assert "http" in app.error_label.cget("text")


def test_engine_error_is_shown(app, server):
    app.url_entry.insert("1.0", server.url("/sites/empty.html"))
    app.main_button.invoke()
    pump(app, lambda: idle(app))
    assert app.status_label.cget("text") == "Échec"
    assert "Aucune planche" in app.error_label.cget("text")
    assert app.open_button.cget("state") == "disabled"
    assert app.url_entry.cget("state") == "normal"


def test_incomplete_chapter_message(server, tmp_path):
    a = App(config_factory=lambda: fast_config(tmp_path, max_retries=0), opener=lambda p: None)
    try:
        a.url_entry.insert("1.0", server.url("/sites/gone.html"))
        a.main_button.invoke()
        pump(a, lambda: idle(a))
        assert a.status_label.cget("text") == "Incomplet"
        assert "planche 3" in a.error_label.cget("text")
        assert a.open_button.cget("state") == "normal"
    finally:
        a.destroy()


def test_cancel_button(app, server):
    app.url_entry.insert("1.0", server.url("/sites/paged_infinite.html"))
    app.main_button.invoke()
    pump(app, lambda: app.pages_label.cget("text").startswith("Pages : 3"))
    app.main_button.invoke()  # « Annuler »
    pump(app, lambda: idle(app), timeout=30)
    assert app.status_label.cget("text") == "Annulé"
    assert app.main_button.cget("state") == "normal"


def test_close_window_during_download(server, tmp_path):
    a = App(config_factory=lambda: fast_config(tmp_path), opener=lambda p: None)
    a.url_entry.insert("1.0", server.url("/sites/paged_infinite.html"))
    a.main_button.invoke()
    pump(a, lambda: a.pages_label.cget("text").startswith("Pages : 2"))
    job = a.job
    a.on_close()
    end = time.monotonic() + 20
    while time.monotonic() < end:
        try:
            a.update()
        except tk.TclError:
            break  # fenêtre détruite
        time.sleep(0.02)
    assert job.join(10), "le moteur doit s'arrêter avec la fenêtre"
    with pytest.raises(tk.TclError):
        a.winfo_exists()


def test_several_links_one_after_another(app, server):
    links = [server.url("/sites/vertical.html"), server.url("/sites/empty.html"), server.url("/sites/paged_button.html")]
    app.url_entry.insert("1.0", "\n".join(links) + "\n")
    app.main_button.invoke()
    statuses = set()
    pump(app, lambda: statuses.add(app.status_label.cget("text")) or (idle(app) and not app.pending))
    assert any(s.startswith("Lien 3 / 3") for s in statuses)
    assert app.status_label.cget("text") == "Terminé avec des erreurs"
    assert app.pages_label.cget("text") == "Chapitres : 2 / 3"
    assert app.error_label.cget("text").startswith("Lien 2 : Aucune planche")
    assert len(list(app.output.parent.glob("*.cbz"))) == 2
    assert app.open_button.cget("state") == "normal"


def test_cancel_stops_remaining_links(app, server):
    app.url_entry.insert("1.0", server.url("/sites/paged_infinite.html") + "\n" + server.url("/sites/vertical.html"))
    app.main_button.invoke()
    pump(app, lambda: app.pages_label.cget("text").startswith("Pages : 2"))
    app.main_button.invoke()
    pump(app, lambda: idle(app), timeout=30)
    assert app.status_label.cget("text") == "Annulé"
    assert len(app.results) == 1 and app.pending == []  # le 2e lien n'a jamais démarré
