"""通知。約定・停止イベント・定期レポートをWebhook(Discord/Slack互換)に送る。

Webhook URLは秘密情報なので設定ファイル(git管理)には書かない。
環境変数 CRYPTOBOT_WEBHOOK_URL、なければ notify_url.txt(git管理外)から読む。
通知の失敗でbot本体を止めないこと(ログに残すだけ)。
"""
from __future__ import annotations

import json
import logging
import os
import ssl
import urllib.request
from pathlib import Path

log = logging.getLogger("cryptobot.notify")

URL_FILE = "notify_url.txt"  # メモ帳でこのファイルを作ってWebhook URLを1行貼るだけでよい

# 唯一の正当値定義(config.pyはここをimportする)
VALID_FORMATS = ("none", "discord", "slack")


def _ssl_context() -> ssl.SSLContext | None:
    """取引所ラッパーと同じCAバンドル環境変数を尊重する(TLS検査プロキシ環境向け)。"""
    ca = (
        os.environ.get("CRYPTOBOT_CA_BUNDLE")
        or os.environ.get("REQUESTS_CA_BUNDLE")
        or os.environ.get("SSL_CERT_FILE")
    )
    return ssl.create_default_context(cafile=ca) if ca else None


class Notifier:
    def __init__(
        self,
        fmt: str = "none",
        url: str | None = None,
        timeout: float = 5.0,
        url_file: str | Path = URL_FILE,
    ):
        if fmt not in VALID_FORMATS:
            raise ValueError(f"notify.format は {VALID_FORMATS} のいずれか: {fmt!r}")
        self.fmt = fmt
        if url is not None:
            self.url = url
        else:
            self.url = os.environ.get("CRYPTOBOT_WEBHOOK_URL", "")
            if not self.url and Path(url_file).exists():
                self.url = Path(url_file).read_text(encoding="utf-8").strip()
        self.timeout = timeout  # 売買ループを塞がないよう短めに
        if self.fmt != "none" and not self.url:
            log.warning(
                "notify.format=%s ですがWebhook URLが未設定のため通知は送られません"
                "(環境変数 CRYPTOBOT_WEBHOOK_URL か %s に設定)", self.fmt, url_file,
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
            with urllib.request.urlopen(req, timeout=self.timeout, context=_ssl_context()) as res:
                ok = 200 <= res.status < 300
                if not ok:
                    log.warning("通知失敗: HTTP %s", res.status)
                return ok
        except Exception as e:
            log.warning("通知失敗: %s", e)
            return False
