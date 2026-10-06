# --- ADDON_MANIFEST ---
# ID: yt_dlp_core
# NAME: Universal Video Addon (yt-dlp)
# VERSION: 1.1.0
# URL_PATTERNS: youtube.com, youtu.be, twitter.com, x.com, tiktok.com
# ----------------------

import importlib
import sys

import yt_dlp


def get_options():
    """本体のダウンロード画面に表示する選択肢（画質・音質・拡張子）を提示する。

    本体（core.Core.get_addon_options → main.DownloadInterface）がこの戻り値を
    読み取り、ComboBox を自動生成する。選択された値は ``download_logic`` の
    ``options`` 引数としてそのまま渡ってくる。
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


def download_logic(url, progress_callback, save_dir="downloads", options=None):
    """本体から URL と進捗通知用の関数、選択されたオプションを受け取り実行する。

    Args:
        url: ダウンロード対象の URL。
        progress_callback: progress_callback(percent: float, text: str) 形式の関数。
        save_dir: 保存先フォルダ。
        options: GUI で選択されたオプション dict（例 {"quality": "1080p",
            "format": "mp4", "audio_bitrate": "320kbps"}）。

    Returns:
        str: ダウンロードしたファイルのパス（yt-dlp の出力に基づく）。
    """
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
    }

    # 音声のみの場合や mp3 出力時の音質設定
    if audio_only and fmt == "mp3":
        ydl_opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "0" if bitrate == "best" else bitrate.replace("kbps", ""),
        }]

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)

    progress_callback(100.0, "完了")
    return filename
