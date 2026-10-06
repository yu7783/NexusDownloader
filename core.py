"""Universal Downloader - 司令塔コア (core.py)

GUI に依存しないロジック層。以下を統括する。
  1. 動的インポート & マニフェスト解析
     addons/ 内の .py を importlib で動的ロードし、先頭コメントから
     「アドオン名」「バージョン」「対応URLパターン」を解析して登録する。
  2. プラグイン管理 (plugins/)
  3. URL から最適なアドオンを自動ジャッジ
  4. GitHub リポジトリからの自動アップデート

セキュリティゲートは security.SecurityGuard に委譲する。
GUI（InfoBar 等）には依存せず、通知は log() と戻り値で行う。
"""

import importlib.util
import json
import os
import re
import sys
import traceback

from PyQt6.QtCore import QObject, pyqtSignal

from security import SecurityGuard, scan_text

# ---------------------------------------------------------------------------
# パス定数（このファイルの位置を基準にする）
# ---------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ADDONS_DIR = os.path.join(BASE_DIR, "addons")
PLUGINS_DIR = os.path.join(BASE_DIR, "plugins")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")


# ---------------------------------------------------------------------------
# マニフェスト解析 & 共通ヘルパ
# ---------------------------------------------------------------------------

_MANIFEST_BLOCK = re.compile(r"#\s*-{2,}\s*(?:ADDON|PLUGIN)_MANIFEST\s*-{2,}", re.IGNORECASE)
_MANIFEST_FIELD = re.compile(
    r"^\s*#\s*(ID|NAME|VERSION|URL_PATTERNS)\s*:\s*(.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def read_manifest(path):
    """ファイル先頭コメントから「アドオン名」「バージョン」「対応URLパターン」を解析する。

    Returns:
        (manifest: dict | None, text: str)
        manifest は {"ID":..., "NAME":..., "VERSION":..., "URL_PATTERNS":...}。
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
# 司令塔コア本体
# ---------------------------------------------------------------------------

class Core(QObject):
    """アドオンの動的ロード・実行・更新を統括する司令塔。"""

    addon_loaded = pyqtSignal(dict)
    addon_rejected = pyqtSignal(str, str)
    update_checked = pyqtSignal(str)

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

    def auto_update_addons(self):
        """config.json の GitHub リポジトリをチェックし、新しければ addons/ を更新する。

        manifest.json は {"addons": [{"file": "...", "version": "x.y.z"}, ...]} を想定。
        取得したファイルもセキュリティスキャンしてから上書き配置する。

        Returns:
            str: 結果メッセージ。
        """
        if not self.config.get("auto_update", True):
            return "自動更新は無効です"
        base = (self.config.get("github_repo", "") or "").rstrip("/")
        manifest_name = self.config.get("update_manifest", "manifest.json")
        if not base:
            return "リポジトリ URL が未設定です"

        try:
            import requests
        except Exception:
            return "requests がインストールされていないため更新できません"

        try:
            resp = requests.get("{}/{}".format(base, manifest_name), timeout=10)
            resp.raise_for_status()
            remote = resp.json()
        except Exception as e:
            return "更新マニフェストの取得に失敗: {}".format(e)

        updated = []
        for item in remote.get("addons", []):
            filename = item.get("file")
            new_version = str(item.get("version", ""))
            if not filename:
                continue
            local_path = os.path.join(ADDONS_DIR, filename)
            local_version = "0.0.0"
            if os.path.exists(local_path):
                local_manifest, _ = read_manifest(local_path)
                if local_manifest:
                    local_version = local_manifest.get("VERSION", "0.0.0")

            if version_gt(new_version, local_version):
                try:
                    file_resp = requests.get("{}/addons/{}".format(base, filename), timeout=20)
                    file_resp.raise_for_status()
                    content = file_resp.text
                except Exception as e:
                    self.log("[UPDATE] ダウンロード失敗 {}: {}".format(filename, e), level="ERROR")
                    continue

                # 更新ファイルもセキュリティスキャンしてから配置
                safe, reason = scan_text(content, self.guard.blocked_keywords)
                if not safe:
                    self.log("[SECURITY] 更新を拒否: {} ({})".format(filename, reason))
                    continue

                tmp_path = local_path + ".tmp"
                with open(tmp_path, "w", encoding="utf-8") as f:
                    f.write(content)
                os.replace(tmp_path, local_path)
                updated.append("{} -> {}".format(filename, new_version))

        result = "更新完了: " + ", ".join(updated) if updated else "最新版です"
        self.update_checked.emit(result)
        return result