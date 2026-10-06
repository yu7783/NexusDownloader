# How to develop a Plugin — プラグインの作り方

プラグインは、司令塔コア（`Core`）の機能を拡張するためのモジュールです。
アドオンが「ダウンロード処理」を追加するのに対し、プラグインは
**ログ出力・統計収集・UI通知フック** など、アプリ全体の振る舞いを拡張します。

プラグインも **1ファイル（`.py`）** で `plugins/` フォルダに配置するだけです。

---

## 1. 最小構成

プラグインのファイルは次の2つを満たす必要があります。

1. 先頭に **マニフェストコメント** を書く
2. モジュールレベルの `register(core)` 関数を定義する（読み込み時に呼ばれる）

```python
# --- PLUGIN_MANIFEST ---
# ID: my_plugin
# NAME: My Plugin
# VERSION: 1.0.0
# ------------------------

class MyPlugin:
    def __init__(self):
        ...
    def log(self, message, level="INFO"):
        ...


def register(core):
    """読み込み時に呼ばれるフック。core にインスタンスを登録する。"""
    core.register_plugin("my_plugin", MyPlugin())
```

これを `plugins/my_plugin.py` として保存し、アプリを再起動すれば自動的に読み込まれます。

---

## 2. マニフェストコメントの仕様

プラグインにも、アドオンと **同じ形式** のマニフェストコメントを付けられます。
（`# --- PLUGIN_MANIFEST ---` / `# --- ADDON_MANIFEST ---` のどちらでも解析されます）

```
# --- PLUGIN_MANIFEST ---
# ID: <識別子>
# NAME: <表示名>
# VERSION: <バージョン>
# ------------------------
```

| フィールド | 必須 | 説明 |
|---|---|---|
| `ID` | 任意 | 識別子（プラグインのロード自体には必須ではありません） |
| `NAME` | 任意 | 表示名 |
| `VERSION` | 任意 | バージョン |

> 💡 アドオンと違い、プラグインのロードはマニフェストの有無に関わらず行われます。
> `register(core)` さえ定義されていれば動作します。マニフェストは識別用のメタ情報です。

---

## 3. ライフサイクル

プラグインは以下の順序で扱われます（`Core.load_plugins()`）。

```
[1] plugins/ 内の *.py をスキャン
      │  ※ ファイル名が "_" で始まるものはスキップ
      ▼
[2] SecurityGuard によるコードスキャン
      │  ✗ 危険キーワード検出 → 読み込み拒否（ログ出力、実行しない）
      ▼
[3] importlib による動的ロード（exec_module）
      │
      ▼
[4] モジュールに register があれば register(core) を呼ぶ
      │  ※ ここでインスタンス生成 & core.register_plugin() する
      ▼
[5] 以降、Core からプラグインのメソッドが呼ばれる
        例: Core.log() → logger プラグインの log() を呼び出す
```

### 重要ポイント

- **GUI 起動前にロードされる**: `main.main()` で `core.load_plugins()` が
  `core.load_addons()` より先に呼ばれます。これにより、アドオン読み込み時のログも残せます。
- **register(core) の中では重い処理をしない**: 起動をブロックします。
- **例外は握りつぶされる**: `register` 内で例外が出てもアプリは落ちませんが、
  ログに `[ERROR] プラグイン登録に失敗: ...` が出力されます。

---

## 4. Core（司令塔）がプラグインに提供する API

`register(core)` で受け取る `core` から、以下の API が利用できます。

### プラグイン登録

```python
core.register_plugin(name: str, instance) -> None
```

`instance` を名前で登録します。登録後、一部の名前はコアから自動的に呼ばれます。

| 登録名 | コアからの呼び出し |
|---|---|
| `"logger"` | `Core.log(message, level)` が `instance.log(message, level)` を呼ぶ |

### ログ

```python
core.log(message: str, level: str = "INFO") -> None
```

- `"logger"` プラグインが登録されていれば、その `log()` に転送されます。
- 未登録の場合は標準出力に `[LEVEL] message` を出力します。

### 設定

```python
core.config                      # dict。現在の設定値
core.save_config()               # -> (ok: bool, message: str)
core.update_config(**kwargs)     # 値を更新して保存 -> (ok: bool, message: str)
core.download_dir()              # 保存先フォルダの絶対パス
```

### アドオン情報

```python
core.addons                      # dict: ID -> {"manifest", "module", "path", "name", "enabled"}
core.find_addon_for_url(url)     # -> entry dict or None
```

### パフォーマンス

```python
core.speed_test(timeout=6)          # -> dict(speed_kib, parallel, chunk_size, message)
core.optimize_parallel(apply=True)  # -> (ok, message)
```

### シグナル（PyQt6）

`Core` は `QObject` なので、プラグインからシグナルを購読できます。

| シグナル | 引数 | タイミング |
|---|---|---|
| `core.addon_loaded` | `dict` | アドオンが1件ロードされた時 |
| `core.addon_rejected` | `str, str` | セキュリティでアドオンが拒否された時 (name, reason) |
| `core.update_checked` | `str` | 自動更新チェックが完了した時 (message) |

```python
core.addon_loaded.connect(lambda entry: core.log("loaded: " + entry["name"]))
```
---

## 5. サンプルコード

### 5.1 同梱の logger プラグイン（実例）

`plugins/logger.py` の全文です。`register(core)` で `"logger"` 名に登録することで、
コアの `log()` 呼び出しを受け取ります。

```python
# --- PLUGIN_MANIFEST ---
# ID: logger_plugin
# NAME: Simple Logger Plugin
# VERSION: 1.0.0
# ------------------------

import datetime
import os


class LoggerPlugin:
    """本体から呼び出されるシンプルなログ出力プラグイン。"""

    def __init__(self, log_dir="logs"):
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)
        self.log_path = os.path.join(log_dir, "downloader.log")

    def log(self, message, level="INFO"):
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = "[{}] [{}] {}\n".format(timestamp, level, message)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(line)
        return line.strip()


def register(core):
    """プラグイン登録用フック。core にインスタンスを登録する。"""
    core.register_plugin("logger", LoggerPlugin())
```

### 5.2 統計収集プラグイン（シグナル購読の例）

`addon_loaded` シグナルを購読し、ロードされたアドオン数を数える例です。

```python
# --- PLUGIN_MANIFEST ---
# ID: stats_plugin
# NAME: Load Statistics Plugin
# VERSION: 1.0.0
# ------------------------

class StatsPlugin:
    """アドオンのロード状況を集計するプラグイン。"""

    def __init__(self):
        self.loaded_count = 0
        self.rejected_count = 0

    def on_addon_loaded(self, entry):
        self.loaded_count += 1

    def on_addon_rejected(self, name, reason):
        self.rejected_count += 1


def register(core):
    plugin = StatsPlugin()
    core.register_plugin("stats", plugin)

    # シグナルを購読
    core.addon_loaded.connect(plugin.on_addon_loaded)
    core.addon_rejected.connect(plugin.on_addon_rejected)

    core.log("stats plugin loaded")
```

### 5.3 保存先フォルダを分類するプラグイン

`core.download_dir()` と `core.config` を使う例です。

```python
# --- PLUGIN_MANIFEST ---
# ID: organize_plugin
# NAME: Auto Organize Plugin
# VERSION: 1.0.0
# ------------------------

import os


class OrganizePlugin:
    CATEGORIES = {
        "videos": (".mp4", ".mkv", ".webm"),
        "audio": (".mp3", ".m4a", ".wav"),
        "images": (".jpg", ".png", ".gif"),
    }

    def __init__(self, core):
        self.core = core

    def category_for(self, filename):
        ext = os.path.splitext(filename)[1].lower()
        for name, exts in self.CATEGORIES.items():
            if ext in exts:
                return name
        return "others"


def register(core):
    core.register_plugin("organize", OrganizePlugin(core))
    core.log("organize plugin loaded")
```

---

## 6. セキュリティ・ガードマン

プラグインもアドオンと同様、読み込み前にコードスキャンされます。
`os.remove` / `shutil.rmtree` / `subprocess` / `os.system` を含むと読み込みが拒否されます。

> ⚠️ ファイル整理など「削除」を行いたい場合は、これらの文字列を直接使わない実装
> （例: 別プロセスに委譲、専用ライブラリ使用）を検討するか、利用者に
> ブロックキーワードの変更を依頼してください。

---

## 7. ベストプラクティス

- `register(core)` では **インスタンス生成とシグナル接続だけ** を行い、重い処理は避ける。
- コアの設定を変更した場合は `core.update_config(...)` を使って永続化する。
- ログは `core.log(...)` を通すことで、logger プラグインと連携できる。
- 例外を自分で握りつぶさず、必要なら `core.log(..., level="ERROR")` で記録する。

---

## 8. チェックリスト

- [ ] 先頭に `# --- PLUGIN_MANIFEST ---` ブロックがある
- [ ] モジュールレベルに `register(core)` を定義した
- [ ] `register` 内で `core.register_plugin(name, instance)` を呼んだ
- [ ] `register` 内で重い処理をしていない
- [ ] ブラックリスト文字列を含んでいない
- [ ] 必要に応じてシグナル（`addon_loaded` 等）を購読した