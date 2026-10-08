"""Nexus Downloader - 本体の自己更新ユーティリティ (updater.py)

このモジュールは意図的に **標準ライブラリのみ** に依存する。

理由: 本体（main.py / core.py / security.py など）の更新は、
      「core.py / security.py を import する前」に適用する必要がある。
      PyQt6 や本体モジュールに依存すると、循環 import や Qt 初期化の
      タイミング問題が発生するため、あえて独立させている。

更新の流れ:
  1. ``core.Core.check_app_update()`` が本体リポジトリから新バージョンを取得し、
     ファイルを ``update/files/`` にステージングして ``update/pending.json`` を書く
     （この時点では本体ファイルは一切書き換えない）。
  2. 次回起動時、``main.py`` が ``core`` を import する前に
     :func:`apply_pending_update` を呼び、ステージング分を本体へ上書きコピーする。
  3. 新旧バージョンが混在したまま実行されるのを避けるため、
     適用後は :func:`restart_application` で自分自身を新しいコードで再起動する。

安全のため、``addons/`` ``plugins/`` ``config.json`` は更新対象から除外する
（利用者のデータとアドオンを本体更新で壊さないため）。
"""

import json
import os
import shutil
import sys

# ---------------------------------------------------------------------------
# パス / 定数
# ---------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPDATE_DIR = os.path.join(BASE_DIR, "update")
FILES_DIR = os.path.join(UPDATE_DIR, "files")
BACKUP_DIR = os.path.join(UPDATE_DIR, "backup")
PENDING_FILE = os.path.join(UPDATE_DIR, "pending.json")
VERSION_FILENAME = "version.txt"

#: 適用後の自己再起動で無限ループしないための目印（環境変数）
RESTART_FLAG = "NEXUS_UPDATE_RESTARTED"

#: 本体更新で書き換えてはいけないパス（利用者のデータ保護）
PROTECTED_PREFIXES = ("addons/", "plugins/", "update/", "downloads/", "logs/")
PROTECTED_FILES = ("config.json",)


# ---------------------------------------------------------------------------
# 環境判定 / 共通ヘルパ
# ---------------------------------------------------------------------------

def is_frozen():
    """ビルド済み（PyInstaller / Nuitka など）の実行かどうかを返す。

    ビルド済みの場合、``.py`` を差し替えても動作は変わらないため、
    本体の自動更新（ソース差し替え方式）は利用できない。
    """
    if getattr(sys, "frozen", False):
        return True
    return "__compiled__" in globals()


def _safe_relpath(path):
    """BASE_DIR 配下に収まる相対パスだけを許可する（``..`` や絶対パスを拒否）。

    Returns:
        str | None: 正規化した相対パス（``/`` 区切り）。不正なら None。
    """
    if not path:
        return None
    text = str(path).strip()
    if not text or ":" in text or text.startswith(("/", "\\")):
        return None
    text = text.replace("\\", "/")
    parts = [p for p in text.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return None
    rel = "/".join(parts)

    lowered = rel.lower()
    if lowered in PROTECTED_FILES:
        return None
    if any(lowered.startswith(prefix) for prefix in PROTECTED_PREFIXES):
        return None
    return rel


def _log(message):
    """標準出力へ更新ログを出す（GUI 初期化前にも呼ばれるため print を使う）。"""
    print("[UPDATER] {}".format(message))


# ---------------------------------------------------------------------------
# ステージング（core.py から呼ばれる）
# ---------------------------------------------------------------------------

def pending_info():
    """ステージング済みの更新情報を返す。無ければ None。"""
    if not os.path.isfile(PENDING_FILE):
        return None
    try:
        with open(PENDING_FILE, "r", encoding="utf-8") as f:
            info = json.load(f)
    except Exception:
        return None
    return info if isinstance(info, dict) else None


def stage_update(version, files, notes="", source=""):
    """本体の更新ファイルを ``update/`` にステージングする（適用はしない）。

    Args:
        version: 新しい本体バージョン文字列（例 ``"1.1.0"``）。
        files: ``{"main.py": "<ソース>", ...}`` の dict（BASE_DIR 相対パス）。
        notes: リリースノート（任意）。
        source: 取得元のベース URL（ログ／記録用）。

    Returns:
        (ok: bool, message: str)
    """
    staged = {}
    for rel, content in (files or {}).items():
        safe = _safe_relpath(rel)
        if safe is None:
            return False, "更新対象として不正なパスです: {}".format(rel)
        staged[safe] = content

    if not staged:
        return False, "更新ファイルがありません"

    try:
        discard_pending()  # 前回のステージングを破棄してから書き直す
        for rel, content in staged.items():
            dest = os.path.join(FILES_DIR, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "w", encoding="utf-8") as f:
                f.write(content)

        info = {
            "version": str(version),
            "notes": str(notes or ""),
            "source": str(source or ""),
            "files": sorted(staged.keys()),
        }
        os.makedirs(UPDATE_DIR, exist_ok=True)
        with open(PENDING_FILE, "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=4)
    except Exception as e:
        return False, "更新のステージングに失敗: {}".format(e)

    return True, "本体 v{} を更新準備しました（次回起動時に適用）".format(version)


def discard_pending():
    """ステージングされた更新（``update/files`` と pending.json）を破棄する。

    適用時に退避したバックアップ（``update/backup``）はロールバック用に残す。
    """
    shutil.rmtree(FILES_DIR, ignore_errors=True)
    try:
        if os.path.isfile(PENDING_FILE):
            os.remove(PENDING_FILE)
    except Exception:
        pass
    # 空になった update/ も片付ける（バックアップが残っていれば何もしない）
    try:
        if os.path.isdir(UPDATE_DIR) and not os.listdir(UPDATE_DIR):
            os.rmdir(UPDATE_DIR)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 適用（main.py から core の import 前に呼ばれる）
# ---------------------------------------------------------------------------

def apply_pending_update():
    """ステージング済みの本体更新を実際に適用する。

    上書き前に対象ファイルのバックアップを ``update/backup/`` へ退避し、
    ``version.txt`` を新しいバージョンで書き換える（``version.txt`` が
    更新ファイルに含まれている場合はその内容を優先する）。

    Returns:
        str | None: 適用した場合は新しいバージョン文字列、未適用なら None。
    """
    info = pending_info()
    if not info:
        return None

    version = str(info.get("version", "")).strip()

    # ビルド済み版では .py の差し替えが無意味なため、ステージングを捨てるだけ
    if is_frozen():
        _log("ビルド済み版のためファイル差し替え更新はスキップしました (v{})".format(version))
        discard_pending()
        return None

    applied, failed = [], []
    for rel in info.get("files", []):
        safe = _safe_relpath(rel)
        if safe is None:
            failed.append(str(rel))
            continue
        src = os.path.join(FILES_DIR, safe.replace("/", os.sep))
        dst = os.path.join(BASE_DIR, safe.replace("/", os.sep))
        if not os.path.isfile(src):
            failed.append(safe)
            continue
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if os.path.isfile(dst):
                backup = os.path.join(BACKUP_DIR, safe.replace("/", os.sep))
                os.makedirs(os.path.dirname(backup), exist_ok=True)
                shutil.copy2(dst, backup)
            shutil.copy2(src, dst)
            applied.append(safe)
        except Exception as e:
            _log("適用に失敗 {}: {}".format(safe, e))
            failed.append(safe)

    if not applied:
        _log("適用できたファイルがありませんでした")
        discard_pending()
        return None

    # version.txt が更新ファイルに含まれていなければ、適用バージョンで書き換える
    if version and VERSION_FILENAME not in applied:
        try:
            with open(os.path.join(BASE_DIR, VERSION_FILENAME), "w", encoding="utf-8") as f:
                f.write(version + "\n")
        except Exception as e:
            _log("version.txt の更新に失敗: {}".format(e))

    discard_pending()
    if failed:
        _log("一部のファイルを適用できませんでした: {}".format(", ".join(failed)))
    _log("本体を v{} に更新しました ({})".format(version, ", ".join(applied)))
    return version


def restart_application():
    """適用済みの更新を反映するため、自分自身を新しいコードで再起動する。

    ``os.execv`` は Windows でも利用可能（内部で新プロセス起動 + 自プロセス終了）。
    環境変数 :data:`RESTART_FLAG` で無限ループを防ぐ。

    Returns:
        bool: 再起動を開始した場合は True。
    """
    if is_frozen():
        return False
    if os.environ.get(RESTART_FLAG):
        return False  # 既に 1 度再起動済み（ループ防止）
    os.environ[RESTART_FLAG] = "1"
    try:
        _log("更新を反映するためアプリを再起動します...")
        os.execv(sys.executable, [sys.executable] + sys.argv)
        return True
    except Exception as e:
        _log("再起動に失敗しました: {}".format(e))
        _log("更新は適用済みです。アプリを手動で再起動してください。")
        return False