# Nexus Downloader

本体（exe）を一切汚さず、外部の `.py` ファイルを配置するだけで機能を拡張できる、**汎用アドオン型ダウンローダー**です。

PyQt6 + PyQt-Fluent-Widgets による Windows 11 純正風のモダンな UI、`importlib` による動的ロード、危険コードの自動ブロック、マルチスレッド実行、GitHub からの自動アップデートを備えています。

---

## ✨ 主な特徴

| 機能 | 説明 |
|---|---|
| 4層アーキテクチャ | 本体を再ビルドせず、外部 `.py` を置くだけで拡張 |
| 動的インポート | `addons/` を起動時にスキャンし、マニフェストを自動解析 |
| セキュリティ・ガードマン | 危険なコード（`os.remove` 等）を実行前にブロック |
| マルチスレッド | ダウンロードは別スレッド実行で GUI は絶対にフリーズしない |
| 複数 URL の並列ダウンロード | 改行区切りで複数 URL を貼り付けると、`parallel` 数の並列で同時ダウンロード |
| 自動アップデート | **本体** は `config.json` の repo から、**各アドオン** は自分の `REPO` から個別に取得 |
| アドオン定義の選択 UI | アドオンの `get_options()` が画質・音質・拡張子などの選択肢を提示し、GUI に自動表示 |
| 速度テスト & 並列最適化 | 帯域を実測し最適な並列数・チャンクサイズを自動適用 |
| フル GUI 設定 | 設定タブから `config.json` を動的に編集・保存 |

---

## 🏗️ アーキテクチャ（4層構造）

```
Nexus-Downloader/
 ├── main.py          # 【プレゼンテーション層】GUI (PyQt6/qfluentwidgets) + 起動エントリポイント
 ├── core.py          # 【ロジック層】司令塔コア（動的ロード / 更新 / スレッド / 設定保存）
 ├── security.py      # 【セキュリティ層】危険コードスキャン（SecurityGuard）
 ├── updater.py       # 【更新層】本体の自己更新（ステージング / 適用 / 再起動）
 ├── version.txt      # 本体バージョン（本体の自動更新の比較に使用）
 ├── manifest.json    # 本体リポジトリ用マニフェスト（app セクション）
 ├── config.json      # 設定ファイル（GUI から編集可能）
 ├── addons/          # 【拡張層】外部アドオン（ダウンロードサイト追加 / 更新元は各ファイルの REPO）
 │    └── yt_dlp_addon.py
 └── plugins/         # 【拡張層】その他機能追加
      └── logger.py
```

依存は **一方向** です（`main.py` → `core.py` → `security.py` / `updater.py`）。これにより本体を汚さず拡張でき、Nuitka での C 言語化も容易です。

```
main.py  (GUI + entry)
   │  import                        ┌─ 起動時にステージ済み更新を適用 → 自己再起動
   ▼                                ▼
core.py  (Core: 動的インポート / 更新 / 設定) ── updater.py (ステージング / 適用)  ← 標準ライブラリのみ
   │  import
   ▼
security.py  (SecurityGuard: コードスキャン)   ← 標準ライブラリのみ依存
```

### 各層の役割

- **`main.py`** — 画面構築（ダウンロード / アドオン管理 / 設定 / About の4タブ）、
  `DownloadWorker`（単発）と `MultiDownloadWorker`（複数 URL の並列）によるワーカースレッド、
  起動時の自動更新呼び出し、**`core` を import する前に** ステージ済みの本体更新を適用。
- **`core.py`** — `Core` クラスが司令塔。`addons/` と `plugins/` のスキャン・動的インポート・
  マニフェスト解析・URL 判定・自動更新（本体 + 各アドオン）・設定の保存・速度テストを担当。GUI に依存しない。
- **`security.py`** — アドオン/プラグイン/更新ファイルを実行する直前にテキストを走査し、
  ブラックリスト文字列を検出したら実行をブロックする。
- **`updater.py`** — 本体の自動更新の実体。更新ファイルを `update/` にステージングし、
  次回起動時に適用（バックアップ退避 + `version.txt` 更新）して自己再起動する。標準ライブラリのみ依存。
- **`addons/`・`plugins/`** — 本体外の拡張ポイント。詳細は下記ドキュメント参照。

---

## 📦 インストール

### 必要環境

- Python 3.10 以上（動作確認: 3.10.11）
- Windows 10 / 11

### 依存パッケージ

```bash
pip install PyQt6 PyQt6-Fluent-Widgets requests yt-dlp
```

| パッケージ | 用途 |
|---|---|
| `PyQt6` | GUI フレームワーク |
| `PyQt6-Fluent-Widgets` | Windows 11 風 Fluent UI |
| `requests` | 自動更新・速度テスト |
| `yt-dlp` | 同梱アドオン（`addons/yt_dlp_addon.py`）で使用 |

---

## 🚀 起動方法

プロジェクト直下で実行します（`core.py` / `security.py` を同階層から import するため）。

```bash
python main.py
```

---

## ⚙️ 設定（config.json）

設定タブから GUI で編集でき、「設定を保存」で `config.json` に永続化されます。

| キー | 型 | 説明 |
|---|---|---|
| `github_repo` | string | **本体**リポジトリの raw ベース URL（本体の自動更新元） |
| `update_manifest` | string | 更新マニフェストのファイル名（既定 `manifest.json`。本体と各アドオンで共通） |
| `download_dir` | string | ダウンロード保存先（相対パスはプロジェクト基準） |
| `auto_update` | bool | 起動時の自動更新の有効/無効（本体 + アドオン） |
| `theme` | string | `dark` / `light` |
| `accent_color` | string | アクセントカラー（例 `#0078D4`） |
| `check_interval_hours` | int | 自動更新チェックの間隔（時間。0 で起動時のみ＝毎回起動時にチェック） |
| `last_update_check` | float | 前回の更新チェック時刻（自動記録・編集不要） |
| `parallel` | int | 同時ダウンロード数（複数 URL 並列の最大並列度・上限 16。0 で自動。速度テストで推奨値を算出） |
| `chunk_size` | int | チャンクサイズ（バイト、速度テストで自動調整） |
| `timeout` | int | ネットワーク接続タイムアウト（秒） |
| `proxy` | string | HTTP プロキシ（空で無効） |
| `user_agent` | string | 送信する User-Agent（空で既定値） |
| `blocked_keywords` | array | ブロックする危険キーワードのリスト |

---

## ⚡ 複数の URL を並列ダウンロード

ダウンロードタブの URL 欄は複数行入力に対応しています。**1 行に 1 つの URL** を入力
（改行区切り）して「ダウンロード」を押すと、それらを **同時に並列ダウンロード** します。

```
https://site-a.example/video/1
https://site-b.example/file/2
https://site-c.example/page/3
```

| 項目 | 動作 |
|---|---|
| 並列度 | `config.json` の `parallel`（上限 16）。URL 数がそれ以下なら URL 数分だけ並列 |
| 空行・前後の空白 | 無視されます |
| まったく同じ URL の重複 | 1 件にまとめられます（誤操作による二重取得を防止） |
| 対応アドオンが無い URL | その URL だけスキップし、残りは実行します（結果一覧に ⚠ で表示） |
| いずれか 1 件が失敗 | 他の URL のダウンロードは継続します（最後に成功／失敗件数を表示） |
| オプション（画質・音質など） | 先頭 URL のアドオンの項目が GUI に表示され、先頭 URL に適用。他の URL には各アドオンの既定値が使われます |
| 1 件だけ入力したとき | 従来どおりの単発ダウンロード（結果一覧カードは表示されません） |

進捗バーは「完了件数 + 進行中タスクの進捗」から全体の進捗率を表示し、
結果一覧には URL ごとの状態（待機中 / 進行中 / 完了 / 失敗 / スキップ）が表示されます。

---

## 🔄 自動アップデートの仕組み

更新の窓口は **2 系統** に分かれています。

| 対象 | 更新元 | 指定場所 |
|---|---|---|
| **本体**（main.py / core.py / security.py / updater.py / version.txt） | `config.json` の `github_repo` | 設定タブ or `config.json` |
| **各アドオン** | **そのアドオンのマニフェストに書いた `REPO`** | アドオンの `.py` 冒頭コメント |

### 1. 本体の自動更新

```
<NexusDownloader リポジトリ>/           # ← config.json の github_repo (raw URL)
 ├── manifest.json                      # {"app": {"version": "1.1.0", "files": [...]}}
 ├── main.py
 ├── core.py
 ├── security.py
 ├── updater.py
 └── version.txt
```

`manifest.json` の仕様:

```json
{
  "app": {
    "version": "1.1.0",
    "files": ["main.py", "core.py", "security.py", "updater.py", "version.txt"],
    "notes": "リリースノート（任意）"
  }
}
```

- `version` がローカルの `version.txt` より新しいときだけ更新します（`files` 省略時は既定の 5 ファイル）。
- 取得したファイルは **必ずセキュリティスキャン** を通します（ブロック語を含めば更新を中止）。
- いきなり本体を書き換えず、まず `update/` に **ステージング** します。
- **次回起動時**、`main.py` が `core`/`security` を import する前に適用され、
  旧ファイルは `update/backup/` に退避 → `version.txt` を新バージョンへ更新 →
  `os.execv` で **自分自身を再起動**（新旧バージョンが混在したまま動くのを防止）。
- 安全のため `addons/` `plugins/` `config.json` は更新対象から除外されます
  （マニフェストに書いても拒否されます）。

> ⚠️ Nuitka / PyInstaller で exe 化した場合は `.py` の差し替えが効かないため、
> 本体の自動更新は **スキップ** します（アドオンの自動更新は exe の外側なので有効です）。

### 2. アドオンの自動更新（各アドオンの REPO）

アドオンは **自分の更新元を自分で宣言** します。

```python
# --- ADDON_MANIFEST ---
# ID: yt_dlp_core
# NAME: Universal Video Addon (yt-dlp)
# VERSION: 1.1.0
# URL_PATTERNS: youtube.com, youtu.be
# REPO: https://raw.githubusercontent.com/yu7783/NexusDownloader-Addons/main
# ----------------------
```

`REPO` には **そのアドオンが配布されているリポジトリの raw ベース URL** を書きます
（複数アドオンが同じリポジトリを共有しても、アドオンごとに別リポジトリでも構いません）。
本体は `<REPO>/<update_manifest>` を取得し、`id`（無ければ `file`）が一致するエントリの
`version` をローカルの `VERSION` と比較して、新しければ `<REPO>/<path>`
（`path` 省略時は `addons/<file>`）を取得 → セキュリティスキャン → `addons/` を上書きします。

```
<NexusDownloader-Addons リポジトリ>/     # ← 各アドオンが REPO で指定
 ├── manifest.json
 │     {"addons": [{"id": "yt_dlp_core", "file": "yt_dlp_addon.py",
 │                  "version": "1.2.0", "path": "addons/yt_dlp_addon.py"}]}
 └── addons/
       └── yt_dlp_addon.py
```

| 状況 | 動作 |
|---|---|
| `REPO` が無いアドオン | 自動更新の対象外（`REPO 未設定のためスキップ` と表示） |
| リポジトリに `manifest.json` が無い / 取得失敗 | そのアドオンだけスキップし、他は継続 |
| マニフェストに該当 `id` / `file` が無い | 何もしない（最新扱い） |
| 取得先に危険なコードがあった | セキュリティゲートが拒否（書き込まない） |
| アドオン管理タブから URL で追加したアドオン | 追加元 URL が `REPO` として自動追記され、以降はその repo を見て更新 |

### 3. チェック頻度

- `check_interval_hours = 0` … 起動のたびにチェック（既定は 24 時間）
- `check_interval_hours > 0` … 前回チェック（`last_update_check`）からその時間が経過していればチェック
- アドオン管理タブの「今すぐ更新チェック」で手動実行できます（本体 + 全アドオン）

### 設定例

| キー | 値 |
|---|---|
| `github_repo` | `https://raw.githubusercontent.com/yu7783/NexusDownloader/main` (本体) |
| `update_manifest` | `manifest.json` |
| `auto_update` | `true` |
| `check_interval_hours` | `24` |

> ⚠️ `github_repo` は **必ず `raw.githubusercontent.com`** を指定してください。
> `github.com/<user>/<repo>` では HTML ページが返り、JSON 解析に失敗します。
> またブランチ名（`main` / `master`）まで含めてください。

> 💡 アドオンの更新元は `config.json` ではなく **各アドオンの `REPO`** です。
> 本体リポジトリ内にアドオンを置く運用にしたい場合は、そのアドオンの `REPO` に
> 本体リポジトリの raw URL を書いてください。

---

## 🧱 Nuitka によるネイティブビルド（C 言語化）

PyInstaller のもっさり感を避け、LTO でネイティブバイナリ化します。
`addons/` は exe の外側に残るため、**アドオンの自動更新は 100% 機能します**。

> ⚠️ exe 化すると `.py` の差し替えが効かないため、**本体の自動更新はスキップ** されます。
> 本体を更新する場合は新しい exe を入手してください（アドオンは従来どおり自動更新）。

```bash
nuitka --standalone --onefile \
       --plugin-enable=pyqt6 \
       --disable-console \
       --include-package-data=addons \
       --follow-import-to=addons \
       --lto=yes \
       main.py
```

| オプション | 効果 |
|---|---|
| `--standalone --onefile` | 単一 exe にまとめる |
| `--plugin-enable=pyqt6` | PyQt6 プラグインを有効化 |
| `--disable-console` | コンソールウィンドウ非表示（GUI アプリ） |
| `--include-package-data=addons` | アドオンのデータを同梱 |
| `--follow-import-to=addons` | addons を追跡 |
| `--lto=yes` | リンク時最適化で爆速起動 |

> 💡 `--pgo-enable` を併用すると更に起動が最適化されます（計測用の実行が必要）。

---

## 🧩 アドオン・プラグインを作る

- アドオンの作り方 → [`How to develop addon.md`](How%20to%20develop%20addon.md)
- プラグインの作り方 → [`How to develop plugin.md`](How%20to%20develop%20plugin.md)
- GitHub への公開手順（本体 + アドオン別リポジトリ） → [`PUBLISHING.md`](PUBLISHING.md)

---

## 📄 ライセンス

Apache License 2.0