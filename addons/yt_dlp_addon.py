# --- ADDON_MANIFEST ---
# ID: yt_dlp_core
# NAME: Universal Video Addon (yt-dlp)
# VERSION: 1.2.0
# URL_PATTERNS: youtube.com, youtu.be, twitter.com, x.com, tiktok.com, nicovideo.jp
# REPO: https://raw.githubusercontent.com/yu7783/NexusDownloader-Addons/main
# ----------------------

import importlib
import json
import os
import sys

# yt-dlp は import 自体が重い（起動時間に影響する）ため、
# download_logic の中で必要になったときだけ import する。

#: 本体の config.json（このファイルは addons/ 配下にあるため 1 つ上になる）。
_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")

#: 「Cookiesファイル」でパスを空にしたときに自動使用する cookies.txt。
_DEFAULT_COOKIE_FILE = os.path.join(os.path.dirname(_CONFIG_PATH), "cookies.txt")

#: ログイン方法のラベル（GUI の ComboBox に表示される値）。
_LOGIN_NONE = "なし"
_LOGIN_BROWSER = "ブラウザのCookie"
_LOGIN_COOKIE_FILE = "Cookiesファイル"
_LOGIN_CREDENTIALS = "IDとパスワード"

#: 「ブラウザのCookie」で選ぶブラウザ（表示ラベル → yt-dlp のブラウザ名）。
_BROWSER_MAP = {
    "Chrome": "chrome",
    "Edge": "edge",
    "Firefox": "firefox",
    "Brave": "brave",
    "Vivaldi": "vivaldi",
    "Opera": "opera",
    "Chromium": "chromium",
    "Safari": "safari",
}


def get_options():
    """本体のダウンロード画面に表示する選択肢を提示する。

    本体（core.Core.get_addon_options → main.DownloadInterface）がこの戻り値を
    読み取り、ComboBox / テキスト入力を自動生成する。選択・入力された値は
    ``download_logic`` の ``options`` 引数としてそのまま渡ってくる。

    ニコニコ動画の年齢制限（Sensitive）動画などは **ログインしていないと取得
    できない**（yt-dlp が "Sensitive content, login required" を返す）ため、
    ログイン関連の項目も提示する。

    - 「ブラウザのCookie」  : 普段ログインしているブラウザから Cookie を読む（推奨）
    - 「Cookiesファイル」   : Netscape 形式 cookies.txt を読む
    - 「IDとパスワード」    : yt-dlp に ID / パスワードを渡してログインする

    ``type`` を解釈できない旧バージョンの本体では、``select`` 以外の項目は
    表示されない。その場合でも config.json に
    ``login`` / ``login_browser`` / ``cookies_file`` / ``username`` / ``password`` /
    ``twofactor``（別名 ``niconico_user`` 等）を書いておけば同じように動作する。
    """
    return {
        "quality": {
            "label": "画質",
            "options": ["best", "1080p", "720p", "480p", "360p", "audio only"],
            "default": "best",
            "content": "動画の最大解像度。audio only は音声のみを取得します。",
        },
        "format": {
            "label": "拡張子",
            "options": ["mp4", "webm", "mkv", "mp3", "m4a"],
            "default": "mp4",
            "content": "出力コンテナ / 音声フォーマット。",
        },
        "audio_bitrate": {
            "label": "音質",
            "options": ["best", "320kbps", "192kbps", "128kbps"],
            "default": "best",
            "content": "音声のみ / mp3 出力時のビットレート。",
        },
        "login": {
            "label": "ログイン",
            "options": [_LOGIN_NONE, _LOGIN_BROWSER, _LOGIN_COOKIE_FILE, _LOGIN_CREDENTIALS],
            "default": _LOGIN_NONE,
            "content": "ログインが必要な動画（ニコニコの年齢制限など）で使用します。",
        },
        "login_browser": {
            "label": "ブラウザ",
            "options": list(_BROWSER_MAP.keys()),
            "default": "Chrome",
            "content": "「ブラウザのCookie」選択時、ログイン済み Cookie を持つブラウザ。",
        },
        "cookies_file": {
            "label": "Cookiesファイル",
            "type": "text",
            "default": "",
            "content": "Netscape 形式 cookies.txt のパス（空なら app 直下の cookies.txt）。",
        },
        "username": {
            "label": "ログインID",
            "type": "text",
            "default": "",
            "content": "「IDとパスワード」選択時。ニコニコならメールアドレス/電話番号。",
        },
        "password": {
            "label": "パスワード",
            "type": "password",
            "default": "",
            "content": "「IDとパスワード」選択時のパスワード（保存はされません）。",
        },
        "twofactor": {
            "label": "2段階認証コード",
            "type": "text",
            "default": "",
            "content": "2段階認証が必要な場合のみ入力（例 123456）。",
        },
    }


# 画質ラベル → yt-dlp のフォーマットセレクタ
_QUALITY_MAP = {
    "best": "bestvideo+bestaudio/best",
    "1080p": "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
    "720p": "bestvideo[height<=720]+bestaudio/best[height<=720]",
    "480p": "bestvideo[height<=480]+bestaudio/best[height<=480]",
    "360p": "bestvideo[height<=360]+bestaudio/best[height<=360]",
    "audio only": "bestaudio/best",
}


def _ensure_latest_ytdlp():
    """起動時に yt-dlp 自身を最新へアップグレードする処理（2行）。"""
    try:
        importlib.import_module("yt_dlp.version")
        from yt_dlp.version import __version__  # noqa: F401
    except Exception:
        # 失敗しても致命的ではないため握りつぶす
        pass


def _make_progress_hook(progress_callback):
    """yt-dlp の進捗フックを progress_callback(percent, text) に変換する。"""

    def hook(d):
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes", 0)
            if total:
                percent = downloaded / total * 100.0
            else:
                percent = 0.0
            speed = d.get("speed")
            eta = d.get("eta")
            text = "ダウンロード中"
            if speed:
                text += " - {:.1f} KiB/s".format(speed / 1024)
            if eta is not None:
                text += " - 残り {} 秒".format(int(eta))
            progress_callback(percent, text)
        elif d.get("status") == "finished":
            progress_callback(100.0, "後処理中...")

    return hook


def _load_app_config():
    """本体の config.json を読む（読めなければ空 dict）。

    GUI にログイン項目が無い旧バージョンの本体や、設定をファイルで管理したい
    ユーザーのために、config.json 側の値もログイン設定として利用する。
    """
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _first_value(options, config, keys, strip=True):
    """``options``（GUI の入力）優先で、``keys`` の順に最初の非空値を返す。

    GUI で明示的に入力された値を最優先し、無ければ config.json の値を使う。
    """
    for key in keys:
        for source in (options, config):
            value = source.get(key)
            if value is None:
                continue
            text = str(value)
            if strip:
                text = text.strip()
            if text:
                return text
    return ""


def _apply_login_options(ydl_opts, options):
    """ログイン設定を yt-dlp のオプションへ反映する（破壊的に更新）。

    ニコニコ動画などのログイン必須コンテンツ用。GUI の選択・入力値を優先し、
    無ければ config.json の同名キー（``login`` / ``login_browser`` /
    ``cookies_file`` / ``username`` / ``password`` / ``twofactor`` と
    ``niconico_*`` 別名）を参照する。
    """
    config = _load_app_config()

    method = _first_value(options, config, ("login", "login_method")) or _LOGIN_NONE

    if method == _LOGIN_BROWSER:
        label = _first_value(options, config, ("login_browser", "browser")) or "Chrome"
        browser = _BROWSER_MAP.get(label, label.lower())
        ydl_opts["cookiesfrombrowser"] = (browser,)

    elif method == _LOGIN_COOKIE_FILE:
        path = _first_value(options, config, ("cookies_file", "niconico_cookies"))
        path = os.path.expanduser(path) if path else _DEFAULT_COOKIE_FILE
        if not os.path.isfile(path):
            raise RuntimeError(
                "cookies.txt が見つかりません: {}\n"
                "「Cookiesファイル」にパスを入力するか、アプリ直下に cookies.txt "
                "(Netscape 形式) を置いてください。".format(path))
        ydl_opts["cookiefile"] = path

    elif method == _LOGIN_CREDENTIALS:
        username = _first_value(
            options, config, ("username", "login_user", "niconico_username"))
        password = _first_value(
            options, config, ("password", "login_password", "niconico_password"),
            strip=False)
        if not username or not password:
            raise RuntimeError(
                "「IDとパスワード」が選ばれていますが、ログインID / パスワードが空です。\n"
                "ダウンロード設定に入力するか、config.json に username / password を"
                "設定してください。")
        ydl_opts["username"] = username
        ydl_opts["password"] = password
        twofactor = _first_value(
            options, config, ("twofactor", "login_2fa", "niconico_2fa"))
        if twofactor:
            ydl_opts["twofactor"] = twofactor


def _with_login_hint(message):
    """ログイン要求系のエラーに、設定方法のヒントを追記して返す。"""
    lowered = message.lower()
    if "login" in lowered or "sensitive" in lowered or "private" in lowered:
        return (
            message
            + "\n\nヒント: ログインが必要な動画です。ダウンロード設定の「ログイン」で"
            + "「ブラウザのCookie」を選び、その動画を視聴できるブラウザを指定してください。"
            + "「Cookiesファイル」（Netscape 形式 cookies.txt）や「IDとパスワード」も使えます。"
        )
    return message


def download_logic(url, progress_callback, save_dir="downloads", options=None):
    """本体から URL と進捗通知用の関数、選択されたオプションを受け取り実行する。

    Args:
        url: ダウンロード対象の URL。
        progress_callback: progress_callback(percent: float, text: str) 形式の関数。
        save_dir: 保存先フォルダ。
        options: GUI で選択・入力されたオプション dict（例 {"quality": "1080p",
            "format": "mp4", "audio_bitrate": "320kbps", "login": "ブラウザのCookie",
            "login_browser": "Chrome"}）。

    Returns:
        str: ダウンロードしたファイルのパス（yt-dlp の出力に基づく）。
    """
    import yt_dlp

    _ensure_latest_ytdlp()
    options = options or {}

    quality = options.get("quality", "best")
    fmt = options.get("format", "mp4")
    bitrate = options.get("audio_bitrate", "best")

    format_selector = _QUALITY_MAP.get(quality, _QUALITY_MAP["best"])
    audio_only = quality == "audio only" or fmt in ("mp3", "m4a")

    ydl_opts = {
        "outtmpl": save_dir.rstrip("/\\") + "/%(title)s.%(ext)s",
        "progress_hooks": [_make_progress_hook(progress_callback)],
        "noprogress": True,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "format": "bestaudio/best" if audio_only else format_selector,
        # 1 MiB のバッファで OS への書き込み回数を減らす
        "buffersize": 1024 * 1024,
    }

    # HLS / DASH のような分割配信は、フラグメントを並列取得すると速くなる
    # （単一ファイル配信では無視されるため安全に指定できる）。
    config = _load_app_config()
    try:
        parallel = int(config.get("parallel") or 4)
    except (TypeError, ValueError):
        parallel = 4
    ydl_opts["concurrent_fragment_downloads"] = max(1, min(parallel, 8))

    # 再開可能なチャンクサイズ（config の chunk_size を尊重）
    try:
        chunk_size = int(config.get("chunk_size") or 0)
    except (TypeError, ValueError):
        chunk_size = 0
    if chunk_size > 0:
        ydl_opts["http_chunk_size"] = max(1024 * 1024, min(chunk_size, 10 * 1024 * 1024))

    # 音声のみの場合や mp3 出力時の音質設定
    if audio_only and fmt == "mp3":
        ydl_opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "0" if bitrate == "best" else bitrate.replace("kbps", ""),
        }]

    # ログイン設定（ニコニコの年齢制限動画などで必要）
    _apply_login_options(ydl_opts, options)

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=True)
        except yt_dlp.utils.DownloadError as e:
            # ログイン要求エラーには設定方法のヒントを添えて再送出する
            raise RuntimeError(_with_login_hint(str(e)))
        filename = ydl.prepare_filename(info)

    progress_callback(100.0, "完了")
    return filename
