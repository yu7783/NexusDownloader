"""Universal Downloader - セキュリティ・ガードマン (security.py)

他人が作った危険なアドオン（PC のデータを消すコードなど）を自動で弾く防衛壁。

仕組み:
    アドオンの .py ファイルをシステムに読み込む（実行する）直前に、
    テキストとして中身をスキャンし、ブラックリスト文字列が含まれていた場合は
    実行を完全ブロックする。

拒否対象（ブラックリスト）の例:
    os.remove        : ファイル削除
    shutil.rmtree    : フォルダ削除
    subprocess       : 外部コマンド実行
    os.system        : 外部コマンド実行

このモジュールは標準ライブラリのみに依存し、他のモジュールから
一方向に参照される（core.py から import される）。
"""

# 既定のブラックリスト（config.json の blocked_keywords で上書き可能）
DEFAULT_BLOCKED = [
    "os.remove",
    "shutil.rmtree",
    "subprocess",
    "os.system",
]


def scan_text(text, blocked_keywords=None):
    """テキストに危険なキーワードが含まれていないか走査する。

    Args:
        text: スキャン対象のソースコード文字列。
        blocked_keywords: ブラックリスト。None の場合は DEFAULT_BLOCKED を使用。

    Returns:
        (is_safe: bool, reason: str) のタプル。
        安全なら (True, "")、危険なら (False, 理由文字列)。
    """
    keywords = list(blocked_keywords) if blocked_keywords is not None else list(DEFAULT_BLOCKED)
    lowered = text.lower()
    for kw in keywords:
        if kw.lower() in lowered:
            return False, "危険な権限が検知されました: '{}'".format(kw)
    return True, ""


class SecurityGuard:
    """他人が作った危険なアドオンを自動で弾く防衛壁。

    .py ファイルをシステムに読み込む（実行する）直前に、テキストとして中身を
    スキャンし、ブラックリスト文字列が含まれていた場合は実行を完全ブロックする。
    """

    def __init__(self, blocked_keywords=None):
        # None なら既定のブラックリスト、指定があればそれを使う
        self.blocked_keywords = list(blocked_keywords) if blocked_keywords else list(DEFAULT_BLOCKED)

    def scan(self, path):
        """ファイルをスキャンして (is_safe: bool, reason: str) を返す。

        Returns:
            安全なら (True, "")、危険なら (False, 警告メッセージ)。
        """
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception as e:
            return False, "ファイルを読み込めませんでした: {}".format(e)

        safe, reason = scan_text(text, self.blocked_keywords)
        if not safe:
            return False, "危険な権限が検知されたため、インストールを拒否しました ({})".format(reason)
        return True, ""