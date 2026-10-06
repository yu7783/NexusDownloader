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
| 自動アップデート | GitHub リポジトリから最新アドオンを自動取得 |
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
 ├── config.json      # 設定ファイル（GUI から編集可能）
 ├── addons/          # 【拡張層】外部アドオン（ダウンロードサイト追加・自動更新先）
 │    └── yt_dlp_addon.py
 └── plugins/         # 【拡張層】その他機能追加
      └── logger.py
```

依存は **一方向** です（`main.py` → `core.py` → `security.py`）。これにより本体を汚さず拡張でき、Nuitka での C 言語化も容易です。

```
main.py  (GUI + entry)
   │  import
   ▼
core.py  (Core: 動的インポート / 更新 / 設定)
   │  import
   ▼
security.py  (SecurityGuard: コードスキャン)   ← 標準ライブラリのみ依存
```

### 各層の役割

- **`main.py`** — 画面構築（ダウンロード / アドオン管理 / 設定 / About の4タブ）、
  `DownloadWorker` によるワーカースレッド、起動時の自動更新呼び出し。
- **`core.py`** — `Core` クラスが司令塔。`addons/` と `plugins/` のスキャン・動的インポート・
  マニフェスト解析・URL 判定・GitHub 自動更新・設定の保存・速度テストを担当。GUI に依存しない。
- **`security.py`** — アドオン/プラグインを実行する直前にテキストを走査し、
  ブラックリスト文字列を検出したら実行をブロックする。
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
| `github_repo` | string | アドオン配布リポジトリの raw ベース URL |
| `update_manifest` | string | 更新マニフェストのファイル名（既定 `manifest.json`） |
| `download_dir` | string | ダウンロード保存先（相対パスはプロジェクト基準） |
| `auto_update` | bool | 起動時の自動更新の有効/無効 |
| `theme` | string | `dark` / `light` |
| `accent_color` | string | アクセントカラー（例 `#0078D4`） |
| `check_interval_hours` | int | 自動更新チェックの間隔（時間。0 で起動時のみ） |
| `parallel` | int | ダウンロード並列数（0 で自動。速度テストで推奨値を算出） |
| `chunk_size` | int | チャンクサイズ（バイト、速度テストで自動調整） |
| `timeout` | int | ネットワーク接続タイムアウト（秒） |
| `proxy` | string | HTTP プロキシ（空で無効） |
| `user_agent` | string | 送信する User-Agent（空で既定値） |
| `blocked_keywords` | array | ブロックする危険キーワードのリスト |

---

## 🔄 自動アップデートの仕組み

アドオンは **本体と別リポジトリ**（例: `NexusDownloader-Addons`）で配布されます。
本体は `config.json` の `github_repo` が指す **raw コンテンツのベース URL** から
`manifest.json` とアドオンファイルを取得します。リポジトリは以下の構造を想定しています。

```
<github_repo>/
 ├── manifest.json     # {"addons": [{"file": "...", "version": "x.y.z"}, ...]}
 └── addons/
      └── yt_dlp_addon.py
```

起動時に `manifest.json` を取得し、ローカルのバージョンより新しければ
`addons/` 配下のファイルを上書きします。更新ファイルもセキュリティスキャンを通します。

### 設定例（addon を別リポジトリに分離した場合）

| キー | 値 |
|---|---|
| `github_repo` | `https://raw.githubusercontent.com/yu7783/NexusDownloader-Addons/main` |
| `update_manifest` | `manifest.json` |
| `auto_update` | `true` |

> ⚠️ `github_repo` は **必ず `raw.githubusercontent.com`** を指定してください。
> `github.com/<user>/<repo>` では HTML ページが返り、JSON 解析に失敗します。
> またブランチ名（`main` / `master`）まで含めてください。

> 💡 本体リポジトリ内に `manifest.json` と `addons/` を置けば、addon を
> 同一リポジトリで配布することも可能です（その場合は `github_repo` を本体の
> raw URL にするだけです）。

---

## 🧱 Nuitka によるネイティブビルド（C 言語化）

PyInstaller のもっさり感を避け、LTO でネイティブバイナリ化します。
`addons/` は exe の外側に残るため、自動パッチ更新が 100% 機能します。

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