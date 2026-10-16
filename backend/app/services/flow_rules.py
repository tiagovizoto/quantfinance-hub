"""Regras que separam o que é receita/despesa REAL do que é só dinheiro mudando de lugar.

Sem isto, ligar vários bancos infla os dois lados: um PIX da conta A para a conta B vira despesa em A e
receita em B; resgates e aplicações viram receita e despesa; o caixa da corretora (ajuste de day trade,
líquido das operações) entra bruto, dia a dia. Cada linha ganha um ``reason`` para ser auditável.
"""

import re
from collections.abc import Sequence
from datetime import timedelta
from typing import Final

import numpy as np
import pandas as pd

# Pagamento de fatura (a compra já foi contada no cartão) e transferência entre contas da mesma pessoa.
CARD_MARKERS: Final = (
    "credit card payment",
    "pagamento de cartao",
    "pagamento de fatura",
    "pagamento fatura",
    "pagamento recebido",
)
OWN_TRANSFER_MARKERS: Final = (
    "same person transfer",
    "mesma titularidade",
)
# Aplicação e resgate: o dinheiro só passa para (ou volta de) um investimento, que já está no patrimônio.
INVESTMENT_MARKERS: Final = ("aplicacao", "resgate", "aplic financ", "resg ")
DAY_TRADE_MARKERS: Final = (
    "ajuste day",
    "day-trade",
    "day trade",
    "daytrade",
    "liquido das operacoes",
    "liquido de operacoes",
)
# Dinheiro de empréstimo liberado na conta: aumenta a dívida, não é receita.
LOAN_PROCEEDS_MARKERS: Final = (
    "lib consig",
    "lib emp",
    "liberacao de credito",
    "credito consignado",
    "emprestimo liberado",
)
_TRANSFER_WORDS: Final = re.compile(r"\b(transf\w*|ted|doc|pix|tef|saldo)\b")
PAIR_WINDOW_DAYS: Final = 2

REASON_LABELS: Final = {
    "cartao": "Pagamento de fatura do cartão",
    "propria": "Transferência entre suas contas",
    "pareada": "Transferência entre suas contas (valores que saem de uma e entram na outra)",
    "investimento": "Aplicação e resgate de investimentos",
    "emprestimo": "Dinheiro de empréstimo liberado na conta (é dívida, não receita)",
    "day_trade": "Caixa da corretora (day trade): entra só o resultado líquido do mês",
}


def _contains(text: pd.Series, markers: Sequence[str]) -> pd.Series:
    return text.map(lambda t: any(m in t for m in markers))


def _squash(text: str, normalize) -> str:
    return " ".join(normalize(text).split())


_TRAILING_NAME: Final = re.compile(r"[-|]\s*([A-Za-zÀ-ÿ]+(?:\s+[A-Za-zÀ-ÿ]+){2,})\s*$")


def detect_own_names(frame: pd.DataFrame, normalize) -> list[str]:
    """Nome do titular, aprendido das linhas que o próprio banco marcou como "Same person transfer"."""
    marked = frame[frame["category"].fillna("").str.lower().str.contains("same person")]
    counts: dict[str, int] = {}
    for desc in marked["description"].fillna(""):
        match = _TRAILING_NAME.search(desc)
        if match:
            name = _squash(match.group(1), normalize)
            counts[name] = counts.get(name, 0) + 1
    # Só nomes completos: "Recarga - TIM" ou "SAQUE BANCO 24H" também vêm marcados pelo banco e não são pessoas.
    return [name for name, n in counts.items() if n >= 2 and len(name.split()) >= 2 and not any(ch.isdigit() for ch in name)]


def own_name_mask(
    description: pd.Series, own_names: Sequence[str], normalize
) -> pd.Series:
    """Linhas cuja descrição cita o nome do titular (PIX/TED para si mesmo)."""
    names = [_squash(n, normalize) for n in own_names if n and n.strip()]
    if not names:
        return pd.Series(False, index=description.index)
    text = description.fillna("").map(lambda t: _squash(t, normalize))
    return text.map(lambda t: any(n in t for n in names))


def pair_transfers(
    frame: pd.DataFrame, text: pd.Series, skip: pd.Series, is_credit: pd.Series
) -> pd.Series:
    """Casa saída e entrada de mesmo valor (R$) em contas DIFERENTES num intervalo de poucos dias.

    Só considera linhas de conta corrente com cara de transferência (TED, PIX, transf., saldo...), para
    não casar por coincidência uma compra de R$ 50 com um R$ 50 recebido de um amigo.
    """
    result = np.zeros(len(frame), dtype=bool)
    if "account_name" not in frame.columns:
        return pd.Series(result, index=frame.index)
    candidates = (
        frame["account_type"].eq("BANK").to_numpy()
        & ~skip.to_numpy()
        & text.map(lambda t: bool(_TRANSFER_WORDS.search(t))).to_numpy()
    )
    cents = (frame["amount"].astype(float).abs() * 100).round().astype(int).to_numpy()
    dates = pd.to_datetime(frame["date"]).to_numpy()
    accounts = frame["account_name"].fillna("").to_numpy()
    credit = is_credit.to_numpy()
    window = np.timedelta64(PAIR_WINDOW_DAYS, "D")

    incoming: dict[int, list[int]] = {}
    for i in np.flatnonzero(candidates & credit):
        incoming.setdefault(int(cents[i]), []).append(int(i))
    for i in sorted(np.flatnonzero(candidates & ~credit), key=lambda k: dates[k]):
        for j in incoming.get(int(cents[i]), []):
            if (
                not result[j]
                and accounts[j] != accounts[i]
                and abs(dates[j] - dates[i]) <= window
            ):
                result[i] = result[j] = True
                break
    return pd.Series(result, index=frame.index)


def net_day_trade(frame: pd.DataFrame, mask: pd.Series) -> pd.DataFrame:
    """Troca os lançamentos brutos do day trade por UMA linha por mês com o resultado líquido."""
    rows = frame[mask]
    if rows.empty:
        return frame
    kept = frame[~mask].copy()
    synthetic = []
    for _, month in rows.groupby(rows["date"].dt.to_period("M")):
        signed = np.where(
            month["type"].str.upper().eq("CREDIT"), month["amount"], -month["amount"]
        )
        net = float(signed.sum())
        if abs(net) < 0.005:
            continue
        account = (
            month["account_name"].mode().iat[0]
            if "account_name" in month.columns
            else ""
        )
        base = {c: None for c in frame.columns}
        base.update(
            date=month["date"].max().normalize() + timedelta(hours=12),
            amount=abs(net),
            type="CREDIT" if net > 0 else "DEBIT",
            category="Day trade",
            description="Resultado líquido de day trade no mês",
            account_type="BANK",
            account_name=account,
            tx_id=f"daytrade-{month['date'].max():%Y-%m}",
            income=abs(net) if net > 0 else 0.0,
            expense=abs(net) if net < 0 else 0.0,
            reason="",
        )
        synthetic.append(base)
    return (
        pd.concat(
            [kept, pd.DataFrame(synthetic, columns=frame.columns)], ignore_index=True
        )
        if synthetic
        else kept
    )
