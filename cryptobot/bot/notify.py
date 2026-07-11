"""通知。約定・停止イベントをWebhook(Discord/Slack互換)に送る。

Webhook URLは秘密情報なので環境変数 CRYPTOBOT_WEBHOOK_URL からのみ読む。
通知の失敗でbot本体を止めないこと(ログに残すだけ)。
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request

log = logging.getLogger("cryptobot.notify")

VALID_FORMATS = ("none", "discord", "slack")


class Notifier:
    def __init__(self, fmt: str = "none", url: str | None = None, timeout: float = 10.0):
        if fmt not in VALID_FORMATS:
            raise ValueError(f"notify.format は {VALID_FORMATS} のいずれか: {fmt!r}")
        self.fmt = fmt
        self.url = url if url is not None else os.environ.get("CRYPTOBOT_WEBHOOK_URL", "")
        self.timeout = timeout
        if self.fmt != "none" and not self.url:
            log.warning(
                "notify.format=%s ですが環境変数 CRYPTOBOT_WEBHOOK_URL が未設定のため"
                "通知は送られません", self.fmt,
            )

    def build_payload(self, text: str) -> dict:
        key = "content" if self.fmt == "discord" else "text"
        return {key: text}

    def send(self, text: str) -> bool:
        """通知を送る。成功でTrue。失敗しても例外は投げない。"""
        if self.fmt == "none" or not self.url:
            return False
        try:
            req = urllib.request.Request(
                self.url,
                data=json.dumps(self.build_payload(text)).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as res:
                ok = 200 <= res.status < 300
                if not ok:
                    log.warning("通知失敗: HTTP %s", res.status)
                return ok
        except Exception as e:
            log.warning("通知失敗: %s", e)
            return False
