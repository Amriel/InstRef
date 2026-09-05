"""Сторінки головного вікна — блоки налаштувань і дій, зібрані за темами.

Це домішка (mixin) до MainWindow: кожен метод `_sec_*` будує один блок і
вішає віджети на `self` під тими самими іменами, які читають
`_load_config_into_ui` / `_collect_ui_into_config`. Обробники (`on_*`)
живуть у main_window.py — тут лише те, що видно.

Принцип каталогізації: одна сторінка — одна тема, всередині сторінки —
внутрішні вкладки за підтемами. «Що качати» лежить біля підбірок, а не в
загальних налаштуваннях; захист акаунта — біля сесії, бо це про акаунт;
обслуговування бібліотеки Eagle — на сторінці Eagle.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QPlainTextEdit, QProgressBar, QPushButton, QSpinBox, QTableWidget,
    QTimeEdit, QVBoxLayout, QWidget,
)

from .. import APP_NAME, __version__
from ..config import (
    DEFAULT_TEMPLATE, SCHED_DAILY, SCHED_HOURLY, SCHED_ONLOGON, SCHED_WEEKLY,
    SCHEDULE_LABELS, STRUCTURE_FLAT, STRUCTURE_PER_COLLECTION, TEMPLATE_TOKENS, WEEKDAYS,
)
from ..session import BROWSER_LABELS, MANUAL_HELP
from ..vision import MAX_FRAMES as VISION_MAX_FRAMES, PLACEHOLDERS as VISION_PLACEHOLDERS
from .widgets import _flabel, _gap, _hint, _label, _left, _row, _section

DESCRIBE_LABEL = "Описати бібліотеку моделлю"


class PagesMixin:
    """Будівники блоків. Очікує, що self — MainWindow з обробниками on_*."""

    # ======================================================== Синхронізація
    def _sec_collections(self) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        bar = QHBoxLayout()
        self.btn_refresh = QPushButton("Оновити підбірки")
        self.btn_refresh.clicked.connect(self.on_refresh_collections)
        self.btn_check_all = QPushButton("Позначити всі")
        self.btn_check_all.clicked.connect(lambda: self._set_all_checks(True))
        self.btn_uncheck_all = QPushButton("Зняти всі")
        self.btn_uncheck_all.clicked.connect(lambda: self._set_all_checks(False))
        bar.addWidget(self.btn_refresh)
        bar.addWidget(self.btn_check_all)
        bar.addWidget(self.btn_uncheck_all)
        bar.addStretch(1)
        self.btn_urls = QPushButton("За посиланням…")
        self.btn_urls.setToolTip(
            "Завантажити конкретні пости за адресами — той самий конвеєр:\n"
            "завантаження → опис → Eagle, без обходу підбірок."
        )
        self.btn_urls.clicked.connect(self.on_download_urls)
        bar.addWidget(self.btn_urls)
        self.btn_open_folder = QPushButton("Відкрити папку")
        self.btn_open_folder.clicked.connect(self.on_open_folder)
        bar.addWidget(self.btn_open_folder)
        layout.addLayout(bar)

        layout.addWidget(_hint(
            "Перша галочка — синхронізувати підбірку; «Опис» — чи показувати її "
            "пости моделі заради опису й тегів. Галочки зберігаються самі."))

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["", "Підбірка", "В Instagram", "Завантажено", "Опис"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        head = self.table.horizontalHeader()
        head.setSectionResizeMode(0, QHeaderView.Fixed)
        head.setSectionResizeMode(1, QHeaderView.Stretch)
        head.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        head.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        head.setSectionResizeMode(4, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 36)
        self.table.setColumnWidth(4, 52)
        self.table.itemChanged.connect(self._on_table_item_changed)
        layout.addWidget(self.table, 1)
        return box

    def _sec_download_what(self) -> QWidget:
        box, form = _section("Що і куди зберігати")

        self.ed_dir = QLineEdit()
        self.btn_pick_dir = QPushButton("Огляд…")
        self.btn_pick_dir.setFixedWidth(90)
        self.btn_pick_dir.clicked.connect(self.on_pick_dir)
        folder = QHBoxLayout()
        folder.addWidget(self.ed_dir, 1)
        folder.addWidget(self.btn_pick_dir)
        form.addRow(_flabel("Папка"), _row(folder))
        form.addRow(_flabel(""), _hint(
            "Перевалка, а не архів: Eagle зберігає власну копію, а локальні файли "
            "можна прибирати після імпорту (сторінка Eagle → Прибирання)."))

        self.cb_structure = QComboBox()
        self.cb_structure.addItem("Усе в одну папку (рекомендовано з Eagle)", STRUCTURE_FLAT)
        self.cb_structure.addItem("Підпапка на кожну підбірку", STRUCTURE_PER_COLLECTION)
        form.addRow(_flabel("Структура"), self.cb_structure)

        self.ck_videos = QCheckBox("Відео / Reels")
        self.ck_photos = QCheckBox("Фото та каруселі")
        self.ck_thumbs = QCheckBox("Превʼю (.jpg)")
        types = QHBoxLayout()
        for widget in (self.ck_videos, self.ck_photos, self.ck_thumbs):
            types.addWidget(widget)
        types.addStretch(1)
        form.addRow(_flabel("Качати"), _row(types))

        self.ck_embed = QCheckBox("Вшивати у файл")
        self.ck_embed.setToolTip(
            "Опис, автор, дата, посилання й хештеги записуються всередину самого\n"
            "mp4 чи jpg. Видно в плеєрах, у Eagle і у властивостях файлу Windows."
        )
        self.ck_meta = QCheckBox("Ще й окремим .json")
        self.ck_meta.setToolTip("Додатковий файл поруч — з повними даними поста.")
        meta_row = QHBoxLayout()
        meta_row.addWidget(self.ck_embed)
        meta_row.addWidget(self.ck_meta)
        meta_row.addStretch(1)
        form.addRow(_flabel("Опис і автор"), _row(meta_row))

        _gap(form)

        self.ed_template = QLineEdit()
        self.ed_template.setPlaceholderText(DEFAULT_TEMPLATE)
        self.ed_template.setToolTip(
            "Доступні токени:\n" + "\n".join(f"{k} — {v}" for k, v in TEMPLATE_TOKENS.items())
        )
        form.addRow(_flabel("Шаблон імені"), self.ed_template)
        form.addRow(_flabel(""), _hint(" ".join(TEMPLATE_TOKENS)))

        self.sp_maxsize = QSpinBox()
        self.sp_maxsize.setRange(0, 20000)
        self.sp_maxsize.setSuffix(" МБ")
        self.sp_maxsize.setSpecialValueText("без обмеження")
        self.sp_maxsize.setFixedWidth(185)
        form.addRow(_flabel("Пропускати більші за"), _row(_left(self.sp_maxsize)))
        return box

    def _sec_scan(self) -> QWidget:
        box, form = _section("Як обходити збережене")

        self.ck_incremental = QCheckBox("Лише нове (пропускати вже завантажене)")
        form.addRow(_flabel(""), self.ck_incremental)

        self.sp_stop_known = QSpinBox()
        self.sp_stop_known.setRange(0, 500)
        self.sp_stop_known.setSuffix(" постів")
        self.sp_stop_known.setFixedWidth(185)
        self.sp_stop_known.setToolTip(
            "Скільки вже відомих постів поспіль зустріти, щоб зупинити обхід підбірки.\n"
            "0 — завжди проходити підбірку до кінця."
        )
        form.addRow(_flabel("Стоп після"), _row(_left(self.sp_stop_known)))

        self.sp_scan_limit = QSpinBox()
        self.sp_scan_limit.setRange(0, 100000)
        self.sp_scan_limit.setSpecialValueText("без обмеження")
        self.sp_scan_limit.setSuffix(" постів")
        self.sp_scan_limit.setFixedWidth(185)
        form.addRow(_flabel("Дивитись останніх"), _row(_left(self.sp_scan_limit)))
        form.addRow(_flabel(""), _hint(
            "Обмежує обхід збережених і підбірок найсвіжішими постами. "
            "У пролайканого свій ліміт — у вкладці «Лайки»."))

        self.sp_limit = QSpinBox()
        self.sp_limit.setRange(0, 100000)
        self.sp_limit.setSpecialValueText("без ліміту")
        self.sp_limit.setFixedWidth(185)
        form.addRow(_flabel("Максимум нових за запуск"), _row(_left(self.sp_limit)))
        form.addRow(_flabel(""), _hint(
            "Паузи між запитами, межа між проходами й реакція на «зачекай» від "
            "Instagram — на сторінці «Акаунт» → «Захист»."))
        return box

    def _sec_liked(self) -> QWidget:
        box, form = _section("Пролайкане та фільтр мемів")

        self.ck_sync_liked = QCheckBox("Синхронізувати пролайкане")
        form.addRow(_flabel(""), self.ck_sync_liked)
        form.addRow(_flabel(""), _hint(
            "Зʼявиться окремою підбіркою у списку, в Eagle — своєю папкою."))

        self.sp_liked_limit = QSpinBox()
        self.sp_liked_limit.setRange(0, 100000)
        self.sp_liked_limit.setSpecialValueText("без обмеження")
        self.sp_liked_limit.setSuffix(" постів")
        self.sp_liked_limit.setFixedWidth(185)
        form.addRow(_flabel("Дивитись останніх"), _row(_left(self.sp_liked_limit)))
        form.addRow(_flabel(""), _hint(
            "Діє лише для першого проходу: далі застосунок памʼятає, де зупинився, "
            "і читає лайки до вже переглянутого."))

        _gap(form)

        self.ck_classify = QCheckBox("Відсівати меми")
        self.ck_classify.setToolTip("Працює тільки для пролайканого. Збережене качається як є.")
        form.addRow(_flabel(""), self.ck_classify)
        form.addRow(_flabel(""), _hint(
            "Оцінка за хештегами, іменем акаунта, тривалістю та позначкою реклами. "
            "Явні випадки визначає добре, межові їдуть у перегляд, а не видаляються."))

        self.cb_uncertain = QComboBox()
        self.cb_uncertain.addItem("Відкласти на перегляд", "review")
        self.cb_uncertain.addItem("Качати", "download")
        self.cb_uncertain.addItem("Пропускати", "skip")
        self.cb_uncertain.setFixedWidth(240)
        form.addRow(_flabel("Сумнівні пости"), _row(_left(self.cb_uncertain)))

        self.sp_meme_seconds = QDoubleSpinBox()
        self.sp_meme_seconds.setRange(0.0, 120.0)
        self.sp_meme_seconds.setSingleStep(1.0)
        self.sp_meme_seconds.setSuffix(" с")
        self.sp_meme_seconds.setSpecialValueText("не зважати")
        self.sp_meme_seconds.setFixedWidth(150)
        self.sp_meme_seconds.setToolTip(
            "Коротке відео без арт-ознак додає бал на користь мема. 0 — вимкнено.")
        form.addRow(_flabel("Короткі відео до"), _row(_left(self.sp_meme_seconds)))

        _gap(form)

        self.ed_block = QLineEdit()
        self.ed_block.setPlaceholderText("memepage, 9gag — через кому")
        form.addRow(_flabel("Ніколи не качати"), self.ed_block)
        self.ed_allow = QLineEdit()
        self.ed_allow.setPlaceholderText("studioalt, formnorm — через кому")
        form.addRow(_flabel("Завжди качати"), self.ed_allow)
        form.addRow(_flabel(""), _hint(
            "Списки акаунтів мають пріоритет над усіма правилами й поповнюються "
            "кнопками «Завжди» / «Ніколи» в перегляді."))

        _gap(form)

        self.ed_meme_tags = QLineEdit()
        self.ed_meme_tags.setPlaceholderText("додаткові хештеги мемів, через кому")
        form.addRow(_flabel("Мем-хештеги"), self.ed_meme_tags)
        self.ed_art_tags = QLineEdit()
        self.ed_art_tags.setPlaceholderText("додаткові хештеги арту, через кому")
        form.addRow(_flabel("Арт-хештеги"), self.ed_art_tags)
        form.addRow(_flabel(""), _hint(
            "Що робить із сумнівними постами візуальна модель — на сторінці «Модель» → «Рішення»."))
        return box

    # ================================================================ Модель
    def _sec_vision_connection(self) -> QWidget:
        box, form = _section("LM Studio")

        self.ck_vision = QCheckBox("Використовувати візуальну модель")
        self.ck_vision.setToolTip(
            "Модель дивиться кадри поста: пише опис і теги, а для лайків ще й\n"
            "вирішує мем / арт / реклама / гра / інше."
        )
        form.addRow(_flabel(""), self.ck_vision)
        form.addRow(_flabel(""), _hint(
            "У LM Studio має бути завантажена візуальна модель і увімкнений сервер "
            "(Developer → Start Server). Адресу можна вставляти як є — /v1 допишеться "
            "сам. Якщо сервер не відповідає, застосунок каже про це й працює за правилами."))

        self.ed_vision_url = QLineEdit()
        self.ed_vision_url.setPlaceholderText("http://localhost:1234")
        form.addRow(_flabel("Адреса LM Studio"), self.ed_vision_url)

        self.cb_vision_model = QComboBox()
        self.cb_vision_model.setEditable(True)
        self.cb_vision_model.lineEdit().setPlaceholderText("порожньо — перша завантажена модель")
        self.btn_vision_test = QPushButton("Перевірити")
        self.btn_vision_test.clicked.connect(self.on_test_vision)
        model_row = QHBoxLayout()
        model_row.addWidget(self.cb_vision_model, 1)
        model_row.addWidget(self.btn_vision_test)
        form.addRow(_flabel("Модель"), _row(model_row))
        self.lbl_vision = _hint("")
        form.addRow(_flabel(""), self.lbl_vision)
        form.addRow(_flabel(""), _hint(
            "Радимо Qwen3-VL-4B-Instruct — Instruct, не Thinking: reasoning-варіанти "
            "витрачають сотні токенів на роздуми замість одного рядка JSON."))

        self.sp_vision_timeout = QSpinBox()
        self.sp_vision_timeout.setRange(10, 900)
        self.sp_vision_timeout.setSuffix(" с")
        self.sp_vision_timeout.setFixedWidth(120)
        self.sp_vision_timeout.setToolTip(
            "Кілька кадрів плюс опис — це довше за одну обкладинку.\n"
            "Якщо в журналі часто «модель не встигла відповісти», збільш."
        )
        form.addRow(_flabel("Чекати відповідь до"), _row(_left(self.sp_vision_timeout)))

        _gap(form)

        self.ck_vision_describe = QCheckBox("Писати опис і теги для кожного нового поста")
        self.ck_vision_describe.setToolTip("Не лише для сумнівних: модель дивиться кожне нове завантаження.")
        form.addRow(_flabel(""), self.ck_vision_describe)
        form.addRow(_flabel(""), _hint(
            "Збережене модель не судить — лише описує, і воно одразу йде в Eagle. "
            "Кожен пост — окремий запит, тож синхронізація повільніша. Яким підбіркам "
            "опис не потрібен — галочка «Опис» у таблиці підбірок."))

        self.ck_transcribe = QCheckBox("Транскрибувати голос за кадром (faster-whisper)")
        self.ck_transcribe.setToolTip(
            "Туторіали пояснюють техніку словами — кадри цього не передають.\n"
            "Потрібен пакет faster-whisper: pip install faster-whisper."
        )
        self.ed_whisper = QLineEdit()
        self.ed_whisper.setPlaceholderText("small")
        self.ed_whisper.setFixedWidth(120)
        self.ed_whisper.setToolTip("Розмір моделі Whisper: tiny / base / small / medium")
        transcribe_row = QHBoxLayout()
        transcribe_row.addWidget(self.ck_transcribe)
        transcribe_row.addWidget(self.ed_whisper)
        transcribe_row.addStretch(1)
        form.addRow(_flabel(""), _row(transcribe_row))
        form.addRow(_flabel(""), _hint(
            "Текст мовлення йде в інструкцію моделі як контекст і в нотатку Eagle. "
            "Повільно (ЦП) — вмикай для підбірок із туторіалами."))
        return box

    def _sec_vision_frames(self) -> QWidget:
        box, form = _section(
            "Кадри",
            "Обкладинка reels — часто чорний кадр або титр; модель дивиться ролик "
            "цілком. Каруселі — по слайдах.")

        self.sp_vision_frames = QSpinBox()
        self.sp_vision_frames.setRange(1, VISION_MAX_FRAMES)
        self.sp_vision_frames.setSuffix(" кадр(ів)")
        self.sp_vision_frames.setFixedWidth(185)
        self.sp_vision_frames.valueChanged.connect(self._update_frames_note)
        form.addRow(_flabel("Мінімум на відео"), _row(_left(self.sp_vision_frames)))
        self.lbl_frames = _hint("")
        form.addRow(_flabel(""), self.lbl_frames)

        self.sp_sec_per_frame = QDoubleSpinBox()
        self.sp_sec_per_frame.setRange(0.0, 60.0)
        self.sp_sec_per_frame.setDecimals(0)
        self.sp_sec_per_frame.setSuffix(" с на кадр")
        self.sp_sec_per_frame.setSpecialValueText("завжди стільки, як вище")
        self.sp_sec_per_frame.setFixedWidth(185)
        form.addRow(_flabel("Кадрів від тривалості"), _row(_left(self.sp_sec_per_frame)))
        form.addRow(_flabel(""), _hint(
            "Приблизно один кадр на стільки секунд ролика — не менше мінімуму і не "
            f"більше стелі {VISION_MAX_FRAMES}. Десятисекундний reel і трихвилинний "
            "туторіал не заслуговують однакової кількості кадрів."))

        self.ck_by_scene = QCheckBox("Брати кадри за зміною сцени, минаючи чорні")
        self.ck_by_scene.setToolTip(
            "Замість рівних кроків — середина кожної монтажної сцени.\n"
            "У ролику з жорстким монтажем рівний крок влучає в переходи."
        )
        form.addRow(_flabel(""), self.ck_by_scene)
        return box

    def _sec_vision_decisions(self) -> QWidget:
        box, form = _section(
            "Рішення по лайках",
            "Стосується лише пролайканого: збережене модель не судить.")

        self.ck_vision_meme = QCheckBox("меми")
        self.ck_vision_game = QCheckBox("ігрові")
        skip_row = QHBoxLayout()
        skip_row.addWidget(self.ck_vision_meme)
        skip_row.addWidget(self.ck_vision_game)
        skip_row.addStretch(1)
        form.addRow(_flabel("Модель відсіює"), _row(skip_row))
        form.addRow(_flabel(""), _hint(
            "Арт і рекламу модель пропускає на завантаження, «інше» лишає тобі в перегляді."))

        self.sp_vision_conf = QDoubleSpinBox()
        self.sp_vision_conf.setRange(0.0, 1.0)
        self.sp_vision_conf.setSingleStep(0.05)
        self.sp_vision_conf.setDecimals(2)
        self.sp_vision_conf.setFixedWidth(120)
        form.addRow(_flabel("Мінімальна впевненість"), _row(_left(self.sp_vision_conf)))
        form.addRow(_flabel(""), _hint(
            "Малі моделі майже завжди кажуть 0.95, тож справжня точність — це "
            "«Згода з моделлю» у перегляді, а не ця цифра."))

        self.ck_model_glance = QCheckBox("Рішення моделі спершу показувати мені")
        self.ck_model_glance.setToolTip("Модель вирішила — але в Eagle пост іде тільки після твого погляду.")
        form.addRow(_flabel(""), self.ck_model_glance)
        form.addRow(_flabel(""), _hint(
            "Схвалене лягає в основну теку й чекає підтвердження; відсіяне лежить у теці "
            "перегляду, доки не погодишся видалити. Без галочки модель діє мовчки."))
        return box

    def _sec_vision_prompt(self) -> QWidget:
        box, form = _section("Інструкція для моделі")

        prompt_head = QHBoxLayout()
        prompt_head.addWidget(QLabel("Що саме модель має зробити з кадрами"))
        prompt_head.addStretch(1)
        self.btn_prompt_reset = QPushButton("Повернути типову")
        self.btn_prompt_reset.setFixedWidth(160)
        self.btn_prompt_reset.clicked.connect(self.on_reset_prompt)
        prompt_head.addWidget(self.btn_prompt_reset)
        form.addRow(_row(prompt_head))

        self.ed_vision_prompt = QPlainTextEdit()
        self.ed_vision_prompt.setMinimumHeight(360)
        self.ed_vision_prompt.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        form.addRow(self.ed_vision_prompt)
        form.addRow(_hint(
            "Відповідь має лишатись JSON із полями category, confidence, description, "
            "on_screen_text і tags. "
            + " ".join(f"{token} — {what}." for token, what in VISION_PLACEHOLDERS.items())
            + " Поки інструкцію не змінено, вона оновлюється разом із застосунком."))
        return box

    def _sec_vocab(self) -> QWidget:
        box, form = _section(
            "Словник тегів",
            "Усе, чого немає у словнику, відкидається кодом — не проханням до моделі. "
            "Без нього 3d-render, 3drender, render і 3d — чотири різні теги для Eagle.")

        self.ck_taxonomy = QCheckBox("Брати теги лише зі словника")
        form.addRow(_flabel(""), self.ck_taxonomy)

        self.btn_taxonomy = QPushButton("Пропозиції до словника")
        self.btn_taxonomy.setToolTip(
            "Теги, які модель пропонувала часто, а словник не прийняв.\n"
            "Звідти ж відкривається сам taxonomy.json."
        )
        self.btn_taxonomy.clicked.connect(self.on_tag_suggestions)
        self.sp_suggest_after = QSpinBox()
        self.sp_suggest_after.setRange(1, 100)
        self.sp_suggest_after.setSuffix(" разів")
        self.sp_suggest_after.setFixedWidth(120)
        vocab = QHBoxLayout()
        vocab.addWidget(self.btn_taxonomy)
        vocab.addWidget(QLabel("після"))
        vocab.addWidget(self.sp_suggest_after)
        vocab.addStretch(1)
        form.addRow(_flabel("Словник росте"), _row(vocab))
        form.addRow(_flabel(""), _hint(
            "Відкинутий тег рахується; коли модель попросить його стільки разів, він "
            "зʼявиться в пропозиціях — і одним кліком додається до обраної категорії."))

        self.btn_vocab = QPushButton("Звіт про словник")
        self.btn_vocab.setToolTip("Перевикористані й мертві теги — де словник не працює")
        self.btn_vocab.clicked.connect(self.on_vocab_report)
        self.btn_normalize = QPushButton("Вирівняти збережені теги за словником")
        self.btn_normalize.setToolTip(
            "Проганяє збережені теги моделі через поточний словник і виправляє\n"
            "вже імпортовані елементи Eagle. Без запитів до моделі."
        )
        self.btn_normalize.clicked.connect(self.on_normalize_tags)
        tools = QHBoxLayout()
        tools.addWidget(self.btn_vocab)
        tools.addWidget(self.btn_normalize)
        tools.addStretch(1)
        form.addRow(_flabel("Інструменти"), _row(tools))
        return box

    # ================================================================= Eagle
    def _sec_eagle_connect(self) -> QWidget:
        box, form = _section("Підключення")

        self.ck_eagle = QCheckBox("Імпортувати завантажене в Eagle")
        form.addRow(_flabel(""), self.ck_eagle)

        self.ed_eagle_url = QLineEdit()
        self.ed_eagle_url.setPlaceholderText("http://localhost:41595")
        form.addRow(_flabel("API"), self.ed_eagle_url)
        form.addRow(_flabel(""), _hint("Eagle слухає порт 41595, поки програма відкрита."))

        self.ed_eagle_token = QLineEdit()
        self.ed_eagle_token.setPlaceholderText("лишити порожнім, якщо Eagle не просить")
        self.ed_eagle_token.setToolTip(
            "Новіші версії Eagle можуть вимагати токен:\nPreferences → Developer → API token.")
        form.addRow(_flabel("Токен"), self.ed_eagle_token)

        self.ed_eagle_root = QLineEdit()
        form.addRow(_flabel("Коренева папка"), self.ed_eagle_root)

        self.ck_eagle_per_col = QCheckBox("Підпапка на кожну підбірку")
        form.addRow(_flabel(""), self.ck_eagle_per_col)

        self.ck_eagle_once = QCheckBox("Один пост — один елемент у бібліотеці")
        self.ck_eagle_once.setToolTip(
            "Пост часто лежить і в збережених, і в лайках, і в підбірці.\n"
            "Eagle на кожен імпорт КОПІЮЄ файл — без цієї галочки той самий\n"
            "ролик з'являється в бібліотеці двічі-тричі."
        )
        form.addRow(_flabel(""), self.ck_eagle_once)

        _gap(form)

        self.btn_eagle_test = QPushButton("Перевірити звʼязок")
        self.btn_eagle_test.clicked.connect(self.on_test_eagle)
        self.lbl_eagle = _hint("")
        check = QHBoxLayout()
        check.addWidget(self.btn_eagle_test)
        check.addWidget(self.lbl_eagle, 1)
        form.addRow(_flabel(""), _row(check))
        return box

    def _sec_eagle_tags(self) -> QWidget:
        box, form = _section(
            "Теги й нотатка",
            "Опис моделі, текст з екрана й транскрипт ідуть у нотатку елемента; "
            "теги моделі — зі словника. Тут — те, що додається окрім них.")

        self.ck_eagle_tags = QCheckBox("Хештеги з підпису")
        self.ck_eagle_tag_author = QCheckBox("Імʼя автора (@username)")
        self.ck_eagle_tag_col = QCheckBox("Назва підбірки")
        tags_col = QVBoxLayout()
        tags_col.setSpacing(4)
        for widget in (self.ck_eagle_tags, self.ck_eagle_tag_author, self.ck_eagle_tag_col):
            tags_col.addWidget(widget)
        form.addRow(_flabel("Додавати як теги"), _row(tags_col))

        self.ed_eagle_tags = QLineEdit()
        self.ed_eagle_tags.setPlaceholderText("instagram, reference — через кому")
        form.addRow(_flabel("Постійні теги"), self.ed_eagle_tags)
        return box

    def _sec_eagle_cleanup(self) -> QWidget:
        box, form = _section("Прибирання й дублікати")

        self.ck_eagle_cleanup = QCheckBox("Видаляти локальні копії після імпорту")
        self.ck_eagle_cleanup.setToolTip(
            "Eagle зберігає власну копію кожного файлу — папка завантажень\n"
            "стає лише перевалкою. Видаляється тільки підтверджене: те, що\n"
            "реально видно в бібліотеці, а не просто відправлене."
        )
        form.addRow(_flabel(""), self.ck_eagle_cleanup)
        form.addRow(_flabel(""), _hint(
            "Памʼять зберігається: прибраний пост ніколи не качається вдруге. "
            "Щойно відправлене чекає до наступного проходу. Черга перегляду не чіпається."))

        self.cb_dupe = QComboBox()
        for key, label in (("review", "показати в перегляді"),
                           ("import", "імпортувати як є"),
                           ("skip", "не брати"),
                           ("off", "не перевіряти")):
            self.cb_dupe.addItem(label, key)
        self.cb_dupe.setFixedWidth(200)
        self.sp_dupe_dist = QSpinBox()
        self.sp_dupe_dist.setRange(0, 20)
        self.sp_dupe_dist.setFixedWidth(70)
        dupe_row = QHBoxLayout()
        dupe_row.addWidget(self.cb_dupe)
        dupe_row.addWidget(QLabel("поріг"))
        dupe_row.addWidget(self.sp_dupe_dist)
        dupe_row.addStretch(1)
        form.addRow(_flabel("Репост наявного"), _row(dupe_row))
        form.addRow(_flabel(""), _hint(
            "Той самий ролик в іншому акаунті — інший пост і інший файл, але ті самі "
            "кадри. Поріг — скільки бітів із 64 можуть відрізнятись: 8 — упевнений збіг."))

        _gap(form)

        self.btn_dupes = QPushButton("Знайти дублікати в Eagle")
        self.btn_dupes.setToolTip(
            "Кілька елементів на один пост Instagram. Спершу лише показує;\n"
            "видаляти чи ні — вирішуєш ти, і лише в кошик."
        )
        self.btn_dupes.clicked.connect(self.on_find_dupes)
        form.addRow(_flabel("Уже накопичене"), _row(_left(self.btn_dupes)))
        return box

    def _sec_eagle_library(self) -> QWidget:
        box, form = _section(
            "Бібліотека",
            "Описи для того, що вже лежить у Eagle: кадри читаються зі сховища "
            "бібліотеки, локальні файли не потрібні.")

        self.sp_backlog = QSpinBox()
        self.sp_backlog.setRange(0, 500)
        self.sp_backlog.setSpecialValueText("вимкнено")
        self.sp_backlog.setSuffix(" елементів")
        self.sp_backlog.setFixedWidth(185)
        form.addRow(_flabel("Дописувати після проходу"), _row(_left(self.sp_backlog)))
        form.addRow(_flabel(""), _hint(
            "Після кожної синхронізації описати ще стільки елементів без опису. "
            "Десяток за прохід непомітний, а за місяць покриває стару бібліотеку."))

        self.btn_describe = QPushButton(DESCRIBE_LABEL)
        self.btn_describe.setToolTip(
            "Описати одразу багато: спробувати на 20, все, лише застарілі\n"
            "(іншою інструкцією) або переописати іншою моделлю."
        )
        self.btn_describe.clicked.connect(self.on_describe_library)
        self.btn_push_eagle = QPushButton("Імпортувати наявні файли в Eagle")
        self.btn_push_eagle.setToolTip(
            "Заливає в Eagle те, що вже лежить на диску.\n"
            "Потрібно, якщо імпорт увімкнули після завантаження."
        )
        self.btn_push_eagle.clicked.connect(self.on_push_eagle)
        actions = QVBoxLayout()
        actions.setSpacing(6)
        for widget in (self.btn_describe, self.btn_push_eagle):
            widget.setMinimumHeight(34)
            actions.addWidget(widget)
        form.addRow(_flabel("Зараз"), _row(actions))
        return box

    # ================================================================ Акаунт
    def _sec_session(self) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(_label("Сесія Instagram", "h2"))
        layout.addWidget(_hint(
            "Застосунок працює під твоєю сесією — паролю не знає. Cookie зберігається "
            "зашифрованим (DPAPI) і читається лише під твоїм обліковим записом Windows."))

        box_auto = QGroupBox("Автоматично з браузера")
        auto = QHBoxLayout(box_auto)
        self.cb_browser = QComboBox()
        for key, label in BROWSER_LABELS.items():
            self.cb_browser.addItem(label, key)
        self.btn_find_cookie = QPushButton("Знайти сесію")
        self.btn_find_cookie.clicked.connect(self.on_find_cookie)
        self.btn_cookies_file = QPushButton("З файлу cookies.txt…")
        self.btn_cookies_file.clicked.connect(self.on_cookies_file)
        auto.addWidget(self.cb_browser, 1)
        auto.addWidget(self.btn_find_cookie)
        auto.addWidget(self.btn_cookies_file)
        layout.addWidget(box_auto)

        box_manual = QGroupBox("Вручну")
        manual = QVBoxLayout(box_manual)
        manual.addWidget(_label(MANUAL_HELP, "muted"))
        row = QHBoxLayout()
        self.ed_session = QLineEdit()
        self.ed_session.setEchoMode(QLineEdit.Password)
        self.ed_session.setPlaceholderText("sessionid…")
        self.ck_show_session = QCheckBox("Показати")
        self.ck_show_session.toggled.connect(
            lambda on: self.ed_session.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password)
        )
        self.btn_verify = QPushButton("Перевірити")
        self.btn_verify.setProperty("role", "primary")
        self.btn_verify.clicked.connect(self.on_verify_session)
        row.addWidget(self.ed_session, 1)
        row.addWidget(self.ck_show_session)
        row.addWidget(self.btn_verify)
        manual.addLayout(row)
        layout.addWidget(box_manual)

        self.lbl_session = _label("", "muted")
        layout.addWidget(self.lbl_session)

        self.session_log = QPlainTextEdit()
        self.session_log.setObjectName("log")
        self.session_log.setReadOnly(True)
        self.session_log.setMaximumBlockCount(400)
        self.session_log.setMinimumHeight(140)
        layout.addWidget(self.session_log, 1)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self.btn_forget = QPushButton("Забути сесію")
        self.btn_forget.clicked.connect(self.on_forget_session)
        bottom.addWidget(self.btn_forget)
        layout.addLayout(bottom)
        return box

    def _sec_protection(self) -> QWidget:
        box, form = _section(
            "Захист акаунта",
            "Instagram позначає як автоматизацію не обсяг, а темп: рівні короткі "
            "інтервали й повторні входи. Усе тут — про те, щоб не поспішати.")

        self.sp_delay_min = QDoubleSpinBox()
        self.sp_delay_min.setRange(0.5, 60.0)
        self.sp_delay_min.setSingleStep(0.5)
        self.sp_delay_min.setSuffix(" с")
        self.sp_delay_min.setFixedWidth(112)
        self.sp_delay_max = QDoubleSpinBox()
        self.sp_delay_max.setRange(0.5, 120.0)
        self.sp_delay_max.setSingleStep(0.5)
        self.sp_delay_max.setSuffix(" с")
        self.sp_delay_max.setFixedWidth(112)
        delays = QHBoxLayout()
        delays.addWidget(QLabel("від"))
        delays.addWidget(self.sp_delay_min)
        delays.addWidget(QLabel("до"))
        delays.addWidget(self.sp_delay_max)
        delays.addStretch(1)
        form.addRow(_flabel("Пауза між сторінками"), _row(delays))

        self.sp_cooldown = QDoubleSpinBox()
        self.sp_cooldown.setRange(0.0, 168.0)
        self.sp_cooldown.setSingleStep(1.0)
        self.sp_cooldown.setDecimals(1)
        self.sp_cooldown.setSuffix(" год")
        self.sp_cooldown.setSpecialValueText("без обмеження")
        self.sp_cooldown.setFixedWidth(150)
        form.addRow(_flabel("Мінімум між проходами"), _row(_left(self.sp_cooldown)))
        form.addRow(_flabel(""), _hint(
            "Запуск за розкладом у цей час просто пропускається; ручний питає "
            "підтвердження. Кілька проходів із різницею у хвилини — найпомітніший слід."))

        self.sp_rate_cooldown = QDoubleSpinBox()
        self.sp_rate_cooldown.setRange(0.0, 168.0)
        self.sp_rate_cooldown.setSingleStep(1.0)
        self.sp_rate_cooldown.setDecimals(0)
        self.sp_rate_cooldown.setSuffix(" год")
        self.sp_rate_cooldown.setSpecialValueText("не зупинятись")
        self.sp_rate_cooldown.setFixedWidth(150)
        form.addRow(_flabel("Після «зачекай»"), _row(_left(self.sp_rate_cooldown)))
        form.addRow(_flabel(""), _hint(
            "«Please wait a few minutes» або 429 зупиняє весь прохід, і стільки годин "
            "застосунок не звертається до Instagram — навіть за ручним запуском."))

        self.sp_jitter = QSpinBox()
        self.sp_jitter.setRange(0, 120)
        self.sp_jitter.setSpecialValueText("без зсуву")
        self.sp_jitter.setSuffix(" хв")
        self.sp_jitter.setFixedWidth(150)
        form.addRow(_flabel("Випадковий зсув розкладу"), _row(_left(self.sp_jitter)))
        form.addRow(_flabel(""), _hint(
            "Плановий прохід починається у випадковий момент у цьому вікні: "
            "рівно о 09:00:00 щодня — підпис скрипта."))

        self.sp_attempts = QSpinBox()
        self.sp_attempts.setRange(0, 20)
        self.sp_attempts.setSpecialValueText("без межі")
        self.sp_attempts.setSuffix(" спроб")
        self.sp_attempts.setFixedWidth(150)
        form.addRow(_flabel("Спроб на один пост"), _row(_left(self.sp_attempts)))
        form.addRow(_flabel(""), _hint(
            "Видалений чи битий пост після цієї кількості невдач відкладається — "
            "список у «Обслуговування»."))
        return box

    def _sec_schedule(self) -> QWidget:
        box, form = _section("Розклад і автозапуск")

        self.ck_schedule = QCheckBox("Синхронізувати за розкладом (Планувальник Windows)")
        self.ck_schedule.toggled.connect(self._update_schedule_widgets)
        form.addRow(_flabel(""), self.ck_schedule)

        self.cb_sched_mode = QComboBox()
        for key in (SCHED_DAILY, SCHED_HOURLY, SCHED_WEEKLY, SCHED_ONLOGON):
            self.cb_sched_mode.addItem(SCHEDULE_LABELS[key], key)
        self.cb_sched_mode.setFixedWidth(190)
        self.cb_sched_mode.currentIndexChanged.connect(self._update_schedule_widgets)
        self.sp_sched_hours = QSpinBox()
        self.sp_sched_hours.setRange(1, 23)
        self.sp_sched_hours.setSuffix(" год")
        self.sp_sched_hours.setFixedWidth(108)
        self.cb_weekday = QComboBox()
        for key, label in WEEKDAYS:
            self.cb_weekday.addItem(label, key)
        self.cb_weekday.setFixedWidth(130)
        self.ed_time = QTimeEdit()
        self.ed_time.setDisplayFormat("HH:mm")
        self.ed_time.setFixedWidth(100)
        mode_row = QHBoxLayout()
        mode_row.addWidget(self.cb_sched_mode)
        mode_row.addWidget(self.sp_sched_hours)
        mode_row.addWidget(self.cb_weekday)
        mode_row.addWidget(QLabel("о"))
        mode_row.addWidget(self.ed_time)
        mode_row.addStretch(1)
        form.addRow(_flabel("Коли"), _row(mode_row))

        self.btn_sched_apply = QPushButton("Застосувати розклад")
        self.btn_sched_apply.clicked.connect(self.on_apply_schedule)
        self.btn_sched_run = QPushButton("Запустити зараз")
        self.btn_sched_run.setToolTip("Виконати задачу планувальника негайно, у фоні")
        self.btn_sched_run.clicked.connect(self.on_run_task_now)
        buttons = QHBoxLayout()
        buttons.addWidget(self.btn_sched_apply)
        buttons.addWidget(self.btn_sched_run)
        buttons.addStretch(1)
        form.addRow(_flabel(""), _row(buttons))
        self.lbl_sched = _hint("")
        form.addRow(_flabel(""), self.lbl_sched)

        _gap(form)

        self.ck_run_at_login = QCheckBox("Запускати застосунок разом із Windows")
        self.ck_start_min = QCheckBox("Стартувати згорнутим у трей")
        self.ck_sync_launch = QCheckBox("Синхронізувати одразу після запуску")
        self.ck_tray = QCheckBox("Закриття вікна згортає в трей")
        self.ck_notify = QCheckBox("Сповіщення після завершення")
        behaviour = QVBoxLayout()
        behaviour.setSpacing(4)
        for widget in (self.ck_run_at_login, self.ck_start_min, self.ck_sync_launch,
                       self.ck_tray, self.ck_notify):
            behaviour.addWidget(widget)
        form.addRow(_flabel("Поведінка"), _row(behaviour))
        return box

    # ======================================================== Обслуговування
    def _sec_network(self) -> QWidget:
        box, form = _section("Мережа")

        self.sp_timeout = QSpinBox()
        self.sp_timeout.setRange(10, 600)
        self.sp_timeout.setSuffix(" с")
        self.sp_timeout.setFixedWidth(106)
        self.sp_retries = QSpinBox()
        self.sp_retries.setRange(1, 10)
        self.sp_retries.setSuffix(" спроб")
        self.sp_retries.setFixedWidth(124)
        net = QHBoxLayout()
        net.addWidget(QLabel("таймаут"))
        net.addWidget(self.sp_timeout)
        net.addWidget(QLabel("повтори"))
        net.addWidget(self.sp_retries)
        net.addStretch(1)
        form.addRow(_flabel("Завантаження"), _row(net))

        self.ed_proxy = QLineEdit()
        self.ed_proxy.setPlaceholderText("http://user:pass@host:port")
        form.addRow(_flabel("Проксі"), self.ed_proxy)
        form.addRow(_flabel(""), _hint("Лишити порожнім, якщо не потрібно."))
        return box

    def _sec_files(self) -> QWidget:
        box, form = _section("Файли й база")

        self.btn_refresh_library = QPushButton("Оновити назви й метадані наявних файлів")
        self.btn_refresh_library.setToolTip(
            "Перейменовує вже завантажене за поточним шаблоном і дописує\n"
            "опис та автора всередину файлів. Нічого не перекачує."
        )
        self.btn_refresh_library.clicked.connect(self.on_refresh_library)
        self.btn_given_up = QPushButton("Пости, від яких відмовились")
        self.btn_given_up.setToolTip(
            "Пости, які не вдалось узяти після кількох спроб.\n"
            "Можна подивитись причини і повернути їх у чергу."
        )
        self.btn_given_up.clicked.connect(self.on_given_up)
        self.btn_clear_downloads = QPushButton("Очистити папку завантажень")
        self.btn_clear_downloads.setToolTip(
            "Видаляє завантажені файли, але памʼятає, що вони вже качались —\n"
            "дублі не зʼявляться. Eagle не чіпається."
        )
        self.btn_clear_downloads.clicked.connect(self.on_clear_downloads)
        self.btn_forget_downloads = QPushButton("Забути історію завантажень")
        self.btn_forget_downloads.setToolTip(
            "Очищає локальну базу, щоб наступний запуск перекачав усе заново.\n"
            "Самі файли на диску не чіпаються."
        )
        self.btn_forget_downloads.clicked.connect(self.on_reset_state)
        actions = QVBoxLayout()
        actions.setSpacing(6)
        for widget in (self.btn_refresh_library, self.btn_given_up,
                       self.btn_clear_downloads, self.btn_forget_downloads):
            widget.setMinimumHeight(34)
            actions.addWidget(widget)
        form.addRow(_flabel("Дані"), _row(actions))

        _gap(form)

        self.btn_open_logs = QPushButton("Папка журналів")
        self.btn_open_logs.clicked.connect(self.on_open_logs)
        self.btn_open_backups = QPushButton("Папка резервних копій")
        self.btn_open_backups.setToolTip("Копії state.db: щотижнева і перед кожною міграцією")
        self.btn_open_backups.clicked.connect(self.on_open_backups)
        self.btn_open_appdir = QPushButton("Папка застосунку")
        self.btn_open_appdir.setToolTip("config.json, state.db, taxonomy.json, session.json")
        self.btn_open_appdir.clicked.connect(self.on_open_appdir)
        folders = QHBoxLayout()
        for widget in (self.btn_open_logs, self.btn_open_backups, self.btn_open_appdir):
            folders.addWidget(widget)
        folders.addStretch(1)
        form.addRow(_flabel("Відкрити"), _row(folders))

        _gap(form)

        self.btn_shortcut = QPushButton("Ярлик на робочому столі")
        self.btn_shortcut.setToolTip("Створює ярлик із нормальною іконкою замість .bat")
        self.btn_shortcut.clicked.connect(self.on_make_shortcut)
        self.btn_reset_settings = QPushButton("Скинути налаштування")
        self.btn_reset_settings.clicked.connect(self.on_reset_settings)
        misc = QHBoxLayout()
        misc.addWidget(self.btn_shortcut)
        misc.addWidget(self.btn_reset_settings)
        misc.addStretch(1)
        form.addRow(_flabel("Інше"), _row(misc))
        return box

    # ======================================================= Про застосунок
    def _sec_about(self) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        from ..updater import mode_label

        layout.addWidget(_label(f"{APP_NAME} {__version__}", "h1"))
        layout.addWidget(_label(
            "Конвеєр для референсів: Instagram → фільтр → опис моделлю → теги зі "
            "словника → Eagle. Базою є Eagle; це лише шлях до неї.", "muted"))
        layout.addWidget(_label(f"Спосіб запуску: {mode_label()}.", "muted"))

        group = QGroupBox("Оновлення")
        inner = QVBoxLayout(group)
        inner.setSpacing(8)
        row = QHBoxLayout()
        self.btn_check_update = QPushButton("Перевірити оновлення")
        self.btn_check_update.clicked.connect(self.on_check_updates_now)
        self.btn_update = QPushButton("Оновити")
        self.btn_update.setProperty("role", "primary")
        self.btn_update.setEnabled(False)
        self.btn_update.clicked.connect(self.on_update_now)
        self.ck_updates = QCheckBox("Перевіряти сам раз на добу")
        row.addWidget(self.btn_check_update)
        row.addWidget(self.btn_update)
        row.addWidget(self.ck_updates)
        row.addStretch(1)
        inner.addLayout(row)
        self.lbl_update = _label("Ще не перевіряли.", "muted")
        inner.addWidget(self.lbl_update)
        self.update_progress = QProgressBar()
        self.update_progress.setRange(0, 100)
        self.update_progress.setValue(0)
        self.update_progress.setVisible(False)
        inner.addWidget(self.update_progress)
        self.update_notes = QPlainTextEdit()
        self.update_notes.setReadOnly(True)
        self.update_notes.setPlaceholderText("Тут буде опис нового релізу.")
        self.update_notes.setMinimumHeight(140)
        self.update_notes.setVisible(False)
        inner.addWidget(self.update_notes)
        inner.addWidget(_hint(
            "Встановлений застосунок качає інсталятор із релізу й запускає його; "
            "запуск із вихідників качає архів релізу, замінює код, не чіпає config.json, "
            "state.db, session.json і taxonomy.json, оновлює залежності й перезапускається."))
        layout.addWidget(group)

        links = QHBoxLayout()
        self.btn_github = QPushButton("GitHub")
        self.btn_github.clicked.connect(lambda: self._open_url("https://github.com/Amriel/InstRef"))
        self.btn_releases = QPushButton("Релізи")
        self.btn_releases.clicked.connect(
            lambda: self._open_url("https://github.com/Amriel/InstRef/releases"))
        self.btn_readme = QPushButton("Інструкція")
        self.btn_readme.clicked.connect(
            lambda: self._open_url("https://github.com/Amriel/InstRef/blob/main/README.uk.md"))
        for widget in (self.btn_github, self.btn_releases, self.btn_readme):
            links.addWidget(widget)
        links.addStretch(1)
        layout.addLayout(links)
        layout.addWidget(_label("MIT © 2026 Amriel · instagrapi · PySide6 · OpenCV · mutagen · piexif", "hint"))
        layout.addStretch(1)
        return box
