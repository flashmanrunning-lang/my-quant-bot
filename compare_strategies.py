"""Metrics + one comparison chart for v1 vs v2 (and benchmarks).

Usage: python compare_strategies.py results.csv out.png [title] [metrics.csv]
Input: Date index and value columns V1, V2, BTC, Basket, VOO (any may be missing/empty); optional columns
V1_/V2_ Invested_Pct, Fees_Cum, Notional_Cum. Works for the long backtest and for the short live log (CAGR shows n/a under 180 days).
"""
import sys
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.ticker

SERIES = [('V1', 'v1 momentum', '#2a6fdb', '-'), ('V2', 'v2 trend + risk', '#d9640f', '-'), ('BTC', 'BTC hold', '#1f8a70', '-'),
          ('Basket', 'Equal-weight basket', '#b8a000', '--'), ('VOO', 'VOO (S&P 500)', '#8e44ad', '-')]


def metrics(v, invested=None, fees=None, notional=None):
    v = v.dropna()
    if len(v) < 3:
        return {}
    r = v.pct_change().dropna()
    days = (pd.Timestamp(v.index[-1]) - pd.Timestamp(v.index[0])).days
    dd = v / v.cummax() - 1
    tot = v.iloc[-1] / v.iloc[0] - 1
    down = r[r < 0].std()
    out = {'Total return': tot,
           'CAGR': (1 + tot) ** (365.25 / days) - 1 if days >= 180 else np.nan,
           'Volatility': r.std() * np.sqrt(365), 'Sharpe': r.mean() / r.std() * np.sqrt(365) if r.std() > 0 else np.nan,
           'Sortino': r.mean() / down * np.sqrt(365) if down and down > 0 else np.nan,
           'Max drawdown': dd.min(), 'Worst day': r.min(), 'Days >10% under peak': (dd < -0.10).mean()}
    if days < 30:  # too short for risk ratios to mean anything
        for k in ('Volatility', 'Sharpe', 'Sortino'):
            out[k] = np.nan
    out['Calmar'] = out['CAGR'] / -out['Max drawdown'] if out['Max drawdown'] < 0 and not np.isnan(out['CAGR']) else np.nan
    if invested is not None and invested.notna().any():
        out['Time in cash'] = (invested.reindex(v.index) < 5).mean()
    if fees is not None and fees.notna().any():
        out['Fees paid'] = fees.dropna().iloc[-1]
        if notional is not None:
            out['Turnover / yr'] = notional.dropna().iloc[-1] / v.mean() / (days / 365.25) if days > 0 else np.nan
    return out


def yearly(v):
    v = v.dropna(); v.index = pd.to_datetime(v.index)
    ends = v.groupby(v.index.year).last()
    prev = ends.shift(1); prev.iloc[0] = v.iloc[0]
    return ends / prev - 1


def fmt(k, x):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return 'n/a'
    if k in ('Sharpe', 'Sortino', 'Calmar'):
        return f'{x:.2f}'
    if k == 'Fees paid':
        return f'${x:,.0f}'
    if k == 'Turnover / yr':
        return f'{x:.1f}x'
    return f'{x * 100:.1f}%'


def main(src, png, title='v1 vs v2', mcsv=None):
    df = pd.read_csv(src, index_col='Date')
    ser = [(k, n, c, ls) for k, n, c, ls in SERIES if k in df and df[k].notna().sum() > 2]
    M = {}
    for k, n, c, ls in ser:
        p = k if k in ('V1', 'V2') else None
        M[n] = metrics(df[k], df.get(f'{p}_Invested_Pct') if p else None, df.get(f'{p}_Fees_Cum') if p else None,
                       df.get(f'{p}_Notional_Cum') if p else None)
    pd.DataFrame(M).to_csv(mcsv or png.replace('.png', '_metrics.csv'), float_format='%.4f')

    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 9, 'text.color': '#1a1a1a', 'axes.labelcolor': '#555',
                         'xtick.color': '#555', 'ytick.color': '#555'})
    fig = plt.figure(figsize=(13, 10), facecolor='#fcfcfb')
    gs = fig.add_gridspec(3, 2, height_ratios=[1.5, 0.9, 1.3], width_ratios=[1, 1.25], hspace=0.42, wspace=0.12)
    idx = pd.to_datetime(df.index)

    def style(ax):
        ax.set_facecolor('#fcfcfb')
        for s in ('top', 'right'): ax.spines[s].set_visible(False)
        for s in ('left', 'bottom'): ax.spines[s].set_color('#c8c8c4')
        ax.grid(axis='y', color='#ececE8', lw=0.8); ax.set_axisbelow(True)

    ax = fig.add_subplot(gs[0, :]); style(ax)
    for k, n, c, ls in ser:
        v = df[k] / df[k].dropna().iloc[0] * 100
        ax.plot(idx, v, color=c, lw=1.6, ls=ls, label=n)
        ax.annotate(f'{v.dropna().iloc[-1]:,.0f}', (idx[v.notna().values][-1], v.dropna().iloc[-1]), xytext=(4, 0),
                    textcoords='offset points', va='center', fontsize=8, color='#1a1a1a')
    ax.set_yscale('log'); ax.set_yticks([50, 100, 200, 400, 800]); ax.set_yticklabels(['50', '100', '200', '400', '800'])
    ax.yaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
    ax.set_title(f'{title}: growth of 100 (log scale)', loc='left', fontsize=12, fontweight='bold')
    ax.legend(ncol=5, frameon=False, loc='upper left', fontsize=9)

    ax = fig.add_subplot(gs[1, :]); style(ax)
    for k, n, c, ls in ser:
        v = df[k].dropna(); ax.plot(pd.to_datetime(v.index), (v / v.cummax() - 1) * 100, color=c, lw=1.2, ls=ls)
    ax.set_ylabel('% below prior peak'); ax.set_title('Drawdown', loc='left', fontsize=10, fontweight='bold')

    ax = fig.add_subplot(gs[2, 0]); style(ax)
    Y = pd.DataFrame({n: yearly(df[k]) for k, n, c, ls in ser}) * 100
    w = 0.8 / max(len(ser), 1)
    for i, (k, n, c, ls) in enumerate(ser):
        ax.bar(np.arange(len(Y)) + i * w - 0.4 + w / 2, Y[n], width=w * 0.85, color=c)
    ax.axhline(0, color='#888', lw=0.8)
    ax.set_xticks(range(len(Y))); ax.set_xticklabels([str(y) if y != Y.index[-1] else f'{y}*' for y in Y.index])
    ax.set_ylabel('% return'); ax.set_title('Calendar-year return (* partial year)', loc='left', fontsize=10, fontweight='bold')

    ax = fig.add_subplot(gs[2, 1]); ax.axis('off')
    keys = ['Total return', 'CAGR', 'Volatility', 'Sharpe', 'Max drawdown', 'Calmar', 'Worst day', 'Days >10% under peak', 'Time in cash',
            'Turnover / yr', 'Fees paid']
    cell = [[fmt(k, M[n].get(k)) for n in M] for k in keys]
    t = ax.table(cellText=cell, rowLabels=keys, colLabels=[n.replace(' ', '\n', 1) for n in M], bbox=[0.18, 0, 0.82, 0.8], cellLoc='center')
    t.auto_set_font_size(False); t.set_fontsize(8); pass
    for (r, cidx), c in t.get_celld().items():
        c.set_edgecolor('#dcdcd8'); c.set_facecolor('#fcfcfb'); c.get_text().set_color('#1a1a1a')
    fig.savefig(png, dpi=130, facecolor='#fcfcfb', bbox_inches='tight')


if __name__ == '__main__':
    main(*sys.argv[1:5])
