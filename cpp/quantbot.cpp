// quantbot.cpp - C++17 port of main.py (the "fixes" branch version).
//
// Strategy: long-only crypto momentum with a macro risk-on/risk-off filter, run as a paper simulation.
//   * Regime: BTC above its 20-day EMA AND at least half of the macro votes bullish.
//   * Picks : top 3 coins by 45-bar return (positive only), equal weight. Otherwise 100% cash.
//   * Costs : FEE_RATE on every dollar traded; no rebalance unless picks change or a weight drifts.
//
// Market data is read from CSV files (Date,Close) written by fetch_data.py, so this program does no networking.
// Build: g++ -std=c++17 -O2 -Wall -Wextra -o quantbot cpp/quantbot.cpp
// Run  : ./quantbot [--data-dir data] [--state-dir .] [--now "YYYY-MM-DD HH:MM:SS"]

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <optional>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace fs = std::filesystem;

namespace {

// ---------------------------------------------------------------- configuration
const std::vector<std::string> COINS = {"ETH-USD", "SOL-USD", "LINK-USD", "AVAX-USD", "NEAR-USD", "ADA-USD", "DOT-USD"};
const std::vector<std::string> MACRO = {"^GSPC", "GC=F", "DX-Y.NYB", "CL=F"};  // S&P 500, gold, dollar index, crude
constexpr double INITIAL_CASH = 100000.0;
constexpr double FEE_RATE = 0.001;         // ASSUMPTION: 0.10% per trade; set to your exchange's real rate
constexpr double REBALANCE_DRIFT = 0.05;   // re-equalize an unchanged pick set only if a weight is >5 points off
constexpr std::size_t MIN_MACRO_VOTES = 2;
const std::string VOO_TICKER = "VOO";    // S&P 500 ETF, shown on the chart as a second benchmark

struct Config {
    fs::path data_dir = "data";
    fs::path state_dir = ".";
    std::string now;
};

// ---------------------------------------------------------------- small helpers
std::string now_utc() {
    std::time_t t = std::time(nullptr);
    std::tm tm{};
    gmtime_r(&t, &tm);
    char buf[32];
    std::strftime(buf, sizeof buf, "%Y-%m-%d %H:%M:%S", &tm);
    return buf;
}

std::string trim(std::string s) {
    while (!s.empty() && std::isspace(static_cast<unsigned char>(s.back()))) s.pop_back();
    std::size_t i = 0;
    while (i < s.size() && std::isspace(static_cast<unsigned char>(s[i]))) ++i;
    return s.substr(i);
}

std::vector<std::string> split_csv(const std::string& line) {
    std::vector<std::string> out;
    std::string cur;
    std::stringstream ss(line);
    while (std::getline(ss, cur, ',')) out.push_back(trim(cur));
    return out;
}

std::string num(double v) {  // round-trip precision for state files
    std::ostringstream o;
    o << std::setprecision(17) << v;
    return o.str();
}

double round2(double v) {
    v = std::round(v * 100.0) / 100.0;
    if (v == 0.0) v = 0.0;  // normalize -0.0
    return v;
}

std::string fixed2(double v) {
    char b[64];
    std::snprintf(b, sizeof b, "%.2f", round2(v));
    return b;
}

std::string money(double v) {  // 1,234,567.89
    char b[64];
    std::snprintf(b, sizeof b, "%.2f", v);
    std::string s = b, sign;
    if (s[0] == '-') { sign = "-"; s.erase(0, 1); }
    std::size_t dot = s.find('.');
    for (int i = static_cast<int>(dot) - 3; i > 0; i -= 3) s.insert(static_cast<std::size_t>(i), ",");
    return sign + s;
}

// ---------------------------------------------------------------- market data
struct Series {
    std::vector<std::string> dates;
    std::vector<double> close;
    bool empty() const { return close.empty(); }
};

std::string safe_name(std::string t) {  // must match fetch_data.py
    for (char& c : t)
        if (!(std::isalnum(static_cast<unsigned char>(c)) || c == '.' || c == '_' || c == '-')) c = '_';
    return t;
}

Series load_series(const Config& cfg, const std::string& ticker) {
    Series s;
    std::ifstream in(cfg.data_dir / (safe_name(ticker) + ".csv"));
    if (!in) return s;
    std::string line;
    std::getline(in, line);  // header
    while (std::getline(in, line)) {
        auto f = split_csv(line);
        if (f.size() < 2) continue;
        char* end = nullptr;
        double v = std::strtod(f[1].c_str(), &end);
        if (end == f[1].c_str() || std::isnan(v)) continue;  // dropna
        s.dates.push_back(f[0]);
        s.close.push_back(v);
    }
    return s;
}

// pandas ewm(span=N, adjust=False).mean().iloc[-1]
double ema_last(const std::vector<double>& x, int span) {
    const double alpha = 2.0 / (span + 1.0);
    double y = x.front();
    for (std::size_t i = 1; i < x.size(); ++i) y = alpha * x[i] + (1.0 - alpha) * y;
    return y;
}

double pearson(const std::vector<double>& a, const std::vector<double>& b) {
    const std::size_t n = a.size();
    double ma = 0, mb = 0;
    for (std::size_t i = 0; i < n; ++i) { ma += a[i]; mb += b[i]; }
    ma /= static_cast<double>(n);
    mb /= static_cast<double>(n);
    double sab = 0, saa = 0, sbb = 0;
    for (std::size_t i = 0; i < n; ++i) {
        sab += (a[i] - ma) * (b[i] - mb);
        saa += (a[i] - ma) * (a[i] - ma);
        sbb += (b[i] - mb) * (b[i] - mb);
    }
    if (saa <= 0 || sbb <= 0) return std::nan("");
    return sab / std::sqrt(saa * sbb);
}

// ---------------------------------------------------------------- regime engine
// true = risk-on. Throws if there is not enough data to decide (caller then holds positions).
bool calculate_macro_regime(const Config& cfg) {
    Series btc = load_series(cfg, "BTC-USD");
    if (btc.empty()) throw std::runtime_error("macro data unavailable (no BTC-USD series)");

    const bool btc_bull = btc.close.back() > ema_last(btc.close, 20);
    std::map<std::string, double> btc_by_date;
    for (std::size_t i = 0; i < btc.dates.size(); ++i) btc_by_date[btc.dates[i]] = btc.close[i];

    int votes_true = 0;
    std::size_t votes = 0;
    for (const auto& ticker : MACRO) {
        Series asset = load_series(cfg, ticker);
        if (asset.close.size() < 25) continue;

        // Returns over the SAME dates (BTC trades weekends, these markets do not).
        std::vector<double> rb, ra;
        for (std::size_t i = 1; i < asset.close.size(); ++i) {
            auto cur = btc_by_date.find(asset.dates[i]);
            auto prev = btc_by_date.find(asset.dates[i - 1]);
            if (cur == btc_by_date.end() || prev == btc_by_date.end()) continue;
            rb.push_back(cur->second / prev->second - 1.0);
            ra.push_back(asset.close[i] / asset.close[i - 1] - 1.0);
        }
        if (rb.size() > 30) {
            rb.erase(rb.begin(), rb.end() - 30);
            ra.erase(ra.begin(), ra.end() - 30);
        }
        if (rb.size() < 20) continue;
        const double corr = pearson(rb, ra);
        if (std::isnan(corr)) continue;

        const bool above = asset.close.back() > ema_last(asset.close, 20);
        const bool vote = corr >= 0 ? above : !above;
        ++votes;
        if (vote) ++votes_true;
    }
    if (votes < MIN_MACRO_VOTES) {
        throw std::runtime_error("only " + std::to_string(votes) + " usable macro votes (need " +
                                 std::to_string(MIN_MACRO_VOTES) + ")");
    }
    return btc_bull && (votes_true >= static_cast<double>(votes) / 2.0);
}

// ---------------------------------------------------------------- prices and momentum
struct Prices {
    std::vector<std::string> top3;
    std::map<std::string, double> current;
};

Prices get_live_prices(const Config& cfg) {
    Prices out;
    std::vector<std::pair<std::string, double>> scores;
    for (const auto& coin : COINS) {
        Series s = load_series(cfg, coin);
        if (s.empty()) {
            std::cout << "⚠️ No price data for " << coin << "\n";
            continue;
        }
        const double cur = s.close.back();
        out.current[coin] = cur;
        if (s.close.size() < 46) continue;
        const double base = s.close[s.close.size() - 45];
        const double momentum = (cur - base) / base;
        if (momentum > 0) scores.emplace_back(coin, momentum);
    }
    std::stable_sort(scores.begin(), scores.end(),
                     [](const auto& a, const auto& b) { return a.second > b.second; });
    for (std::size_t i = 0; i < scores.size() && i < 3; ++i) out.top3.push_back(scores[i].first);
    return out;
}

// ---------------------------------------------------------------- ledger
struct Row {
    std::string asset;
    double quantity = 0, avg_price = 0, total_value = 0;
    std::string type;  // CASH or CRYPTO
};

std::vector<Row> load_ledger(const Config& cfg) {
    std::vector<Row> rows;
    std::ifstream in(cfg.state_dir / "portfolio_ledger.csv");
    if (!in) {
        rows.push_back({"USDT", INITIAL_CASH, 1.0, INITIAL_CASH, "CASH"});
        return rows;
    }
    std::string line;
    std::getline(in, line);
    auto header = split_csv(line);
    auto col = [&](const std::string& name) {
        for (std::size_t i = 0; i < header.size(); ++i) if (header[i] == name) return static_cast<int>(i);
        throw std::runtime_error("ledger is missing column " + name);
    };
    const int ca = col("Asset"), cq = col("Quantity"), cp = col("Avg_Price"), cv = col("Total_Value"), ct = col("Asset_Type");
    while (std::getline(in, line)) {
        auto f = split_csv(line);
        if (f.size() < header.size()) continue;
        Row r{f[ca], std::stod(f[cq]), std::stod(f[cp]), std::stod(f[cv]), f[ct]};
        if (r.type != "BENCHMARK") rows.push_back(r);
    }
    return rows;
}

void save_ledger(const Config& cfg, const std::vector<Row>& rows) {
    std::ofstream out(cfg.state_dir / "portfolio_ledger.csv");
    out << "Asset,Quantity,Avg_Price,Total_Value,Asset_Type\n";
    for (const auto& r : rows)
        out << r.asset << "," << num(r.quantity) << "," << num(r.avg_price) << "," << num(r.total_value) << "," << r.type << "\n";
}

// ---------------------------------------------------------------- benchmark baseline (JSON, same file as main.py)
struct Baseline {
    std::string started;
    double capital = 0;
    std::map<std::string, double> prices, last;
    bool has_voo = false;       // VOO comparison is optional: it starts the first time a VOO price is available
    double voo_start = 0;       // VOO price when the comparison started
    double voo_capital = 0;     // dollars "invested in VOO" at that moment (= the bot's value then)
};

struct JsonReader {
    const std::string& s;
    std::size_t i = 0;
    explicit JsonReader(const std::string& text) : s(text) {}
    void ws() { while (i < s.size() && std::isspace(static_cast<unsigned char>(s[i]))) ++i; }
    void expect(char c) {
        ws();
        if (i >= s.size() || s[i] != c) throw std::runtime_error(std::string("baseline json: expected '") + c + "'");
        ++i;
    }
    bool peek(char c) { ws(); return i < s.size() && s[i] == c; }
    std::string str() {
        expect('"');
        std::string out;
        while (i < s.size() && s[i] != '"') out += s[i++];
        expect('"');
        return out;
    }
    double number() {
        ws();
        std::size_t j = i;
        while (j < s.size() && (std::isdigit(static_cast<unsigned char>(s[j])) || std::string("+-.eE").find(s[j]) != std::string::npos)) ++j;
        if (j == i) throw std::runtime_error("baseline json: expected number");
        double v = std::stod(s.substr(i, j - i));
        i = j;
        return v;
    }
    std::map<std::string, double> numobj() {
        std::map<std::string, double> m;
        expect('{');
        if (peek('}')) { expect('}'); return m; }
        while (true) {
            std::string k = str();
            expect(':');
            m[k] = number();
            if (peek(',')) { expect(','); continue; }
            expect('}');
            return m;
        }
    }
};

bool load_baseline(const Config& cfg, Baseline& b) {
    std::ifstream in(cfg.state_dir / "benchmark_baseline.json");
    if (!in) return false;
    std::stringstream ss;
    ss << in.rdbuf();
    const std::string text = ss.str();
    JsonReader r(text);
    r.expect('{');
    while (true) {
        std::string key = r.str();
        r.expect(':');
        if (key == "started") b.started = r.str();
        else if (key == "capital") b.capital = r.number();
        else if (key == "prices") b.prices = r.numobj();
        else if (key == "last_prices") b.last = r.numobj();
        else if (key == "voo_start") { b.voo_start = r.number(); b.has_voo = true; }
        else if (key == "voo_capital") b.voo_capital = r.number();
        else throw std::runtime_error("baseline json: unexpected key " + key);
        if (r.peek(',')) { r.expect(','); continue; }
        r.expect('}');
        break;
    }
    return true;
}

void write_obj(std::ostream& o, const std::map<std::string, double>& m) {
    if (m.empty()) { o << "{}"; return; }
    o << "{\n";
    std::size_t n = 0;
    for (const auto& kv : m) o << "    \"" << kv.first << "\": " << num(kv.second) << (++n < m.size() ? ",\n" : "\n");
    o << "  }";
}

void save_baseline(const Config& cfg, const Baseline& b) {
    std::ofstream o(cfg.state_dir / "benchmark_baseline.json");
    o << "{\n  \"started\": \"" << b.started << "\",\n  \"capital\": " << num(b.capital) << ",\n";
    if (b.has_voo) o << "  \"voo_start\": " << num(b.voo_start) << ",\n  \"voo_capital\": " << num(b.voo_capital) << ",\n";
    o << "  \"prices\": ";
    write_obj(o, b.prices);
    o << ",\n  \"last_prices\": ";
    write_obj(o, b.last);
    o << "\n}\n";
}

Baseline load_or_create_baseline(const Config& cfg, double strategy_val, const std::map<std::string, double>& prices,
                                 std::optional<double> voo_price) {
    Baseline b;
    bool fresh = false;
    if (!load_baseline(cfg, b)) {
        fresh = true;
        b.started = cfg.now;
        b.capital = round2(strategy_val);  // benchmark starts level with the bot, so alpha starts at 0%
        for (const auto& c : COINS) {
            auto it = prices.find(c);
            if (it != prices.end()) b.prices[c] = it->second;
        }
        std::cout << "📌 Benchmark baseline created at " << cfg.now << " with $" << money(b.capital) << ".\n";
    }
    for (const auto& c : COINS) {
        auto it = prices.find(c);
        if (it != prices.end()) b.last[c] = it->second;
    }
    if (!b.has_voo && voo_price && *voo_price > 0) {
        // Starts together with the baseline, or later (older baseline files, or VOO data missing on day one),
        // in which case VOO starts level with the bot's value at that moment.
        b.has_voo = true;
        b.voo_start = *voo_price;
        b.voo_capital = fresh ? b.capital : round2(strategy_val);
        std::cout << "📌 VOO comparison started at $" << money(*voo_price) << " per share with $" << money(b.voo_capital) << ".\n";
    }
    save_baseline(cfg, b);
    return b;
}

double benchmark_value(const Baseline& b, const std::map<std::string, double>& prices) {
    const double slot = b.capital / static_cast<double>(COINS.size());
    double total = slot * static_cast<double>(COINS.size() - b.prices.size());  // no start price -> flat cash
    for (const auto& kv : b.prices) {
        double price = kv.second;
        if (auto it = prices.find(kv.first); it != prices.end()) price = it->second;
        else if (auto lt = b.last.find(kv.first); lt != b.last.end()) price = lt->second;
        total += slot * price / kv.second;
    }
    return total;
}

// ---------------------------------------------------------------- journals
struct Journal {
    double bnh = 0, alpha_pct = 0;
    std::optional<double> voo_value;  // what the bot's starting capital would be worth if held in VOO
};

// Older alpha_performance.csv files have no VOO column: add it (empty for the old rows) so rows stay aligned.
void ensure_perf_schema(const fs::path& path) {
    std::ifstream in(path);
    if (!in) return;
    std::vector<std::string> lines;
    std::string line;
    while (std::getline(in, line)) lines.push_back(line);
    in.close();
    if (lines.empty() || lines[0].find("VOO_Hold_Value") != std::string::npos) return;
    std::ofstream out(path, std::ios::trunc);
    out << trim(lines[0]) << ",VOO_Hold_Value\n";
    for (std::size_t i = 1; i < lines.size(); ++i)
        if (!trim(lines[i]).empty()) out << trim(lines[i]) << ",\n";
}

Journal journal_alpha(const Config& cfg, double strategy_val, const std::map<std::string, double>& prices,
                      std::optional<double> voo_price) {
    Baseline b = load_or_create_baseline(cfg, strategy_val, prices, voo_price);
    Journal j;
    j.bnh = benchmark_value(b, prices);
    const double alpha_usdt = strategy_val - j.bnh;
    j.alpha_pct = j.bnh != 0.0 ? ((strategy_val / j.bnh) - 1.0) * 100.0 : 0.0;
    if (b.has_voo && voo_price) j.voo_value = b.voo_capital * (*voo_price) / b.voo_start;

    const fs::path path = cfg.state_dir / "alpha_performance.csv";
    ensure_perf_schema(path);
    const bool exists = fs::exists(path);
    std::ofstream out(path, std::ios::app);
    if (!exists) out << "Timestamp,Active_Bot_Value,Benchmark_BnH_Value,Alpha_USDT,Alpha_Percent,VOO_Hold_Value\n";
    out << cfg.now << "," << fixed2(strategy_val) << "," << fixed2(j.bnh) << "," << fixed2(alpha_usdt) << "," << fixed2(j.alpha_pct)
        << "," << (j.voo_value ? fixed2(*j.voo_value) : std::string()) << "\n";
    return j;
}

void log_trades(const Config& cfg, const std::map<std::string, double>& old_qty, const std::map<std::string, double>& new_qty,
                const std::map<std::string, double>& trade_values, const std::map<std::string, double>& prices) {
    std::set<std::string> assets;
    for (const auto& kv : old_qty) assets.insert(kv.first);
    for (const auto& kv : new_qty) assets.insert(kv.first);

    std::ostringstream rows;
    for (const auto& a : assets) {
        const double o = old_qty.count(a) ? old_qty.at(a) : 0.0;
        const double n = new_qty.count(a) ? new_qty.at(a) : 0.0;
        const double delta = n - o;
        if (std::fabs(delta) < 1e-12) continue;
        const double tv = trade_values.count(a) ? trade_values.at(a) : 0.0;
        rows << cfg.now << "," << a << "," << (delta > 0 ? "BUY" : "SELL") << "," << num(std::fabs(delta)) << ","
             << num(prices.at(a)) << "," << num(FEE_RATE * std::fabs(tv)) << "\n";
    }
    if (rows.str().empty()) return;
    const fs::path path = cfg.state_dir / "trades.csv";
    const bool exists = fs::exists(path);
    std::ofstream out(path, std::ios::app);
    if (!exists) out << "Timestamp,Asset,Side,Quantity,Price,Fee_USDT\n";
    out << rows.str();
}

// ---------------------------------------------------------------- execution
void execute_trading_cycle(const Config& cfg) {
    const Prices px = get_live_prices(cfg);
    const std::vector<Row> ledger = load_ledger(cfg);

    std::map<std::string, double> holdings, holding_values;
    double cash = 0, total = 0;
    std::vector<std::string> missing;
    for (const auto& r : ledger) {
        if (r.type == "CASH") cash += r.quantity;
        else if (r.type == "CRYPTO" && !px.current.count(r.asset)) missing.push_back(r.asset);
    }
    if (!missing.empty()) {
        std::cout << "⚠️ No price for held asset(s) ";
        for (const auto& m : missing) std::cout << m << " ";
        std::cout << ". Skipping this cycle; positions unchanged.\n";
        return;
    }
    for (const auto& r : ledger) {
        if (r.type != "CRYPTO") continue;
        holdings[r.asset] = r.quantity;
        holding_values[r.asset] = r.quantity * px.current.at(r.asset);
    }
    total = cash;
    for (const auto& kv : holding_values) total += kv.second;

    std::optional<double> voo_price;
    {
        Series voo = load_series(cfg, VOO_TICKER);
        if (!voo.empty()) voo_price = voo.close.back();
        else std::cout << "⚠️ No VOO data; skipping the VOO comparison this cycle.\n";
    }
    const Journal jr = journal_alpha(cfg, total, px.current, voo_price);
    char alpha_buf[64];
    std::snprintf(alpha_buf, sizeof alpha_buf, "%+.2f", jr.alpha_pct);
    std::cout << "\n==================================================\n"
              << "💳 Active Bot Valuation:  $" << money(total) << " USDT\n"
              << "📉 Benchmark Buy & Hold:  $" << money(jr.bnh) << " USDT\n"
              << "🏆 Alpha Outperformance:  " << alpha_buf << "%\n";
    if (jr.voo_value) {
        char vs[64];
        std::snprintf(vs, sizeof vs, "%+.2f", (total / *jr.voo_value - 1.0) * 100.0);
        std::cout << "📈 Hold VOO (S&P 500):    $" << money(*jr.voo_value) << "  (bot vs VOO: " << vs << "%)\n";
    }
    std::cout << "==================================================\n\n";

    bool is_bull = false;
    try {
        is_bull = calculate_macro_regime(cfg);
    } catch (const std::exception& e) {
        std::cout << "⚠️ Regime check failed (" << e.what() << "). Holding current positions; no trades this cycle.\n";
        return;
    }

    // Desired portfolio: equal weight in the top picks if risk-on, otherwise all cash.
    std::vector<std::string> picks;  // ordered, like the Python dict
    std::map<std::string, double> target_values;
    if (is_bull && !px.top3.empty()) {
        picks = px.top3;
        for (const auto& c : picks) target_values[c] = total / static_cast<double>(picks.size());
    }

    // Decide whether a trade is worth making at all.
    if (target_values.empty()) {
        if (holdings.empty()) {
            std::cout << "😴 Risk-off and already in cash. No trade.\n";
            return;
        }
    } else {
        std::set<std::string> held, want;
        for (const auto& kv : holdings) held.insert(kv.first);
        for (const auto& kv : target_values) want.insert(kv.first);
        if (held == want) {
            const double n = static_cast<double>(target_values.size());
            double drift = cash / total;
            for (const auto& kv : holding_values) drift = std::max(drift, std::fabs(kv.second / total - 1.0 / n));
            if (drift <= REBALANCE_DRIFT) {
                std::printf("✅ Same picks, max weight drift %.1f%% <= %.0f%%. No trade.\n", drift * 100.0, REBALANCE_DRIFT * 100.0);
                return;
            }
        }
    }

    // Execute: charge FEE_RATE on every dollar bought or sold.
    std::set<std::string> assets;
    for (const auto& kv : target_values) assets.insert(kv.first);
    for (const auto& kv : holdings) assets.insert(kv.first);
    std::map<std::string, double> trade_values;
    double notional = 0;
    for (const auto& a : assets) {
        const double t = target_values.count(a) ? target_values.at(a) : 0.0;
        const double h = holding_values.count(a) ? holding_values.at(a) : 0.0;
        trade_values[a] = t - h;
        notional += std::fabs(t - h);
    }
    const double fee = FEE_RATE * notional;
    const double net_value = total - fee;

    std::vector<Row> new_rows;
    std::map<std::string, double> new_qty;
    if (!picks.empty()) {
        const double slot = net_value / static_cast<double>(picks.size());
        for (const auto& coin : picks) {
            const double price = px.current.at(coin);
            new_qty[coin] = slot / price;
            new_rows.push_back({coin, slot / price, price, slot, "CRYPTO"});
        }
    } else {
        new_rows.push_back({"USDT", net_value, 1.0, net_value, "CASH"});
    }

    log_trades(cfg, holdings, new_qty, trade_values, px.current);
    save_ledger(cfg, new_rows);

    std::string names;
    for (const auto& c : picks) names += (names.empty() ? "" : ", ") + c;
    std::cout << "🔁 Rebalanced into [" << (names.empty() ? "CASH" : names) << "]; fees paid: $" << money(fee) << "\n";
}

}  // namespace

int main(int argc, char** argv) {
    Config cfg;
    for (int i = 1; i < argc; ++i) {
        const std::string a = argv[i];
        auto next = [&]() -> std::string {
            if (i + 1 >= argc) { std::cerr << "missing value for " << a << "\n"; std::exit(2); }
            return argv[++i];
        };
        if (a == "--data-dir") cfg.data_dir = next();
        else if (a == "--state-dir") cfg.state_dir = next();
        else if (a == "--now") cfg.now = next();
        else { std::cerr << "usage: quantbot [--data-dir DIR] [--state-dir DIR] [--now 'YYYY-MM-DD HH:MM:SS']\n"; return 2; }
    }
    if (cfg.now.empty()) cfg.now = now_utc();
    std::cout << "⏰ Execution Time: " << cfg.now << " UTC\n";
    try {
        execute_trading_cycle(cfg);
    } catch (const std::exception& e) {
        std::cerr << "❌ Fatal: " << e.what() << "\n";
        return 1;
    }
    return 0;
}
