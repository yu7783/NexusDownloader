"""Universal Downloader - 司令塔コア (core.py)

GUI に依存しないロジック層。以下を統括する。
  1. 動的インポート & マニフェスト解析
     addons/ 内の .py を importlib で動的ロードし、先頭コメントから
     「アドオン名」「バージョン」「対応URLパターン」を解析して登録する。
  2. プラグイン管理 (plugins/)
  3. URL から最適なアドオンを自動ジャッジ
  4. 自動アップデート
     ・本体: config.json の ``github_repo``（本体リポジトリ）から取得し、
       次回起動時に適用（updater.py に委譲）
     ・アドオン: **各アドオンのマニフェストに書かれた ``REPO``** から個別に取得

セキュリティゲートは security.SecurityGuard に委譲する。
GUI（InfoBar 等）には依存せず、通知は log() と戻り値で行う。
"""

import importlib.util
import json
import os
import re
import sys
import time
import traceback
from urllib.parse import urlsplit, urlunsplit

from PyQt6.QtCore import QObject, pyqtSignal

import updater
from security import SecurityGuard, scan_text

# ---------------------------------------------------------------------------
# パス定数（このファイルの位置を基準にする）
# ---------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ADDONS_DIR = os.path.join(BASE_DIR, "addons")
PLUGINS_DIR = os.path.join(BASE_DIR, "plugins")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
VERSION_PATH = os.path.join(BASE_DIR, "version.txt")

#: version.txt が無い場合に使う本体バージョン
DEFAULT_APP_VERSION = "0.0.0"

#: 本体マニフェストで files が省略されたときに更新するファイル
DEFAULT_APP_FILES = ["main.py", "core.py", "security.py", "updater.py", "version.txt"]


# ---------------------------------------------------------------------------
# 本体バージョン
# ---------------------------------------------------------------------------

def read_app_version():
    """本体バージョン（``version.txt``）を読む。無ければ既定値を返す。"""
    try:
        with open(VERSION_PATH, "r", encoding="utf-8") as f:
            version = f.read().strip()
        return version or DEFAULT_APP_VERSION
    except Exception:
        return DEFAULT_APP_VERSION


# ---------------------------------------------------------------------------
# マニフェスト解析 & 共通ヘルパ
# ---------------------------------------------------------------------------

_MANIFEST_BLOCK = re.compile(r"#\s*-{2,}\s*(?:ADDON|PLUGIN)_MANIFEST\s*-{2,}", re.IGNORECASE)
_MANIFEST_FIELD = re.compile(
    r"^\s*#\s*(ID|NAME|VERSION|URL_PATTERNS|REPO)\s*:\s*(.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def read_manifest(path):
    """ファイル先頭コメントから「アドオン名」「バージョン」「対応URLパターン」「更新元」を解析する。

    Returns:
        (manifest: dict | None, text: str)
        manifest は {"ID":..., "NAME":..., "VERSION":..., "URL_PATTERNS":..., "REPO":...}。
        ID が無い場合は (None, text) を返す。
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except Exception:
        return None, ""

    match = _MANIFEST_BLOCK.search(text)
    if not match:
        return None, text

    # マニフェストブロックの開始行から終了行までを抽出
    end_marker = text.find("----", match.end())
    block = text[match.start():end_marker + 4] if end_marker != -1 else text[match.start():]

    manifest = {}
    for m in _MANIFEST_FIELD.finditer(block):
        manifest[m.group(1).upper()] = m.group(2).strip()

    if "ID" not in manifest:
        return None, text
    return manifest, text


def version_gt(v1, v2):
    """v1 > v2 を簡易比較する（数値部分のみを比較）。"""
    def parts(v):
        return [int(x) for x in re.findall(r"\d+", str(v))]

    p1, p2 = parts(v1), parts(v2)
    length = max(len(p1), len(p2))
    p1 += [0] * (length - len(p1))
    p2 += [0] * (length - len(p2))
    return p1 > p2


# ---------------------------------------------------------------------------
# URL 正規化（GitHub HTML URL → raw ベース URL）
# ---------------------------------------------------------------------------

#: GitHub のパスで「表示用」セグメント（除去して raw パスに変換する）
_GITHUB_VIEW_SEGMENTS = ("tree", "blob", "raw")

#: GitHub のパスで「リポジトリ直下のページ」セグメント（リポジトリ直下へ丸める）
_GITHUB_PAGE_SEGMENTS = (
    "releases", "issues", "pulls", "wiki", "actions", "commits", "tags",
    "branches", "discussions", "settings", "network", "graphs", "security",
)


def _github_content_path(segments):
    """GitHub のパスセグメントから表示用セグメントを除去して「中身」のパスにする。

    ``<owner>/<repo>/tree/<branch>/sub`` → ``<owner>/<repo>/<branch>/sub``
    ``<owner>/<repo>/blob/<branch>/x.py`` → ``<owner>/<repo>/<branch>/x.py``
    ``<owner>/<repo>/releases``           → ``<owner>/<repo>``
    """
    if len(segments) >= 3:
        marker = segments[2].lower()
        if marker in _GITHUB_VIEW_SEGMENTS:
            return segments[:2] + segments[3:]
        if marker in _GITHUB_PAGE_SEGMENTS:
            return segments[:2]
    return segments


def normalize_repo_url(url):
    """リポジトリ URL を「raw ベース URL」へ正規化する。

    ``github.com/<owner>/<repo>/tree/<branch>`` のような **HTML ページ URL** を
    そのまま ``<base>/manifest.json`` として連結すると 404 になるため、
    連結して使える形へ必ず揃える（ブランチ未指定なら ``HEAD`` を補う）。

    Examples:
        https://github.com/u/r                          -> https://raw.githubusercontent.com/u/r/HEAD
        https://github.com/u/r/tree/main                -> https://raw.githubusercontent.com/u/r/main
        https://github.com/u/r/tree/main/addons         -> https://raw.githubusercontent.com/u/r/main/addons
        https://github.com/u/r/blob/main/a/x.py         -> https://raw.githubusercontent.com/u/r/main/a/x.py
        https://github.com/u/r/raw/main/x.py            -> https://raw.githubusercontent.com/u/r/main/x.py
        https://github.com/u/r/releases                 -> https://raw.githubusercontent.com/u/r/HEAD
        https://raw.githubusercontent.com/u/r           -> https://raw.githubusercontent.com/u/r/HEAD
        https://raw.githubusercontent.com/u/r/tree/main -> https://raw.githubusercontent.com/u/r/main

    Args:
        url: ユーザーが入力した URL（前後の空白・引用符・``<>`` は無視する）。

    Returns:
        str: 正規化したベース URL。空入力・不正な入力なら空文字列。
    """
    text = str(url or "").strip().strip("<>\"'").strip()
    if not text:
        return ""
    # クエリ / フラグメントを落とす
    text = text.split("#", 1)[0].split("?", 1)[0].strip()
    if not text:
        return ""
    # スキーム省略（github.com/... や //github.com/...）にも対応
    if text.startswith("//"):
        text = "https:" + text
    elif "://" not in text:
        text = "https://" + text

    parts = urlsplit(text)
    host = parts.netloc.lower()
    if "@" in host:  # 認証情報付き URL は扱わない
        return ""
    if host.startswith("www."):
        host = host[4:]

    segments = [seg for seg in parts.path.split("/") if seg]
    if host == "github.com":
        host = "raw.githubusercontent.com"
        segments = _github_content_path(segments)
    elif host == "raw.githubusercontent.com":
        # raw URL に /tree/ や /blob/ が混ざっている場合も掃除する
        segments = _github_content_path(segments)

    if host == "raw.githubusercontent.com":
        if len(segments) < 2:
            return ""
        if len(segments) == 2:
            segments = segments + ["HEAD"]  # ブランチ未指定 → 既定ブランチ(HEAD)

    path = "/".join(segments)
    return urlunsplit((parts.scheme or "https", host, "/" + path if path else "", "", ""))


def join_url(base, *parts):
    """URL を安全に連結する（スラッシュの重複・欠落を防ぐ）。

    Args:
        base: ベース URL（末尾スラッシュは自動で除去）。
        *parts: 連結するパス片（前後のスラッシュは自動で除去）。

    Returns:
        str: 連結した URL。
    """
    url = str(base or "").strip().rstrip("/")
    if not url:
        return ""
    for part in parts:
        piece = str(part or "").strip().strip("/")
        if piece:
            url = "{}/{}".format(url, piece)
    return url


def repo_base_from_url(url):
    """ファイル URL から「リポジトリ（ブランチ）のベース URL」を取り出す。

    ``.../<branch>/addons/x.py`` → ``.../<branch>``
    ``.../<branch>/x.py``        → ``.../<branch>``

    スキーム（``https://``）を壊さずに組み立てるため :func:`urlsplit` を使う。

    Args:
        url: ファイルを指す URL。

    Returns:
        str: ベース URL。判定できなければ空文字列。
    """
    text = str(url or "").strip()
    if not text:
        return ""
    parts = urlsplit(text)
    segments = [seg for seg in parts.path.split("/") if seg]
    if len(segments) < 2:
        return ""
    if segments[-2].lower() == "addons":
        segments = segments[:-2]
    else:
        segments = segments[:-1]
    path = "/".join(segments)
    return urlunsplit((parts.scheme or "https", parts.netloc, "/" + path if path else "", "", "")).rstrip("/")
# ---------------------------------------------------------------------------
# 司令塔コア本体
# ---------------------------------------------------------------------------

class Core(QObject):
    """アドオンの動的ロード・実行・更新を統括する司令塔。"""

    addon_loaded = pyqtSignal(dict)
    addon_rejected = pyqtSignal(str, str)
    update_checked = pyqtSignal(str)
    app_update_ready = pyqtSignal(str)   # 本体の更新をステージングした（新バージョン）

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.guard = SecurityGuard(config.get("blocked_keywords"))
        self.addons = {}      # id -> {"manifest":..., "module":..., "path":..., "enabled":...}
        self._plugins = {}    # name -> instance

    # -- 設定 ---------------------------------------------------------------

    def download_dir(self):
        """保存先フォルダを返す（相対パスは BASE_DIR 基準で解決し、作成する）。"""
        d = self.config.get("download_dir", "downloads")
        if not os.path.isabs(d):
            d = os.path.join(BASE_DIR, d)
        os.makedirs(d, exist_ok=True)
        return d

    def save_config(self):
        """現在の self.config を config.json へ書き出す。

        Returns:
            (ok: bool, message: str)
        """
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(self.config, f, ensure_ascii=False, indent=4)
            return True, "設定を保存しました"
        except Exception as e:
            return False, "設定の保存に失敗: {}".format(e)

    def update_config(self, **kwargs):
        """設定値を更新して保存する。unknown キーはそのまま追加される。

        Returns:
            (ok: bool, message: str)
        """
        # ブラックリスト変更時は SecurityGuard も同期させる
        if "blocked_keywords" in kwargs:
            self.set_blocked_keywords(kwargs["blocked_keywords"])

        self.config.update(kwargs)

        # ダウンロード先が変わった場合はフォルダを再作成
        if "download_dir" in kwargs:
            self.download_dir()

        return self.save_config()

    def set_blocked_keywords(self, keywords):
        """ブロックする危険キーワードを更新し、SecurityGuard に反映する。"""
        if isinstance(keywords, str):
            keywords = [k.strip() for k in keywords.split(",") if k.strip()]
        keywords = [str(k).strip() for k in keywords if str(k).strip()]
        self.config["blocked_keywords"] = keywords
        self.guard = SecurityGuard(keywords)

    # -- 速度テスト & 並列最適化 --------------------------------------------

    def speed_test(self, timeout=6):
        """ネットワーク帯域を簡易計測し、推奨の並列数・チャンクサイズを返す。

        外部の速度計測エンドポイントへ小さなファイルを取りに行き、
        実測スループット（KiB/s）からダウンロードの並列数とチャンクサイズを
        見積もる。ネットワークが無い環境でも安全に既定値を返す。

        Returns:
            dict: {
                "speed_kib": float,       # 実測スループット
                "parallel": int,          # 推奨並列数
                "chunk_size": int,        # 推奨チャンクサイズ(bytes)
                "message": str,
            }
        """
        import time

        # 計測用の小さなファイル（複数のミラーを順に試す）
        urls = [
            "https://speed.cloudflare.com/__down?bytes=2000000",
            "https://raw.githubusercontent.com/yt-dlp/yt-dlp/master/README.md",
        ]

        result = {
            "speed_kib": 0.0,
            "parallel": 4,
            "chunk_size": 10 * 1024 * 1024,
            "message": "",
        }

        speed_kib = 0.0
        for url in urls:
            try:
                import requests
                start = time.time()
                resp = requests.get(url, timeout=timeout, stream=True)
                resp.raise_for_status()

                downloaded = 0
                for chunk in resp.iter_content(chunk_size=65536):
                    if not chunk:
                        continue
                    downloaded += len(chunk)
                    if time.time() - start > timeout:
                        break
                elapsed = max(time.time() - start, 1e-3)
                if downloaded > 0:
                    speed_kib = (downloaded / 1024.0) / elapsed
                    break
            except Exception as e:
                result["message"] = "計測失敗: {}".format(e)
                continue

        result["speed_kib"] = round(speed_kib, 1)

        # 実測速度から並列数を決定（速い回線ほど並列を増やす）
        if speed_kib >= 8000:       # 約 64 Mbps 以上
            parallel = 8
        elif speed_kib >= 4000:     # 約 32 Mbps 以上
            parallel = 6
        elif speed_kib >= 1500:     # 約 12 Mbps 以上
            parallel = 4
        elif speed_kib > 0:
            parallel = 2
        else:
            parallel = 4            # 計測不能時は無難な既定値

        # 帯域に応じたチャンクサイズ（大きいほど高速だがメモリ消費増）
        chunk_size = 10 * 1024 * 1024 if speed_kib >= 4000 else 5 * 1024 * 1024

        result["parallel"] = parallel
        result["chunk_size"] = chunk_size

        if speed_kib > 0:
            result["message"] = "計測完了: 約 {:.0f} KiB/s".format(speed_kib)
        elif not result["message"]:
            result["message"] = "計測できませんでした（既定値を適用）"

        return result

    def optimize_parallel(self, apply=True):
        """速度テストを実行し、最適な並列数を config に保存する。

        Returns:
            (ok: bool, message: str)
        """
        profile = self.speed_test()
        if apply:
            self.config["parallel"] = profile["parallel"]
            self.config["chunk_size"] = profile["chunk_size"]
            self.save_config()
        return True, "{} / 推奨並列数: {} / チャンク: {} MB".format(
            profile["message"],
            profile["parallel"],
            profile["chunk_size"] // (1024 * 1024),
        )

    # -- 動的インポート & マニフェスト解析 ----------------------------------

    def load_addons(self):
        """addons/ 内の .py ファイルをスキャンし、動的にロード・登録する。"""
        os.makedirs(ADDONS_DIR, exist_ok=True)
        loaded = []
        for name in sorted(os.listdir(ADDONS_DIR)):
            if not name.endswith(".py") or name.startswith("_"):
                continue
            path = os.path.join(ADDONS_DIR, name)

            # 1) マニフェスト解析
            manifest, _ = read_manifest(path)
            if manifest is None:
                self.log("[WARN] マニフェストなし、スキップ: {}".format(name))
                continue

            # 2) セキュリティゲート（実行前にテキストスキャン）
            safe, reason = self.guard.scan(path)
            if not safe:
                self.log("[SECURITY] アドオンを拒否: {} ({})".format(name, reason))
                self.addon_rejected.emit(name, reason)
                continue

            # 3) 動的インポート
            module = self._import_module(path, "addon_" + manifest["ID"])
            if module is None:
                continue

            previous = self.addons.get(manifest["ID"])
            entry = {
                "manifest": manifest,
                "module": module,
                "path": path,
                "name": manifest.get("NAME", manifest["ID"]),
                "enabled": previous.get("enabled", True) if previous else True,
            }
            self.addons[manifest["ID"]] = entry
            loaded.append(entry)
            self.addon_loaded.emit(entry)
            self.log("[LOAD] アドオン登録: {} v{}".format(
                entry["name"], manifest.get("VERSION", "-")))
        return loaded

    def _import_module(self, path, module_name):
        """ファイルパスからモジュールを動的にロードする。"""
        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            return module
        except Exception:
            self.log("[ERROR] モジュールのロードに失敗: {}".format(path), level="ERROR")
            print(traceback.format_exc())
            return None

    # -- プラグイン管理 -----------------------------------------------------

    def register_plugin(self, name, instance):
        """プラグインインスタンスを名前で登録する。"""
        self._plugins[name] = instance

    def load_plugins(self):
        """plugins/ 内の .py をスキャンし、register(core) フックを呼ぶ。"""
        os.makedirs(PLUGINS_DIR, exist_ok=True)
        for name in sorted(os.listdir(PLUGINS_DIR)):
            if not name.endswith(".py") or name.startswith("_"):
                continue
            path = os.path.join(PLUGINS_DIR, name)

            safe, reason = self.guard.scan(path)
            if not safe:
                self.log("[SECURITY] プラグインを拒否: {} ({})".format(name, reason))
                continue

            module = self._import_module(path, "plugin_" + os.path.splitext(name)[0])
            if module is None:
                continue
            if hasattr(module, "register"):
                try:
                    module.register(self)
                except Exception:
                    self.log("[ERROR] プラグイン登録に失敗: {}".format(name), level="ERROR")
                    print(traceback.format_exc())
        return self._plugins

    def log(self, message, level="INFO"):
        """登録済みプラグイン経由でログを残す。プラグインが無ければ標準出力へ。"""
        plugin = self._plugins.get("logger")
        if plugin is not None and hasattr(plugin, "log"):
            try:
                plugin.log(message, level)
                return
            except Exception:
                pass
        print("[{}] {}".format(level, message))

    # -- アドオン選択 -------------------------------------------------------

    def find_addon_for_url(self, url):
        """登録された URL パターンから最適なアドオンを自動ジャッジする。

        複数のアドオンが一致した場合は、より長く一致したパターンを持つものを選ぶ。
        無効化（enabled=False）されたアドオンは対象外。
        """
        host = (url or "").lower()
        best = None
        for entry in self.addons.values():
            if not entry.get("enabled", True):
                continue
            patterns = entry["manifest"].get("URL_PATTERNS", "")
            for pat in [p.strip() for p in patterns.split(",") if p.strip()]:
                if pat.lower() in host:
                    if best is None or len(pat) > best[0]:
                        best = (len(pat), entry)
        return best[1] if best else None

    def get_addon_options(self, addon):
        """アドオンが提示する選択肢（画質・音質・拡張子など）を取得する。

        アドオンは任意で ``get_options()`` を定義できる。戻り値は次の形式の dict::

            {
                "format":  {"label": "拡張子", "options": ["mp4", "webm", "mp3"],
                            "default": "mp4"},
                "quality": {"label": "画質", "options": ["1080p", "720p", "best"],
                            "default": "1080p", "content": "解像度を選択"},
            }

        - キー: 本体が ``download_logic`` へ渡す ``options`` dict のキー名。
        - ``label``   : GUI に表示する見出し（無ければキー名を使用）。
        - ``options`` : 選択肢の文字列リスト。
        - ``default`` : 初期選択値（省略時は先頭）。
        - ``content`` : 補足説明（任意）。

        ``get_options`` が無い / 例外を出すアドオンは ``{}`` を返し、
        GUI 側は選択 UI を表示しない（後方互換）。

        Returns:
            dict: 正規化済みのオプション定義。
        """
        module = addon.get("module") if isinstance(addon, dict) else None
        func = getattr(module, "get_options", None) if module is not None else None
        if not callable(func):
            return {}

        try:
            raw = func()
        except Exception:
            self.log("[WARN] get_options の取得に失敗", level="ERROR")
            print(traceback.format_exc())
            return {}

        return self._normalize_options(raw)

    @staticmethod
    def _normalize_options(raw):
        """アドオンが返した選択肢定義を検証・正規化する。"""
        if not isinstance(raw, dict):
            return {}

        normalized = {}
        for key, spec in raw.items():
            if not isinstance(spec, dict):
                continue
            choices = spec.get("options")
            if not isinstance(choices, (list, tuple)) or not choices:
                continue
            choices = [str(c) for c in choices]

            default = spec.get("default", choices[0])
            default = str(default)
            if default not in choices:
                default = choices[0]

            normalized[str(key)] = {
                "label": str(spec.get("label", key)),
                "options": choices,
                "default": default,
                "content": str(spec.get("content", "")),
            }
        return normalized

    def default_options(self, addon):
        """``get_addon_options`` の既定値を ``options`` dict にして返す。"""
        return {k: v["default"] for k, v in self.get_addon_options(addon).items()}

    # -- 自動アップデート ---------------------------------------------------

    # __UPDATE_METHODS_PLACEHOLDER__

    def _fetch_json(self, url):
        """JSON を取得する。失敗時はログを残して None を返す。"""
        try:
            import requests
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            self.log("[UPDATE] 取得失敗 {}: {}".format(url, e), level="ERROR")
            return None

    def _fetch_text(self, url):
        """テキスト（本体/アドオンのソース）を取得する。失敗時は None を返す。"""
        try:
            import requests
            resp = requests.get(url, timeout=20)
            resp.raise_for_status()
            return resp.text
        except Exception as e:
            self.log("[UPDATE] 取得失敗 {}: {}".format(url, e), level="ERROR")
            return None

    @staticmethod
    def _find_remote_addon(remote, addon_id, filename):
        """リモートマニフェストから対象アドオンのエントリを探す。

        まず ``id`` の一致、次に ``file`` の一致で探す（``id`` 省略の旧形式にも対応）。
        """
        entries = remote.get("addons") or []
        if not isinstance(entries, list):
            return None
        for item in entries:
            if isinstance(item, dict) and str(item.get("id", "")) == addon_id:
                return item
        for item in entries:
            if isinstance(item, dict) and str(item.get("file", "")) == filename:
                return item
        return None

    def _update_due(self):
        """config の ``check_interval_hours`` から、今チェックすべきか判定する。

        ``0`` の場合は「起動時のみ」＝毎回の起動でチェックする。
        """
        try:
            interval = float(self.config.get("check_interval_hours", 24) or 0)
        except Exception:
            interval = 24.0
        if interval <= 0:
            return True
        try:
            last = float(self.config.get("last_update_check", 0) or 0)
        except Exception:
            last = 0.0
        return (time.time() - last) >= interval * 3600

    def _mark_update_checked(self):
        """更新チェック時刻を記録する（次回の間隔判定に使う）。"""
        self.config["last_update_check"] = time.time()
        self.save_config()

    def auto_update_all(self):
        """本体と全アドオンをまとめて更新チェックする（起動時・手動チェック用）。

        Returns:
            str: 結果メッセージ。
        """
        if not self.config.get("auto_update", True):
            return "自動更新は無効です"
        if not self._update_due():
            return "更新チェックをスキップしました（前回チェックから間隔未満）"

        app_result = self.check_app_update()
        addon_result = self.auto_update_addons()
        self._mark_update_checked()

        result = "本体: {} / アドオン: {}".format(app_result, addon_result)
        self.update_checked.emit(result)
        return result

    def check_app_update(self):
        """本体リポジトリ（config: ``github_repo``）から本体の更新をステージングする。

        マニフェスト（``update_manifest``、既定 manifest.json）は次の形を想定::

            {
              "app": {
                "version": "1.1.0",
                "files": ["main.py", "core.py", "security.py", "updater.py"],
                "notes": "リリースノート（任意）"
              }
            }

        ``files`` を省略した場合は :data:`DEFAULT_APP_FILES` を使う。取得した
        ファイルはセキュリティスキャン後 ``update/`` にステージングされ、
        **次回起動時** に適用される（実行中の本体は書き換えない）。

        Returns:
            str: 結果メッセージ。
        """
        if not self.config.get("auto_update", True):
            return "自動更新は無効です"
        if updater.is_frozen():
            return "ビルド済み版ではファイル差し替え更新は利用できません"

        base = normalize_repo_url(self.config.get("github_repo", ""))
        if not base:
            return "本体リポジトリ URL が未設定です"

        manifest_name = self.config.get("update_manifest", "manifest.json")
        remote = self._fetch_json(join_url(base, manifest_name))
        if remote is None:
            return "本体マニフェストの取得に失敗"

        app_info = remote.get("app")
        if not isinstance(app_info, dict):
            app_info = {}
        remote_version = str(app_info.get("version") or remote.get("version") or "").strip()
        if not remote_version:
            return "本体マニフェストに app.version がありません"

        local_version = read_app_version()
        if not version_gt(remote_version, local_version):
            return "本体は最新版です (v{})".format(local_version)

        files = app_info.get("files") or DEFAULT_APP_FILES
        staged = {}
        for rel in files:
            rel = str(rel).strip()
            if not rel:
                continue
            content = self._fetch_text(join_url(base, rel))
            if content is None:
                return "本体ファイルの取得に失敗: {}".format(rel)

            safe, reason = scan_text(content, self.guard.blocked_keywords)
            if not safe:
                self.log("[SECURITY] 本体更新を拒否: {} ({})".format(rel, reason))
                return "本体更新を拒否しました ({}): {}".format(rel, reason)
            staged[rel] = content

        ok, message = updater.stage_update(
            remote_version, staged,
            notes=app_info.get("notes", ""), source=base)
        if not ok:
            return message

        self.log("[UPDATE] 本体 v{} を更新準備（次回起動時に適用）".format(remote_version))
        self.app_update_ready.emit(remote_version)
        return message

    def auto_update_addons(self):
        """**各アドオンの REPO** を見て、新しければ ``addons/`` を更新する。

        更新元はアドオン自身のマニフェストコメントに書かれた ``REPO``
        （raw 形式のベース URL。``github.com/.../tree/<branch>`` の HTML URL でも
        自動で raw へ変換する）を使う。``REPO`` が無いアドオンは更新対象外。

        各リポジトリ直下の ``update_manifest``（既定 manifest.json）に::

            {"addons": [{"id": "yt_dlp_core", "file": "yt_dlp_addon.py",
                         "version": "1.2.0",
                         "path": "addons/yt_dlp_addon.py"}, ...]}

        がある想定（``id`` か ``file`` が一致するエントリを採用。``path`` 省略時は
        ``addons/<file>``）。取得したファイルはセキュリティスキャンしてから上書きする。

        Returns:
            str: 結果メッセージ。
        """
        if not self.config.get("auto_update", True):
            return "自動更新は無効です"
        try:
            import requests  # noqa: F401
        except Exception:
            return "requests がインストールされていないため更新できません"

        if not os.path.isdir(ADDONS_DIR):
            return "addons フォルダがありません"

        manifest_name = self.config.get("update_manifest", "manifest.json")
        updated, failed, no_repo = [], [], []

        for filename in sorted(os.listdir(ADDONS_DIR)):
            if not filename.endswith(".py") or filename.startswith("_"):
                continue
            local_path = os.path.join(ADDONS_DIR, filename)
            manifest, _text = read_manifest(local_path)
            if not manifest:
                continue

            addon_id = manifest.get("ID", filename)
            # REPO は github.com の HTML URL でも受け付ける（raw ベースへ正規化）
            repo = normalize_repo_url(manifest.get("REPO"))
            if not repo:
                no_repo.append(addon_id)
                continue

            remote = self._fetch_json(join_url(repo, manifest_name))
            if remote is None:
                failed.append("{} (マニフェスト取得失敗)".format(addon_id))
                continue

            entry = self._find_remote_addon(remote, addon_id, filename)
            if not entry:
                continue

            remote_version = str(entry.get("version", "")).strip()
            local_version = manifest.get("VERSION", "0.0.0")
            if not version_gt(remote_version, local_version):
                continue

            rel = str(entry.get("path") or "addons/{}".format(entry.get("file") or filename))
            content = self._fetch_text(join_url(repo, rel))
            if content is None:
                failed.append("{} (ダウンロード失敗)".format(addon_id))
                continue

            # 更新ファイルもセキュリティスキャンしてから配置
            safe, reason = scan_text(content, self.guard.blocked_keywords)
            if not safe:
                self.log("[SECURITY] 更新を拒否: {} ({})".format(filename, reason))
                failed.append("{} (セキュリティ拒否)".format(addon_id))
                continue

            tmp_path = local_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp_path, local_path)
            updated.append("{} v{} -> v{}".format(filename, local_version, remote_version))
            self.log("[UPDATE] アドオン更新: {} v{} -> v{}".format(
                filename, local_version, remote_version))

        parts = []
        if updated:
            parts.append("更新完了: " + ", ".join(updated))
        if failed:
            parts.append("失敗: " + ", ".join(failed))
        if no_repo:
            parts.append("REPO 未設定のためスキップ: " + ", ".join(no_repo))
        return " / ".join(parts) if parts else "最新版です"