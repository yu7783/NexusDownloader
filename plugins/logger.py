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
