"""Nexus Downloader - GUI 兼 起動エントリーポイント (main.py)

4層構造:
  main.py            : GUI (PyQt6 + PyQt-Fluent-Widgets) と起動エントリポイント
  core.py            : 司令塔コア (動的ロード / セキュリティ / 更新 / スレッド)
  security.py        : セキュリティ・ガードマン (危険コードスキャン)
  addons/            : 外部アドオン (ダウンロードサイト追加・自動更新先)
  plugins/           : その他機能追加
  config.json        : 設定ファイル
"""

import json
import os
import re
import sys
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed

from PyQt6.QtCore import Qt, QObject, pyqtSignal, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QPlainTextEdit,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
)

from qfluentwidgets import (
    FluentWindow,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PlainTextEdit,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    ScrollArea,
    SubtitleLabel,
    BodyLabel,
    StrongBodyLabel,
    CaptionLabel,
    TitleLabel,
    SwitchButton,
    CardWidget,
    SimpleCardWidget,
    IconWidget,
    SettingCard,
    SettingCardGroup,
    SwitchSettingCard,
    PushSettingCard,
    PrimaryPushSettingCard,
    ExpandSettingCard,
    ComboBox,
    IndeterminateProgressBar,
    setTheme,
    Theme,
    setThemeColor,
)

# ---------------------------------------------------------------------------
# 本体の自動更新（core / security を import する前に適用する）
# ---------------------------------------------------------------------------
# updater は標準ライブラリしか使わないため、Qt 初期化前でも安全に呼び出せる。
# 前回起動時にステージングされた本体更新があれば、ここで本体ファイルへ適用し、
# 自分自身を再起動して「新旧バージョンが混在した状態」での動作を防ぐ。
import updater  # noqa: E402

_APPLIED_APP_VERSION = updater.apply_pending_update()
if _APPLIED_APP_VERSION and not updater.restart_application():
    # 再起動できなかった場合は、混在動作を避けるためここで終了する
    print("[Nexus] 本体を v{} に更新しました。アプリを再起動してください。".format(
        _APPLIED_APP_VERSION))
    sys.exit(0)

from core import Core, read_manifest, ADDONS_DIR, read_app_version  # noqa: E402
from security import scan_text  # noqa: E402

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

# ---------------------------------------------------------------------------
# アプリ情報（About タブで使用）
# ---------------------------------------------------------------------------

APP_NAME = "Nexus Downloader"
APP_VERSION = read_app_version()
APP_AUTHOR = "VPTensor35"
APP_LICENSE = "Apache License 2.0"
APP_DESCRIPTION = (
    "本体 (exe) を一切汚さず、外部の .py アドオン/プラグインを配置するだけで\n"
    "機能を拡張できる汎用ダウンローダー。動的インポート・セキュリティスキャン・\n"
    "バックグラウンド実行・GitHub 自動更新を備える。"
)
APP_HOMEPAGE = "https://github.com/yu7783"


# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "github_repo": "",
    "update_manifest": "manifest.json",
    "download_dir": "downloads",
    "theme": "dark",
    "accent_color": "#0078D4",
    "auto_update": True,
    "check_interval_hours": 24,
    "last_update_check": 0,
    "parallel": 4,
    "chunk_size": 10485760,
    "timeout": 30,
    "proxy": "",
    "user_agent": "",
    "blocked_keywords": ["os.remove", "shutil.rmtree", "subprocess", "os.system"],
}


def load_config():
    """config.json を読み込む。存在しなければ既定値で生成する。"""
    config = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            user_config = json.load(f)
        if isinstance(user_config, dict):
            config.update(user_config)
    except Exception:
        # 設定ファイルが無い / 壊れている場合は既定値を書き出す
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=4)
        except Exception:
            pass
    return config


# ---------------------------------------------------------------------------
# 並列ダウンロード
# ---------------------------------------------------------------------------

#: 並列ダウンロードの上限（回線・メモリ保護のための安全弁）
MAX_PARALLEL_DOWNLOADS = 16

#: config.json の ``parallel`` が 0 / 未設定のときに使う既定並列数
DEFAULT_PARALLEL_DOWNLOADS = 4


def resolve_parallel_workers(config, task_count):
    """実際に使う並列ダウンロード数を決める。

    ``config.json`` の ``parallel``（速度テストで自動調整される値）を採用し、
    ``0`` 以下なら既定値、タスク数と上限（:data:`MAX_PARALLEL_DOWNLOADS`）で
    クリップした整数を返す。

    Args:
        config: 設定 dict。
        task_count: 今回ダウンロードする URL の件数。

    Returns:
        int: 1 以上 ``MAX_PARALLEL_DOWNLOADS`` 以下の並列数。
    """
    try:
        workers = int(config.get("parallel", 0) or 0)
    except Exception:
        workers = 0
    if workers <= 0:
        workers = DEFAULT_PARALLEL_DOWNLOADS
    return max(1, min(workers, max(1, int(task_count)), MAX_PARALLEL_DOWNLOADS))


def _info_bar(kind, title, content, parent):
    """InfoBar を簡潔に呼ぶためのヘルパ。"""
    func = {
        "success": InfoBar.success,
        "warning": InfoBar.warning,
        "error": InfoBar.error,
        "info": InfoBar.info,
    }.get(kind, InfoBar.info)
    func(
        title=title,
        content=content,
        orient=Qt.Orientation.Horizontal,
        isClosable=True,
        position=InfoBarPosition.TOP_RIGHT,
        duration=5000,
        parent=parent,
    )
# ---------------------------------------------------------------------------
# バックグラウンド実行 (マルチスレッド)
# ---------------------------------------------------------------------------

class DownloadWorker(QObject):
    """ダウンロード処理を別スレッドに丸投げし、GUI のフリーズを防ぐ。"""

    progress = pyqtSignal(float, str)
    finished = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, core, addon, url, save_dir, options=None):
        super().__init__()
        self.core = core
        self.addon = addon
        self.url = url
        self.save_dir = save_dir
        self.options = dict(options or {})

    def run(self):
        try:
            module = self.addon["module"]
            if not hasattr(module, "download_logic"):
                raise RuntimeError("アドオンに download_logic が定義されていません")

            # options に対応したアドオンか確認（後方互換: 未対応なら従来通り呼ぶ）
            args = _call_download_logic(module.download_logic, self.url,
                                        lambda p, t: self.progress.emit(float(p), str(t)),
                                        self.save_dir, self.options)
            result = args
            self.core.log("[DL] 完了: {} -> {}".format(self.url, result))
            self.finished.emit(str(result) if result else "ダウンロード完了")
        except Exception as e:
            self.core.log("[DL] 失敗: {} ({})".format(self.url, e), level="ERROR")
            self.failed.emit("{}\n{}".format(e, traceback.format_exc()))


def _call_download_logic(func, url, progress_callback, save_dir, options):
    """``download_logic`` を後方互換で呼び分ける。

    - 引数 ``options`` を受け取れる（または ``**kwargs`` を持つ）場合は渡す。
    - 受け取らない旧式アドオンには、従来の 3 引数で呼び出す。
    """
    import inspect

    try:
        sig = inspect.signature(func)
        params = sig.parameters
        accepts_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
        accepts_options = "options" in params
        positional = [
            p for p in params.values()
            if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        ]
    except (TypeError, ValueError):
        accepts_kwargs = False
        accepts_options = False
        positional = []

    if accepts_kwargs or accepts_options:
        # キーワードで options を渡せる（**kwargs / options 引数あり）
        return func(url, progress_callback, save_dir, options=options)
    if len(positional) >= 4:
        # 位置引数で 4 番目が options
        return func(url, progress_callback, save_dir, options)
    return func(url, progress_callback, save_dir)


class MultiDownloadWorker(QObject):
    """複数の URL を並列ダウンロードするワーカー。

    ``run()`` を 1 本のスレッドで動かし、その中で ``ThreadPoolExecutor`` を使う。
    ワーカー数は ``config.json`` の ``parallel``（上限
    :data:`MAX_PARALLEL_DOWNLOADS`）で決まる。1 つの URL の失敗は他の URL の
    ダウンロードを止めず、最後に成功／失敗の件数をまとめて通知する。
    """

    item_progress = pyqtSignal(str, float, str)  # url, percent, text
    item_finished = pyqtSignal(str)              # url
    item_failed = pyqtSignal(str, str)           # url, error
    overall = pyqtSignal(int, int)               # done, total
    finished = pyqtSignal(int, int)              # ok, failed

    def __init__(self, core, tasks, save_dir, max_workers=None):
        super().__init__()
        self.core = core
        # tasks: [(url, addon, options), ...]
        self.tasks = list(tasks)
        self.save_dir = save_dir
        self.max_workers = self._resolve_workers(max_workers)

    def _resolve_workers(self, max_workers):
        """並列数を決める（未指定なら config の ``parallel`` から算出）。"""
        if max_workers is None:
            return resolve_parallel_workers(self.core.config, len(self.tasks))
        try:
            max_workers = int(max_workers)
        except Exception:
            max_workers = 0
        return max(1, min(max_workers, max(1, len(self.tasks)), MAX_PARALLEL_DOWNLOADS))

    def run(self):
        """全 URL を並列でダウンロードし、結果をシグナルで通知する。"""
        total = len(self.tasks)
        ok = 0
        failed = 0
        done = 0
        self.core.log("[DL] 並列ダウンロード開始: {} 件 (並列数 {})".format(
            total, self.max_workers))

        try:
            with ThreadPoolExecutor(max_workers=self.max_workers,
                                    thread_name_prefix="dl") as executor:
                futures = {}
                for url, addon, options in self.tasks:
                    future = executor.submit(self._download_one, url, addon, options)
                    futures[future] = url

                for future in as_completed(futures):
                    url = futures[future]
                    done += 1
                    try:
                        result = future.result()
                        ok += 1
                        self.core.log("[DL] 完了: {} -> {}".format(url, result))
                        self.item_finished.emit(url)
                    except Exception as e:
                        failed += 1
                        self.core.log("[DL] 失敗: {} ({})".format(url, e), level="ERROR")
                        self.item_failed.emit(url, str(e))
                    self.overall.emit(done, total)
        except Exception as e:
            # スレッドプール自体が起動できなかった等の致命エラー
            failed = max(failed, total - ok)
            self.core.log("[DL] 並列実行に失敗: {}".format(e), level="ERROR")

        self.finished.emit(ok, failed)

    def _download_one(self, url, addon, options):
        """1 つの URL をダウンロードする（executor のワーカーから呼ばれる）。"""
        module = addon["module"]
        if not hasattr(module, "download_logic"):
            raise RuntimeError("アドオンに download_logic が定義されていません")

        return _call_download_logic(
            module.download_logic,
            url,
            lambda p, t: self.item_progress.emit(url, float(p), str(t)),
            self.save_dir,
            options,
        )


# ---------------------------------------------------------------------------
# ダウンロード画面
# ---------------------------------------------------------------------------

class DownloadInterface(QWidget):
    """メイン画面: URL 入力欄 + 進捗バー + ダウンロードボタン。"""

    def __init__(self, core, parent=None):
        super().__init__(parent)
        self.core = core
        self.setObjectName("downloadInterface")
        self._thread = None
        self._worker = None
        # 複数 URL ダウンロードの状態管理
        self._result_rows = {}   # url -> (dot_label, status_label)
        self._progress = {}      # url -> 直近の進捗 (%)
        self._done_urls = set()  # 完了 / 失敗した URL
        self._active_urls = []   # 今回のタスク URL（全体進捗の母数）
        self._skipped = 0        # 対応アドオンが無くスキップした件数
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 28, 36, 28)
        layout.setSpacing(18)

        layout.addWidget(TitleLabel("ダウンロード", self))

        desc = BodyLabel(
            "URL を入力すると、対応するアドオンを自動で選んでダウンロードします。\n"
            "改行で区切って複数の URL を入力すると、それらを並列でダウンロードします。", self)
        desc.setTextColor(QColor(160, 160, 160), QColor(120, 120, 120))
        layout.addWidget(desc)

        # --- URL 入力カード ---
        input_card = SimpleCardWidget(self)
        input_layout = QVBoxLayout(input_card)
        input_layout.setContentsMargins(20, 18, 20, 18)
        input_layout.setSpacing(12)

        self.url_edit = PlainTextEdit(input_card)
        self.url_edit.setPlaceholderText(
            "動画やファイルの URL を貼り付けてください...\n"
            "（複数 URL は改行で区切ると並列ダウンロードします）")
        self.url_edit.setMinimumHeight(84)
        self.url_edit.setMaximumHeight(160)
        self.url_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        input_layout.addWidget(self.url_edit)

        row = QHBoxLayout()
        self.download_btn = PrimaryPushButton(FluentIcon.DOWNLOAD, "ダウンロード", input_card)
        self.download_btn.clicked.connect(self.start_download)
        row.addWidget(self.download_btn)

        self.open_dir_btn = PushButton(FluentIcon.FOLDER, "保存先を開く", input_card)
        self.open_dir_btn.clicked.connect(self._open_download_dir)
        row.addWidget(self.open_dir_btn)

        self.addon_btn = PushButton(FluentIcon.ROBOT, "アドオン管理へ", input_card)
        row.addWidget(self.addon_btn)

        row.addStretch(1)
        input_layout.addLayout(row)
        layout.addWidget(input_card)

        # --- オプションカード（アドオンが提示した場合のみ表示） ---
        self.options_card = SimpleCardWidget(self)
        opt_outer = QVBoxLayout(self.options_card)
        opt_outer.setContentsMargins(20, 16, 20, 16)
        opt_outer.setSpacing(10)

        self.options_header = StrongBodyLabel("ダウンロード設定", self.options_card)
        opt_outer.addWidget(self.options_header)

        # アドオン名 / 説明
        self.options_addon_label = CaptionLabel("", self.options_card)
        self.options_addon_label.setTextColor(QColor(150, 150, 150), QColor(130, 130, 130))
        opt_outer.addWidget(self.options_addon_label)

        # 動的生成されたコンボボックスを並べるグリッド
        self.options_grid = QGridLayout()
        self.options_grid.setHorizontalSpacing(24)
        self.options_grid.setVerticalSpacing(10)
        self.options_grid.setContentsMargins(0, 4, 0, 0)
        opt_outer.addLayout(self.options_grid)

        self.options_card.setVisible(False)  # 初期は非表示
        layout.addWidget(self.options_card)

        self._option_widgets = {}   # key -> (ComboBox, spec)
        self._current_addon = None  # 現在オプションを表示中のアドオン

        # URL 入力の変化に追従してオプションを再構築
        self.url_edit.textChanged.connect(self._on_url_changed)

        # --- 進捗カード ---
        prog_card = SimpleCardWidget(self)
        prog_layout = QVBoxLayout(prog_card)
        prog_layout.setContentsMargins(20, 18, 20, 18)
        prog_layout.setSpacing(12)

        prog_head = QHBoxLayout()
        prog_head.addWidget(StrongBodyLabel("進捗", prog_card))
        prog_head.addStretch(1)
        self.percent_label = BodyLabel("0%", prog_card)
        self.percent_label.setTextColor(QColor(150, 150, 150), QColor(130, 130, 130))
        prog_head.addWidget(self.percent_label)
        prog_layout.addLayout(prog_head)

        self.progress = ProgressBar(prog_card)
        self.progress.setValue(0)
        prog_layout.addWidget(self.progress)

        self.status = BodyLabel("待機中", prog_card)
        self.status.setTextColor(QColor(150, 150, 150), QColor(130, 130, 130))
        prog_layout.addWidget(self.status)
        layout.addWidget(prog_card)

        # --- 複数 URL の結果一覧カード（複数 URL 指定時のみ表示） ---
        self.results_card = SimpleCardWidget(self)
        results_outer = QVBoxLayout(self.results_card)
        results_outer.setContentsMargins(20, 16, 20, 16)
        results_outer.setSpacing(10)
        results_outer.addWidget(StrongBodyLabel("ダウンロード結果", self.results_card))

        self.results_scroll = ScrollArea(self.results_card)
        self.results_scroll.setWidgetResizable(True)
        self.results_scroll.setFixedHeight(150)
        self.results_scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")
        self.results_container = QWidget()
        self.results_layout = QVBoxLayout(self.results_container)
        self.results_layout.setContentsMargins(0, 0, 0, 0)
        self.results_layout.setSpacing(6)
        self.results_scroll.setWidget(self.results_container)
        results_outer.addWidget(self.results_scroll)

        self.results_card.setVisible(False)  # 初期は非表示
        layout.addWidget(self.results_card)

        # --- ステータスカード ---
        stat_card = SimpleCardWidget(self)
        stat_layout = QHBoxLayout(stat_card)
        stat_layout.setContentsMargins(20, 14, 20, 14)
        stat_layout.setSpacing(28)
        stat_layout.addWidget(self._stat_item(stat_card, "利用可能アドオン",
                                              str(len(self.core.addons))))
        stat_layout.addWidget(self._stat_item(stat_card, "並列数",
                                              str(self.core.config.get("parallel", 4))))
        stat_layout.addWidget(self._stat_item(stat_card, "保存先",
                                              self.core.download_dir()))
        stat_layout.addStretch(1)
        layout.addWidget(stat_card)

        layout.addStretch(1)

    def _stat_item(self, parent, title, value):
        box = QWidget(parent)
        col = QVBoxLayout(box)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        col.addWidget(CaptionLabel(title, box))
        val = StrongBodyLabel(value, box)
        col.addWidget(val)
        return box

    def refresh_stats(self):
        """利用可能アドオン数などを再表示する。"""
        try:
            self.percent_label.setText("0%")
        except Exception:
            pass

    # -- アドオンが提示するオプション（画質・音質・拡張子など） -------------

    def _input_urls(self):
        """入力欄（改行区切り）から URL のリストを取り出す。

        - 空行・前後の空白は無視する。
        - まったく同じ URL が複数行ある場合は 1 件にまとめる（誤操作による二重取得を防ぐ）。
        """
        text = self.url_edit.toPlainText().replace("\r\n", "\n").replace("\r", "\n")
        urls = []
        seen = set()
        for line in text.split("\n"):
            url = line.strip()
            if not url or url in seen:
                continue
            seen.add(url)
            urls.append(url)
        return urls

    def _on_url_changed(self, _text=None):
        """URL 入力が変わったら、対応アドオンの選択肢を再構築する。"""
        self._rebuild_options()

    def _clear_options(self):
        """生成済みの選択肢ウィジェットを破棄する。"""
        while self.options_grid.count():
            item = self.options_grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._option_widgets = {}

    def _rebuild_options(self):
        """現在の URL に対応するアドオンの選択肢を GUI に反映する。

        - アドオンが ``get_options()`` を実装している場合のみカードを表示。
        - 各選択肢はラベル＋ComboBox としてグリッド状に並べる。
        - 複数 URL が入力されている場合は、先頭 URL のアドオンの選択肢を表示する
          （先頭以外の URL にはアドオンの既定値が使われる）。
        """
        urls = self._input_urls()
        addon = self.core.find_addon_for_url(urls[0]) if urls else None

        # 同じアドオンなら作り直さない（入力中のチラつき防止）
        if addon is self._current_addon and self._option_widgets:
            return

        self._clear_options()
        self._current_addon = addon

        specs = self.core.get_addon_options(addon) if addon else {}
        if not specs:
            self.options_card.setVisible(False)
            return

        # アドオン名・説明を表示
        note = "{} が提供するオプション".format(addon.get("name", "アドオン"))
        if len(urls) > 1:
            note += "（先頭 URL のアドオン。他 {} 件には既定値を適用）".format(len(urls) - 1)
        self.options_addon_label.setText(note)

        row, col = 0, 0
        for key, spec in specs.items():
            label = BodyLabel(spec["label"], self.options_card)
            combo = ComboBox(self.options_card)
            combo.addItems(spec["options"])
            combo.setCurrentText(spec["default"])
            combo.setMinimumWidth(140)
            if spec.get("content"):
                combo.setToolTip(spec["content"])

            self.options_grid.addWidget(label, row, col * 2)
            self.options_grid.addWidget(combo, row, col * 2 + 1)
            self._option_widgets[key] = (combo, spec)

            col += 1
            if col >= 3:  # 1 行に最大 3 項目
                col = 0
                row += 1

        self.options_grid.setColumnStretch(col * 2 + 2, 1)
        self.options_card.setVisible(True)

    def collect_options(self):
        """現在 GUI で選択されているオプションを dict にして返す。"""
        return {key: combo.currentText() for key, (combo, _spec) in self._option_widgets.items()}

    def _open_download_dir(self):
        import os as _os
        path = self.core.download_dir()
        try:
            _os.startfile(path)  # Windows
        except Exception:
            _info_bar("info", "保存先", path, self)

    # -- 複数 URL の結果一覧 ------------------------------------------------

    #: 結果一覧に表示する状態マークと配色（light, dark の RGB）
    _STATE_MARKS = {"idle": "•", "run": "▶", "ok": "✔", "warn": "⚠", "error": "✖"}
    _STATE_COLORS = {
        "idle": ((150, 150, 150), (130, 130, 130)),
        "run": ((0, 120, 212), (76, 160, 235)),
        "ok": ((16, 137, 62), (84, 190, 120)),
        "warn": ((196, 110, 0), (230, 150, 40)),
        "error": ((196, 43, 28), (235, 100, 90)),
    }

    @staticmethod
    def _shorten(url, limit=72):
        """一覧表示用に URL を省略する（中央を ... で詰める）。"""
        url = str(url)
        if len(url) <= limit:
            return url
        keep = max(4, (limit - 3) // 2)
        return "{}...{}".format(url[:keep], url[-keep:])

    def _clear_results(self):
        """結果一覧の行をすべて破棄する。"""
        self._result_rows = {}
        while self.results_layout.count():
            item = self.results_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _add_result_row(self, url, text="待機中...", level="idle"):
        """URL 1 件分の行（状態マーク + 説明）を結果一覧へ追加する。"""
        if url in self._result_rows:
            self._set_result(url, text, level)
            return
        row = QWidget(self.results_container)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)

        dot = BodyLabel(self._STATE_MARKS.get(level, "•"), row)
        dot.setFixedWidth(16)
        row_layout.addWidget(dot)

        label = BodyLabel("{}    {}".format(self._shorten(url), text), row)
        row_layout.addWidget(label, 1)

        self.results_layout.addWidget(row)
        self._result_rows[url] = (dot, label)
        self._set_result(url, text, level)

    def _set_result(self, url, text, level="run"):
        """結果一覧の 1 行の状態表示を更新する。"""
        entry = self._result_rows.get(url)
        if entry is None:
            return
        dot, label = entry
        light, dark = self._STATE_COLORS.get(level, self._STATE_COLORS["idle"])
        dot.setText(self._STATE_MARKS.get(level, "•"))
        dot.setTextColor(QColor(*light), QColor(*dark))
        label.setText("{}    {}".format(self._shorten(url), text))

    def _show_results(self, urls, unresolved):
        """結果一覧カードを初期化して表示する。"""
        self._clear_results()
        for url in urls:
            self._add_result_row(url, "待機中...", "idle")
        for url in unresolved:
            self._add_result_row(url, "対応アドオン無し（スキップ）", "warn")
        self.results_card.setVisible(True)

    def _update_overall_progress(self):
        """完了件数と進行中タスクの進捗から全体の進捗バーを更新する。"""
        total = max(1, len(self._active_urls))
        completed = len(self._done_urls)
        active = 0.0
        for url in self._active_urls:
            if url in self._done_urls:
                continue
            active += max(0.0, min(100.0, self._progress.get(url, 0.0))) / 100.0
        value = int(max(0.0, min(100.0, (completed + active) / total * 100.0)))
        self.progress.setValue(value)
        self.percent_label.setText("{}%".format(value))

    # -- 開始 ---------------------------------------------------------------

    def start_download(self):
        """入力された URL（改行区切りで複数可）のダウンロードを開始する。

        1 件のときは従来どおり単発ダウンロード、2 件以上のときは
        ``config.json`` の ``parallel`` 数を並列度として並列ダウンロードする。
        """
        urls = self._input_urls()
        if not urls:
            _info_bar("warning", "入力エラー", "URL を入力してください。", self)
            return

        if self._thread is not None and self._thread.is_alive():
            _info_bar("info", "処理中", "別のダウンロードが進行中です。", self)
            return

        # 選択中のオプション（画質・音質・拡張子など）を取得
        self._rebuild_options()
        gui_options = self.collect_options()

        # URL ごとに対応アドオンとオプションを解決
        tasks = []        # [(url, addon, options), ...]
        unresolved = []   # 対応アドオンが無かった URL
        for url in urls:
            addon = self.core.find_addon_for_url(url)
            if addon is None:
                unresolved.append(url)
                continue
            # オプションカードに表示中のアドオンなら GUI の選択を使う
            options = gui_options if addon is self._current_addon \
                else self.core.default_options(addon)
            tasks.append((url, addon, options))

        if not tasks:
            _info_bar("error", "アドオン未検出",
                      "入力された URL に対応するアドオンが見つかりませんでした。", self)
            return

        self.download_btn.setEnabled(False)
        self.progress.setValue(0)
        self.percent_label.setText("0%")
        self._progress = {}
        self._done_urls = set()
        self._active_urls = [url for url, _addon, _opt in tasks]
        self._skipped = len(unresolved)

        if len(urls) > 1:
            # 2 件以上のときだけ結果一覧カードを表示
            self._show_results(self._active_urls, unresolved)
        else:
            self.results_card.setVisible(False)

        if unresolved:
            _info_bar("warning", "一部スキップ",
                      "{} 件の URL に対応アドオンが見つかりませんでした。".format(
                          len(unresolved)), self)

        save_dir = self.core.download_dir()

        # --- 単発（従来の動作を維持） ---
        if len(self._active_urls) == 1 and not unresolved:
            url, addon, options = tasks[0]
            if options:
                self.status.setText("{} で処理中... ({})".format(
                    addon["name"], ", ".join("{}={}".format(k, v) for k, v in options.items())))
            else:
                self.status.setText("{} で処理中...".format(addon["name"]))

            worker = DownloadWorker(self.core, addon, url, save_dir, options)
            worker.progress.connect(self._on_progress)
            worker.finished.connect(self._on_finished)
            worker.failed.connect(self._on_failed)
        else:
            # --- 複数 URL を並列ダウンロード ---
            workers = resolve_parallel_workers(self.core.config, len(tasks))
            self.status.setText("{} 件の URL を並列ダウンロード中... (並列数 {})".format(
                len(tasks), workers))

            worker = MultiDownloadWorker(self.core, tasks, save_dir, workers)
            worker.item_progress.connect(self._on_item_progress)
            worker.item_finished.connect(self._on_item_finished)
            worker.item_failed.connect(self._on_item_failed)
            worker.finished.connect(self._on_multi_finished)

        thread = threading.Thread(target=worker.run, daemon=True)
        self._worker = worker
        self._thread = thread
        thread.start()

    # -- 単発ダウンロードの進捗 ---

    def _on_progress(self, percent, text):
        value = int(max(0, min(100, percent)))
        self.progress.setValue(value)
        self.percent_label.setText("{}%".format(value))
        self.status.setText(text)

    def _on_finished(self, message):
        self.progress.setValue(100)
        self.percent_label.setText("100%")
        self.status.setText("完了: {}".format(message))
        self.download_btn.setEnabled(True)
        _info_bar("success", "完了", message, self)

    def _on_failed(self, message):
        self.status.setText("失敗しました")
        self.download_btn.setEnabled(True)
        _info_bar("error", "ダウンロード失敗", message.splitlines()[0], self)

    # -- 複数 URL 並列ダウンロードの進捗 ---

    def _on_item_progress(self, url, percent, text):
        """1 件の URL の進捗を全体進捗と結果一覧へ反映する。"""
        self._progress[url] = float(percent)
        self._update_overall_progress()
        self._set_result(url, "{}% {}".format(int(max(0, min(100, percent))), text), "run")
        self.status.setText("並列ダウンロード中... {} / {} 件完了".format(
            len(self._done_urls), len(self._active_urls)))

    def _on_item_finished(self, url):
        self._progress[url] = 100.0
        self._done_urls.add(url)
        self._update_overall_progress()
        self._set_result(url, "完了", "ok")

    def _on_item_failed(self, url, error):
        self._done_urls.add(url)
        self._update_overall_progress()
        first_line = (error or "").splitlines()[0] if error else "不明なエラー"
        self._set_result(url, "失敗: {}".format(first_line), "error")

    def _on_multi_finished(self, ok, failed):
        """並列ダウンロード全体の完了通知。"""
        self.progress.setValue(100)
        self.percent_label.setText("100%")
        message = "成功 {} 件 / 失敗 {} 件".format(ok, failed)
        if self._skipped:
            message += " / スキップ {} 件".format(self._skipped)
        self.status.setText("完了: {}".format(message))
        self.download_btn.setEnabled(True)
        _info_bar("success" if failed == 0 else "warning", "ダウンロード完了", message, self)


# ---------------------------------------------------------------------------
# アドオン管理画面
# ---------------------------------------------------------------------------

class AddonCard(CardWidget):
    """インストール済みアドオン1件を表すカード。トグルスイッチ付き。"""

    def __init__(self, entry, parent=None):
        super().__init__(parent)
        self.entry = entry
        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 12, 18, 12)
        layout.setSpacing(12)

        info = QVBoxLayout()
        info.setSpacing(2)
        info.addWidget(BodyLabel(entry["name"], self))

        manifest = entry["manifest"]
        detail = BodyLabel(
            "ID: {}  |  v{}".format(manifest.get("ID", "-"), manifest.get("VERSION", "-")),
            self,
        )
        detail.setTextColor(QColor(150, 150, 150), QColor(130, 130, 130))
        info.addWidget(detail)

        repo = (manifest.get("REPO") or "").strip()
        source = CaptionLabel(
            "更新元: {}".format(repo) if repo else "更新元: REPO 未設定（自動更新の対象外）",
            self,
        )
        source.setTextColor(QColor(150, 150, 150), QColor(130, 130, 130))
        info.addWidget(source)

        layout.addLayout(info, 1)

        self.switch = SwitchButton(self)
        self.switch.setChecked(entry.get("enabled", True))
        self.switch.setOnText("ON")
        self.switch.setOffText("OFF")
        self.switch.checkedChanged.connect(self._on_toggle)
        layout.addWidget(self.switch)

    def _on_toggle(self, checked):
        self.entry["enabled"] = bool(checked)


class AddonInterface(QWidget):
    """アドオン管理画面: インストール済み一覧 + GitHub URL + ローカル .py 追加。"""

    def __init__(self, core, parent=None):
        super().__init__(parent)
        self.core = core
        self.setObjectName("addonInterface")
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 28, 36, 28)
        layout.setSpacing(16)

        layout.addWidget(SubtitleLabel("アドオン管理", self))

        scroll = ScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")
        container = QWidget()
        self.list_layout = QVBoxLayout(container)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(10)
        scroll.setWidget(container)
        layout.addWidget(scroll, 1)

        self.reload_list()

        layout.addWidget(BodyLabel("アドオンの追加", self))

        row = QHBoxLayout()
        self.repo_edit = LineEdit(self)
        self.repo_edit.setPlaceholderText("アドオンの配布元 URL を入力...")
        row.addWidget(self.repo_edit, 1)

        self.add_btn = PushButton(FluentIcon.GLOBE, "追加", self)
        self.add_btn.clicked.connect(self.install_from_url)
        row.addWidget(self.add_btn)

        self.browse_btn = PushButton(FluentIcon.FOLDER, "ローカル .py 選択", self)
        self.browse_btn.clicked.connect(self.install_from_file)
        row.addWidget(self.browse_btn)
        layout.addLayout(row)

        self.update_btn = PrimaryPushButton(FluentIcon.SYNC, "今すぐ更新チェック", self)
        self.update_btn.clicked.connect(self.check_updates)
        layout.addWidget(self.update_btn)

    def reload_list(self):
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        if not self.core.addons:
            self.list_layout.addWidget(BodyLabel("アドオンがありません。", self))
        for entry in self.core.addons.values():
            self.list_layout.addWidget(AddonCard(entry, self))
        self.list_layout.addStretch(1)

    # -- インストール処理 ---------------------------------------------------

    def _github_to_raw(self, url):
        """GitHub の通常 URL を raw.githubusercontent.com 形式へ変換する。"""
        raw = url.replace("github.com", "raw.githubusercontent.com")
        raw = raw.replace("/blob/", "/").replace("/tree/", "/")
        return raw.rstrip("/")

    @staticmethod
    def _with_repo_field(content, repo_url):
        """マニフェストに ``REPO`` が無ければ、インストール元 URL を追記する。

        こうすることで、URL から追加したアドオンも次回以降は
        **そのアドオン自身のリポジトリ** を見て自動更新できるようになる。
        追記するのは本体が生成したコメント行のみ。

        Returns:
            str: 書き換え後のソース（REPO が既にある / 追記位置が無い場合はそのまま）。
        """
        if re.search(r"^\s*#\s*REPO\s*:", content, re.IGNORECASE | re.MULTILINE):
            return content
        version_line = re.compile(r"(^\s*#\s*VERSION\s*:\s*.+?$)", re.IGNORECASE | re.MULTILINE)
        match = version_line.search(content)
        if not match:
            return content
        insertion = "{}\n# REPO: {}".format(match.group(1), repo_url)
        return content[:match.start(1)] + insertion + content[match.end(1):]

    def install_from_url(self):
        """GitHub Repo URL から manifest.json を読み、addon .py を取得・配置する。"""
        url = self.repo_edit.text().strip()
        if not url:
            return
        raw = self._github_to_raw(url)
        try:
            import requests
            manifest_name = self.core.config.get("update_manifest", "manifest.json")
            try:
                r = requests.get(raw + "/" + manifest_name, timeout=10)
                r.raise_for_status()
                remote = r.json()
            except Exception:
                # manifest.json が無い場合は単一ファイルとして扱う
                remote = {"addons": [{"file": url.split("/")[-1], "version": "0.0.0"}]}

            installed = []
            for item in remote.get("addons", []):
                fname = item.get("file")
                if not fname:
                    continue
                rel = str(item.get("path") or "addons/{}".format(fname)).strip("/")
                fr = requests.get(raw + "/" + rel, timeout=20)
                fr.raise_for_status()
                # インストール元を REPO として記録し、以降は各アドオンの repo を見て更新する
                content = self._with_repo_field(fr.text, raw)

                # セキュリティゲート: 危険コードなら拒否
                safe, reason = scan_text(content, self.core.guard.blocked_keywords)
                if not safe:
                    _info_bar("warning", "インストール拒否", reason, self)
                    continue

                dest = os.path.join(ADDONS_DIR, fname)
                with open(dest, "w", encoding="utf-8") as f:
                    f.write(content)
                installed.append(fname)

            if installed:
                self.core.addons.clear()
                self.core.load_addons()
                self.reload_list()
                _info_bar("success", "追加完了", ", ".join(installed), self)
            else:
                _info_bar("warning", "追加なし", "インストールできるアドオンがありませんでした。", self)
        except Exception as e:
            _info_bar("error", "追加失敗", str(e), self)

    def install_from_file(self):
        """ローカルの .py ファイルを選択して addons/ に配置する。"""
        path, _ = QFileDialog.getOpenFileName(self, "アドオンを選択", "", "Python Files (*.py)")
        if not path:
            return

        safe, reason = self.core.guard.scan(path)
        if not safe:
            _info_bar("warning", "インストール拒否", reason, self)
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            manifest, _ = read_manifest(path)
            if manifest is None:
                raise RuntimeError("マニフェストが見つかりません (ADDON_MANIFEST)")
            dest = os.path.join(ADDONS_DIR, os.path.basename(path))
            with open(dest, "w", encoding="utf-8") as f:
                f.write(content)
            self.core.addons.clear()
            self.core.load_addons()
            self.reload_list()
            _info_bar("success", "追加完了", manifest.get("NAME", os.path.basename(path)), self)
        except Exception as e:
            _info_bar("error", "追加失敗", str(e), self)

    def check_updates(self):
        """本体 (config の本体リポジトリ) と全アドオン (各アドオンの REPO) を今すぐ確認する。"""
        result = self.core.auto_update_all()
        _info_bar("info", "更新チェック", result, self)
        # アドオンが更新されていれば読み込み直す
        if "更新完了" in result:
            self.core.addons.clear()
            self.core.load_addons()
            self.reload_list()


# ---------------------------------------------------------------------------
# 設定画面
# ---------------------------------------------------------------------------

class SettingsInterface(QWidget):
    """設定タブ: config.json の値を GUI から動的に変更・保存する。"""

    def __init__(self, core, parent=None):
        super().__init__(parent)
        self.core = core
        self.setObjectName("settingsInterface")
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 28, 36, 28)
        layout.setSpacing(16)
        layout.addWidget(TitleLabel("設定", self))

        scroll = ScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")
        container = QWidget()
        body = QVBoxLayout(container)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(20)
        scroll.setWidget(container)
        layout.addWidget(scroll, 1)

        # --- 更新設定 ---
        self.update_group = SettingCardGroup("更新", container)
        self.repo_card = SettingCard(
            FluentIcon.GITHUB, "本体リポジトリ URL",
            "本体の自動更新元 (raw 形式のベース URL)", self.update_group)
        self.repo_edit = LineEdit(self.repo_card)
        self.repo_edit.setText(self.core.config.get("github_repo", ""))
        self.repo_edit.setPlaceholderText("https://raw.githubusercontent.com/user/NexusDownloader/main")
        self.repo_edit.setMinimumWidth(320)
        self.repo_card.hBoxLayout.addWidget(self.repo_edit, 1, Qt.AlignmentFlag.AlignRight)
        self.repo_card.hBoxLayout.addSpacing(16)

        self.auto_update_card = SwitchSettingCard(
            FluentIcon.SYNC, "自動更新",
            "起動時に本体とアドオン (各アドオンの REPO) を自動更新します",
            configItem=None, parent=self.update_group)
        self.auto_update_card.setValue(bool(self.core.config.get("auto_update", True)))

        self.manifest_card = SettingCard(
            FluentIcon.DOCUMENT, "更新マニフェスト名",
            "本体リポジトリ直下に置く JSON ファイルの名前 (各アドオンの REPO でも使用)",
            self.update_group)
        self.manifest_edit = LineEdit(self.manifest_card)
        self.manifest_edit.setText(self.core.config.get("update_manifest", "manifest.json"))
        self.manifest_edit.setMinimumWidth(240)
        self.manifest_card.hBoxLayout.addWidget(self.manifest_edit, 1, Qt.AlignmentFlag.AlignRight)
        self.manifest_card.hBoxLayout.addSpacing(16)

        self.interval_card = SettingCard(
            FluentIcon.HISTORY, "更新チェック間隔 (時間)",
            "自動更新を行う間隔。0 で起動時のみ", self.update_group)
        self.interval_edit = LineEdit(self.interval_card)
        self.interval_edit.setText(str(self.core.config.get("check_interval_hours", 24)))
        self.interval_edit.setMinimumWidth(120)
        self.interval_card.hBoxLayout.addWidget(self.interval_edit, 1, Qt.AlignmentFlag.AlignRight)
        self.interval_card.hBoxLayout.addSpacing(16)

        self.update_group.addSettingCard(self.repo_card)
        self.update_group.addSettingCard(self.auto_update_card)
        self.update_group.addSettingCard(self.manifest_card)
        self.update_group.addSettingCard(self.interval_card)
        body.addWidget(self.update_group)

        # --- ダウンロード設定 ---
        self.dl_group = SettingCardGroup("ダウンロード", container)
        self.dir_card = PushSettingCard(
            "フォルダを選択", FluentIcon.FOLDER, "ダウンロード先フォルダ",
            self.core.download_dir(), self.dl_group)
        self.dir_card.clicked.connect(self._choose_dir)

        self.keywords_card = SettingCard(
            FluentIcon.LABEL, "ブロックする危険キーワード",
            "カンマ区切り。この文字列を含むアドオンは実行を拒否します", self.dl_group)
        self.keywords_edit = LineEdit(self.keywords_card)
        self.keywords_edit.setText(", ".join(self.core.config.get("blocked_keywords", [])))
        self.keywords_edit.setPlaceholderText("os.remove, shutil.rmtree, subprocess, os.system")
        self.keywords_edit.setMinimumWidth(320)
        self.keywords_card.hBoxLayout.addWidget(self.keywords_edit, 1, Qt.AlignmentFlag.AlignRight)
        self.keywords_card.hBoxLayout.addSpacing(16)

        self.dl_group.addSettingCard(self.dir_card)
        self.dl_group.addSettingCard(self.keywords_card)
        body.addWidget(self.dl_group)

        # --- 外観設定 ---
        self.appear_group = SettingCardGroup("外観", container)
        self.theme_card = SettingCard(
            FluentIcon.CONSTRACT, "テーマ",
            "アプリ全体の配色（再起動で反映）", self.appear_group)
        self.theme_combo = ComboBox(self.theme_card)
        self.theme_combo.addItems(["dark", "light"])
        self.theme_combo.setCurrentText(self.core.config.get("theme", "dark"))
        self.theme_card.hBoxLayout.addWidget(self.theme_combo, 0, Qt.AlignmentFlag.AlignRight)
        self.theme_card.hBoxLayout.addSpacing(16)

        self.accent_card = SettingCard(
            FluentIcon.PALETTE, "アクセントカラー",
            "16 進カラーコード（例 #0078D4、再起動で反映）", self.appear_group)
        self.accent_edit = LineEdit(self.accent_card)
        self.accent_edit.setText(self.core.config.get("accent_color", "#0078D4"))
        self.accent_edit.setMinimumWidth(140)
        self.accent_card.hBoxLayout.addWidget(self.accent_edit, 1, Qt.AlignmentFlag.AlignRight)
        self.accent_card.hBoxLayout.addSpacing(16)

        self.appear_group.addSettingCard(self.theme_card)
        self.appear_group.addSettingCard(self.accent_card)
        body.addWidget(self.appear_group)

        # --- 速度最適化 ---
        self.opt_group = SettingCardGroup("パフォーマンス", container)
        self.speed_card = PrimaryPushSettingCard(
            "速度テスト & 並列最適化", FluentIcon.SPEED_HIGH, "ダウンロード速度テスト",
            "帯域を計測し、最適な並列数・チャンクサイズを自動適用します", self.opt_group)
        self.speed_card.clicked.connect(self._run_speed_test)

        # 速度テストの進捗バー
        self.speed_progress_card = SettingCard(
            FluentIcon.SPEED_HIGH, "計測の進捗",
            "速度テストの実行状況", self.opt_group)
        self.speed_progress = IndeterminateProgressBar(self.speed_progress_card)
        self.speed_progress.setFixedWidth(220)
        self.speed_progress_card.hBoxLayout.addWidget(
            self.speed_progress, 0, Qt.AlignmentFlag.AlignRight)
        self.speed_progress_card.hBoxLayout.addSpacing(16)
        self.speed_progress.stop()  # 待機中は停止

        self.parallel_card = SettingCard(
            FluentIcon.SPEED_MEDIUM, "現在の並列数",
            "速度テストで自動調整されます", self.opt_group)
        self.parallel_label = BodyLabel(str(self.core.config.get("parallel", 4)), self.parallel_card)
        self.parallel_card.hBoxLayout.addWidget(self.parallel_label, 0, Qt.AlignmentFlag.AlignRight)
        self.parallel_card.hBoxLayout.addSpacing(16)

        self.opt_group.addSettingCard(self.parallel_card)
        self.opt_group.addSettingCard(self.speed_progress_card)
        self.opt_group.addSettingCard(self.speed_card)
        body.addWidget(self.opt_group)

        # --- 高度な設定（クリックで展開） ---
        self.advanced_card = ExpandSettingCard(
            FluentIcon.DEVELOPER_TOOLS, "高度な設定",
            "クリックすると詳細設定を展開します", container)

        self.proxy_card = SettingCard(
            FluentIcon.GLOBE, "HTTP プロキシ",
            "必要なら設定（例 http://127.0.0.1:8080）。空で無効", self.advanced_card)
        self.proxy_edit = LineEdit(self.proxy_card)
        self.proxy_edit.setText(self.core.config.get("proxy", ""))
        self.proxy_edit.setMinimumWidth(260)
        self.proxy_card.hBoxLayout.addWidget(self.proxy_edit, 1, Qt.AlignmentFlag.AlignRight)
        self.proxy_card.hBoxLayout.addSpacing(16)
        self.advanced_card.addWidget(self.proxy_card)

        self.ua_card = SettingCard(
            FluentIcon.PEOPLE, "User-Agent",
            "サーバーに送るブラウザ識別子（空で既定値）", self.advanced_card)
        self.ua_edit = LineEdit(self.ua_card)
        self.ua_edit.setText(self.core.config.get("user_agent", ""))
        self.ua_edit.setMinimumWidth(260)
        self.ua_card.hBoxLayout.addWidget(self.ua_edit, 1, Qt.AlignmentFlag.AlignRight)
        self.ua_card.hBoxLayout.addSpacing(16)
        self.advanced_card.addWidget(self.ua_card)

        self.manual_parallel_card = SettingCard(
            FluentIcon.SPEED_MEDIUM, "並列数（手動）",
            "0 で自動（速度テストの推奨値を使用）", self.advanced_card)
        self.manual_parallel_edit = LineEdit(self.manual_parallel_card)
        self.manual_parallel_edit.setText(str(self.core.config.get("parallel", 0)))
        self.manual_parallel_edit.setMinimumWidth(120)
        self.manual_parallel_card.hBoxLayout.addWidget(
            self.manual_parallel_edit, 1, Qt.AlignmentFlag.AlignRight)
        self.manual_parallel_card.hBoxLayout.addSpacing(16)
        self.advanced_card.addWidget(self.manual_parallel_card)

        self.chunk_card = SettingCard(
            FluentIcon.ZIP_FOLDER, "チャンクサイズ (MB)",
            "一度に読み込む単位。大きいほど高速だがメモリ消費増", self.advanced_card)
        self.chunk_edit = LineEdit(self.chunk_card)
        chunk_mb = max(1, int(self.core.config.get("chunk_size", 10485760)) // (1024 * 1024))
        self.chunk_edit.setText(str(chunk_mb))
        self.chunk_edit.setMinimumWidth(120)
        self.chunk_card.hBoxLayout.addWidget(self.chunk_edit, 1, Qt.AlignmentFlag.AlignRight)
        self.chunk_card.hBoxLayout.addSpacing(16)
        self.advanced_card.addWidget(self.chunk_card)

        self.timeout_card = SettingCard(
            FluentIcon.HISTORY, "接続タイムアウト (秒)",
            "ネットワーク接続のタイムアウト", self.advanced_card)
        self.timeout_edit = LineEdit(self.timeout_card)
        self.timeout_edit.setText(str(self.core.config.get("timeout", 30)))
        self.timeout_edit.setMinimumWidth(120)
        self.timeout_card.hBoxLayout.addWidget(self.timeout_edit, 1, Qt.AlignmentFlag.AlignRight)
        self.timeout_card.hBoxLayout.addSpacing(16)
        self.advanced_card.addWidget(self.timeout_card)

        body.addWidget(self.advanced_card)

        # --- 保存ボタン ---
        save_row = QHBoxLayout()
        self.save_btn = PrimaryPushButton(FluentIcon.SAVE, "設定を保存", container)
        self.save_btn.clicked.connect(self.save_settings)
        save_row.addWidget(self.save_btn)
        self.reload_btn = PushButton(FluentIcon.SYNC, "再読込", container)
        self.reload_btn.clicked.connect(self.reload_settings)
        save_row.addWidget(self.reload_btn)
        save_row.addStretch(1)
        body.addLayout(save_row)
        body.addStretch(1)

    # -- イベント -----------------------------------------------------------

    def _choose_dir(self):
        path = QFileDialog.getExistingDirectory(self, "ダウンロード先を選択",
                                                self.core.download_dir())
        if path:
            self.dir_card.setContent(path)

    def _safe_int(self, text, default):
        """テキストを int に変換する。失敗時は default を返す。"""
        try:
            return int(str(text).strip())
        except Exception:
            return default

    def _run_speed_test(self):
        self.speed_card.button.setText("計測中...")
        self.speed_card.button.setEnabled(False)
        self.speed_progress.start()  # 進捗バーを開始

        def work():
            try:
                ok, message = self.core.optimize_parallel(apply=True)
            except Exception as e:
                ok, message = False, "計測エラー: {}".format(e)
            # GUI スレッドへ戻して反映
            QTimer.singleShot(0, lambda: self._on_speed_done(ok, message))

        threading.Thread(target=work, daemon=True).start()

    def _on_speed_done(self, ok, message):
        self.speed_progress.stop()  # 進捗バーを停止
        self.speed_card.button.setText("速度テスト & 並列最適化")
        self.speed_card.button.setEnabled(True)
        self.parallel_label.setText(str(self.core.config.get("parallel", 4)))
        _info_bar("success" if ok else "error", "速度テスト", message, self)

    def save_settings(self):
        keywords = [k.strip() for k in self.keywords_edit.text().split(",") if k.strip()]
        chunk_mb = max(1, self._safe_int(self.chunk_edit.text(), 10))
        ok, message = self.core.update_config(
            github_repo=self.repo_edit.text().strip(),
            auto_update=self.auto_update_card.isChecked(),
            download_dir=self.dir_card.contentLabel.text(),
            blocked_keywords=keywords,
            update_manifest=self.manifest_edit.text().strip() or "manifest.json",
            check_interval_hours=max(0, self._safe_int(self.interval_edit.text(), 24)),
            theme=self.theme_combo.currentText(),
            accent_color=self.accent_edit.text().strip() or "#0078D4",
            proxy=self.proxy_edit.text().strip(),
            user_agent=self.ua_edit.text().strip(),
            parallel=max(0, self._safe_int(self.manual_parallel_edit.text(), 0)),
            chunk_size=chunk_mb * 1024 * 1024,
            timeout=max(1, self._safe_int(self.timeout_edit.text(), 30)),
        )
        _info_bar("success" if ok else "error", "設定", message, self)

    def reload_settings(self):
        self.repo_edit.setText(self.core.config.get("github_repo", ""))
        self.auto_update_card.setValue(bool(self.core.config.get("auto_update", True)))
        self.dir_card.setContent(self.core.download_dir())
        self.keywords_edit.setText(", ".join(self.core.config.get("blocked_keywords", [])))
        self.manifest_edit.setText(self.core.config.get("update_manifest", "manifest.json"))
        self.interval_edit.setText(str(self.core.config.get("check_interval_hours", 24)))
        self.theme_combo.setCurrentText(self.core.config.get("theme", "dark"))
        self.accent_edit.setText(self.core.config.get("accent_color", "#0078D4"))
        self.proxy_edit.setText(self.core.config.get("proxy", ""))
        self.ua_edit.setText(self.core.config.get("user_agent", ""))
        self.manual_parallel_edit.setText(str(self.core.config.get("parallel", 0)))
        chunk_mb = max(1, int(self.core.config.get("chunk_size", 10485760)) // (1024 * 1024))
        self.chunk_edit.setText(str(chunk_mb))
        self.timeout_edit.setText(str(self.core.config.get("timeout", 30)))
        self.parallel_label.setText(str(self.core.config.get("parallel", 4)))
        _info_bar("info", "設定", "設定を再読込しました", self)


# ---------------------------------------------------------------------------
# About 画面
# ---------------------------------------------------------------------------

class AboutInterface(QWidget):
    """About タブ: アプリの情報（バージョン・開発者・ライセンス）を表示する。"""

    def __init__(self, core, parent=None):
        super().__init__(parent)
        self.core = core
        self.setObjectName("aboutInterface")
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 28, 36, 28)
        layout.setSpacing(16)
        layout.addWidget(TitleLabel("このアプリについて", self))

        scroll = ScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")
        container = QWidget()
        body = QVBoxLayout(container)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(20)
        scroll.setWidget(container)
        layout.addWidget(scroll, 1)

        # --- ヒーローカード ---
        hero = SimpleCardWidget(container)
        hero.setFixedHeight(150)
        hero_layout = QHBoxLayout(hero)
        hero_layout.setContentsMargins(28, 20, 28, 20)
        hero_layout.setSpacing(20)

        icon_widget = IconWidget(FluentIcon.CLOUD_DOWNLOAD, hero)
        icon_widget.setFixedSize(64, 64)
        hero_layout.addWidget(icon_widget)

        text_col = QVBoxLayout()
        text_col.setSpacing(4)
        name_label = StrongBodyLabel(APP_NAME, hero)
        name_label.setStyleSheet("font-size: 22px;")
        text_col.addWidget(name_label)
        ver_label = BodyLabel("バージョン {}".format(APP_VERSION), hero)
        ver_label.setTextColor(QColor(150, 150, 150), QColor(130, 130, 130))
        text_col.addWidget(ver_label)
        text_col.addWidget(BodyLabel(APP_DESCRIPTION.replace("\n", " "), hero))
        hero_layout.addLayout(text_col, 1)
        body.addWidget(hero)

        # --- 詳細情報 ---
        info_group = SettingCardGroup("アプリ情報", container)
        self._add_info_card(info_group, FluentIcon.INFO, "バージョン", APP_VERSION)
        self._add_info_card(info_group, FluentIcon.DEVELOPER_TOOLS, "開発者", APP_AUTHOR)
        self._add_info_card(info_group, FluentIcon.CERTIFICATE, "ライセンス", APP_LICENSE)
        body.addWidget(info_group)

        # --- リンク ---
        links_group = SettingCardGroup("リンク", container)
        self.homepage_card = PushSettingCard(
            "開く", FluentIcon.LINK, "ホームページ", APP_HOMEPAGE, links_group)
        self.homepage_card.clicked.connect(self._open_homepage)
        links_group.addSettingCard(self.homepage_card)
        body.addWidget(links_group)

        body.addStretch(1)

    def _add_info_card(self, group, icon, title, content):
        card = SettingCard(icon, title, None, group)
        value = BodyLabel(content, card)
        card.hBoxLayout.addWidget(value, 0, Qt.AlignmentFlag.AlignRight)
        card.hBoxLayout.addSpacing(16)
        group.addSettingCard(card)
        return card

    def _open_homepage(self):
        import webbrowser
        webbrowser.open(APP_HOMEPAGE)


# ---------------------------------------------------------------------------
# メインウィンドウ
# ---------------------------------------------------------------------------

class MainWindow(FluentWindow):
    """Windows 11 設定画面スタイルのメインウィンドウ。

    左側に縦型ナビゲーション、右側にコンテンツ画面を配置する。
    """

    def __init__(self, core):
        super().__init__()
        self.core = core
        self.setWindowTitle(APP_NAME)
        self.resize(980, 680)

        self.download_interface = DownloadInterface(core, self)
        self.addon_interface = AddonInterface(core, self)
        self.settings_interface = SettingsInterface(core, self)
        self.about_interface = AboutInterface(core, self)

        self.addSubInterface(self.download_interface, FluentIcon.DOWNLOAD, "ダウンロード")
        self.addSubInterface(self.addon_interface, FluentIcon.ROBOT, "アドオン管理")
        self.addSubInterface(self.settings_interface, FluentIcon.SETTING, "設定")
        self.addSubInterface(self.about_interface, FluentIcon.HELP, "このアプリについて")

        # ダウンロード画面の「アドオン管理へ」ボタンでタブ切替
        self.download_interface.addon_btn.clicked.connect(
            lambda: self.switchTo(self.addon_interface))

        # セキュリティゲートで拒否されたアドオンを GUI に通知
        core.addon_rejected.connect(self._on_addon_rejected)

        # 本体の更新がステージングされたら通知（次回起動時に自動適用）
        core.app_update_ready.connect(self._on_app_update_ready)

    def _on_addon_rejected(self, name, reason):
        _info_bar("warning", "インストール拒否",
                  "{}\n({})".format(reason, name), self)

    def _on_app_update_ready(self, version):
        _info_bar("info", "本体の更新",
                  "v{} を更新準備しました。次回起動時に自動で適用されます。".format(version),
                  self)


def _startup_update(core):
    """起動時の自動アップデートをバックグラウンドで行う（GUI を塞がない）。

    本体は config.json の本体リポジトリ、アドオンは各アドオンの REPO を見る。
    """
    def work():
        try:
            result = core.auto_update_all()
            core.log("[UPDATE] {}".format(result), level="INFO")
        except Exception:
            print("[UPDATE] 例外: {}".format(traceback.format_exc()))

    threading.Thread(target=work, daemon=True).start()


def main():
    config = load_config()

    # テーマ設定（Windows 11 純正風ダークモード + アクセントカラー）
    setTheme(Theme.DARK if config.get("theme", "dark") == "dark" else Theme.LIGHT)
    try:
        setThemeColor(config.get("accent_color", "#0078D4"))
    except Exception:
        pass

    app = QApplication(sys.argv)

    core = Core(config)
    core.load_plugins()    # プラグインを先にロード（logger 等を利用可能に）
    core.load_addons()     # アドオンを動的ロード＆マニフェスト解析

    window = MainWindow(core)   # 更新通知シグナルは MainWindow が受け取る
    window.show()

    _startup_update(core)  # 起動時の自動アップデート（本体 + 各アドオン, 非同期）
    sys.exit(app.exec())


if __name__ == "__main__":
    main()