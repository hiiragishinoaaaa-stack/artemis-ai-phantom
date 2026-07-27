"""有名コインの名前をもじっただけのトークンを見分ける。

## なぜ必要か

`token_name_history.py` の重複検出は「**過去にこのbotが観測したのと同じ名前**」
しか捕まえられない。「Doge Head Coin」のような、**有名コインの名前をもじった
新しい名前**は初出なので素通りする(2026-07、実際に候補チャンネルへ流れた)。

## 何を根拠に弾くのか — 正直に言うと、実測ではない

なりすまし・便乗系の成績は**一度も測っていない**。「Dogeの名前を借りたコインは
不利」という測定結果があるわけではなく、「明らかな偽物は見たくない」という
方針の実装。

そのため既定では**通常通知は今まで通り送り、候補(🎯)からだけ外す**。
候補は「実際に買うか検討する対象」なので厳しくてよいが、通常通知まで止めると
結果の記録が作られなくなり、**この判断が正しかったのか永久に検証できなくなる**。
記録さえ残っていれば、後から「便乗系の勝率」を測って判断し直せる。

## 判定方法

名前とティッカーを英数字だけに正規化し、有名コイン名を**含んでいて、かつ
それ自体ではない**ものを便乗とみなす。

- 「Doge Head Coin」→ `dogeheadcoin` は `doge` を含み、`doge` そのものではない → 便乗
- 「Dogecoin」→ `dogecoin` も `doge` を含む。**本物も引っかかる**が、pump.fun
  から本物のDogecoinが出てくることはないので実害はない。
- 短すぎる語(sol, btc等)は「solar」「robot」のような無関係な名前を巻き込むため
  最初から入れない(_MIN_TERM_LENGTH)。
"""
from __future__ import annotations

import re

import config

# これ未満の長さの語は判定に使わない。"sol"を入れると"solar"、"btc"を入れると
# 無関係な文字列まで便乗扱いになり、巻き込みが大きすぎる。
_MIN_TERM_LENGTH = 4

_NON_ALNUM = re.compile(r"[^a-z0-9]")


def normalize(text: str) -> str:
    """英小文字と数字だけにする。空白・記号・大文字の違いを無視するため。"""
    return _NON_ALNUM.sub("", text.lower())


def famous_terms() -> list[str]:
    """判定に使う有名コイン名の一覧(config.FAMOUS_COIN_NAMES)。"""
    return [t for t in (normalize(x) for x in config.FAMOUS_COIN_NAMES) if len(t) >= _MIN_TERM_LENGTH]


def matched_term(name: str, symbol: str) -> str:
    """便乗と判定した根拠の語を返す。該当しなければ空文字。

    名前かティッカーのどちらかが有名コイン名を含んでいれば便乗とみなす。
    """
    haystacks = [normalize(name), normalize(symbol)]
    for term in famous_terms():
        for haystack in haystacks:
            if haystack and term in haystack:
                return term
    return ""


def is_derivative(name: str, symbol: str) -> bool:
    """有名コインの名前をもじっただけのトークンか。"""
    return bool(matched_term(name, symbol))
