# GitHub 公開手順（Nexus Downloader 本体 + アドオン）

このドキュメントでは、**本体リポジトリ** と **アドオン専用リポジトリ** の 2 つを
GitHub に公開する手順を説明します。

| リポジトリ | 用途 | 例 |
|---|---|---|
| 本体 | アプリ本体（main.py/core.py/security.py/updater.py/version.txt/manifest.json） | `yu7783/NexusDownloader` |
| アドオン | アドオン配布用（アドオンの `REPO` が指す先） + manifest.json | `yu7783/NexusDownloader-Addons` |

> すでにこのフォルダで `git init` / `git add` / ブランチ `main` 作成済みです
> （`.gitignore`・`LICENSE`・`requirements.txt` も作成済み）。
> 未完の手順は「git ユーザー設定」→「コミット」→「GitHub で push」です。

---

## 0. 事前準備

### 0.1 Git のユーザー設定（初回のみ・必須）

現在 **user.name / user.email が未設定** です。GitHub に push する前に設定します。

```powershell
git config --global user.name "yuu"
git config --global user.email "your-email@example.com"
```

（`--global` は全リポジトリ共通。個人用 PC ならこれで OK）

### 0.2 GitHub 側の準備

1. https://github.com にログイン
2. **空の**リポジトリを 2 つ作成（README/LICENSE は追加しない＝空で作る）
   - `NexusDownloader`
   - `NexusDownloader-Addons`
3. 認証方式をいずれか用意
   - **HTTPS + Personal Access Token**（簡単）
   - **SSH キー**（推奨・毎回パス不要）
   - **GitHub CLI (`gh`)**（最速。`gh auth login` でブラウザ認証）

> 💡 GitHub CLI が入っていれば `gh repo create` で作成〜push まで一括できます（後述）。

---

## 1. 本体リポジトリを公開する

作業ディレクトリ（`c:\Users\nnyk0\programings\downloader`）で:

```powershell
cd c:\Users\nnyk0\programings\downloader

# 初回コミット（既に add 済み。内容を確認）
git status

git commit -m "Initial commit: Nexus Downloader (4-layer addon-based downloader)"

# リモート接続（HTTPS の例。SSH なら git@github.com:yu7783/NexusDownloader.git）
git remote add origin https://github.com/yu7783/NexusDownloader.git
git branch -M main
git push -u origin main
```

### 公開前に必ず確認すること

- `.gitignore` により以下は **除外済み**: `build/`, `__pycache__/`, `downloads/`, `logs/`, `addon-repo/`, `addons/momonga_addon.py`
- `git status` の一覧に **意図しないファイルが無いか** 目視確認
  - 特に `config.json`（`github_repo` に個人情報は無いが、設定を公開したくない場合は除外検討）
  - `downloads/` の中身が混ざっていないか
- 巨大ファイル（`build/main.dist` 等）が入っていないか
  - `git ls-files | ForEach-Object { (Get-Item $_).Length } | Measure-Object -Sum`

### GitHub CLI を使う場合（一括）

```powershell
gh auth login
gh repo create yu7783/NexusDownloader --public --source=. --remote=origin --push
```

---

## 2. アドオン専用リポジトリを公開する

アドオン配布用のファイル一式は **`addon-repo/`** に用意済みです
（`manifest.json` / `addons/yt_dlp_addon.py` / `README.md` / `LICENSE` / `.gitignore`、
git init とブランチ `main` も済み）。

```powershell
cd c:\Users\nnyk0\programings\downloader\addon-repo

git status   # 追跡ファイル確認（manifest.json, addons/yt_dlp_addon.py, README.md, LICENSE）

git commit -m "Initial addon release: yt_dlp_addon v1.1.0"

git remote add origin https://github.com/yu7783/NexusDownloader-Addons.git
git branch -M main
git push -u origin main
```

### GitHub CLI を使う場合

```powershell
gh repo create yu7783/NexusDownloader-Addons --public --source=. --remote=origin --push
```

---

## 3. 本体とアドオンリポジトリの接続（重要）

更新の窓口は **2 系統** です。

| 対象 | 更新元 | 設定する場所 |
|---|---|---|
| 本体 | `config.json` の `github_repo` | 本体リポジトリの raw URL |
| 各アドオン | 各アドオンの `# REPO:` | そのアドオンが置かれたリポジトリの raw URL |

**必ず raw コンテンツ URL** を指定してください（`github.com/...` は HTML を返すため JSON 解析に失敗します）。

```jsonc
// config.json（本体の更新元）
{
  "github_repo": "https://raw.githubusercontent.com/yu7783/NexusDownloader/main",
  "update_manifest": "manifest.json",
  "auto_update": true,
  "check_interval_hours": 24
}
```

```python
# addons/yt_dlp_addon.py（このアドオン自身の更新元）
# REPO: https://raw.githubusercontent.com/yu7783/NexusDownloader-Addons/main
```

> これは `config.json` に **設定済み** です。設定タブの GUI からも変更できます。

push 後、次の URL で JSON が取得できれば準備完了:

```
https://raw.githubusercontent.com/yu7783/NexusDownloader/main/manifest.json
https://raw.githubusercontent.com/yu7783/NexusDownloader-Addons/main/manifest.json
```

### 本体リポジトリに必要なファイル

`manifest.json`（バージョンを上げると各クライアントが次回起動時に自動更新）:

```json
{
  "app": {
    "version": "1.1.0",
    "files": ["main.py", "core.py", "security.py", "updater.py", "version.txt"],
    "notes": "v1.1.0: 複数 URL 並列ダウンロード対応"
  }
}
```

- 更新の比較に使うのは **`version.txt` の中身** です。本体を更新したら
  `version.txt` と `manifest.json` の `version` を **同じ値に** 上げてください。
- クライアント側は `update/` にステージング → **次回起動時** に適用 → 自己再起動します。

### アドオンリポジトリに必要なファイル

```
<NexusDownloader-Addons>/
 ├── manifest.json     # {"addons": [{"id": ..., "file": ..., "version": ..., "path": ...}]}
 └── addons/
       └── yt_dlp_addon.py
```

```json
{
  "addons": [
    {
      "id": "yt_dlp_core",
      "file": "yt_dlp_addon.py",
      "version": "1.2.0",
      "path": "addons/yt_dlp_addon.py"
    }
  ]
}
```

- `id` はアドオン側の `# ID:` と一致させます（一致しなければ `file` で照合）。
- `path` 省略時は `addons/<file>` が使われます。
- アドオンを更新したら **アドオン内の `# VERSION:`** と `manifest.json` の `version` を上げて push。

---

## 4. 動作確認

1. 本体を起動（`python main.py`）
2. 起動時に自動更新が走り、コンソール／`logs/downloader.log` に結果が出る
   （例: `本体: 本体は最新版です (v1.0.0) / アドオン: 最新版です`）
3. 本体を更新した場合は、`update/` にステージングされ次回起動時に適用される
   （適用ログ: `[UPDATER] 本体を v1.1.0 に更新しました (...)`）
4. アドオンを更新したら、アドオン内の `# VERSION:` と `manifest.json` の `version` を
   上げて push → 次回起動時に本体が自動で上書き

> ⚠️ 更新ファイルは実行前に本体の **セキュリティスキャン** を通ります。
> `os.remove` / `subprocess` 等のブロック語を含めないでください。

---

## 5. 更新を配布するフロー（今後の運用）

### 5.1 アドオンを更新する

```powershell
# 1) アドオンを編集（addon-repo/addons/*.py）— マニフェストの # VERSION: も上げる
# 2) manifest.json の該当 version を同じ値に上げる
# 3) コミット & push
cd c:\Users\nnyk0\programings\downloader\addon-repo
git add -A
git commit -m "Update yt_dlp_addon to v1.2.0"
git push
```

アドオンを別リポジトリに分けた場合は、そのアドオンのファイルの `# REPO:` を
**そのリポジトリの raw URL** に書き換えてください（本体側の `config.json` は触りません）。

### 5.2 本体を更新する

```powershell
cd c:\Users\nnyk0\programings\downloader
# 1) main.py / core.py / security.py / updater.py 等を編集
# 2) version.txt と manifest.json の "app".version を同じ値に上げる（例 1.1.0）
# 3) コミット & push
git add -A
git commit -m "Release v1.1.0"
git push
```

クライアントは次回起動時に本体を自動更新し、`os.execv` で再起動して新バージョンで動作します。

---

## 6. よくあるトラブル

| 症状 | 原因 / 対処 |
|---|---|
| `updates were rejected` | リモートに既にコミットがある。`git pull --rebase origin main` してから push |
| 更新マニフェストの取得に失敗 | URL が `github.com` になっている。`raw.githubusercontent.com` に変更 |
| 404 | ブランチ名不一致（`main` / `master`）。URL のブランチ部分を実際に合わせる |
| push 時に認証エラー | PAT or SSH 未設定。`gh auth login` か `git config` で設定 |
| 巨大ファイルで push 遅い | `.gitignore` 漏れ。`build/` 等を除外し `git rm -r --cached build` |
| 本体が何度も更新される | `version.txt` と `manifest.json` の `version` が不一致。同じ値に揃える |
| アドオンが更新されない | アドオンに `# REPO:` が無い（`REPO 未設定のためスキップ` と表示される） |
| `ビルド済み版ではファイル差し替え更新は利用できません` | exe 版は本体の自動更新対象外。新しい exe を配布する |

---

## 7. チェックリスト

- [ ] `git config --global user.name/user.email` を設定した
- [ ] GitHub に空リポジトリを 2 つ作成した
- [ ] 本体: `git status` に不要ファイル（build 等）が無いことを確認した
- [ ] 本体: `manifest.json` と `version.txt` のバージョンを揃えた
- [ ] 本体: `git push -u origin main` した
- [ ] アドオン: `addon-repo/` から `git push -u origin main` した
- [ ] アドオン: 各 `.py` の `# REPO:` が配布先の raw URL になっている
- [ ] `config.json` の `github_repo` が **本体** の raw URL になっている
- [ ] raw の `manifest.json` URL（本体 / アドオン）をブラウザで開いて JSON が見えることを確認した
- [ ] 本体を起動して自動更新のログを確認した
