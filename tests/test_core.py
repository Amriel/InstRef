"""Юніт-тести логіки, яку можна перевірити без Instagram і без GUI.

Запуск:  .venv\\Scripts\\python.exe -m pytest tests -q
"""

from __future__ import annotations

import json
import sys
import types
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from igsaved.config import (  # noqa: E402
    Config, DEFAULT_TEMPLATE, SCHED_DAILY, STRUCTURE_PER_COLLECTION,
)
from igsaved.eagle import EagleClient, EagleItem  # noqa: E402
from igsaved.instagram import collect_assets, label_for, media_to_dict, media_url  # noqa: E402
from igsaved.naming import (  # noqa: E402
    asset_name, base_name, caption_slug, ext_from_url, hashtags, render_template,
    safe_component, short_title, unique_path,
)
from igsaved.tagging import MediaTags, apply as apply_tags  # noqa: E402
from igsaved import status as run_status  # noqa: E402
from igsaved.session import CookieResult, explain, find_sessionid, normalize_sessionid  # noqa: E402
from igsaved.state import State  # noqa: E402


# ----------------------------------------------------------------- naming
def test_safe_component_strips_illegal_windows_chars():
    assert safe_component('my: "best" <ref>/pack?') == "my_ _best_ _ref__pack_"
    assert safe_component("  trailing dots... ") == "trailing dots"
    assert safe_component("") == "unnamed"
    assert safe_component("CON").startswith("_")
    assert len(safe_component("x" * 300)) <= 80


def test_base_and_asset_names():
    taken = datetime(2026, 8, 20, 12, 30, tzinfo=timezone.utc)
    base = base_name(taken, "some.user", "AbC123")
    assert base == "2026-08-20_some.user_AbC123"
    assert asset_name(base, None, ".mp4") == f"{base}.mp4"
    assert asset_name(base, 3, "jpg") == f"{base}_03.jpg"


def test_ext_from_url_handles_query_strings():
    assert ext_from_url("https://cdn/x/video.mp4?efg=abc&oh=1") == ".mp4"
    assert ext_from_url("https://cdn/x/pic.jpeg?x=1") == ".jpg"
    assert ext_from_url("https://cdn/x/weird") == ".jpg"
    assert ext_from_url("https://cdn/x/weird", ".mp4") == ".mp4"


def test_hashtags_and_title():
    caption = "Дивись це #Reference #design #design #3d_model отут"
    assert hashtags(caption) == ["reference", "design", "3d_model"]
    assert short_title(caption, "u", "C") == "Дивись це #Reference #design #design #3d_model отут"
    assert short_title("", "bob", "XY") == "@bob · XY"
    assert short_title("x" * 200, "u", "C").endswith("…")


def test_caption_slug_picks_the_readable_part():
    caption = (
        "Знімали цей таймлапс три ночі поспіль 🌙\n"
        "Вітер зривав штатив, але воно того варте.\n\n"
        "#таймлапс #київ @studioalt"
    )
    assert caption_slug(caption) == "Знімали цей таймлапс три ночі поспіль"


def test_caption_slug_skips_hashtag_only_first_line():
    caption = "#reference #moodboard\nМʼяке світло у ранковій кухні"
    assert caption_slug(caption) == "Мʼяке світло у ранковій кухні"


def test_caption_slug_falls_back_when_only_hashtags():
    slug = caption_slug("#minimal #interior #design")
    assert slug and "#" not in slug


def test_caption_slug_handles_empty_and_junk():
    assert caption_slug("") == ""
    assert caption_slug("   \n\n  ") == ""
    assert caption_slug("🔥🔥🔥") == ""          # самі емодзі — нема чого брати
    assert caption_slug("https://example.com") == ""


def test_caption_slug_cuts_on_word_boundary():
    caption = "Перший рядок дуже довгий і має бути обрізаний десь на межі слова точно"
    slug = caption_slug(caption, limit=30)
    assert len(slug) <= 30
    assert not slug.endswith(" ")
    assert slug in caption or slug == caption[:len(slug)].strip()


def test_caption_slug_is_filesystem_safe():
    slug = caption_slug('Тест: "лапки" / слеш \\ і <кутики>')
    assert not set(slug) & set('<>:"/\\|?*')


def test_render_template_title_token():
    taken = datetime(2026, 8, 21, tzinfo=timezone.utc)
    caption = "Мʼяке світло у ранковій кухні #interior"
    out = render_template("{date}_{user}_{title}", taken, "formnorm", "C9wZa1", caption=caption)
    assert out == "2026-08-21_formnorm_Мʼяке світло у ранковій кухні"


def test_render_template_title_falls_back_to_code_without_caption():
    taken = datetime(2026, 8, 21, tzinfo=timezone.utc)
    out = render_template("{date}_{user}_{title}", taken, "formnorm", "C9wZa1", caption="")
    assert out == "2026-08-21_formnorm_C9wZa1"


def test_render_template_collapses_gaps_from_empty_tokens():
    taken = datetime(2026, 8, 21, tzinfo=timezone.utc)
    out = render_template("{collection}_{user}_{title}", taken, "bob", "XY", caption="")
    assert not out.startswith("_")
    assert "__" not in out


def test_render_template_tokens():
    taken = datetime(2026, 8, 20, 14, 5, tzinfo=timezone.utc)
    out = render_template("{date}_{time}_{user}_{type}", taken, "bob", "XY", kind="reel")
    assert out == "2026-08-20_14-05_bob_reel"
    assert render_template(DEFAULT_TEMPLATE, taken, "bob", "XY") == "2026-08-20_bob_XY"
    assert render_template("{collection}-{code}", taken, "b", "XY", collection="My Refs") \
        == "My Refs-XY"


def test_render_template_handles_missing_data_and_junk():
    # немає дати й автора
    assert render_template("{date}_{user}", None, "", "XY") == "0000-00-00_unknown"
    # шаблон із забороненими символами очищується
    assert render_template("a/b:{code}", None, "u", "XY") == "a_b_XY"
    # порожній шаблон відкочується на типовий
    assert render_template("", None, "u", "XY") == "0000-00-00_u_XY"


def test_render_template_is_windows_safe_and_bounded():
    taken = datetime(2026, 1, 2, tzinfo=timezone.utc)
    out = render_template("{user}", taken, "x" * 300, "C")
    assert len(out) <= 120  # запас під читабельний {title}, але в межах шляху Windows
    assert not set(out) & set('<>:"/\\|?*')

    # найдовший реалістичний випадок: довгий підпис + довгий нік
    long_out = render_template(
        "{date}_{user}_{title}", taken, "a" * 40, "C9xKp2",
        caption="Дуже довгий підпис " * 20,
    )
    assert len(long_out) <= 120


def test_unique_path(tmp_path):
    first = tmp_path / "a.mp4"
    first.write_bytes(b"1")
    assert unique_path(first).name == "a_2.mp4"


def test_normalize_sessionid_accepts_cookie_header():
    assert normalize_sessionid(' sessionid=abc%3A123; csrftoken=zz ') == "abc%3A123"
    assert normalize_sessionid('"plain"') == "plain"


# ------------------------------------------------------------------ media
class FakeUser:
    def __init__(self, username="tester"):
        self.username = username
        self.full_name = "Test Er"
        self.pk = "999"


class FakeResource:
    def __init__(self, media_type, video_url=None, thumbnail_url=None):
        self.media_type = media_type
        self.video_url = video_url
        self.thumbnail_url = thumbnail_url


class FakeMedia:
    def __init__(self, **kw):
        self.pk = kw.get("pk", "111")
        self.code = kw.get("code", "CoDe1")
        self.media_type = kw.get("media_type", 2)
        self.product_type = kw.get("product_type", "clips")
        self.taken_at = kw.get("taken_at", datetime(2026, 8, 1, tzinfo=timezone.utc))
        self.user = FakeUser()
        self.caption_text = kw.get("caption", "hello #tag")
        self.video_url = kw.get("video_url", "https://cdn/v.mp4?x=1")
        self.thumbnail_url = kw.get("thumbnail_url", "https://cdn/t.jpg?x=1")
        self.resources = kw.get("resources", [])
        self.like_count = 5
        self.view_count = 50
        self.play_count = 60
        self.comment_count = 2
        self.video_duration = 12.5
        self.clips_metadata = None


def test_collect_assets_reel():
    assets = collect_assets(FakeMedia(), True, True, True)
    kinds = [(a.kind, a.index) for a in assets]
    assert kinds == [("video", None), ("thumb", None)]


def test_collect_assets_reel_without_thumbnails():
    assets = collect_assets(FakeMedia(), True, True, False)
    assert [a.kind for a in assets] == ["video"]


def test_collect_assets_carousel_mixed():
    media = FakeMedia(
        media_type=8,
        resources=[
            FakeResource(1, thumbnail_url="https://cdn/p1.jpg"),
            FakeResource(2, video_url="https://cdn/v2.mp4", thumbnail_url="https://cdn/t2.jpg"),
        ],
    )
    assets = collect_assets(media, True, True, False)
    assert [(a.kind, a.index) for a in assets] == [("photo", 1), ("video", 2)]

    photos_only = collect_assets(media, False, True, False)
    assert [(a.kind, a.index) for a in photos_only] == [("photo", 1)]


def test_label_and_url():
    assert label_for(FakeMedia()) == "reel"
    assert label_for(FakeMedia(media_type=1, product_type="feed")) == "photo"
    assert label_for(FakeMedia(media_type=8)) == "carousel"
    assert media_url("AbC") == "https://www.instagram.com/p/AbC/"


def test_media_to_dict_is_json_serializable():
    payload = media_to_dict(FakeMedia(), ["Refs"])
    assert payload["author"]["username"] == "tester"
    assert payload["collections"] == ["Refs"]
    json.dumps(payload, ensure_ascii=False)  # не має кидати


# ------------------------------------------------------------------ state
def test_state_roundtrip(tmp_path):
    state = State(tmp_path / "s.db")
    try:
        assert state.is_downloaded("1") is False
        state.record_media("1", "C", "u", datetime.now(timezone.utc), 2, "clips", "cap", "url")
        assert state.is_downloaded("1") is False  # ще pending
        state.mark_done("1")
        assert state.is_downloaded("1") is True

        assert state.files_exist("1") is False  # файлів ще не записано
        media_file = tmp_path / "f.mp4"
        media_file.write_bytes(b"data")
        state.add_file(str(media_file), "1", "video", 0, 4)
        assert state.files_exist("1") is True
        media_file.unlink()
        assert state.files_exist("1") is False  # файл прибрали вручну

        state.add_membership("1", "COL", "Refs")
        assert state.collection_names_for("1") == ["Refs"]
        state.upsert_collection("COL", "Refs", 10)
        assert state.collection_downloaded_count("COL") == 1

        assert state.is_in_eagle("1", "COL") is False
        state.mark_in_eagle("1", "COL", "FID")
        assert state.is_in_eagle("1", "COL") is True

        run = state.start_run()
        state.finish_run(run, 5, 1, 4, 0, "ok")
        assert state.last_runs()[0]["scanned"] == 5
    finally:
        state.close()


def test_state_survives_reopen(tmp_path):
    path = tmp_path / "s.db"
    first = State(path)
    first.record_media("7", "C", "u", None, 1, "feed", "", "")
    first.mark_done("7")
    first.close()
    second = State(path)
    try:
        assert second.is_downloaded("7") is True
    finally:
        second.close()


def test_state_is_thread_safe(tmp_path):
    state = State(tmp_path / "s.db")
    errors = []

    def worker(start):
        try:
            for i in range(start, start + 40):
                state.record_media(str(i), "C", "u", None, 2, "clips", "", "")
                state.mark_done(str(i))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n * 100,)) for n in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    state_totals = state.totals()
    state.close()
    assert not errors
    assert state_totals["media"] == 160


# ------------------------------------------------------------------ config
def test_config_roundtrip(tmp_path):
    path = tmp_path / "config.json"
    cfg = Config()
    cfg.download_dir = str(tmp_path / "dl")
    cfg.enabled_collections = ["1", "2"]
    cfg.page_delay_min = 3.5
    cfg.save(path)

    again = Config.load(path)
    assert again.download_dir == str(tmp_path / "dl")
    assert again.enabled_collections == ["1", "2"]
    assert again.page_delay_min == 3.5


def test_config_ignores_unknown_and_broken_json(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"download_videos": false, "who_is_this": 1}', encoding="utf-8")
    cfg = Config.load(path)
    assert cfg.download_videos is False
    assert not hasattr(cfg, "who_is_this")

    path.write_text("{not json", encoding="utf-8")
    assert Config.load(path).download_videos is True  # тихо повертає дефолти


def test_config_target_dir_modes(tmp_path):
    cfg = Config()
    cfg.download_dir = str(tmp_path)
    assert cfg.target_dir("Refs") == tmp_path
    cfg.structure = STRUCTURE_PER_COLLECTION
    assert cfg.target_dir("Ref: pack/1") == tmp_path / "Ref_ pack_1"


def test_config_wants_respects_toggles():
    cfg = Config()
    cfg.download_videos = False
    assert cfg.wants(2) is False
    assert cfg.wants(1) is True
    cfg.download_photos = False
    assert cfg.wants(8) is False


# ------------------------------------------------------------------ Eagle
class _EagleHandler(BaseHTTPRequestHandler):
    """Мінімальний фейковий Eagle для перевірки клієнта."""

    folders = [{"id": "ROOT1", "name": "Instagram Saved", "children": [
        {"id": "SUB1", "name": "Refs", "children": []}
    ]}]
    added: list = []
    created: list = []

    def log_message(self, *args):  # тиша в тестах
        pass

    def _send(self, payload, code=200):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/api/application/info"):
            self._send({"status": "success", "data": {"version": "4.0.0"}})
        elif self.path.startswith("/api/folder/list"):
            self._send({"status": "success", "data": self.folders})
        else:
            self._send({"status": "error", "message": "nope"}, 404)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        if self.path.startswith("/api/folder/create"):
            new_id = f"NEW{len(self.created)}"
            self.created.append(payload)
            self._send({"status": "success", "data": {"id": new_id, "name": payload["folderName"]}})
        elif self.path.startswith("/api/item/addFromPaths"):
            self.added.append(payload)
            self._send({"status": "success"})
        else:
            self._send({"status": "error", "message": "nope"}, 404)


def _serve():
    server = HTTPServer(("127.0.0.1", 0), _EagleHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}"


def test_eagle_client_against_fake_server():
    _EagleHandler.added.clear()
    _EagleHandler.created.clear()
    server, url = _serve()
    try:
        client = EagleClient(url)
        assert client.ping()["version"] == "4.0.0"

        # існуючу кореневу папку знаходить, а не створює
        root = client.ensure_folder("Instagram Saved")
        assert root == "ROOT1"
        assert _EagleHandler.created == []

        # існуючу підпапку теж
        assert client.ensure_folder("Refs", "ROOT1") == "SUB1"

        # нову — створює під потрібним батьком
        client.invalidate_folders()
        new_id = client.ensure_folder("Travel", "ROOT1")
        assert new_id.startswith("NEW")
        assert _EagleHandler.created[0] == {"folderName": "Travel", "parent": "ROOT1"}

        sent = client.add_items(
            [EagleItem(path="C:/x/a.mp4", name="A", website="https://ig/p/1",
                       annotation="cap", tags=["instagram", "@user"])],
            new_id,
        )
        assert sent == 1
        body = _EagleHandler.added[0]
        assert body["folderId"] == new_id
        assert body["items"][0]["path"] == "C:/x/a.mp4"
        assert body["items"][0]["tags"] == ["instagram", "@user"]
    finally:
        server.shutdown()


def test_eagle_client_reports_offline():
    client = EagleClient("http://127.0.0.1:9")  # закритий порт
    try:
        client.ping()
    except Exception as exc:  # noqa: BLE001
        assert "Eagle" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("мала бути помилка")


def test_eagle_item_payload_trims_empty_fields():
    payload = EagleItem(path="p", name="n").payload()
    assert payload == {"path": "p", "name": "n"}


# --------------------------------------------------------------- downloader
def test_downloader_skips_oversized_files():
    from igsaved.downloader import Downloader, TooLarge

    class _BigHandler(_EagleHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(5 * 1024 * 1024))
            self.end_headers()
            self.wfile.write(b"\0" * 1024)

    server = HTTPServer(("127.0.0.1", 0), _BigHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        loader = Downloader(retries=3, pause=0, max_bytes=1024 * 1024)
        try:
            loader.fetch(f"http://127.0.0.1:{server.server_port}/big.mp4", Path("/tmp/big.mp4"))
        except TooLarge as exc:
            assert "ліміт" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("мав спрацювати ліміт розміру")
        finally:
            loader.close()
    finally:
        server.shutdown()


def test_downloader_writes_file_and_skips_existing(tmp_path):
    from igsaved.downloader import Downloader

    payload = b"hello-bytes" * 100

    class _FileHandler(_EagleHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = HTTPServer(("127.0.0.1", 0), _FileHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        loader = Downloader(retries=1, pause=0)
        dest = tmp_path / "sub" / "v.mp4"
        url = f"http://127.0.0.1:{server.server_port}/v.mp4"
        first = loader.fetch(url, dest)
        assert first.size == len(payload) and dest.read_bytes() == payload
        assert first.skipped is False
        assert loader.fetch(url, dest).skipped is True  # вдруге не качає
        assert not list(dest.parent.glob("*.part"))     # тимчасові файли прибрано
        loader.close()
    finally:
        server.shutdown()


# ------------------------------------------------------- метадані у файлі
SAMPLE_CAPTION = (
    "Знімали цей таймлапс три ночі поспіль 🌙\n"
    "Вітер зривав штатив, але воно того варте.\n\n#таймлапс #київ"
)


def _sample_tags() -> MediaTags:
    return MediaTags(
        title=caption_slug(SAMPLE_CAPTION),
        author="studioalt",
        author_full="Studio Alt",
        caption=SAMPLE_CAPTION,
        url="https://www.instagram.com/p/C9xKp2/",
        taken_at=datetime(2026, 8, 21, 23, 14, tzinfo=timezone.utc),
        kind="reel",
        collections=["Refs / motion"],
        hashtags=["таймлапс", "київ"],
    )


def _xp(value) -> str:
    """XP-теги Windows: UTF-16LE із нульовим термінатором у кінці."""
    return bytes(value).decode("utf-16-le").rstrip("\x00")


def _make_jpeg(path: Path) -> Path:
    from PIL import Image

    Image.new("RGB", (64, 48), (120, 80, 100)).save(path, quality=80)
    return path


def test_tags_comment_carries_caption_author_and_link():
    tags = _sample_tags()
    comment = tags.comment()
    assert "Вітер зривав штатив" in comment
    assert "Посилання: https://www.instagram.com/p/C9xKp2/" in comment
    assert "Автор: @studioalt" in comment
    assert tags.artist == "@studioalt"
    assert tags.year == "2026-08-21"


def test_tags_comment_without_caption_still_has_the_link():
    tags = _sample_tags()
    tags.caption = ""
    comment = tags.comment()
    assert comment.startswith("Автор:")
    assert "https://www.instagram.com/p/C9xKp2/" in comment
    assert not comment.startswith("\n")


def test_tags_keywords_dedupe_and_merge_collections():
    tags = _sample_tags()
    tags.hashtags = ["київ", "Київ", "ніч"]
    keywords = tags.keywords()
    assert keywords.count("київ") == 1  # без урахування регістру
    assert "Refs / motion" in keywords


def test_embed_into_jpeg_is_readable_by_windows_fields(tmp_path):
    piexif = pytest.importorskip("piexif")
    pytest.importorskip("PIL")
    path = _make_jpeg(tmp_path / "shot.jpg")

    ok, problem = apply_tags(path, _sample_tags())
    assert ok, problem

    exif = piexif.load(str(path))
    zeroth = exif["0th"]
    assert _xp(zeroth[piexif.ImageIFD.XPTitle]) == "Знімали цей таймлапс три ночі поспіль"
    assert _xp(zeroth[piexif.ImageIFD.XPAuthor]) == "@studioalt"
    assert "Вітер зривав штатив" in _xp(zeroth[piexif.ImageIFD.XPComment])
    assert "київ" in _xp(zeroth[piexif.ImageIFD.XPKeywords])
    assert bytes(zeroth[piexif.ImageIFD.Copyright]).decode("utf-8").endswith("/C9xKp2/")
    assert bytes(exif["Exif"][piexif.ExifIFD.DateTimeOriginal]).decode() == "2026:08:21 23:14:00"

    # найголовніше: файл лишився картинкою
    from PIL import Image

    image = Image.open(path)
    image.load()
    assert image.size == (64, 48)


def test_embed_into_jpeg_is_idempotent(tmp_path):
    pytest.importorskip("piexif")
    pytest.importorskip("PIL")
    path = _make_jpeg(tmp_path / "shot.jpg")
    assert apply_tags(path, _sample_tags())[0] is True
    assert apply_tags(path, _sample_tags())[0] is True  # повторно — без падінь

    from PIL import Image

    Image.open(path).load()


def test_embed_skips_formats_without_tag_support(tmp_path):
    path = tmp_path / "thing.webp"
    path.write_bytes(b"not really a webp")
    ok, problem = apply_tags(path, _sample_tags())
    assert ok is False
    assert problem == ""  # мовчки, без скарг у журнал


def test_embed_never_raises_on_broken_file(tmp_path):
    """Пошкоджений mp4 не має валити синхронізацію — лише повернути причину."""
    path = tmp_path / "broken.mp4"
    path.write_bytes(b"\x00\x01\x02 not a real mp4")
    ok, problem = apply_tags(path, _sample_tags())
    assert ok is False
    assert problem  # є що написати в журнал


def test_embed_into_mp4_round_trips(tmp_path):
    """Справжній mp4 збираємо через ffmpeg; без нього — тест пропускається."""
    import shutil as _shutil
    import subprocess

    if not _shutil.which("ffmpeg"):
        pytest.skip("ffmpeg недоступний")
    mutagen_mp4 = pytest.importorskip("mutagen.mp4")

    path = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "color=c=teal:s=160x120:d=1", "-c:v", "libx264",
         "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )

    ok, problem = apply_tags(path, _sample_tags())
    assert ok, problem

    handle = mutagen_mp4.MP4(str(path))
    assert handle.tags["\xa9nam"] == ["Знімали цей таймлапс три ночі поспіль"]
    assert handle.tags["\xa9ART"] == ["@studioalt"]
    assert handle.tags["aART"] == ["Studio Alt"]
    assert handle.tags["\xa9day"] == ["2026-08-21"]
    assert handle.tags["\xa9gen"] == ["Instagram reel"]
    assert "Вітер зривав штатив" in handle.tags["\xa9cmt"][0]
    # посилання має лежати в кількох місцях — різні програми читають різні поля
    url = "https://www.instagram.com/p/C9xKp2/"
    assert bytes(handle.tags["----:com.apple.iTunes:URL"][0]).decode() == url
    assert bytes(handle.tags["----:com.apple.iTunes:Instagram"][0]).decode() == url
    assert handle.tags["purl"] == [url]
    assert url in handle.tags["\xa9cmt"][0]
    # відео лишилось читабельним для декодера
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True,
    )
    assert probe.returncode == 0 and float(probe.stdout.strip()) > 0


# --------------------------------------------------- захист від збігів імен
def test_state_path_owner_detects_foreign_file(tmp_path):
    state = State(tmp_path / "s.db")
    try:
        state.add_file(str(tmp_path / "a.mp4"), "111", "video", 0, 10)
        assert state.path_owner(str(tmp_path / "a.mp4")) == "111"
        assert state.path_owner(str(tmp_path / "nope.mp4")) is None
    finally:
        state.close()


# ------------------------------------- перейменування вже завантаженого
def _seed_library(tmp_path, caption="Мʼяке світло у ранковій кухні"):
    """Імітує бібліотеку, скачану старою версією: імена з кодом поста."""
    from PIL import Image

    cfg = Config()
    cfg.download_dir = str(tmp_path / "dl")
    cfg.root.mkdir(parents=True, exist_ok=True)
    cfg.thumbs_dir.mkdir(parents=True, exist_ok=True)
    cfg.meta_dir.mkdir(parents=True, exist_ok=True)

    old_base = "2026-06-09_formnorm_DZXi35noMC8"
    media = cfg.root / f"{old_base}.jpg"
    Image.new("RGB", (48, 32), (90, 110, 130)).save(media, quality=70)
    (cfg.thumbs_dir / f"{old_base}.jpg").write_bytes(b"thumb")
    (cfg.meta_dir / f"{old_base}.json").write_text("{}", encoding="utf-8")

    state = State(tmp_path / "s.db")
    state.record_media("777", "DZXi35noMC8", "formnorm",
                       datetime(2026, 6, 9, tzinfo=timezone.utc), 1, "feed",
                       caption, "https://www.instagram.com/p/DZXi35noMC8/")
    state.mark_done("777")
    state.add_file(str(media), "777", "photo", 0, media.stat().st_size)
    state.add_membership("777", "COL", "Refs")
    return cfg, state, media, old_base


def test_refresh_library_renames_and_tags(tmp_path):
    pytest.importorskip("PIL")
    pytest.importorskip("piexif")
    from igsaved.maintenance import refresh_library

    cfg, state, old_media, old_base = _seed_library(tmp_path)
    try:
        stats = refresh_library(cfg, state, log=lambda _m: None)

        new_media = cfg.root / "2026-06-09_formnorm_Мʼяке світло у ранковій кухні.jpg"
        assert new_media.exists()
        assert not old_media.exists()
        assert stats.renamed == 1 and stats.tagged == 1 and stats.failed == 0

        # база вказує на новий шлях, інакше наступний запуск перекачає файл
        assert state.media_files("777") == [str(new_media)]

        # супутні файли поїхали слідом
        assert (cfg.thumbs_dir / f"{new_media.stem}.jpg").exists()
        assert (cfg.meta_dir / f"{new_media.stem}.json").exists()
        assert not (cfg.thumbs_dir / f"{old_base}.jpg").exists()

        # і теги справді всередині
        import piexif

        exif = piexif.load(str(new_media))
        assert _xp(exif["0th"][piexif.ImageIFD.XPAuthor]) == "@formnorm"
    finally:
        state.close()


def test_refresh_library_is_idempotent(tmp_path):
    pytest.importorskip("PIL")
    from igsaved.maintenance import refresh_library

    cfg, state, _old, _base = _seed_library(tmp_path)
    try:
        refresh_library(cfg, state, log=lambda _m: None)
        second = refresh_library(cfg, state, log=lambda _m: None)
        assert second.renamed == 0      # уже має правильну назву
        assert second.skipped == 1
    finally:
        state.close()


def test_refresh_library_keeps_going_without_caption(tmp_path):
    pytest.importorskip("PIL")
    from igsaved.maintenance import refresh_library

    cfg, state, old_media, _base = _seed_library(tmp_path, caption="")
    try:
        stats = refresh_library(cfg, state, log=lambda _m: None)
        # без підпису ім'я лишається на коді поста — тобто не змінюється
        assert old_media.exists()
        assert stats.renamed == 0 and stats.failed == 0
    finally:
        state.close()


def test_refresh_library_on_empty_library_says_so(tmp_path):
    from igsaved.maintenance import refresh_library

    cfg = Config()
    cfg.download_dir = str(tmp_path / "empty")
    state = State(tmp_path / "s.db")
    lines = []
    try:
        stats = refresh_library(cfg, state, log=lines.append)
        assert stats.seen == 0
        assert any("Нема чого оновлювати" in line for line in lines)
    finally:
        state.close()


def test_config_migrates_legacy_template(tmp_path):
    """Старий конфіг не має залишати користувача зі старими назвами."""
    path = tmp_path / "config.json"
    path.write_text('{"filename_template": "{date}_{user}_{code}"}', encoding="utf-8")
    assert Config.load(path).filename_template == DEFAULT_TEMPLATE


def test_config_keeps_custom_template(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"filename_template": "{user}-{code}"}', encoding="utf-8")
    assert Config.load(path).filename_template == "{user}-{code}"


# ------------------------------------------------- класифікація пролайканого
def _post(caption="", username="someone", full_name="", duration=None,
          paid=False, sponsors=(), verified=False, pk=None):
    media = FakeMedia(caption=caption)
    if pk is not None:
        media.pk = str(pk)
    media.user = FakeUser(username)
    media.user.full_name = full_name
    media.user.is_verified = verified
    media.video_duration = duration
    media.is_paid_partnership = paid
    media.sponsor_tags = list(sponsors)
    return media


def test_classifier_skips_obvious_memes():
    from igsaved.classify import SKIP, classify

    verdict = classify(_post(caption="коли дедлайн завтра #мем #прикол"))
    assert verdict.decision == SKIP
    assert "мем-хештеги" in verdict.why()


def test_classifier_skips_meme_accounts():
    from igsaved.classify import SKIP, classify

    assert classify(_post(username="daily_memes_ua")).decision == SKIP


def test_classifier_downloads_art():
    from igsaved.classify import DOWNLOAD, classify

    verdict = classify(_post(caption="new loop #c4d #octane #motiondesign"))
    assert verdict.decision == DOWNLOAD
    assert verdict.art_score >= verdict.meme_score


def test_classifier_downloads_ads_even_without_art_tags():
    from igsaved.classify import DOWNLOAD, classify

    assert classify(_post(caption="новий ролик", paid=True)).decision == DOWNLOAD
    assert classify(_post(caption="x", sponsors=["brand"])).decision == DOWNLOAD
    assert classify(_post(caption="дивіться #реклама")).decision == DOWNLOAD


def test_gaming_post_goes_to_review_not_download():
    """Реальний промах: ігровий ролик качався без ревʼю.

    Причина була в підрядковому пошуку — «art» сидить усередині gamestart,
    spartan, parts, і цього вистачало, щоб визнати пост артом.
    """
    from igsaved.classify import DOWNLOAD, REVIEW, classify

    for username in ("gamestart", "spartan_plays", "parts_gaming", "cartoonz"):
        verdict = classify(_post(caption="нове проходження, гляньте", username=username))
        assert verdict.decision == REVIEW, f"{username}: {verdict.why()}"
        assert verdict.art_score == 0


def test_account_name_alone_is_not_enough_to_download():
    """Схоже ім'я акаунта — привід показати користувачу, а не вирішити за нього."""
    from igsaved.classify import DOWNLOAD, REVIEW, classify

    only_name = classify(_post(caption="без хештегів", username="alt_studio"))
    assert only_name.art_score == 1
    assert only_name.decision == REVIEW

    # а от арт-хештеги — вагома ознака
    with_tags = classify(_post(caption="#c4d #octane", username="alt_studio"))
    assert with_tags.decision == DOWNLOAD


def test_name_hit_matches_words_not_substrings():
    from igsaved.classify import ART_ACCOUNT_HINTS, name_hit

    assert name_hit("alt_studio", ART_ACCOUNT_HINTS) == "studio"
    assert name_hit("studio.alt", ART_ACCOUNT_HINTS) == "studio"
    assert name_hit("3d-artist", ART_ACCOUNT_HINTS) in ("3d", "artist")
    # і не чіпляється до випадкових збігів усередині слів
    assert name_hit("gamestart", ART_ACCOUNT_HINTS) is None
    assert name_hit("spartan", ART_ACCOUNT_HINTS) is None
    assert name_hit("smartphone_news", ART_ACCOUNT_HINTS) is None


def test_classifier_sends_ambiguous_to_review():
    from igsaved.classify import REVIEW, classify

    # ні арт-, ні мем-ознак
    assert classify(_post(caption="просто підпис без нічого")).decision == REVIEW
    # ознаки з обох боків урівноважуються
    mixed = classify(_post(caption="#art #meme"))
    assert mixed.decision == REVIEW


def test_account_lists_beat_every_rule():
    from igsaved.classify import DOWNLOAD, SKIP, Rules, classify

    art_meme = _post(caption="#c4d #render #octane", username="goodstudio")
    assert classify(art_meme).decision == DOWNLOAD
    blocked = Rules(block_accounts=["GoodStudio"])           # регістр не важливий
    assert classify(art_meme, blocked).decision == SKIP

    meme = _post(caption="#мем #прикол", username="myfriend")
    assert classify(meme).decision == SKIP
    allowed = Rules(allow_accounts=["@myfriend"])            # @ теж приймається
    assert classify(meme, allowed).decision == DOWNLOAD


def test_short_video_rule_is_opt_in():
    from igsaved.classify import REVIEW, SKIP, Rules, classify

    short = _post(caption="без підпису", duration=6.0)
    assert classify(short).decision == REVIEW          # типово вимкнено
    assert classify(short, Rules(max_meme_seconds=10)).meme_score == 1
    # одного балу мало для відсіву — потрібні дві ознаки
    assert classify(short, Rules(max_meme_seconds=10)).decision == REVIEW
    both = _post(caption="#жиза", duration=6.0)
    assert classify(both, Rules(max_meme_seconds=10)).decision == SKIP


def test_custom_tag_lists_replace_defaults():
    from igsaved.classify import Rules, SKIP, classify

    rules = Rules(meme_tags=["котики"])
    assert classify(_post(caption="#котики #котики2"), rules).meme_score >= 1
    # типовий #мем більше не рахується, бо список замінено
    assert classify(_post(caption="#мем"), rules).decision != SKIP


def test_verdict_labels_are_human_readable():
    from igsaved.classify import classify

    verdict = classify(_post(caption="#мем #прикол"))
    assert verdict.label == "мем"
    assert verdict.why()


# ------------------------------------------- візуальна модель (LM Studio)
def test_vision_url_gets_v1_appended():
    """Реальний промах: LM Studio показує адресу без /v1, і саме її вставляють.

    Без /v1 сервер відповідає 200 із «Unexpected endpoint», список моделей
    приходить порожнім — і виглядає це так, наче нічого не завантажено.
    """
    from igsaved.vision import DEFAULT_URL, VisionClient, normalize_url

    assert normalize_url("http://127.0.0.1:1234") == "http://127.0.0.1:1234/v1"
    assert normalize_url("http://localhost:1234/") == "http://localhost:1234/v1"
    assert normalize_url("localhost:1234") == "http://localhost:1234/v1"
    # вже правильну не чіпаємо
    assert normalize_url("http://localhost:1234/v1") == "http://localhost:1234/v1"
    assert normalize_url("http://localhost:1234/v1/") == "http://localhost:1234/v1"
    # чужий шлях (нативний API або проксі) лишаємо як є
    assert normalize_url("http://host/api/v0") == "http://host/api/v0"
    assert normalize_url("") == DEFAULT_URL
    assert VisionClient("http://127.0.0.1:1234").base_url.endswith("/v1")


def test_vision_parses_plain_json():
    from igsaved.vision import ART, parse_answer

    verdict = parse_answer('{"category": "art", "confidence": 0.9, "why": "3d render"}')
    assert verdict.ok and verdict.category == ART
    assert verdict.confidence == 0.9
    assert verdict.label == "арт"


def test_vision_parses_json_wrapped_in_fences():
    """Моделі люблять обгортати відповідь у ```json — це не має ламати розбір."""
    from igsaved.vision import GAME, parse_answer

    verdict = parse_answer('Sure!\n```json\n{"category":"game","confidence":0.8}\n```')
    assert verdict.ok and verdict.category == GAME


def test_vision_falls_back_to_bare_word():
    from igsaved.vision import MEME, parse_answer

    verdict = parse_answer("This is clearly a meme.")
    assert verdict.category == MEME
    assert verdict.confidence == 0.5


def test_vision_rejects_garbage():
    from igsaved.vision import parse_answer

    assert parse_answer("").ok is False
    assert parse_answer("{broken json").ok is False
    assert parse_answer('{"category": "cooking"}').ok is False
    assert parse_answer('{"category": "art", "confidence": "дуже"}').confidence == 0.0


def test_vision_clamps_confidence():
    from igsaved.vision import parse_answer

    assert parse_answer('{"category":"art","confidence":5}').confidence == 1.0
    assert parse_answer('{"category":"art","confidence":-2}').confidence == 0.0


class _LMStudioHandler(_EagleHandler):
    """Мінімальний фейк LM Studio: /v1/models і /v1/chat/completions."""

    answer = '{"category": "game", "confidence": 0.92, "why": "gameplay footage"}'
    seen: list = []

    def do_GET(self):  # noqa: N802
        if self.path.endswith("/models"):
            self._send({"data": [{"id": "qwen2.5-vl-7b"}, {"id": "llama-3-text"}]})
        else:
            self._send({"error": "nope"}, 404)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        self.seen.append(payload)
        self._send({"choices": [{"message": {"content": self.answer}}]})


def test_vision_client_against_fake_lm_studio():
    from igsaved.vision import GAME, VisionClient

    _LMStudioHandler.seen.clear()
    server = HTTPServer(("127.0.0.1", 0), _LMStudioHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        client = VisionClient(f"http://127.0.0.1:{server.server_port}/v1")
        assert client.list_models() == ["qwen2.5-vl-7b", "llama-3-text"]
        assert client.resolve_model() == "qwen2.5-vl-7b"   # перша завантажена

        verdict = client.classify_image(b"\xff\xd8fake-jpeg", caption="гляньте",
                                        username="someplayer")
        assert verdict.ok and verdict.category == GAME
        assert verdict.confidence == 0.92

        # запит має містити і картинку, і контекст
        sent = _LMStudioHandler.seen[0]
        content = sent["messages"][0]["content"]
        kinds = {part["type"] for part in content}
        assert kinds == {"text", "image_url"}
        image_part = next(p for p in content if p["type"] == "image_url")
        assert image_part["image_url"]["url"].startswith("data:image/jpeg;base64,")
        text_part = next(p for p in content if p["type"] == "text")
        assert "@someplayer" in text_part["text"]
        assert sent["temperature"] == 0
    finally:
        server.shutdown()


def test_vision_reports_offline_server():
    from igsaved.vision import VisionClient, VisionError

    client = VisionClient("http://127.0.0.1:9/v1")
    with pytest.raises(VisionError):
        client.list_models()
    # класифікація не кидає, а повертає помилку — синхронізація має жити далі
    assert client.classify_image(b"x").ok is False


def test_vision_needs_an_image():
    from igsaved.vision import VisionClient

    assert VisionClient().classify_image(b"").error == "немає зображення"


def test_thumbnail_url_falls_back_to_carousel_slide():
    from igsaved.vision import thumbnail_url

    media = FakeMedia(media_type=8)
    media.thumbnail_url = None
    media.resources = [FakeResource(1, thumbnail_url="https://cdn/slide.jpg")]
    assert thumbnail_url(media) == "https://cdn/slide.jpg"


# ----------------------------------------------------------- черга ревʼю
def test_state_review_queue(tmp_path):
    state = State(tmp_path / "s.db")
    try:
        assert state.review_count() == 0
        state.add_review("42", "/x/a.mp4", "/x/t.jpg", "bob", "підпис",
                         "https://ig/p/1", "порівну ознак", "review")
        assert state.review_count() == 1
        row = state.pending_review()[0]
        assert row["username"] == "bob"
        assert row["reason"] == "порівну ознак"

        state.decide_review("42", "kept")
        assert state.review_count() == 0
    finally:
        state.close()


def _fake_feed(count, caption="#мем #прикол"):
    return [_post(caption=caption, username=f"acc_{i}", pk=1000 + i) for i in range(count)]


def _engine_over(monkeypatch, cfg, state, feed):
    """Двигун із підміненою стрічкою і без справжніх завантажень."""
    from igsaved.sync import SyncEngine

    engine = SyncEngine(cfg, state, "sid", log=lambda _m: None)
    monkeypatch.setattr(engine.ig, "iter_media",
                        lambda pk, stop=None, on_page=None: iter(feed))
    downloaded = []
    monkeypatch.setattr(engine, "_process_media",
                        lambda media, col, verdict=None: downloaded.append(media))
    return engine, downloaded


def test_liked_scan_limit_stops_the_endless_feed(tmp_path, monkeypatch):
    """Стрічка лайків із самих мемів має спинятись на ліміті переглянутих.

    Саме тут ламався max_items_per_run: він рахує завантажене, а відсіяні
    меми його не збільшують — тож обхід тягнувся через усю історію.
    """
    from igsaved.instagram import CollectionInfo

    cfg = Config()
    cfg.download_dir = str(tmp_path / "dl")
    cfg.eagle_enabled = False
    cfg.liked_scan_limit = 50
    cfg.max_items_per_run = 10          # не спрацює: нічого не завантажується

    liked = CollectionInfo("liked", "Пролайкане", 0, is_liked=True)
    state = State(tmp_path / "s.db")
    try:
        engine, _ = _engine_over(monkeypatch, cfg, state, _fake_feed(500))
        engine._sync_collection(liked)
        assert engine.stats.scanned == 50
        assert engine.stats.filtered == 50     # усі відсіяні як меми
        assert engine.stats.downloaded == 0
    finally:
        state.close()


def test_without_limit_the_whole_liked_feed_is_walked(tmp_path, monkeypatch):
    from igsaved.instagram import CollectionInfo

    cfg = Config()
    cfg.download_dir = str(tmp_path / "dl")
    cfg.eagle_enabled = False
    cfg.liked_scan_limit = 0            # без обмеження

    liked = CollectionInfo("liked", "Пролайкане", 0, is_liked=True)
    state = State(tmp_path / "s.db")
    try:
        engine, _ = _engine_over(monkeypatch, cfg, state, _fake_feed(120))
        engine._sync_collection(liked)
        assert engine.stats.scanned == 120
    finally:
        state.close()


def test_scan_limit_does_not_touch_saved(tmp_path, monkeypatch):
    """Ліміт стосується лише пролайканого — збережене проходить повністю."""
    from igsaved.instagram import CollectionInfo

    cfg = Config()
    cfg.download_dir = str(tmp_path / "dl")
    cfg.eagle_enabled = False
    cfg.liked_scan_limit = 5

    saved = CollectionInfo("ALL_MEDIA_AUTO_COLLECTION", "All Posts", 0, is_all_saved=True)
    state = State(tmp_path / "s.db")
    try:
        engine, downloaded = _engine_over(monkeypatch, cfg, state, _fake_feed(30))
        engine._sync_collection(saved)
        assert engine.stats.scanned == 30
        assert engine.stats.filtered == 0      # у збереженому не класифікуємо
        assert len(downloaded) == 30           # усе пішло на завантаження
    finally:
        state.close()


def test_vision_setup_ignores_sync_liked_flag(tmp_path, monkeypatch):
    """Реальний промах: модель мовчки вимикалась, якщо підбірку обрали галочкою.

    «Пролайкане» можна увімкнути прямо в списку на вкладці синхронізації —
    тоді cfg.sync_liked лишається False, хоча підбірка синхронізується.
    Прив'язка перевірки до цього прапорця вимикала модель без жодного слова.
    """
    from igsaved import vision
    from igsaved.sync import SyncEngine

    cfg = Config()
    cfg.download_dir = str(tmp_path / "dl")
    cfg.vision_enabled = True
    cfg.classify_liked = True
    cfg.sync_liked = False              # саме той випадок
    cfg.vision_describe_downloads = False

    state = State(tmp_path / "s.db")
    try:
        engine = SyncEngine(cfg, state, "sid", log=lambda _m: None)
        monkeypatch.setattr(vision.VisionClient, "resolve_model", lambda self: "vlm")
        assert engine._setup_vision() is not None

        # вимкнені і фільтр мемів, і опис — модель не потрібна, і про це кажемо
        cfg.classify_liked = False
        cfg.vision_describe_downloads = False
        lines = []
        engine.log = lines.append
        assert engine._setup_vision() is None
        assert any("фільтр мемів" in line for line in lines)

        # але сам лише опис — уже привід підняти модель
        cfg.vision_describe_downloads = True
        assert engine._setup_vision() is not None

        # вимкнена сама модель — тиша доречна
        cfg.classify_liked = True
        cfg.vision_enabled = False
        lines.clear()
        assert engine._setup_vision() is None
        assert lines == []
    finally:
        state.close()


def test_pending_review_blocks_eagle_backfill(tmp_path):
    """Головна вимога: у Eagle не має потрапляти нічого до схвалення."""
    from igsaved.maintenance import push_to_eagle

    cfg = Config()
    cfg.download_dir = str(tmp_path / "dl")
    cfg.eagle_enabled = True
    cfg.eagle_url = "http://127.0.0.1:9"      # свідомо мертвий порт
    cfg.root.mkdir(parents=True, exist_ok=True)

    media = cfg.root / "post.mp4"
    media.write_bytes(b"x")

    state = State(tmp_path / "s.db")
    try:
        state.record_media("55", "C", "u", None, 2, "clips", "", "")
        state.mark_done("55")
        state.add_file(str(media), "55", "video", 0, 1)
        state.add_review("55", str(media), "", "u", "", "", "порівну", "review")

        assert state.is_pending_review("55") is True
        state.decide_review("55", "kept")
        assert state.is_pending_review("55") is False

        # Eagle недоступний — має чесно сказати, а не впасти
        stats = push_to_eagle(cfg, state, log=lambda _m: None)
        assert stats.sent == 0
        assert "Eagle" in stats.error
    finally:
        state.close()


def test_clear_downloads_keeps_the_memory(tmp_path):
    """Головна вимога: файли зникають, але дублі не качаються заново."""
    from igsaved.maintenance import clear_downloads, downloads_summary

    cfg = Config()
    cfg.download_dir = str(tmp_path / "dl")
    cfg.root.mkdir(parents=True, exist_ok=True)
    cfg.thumbs_dir.mkdir(parents=True, exist_ok=True)
    cfg.meta_dir.mkdir(parents=True, exist_ok=True)

    media = cfg.root / "post.mp4"
    media.write_bytes(b"x" * 2048)
    thumb = cfg.thumbs_dir / "post.jpg"
    thumb.write_bytes(b"t" * 100)
    sidecar = cfg.meta_dir / "post.json"
    sidecar.write_text("{}", encoding="utf-8")
    foreign = cfg.root / "не наш файл.txt"      # чуже — чіпати не можна
    foreign.write_text("hands off", encoding="utf-8")

    state = State(tmp_path / "s.db")
    try:
        state.record_media("77", "C", "u", None, 2, "clips", "", "")
        state.mark_done("77")
        state.add_file(str(media), "77", "video", 0, 2048)
        state.add_file(str(thumb), "77", "thumb", 0, 100)

        assert state.is_known("77") is True
        count, size = downloads_summary(state)
        assert count == 2 and size == 2148

        stats = clear_downloads(cfg, state, log=lambda _m: None)

        assert not media.exists()
        assert not thumb.exists()
        assert not sidecar.exists()
        assert foreign.exists()          # чужий файл цілий
        assert stats.files == 3 and stats.posts == 1

        # і найголовніше — пост досі вважається завантаженим
        assert state.is_known("77") is True
        assert state.files_exist("77") is False
    finally:
        state.close()


def test_accidentally_deleted_file_is_redownloaded(tmp_path):
    """А ось випадково стертий файл має скачатись знову — це не архів."""
    cfg = Config()
    cfg.download_dir = str(tmp_path / "dl")
    cfg.root.mkdir(parents=True, exist_ok=True)
    media = cfg.root / "post.mp4"
    media.write_bytes(b"x")

    state = State(tmp_path / "s.db")
    try:
        state.record_media("88", "C", "u", None, 2, "clips", "", "")
        state.mark_done("88")
        state.add_file(str(media), "88", "video", 0, 1)
        assert state.is_known("88") is True

        media.unlink()                       # користувач стер вручну
        assert state.is_known("88") is False  # отже, качаємо знову

        state.mark_archived("88")            # а тепер це свідома чистка
        assert state.is_known("88") is True
    finally:
        state.close()


def test_state_skip_and_forget(tmp_path):
    state = State(tmp_path / "s.db")
    try:
        state.record_media("9", "C", "u", None, 2, "clips", "", "")
        assert state.is_skipped("9") is False
        state.mark_skipped("9", "мем")
        assert state.is_skipped("9") is True
        assert state.is_downloaded("9") is False   # не рахується завантаженим

        state.add_file("/x/a.mp4", "9", "video", 0, 1)
        state.forget_media("9")
        assert state.is_skipped("9") is False
        assert state.media_files("9") == []
    finally:
        state.close()


def test_state_update_file_path(tmp_path):
    state = State(tmp_path / "s.db")
    try:
        state.add_file("/old/a.mp4", "1", "video", 0, 5)
        state.add_review("1", "/old/a.mp4", "", "u", "", "", "r", "review")
        state.update_file_path("/old/a.mp4", "/new/a.mp4")
        assert state.media_files("1") == ["/new/a.mp4"]
        assert state.pending_review()[0]["path"] == "/new/a.mp4"
    finally:
        state.close()


# ---------------------------------------------------------------- scheduler
def test_scheduler_builds_expected_schtasks_arguments(monkeypatch):
    from igsaved import scheduler
    from igsaved.config import SCHED_HOURLY, SCHED_ONLOGON, SCHED_WEEKLY

    captured = {}

    def fake_run(args):
        captured["args"] = args
        return 0, ""

    monkeypatch.setattr(scheduler, "_run", fake_run)
    monkeypatch.setattr(scheduler.sys, "platform", "win32")

    assert scheduler.create_task("T", SCHED_DAILY, "07:30").ok
    assert captured["args"][captured["args"].index("/SC") + 1] == "DAILY"
    assert "07:30" in captured["args"]

    scheduler.create_task("T", SCHED_HOURLY, "07:30", interval_hours=4)
    args = captured["args"]
    assert args[args.index("/SC") + 1] == "HOURLY"
    assert args[args.index("/MO") + 1] == "4"

    scheduler.create_task("T", SCHED_WEEKLY, "07:30", weekday="fri")
    args = captured["args"]
    assert args[args.index("/D") + 1] == "FRI"

    scheduler.create_task("T", SCHED_ONLOGON)
    args = captured["args"]
    assert args[args.index("/SC") + 1] == "ONLOGON"
    assert "/ST" not in args


def test_scheduler_rejects_bad_time(monkeypatch):
    from igsaved import scheduler

    monkeypatch.setattr(scheduler.sys, "platform", "win32")
    result = scheduler.create_task("T", SCHED_DAILY, "25:99")
    assert result.ok is False
    assert "ГГ:ХХ" in result.message


def test_scheduler_is_noop_off_windows(monkeypatch):
    from igsaved import scheduler

    monkeypatch.setattr(scheduler.sys, "platform", "linux")
    assert scheduler.create_task("T").ok is False
    assert scheduler.is_run_at_login() is False
    assert scheduler.status("T") is None


# ------------------------------------------------------ нові поля конфігу
def test_config_new_fields_roundtrip(tmp_path):
    path = tmp_path / "c.json"
    cfg = Config()
    cfg.filename_template = "{user}-{code}"
    cfg.skip_larger_than_mb = 250
    cfg.proxy = "http://127.0.0.1:8080"
    cfg.schedule_mode = "hourly"
    cfg.schedule_interval_hours = 3
    cfg.run_on_windows_start = True
    cfg.sync_on_launch = True
    cfg.eagle_extra_tags = ["instagram", "reference"]
    cfg.save(path)

    again = Config.load(path)
    assert again.filename_template == "{user}-{code}"
    assert again.skip_larger_than_mb == 250
    assert again.proxy == "http://127.0.0.1:8080"
    assert again.schedule_mode == "hourly"
    assert again.schedule_interval_hours == 3
    assert again.run_on_windows_start is True
    assert again.sync_on_launch is True
    assert again.eagle_extra_tags == ["instagram", "reference"]


# -------------------------------------------------- діагностика сесії
def test_explain_maps_browser_cookie3_noise_to_useful_hints():
    # саме це повідомлення бачив користувач для Chrome/Edge — воно оманливе
    label, hint = explain("This operation requires admin. Please run as admin.")
    assert "зашифровані" in label
    assert "App-Bound" in hint
    assert "адміністратора" in hint  # прямо кажемо, що адмін не допоможе

    label, hint = explain("Failed to find Firefox cookie file")
    assert label == "не встановлений"
    assert "не знайдено" in hint

    label, hint = explain("database is locked")
    assert "зайнята" in label

    # невідому помилку не ковтаємо, показуємо як є
    assert explain("something odd") == ("something odd", "")


def test_find_sessionid_reports_hint_when_nothing_found(monkeypatch):
    from igsaved import session as sess

    def boom(name):
        raise RuntimeError("This operation requires admin. Please run as admin.")

    monkeypatch.setattr(sess, "_extract", boom)
    result = sess.find_sessionid("chrome")
    assert result.ok is False
    assert result.hint and "App-Bound" in result.hint
    assert any("зашифровані" in note for note in result.notes)


def test_find_sessionid_returns_value_when_browser_cooperates(monkeypatch):
    from igsaved import session as sess

    monkeypatch.setattr(sess, "_extract", lambda name: "abc123" if name == "firefox" else None)
    result = sess.find_sessionid("auto")
    assert result.ok is True
    assert result.sessionid == "abc123"
    assert result.browser == "firefox"


def test_find_sessionid_notes_logged_out_browser(monkeypatch):
    from igsaved import session as sess

    monkeypatch.setattr(sess, "_extract", lambda name: None)
    result = sess.find_sessionid("chrome")
    assert result.ok is False
    assert any("не залогінений" in note for note in result.notes)


# ------------------------------------------------------- статус запуску
def test_status_roundtrip_and_failed_flag(tmp_path):
    path = tmp_path / "last_run.json"

    run_status.write(run_status.OK, source="gui", summary="усе добре", path=path)
    ok = run_status.read(path)
    assert ok.result == run_status.OK
    assert ok.failed is False
    assert ok.summary == "усе добре"
    assert ok.when_human  # дата відформатувалась

    run_status.write(
        run_status.NO_SESSION, source="scheduled",
        summary="Сесію Instagram не підключено",
        advice=run_status.NO_SESSION_ADVICE, path=path,
    )
    bad = run_status.read(path)
    assert bad.failed is True
    assert bad.source == "scheduled"
    assert "Сесія" in bad.headline or "сесі" in bad.headline.lower()

    run_status.clear(path)
    assert run_status.read(path) is None


def test_status_survives_broken_file(tmp_path):
    path = tmp_path / "last_run.json"
    path.write_text("{ broken", encoding="utf-8")
    assert run_status.read(path) is None


def test_status_ignores_unknown_keys(tmp_path):
    path = tmp_path / "last_run.json"
    path.write_text('{"result": "failed", "mystery": 1}', encoding="utf-8")
    parsed = run_status.read(path)
    assert parsed.result == "failed"
    assert parsed.failed is True


def test_status_when_human_tolerates_garbage():
    assert run_status.RunStatus(when="not-a-date").when_human == ""
    assert run_status.RunStatus().when_human == ""


# ------------------------------------------------------------- CLI
def test_cli_without_session_writes_status_and_exits(tmp_path, monkeypatch):
    """Головний сценарій з логу користувача: фоновий запуск без збереженої сесії."""
    from igsaved import cli

    status_path = tmp_path / "last_run.json"
    monkeypatch.setattr(cli.status, "STATUS_PATH", status_path)
    monkeypatch.setattr(cli, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(cli, "load_session", lambda: {})
    monkeypatch.setattr(cli, "find_sessionid",
                        lambda browser: CookieResult(None, None, ["Chrome: зашифровані"], "підказка"))
    popped = {}
    monkeypatch.setattr(cli.notify, "popup",
                        lambda title, message, seconds=25: popped.update(title=title, message=message))

    code = cli.main(["--sync", "--quiet"])
    assert code == 2

    written = run_status.read(status_path)
    assert written.result == run_status.NO_SESSION
    assert written.source == "scheduled"
    assert written.advice  # у банері GUI буде що показати
    # і користувач побачить вікно, а не лише рядок у лозі
    assert "сесія" in popped["title"].lower()
    assert "sessionid" in popped["message"]


def test_cli_popup_can_be_disabled(tmp_path, monkeypatch):
    from igsaved import cli

    monkeypatch.setattr(cli.status, "STATUS_PATH", tmp_path / "last_run.json")
    monkeypatch.setattr(cli, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(cli, "load_session", lambda: {})
    monkeypatch.setattr(cli, "find_sessionid",
                        lambda browser: CookieResult(None, None, [], ""))
    called = []
    monkeypatch.setattr(cli.notify, "popup", lambda *a, **k: called.append(1))

    assert cli.main(["--sync", "--quiet", "--no-popup"]) == 2
    assert called == []


def test_notify_popup_is_noop_off_windows(monkeypatch):
    from igsaved import notify

    monkeypatch.setattr(notify.sys, "platform", "linux")
    assert notify.popup("t", "m") is False


def test_old_config_file_gets_new_defaults(tmp_path):
    """Конфіг, збережений попередньою версією, має читатись без помилок."""
    path = tmp_path / "c.json"
    path.write_text('{"download_dir": "D:/x", "schedule_time": "22:15"}', encoding="utf-8")
    cfg = Config.load(path)
    assert cfg.download_dir == "D:/x"
    assert cfg.schedule_time == "22:15"
    assert cfg.filename_template == DEFAULT_TEMPLATE
    assert cfg.schedule_mode == SCHED_DAILY
    assert cfg.minimize_to_tray is True


# ==========================================================================
#  Кадри з відео: модель має бачити ролик, а не одну обкладинку
# ==========================================================================
def _make_video(path, count=60, size=(160, 120)):
    """Синтетичний ролик, де кожен кадр іншого кольору."""
    cv2 = pytest.importorskip("cv2")
    import numpy as np

    width, height = size
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"mp4v"), 25, (width, height)
    )
    assert writer.isOpened()
    for index in range(count):
        frame = np.zeros((height, width, 3), np.uint8)
        frame[:, :] = (index * 4 % 255, 255 - (index * 4 % 255), 128)
        writer.write(frame)
    writer.release()
    return path


def test_frames_extract_walks_the_whole_video(tmp_path):
    from igsaved import frames

    video = _make_video(tmp_path / "clip.mp4")
    shots = frames.extract(video, 6)
    assert len(shots) == 6
    # різні кадри, а не шість копій обкладинки — саме в цьому був сенс
    assert len(set(shots)) == 6
    assert all(shot.startswith(b"\xff\xd8") for shot in shots)


def test_frames_extract_handles_edge_cases(tmp_path):
    from igsaved import frames

    video = _make_video(tmp_path / "clip.mp4")
    assert len(frames.extract(video, 1)) == 1
    assert frames.extract(video, 0) == []
    assert frames.extract(tmp_path / "nope.mp4", 4) == []
    # не відео — навіть не намагаємось
    picture = tmp_path / "pic.jpg"
    picture.write_bytes(b"\xff\xd8\xff")
    assert frames.extract(picture, 4) == []


def test_frames_shrink_keeps_broken_data_untouched():
    from igsaved import frames

    assert frames.shrink_image(b"") == b""
    assert frames.shrink_image(b"not an image") == b"not an image"


def test_frames_shrink_reduces_a_big_picture(tmp_path):
    cv2 = pytest.importorskip("cv2")
    import numpy as np

    from igsaved import frames

    big = np.zeros((1600, 1200, 3), np.uint8)
    big[:, :] = (40, 90, 200)
    ok, buffer = cv2.imencode(".jpg", big)
    assert ok
    small = frames.shrink_image(buffer.tobytes())
    decoded = cv2.imdecode(np.frombuffer(small, np.uint8), cv2.IMREAD_COLOR)
    assert max(decoded.shape[:2]) == frames.MAX_SIDE


# ==========================================================================
#  Опис і теги від моделі
# ==========================================================================
def test_vision_parses_description_and_tags():
    from igsaved.vision import ART, parse_answer

    verdict = parse_answer(
        '{"category": "art", "confidence": 0.8,'
        ' "description": "Повільний оберт 3D-форми.",'
        ' "tags": ["#3D Render", "motion-graphics", "3d render", ""],'
        ' "why": "render loop"}'
    )
    assert verdict.ok and verdict.category == ART
    assert verdict.description == "Повільний оберт 3D-форми."
    # ґратки зняті, пробіли замінені, дублікат прибрано
    assert verdict.tags == ["3d-render", "motion-graphics"]
    assert verdict.has_text


def test_vision_survives_braces_inside_the_description():
    """Опис — вільний текст, і фігурна дужка в ньому не має ламати розбір."""
    from igsaved.vision import parse_answer

    verdict = parse_answer(
        'Ось відповідь:\n'
        '{"category": "meme", "confidence": 0.9,'
        ' "description": "Текст {у дужках} і \\"лапках\\".", "tags": []}\n'
        'Сподіваюсь, допоміг! {'
    )
    assert verdict.ok
    assert verdict.description == 'Текст {у дужках} і "лапках".'


def test_vision_keeps_text_even_when_category_is_wrong():
    """Категорія не зрозуміла — але опис і теги вже написані, і вони не винні."""
    from igsaved.vision import parse_answer

    verdict = parse_answer(
        '{"category": "artwork", "description": "Каліграфія тушшю.",'
        ' "tags": "calligraphy, ink"}'
    )
    assert verdict.ok is False and verdict.error
    assert verdict.description == "Каліграфія тушшю."
    assert verdict.tags == ["calligraphy", "ink"]


def test_vision_tag_cleaner_is_strict_but_forgiving():
    from igsaved.vision import MAX_TAGS, clean_tags

    assert clean_tags(None) == []
    assert clean_tags("one; two\nthree") == ["one", "two", "three"]
    assert clean_tags(["  #Motion Design  "]) == ["motion-design"]
    assert clean_tags(["x" * 40]) == []                    # надто довгий
    assert len(clean_tags([f"tag{i}" for i in range(40)])) == MAX_TAGS


def test_vision_prompt_placeholders_and_broken_edits():
    from igsaved.vision import DEFAULT_PROMPT, build_prompt

    text = build_prompt("", 6, "reel")
    assert "6 frame(s)" in text and "reel" in text
    assert "{frames}" not in text
    # порожньо в конфізі = вбудована інструкція
    assert build_prompt("   ", 1, "photo") == build_prompt(DEFAULT_PROMPT, 1, "photo")
    # своя інструкція із зайвою дужкою не має нічого валити
    assert build_prompt("дивись {frames} кадрів {oops}", 3, "video") == \
        "дивись 3 кадрів {oops}"


def test_vision_client_sends_every_frame():
    from igsaved.vision import VisionClient

    _LMStudioHandler.seen.clear()
    server = HTTPServer(("127.0.0.1", 0), _LMStudioHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        client = VisionClient(f"http://127.0.0.1:{server.server_port}/v1")
        verdict = client.classify(
            [b"\xff\xd8one", b"\xff\xd8two", b"\xff\xd8three"], kind="reel"
        )
        assert verdict.frames == 3
        content = _LMStudioHandler.seen[0]["messages"][0]["content"]
        images = [part for part in content if part["type"] == "image_url"]
        assert len(images) == 3
        text = next(part for part in content if part["type"] == "text")["text"]
        assert "3 frame(s)" in text and "reel" in text
    finally:
        server.shutdown()


def test_vision_client_caps_the_number_of_frames():
    from igsaved.vision import MAX_FRAMES, VisionClient

    _LMStudioHandler.seen.clear()
    server = HTTPServer(("127.0.0.1", 0), _LMStudioHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        client = VisionClient(f"http://127.0.0.1:{server.server_port}/v1")
        client.classify([b"\xff\xd8x"] * (MAX_FRAMES + 8))
        content = _LMStudioHandler.seen[0]["messages"][0]["content"]
        assert len([p for p in content if p["type"] == "image_url"]) == MAX_FRAMES
    finally:
        server.shutdown()


def test_slide_urls_covers_the_whole_carousel():
    from igsaved.vision import slide_urls

    media = FakeMedia(media_type=8)
    media.resources = [
        FakeResource(1, thumbnail_url="https://cdn/a.jpg"),
        FakeResource(2, thumbnail_url="https://cdn/b.jpg"),
        FakeResource(3, thumbnail_url="https://cdn/c.jpg"),
    ]
    assert slide_urls(media, 2) == ["https://cdn/a.jpg", "https://cdn/b.jpg"]
    # каруселі без ресурсів лишається обкладинка
    plain = FakeMedia()
    assert slide_urls(plain, 4) == [plain.thumbnail_url]


# ==========================================================================
#  Опис у базі, у файлі й у Eagle
# ==========================================================================
def test_state_remembers_what_the_model_wrote(tmp_path):
    state = State(tmp_path / "s.db")
    try:
        assert state.ai_meta("1") is None
        state.set_ai_meta("1", "art", 0.81, "Опис ролика.", ["3d", "loop"], "vlm", 6)
        meta = state.ai_meta("1")
        assert meta["category"] == "art"
        assert meta["description"] == "Опис ролика."
        assert meta["tags"] == ["3d", "loop"]
        assert meta["frames"] == 6
        assert state.all_ai_meta()["1"]["model"] == "vlm"

        # забути пост = забути й те, що модель про нього писала
        state.forget_media("1")
        assert state.ai_meta("1") is None
    finally:
        state.close()


def test_media_tags_carry_the_model_description():
    from igsaved.tagging import MediaTags, annotation

    tags = MediaTags(
        title="Скло",
        author="studioalt",
        caption="нове #glass",
        url="https://www.instagram.com/p/AbC/",
        hashtags=["#glass"],
        collections=["Пролайкане"],
        description="  Камера   облітає скляну форму.  ",
        ai_tags=["3d-render", "glass"],
    )
    comment = tags.comment()
    assert "нове #glass" in comment
    assert "Visual summary: Камера облітає скляну форму." in comment
    assert "Автор: @studioalt" in comment
    assert comment.index("нове") < comment.index("Visual summary:") < comment.index("Автор:")
    assert tags.summary() == "Камера облітає скляну форму."

    keywords = tags.keywords()
    assert "3d-render" in keywords and "#glass" in keywords

    assert annotation("підпис", "опис") == "підпис\n\nVisual summary: опис"
    assert annotation("", "опис") == "Visual summary: опис"
    assert annotation("підпис", "") == "підпис"


def test_summary_falls_back_to_the_title_without_a_description():
    from igsaved.tagging import MediaTags

    assert MediaTags(title="Скло").summary() == "Скло"


# ==========================================================================
#  Синхронізація: ролик качається один раз, навіть коли його дивилась модель
# ==========================================================================
def _engine(tmp_path, **overrides):
    from igsaved.sync import SyncEngine

    cfg = Config()
    cfg.download_dir = str(tmp_path / "dl")
    for key, value in overrides.items():
        setattr(cfg, key, value)
    state = State(tmp_path / "s.db")
    return SyncEngine(cfg, state, "sid", log=lambda _m: None), cfg, state


def test_prefetched_video_moves_instead_of_downloading_again(tmp_path):
    engine, cfg, state = _engine(tmp_path)
    try:
        cached = cfg.cache_dir
        cached.mkdir(parents=True, exist_ok=True)
        source = cached / "111.mp4"
        source.write_bytes(b"video-bytes")
        engine._prefetch["https://cdn/v.mp4?x=1"] = source

        pulled = []
        engine.dl.fetch = lambda url, dest: pulled.append(url)

        dest = cfg.root / "out.mp4"
        size = engine._download_or_reuse("https://cdn/v.mp4?x=1", dest, "111", "video")

        assert size == len(b"video-bytes")
        assert dest.read_bytes() == b"video-bytes"
        assert not source.exists()        # переїхав, а не скопіювався
        assert pulled == []               # мережу не чіпали
    finally:
        state.close()


def test_skipped_post_takes_its_temporary_video_with_it(tmp_path):
    engine, cfg, state = _engine(tmp_path)
    try:
        cfg.cache_dir.mkdir(parents=True, exist_ok=True)
        temp = cfg.cache_dir / "111.mp4"
        temp.write_bytes(b"meme")
        engine._prefetch["https://cdn/v.mp4?x=1"] = temp

        engine._drop_prefetch("111")
        assert not temp.exists()
        assert engine._prefetch == {}

        # і хвіст прибирається наприкінці запуску
        engine._clear_cache()
        assert not cfg.cache_dir.exists()
    finally:
        state.close()


def test_frames_for_falls_back_to_the_cover(tmp_path, monkeypatch):
    """Немає opencv або ролик не завантажився — дивимось хоч обкладинку."""
    from igsaved import vision

    engine, cfg, state = _engine(tmp_path, vision_frames=6)
    try:
        monkeypatch.setattr(engine, "_prefetch_video", lambda media: None)
        monkeypatch.setattr(vision, "fetch_image", lambda *a, **k: b"\xff\xd8cover")

        shots, kind = engine._frames_for(FakeMedia())
        assert len(shots) == 1
        assert kind == "reel"

        # зовсім без картинки — порожньо, і ніхто не падає
        monkeypatch.setattr(vision, "fetch_image", lambda *a, **k: None)
        assert engine._frames_for(FakeMedia())[0] == []
    finally:
        state.close()


def test_frames_for_reads_the_real_video(tmp_path, monkeypatch):
    pytest.importorskip("cv2")
    engine, cfg, state = _engine(tmp_path, vision_frames=5)
    try:
        cfg.cache_dir.mkdir(parents=True, exist_ok=True)
        _make_video(cfg.cache_dir / "111.mp4")
        shots, kind = engine._frames_for(FakeMedia())
        assert len(shots) == 5 and len(set(shots)) == 5
        assert kind == "reel"
    finally:
        state.close()


def test_vision_verdict_is_saved_even_when_confidence_is_low(tmp_path, monkeypatch):
    from igsaved import classify as classifier
    from igsaved import vision

    engine, cfg, state = _engine(tmp_path, vision_min_confidence=0.9)
    try:
        engine._vision_model = "vlm"
        engine._vision = types.SimpleNamespace(
            classify=lambda *a, **k: vision.VisionVerdict(
                category=vision.ART, confidence=0.4, description="Скляна форма.",
                tags=["glass"], frames=6,
            )
        )
        monkeypatch.setattr(engine, "_frames_for", lambda media: ([b"a", b"b"], "reel"))

        verdict = classifier.Verdict()
        media = FakeMedia()
        assert engine._ask_vision(media, verdict) is False    # рішення не прийнято
        saved = state.ai_meta(media.pk)
        assert saved["description"] == "Скляна форма."        # але опис лишився
        assert saved["tags"] == ["glass"]
        assert any("вагається" in reason for reason in verdict.reasons)
    finally:
        state.close()


def test_vision_decision_is_taken_when_confident(tmp_path, monkeypatch):
    from igsaved import classify as classifier
    from igsaved import vision
    from igsaved.classify import DOWNLOAD, SKIP

    engine, cfg, state = _engine(tmp_path, vision_min_confidence=0.5)
    try:
        engine._vision_model = "vlm"
        monkeypatch.setattr(engine, "_frames_for", lambda media: ([b"a"], "reel"))

        engine._vision = types.SimpleNamespace(
            classify=lambda *a, **k: vision.VisionVerdict(
                category=vision.ART, confidence=0.88, frames=6))
        verdict = classifier.Verdict()
        assert engine._ask_vision(FakeMedia(), verdict) is True
        assert verdict.decision == DOWNLOAD

        engine._vision = types.SimpleNamespace(
            classify=lambda *a, **k: vision.VisionVerdict(
                category=vision.MEME, confidence=0.95, frames=6))
        verdict = classifier.Verdict()
        assert engine._ask_vision(FakeMedia(pk="222"), verdict) is True
        assert verdict.decision == SKIP

        # «інше» — не рішення моделі, а робота для людини
        engine._vision = types.SimpleNamespace(
            classify=lambda *a, **k: vision.VisionVerdict(
                category=vision.OTHER, confidence=0.99, frames=6))
        verdict = classifier.Verdict()
        assert engine._ask_vision(FakeMedia(pk="333"), verdict) is False
    finally:
        state.close()


def test_describe_runs_on_downloaded_files(tmp_path, monkeypatch):
    """Опис для кожного нового поста бере кадри з уже завантаженого файлу."""
    from igsaved import vision

    engine, cfg, state = _engine(tmp_path, vision_describe_downloads=True,
                                 vision_frames=4)
    try:
        pytest.importorskip("cv2")
        cfg.root.mkdir(parents=True, exist_ok=True)
        video = _make_video(cfg.root / "clip.mp4")

        seen = {}

        def fake_classify(images, **kwargs):
            seen["count"] = len(images)
            return vision.VisionVerdict(category=vision.ART, confidence=0.8,
                                        description="Кольори змінюються.",
                                        tags=["gradient"], frames=len(images))

        engine._vision_model = "vlm"
        engine._vision = types.SimpleNamespace(classify=fake_classify)
        media = FakeMedia()
        engine._describe(media, media.pk, [(video, 0)])

        assert seen["count"] == 4
        assert state.ai_meta(media.pk)["description"] == "Кольори змінюються."

        # вдруге не питаємо — опис уже є
        seen.clear()
        engine._describe(media, media.pk, [(video, 0)])
        assert seen == {}
    finally:
        state.close()


def test_setup_vision_runs_for_description_only(tmp_path, monkeypatch):
    """Фільтр мемів вимкнено, а опис потрібен — модель має піднятись."""
    from igsaved import vision

    engine, cfg, state = _engine(tmp_path, vision_enabled=True,
                                 classify_liked=False,
                                 vision_describe_downloads=True)
    try:
        monkeypatch.setattr(vision.VisionClient, "resolve_model", lambda self: "vlm")
        assert engine._setup_vision() is not None
    finally:
        state.close()


def test_config_keeps_the_prompt_and_frame_count(tmp_path):
    path = tmp_path / "c.json"
    cfg = Config()
    cfg.vision_frames = 8
    cfg.vision_prompt = "дивись на {frames} кадрів"
    cfg.vision_describe_downloads = True
    cfg.save(path)

    again = Config.load(path)
    assert again.vision_frames == 8
    assert again.vision_prompt == "дивись на {frames} кадрів"
    assert again.vision_describe_downloads is True


def test_old_vision_timeout_is_raised_for_multi_frame_requests(tmp_path):
    """60 с вистачало на обкладинку; кадри плюс опис у нього не влазять."""
    path = tmp_path / "c.json"
    path.write_text('{"vision_timeout": 60}', encoding="utf-8")
    assert Config.load(path).vision_timeout == 120

    # свідомо виставлене значення не чіпаємо
    path.write_text('{"vision_timeout": 45}', encoding="utf-8")
    assert Config.load(path).vision_timeout == 45


def test_eagle_item_carries_description_and_model_tags(tmp_path):
    from igsaved.instagram import CollectionInfo

    engine, cfg, state = _engine(tmp_path)
    try:
        media = FakeMedia(caption="скло #glass")
        state.set_ai_meta(media.pk, "art", 0.9, "Камера облітає форму.",
                          ["3d-render", "glass"], "vlm", 6)
        col = CollectionInfo(pk="liked", name="Пролайкане", media_count=0)
        item = engine._build_item(media, col, Path("x.mp4"))

        assert "Visual summary: Камера облітає форму." in item.annotation
        assert "скло #glass" in item.annotation
        assert "3d-render" in item.tags
        # дублікат між хештегом і тегом моделі не подвоюється
        assert sum(1 for tag in item.tags if tag.lower() == "glass") <= 1
    finally:
        state.close()



# ==========================================================================
#  Рішення моделі проходять перед очима, перш ніж потрапити в Eagle
# ==========================================================================
def _fake_downloader(engine, monkeypatch, pulled):
    """Замість мережі: у .mp4 кладемо справжнє відео, у .jpg — справжній jpeg."""
    cv2 = pytest.importorskip("cv2")
    import numpy as np

    def fetch(url, dest):
        pulled.append(url)
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.suffix.lower() == ".mp4":
            _make_video(dest)
        else:
            picture = np.zeros((80, 60, 3), np.uint8)
            picture[:, :] = (10, 120, 200)
            dest.write_bytes(cv2.imencode(".jpg", picture)[1].tobytes())
        return types.SimpleNamespace(size=dest.stat().st_size)

    monkeypatch.setattr(engine.dl, "fetch", fetch)


def _liked_engine(tmp_path, monkeypatch, state, media, category, **overrides):
    from igsaved import vision
    from igsaved.sync import SyncEngine

    cfg = Config()
    cfg.download_dir = str(tmp_path / "dl")
    cfg.eagle_enabled = False
    cfg.vision_enabled = True
    cfg.vision_frames = 4
    cfg.vision_min_confidence = 0.5
    for key, value in overrides.items():
        setattr(cfg, key, value)

    engine = SyncEngine(cfg, state, "sid", log=lambda _m: None)
    monkeypatch.setattr(engine.ig, "iter_media",
                        lambda pk, stop=None, on_page=None: iter([media]))
    pulled = []
    _fake_downloader(engine, monkeypatch, pulled)
    engine._vision_model = "vlm"
    engine._vision = types.SimpleNamespace(
        classify=lambda images, **kw: vision.VisionVerdict(
            category=category, confidence=0.9, description="Що видно на кадрах.",
            tags=["tag"], frames=len(images),
        )
    )
    return engine, cfg, pulled


def test_model_approval_waits_for_a_glance_before_eagle(tmp_path, monkeypatch):
    """Модель сказала «арт»: файл на диску, але в Eagle чекає підтвердження."""
    from igsaved import vision
    from igsaved.instagram import CollectionInfo
    from igsaved.state import REVIEW_MODEL, REVIEW_RULES

    media = _post(caption="без хештегів", username="mixedbag", pk=777)
    liked = CollectionInfo("liked", "Пролайкане", 0, is_liked=True)
    state = State(tmp_path / "s.db")
    try:
        engine, cfg, pulled = _liked_engine(
            tmp_path, monkeypatch, state, media, vision.ART)
        engine._sync_collection(liked)

        # файл — в основній теці, а не в ревʼю
        assert [p.name for p in cfg.root.glob("*.mp4")]
        assert not list(cfg.review_dir.glob("*.mp4"))
        assert pulled.count(media.video_url) == 1     # ролик тягнули один раз

        queue = state.pending_review(REVIEW_MODEL)
        assert len(queue) == 1
        assert queue[0]["verdict"] == "download"
        assert state.pending_review(REVIEW_RULES) == []
        # головне: у Eagle поки нічого
        assert state.is_pending_review("777") is True
    finally:
        state.close()


def test_model_rejection_is_kept_until_you_agree(tmp_path, monkeypatch):
    """Модель сказала «мем»: файл лежить у ревʼю, щоб помилку можна було спіймати."""
    from igsaved import vision
    from igsaved.instagram import CollectionInfo
    from igsaved.state import REVIEW_MODEL

    media = _post(caption="без хештегів", username="mixedbag", pk=888)
    liked = CollectionInfo("liked", "Пролайкане", 0, is_liked=True)
    state = State(tmp_path / "s.db")
    try:
        engine, cfg, pulled = _liked_engine(
            tmp_path, monkeypatch, state, media, vision.MEME)
        engine._sync_collection(liked)
        engine._clear_cache()

        assert engine.stats.filtered == 0            # не викинуто мовчки
        assert engine.stats.to_review == 1
        assert list(cfg.review_dir.glob("*.mp4"))    # лежить у теці ревʼю
        assert not list(cfg.root.glob("*.mp4"))
        assert not cfg.cache_dir.exists()
        # ролик уже був у _cache після перегляду — другий раз не качали
        assert pulled.count(media.video_url) == 1

        queue = state.pending_review(REVIEW_MODEL)
        assert len(queue) == 1 and queue[0]["verdict"] == "skip"
    finally:
        state.close()


def test_model_acts_silently_when_the_glance_is_off(tmp_path, monkeypatch):
    """Без галочки поведінка стара: мем зникає одразу, арт іде далі сам."""
    from igsaved import vision
    from igsaved.instagram import CollectionInfo
    from igsaved.state import REVIEW_MODEL

    media = _post(caption="без хештегів", username="mixedbag", pk=999)
    liked = CollectionInfo("liked", "Пролайкане", 0, is_liked=True)
    state = State(tmp_path / "s.db")
    try:
        engine, cfg, _ = _liked_engine(
            tmp_path, monkeypatch, state, media, vision.MEME,
            model_needs_glance=False)
        engine._sync_collection(liked)

        assert engine.stats.filtered == 1
        assert engine.stats.to_review == 0
        assert state.pending_review(REVIEW_MODEL) == []
        assert state.is_skipped("999")
    finally:
        state.close()


def test_saved_posts_are_described_and_go_straight_to_eagle(tmp_path, monkeypatch):
    """Збережене модель не судить: тільки опис і теги, ревʼю не залучається."""
    from igsaved import vision
    from igsaved.instagram import CollectionInfo
    from igsaved.state import REVIEW_MODEL, REVIEW_RULES

    media = _post(caption="без хештегів", username="mixedbag", pk=555)
    saved = CollectionInfo("ALL_MEDIA_AUTO_COLLECTION", "All Posts", 0,
                           is_all_saved=True)
    state = State(tmp_path / "s.db")
    try:
        engine, cfg, _ = _liked_engine(
            tmp_path, monkeypatch, state, media, vision.MEME)
        queued = []
        monkeypatch.setattr(engine, "_queue_eagle",
                            lambda m, c, paths: queued.extend(paths))

        engine._sync_collection(saved)

        assert engine.stats.downloaded == 1
        assert engine.stats.filtered == 0            # навіть «мем» не відсіюється
        assert state.pending_review(REVIEW_MODEL) == []
        assert state.pending_review(REVIEW_RULES) == []
        assert queued                                 # одразу в Eagle
        assert state.ai_meta("555")["description"] == "Що видно на кадрах."
        assert list(cfg.root.glob("*.mp4"))
    finally:
        state.close()


def test_uncertain_post_still_goes_to_the_human_queue(tmp_path, monkeypatch):
    """Модель відповіла «інше» — рішення лишається за людиною, черга своя."""
    from igsaved import vision
    from igsaved.instagram import CollectionInfo
    from igsaved.state import REVIEW_MODEL, REVIEW_RULES

    media = _post(caption="без хештегів", username="mixedbag", pk=444)
    liked = CollectionInfo("liked", "Пролайкане", 0, is_liked=True)
    state = State(tmp_path / "s.db")
    try:
        engine, cfg, _ = _liked_engine(
            tmp_path, monkeypatch, state, media, vision.OTHER)
        engine._sync_collection(liked)

        assert state.pending_review(REVIEW_MODEL) == []
        rules_queue = state.pending_review(REVIEW_RULES)
        assert len(rules_queue) == 1
        assert rules_queue[0]["verdict"] == "review"
        assert list(cfg.review_dir.glob("*.mp4"))
    finally:
        state.close()


def test_old_database_gets_the_source_column(tmp_path):
    """База з попередньої версії не має ламатись на новому стовпці."""
    import sqlite3

    from igsaved.state import REVIEW_RULES

    path = tmp_path / "old.db"
    legacy = sqlite3.connect(str(path))
    legacy.executescript(
        "CREATE TABLE review (media_pk TEXT PRIMARY KEY, path TEXT, thumb TEXT,"
        " username TEXT, caption TEXT, url TEXT, reason TEXT, verdict TEXT,"
        " added_at TEXT, decided TEXT);"
        "INSERT INTO review VALUES ('1','/a.mp4','','bob','','','порівну',"
        "'review','2026-01-01',NULL);"
    )
    legacy.commit()
    legacy.close()

    state = State(path)
    try:
        # старий запис читається і вважається таким, що чекає на людину
        assert len(state.pending_review(REVIEW_RULES)) == 1
        assert state.review_count(REVIEW_RULES) == 1
        assert state.review_count("model") == 0
        # спільна черга бачить його теж — вкладка тепер одна
        assert state.review_count() == 1
    finally:
        state.close()


# ==========================================================================
#  Багато кадрів: стеля висока, але запит не має розпухати
# ==========================================================================
def test_frame_side_shrinks_as_the_count_grows():
    from igsaved.frames import MAX_SIDE, side_for

    assert side_for(1) == MAX_SIDE
    assert side_for(12) == MAX_SIDE
    # 60 кадрів по 640 px — це мегабайти base64 в одному запиті
    assert side_for(24) < MAX_SIDE
    assert side_for(60) < side_for(24)
    # менше не буває — інакше модель уже нічого не розбере
    assert side_for(60) >= 320


def test_many_frames_really_are_smaller(tmp_path):
    from igsaved import frames

    video = _make_video(tmp_path / "clip.mp4", count=200, size=(1280, 720))
    few = frames.extract(video, 6)
    many = frames.extract(video, 48)
    assert len(few) == 6 and len(many) == 48

    cv2 = pytest.importorskip("cv2")
    import numpy as np

    def longest(data):
        picture = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        return max(picture.shape[:2])

    assert longest(few[0]) == frames.side_for(6)
    assert longest(many[0]) == frames.side_for(48)
    # головне заради чого все: сумарна вага не росте пропорційно кількості
    assert sum(len(x) for x in many) < sum(len(x) for x in few) * len(many) / len(few)


def test_ceiling_allows_sixty_frames():
    from igsaved.vision import MAX_FRAMES, SAFE_FRAMES, VisionClient

    assert MAX_FRAMES == 60
    assert SAFE_FRAMES < MAX_FRAMES

    _LMStudioHandler.seen.clear()
    server = HTTPServer(("127.0.0.1", 0), _LMStudioHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        client = VisionClient(f"http://127.0.0.1:{server.server_port}/v1")
        client.classify([b"\xff\xd8x"] * 60)
        content = _LMStudioHandler.seen[0]["messages"][0]["content"]
        assert len([p for p in content if p["type"] == "image_url"]) == 60
    finally:
        server.shutdown()


def test_engine_honours_a_high_frame_count(tmp_path):
    pytest.importorskip("cv2")
    engine, cfg, state = _engine(tmp_path, vision_frames=40)
    try:
        cfg.cache_dir.mkdir(parents=True, exist_ok=True)
        _make_video(cfg.cache_dir / "111.mp4", count=200)
        shots, _ = engine._frames_for(FakeMedia())
        assert len(shots) == 40
    finally:
        state.close()


# ==========================================================================
#  Карусель: у кожного слайда свій опис
# ==========================================================================
def test_ai_meta_is_stored_per_slide(tmp_path):
    state = State(tmp_path / "s.db")
    try:
        state.set_ai_meta("1", "art", 0.9, "Про пост загалом.", ["post"], "vlm", 6, idx=0)
        state.set_ai_meta("1", "art", 0.9, "Синій градієнт.", ["blue"], "vlm", 1, idx=1)
        state.set_ai_meta("1", "art", 0.9, "Червона типографіка.", ["red"], "vlm", 1, idx=2)

        assert state.ai_meta("1", 1)["description"] == "Синій градієнт."
        assert state.ai_meta("1", 2)["tags"] == ["red"]
        # для слайда без власного опису беремо опис поста — краще, ніж порожньо
        assert state.ai_meta("1", 7)["description"] == "Про пост загалом."
        assert state.has_ai_meta("1", 2) is True
        assert state.has_ai_meta("1", 7) is False

        bulk = state.all_ai_meta()
        assert bulk[("1", 1)]["description"] == "Синій градієнт."
        assert bulk["1"]["description"] == "Про пост загалом."
    finally:
        state.close()


def test_carousel_gets_a_description_per_picture(tmp_path, monkeypatch):
    """Головна претензія: один опис на всі слайди — неправда про кожен із них."""
    cv2 = pytest.importorskip("cv2")
    import numpy as np

    from igsaved import vision

    engine, cfg, state = _engine(tmp_path, vision_frames=4)
    try:
        cfg.root.mkdir(parents=True, exist_ok=True)
        slides = []
        for number in range(1, 4):
            path = cfg.root / f"slide{number}.jpg"
            picture = np.zeros((90, 70, 3), np.uint8)
            picture[:, :] = (number * 60, 40, 200 - number * 40)
            path.write_bytes(cv2.imencode(".jpg", picture)[1].tobytes())
            slides.append((path, number))

        asked = []

        def fake_classify(images, **kwargs):
            asked.append(len(images))
            return vision.VisionVerdict(
                category=vision.ART, confidence=0.9,
                description=f"Слайд номер {len(asked)}.",
                tags=[f"slide-{len(asked)}"], frames=len(images),
            )

        engine._vision_model = "vlm"
        engine._vision = types.SimpleNamespace(classify=fake_classify)
        media = FakeMedia(media_type=8)
        engine._describe(media, media.pk, slides)

        assert asked == [1, 1, 1]        # по одному запиту на слайд
        assert state.ai_meta(media.pk, 1)["description"] == "Слайд номер 1."
        assert state.ai_meta(media.pk, 3)["description"] == "Слайд номер 3."
        assert state.ai_meta(media.pk, 2)["tags"] == ["slide-2"]
    finally:
        state.close()


def test_video_still_gets_one_description_from_many_frames(tmp_path):
    pytest.importorskip("cv2")
    from igsaved import vision

    engine, cfg, state = _engine(tmp_path, vision_frames=5)
    try:
        cfg.root.mkdir(parents=True, exist_ok=True)
        video = _make_video(cfg.root / "clip.mp4")
        asked = []
        engine._vision_model = "vlm"
        engine._vision = types.SimpleNamespace(
            classify=lambda images, **kw: (
                asked.append(len(images)),
                vision.VisionVerdict(category=vision.ART, confidence=0.9,
                                     description="Один ролик.", tags=["clip"],
                                     frames=len(images)),
            )[1]
        )
        engine._describe(FakeMedia(), "111", [(video, 0)])
        assert asked == [5]              # один запит на п'ять кадрів
    finally:
        state.close()


def test_empty_answer_does_not_block_a_second_try(tmp_path):
    """Порожній опис, збережений мовчки, назавжди закрив би повторну спробу."""
    pytest.importorskip("cv2")
    from igsaved import vision

    engine, cfg, state = _engine(tmp_path, vision_frames=3)
    try:
        cfg.root.mkdir(parents=True, exist_ok=True)
        video = _make_video(cfg.root / "clip.mp4")
        answers = [
            vision.VisionVerdict(category=vision.ART, confidence=0.9),   # без тексту
            vision.VisionVerdict(category=vision.ART, confidence=0.9,
                                 description="З другого разу вийшло.", tags=["ok"]),
        ]
        engine._vision_model = "vlm"
        engine._vision = types.SimpleNamespace(
            classify=lambda images, **kw: answers.pop(0))

        engine._describe(FakeMedia(), "111", [(video, 0)])
        assert state.ai_meta("111") is None       # нічого не збережено

        engine._describe(FakeMedia(), "111", [(video, 0)])
        assert state.ai_meta("111")["description"] == "З другого разу вийшло."
    finally:
        state.close()


def test_a_video_is_never_sent_as_a_picture(tmp_path):
    """Раніше нерозібране відео їхало моделі як «image/jpeg» цілим mp4."""
    from igsaved import frames

    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not really a video")
    assert frames.shots_from_file(broken, 4) == []

    text = tmp_path / "notes.txt"
    text.write_bytes(b"hello")
    assert frames.shots_from_file(text, 4) == []


def test_shots_from_file_reads_pictures_and_videos(tmp_path):
    cv2 = pytest.importorskip("cv2")
    import numpy as np

    from igsaved import frames

    picture = tmp_path / "p.jpg"
    picture.write_bytes(cv2.imencode(".jpg", np.zeros((50, 50, 3), np.uint8))[1].tobytes())
    assert len(frames.shots_from_file(picture, 6)) == 1

    video = _make_video(tmp_path / "v.mp4")
    assert len(frames.shots_from_file(video, 6)) == 6


def test_ai_meta_migrates_from_the_single_key_layout(tmp_path):
    """База v2.8 мала ключ (media_pk); тепер (media_pk, idx) — описи не губимо."""
    import sqlite3

    path = tmp_path / "old.db"
    legacy = sqlite3.connect(str(path))
    legacy.executescript(
        "CREATE TABLE ai_meta (media_pk TEXT PRIMARY KEY, category TEXT,"
        " confidence REAL, description TEXT, tags TEXT, model TEXT,"
        " frames INTEGER, created_at TEXT);"
        "INSERT INTO ai_meta VALUES ('42','art',0.9,'Старий опис.','a\nb','vlm',6,'2026-01-01');"
    )
    legacy.commit()
    legacy.close()

    state = State(path)
    try:
        meta = state.ai_meta("42")
        assert meta["description"] == "Старий опис."
        assert meta["tags"] == ["a", "b"]
        assert meta["idx"] == 0
        # новий ключ працює
        state.set_ai_meta("42", "art", 0.9, "Слайд.", ["s"], "vlm", 1, idx=2)
        assert state.ai_meta("42", 2)["description"] == "Слайд."
        assert state.ai_meta_count() == 2
    finally:
        state.close()


# ==========================================================================
#  Дозапис описів у вже зібрану бібліотеку Eagle
# ==========================================================================
class _FakeEagle(BaseHTTPRequestHandler):
    """Достатньо схожий на Eagle, щоб перевірити весь шлях дозапису."""

    library = ""
    items: list = []
    updates: list = []

    def log_message(self, *a):  # тиша в тестах
        pass

    def _send(self, payload, code=200):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if "application/info" in self.path:
            self._send({"status": "success", "data": {"version": "4.0"}})
        elif "library/info" in self.path:
            self._send({"status": "success",
                        "data": {"library": {"path": self.library}}})
        elif "folder/list" in self.path:
            self._send({"status": "success", "data": [
                {"id": "root1", "name": "Instagram Saved", "children": [
                    {"id": "kid1", "name": "Пролайкане", "children": []}]}]})
        elif "item/list" in self.path:
            offset = 0
            for part in self.path.split("?", 1)[-1].split("&"):
                if part.startswith("offset="):
                    offset = int(part.split("=")[1])
            self._send({"status": "success", "data": self.items[offset:offset + 200]})
        else:
            self._send({"status": "error", "message": "nope"}, 404)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        if "item/update" in self.path:
            self.updates.append(payload)
        self._send({"status": "success", "data": {}})


def _eagle_library(root: Path, entries):
    """Розкладає файли так, як їх тримає Eagle: images/<id>.info/<name>.<ext>."""
    for item_id, name, ext, payload in entries:
        folder = root / "images" / f"{item_id}.info"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{name}.{ext}").write_bytes(payload)
        (folder / f"{name}_thumbnail.png").write_bytes(b"thumb")
        (folder / "metadata.json").write_bytes(b"{}")


def test_describe_library_fills_in_the_old_eagle_items(tmp_path, monkeypatch):
    """Синхронізація описує лише НОВЕ — стара бібліотека лишалась без описів."""
    cv2 = pytest.importorskip("cv2")
    import numpy as np

    from igsaved import vision
    from igsaved.maintenance import describe_library

    library = tmp_path / "eagle"
    picture = cv2.imencode(".jpg", np.full((80, 60, 3), 120, np.uint8))[1].tobytes()
    _eagle_library(library, [
        ("aaa", "2026-06-01_studio_Скло", "jpg", picture),
        ("bbb", "2026-06-02_studio_Метал", "jpg", picture),
    ])
    _FakeEagle.library = str(library)
    _FakeEagle.updates = []
    _FakeEagle.items = [
        {"id": "aaa", "name": "2026-06-01_studio_Скло", "ext": "jpg",
         "tags": ["instagram"], "annotation": "підпис автора",
         "url": "https://www.instagram.com/p/AAA/"},
        {"id": "bbb", "name": "2026-06-02_studio_Метал", "ext": "jpg",
         "tags": [], "annotation": "інший підпис\n\nVisual summary: already described",
         "url": "https://www.instagram.com/p/BBB/"},
    ]

    server = HTTPServer(("127.0.0.1", 0), _FakeEagle)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    state = State(tmp_path / "s.db")
    try:
        cfg = Config()
        cfg.download_dir = str(tmp_path / "dl")
        cfg.vision_enabled = True
        cfg.vision_frames = 3
        cfg.eagle_url = f"http://127.0.0.1:{server.server_port}"
        cfg.eagle_root_folder = "Instagram Saved"

        # пост відомий базі — опис має лишитись і в нас
        state.record_media("42", "AAA", "studio", None, 1, "feed", "підпис автора",
                           "https://www.instagram.com/p/AAA/", status="archived")

        monkeypatch.setattr(vision.VisionClient, "resolve_model", lambda self: "vlm")
        monkeypatch.setattr(
            vision.VisionClient, "classify",
            lambda self, images, **kw: vision.VisionVerdict(
                category=vision.ART, confidence=0.9,
                description="Скляна форма на темному тлі.",
                tags=["glass", "studio"], frames=len(images)),
        )

        lines = []
        stats = describe_library(cfg, state, log=lines.append)

        assert stats.described == 1
        assert stats.already == 1          # другий уже мав «Опис:»
        assert stats.failed == 0

        update = _FakeEagle.updates[0]
        assert update["id"] == "aaa"
        assert "Visual summary: Скляна форма на темному тлі." in update["annotation"]
        assert "підпис автора" in update["annotation"]
        assert "glass" in update["tags"] and "instagram" in update["tags"]

        # і в нашій базі теж
        assert state.ai_meta("42")["description"] == "Скляна форма на темному тлі."
    finally:
        state.close()
        server.shutdown()


def test_describe_library_says_why_it_did_nothing(tmp_path):
    from igsaved.maintenance import describe_library

    state = State(tmp_path / "s.db")
    try:
        cfg = Config()
        cfg.download_dir = str(tmp_path / "dl")
        cfg.vision_enabled = False
        lines = []
        stats = describe_library(cfg, state, log=lines.append)
        assert "вимкнена" in stats.error
        assert lines                      # мовчазної відмови не буває

        cfg.vision_enabled = True
        cfg.vision_url = "http://127.0.0.1:9/v1"
        stats = describe_library(cfg, state, log=lambda _m: None)
        assert "недоступна" in stats.error
    finally:
        state.close()


def test_eagle_item_file_is_found_next_to_the_thumbnail(tmp_path):
    from igsaved.eagle import EagleClient

    library = tmp_path / "lib"
    _eagle_library(library, [("ccc", "назва з пробілами", "mp4", b"video")])
    client = EagleClient("http://127.0.0.1:9")

    found = client.item_file(
        {"id": "ccc", "name": "назва з пробілами", "ext": "mp4"}, str(library))
    assert found and Path(found).name == "назва з пробілами.mp4"

    # прев'ю не має видавати себе за сам файл
    assert "_thumbnail" not in (found or "")
    assert client.item_file({"id": "zzz", "name": "x", "ext": "mp4"}, str(library)) is None


def test_annotation_is_not_appended_twice(tmp_path):
    from igsaved.maintenance import _strip_description
    from igsaved.tagging import annotation

    first = annotation("підпис", "перший опис")
    again = annotation(_strip_description(first), "новий опис")
    assert again.count("Visual summary:") == 1
    assert again == "підпис\n\nVisual summary: новий опис"

    # старий український заголовок теж має впізнаватись як «вже описано»
    legacy = "підпис\n\nОпис: старий опис"
    assert _strip_description(legacy) == "підпис"


# ==========================================================================
#  Контрольований словник тегів
# ==========================================================================
def test_taxonomy_keeps_only_words_it_knows():
    from igsaved.taxonomy import MARKER, Taxonomy

    tax = Taxonomy()
    kept, dropped = tax.normalize(
        ["Close-Up", "#Golden Hour", "totally-made-up", "cinematic"], mode="video")

    assert "close-up" in kept and "golden-hour" in kept and "cinematic" in kept
    assert "totally-made-up" not in kept
    assert dropped == ["totally-made-up"]
    assert kept[-1] == MARKER          # службова позначка завжди остання


def test_taxonomy_normalises_case_and_spacing():
    from igsaved.taxonomy import clean_token

    assert clean_token("  #Motion Graphics ") == "motion-graphics"
    assert clean_token("Black_And_White") == "black-and-white"
    assert clean_token("3D  Render!!") == "3d-render"
    assert clean_token("—") == ""


def test_taxonomy_maps_near_misses_to_the_right_word():
    from igsaved.taxonomy import Taxonomy

    tax = Taxonomy()
    kept, dropped = tax.normalize(["skiing", "goldfish", "computer"], mode="video")
    assert "sport-action" in kept
    assert "fish" in kept
    assert "screen" in kept
    assert dropped == []


def test_taxonomy_refuses_mutually_exclusive_tags():
    from igsaved.taxonomy import Taxonomy

    tax = Taxonomy()
    kept, _ = tax.normalize(
        ["handheld", "locked-off", "hard-light", "soft-light"], mode="video")
    assert "handheld" in kept and "locked-off" not in kept
    assert "hard-light" in kept and "soft-light" not in kept


def test_taxonomy_drops_motion_tags_from_stills():
    """Нерухома картинка не буває знята з рук — це не помилка, а режим."""
    from igsaved.taxonomy import Taxonomy

    tax = Taxonomy()
    kept, dropped = tax.normalize(["handheld", "jump-cut", "close-up"], mode="image")
    assert kept[:-1] == ["close-up"]
    assert set(dropped) == {"handheld", "jump-cut"}

    # у відео ті самі теги приймаються
    kept_video, _ = tax.normalize(["handheld", "jump-cut"], mode="video")
    assert "handheld" in kept_video and "jump-cut" in kept_video


def test_taxonomy_respects_per_category_limits():
    from igsaved.taxonomy import Taxonomy

    tax = Taxonomy()
    # COLOR дозволяє 2 — решта відсікається, а не змагається за увагу
    kept, _ = tax.normalize(
        ["warm-tones", "cool-tones", "vibrant", "desaturated", "sepia"], mode="video")
    colors = [t for t in kept if t in ("warm-tones", "cool-tones", "vibrant",
                                       "desaturated", "sepia")]
    assert len(colors) == 2
    assert colors == ["warm-tones", "cool-tones"]     # порядок моделі зберігається


def test_taxonomy_prompt_lists_differ_by_mode():
    from igsaved.taxonomy import Taxonomy

    tax = Taxonomy()
    video = tax.render("video")
    image = tax.render("image")
    assert "CAMERA MOVEMENT" in video and "CAMERA MOVEMENT" not in image
    assert "live-action" in video and "live-action" not in image
    assert "photograph" in image and "photograph" not in video
    assert "LIGHTING SOURCE" in video and "LIGHTING SOURCE" in image


def test_taxonomy_survives_a_broken_file(tmp_path):
    from igsaved.taxonomy import Taxonomy

    broken = tmp_path / "taxonomy.json"
    broken.write_text("{ не json", encoding="utf-8")
    assert Taxonomy.load(broken).known("close-up")      # мовчки беремо вбудований

    empty = tmp_path / "empty.json"
    empty.write_text('{"categories": []}', encoding="utf-8")
    assert Taxonomy.load(empty).known("close-up")


def test_taxonomy_roundtrips_and_grows(tmp_path):
    from igsaved.taxonomy import Taxonomy

    path = tmp_path / "taxonomy.json"
    tax = Taxonomy()
    assert tax.add("vaporwave", "aesthetic") is True
    assert tax.add("vaporwave", "aesthetic") is False     # двічі не додається
    assert tax.add("whatever", "no-such-category") is False
    tax.save(path)

    again = Taxonomy.load(path)
    assert again.known("vaporwave")
    kept, dropped = again.normalize(["vaporwave"], mode="video")
    assert "vaporwave" in kept and dropped == []


def test_prompt_embeds_the_vocabulary():
    from igsaved.taxonomy import Taxonomy
    from igsaved.vision import DEFAULT_PROMPT, build_prompt

    tax = Taxonomy()
    text = build_prompt("", 6, "reel", "video", tax)
    assert "{taxonomy}" not in text
    assert "golden-hour" in text and "ALLOWED TAGS" in text
    assert "VIDEO" in text
    # опис англійською — саме те, чого бракувало
    assert "ENGLISH" in DEFAULT_PROMPT

    # своя інструкція без плейсхолдера не має нічого ламати
    assert build_prompt("просто опиши", 1, "photo", "image", tax) == "просто опиши"


def test_client_enforces_the_vocabulary_on_the_answer():
    """Інструкцію модель порушує, перевірку кодом — ні."""
    from igsaved.taxonomy import Taxonomy
    from igsaved.vision import VisionClient

    class _Invents(_LMStudioHandler):
        answer = ('{"category": "art", "confidence": 0.9, "description": "A glass form.",'
                  ' "tags": ["Close-Up", "skiing", "totally-invented", "cinematic"]}')

    server = HTTPServer(("127.0.0.1", 0), _Invents)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        client = VisionClient(f"http://127.0.0.1:{server.server_port}/v1",
                              taxonomy=Taxonomy())
        verdict = client.classify([b"\xff\xd8x"], kind="reel")
        assert "close-up" in verdict.tags
        assert "sport-action" in verdict.tags        # skiing прийняв алiас
        assert "totally-invented" not in verdict.tags
        assert verdict.dropped == ["totally-invented"]
        assert verdict.tags[-1] == "autotagged"
    finally:
        server.shutdown()


def test_state_counts_what_the_dictionary_rejected(tmp_path):
    state = State(tmp_path / "s.db")
    try:
        for _ in range(6):
            state.note_tag_candidates(["vaporwave"])
        state.note_tag_candidates(["one-off"])

        assert state.tag_candidate_count(5) == 1
        assert state.tag_candidate_count(1) == 2
        top = state.tag_candidates(5)
        assert top[0]["tag"] == "vaporwave" and top[0]["hits"] == 6

        state.resolve_tag_candidate("vaporwave", "added")
        assert state.tag_candidate_count(5) == 0
        # відхилене більше не спливає
        state.resolve_tag_candidate("one-off", "ignored")
        assert state.tag_candidate_count(1) == 0
    finally:
        state.close()


def test_mode_for_tells_video_from_still():
    from igsaved.taxonomy import IMAGE, VIDEO, mode_for

    assert mode_for("clip.mp4") == VIDEO
    assert mode_for("clip.MOV") == VIDEO
    assert mode_for("reel") == VIDEO
    assert mode_for("slide.jpg") == IMAGE
    assert mode_for("carousel") == IMAGE
    assert mode_for("") == IMAGE


# ==========================================================================
#  Один пост — один елемент у Eagle
# ==========================================================================
def test_post_in_two_collections_is_imported_once(tmp_path):
    """Справжня причина «одні й ті самі відоси»: Eagle КОПІЮЄ файл на кожен
    імпорт, тож пост зі збережених і з лайків ставав двома елементами."""
    from igsaved.instagram import CollectionInfo

    engine, cfg, state = _engine(tmp_path)
    try:
        engine.eagle = object()          # достатньо, щоб черга працювала
        engine._eagle_folder = lambda col: f"folder-{col.pk}"
        media = FakeMedia(pk="777")
        saved = CollectionInfo("ALL_MEDIA_AUTO_COLLECTION", "Усі збережені", 0,
                               is_all_saved=True)
        liked = CollectionInfo("liked", "Пролайкане", 0, is_liked=True)

        engine._queue_eagle(media, saved, [Path("a.mp4")])
        assert len(engine._eagle_queue) == 1
        # той самий пост із другої підбірки — другої копії бути не має
        engine._queue_eagle(media, liked, [Path("a.mp4")])
        assert len(engine._eagle_queue) == 1

        engine._eagle_queue = []
        state.mark_in_eagle("777", "ALL_MEDIA_AUTO_COLLECTION", "folder-1")
        engine._queue_eagle(media, liked, [Path("a.mp4")])
        assert engine._eagle_queue == []
    finally:
        state.close()


def test_per_collection_copies_stay_possible_when_asked(tmp_path):
    from igsaved.instagram import CollectionInfo

    engine, cfg, state = _engine(tmp_path, eagle_one_item_per_post=False)
    try:
        engine.eagle = object()
        engine._eagle_folder = lambda col: f"folder-{col.pk}"
        state.mark_in_eagle("777", "ALL_MEDIA_AUTO_COLLECTION", "folder-1")
        engine._queue_eagle(FakeMedia(pk="777"),
                            CollectionInfo("liked", "Пролайкане", 0, is_liked=True),
                            [Path("a.mp4")])
        assert len(engine._eagle_queue) == 1
    finally:
        state.close()


def test_is_in_eagle_answers_anywhere_and_per_collection(tmp_path):
    state = State(tmp_path / "s.db")
    try:
        state.mark_in_eagle("1", "liked", "f1")
        assert state.is_in_eagle("1") is True                 # будь-де
        assert state.is_in_eagle("1", "liked") is True
        assert state.is_in_eagle("1", "ALL_MEDIA_AUTO_COLLECTION") is False
        assert state.is_in_eagle("2") is False

        state.mark_in_eagle("1", "Пролайкане", "f2")          # ключ назвою — стара помилка
        dupes = state.eagle_duplicates()
        assert len(dupes) == 1 and dupes[0]["copies"] == 2
    finally:
        state.close()


def test_maintenance_uses_collection_pk_not_name(tmp_path):
    """Синхронізація позначала імпорт за pk, а дозалив питав назвою — і не
    впізнавав уже імпортоване. Саме звідси бралась друга копія."""
    from igsaved.maintenance import _from_state

    state = State(tmp_path / "s.db")
    try:
        media_file = tmp_path / "clip.mp4"
        media_file.write_bytes(b"x")
        state.record_media("1", "c", "bob", None, 2, "clips", "", "https://ig/1")
        state.add_file(str(media_file), "1", "video", 0, 1)
        state.add_membership("1", "liked", "Пролайкане")

        post = _from_state(state)["1"]
        assert post.collections == ["Пролайкане"]     # для людини
        assert post.collection_pks == ["liked"]       # для Eagle
    finally:
        state.close()


def test_duplicate_finder_spots_repeated_imports(tmp_path):
    """Карусель — це різні файли одного поста, і дублікатом вона не є."""
    from igsaved.maintenance import find_eagle_duplicates

    _FakeEagle.library = str(tmp_path)
    _FakeEagle.updates = []
    _FakeEagle.items = [
        {"id": "a1", "name": "clip", "ext": "mp4", "url": "https://ig/p/AAA/",
         "modificationTime": 100, "tags": [], "annotation": ""},
        {"id": "a2", "name": "clip", "ext": "mp4", "url": "https://ig/p/AAA",
         "modificationTime": 200, "tags": [], "annotation": ""},
        {"id": "b1", "name": "carousel_01", "ext": "jpg", "url": "https://ig/p/BBB/",
         "modificationTime": 100, "tags": [], "annotation": ""},
        {"id": "b2", "name": "carousel_02", "ext": "jpg", "url": "https://ig/p/BBB/",
         "modificationTime": 100, "tags": [], "annotation": ""},
    ]
    server = HTTPServer(("127.0.0.1", 0), _FakeEagle)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    state = State(tmp_path / "s.db")
    try:
        cfg = Config()
        cfg.download_dir = str(tmp_path / "dl")
        cfg.eagle_url = f"http://127.0.0.1:{server.server_port}"

        stats = find_eagle_duplicates(cfg, state, log=lambda _m: None)
        assert stats.scanned == 4
        assert stats.groups == 1        # лише clip, карусель не рахується
        assert stats.extra == 1
        assert stats.removed == 0       # без прямої вказівки нічого не чіпаємо
    finally:
        state.close()
        server.shutdown()


def test_review_queue_is_one_list_with_origin_marks(tmp_path):
    """Дві вкладки виконували ту саму роботу — злиті в одну.

    Походження не зникло: воно лишилось у колонці source і показується
    позначкою на картці, бо від нього залежить, що саме означає «Залишити».
    """
    from igsaved.state import REVIEW_MODEL, REVIEW_RULES

    state = State(tmp_path / "s.db")
    try:
        state.add_review("1", "/a.mp4", "", "alice", "", "", "порівну ознак",
                         "review", source=REVIEW_RULES)
        state.add_review("2", "/b.mp4", "", "bob", "", "", "модель: арт",
                         "download", source=REVIEW_MODEL)
        state.add_review("3", "/c.mp4", "", "carol", "", "", "модель: мем",
                         "skip", source=REVIEW_MODEL)

        rows = state.pending_review()
        assert len(rows) == 3                      # одна спільна черга
        # відсіяне моделлю стоїть першим: рятувати треба саме його
        assert rows[0]["media_pk"] == "3"
        assert {r["media_pk"]: r["source"] for r in rows} == {
            "1": REVIEW_RULES, "2": REVIEW_MODEL, "3": REVIEW_MODEL}

        # фільтр за походженням лишився — ним користується «Погодитись з моделлю»
        assert len(state.pending_review(REVIEW_MODEL)) == 2
        assert state.review_count() == 3
    finally:
        state.close()


# ==========================================================================
#  Запобіжник частоти: Instagram надіслав попередження про автоматизацію
# ==========================================================================
def test_engine_skips_a_run_that_comes_too_soon(tmp_path, monkeypatch):
    """15 проходів за дві доби, деякі з різницею у 2 хвилини — саме той
    візерунок, за який приходить попередження. Тепер він неможливий."""
    engine, cfg, state = _engine(tmp_path, min_hours_between_runs=6.0)
    try:
        lines = []
        engine.log = lines.append
        connected = []
        monkeypatch.setattr(engine.ig, "connect", lambda sid: connected.append(sid))

        # перший прохід дозволено: попередніх немає
        assert engine.cooldown_left() == 0.0

        run_id = state.start_run()
        state.finish_run(run_id, 10, 1, 0, 0, "")
        assert engine.cooldown_left() > 5.9

        stats = engine.run()
        assert connected == []                     # до Instagram навіть не постукали
        assert "cooldown" in stats.errors
        assert any("Пропускаю прохід" in line for line in lines)
        assert any("автоматизацію" in line for line in lines)   # сказано чому
    finally:
        state.close()


def test_manual_run_can_override_the_cooldown(tmp_path, monkeypatch):
    """Людина має право наполягти — але свідомо, а не випадково."""
    engine, cfg, state = _engine(tmp_path, min_hours_between_runs=6.0)
    try:
        run_id = state.start_run()
        state.finish_run(run_id, 10, 1, 0, 0, "")

        monkeypatch.setattr(engine.ig, "connect", lambda sid: "user")
        monkeypatch.setattr(engine, "_pick_collections", lambda only: [])
        stats = engine.run(force=True)
        assert "cooldown" not in stats.errors
    finally:
        state.close()


def test_cooldown_can_be_switched_off(tmp_path):
    engine, cfg, state = _engine(tmp_path, min_hours_between_runs=0)
    try:
        run_id = state.start_run()
        state.finish_run(run_id, 10, 1, 0, 0, "")
        assert engine.cooldown_left() == 0.0
    finally:
        state.close()


def test_state_measures_time_since_the_last_run(tmp_path):
    from datetime import datetime, timedelta, timezone

    state = State(tmp_path / "s.db")
    try:
        assert state.hours_since_last_run() is None      # проходів ще не було

        run_id = state.start_run()
        state.finish_run(run_id, 1, 0, 0, 0, "")
        assert 0 <= state.hours_since_last_run() < 0.1

        # незавершений прохід не рахується — інакше падіння блокувало б наступний
        long_ago = (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat(timespec="seconds")
        state.db.execute("UPDATE runs SET finished_at = ?", (long_ago,))
        state.db.commit()
        state.start_run()
        assert state.hours_since_last_run() > 9
    finally:
        state.close()


def test_delays_are_raised_for_configs_that_never_touched_them(tmp_path):
    """Старі 2–5 с виявились надто жвавими; хто їх не міняв — отримує спокійніші."""
    path = tmp_path / "c.json"
    path.write_text('{"page_delay_min": 2.0, "page_delay_max": 5.0, "download_delay": 0.4}',
                    encoding="utf-8")
    cfg = Config.load(path)
    assert (cfg.page_delay_min, cfg.page_delay_max) == (8.0, 15.0)
    assert cfg.download_delay == 1.0

    # свідомо виставлені значення не чіпаємо
    path.write_text('{"page_delay_min": 3.0, "page_delay_max": 7.0, "download_delay": 0.9}',
                    encoding="utf-8")
    cfg = Config.load(path)
    assert (cfg.page_delay_min, cfg.page_delay_max, cfg.download_delay) == (3.0, 7.0, 0.9)


def test_saved_session_is_reused_instead_of_logging_in_again(tmp_path):
    """Повторний вхід — найпомітніший слід автоматизації. Живу сесію не чіпаємо."""
    import types as _types

    from igsaved.instagram import IGClient

    client = IGClient(log=lambda _m: None)
    logins = []

    fake = _types.SimpleNamespace(
        get_settings=lambda: {
            "cookies": {"sessionid": "12345%3AoldTOKEN"},
            "authorization_data": {"ds_user_id": "12345"},
        },
        account_info=lambda: _types.SimpleNamespace(username="amriel"),
        login_by_sessionid=lambda sid: logins.append(sid) or True,
        username="amriel",
    )
    client._client = fake

    assert client.connect("12345%3AnewTOKEN") == "amriel"
    assert logins == []                       # входу не було


def test_full_login_still_happens_for_a_different_account(tmp_path):
    import types as _types

    from igsaved.instagram import IGClient

    client = IGClient(log=lambda _m: None)
    logins = []
    fake = _types.SimpleNamespace(
        get_settings=lambda: {
            "cookies": {"sessionid": "99999%3AotherUSER"},
            "authorization_data": {"ds_user_id": "99999"},
        },
        account_info=lambda: _types.SimpleNamespace(username="someone"),
        login_by_sessionid=lambda sid: logins.append(sid) or True,
        username="someone",
        dump_settings=lambda path: None,
    )
    client._client = fake

    client.connect("12345%3AmyTOKEN")
    assert logins == ["12345%3AmyTOKEN"]      # чужий профіль → повний вхід


# ==========================================================================
#  Перевалка без осаду: Eagle — база, локальні файли прибираються
# ==========================================================================
def test_cleanup_candidates_query(tmp_path):
    """Кандидат = завантажений + позначений у Eagle + не в черзі перегляду."""
    state = State(tmp_path / "s.db")
    try:
        for pk, status in (("1", "done"), ("2", "done"), ("3", "done"), ("4", "archived")):
            state.record_media(pk, f"c{pk}", "user", None, 2, "clips", "",
                               f"https://ig/p/{pk}/", status=status)
            state.mark_done(pk, status)
            state.add_file(str(tmp_path / f"{pk}.mp4"), pk, "video", 0, 1)

        state.mark_in_eagle("1", "liked", "f")        # кандидат
        state.mark_in_eagle("2", "liked", "f")
        state.add_review("2", "", "", "u", "", "", "", "review")   # чекає рішення
        # 3 — не в Eagle; 4 — уже archived

        picked = {str(r["pk"]) for r in state.cleanup_candidates()}
        assert picked == {"1"}
    finally:
        state.close()


def test_cleanup_deletes_only_what_eagle_confirms(tmp_path, monkeypatch):
    """Своя позначка каже «відправили», а не «воно там є» — звіряємось із
    реальним вмістом бібліотеки, бо копіювання на боці Eagle асинхронне."""
    engine, cfg, state = _engine(tmp_path, eagle_delete_local_after_import=True)
    try:
        cfg.root.mkdir(parents=True, exist_ok=True)
        confirmed = cfg.root / "a.mp4"
        confirmed.write_bytes(b"x")
        thumb = cfg.root / "a_thumb.jpg"
        thumb.write_bytes(b"t")
        pending = cfg.root / "b.mp4"
        pending.write_bytes(b"y")

        for pk, path, url in (("1", confirmed, "https://ig/p/AAA/"),
                              ("2", pending, "https://ig/p/BBB/")):
            state.record_media(pk, "c", "user", None, 2, "clips", "", url, status="done")
            state.mark_done(pk)
            state.add_file(str(path), pk, "video", 0, 1)
            state.mark_in_eagle(pk, "liked", "f")
        state.add_file(str(thumb), "1", "thumb", 0, 1)

        engine.eagle = object()   # достатньо, щоб не відсіктись на guard
        # Eagle «бачить» лише перший пост — другий ще копіюється
        monkeypatch.setattr(engine, "_eagle_urls", lambda: {"https://ig/p/AAA"})
        lines = []
        engine.log = lines.append

        engine._cleanup_imported()

        assert not confirmed.exists() and not thumb.exists()   # підтверджене — прибрано
        assert pending.exists()                                # непідтверджене — чекає
        assert state.is_known("1") is True                     # памʼять лишилась
        assert not state.files_exist("1")
        assert any("Перевалку прибрано" in line for line in lines)
        assert any("не видно в бібліотеці" in line for line in lines)
    finally:
        state.close()


def test_cleanup_does_nothing_when_eagle_is_silent(tmp_path, monkeypatch):
    """Немає відповіді від Eagle ≠ файлів немає. Мовчання — не привід видаляти."""
    engine, cfg, state = _engine(tmp_path, eagle_delete_local_after_import=True)
    try:
        cfg.root.mkdir(parents=True, exist_ok=True)
        media = cfg.root / "a.mp4"
        media.write_bytes(b"x")
        state.record_media("1", "c", "u", None, 2, "clips", "", "https://ig/p/A/", status="done")
        state.mark_done("1")
        state.add_file(str(media), "1", "video", 0, 1)
        state.mark_in_eagle("1", "liked", "f")

        engine.eagle = object()
        monkeypatch.setattr(engine, "_eagle_urls", lambda: None)
        engine._cleanup_imported()
        assert media.exists()

        # і при вимкненій галочці — теж нічого
        cfg.eagle_delete_local_after_import = False
        monkeypatch.setattr(engine, "_eagle_urls",
                            lambda: (_ for _ in ()).throw(AssertionError("не мав питати")))
        engine._cleanup_imported()
        assert media.exists()
    finally:
        state.close()
