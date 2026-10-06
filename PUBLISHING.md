# GitHub 公開手順（Nexus Downloader 本体 + アドオン）

このドキュメントでは、**本体リポジトリ** と **アドオン専用リポジトリ** の 2 つを
GitHub に公開する手順を説明します。

| リポジトリ | 用途 | 例 |
|---|---|---|
| 本体 | アプリ本体（main.py/core.py/security.py 等） | `yu7783/NexusDownloader` |
| アドオン | 自動更新で配布するアドオン + manifest.json | `yu7783/NexusDownloader-Addons` |

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

本体は `config.json` の `github_repo` から manifest を取得します。
**`github.com/...` ではなく raw コンテンツ URL を指定** してください
（`github.com` は HTML を返すため JSON 解析に失敗します）。

| キー | 値 |
|---|---|
| `github_repo` | `https://raw.githubusercontent.com/yu7783/NexusDownloader-Addons/main` |
| `update_manifest` | `manifest.json` |
| `auto_update` | `true` |

> これは `config.json` に **設定済み** です（追記済み）。
> 設定タブの GUI からも変更できます。

push 後、次の URL で manifest が取得できれば準備完了:

```
https://raw.githubusercontent.com/yu7783/NexusDownloader-Addons/main/manifest.json
```

---

## 4. 動作確認

1. 本体を起動（`python main.py`）
2. 起動時に自動更新が走り、コンソール／`logs/downloader.log` に結果が出る
3. GitHub の URL とローカルの `addons/yt_dlp_addon.py` のバージョンを比較
4. アドオンを更新したら `manifest.json` の `version` を上げて push
   → 次回起動時に本体が自動で上書き

> ⚠️ 更新ファイルは実行前に本体の **セキュリティスキャン** を通ります。
> `os.remove` / `subprocess` 等のブロック語を含めないでください。

---

## 5. アドオンを追加・更新するフロー（今後の運用）

```powershell
# 1) アドオンを編集（addon-repo/addons/*.py）
# 2) manifest.json の該当 version を上げる
# 3) コミット & push
cd c:\Users\nnyk0\programings\downloader\addon-repo
git add -A
git commit -m "Update yt_dlp_addon to v1.2.0"
git push
```

本体側の `addons/` にもコピーを置いておくと、開発中の動作確認が楽です。

---

## 6. よくあるトラブル

| 症状 | 原因 / 対処 |
|---|---|
| `updates were rejected` | リモートに既にコミットがある。`git pull --rebase origin main` してから push |
| 更新マニフェストの取得に失敗 | `github_repo` が `github.com` になっている。`raw.githubusercontent.com` に変更 |
| 404 | ブランチ名不一致（`main` / `master`）。URL のブランチ部分を実際に合わせる |
| push 時に認証エラー | PAT or SSH 未設定。`gh auth login` か `git config` で設定 |
| 巨大ファイルで push 遅い | `.gitignore` 漏れ。`build/` 等を除外し `git rm -r --cached build` |

---

## 7. チェックリスト

- [ ] `git config --global user.name/user.email` を設定した
- [ ] GitHub に空リポジトリを 2 つ作成した
- [ ] 本体: `git status` に不要ファイル（build 等）が無いことを確認した
- [ ] 本体: `git push -u origin main` した
- [ ] アドオン: `addon-repo/` から `git push -u origin main` した
- [ ] `config.json` の `github_repo` が raw URL になっている
- [ ] raw の `manifest.json` URL をブラウザで開いて JSON が見えることを確認した
- [ ] 本体を起動して自動更新のログを確認した
