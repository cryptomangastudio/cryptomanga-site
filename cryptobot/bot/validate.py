"""過学習検出(リサーチ#6): シャープレシオ・Deflated Sharpe Ratio・ウォークフォワード。

Bailey & López de Prado (SSRN 2460551) のDSR: 多数のパラメータ試行から
生まれた「見かけ上のシャープレシオ」が、試行回数を考慮してもなお有意かを検定する。
DSR < 0.95 の戦略は実弾に昇格させない、が推奨ゲート。
"""
from __future__ import annotations

import math
from statistics import NormalDist

_N = NormalDist()


def sharpe_ratio(returns: list[float], periods_per_year: int) -> float:
    """期間リターン列から年率換算シャープレシオ(無リスク金利0仮定)。"""
    n = len(returns)
    if n < 2:
        return 0.0
    mean = sum(returns) / n
    var = sum((r - mean) ** 2 for r in returns) / (n - 1)
    if var <= 0:
        return 0.0
    return mean / math.sqrt(var) * math.sqrt(periods_per_year)


def _moments(returns: list[float]) -> tuple[float, float]:
    """(歪度, 尖度)。サンプル不足時は正規分布相当(0, 3)。"""
    n = len(returns)
    if n < 4:
        return 0.0, 3.0
    mean = sum(returns) / n
    var = sum((r - mean) ** 2 for r in returns) / n
    if var <= 0:
        return 0.0, 3.0
    sd = math.sqrt(var)
    skew = sum(((r - mean) / sd) ** 3 for r in returns) / n
    kurt = sum(((r - mean) / sd) ** 4 for r in returns) / n
    return skew, kurt


def expected_max_sharpe(n_trials: int, n_obs: int) -> float:
    """N回の独立試行でノイズだけから得られる最大シャープの期待値(期間ベース)。"""
    if n_trials <= 1 or n_obs <= 1:
        return 0.0
    gamma = 0.5772156649015329  # オイラー・マスケローニ定数
    q1 = _N.inv_cdf(1 - 1 / n_trials)
    q2 = _N.inv_cdf(1 - 1 / (n_trials * math.e))
    return math.sqrt(1 / (n_obs - 1)) * ((1 - gamma) * q1 + gamma * q2)


def deflated_sharpe(returns: list[float], n_trials: int) -> float:
    """DSR: 試行回数を考慮した「シャープが本物である確率」(0〜1)。

    returns は期間(バー)ごとのリターン列。0.95以上で合格が目安。
    """
    n = len(returns)
    if n < 10:
        return 0.0
    mean = sum(returns) / n
    var = sum((r - mean) ** 2 for r in returns) / (n - 1)
    if var <= 0:
        return 0.0
    sr = mean / math.sqrt(var)  # 期間ベースのSR(年率化しない)
    sr0 = expected_max_sharpe(max(n_trials, 1), n)
    skew, kurt = _moments(returns)
    denom = 1 - skew * sr + (kurt - 1) / 4 * sr * sr
    if denom <= 0:
        return 0.0
    z = (sr - sr0) * math.sqrt(n - 1) / math.sqrt(denom)
    return _N.cdf(z)


def walk_forward_segments(n_rows: int, n_segments: int) -> list[tuple[int, int]]:
    """データをn_segmentsの連続区間に分割する(start, end)のリスト。"""
    if n_segments < 2 or n_rows < n_segments * 2:
        return [(0, n_rows)]
    size = n_rows // n_segments
    segments = []
    for i in range(n_segments):
        start = i * size
        end = n_rows if i == n_segments - 1 else (i + 1) * size
        segments.append((start, end))
    return segments
