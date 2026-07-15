# bitbank実務仕様・過去データ入手先・実弾昇格基準のリサーチ

実施: 2026-07-14夜間 / 手法: 3テーマ並列調査(一次情報優先)。

## bitbank API実務仕様

- **レート制限**: 読み取り系(QUERY)10回/秒、更新系(注文・取消・出金)6回/秒。超過でHTTP 429(出典: bitbank-api-docs rest-api.md)
- **candlestickのバケット仕様**: 1分〜1時間足は`YYYYMMDD`の**日次**バケット(1リクエスト=最大1日分)、4時間足以上(4h/8h/12h/1day/1week/1month)は`YYYY`の**年次**バケット(1リクエスト=1年分)
- **最低注文数量・手数料率**: `GET /spot/pairs`という一次APIが実在し(ccxtのソースコードで確認)、銘柄ごとの`unit_amount`(最低注文数量)・`maker_fee_rate_quote`・`taker_fee_rate_quote`を返す。BTC/JPYはmaker -0.02%・taker 0.12%(config.example.yamlの既定値と一致)。**手数料は銘柄ごとに異なりうる**ため、`main.py --check`に自動突合を追加した
- **post-onlyの挙動**: 板を食う価格を指定してもエラーにはならず、注文自体が自動キャンセル(`CANCELED_UNFILLED`/`CANCELED_PARTIALLY_FILLED`)される。execution.pyのキャンセル処理を「既に終端状態の注文には無駄なcancel_orderを呼ばない」よう修正した

## 過去データの入手先

- **bitbank公式candlestick API(無料・APIキー不要)が最も確実**。CryptoDataDownload等の海外系サービスはbitbankを扱っておらず、Zaif向けデータにもXRP/JPYがない
- `fetch_history.py`を新規実装。1時間足の数年分は数百リクエストになるため、ccxtの自動レート制限（+安全マージン）に任せてゆっくり取得する設計にした

## 実弾昇格基準

- プロップファーム(FTMO)基準: 最低取引日数4日、日次DD5%、最大DD10%
- アルゴトレード実務: バックテストは最低200トレード推奨、統計的有意性の絶対下限は約30トレード
- EA運用実務: デモ運用3ヶ月が実弾検討の最短ライン、理想は6ヶ月+もう1周期
- 段階増額: FTMOのスケーリングプラン(4ヶ月ごとに条件クリアで+25%)を参考に、`promote.py`に「まず総予算の20%から、約2ヶ月ごとに+25%」のガイドを実装

これらを反映し、`promote.py`の既定値を「最低30日・10取引」から「**最低90日・30取引**(理想180日・200取引)」に引き上げた。
