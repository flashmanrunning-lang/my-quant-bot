"""Redraw performance_chart.png from alpha_performance.csv (the C++ engine writes the CSV)."""
import matplotlib
matplotlib.use('Agg')  # headless
import matplotlib.pyplot as plt
import pandas as pd

PERFORMANCE_FILE = "alpha_performance.csv"
CHART_FILE = "performance_chart.png"


def generate_performance_chart(df):
    """Generates an equity curve graph and saves it as a PNG file."""
    try:
        if len(df) < 2:
            print("📊 Chart generation skipped: Waiting for more historical timeline data rows.")
            return

        # Parse string timestamps into proper matplotlib datetime elements
        df['PlotTime'] = pd.to_datetime(df['Timestamp'])
        
        # Build layout grid setup (Top Panel for Equity, Bottom Panel for Relative Alpha)
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 7), gridspec_kw={'height_ratios': [2, 1]}, sharex=True)
        
        # Panel 1: Strategy vs Benchmark Capital Growth Lines
        ax1.plot(df['PlotTime'], df['Active_Bot_Value'], label='Active Bot Strategy', color='#00b0ff', lw=2.5)
        ax1.plot(df['PlotTime'], df['Benchmark_BnH_Value'], label='Buy & Hold Benchmark', color='#90a4ae', lw=1.5, linestyle='--')
        ax1.set_title('Live Dynamic Tracking Dashboard: Active Quant Bot vs Benchmark', fontsize=12, fontweight='bold', pad=12)
        ax1.set_ylabel('Portfolio Assets Value (USDT)', fontsize=10)
        ax1.legend(loc='upper left')
        ax1.grid(True, linestyle=':', alpha=0.6)
        
        # Format the numbers clean (e.g. 101,230)
        ax1.get_yaxis().set_major_formatter(plt.FuncFormatter(lambda x, loc: "{:,}".format(int(x))))
        
        # Panel 2: Alpha Area Percentage Spread
        ax2.fill_between(df['PlotTime'], df['Alpha_Percent'], 0, where=(df['Alpha_Percent'] >= 0), color='#00e676', alpha=0.3, label='Positive Alpha')
        ax2.fill_between(df['PlotTime'], df['Alpha_Percent'], 0, where=(df['Alpha_Percent'] < 0), color='#ff1744', alpha=0.3, label='Negative Alpha')
        ax2.plot(df['PlotTime'], df['Alpha_Percent'], color='#4caf50' if df['Alpha_Percent'].iloc[-1] >= 0 else '#f44336', lw=1.2)
        ax2.axhline(0, color='black', lw=0.8, linestyle=':')
        ax2.set_ylabel('Net Alpha Edge (%)', fontsize=10)
        ax2.set_xlabel('Timeline Horizon (3-Hour Automated Update Cycles)', fontsize=10)
        ax2.grid(True, linestyle=':', alpha=0.6)
        
        # Handle axis formatting
        fig.autofmt_xdate()
        plt.tight_layout()
        
        # Save output image
        plt.savefig(CHART_FILE, dpi=200)
        plt.close()
        print("📊 New visual performance graph plotted and updated successfully.")
    except Exception as e:
        print(f"⚠️ Performance chart generation failed: {e}")


if __name__ == "__main__":
    generate_performance_chart(pd.read_csv(PERFORMANCE_FILE))
