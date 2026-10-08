# How to develop an Addon — アドオンの作り方

アドオンは **1ファイル（`.py`）** だけで作れます。`addons/` フォルダに配置するだけで、
本体（exe）を再ビルドせずに新しいダウンロードサイトへ対応できます。

---

## 1. 最小構成

アドオンのファイルは次の2つを満たす必要があります。

1. 先頭に **マニフェストコメント** を書く
2. `download_logic()` 関数を定義する

```python
# --- ADDON_MANIFEST ---
# ID: my_site
# NAME: My Site Addon
# VERSION: 1.0.0
# URL_PATTERNS: example.com, example.org
# REPO: https://raw.githubusercontent.com/<user>/<repo>/main
# ----------------------

def download_logic(url, progress_callback, save_dir="downloads"):
    # ダウンロード処理をここに書く
    ...
    return "保存したファイルのパス"
```

これを `addons/my_site_addon.py` として保存し、アプリを再起動（またはアドオン管理タブで再読込）すれば自動的に読み込まれます。

---

## 2. マニフェストコメントの仕様

ファイル先頭に、以下のブロックを **コメント（`#`）として** 記述します。
`core.read_manifest()` がこの部分を正規表現で解析し、標識として登録します。

```
# --- ADDON_MANIFEST ---
# ID: <識別子>
# NAME: <表示名>
# VERSION: <バージョン>
# URL_PATTERNS: <パターン1>, <パターン2>, ...
# REPO: <このアドオンの更新元 raw ベース URL>
# ----------------------
```

### 各フィールド

| フィールド | 必須 | 説明 |
|---|---|---|
| `ID` | ✅ **必須** | アドオンを一意に識別する文字列。これが無いと **読み込まれません** |
| `NAME` | 任意 | アドオン管理画面に表示される名前（無い場合は `ID` が使われる） |
| `VERSION` | 任意 | バージョン。自動更新の新旧比較に使用（例 `1.0.2`） |
| `URL_PATTERNS` | 任意 | 対応 URL の判定パターン。カンマ区切り。部分一致で判定 |
| `REPO` | 任意 | **このアドオン自身の更新元**（raw ベース URL。`github.com/.../tree/<branch>` の HTML URL も自動変換）。書くと自動更新の対象になる（後述） |

### パーサの規則（厳密仕様）

- **開始マーカー**: `#` ＋ ハイフン2個以上 ＋ `ADDON_MANIFEST`（または `PLUGIN_MANIFEST`）＋ ハイフン2個以上
  - 実際の正規表現: `#\s*-{2,}\s*(?:ADDON|PLUGIN)_MANIFEST\s*-{2,}`（大文字小文字は区別しない）
- **終了**: 開始マーカー以降で最初に現れる `----` までをブロックとして扱う
- **フィールド行**: `# KEY: value` の形式。`KEY` は `ID` / `NAME` / `VERSION` / `URL_PATTERNS` / `REPO`（大文字小文字不問）
  - 正規表現: `^\s*#\s*(ID|NAME|VERSION|URL_PATTERNS|REPO)\s*:\s*(.+?)\s*$`
- `ID` が無い場合はアドオンとして登録されません。

### 自動更新（`REPO`）のルール

`REPO` を書くと、本体は起動時に **そのアドオン専用の更新元** を見て自動更新します。
アドオンごとに別リポジトリを持てます（同じリポジトリを共有しても構いません）。

1. `<REPO>/manifest.json`（`config.json` の `update_manifest` 名）を取得する
2. `id` が `ID` と一致するエントリ（無ければ `file` の一致）を探す
3. リモートの `version` が、手元の `VERSION` より新しければ
   `<REPO>/<path>`（`path` 省略時は `addons/<file>`）を取得する
4. **セキュリティスキャン** を通してから `addons/` のファイルを上書きする

```json
{
  "addons": [
    {
      "id": "my_site",
      "file": "my_site_addon.py",
      "version": "1.1.0",
      "path": "addons/my_site_addon.py"
    }
  ]
}
```

| ポイント | 内容 |
|---|---|
| `REPO` を書かない場合 | 自動更新の対象外（手動更新のみ） |
| `VERSION` は必須級 | 無いと `0.0.0` 扱いになるため、必ず更新のたびに上げる |
| 更新ファイルもスキャンされる | `os.remove` 等のブロック語を含めると更新が拒否される |
| アドオン管理タブから URL で追加した場合 | 追加元 URL が `REPO` として自動で追記される |

### URL 判定（`URL_PATTERNS`）のルール

`Core.find_addon_for_url(url)` が、入力 URL に対して各パターンを **部分一致（小文字比較）** で照合します。
複数のアドオンが一致した場合は、**より長いパターンで一致したものが優先**されます。

```
URL_PATTERNS: youtube.com, youtu.be
```

| 入力 URL | 一致 |
|---|---|
| `https://www.youtube.com/watch?v=xxx` | ✅ `youtube.com` |
| `https://youtu.be/xxx` | ✅ `youtu.be` |
| `https://vimeo.com/xxx` | ❌ 一致なし |

> 💡 汎用的な短いパターン（例 `com`）は誤マッチの原因になります。ドメイン固有の文字列を指定してください。

---

## 3. `download_logic()` のシグネチャ

本体（`main.DownloadWorker`）から、この関数が **別スレッドで** 呼び出されます。

```python
def download_logic(url, progress_callback, save_dir="downloads", options=None):
    ...
```

### 引数

| 引数 | 型 | 説明 |
|---|---|---|
| `url` | `str` | ユーザーが入力した URL |
| `progress_callback` | `Callable[[float, str], None]` | 進捗通知関数。`(percent, text)` を渡して呼ぶ |
| `save_dir` | `str` | 保存先フォルダの絶対パス（本体が `Core.download_dir()` を解決して渡す） |
| `options` | `dict` | **任意**。GUI で選択されたオプション（後述の `get_options()` に対応）。未選択時は既定値の dict |

> 🔁 **後方互換**: `options` を定義しない旧式（`def download_logic(url, progress_callback, save_dir="downloads")`）でも
> そのまま動作します。本体がシグネチャを検査し、`options` を受け取れるアドオンにだけ渡します。

### 戻り値

| 型 | 説明 |
|---|---|
| `str` | ダウンロードしたファイルのパスなど、完了メッセージとして表示する文字列 |

> ⚠️ GUI に直接アクセスしないでください。進捗は必ず `progress_callback` 経由で通知します。
> 別スレッド実行のため、Qt ウィジェットを直接操作するとクラッシュします。

### `progress_callback` の仕様

```python
progress_callback(percent, text)
```

| 引数 | 型 | 説明 |
|---|---|---|
| `percent` | `float` | 進捗率（0.0〜100.0）。`ProgressBar` に反映される |
| `text` | `str` | ステータス欄に表示するテキスト（速度・残り時間など） |

本体側では受信した値で `ProgressBar.setValue()` と `status.setText()` を更新します。

---

## 3.5 選択肢を提示する `get_options()`（画質・音質・拡張子など）

アドオンは **任意で** `get_options()` を定義できます。定義すると、本体の
ダウンロード画面に **選択 UI（コンボボックス）が自動生成** され、ユーザーが選んだ値が
`download_logic(..., options=...)` に渡されます。

```python
def get_options():
    return {
        "quality": {
            "label": "画質",
            "options": ["best", "1080p", "720p", "480p"],
            "default": "1080p",
            "content": "解像度を選択してください",   # 任意（ツールチップ）
        },
        "format": {
            "label": "拡張子",
            "options": ["mp4", "webm", "mp3"],
            "default": "mp4",
        },
        "audio_bitrate": {
            "label": "音質",
            "options": ["best", "320kbps", "192kbps"],
            "default": "best",
        },
    }
```

### 戻り値の仕様

`dict[str, dict]` を返します。**外側のキー** が `options` dict のキー名になります。

| フィールド | 必須 | 説明 |
|---|---|---|
| `label` | 任意 | 画面に表示する見出し（無ければキー名を使用） |
| `options` | ✅ **必須** | 選択肢の文字列リスト。空/非リストの項目は無視される |
| `default` | 任意 | 初期選択値。`options` に無い値なら先頭が使われる |
| `content` | 任意 | 補足説明（コンボボックスのツールチップになる） |

### 受け取り方

`get_options()` で定義したキーがそのまま `download_logic` の `options` に渡ります。

```python
def download_logic(url, progress_callback, save_dir="downloads", options=None):
    options = options or {}
    quality = options.get("quality", "1080p")
    fmt = options.get("format", "mp4")
    bitrate = options.get("audio_bitrate", "best")
    # ↑ 選択値を使ってダウンロード処理を分岐する
    ...
```

### GUI での挙動

- URL を入力すると、対応するアドオンが判定され、`get_options()` があれば
  **「ダウンロード設定」カードに選択 UI が出現** します。
- アドオンが切り替わると選択 UI も切り替わり、`get_options()` を持たない
  アドオンでは非表示になります（後方互換）。
- `get_options()` が例外を出す / 不正な形式を返す場合は、安全に無視されます。
---

## 4. サンプルコード

### 4.1 シンプルな HTTP ダウンロード

```python
# --- ADDON_MANIFEST ---
# ID: direct_http
# NAME: Direct HTTP Addon
# VERSION: 1.0.0
# URL_PATTERNS: example.com
# ----------------------

import os
import requests


def download_logic(url, progress_callback, save_dir="downloads"):
    """requests でストリーミングダウンロードし、進捗を通知する。"""
    progress_callback(0.0, "接続中...")
    resp = requests.get(url, stream=True, timeout=30)
    resp.raise_for_status()

    total = int(resp.headers.get("Content-Length", 0))
    filename = url.split("/")[-1] or "downloaded.bin"
    dest = os.path.join(save_dir, filename)

    downloaded = 0
    with open(dest, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            if not chunk:
                continue
            f.write(chunk)
            downloaded += len(chunk)
            percent = downloaded / total * 100.0 if total else 0.0
            progress_callback(percent, "ダウンロード中 {} バイト".format(downloaded))

    progress_callback(100.0, "完了")
    return dest
```

### 4.2 同梱の yt-dlp アドオン（実例）

`addons/yt_dlp_addon.py` の要点です。複雑なライブラリでも同じ規格で書けます。

```python
# --- ADDON_MANIFEST ---
# ID: yt_dlp_core
# NAME: Universal Video Addon (yt-dlp)
# VERSION: 1.0.2
# URL_PATTERNS: youtube.com, youtu.be, twitter.com, x.com, tiktok.com
# REPO: https://raw.githubusercontent.com/<user>/NexusDownloader-Addons/main
# ----------------------

import yt_dlp


def _make_progress_hook(progress_callback):
    """yt-dlp の progress_hooks を progress_callback(percent, text) に変換する。"""
    def hook(d):
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes", 0)
            percent = downloaded / total * 100.0 if total else 0.0
            progress_callback(percent, "ダウンロード中")
        elif d.get("status") == "finished":
            progress_callback(100.0, "後処理中...")
    return hook


def download_logic(url, progress_callback, save_dir="downloads"):
    ydl_opts = {
        "outtmpl": save_dir.rstrip("/\\") + "/%(title)s.%(ext)s",
        "progress_hooks": [_make_progress_hook(progress_callback)],
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)
    progress_callback(100.0, "完了")
    return filename
```

### 4.3 選択肢（画質・拡張子）を提示する実例

`get_options()` で選択肢を返し、`options` を受け取って分岐するパターンです。

```python
# --- ADDON_MANIFEST ---
# ID: pic_site
# NAME: Picture Site Addon
# VERSION: 1.0.0
# URL_PATTERNS: pics.example.com
# REPO: https://raw.githubusercontent.com/<user>/pic-site-addon/main
# ----------------------

import os
import requests

_FORMAT_MAP = {
    "format": {"label": "拡張子", "options": ["jpg", "png", "webp"], "default": "jpg"},
    "size": {"label": "サイズ", "options": ["original", "large", "medium"],
             "default": "original", "content": "取得する画像サイズ"},
}


def get_options():
    # 外側のキーが options dict のキーになる
    return _FORMAT_MAP


def download_logic(url, progress_callback, save_dir="downloads", options=None):
    options = options or {}
    ext = options.get("format", "jpg")
    size = options.get("size", "original")

    progress_callback(0.0, "接続中... ({} / {})".format(ext, size))
    resp = requests.get(url, stream=True, timeout=30)
    resp.raise_for_status()

    total = int(resp.headers.get("Content-Length", 0))
    filename = (url.split("/")[-1] or "image") + "." + ext
    dest = os.path.join(save_dir, filename)

    downloaded = 0
    with open(dest, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            if not chunk:
                continue
            f.write(chunk)
            downloaded += len(chunk)
            percent = downloaded / total * 100.0 if total else 0.0
            progress_callback(percent, "ダウンロード中")

    progress_callback(100.0, "完了")
    return dest
```

---

## 5. セキュリティ・ガードマン（重要）

アドオンは **実行される前に** テキストスキャンされます。以下の文字列が含まれていると
**実行を完全にブロック** され、「インストール拒否」の警告が出ます（既定のブラックリスト）。

| ブロック対象 | 用途 |
|---|---|
| `os.remove` | ファイル削除 |
| `shutil.rmtree` | フォルダ削除 |
| `subprocess` | 外部コマンド実行 |
| `os.system` | 外部コマンド実行 |

このリストは 設定タブ の「ブロックする危険キーワード」から `config.json` の
`blocked_keywords` を編集して変更できます。

> ⚠️ 危険な API を使う正当な理由がある場合でも、これらの文字列は避けてください
> （誤検知で弾かれます）。どうしても必要な場合は利用者にキーワード変更を依頼してください。

---

## 6. 動作確認とデバッグ

- 配置後、アプリの **アドオン管理タブ** に名前・バージョンが表示されれば成功です。
- ロードに失敗した場合はコンソールに `[ERROR] モジュールのロードに失敗: ...` が出ます。
- ロード状況は `plugins/logger.py` により `logs/downloader.log` に記録されます。
- 単体で動作確認する場合:

```python
import importlib.util
spec = importlib.util.spec_from_file_location("test", "addons/my_site_addon.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

def cb(percent, text):
    print(percent, text)

mod.download_logic("https://example.com/file.bin", cb, "downloads")
```

---

## 7. チェックリスト

- [ ] 先頭に `# --- ADDON_MANIFEST ---` ブロックがある
- [ ] `ID` を記入した（必須）
- [ ] `URL_PATTERNS` にドメイン固有の文字列を指定した
- [ ] （任意）`REPO` に配布リポジトリの raw ベース URL を書いた（自動更新を使う場合）
- [ ] `download_logic(url, progress_callback, save_dir, options=None)` を定義した
- [ ] （任意）画質・音質・拡張子などの選択肢を `get_options()` で提示した
- [ ] 進捗は `progress_callback(percent, text)` で通知している
- [ ] GUI に直接アクセスしていない
- [ ] ブラックリスト文字列を含んでいない
- [ ] 戻り値はファイルパスなどの文字列