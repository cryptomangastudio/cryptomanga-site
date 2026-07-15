"""多重起動防止ロック。

同じ帳簿(data/)に2つのbotが同時に書き込むと、記帳が交錯して次回起動時の
リプレイが失敗し、liveなら二重買いにもなる。start.bat のダブルクリック等で
起きやすいため、ローカルポートの占有を排他ロックとして使う
(プロセスが死ねばOSが自動で解放するので、ロックファイルの残留問題がない)。
"""
from __future__ import annotations

import socket

LOCK_PORT = 8762  # ダッシュボード(8765)とは別の、ロック専用ポート


def acquire_singleton_lock(port: int = LOCK_PORT) -> socket.socket:
    """プロセス存続中ずっと保持するロックソケットを返す。取得できなければ終了。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
        sock.listen(1)
    except OSError:
        raise SystemExit(
            "既に別のCryptoBotがこのPCで動いています(二重起動は帳簿が壊れるため中止)。\n"
            "先に他の黒い画面(ターミナル)を閉じてから、もう一度起動してください。"
        )
    return sock
