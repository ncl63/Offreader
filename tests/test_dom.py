"""Tests unitaires du tri planches / parasites (sans navigateur)."""

from offreader.dom import ImageInfo, normalize_url, select_pages


def img(id, url, w=800, h=1200, top=0, path="div.reader", own="", anc="", complete=True, hidden=False, dom=None, left=240):
    return ImageInfo(id=str(id), dom=dom if dom is not None else int(id), url=url, natural_w=w if complete else 0,
                     natural_h=h if complete else 0, render_w=w, render_h=h, attr_w=0, attr_h=0, top=top,
                     left=left, complete=complete, broken=False, hidden=hidden, in_viewport=True, path=path,
                     own=own, ancestors=anc)


def urls(pages):
    return [p.url for p in pages]


def test_keeps_main_group_in_order():
    images = [
        img(1, "http://x/logo.png", 180, 50, top=0, path="header"),
        img(3, "http://x/2.jpg", top=1300), img(2, "http://x/1.jpg", top=100),
        img(4, "http://x/3.jpg", top=2500),
    ]
    assert urls(select_pages(images, 1280)) == ["http://x/1.jpg", "http://x/2.jpg", "http://x/3.jpg"]


def test_thumbnail_sidebar_loses_to_reader():
    pages = [img(i, f"http://x/p{i}.jpg", 400, 600, top=i * 600) for i in range(1, 4)]
    thumbs = [img(10 + i, f"http://x/t{i}.jpg", 200, 300, top=i * 300, path="aside.related", left=1000)
              for i in range(12)]
    assert urls(select_pages(pages + thumbs, 1280)) == [f"http://x/p{i}.jpg" for i in range(1, 4)]


def test_pending_images_of_same_structure_are_kept():
    images = [img(1, "http://x/1.jpg", top=0), img(2, "http://x/2.jpg", complete=False, top=1200),
              img(3, "http://x/3.jpg", top=1300)]
    assert len(select_pages(images, 1280)) == 3


def test_duplicate_urls_removed_distinct_kept():
    images = [img(1, "http://x/1.jpg", top=0), img(2, "http://x/1.jpg#dup", top=1200),
              img(3, "http://x/2.jpg?v=1", top=2400), img(4, "http://x/2.jpg?v=2", top=3600)]
    assert urls(select_pages(images, 1280)) == ["http://x/1.jpg", "http://x/2.jpg?v=1", "http://x/2.jpg?v=2"]


def test_banner_inside_reader_excluded():
    images = [img(1, "http://x/1.jpg", top=0), img(2, "http://x/ban.jpg", 728, 250, top=1200),
              img(3, "http://x/2.jpg", top=1500), img(4, "http://x/ad.jpg", own="ad", top=2700)]
    assert urls(select_pages(images, 1280)) == ["http://x/1.jpg", "http://x/2.jpg"]


def test_hidden_images_ignored():
    images = [img(1, "http://x/1.jpg"), img(2, "http://x/2.jpg", hidden=True, top=1200)]
    assert urls(select_pages(images, 1280)) == ["http://x/1.jpg"]


def test_normalize_url():
    assert normalize_url("http://a/b.jpg?x=1#frag") == "http://a/b.jpg?x=1"
