// quantbot_v2.cpp - strategy v2 (C++17). v1 (cpp/quantbot.cpp) is untouched and keeps running.
//
// v2 is a response to the weaknesses found in the v1 review. One line per weakness:
//   flips too easily    -> BTC trend score of 3 parts, a +/-1.5% band on the slow EMA, and a regime change has to
//                          hold for 2 closed daily bars before it counts
//   slow to re-enter    -> graded exposure (0% / 50% / 100%) instead of all-or-nothing
//   macro votes         -> dropped: unproven, and four more ways for a data feed to fail
//   three picks, one bet-> BTC joins the universe; top 4 coins; inverse-volatility weights; 35% cap per coin
//   no risk limits      -> per-coin trend filter, volatility targeting, portfolio drawdown circuit breaker
//   guessed numbers     -> every parameter below was fixed BEFORE the first backtest was run
//                          (`--set name=value` only exists so tests and the sensitivity table can vary them)
//   partial-day candle  -> signals use CLOSED daily bars only; a run on the same bar again only marks to market
//
// One decision path (process_bar) serves both modes, so a backtest tests exactly the code that trades live.
//
// Live    : quantbot_v2 --data-dir data_v2 --state-dir v2 [--now "YYYY-MM-DD HH:MM:SS"] [--capital 100000]
// Backtest: quantbot_v2 --backtest --prices backtest/prices.csv --start 2021-02-15 --out results_v2.csv
//                       [--end D] [--capital 100000] [--trades-out FILE] [--set name=value ...]
//
// Build: g++ -std=c++17 -O2 -Wall -Wextra -Wpedantic -o quantbot_v2 cpp/quantbot_v2.cpp

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
#include <limits>
#include <map>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace fs = std::filesystem;

namespace {

const double NaN = std::numeric_limits<double>::quiet_NaN();

// ---------------------------------------------------------------- parameters (fixed before any backtest)
struct Params {
    int window = 200;          // signals only look at the last 200 closed bars, so live and backtest agree exactly
    int min_bars = 100;        // a coin needs this much history before it can be traded
    int ema_fast = 20;
    int ema_slow = 50;
    double band = 0.015;       // BTC must clear the slow EMA by 1.5% to turn the price test on, and fall 1.5% below to turn it off
    int mom_days = 60;         // momentum look-back, also the BTC return test
    int confirm_bars = 2;      // a new regime must hold for this many closed daily bars
    int top_k = 4;
    int vol_days = 30;
    double cap = 0.35;         // max weight of one coin before exposure scaling
    double target_vol = 0.60;  // annualised; exposure is scaled down when the picks are more volatile than this
    double dd_limit = 0.12;    // circuit breaker: go to cash after a 12% fall from the equity peak
    int cooldown = 5;          // bars in cash at least, after the breaker trips
    double drift = 0.05;       // rebalance an unchanged pick set only if a weight is more than 5 points off target
    double fee = 0.001;        // 0.10% of traded value, same assumption as v1
};
Params P;

bool set_param(const std::string& k, const std::string& v) {
    static const std::map<std::string, int*> ints = {
        {"window", &P.window}, {"min_bars", &P.min_bars}, {"ema_fast", &P.ema_fast}, {"ema_slow", &P.ema_slow},
        {"mom_days", &P.mom_days}, {"confirm_bars", &P.confirm_bars}, {"top_k", &P.top_k},
        {"vol_days", &P.vol_days}, {"cooldown", &P.cooldown}};
    static const std::map<std::string, double*> dbls = {
        {"band", &P.band}, {"cap", &P.cap}, {"target_vol", &P.target_vol}, {"dd_limit", &P.dd_limit},
        {"drift", &P.drift}, {"fee", &P.fee}};
    if (auto it = ints.find(k); it != ints.end()) { *it->second = std::stoi(v); return true; }
    if (auto it = dbls.find(k); it != dbls.end()) { *it->second = std::stod(v); return true; }
    return false;
}

// The coins v2 may hold. BTC must be in the list because its trend drives the regime. `--universe` replaces it
// (used by the backtest to run on a frozen, hindsight-free list of coins).
std::vector<std::string> UNIVERSE = {"BTC-USD", "ETH-USD", "SOL-USD", "LINK-USD",
                                     "AVAX-USD", "NEAR-USD", "ADA-USD", "DOT-USD"};
const std::string BTC = "BTC-USD";

// ---------------------------------------------------------------- small helpers
std::string trim(std::string s) {
    while (!s.empty() && std::isspace(static_cast<unsigned char>(s.back()))) s.pop_back();
    std::size_t i = 0;
    while (i < s.size() && std::isspace(static_cast<unsigned char>(s[i]))) ++i;
    return s.substr(i);
}

std::vector<std::string> split_csv(const std::string& line) {  // keeps empty cells
    std::vector<std::string> out;
    std::string cur;
    for (char c : line) {
        if (c == ',') { out.push_back(trim(cur)); cur.clear(); }
        else cur += c;
    }
    out.push_back(trim(cur));
    return out;
}

std::string num(double v) {
    std::ostringstream o;
    o << std::setprecision(17) << v;
    return o.str();
}

std::string money(double v) {
    char b[64];
    std::snprintf(b, sizeof b, "%.2f", v);
    std::string s = b, sign;
    if (s[0] == '-') { sign = "-"; s.erase(0, 1); }
    const std::size_t dot = s.find('.');
    for (int i = static_cast<int>(dot) - 3; i > 0; i -= 3) s.insert(static_cast<std::size_t>(i), ",");
    return sign + s;
}

std::string safe_name(std::string t) {
    for (char& c : t)
        if (!(std::isalnum(static_cast<unsigned char>(c)) || c == '.' || c == '_' || c == '-')) c = '_';
    return t;
}

std::string now_utc() {
    std::time_t t = std::time(nullptr);
    std::tm tm{};
    gmtime_r(&t, &tm);
    char buf[32];
    std::strftime(buf, sizeof buf, "%Y-%m-%d %H:%M:%S", &tm);
    return buf;
}

long day_number(const std::string& d) {  // "YYYY-MM-DD" -> days since 1970-01-01
    std::tm tm{};
    tm.tm_year = std::stoi(d.substr(0, 4)) - 1900;
    tm.tm_mon = std::stoi(d.substr(5, 2)) - 1;
    tm.tm_mday = std::stoi(d.substr(8, 2));
    return static_cast<long>(timegm(&tm) / 86400);
}

// ---------------------------------------------------------------- market data panel
// One row per calendar date. A coin's value is NaN before it has any price and forward-filled afterwards.
using Raw = std::map<std::string, std::map<std::string, double>>;  // ticker -> date -> close

struct Panel {
    std::vector<std::string> dates;
    std::map<std::string, std::vector<double>> px;
    std::map<std::string, int> first;  // index of the first valid value
    bool has(const std::string& t) const { return px.count(t) > 0; }
    int size() const { return static_cast<int>(dates.size()); }
};

Panel build_panel(const Raw& raw) {
    Panel pn;
    std::set<std::string> ds;
    for (const auto& t : UNIVERSE) {
        auto it = raw.find(t);
        if (it == raw.end()) continue;
        for (const auto& kv : it->second) ds.insert(kv.first);
    }
    pn.dates.assign(ds.begin(), ds.end());
    const std::size_t n = pn.dates.size();
    for (const auto& t : UNIVERSE) {
        auto it = raw.find(t);
        if (it == raw.end() || it->second.empty()) continue;
        std::vector<double> v(n, NaN);
        int first = -1;
        double last = NaN;
        for (std::size_t i = 0; i < n; ++i) {
            auto f = it->second.find(pn.dates[i]);
            if (f != it->second.end()) {
                last = f->second;
                if (first < 0) first = static_cast<int>(i);
            }
            if (first >= 0) v[i] = last;  // forward fill after the first price
        }
        pn.px[t] = v;
        pn.first[t] = first;
    }
    return pn;
}

bool parse_price(const std::string& s, double& out) {
    if (s.empty()) return false;
    char* end = nullptr;
    const double v = std::strtod(s.c_str(), &end);
    if (end == s.c_str() || std::isnan(v) || v <= 0) return false;  // zero/negative prints are bad data
    out = v;
    return true;
}

Raw load_wide(const fs::path& path) {  // backtest/prices.csv: Date,<ticker>,<ticker>,...
    Raw raw;
    std::ifstream in(path);
    if (!in) throw std::runtime_error("cannot open " + path.string());
    std::string line;
    std::getline(in, line);
    const auto header = split_csv(line);
    while (std::getline(in, line)) {
        if (trim(line).empty()) continue;
        const auto f = split_csv(line);
        for (std::size_t c = 1; c < header.size() && c < f.size(); ++c) {
            double v;
            if (parse_price(f[c], v)) raw[header[c]][f[0]] = v;
        }
    }
    return raw;
}

Raw load_dir(const fs::path& dir) {  // data_v2/<ticker>.csv: Date,Close
    Raw raw;
    for (const auto& t : UNIVERSE) {
        std::ifstream in(dir / (safe_name(t) + ".csv"));
        if (!in) continue;
        std::string line;
        std::getline(in, line);
        while (std::getline(in, line)) {
            const auto f = split_csv(line);
            double v;
            if (f.size() >= 2 && parse_price(f[1], v)) raw[t][f[0]] = v;
        }
    }
    return raw;
}

// ---------------------------------------------------------------- indicators (all use only the last P.window bars)
double ema_window(const std::vector<double>& v, int i, int first, int span) {
    const int lo = std::max(first, i - P.window + 1);
    const double a = 2.0 / (span + 1.0);
    double y = v[static_cast<std::size_t>(lo)];
    for (int k = lo + 1; k <= i; ++k) y = a * v[static_cast<std::size_t>(k)] + (1.0 - a) * y;
    return y;
}

double stdev(const std::vector<double>& x) {  // sample standard deviation (n-1)
    const double n = static_cast<double>(x.size());
    if (x.size() < 2) return 0.0;
    double m = 0;
    for (double a : x) m += a;
    m /= n;
    double s = 0;
    for (double a : x) s += (a - m) * (a - m);
    return std::sqrt(s / (n - 1.0));
}

// Scale weights (sum 1) so none exceeds `cap`; the excess goes to the others in proportion.
// If every coin is capped the weights sum to less than 1 and the rest stays in cash.
void apply_cap(std::vector<double>& w, double cap) {
    const std::size_t n = w.size();
    std::vector<bool> capped(n, false);
    for (std::size_t it = 0; it <= n; ++it) {
        double over = 0;
        for (std::size_t i = 0; i < n; ++i)
            if (!capped[i] && w[i] > cap + 1e-15) { over += w[i] - cap; w[i] = cap; capped[i] = true; }
        if (over < 1e-15) break;
        double free_sum = 0;
        for (std::size_t i = 0; i < n; ++i) if (!capped[i]) free_sum += w[i];
        if (free_sum <= 0) break;
        for (std::size_t i = 0; i < n; ++i) if (!capped[i]) w[i] += over * w[i] / free_sum;
    }
}

// ---------------------------------------------------------------- strategy state
struct State {
    std::string last_bar;        // date of the last closed bar processed (empty = never)
    int regime = 0;              // confirmed exposure state: 0 OFF, 1 HALF, 2 ON
    int pending = -1;            // candidate state waiting for confirmation
    int pending_n = 0;
    int c1 = -1;                 // latch of the band price test: -1 unknown, 0 off, 1 on
    double peak = 0;             // equity peak used by the circuit breaker
    bool breaker = false;
    int cooldown_left = 0;
    double cash = 0;
    std::map<std::string, double> qty;
    double fees = 0, notional = 0;
    long trades = 0;
    double capital0 = 0;
    std::string started_at;
};

struct TradeRow {
    std::string stamp, asset, side;
    double qty, price, fee;
};

struct Snapshot {
    double equity = 0, invested = 0, max_weight = 0;
    int n_positions = 0;
};

double price_at(const Panel& pn, const std::string& t, int i) { return pn.px.at(t)[static_cast<std::size_t>(i)]; }

Snapshot snapshot(const State& s, const Panel& pn, int pi) {
    Snapshot sn;
    double invested = 0, maxv = 0;
    for (const auto& kv : s.qty) {
        const double v = kv.second * price_at(pn, kv.first, pi);
        if (kv.second > 1e-12) { invested += v; maxv = std::max(maxv, v); ++sn.n_positions; }
    }
    sn.equity = s.cash + invested;
    sn.invested = sn.equity > 0 ? invested / sn.equity : 0;
    sn.max_weight = sn.equity > 0 ? maxv / sn.equity : 0;
    return sn;
}

struct Decision {
    std::vector<std::string> picks;
    bool traded = false;
    double fee = 0, vol_scale = 1, exposure = 0;
};

// Handle ONE closed daily bar. `ci` = index of the closed bar the signals use; `pi` = index of the price used to
// value and trade (equal to ci in a backtest; the latest live price in a live run).
Decision process_bar(State& s, const Panel& pn, int ci, int pi, const std::string& stamp, std::vector<TradeRow>* trades) {
    Decision d;
    double equity = s.cash;
    for (const auto& kv : s.qty) equity += kv.second * price_at(pn, kv.first, pi);

    // ---- 1. BTC trend score -> raw exposure state
    int raw_state = 0;
    if (pn.has(BTC)) {
        const auto& b = pn.px.at(BTC);
        const int bf = pn.first.at(BTC);
        if (ci - bf + 1 >= P.min_bars && ci - P.mom_days >= bf) {
            const double c = b[static_cast<std::size_t>(ci)];
            const double es = ema_window(b, ci, bf, P.ema_slow), ef = ema_window(b, ci, bf, P.ema_fast);
            if (c > es * (1.0 + P.band)) s.c1 = 1;
            else if (c < es * (1.0 - P.band)) s.c1 = 0;
            else if (s.c1 < 0) s.c1 = c > es ? 1 : 0;  // inside the band: keep the previous answer
            const int c2 = ef > es ? 1 : 0;
            const int c3 = (c / b[static_cast<std::size_t>(ci - P.mom_days)] - 1.0 > 0) ? 1 : 0;
            const int score = s.c1 + c2 + c3;
            raw_state = score == 3 ? 2 : (score == 2 ? 1 : 0);
        }
    }

    // ---- 2. confirmation: a new state must hold for confirm_bars closed bars
    if (s.last_bar.empty()) {
        s.regime = raw_state; s.pending = -1; s.pending_n = 0;
    } else if (raw_state == s.regime) {
        s.pending = -1; s.pending_n = 0;
    } else {
        if (raw_state == s.pending) ++s.pending_n;
        else { s.pending = raw_state; s.pending_n = 1; }
        if (s.pending_n >= P.confirm_bars) { s.regime = s.pending; s.pending = -1; s.pending_n = 0; }
    }

    // ---- 3. circuit breaker
    if (!s.breaker) {
        s.peak = std::max(s.peak, equity);
        if (equity <= s.peak * (1.0 - P.dd_limit)) { s.breaker = true; s.cooldown_left = P.cooldown; }
    } else {
        if (s.cooldown_left > 0) --s.cooldown_left;
        if (s.cooldown_left == 0 && s.regime != 0) { s.breaker = false; s.peak = equity; }
    }

    // ---- 4. target weights
    std::map<std::string, double> tw;
    if (!s.breaker && s.regime > 0) {
        struct Cand { std::string t; double mom; };
        std::vector<Cand> cands;
        for (const auto& t : UNIVERSE) {
            if (!pn.has(t)) continue;
            const int f = pn.first.at(t);
            if (ci - f + 1 < P.min_bars || ci - std::max(P.mom_days, P.vol_days) < f) continue;
            const auto& v = pn.px.at(t);
            const double c = v[static_cast<std::size_t>(ci)];
            const double mom = c / v[static_cast<std::size_t>(ci - P.mom_days)] - 1.0;
            if (mom > 0 && c > ema_window(v, ci, f, P.ema_slow)) cands.push_back({t, mom});
        }
        std::stable_sort(cands.begin(), cands.end(), [](const Cand& a, const Cand& b) { return a.mom > b.mom; });
        if (static_cast<int>(cands.size()) > P.top_k) cands.resize(static_cast<std::size_t>(P.top_k));

        if (!cands.empty()) {
            const std::size_t n = cands.size();
            std::vector<std::vector<double>> rets(n);
            std::vector<double> w(n);
            double wsum = 0;
            for (std::size_t j = 0; j < n; ++j) {
                const auto& v = pn.px.at(cands[j].t);
                for (int k = ci - P.vol_days + 1; k <= ci; ++k)
                    rets[j].push_back(v[static_cast<std::size_t>(k)] / v[static_cast<std::size_t>(k - 1)] - 1.0);
                w[j] = 1.0 / std::max(stdev(rets[j]), 1e-6);
                wsum += w[j];
            }
            for (double& x : w) x /= wsum;
            apply_cap(w, P.cap);

            std::vector<double> port;
            for (int k = 0; k < P.vol_days; ++k) {
                double r = 0;
                for (std::size_t j = 0; j < n; ++j) r += w[j] * rets[j][static_cast<std::size_t>(k)];
                port.push_back(r);
            }
            const double pvol = stdev(port) * std::sqrt(365.0);
            d.vol_scale = pvol > 0 ? std::min(1.0, P.target_vol / pvol) : 1.0;
            d.exposure = (s.regime == 2 ? 1.0 : 0.5) * d.vol_scale;
            for (std::size_t j = 0; j < n; ++j) {
                tw[cands[j].t] = w[j] * d.exposure;
                d.picks.push_back(cands[j].t);
            }
        }
    }

    // ---- 5. trade only if the picks changed or a weight drifted
    std::map<std::string, double> hv;
    std::set<std::string> held, want, all;
    for (const auto& kv : s.qty) {
        hv[kv.first] = kv.second * price_at(pn, kv.first, pi);
        if (kv.second > 1e-12) held.insert(kv.first);
        all.insert(kv.first);
    }
    for (const auto& kv : tw) if (kv.second > 1e-12) { want.insert(kv.first); all.insert(kv.first); }
    bool trade = held != want;
    if (!trade && equity > 0) {
        double dmax = 0;
        for (const auto& t : all) {
            const double cur = hv.count(t) ? hv[t] / equity : 0.0;
            const double tgt = tw.count(t) ? tw[t] : 0.0;
            dmax = std::max(dmax, std::fabs(cur - tgt));
        }
        trade = dmax > P.drift;
    }

    if (trade) {
        double notional = 0;
        for (const auto& t : all) {
            const double tgt = tw.count(t) ? tw[t] * equity : 0.0;
            notional += std::fabs(tgt - (hv.count(t) ? hv[t] : 0.0));
        }
        const double fee = P.fee * notional;
        const double net = equity - fee;
        std::map<std::string, double> newq;
        double invested = 0;
        for (const auto& t : want) {
            const double val = tw[t] * net;
            newq[t] = val / price_at(pn, t, pi);
            invested += val;
        }
        for (const auto& t : all) {
            const double oldq = s.qty.count(t) ? s.qty[t] : 0.0;
            const double nq = newq.count(t) ? newq[t] : 0.0;
            if (std::fabs(nq - oldq) < 1e-12) continue;
            const double tgt = tw.count(t) ? tw[t] * equity : 0.0;
            const double tfee = P.fee * std::fabs(tgt - (hv.count(t) ? hv[t] : 0.0));
            if (trades) trades->push_back({stamp, t, nq > oldq ? "BUY" : "SELL", std::fabs(nq - oldq), price_at(pn, t, pi), tfee});
            ++s.trades;
        }
        s.qty = newq;
        s.cash = net - invested;
        s.fees += fee;
        s.notional += notional;
        d.traded = true;
        d.fee = fee;
    }
    s.last_bar = pn.dates[static_cast<std::size_t>(ci)];
    return d;
}

// ---------------------------------------------------------------- output helpers
const char* HEADER_TAIL = ",V2_Value,Invested_Pct,Regime,Breaker,Fees_Cum,Notional_Cum,Trades_Cum,Max_Weight,N_Positions\n";

std::string row_tail(const State& s, const Snapshot& sn) {
    std::ostringstream o;
    o << num(sn.equity) << "," << num(sn.invested * 100.0) << "," << s.regime << "," << (s.breaker ? 1 : 0) << ","
      << num(s.fees) << "," << num(s.notional) << "," << s.trades << "," << num(sn.max_weight) << "," << sn.n_positions << "\n";
    return o.str();
}

void write_trades(const fs::path& path, const std::vector<TradeRow>& rows, bool append) {
    if (rows.empty() && append) return;
    const bool exists = fs::exists(path);
    std::ofstream out(path, append ? std::ios::app : std::ios::trunc);
    if (!append || !exists) out << "Timestamp,Asset,Side,Quantity,Price,Fee_USDT\n";
    for (const auto& r : rows)
        out << r.stamp << "," << r.asset << "," << r.side << "," << num(r.qty) << "," << num(r.price) << "," << num(r.fee) << "\n";
}

// ---------------------------------------------------------------- live state files (plain key=value)
void save_state(const fs::path& dir, const State& s) {
    std::ofstream o(dir / "state.txt");
    o << "last_bar=" << s.last_bar << "\nregime=" << s.regime << "\npending=" << s.pending << "\npending_n=" << s.pending_n
      << "\nc1=" << s.c1 << "\npeak=" << num(s.peak) << "\nbreaker=" << (s.breaker ? 1 : 0)
      << "\ncooldown_left=" << s.cooldown_left << "\ncash=" << num(s.cash) << "\nfees=" << num(s.fees)
      << "\nnotional=" << num(s.notional) << "\ntrades=" << s.trades << "\ncapital0=" << num(s.capital0)
      << "\nstarted_at=" << s.started_at << "\n";
    for (const auto& kv : s.qty) o << "q:" << kv.first << "=" << num(kv.second) << "\n";
}

bool load_state(const fs::path& dir, State& s) {
    std::ifstream in(dir / "state.txt");
    if (!in) return false;
    std::string line;
    while (std::getline(in, line)) {
        const auto eq = line.find('=');
        if (eq == std::string::npos) continue;
        const std::string k = line.substr(0, eq), v = line.substr(eq + 1);
        if (k == "last_bar") s.last_bar = v;
        else if (k == "regime") s.regime = std::stoi(v);
        else if (k == "pending") s.pending = std::stoi(v);
        else if (k == "pending_n") s.pending_n = std::stoi(v);
        else if (k == "c1") s.c1 = std::stoi(v);
        else if (k == "peak") s.peak = std::stod(v);
        else if (k == "breaker") s.breaker = v == "1";
        else if (k == "cooldown_left") s.cooldown_left = std::stoi(v);
        else if (k == "cash") s.cash = std::stod(v);
        else if (k == "fees") s.fees = std::stod(v);
        else if (k == "notional") s.notional = std::stod(v);
        else if (k == "trades") s.trades = std::stol(v);
        else if (k == "capital0") s.capital0 = std::stod(v);
        else if (k == "started_at") s.started_at = v;
        else if (k.rfind("q:", 0) == 0) s.qty[k.substr(2)] = std::stod(v);
    }
    return true;
}

void save_ledger(const fs::path& dir, const State& s, const Panel& pn, int pi) {
    std::ofstream o(dir / "ledger.csv");
    o << "Asset,Quantity,Price,Value\n";
    o << "USDT," << num(s.cash) << ",1," << num(s.cash) << "\n";
    for (const auto& kv : s.qty) {
        const double p = price_at(pn, kv.first, pi);
        o << kv.first << "," << num(kv.second) << "," << num(p) << "," << num(kv.second * p) << "\n";
    }
}

// ---------------------------------------------------------------- modes
struct Config {
    bool backtest = false;
    fs::path data_dir = "data_v2", state_dir = "v2", prices, out, trades_out;
    std::string now, start, end;
    double capital = 100000.0;
};

int run_backtest(const Config& cfg) {
    if (cfg.prices.empty() || cfg.out.empty() || cfg.start.empty()) {
        std::cerr << "backtest needs --prices, --start and --out\n";
        return 2;
    }
    const Panel pn = build_panel(load_wide(cfg.prices));
    if (!pn.has(BTC)) { std::cerr << "no BTC-USD in the price file\n"; return 1; }
    State s;
    s.cash = s.capital0 = cfg.capital;
    std::vector<TradeRow> trades;
    std::ofstream out(cfg.out);
    out << "Date" << HEADER_TAIL;
    int rows = 0;
    for (int i = 0; i < pn.size(); ++i) {
        const std::string& d = pn.dates[static_cast<std::size_t>(i)];
        if (d < cfg.start || (!cfg.end.empty() && d > cfg.end)) continue;
        process_bar(s, pn, i, i, d, &trades);
        out << d << "," << row_tail(s, snapshot(s, pn, i));
        ++rows;
    }
    if (!cfg.trades_out.empty()) write_trades(cfg.trades_out, trades, false);
    std::printf("backtest: %d days, final value %s, fees paid %s, %ld trades\n", rows,
                money(snapshot(s, pn, pn.size() - 1).equity).c_str(), money(s.fees).c_str(), s.trades);
    return rows > 0 ? 0 : 1;
}

int run_live(const Config& cfg) {
    const Panel pn = build_panel(load_dir(cfg.data_dir));
    std::printf("v2 run %s UTC\n", cfg.now.c_str());
    if (pn.size() == 0) { std::printf("No price data in %s. Nothing done.\n", cfg.data_dir.string().c_str()); return 0; }
    const std::string today = cfg.now.substr(0, 10);
    int ci = -1;
    for (int i = pn.size() - 1; i >= 0; --i)
        if (pn.dates[static_cast<std::size_t>(i)] < today) { ci = i; break; }
    const int pi = pn.size() - 1;  // latest price, possibly today's unfinished candle: used to value and trade only

    fs::create_directories(cfg.state_dir);
    State s;
    const bool fresh = !load_state(cfg.state_dir, s);
    if (fresh) { s.cash = s.capital0 = cfg.capital; s.started_at = cfg.now; }
    for (const auto& kv : s.qty)
        if (kv.second > 1e-12 && !pn.has(kv.first)) {
            std::printf("No price data for held asset %s. Skipping this run; positions unchanged.\n", kv.first.c_str());
            return 0;
        }

    std::vector<TradeRow> trades;
    if (ci < 0 || !pn.has(BTC)) {
        std::printf("No closed daily bars or no BTC-USD data. Marking to market only.\n");
    } else if (day_number(today) - day_number(pn.dates[static_cast<std::size_t>(ci)]) > 3) {
        std::printf("Newest closed bar is %s, too old. No decisions this run (marking to market only).\n",
                    pn.dates[static_cast<std::size_t>(ci)].c_str());
    } else if (s.last_bar == pn.dates[static_cast<std::size_t>(ci)]) {
        std::printf("Closed bar %s already processed. Marking to market only.\n", s.last_bar.c_str());
    } else {
        const Decision d = process_bar(s, pn, ci, pi, cfg.now, &trades);
        static const char* names[] = {"OFF", "HALF", "ON"};
        std::string picks;
        for (const auto& p : d.picks) picks += (picks.empty() ? "" : ", ") + p;
        std::printf("Bar %s: regime %s, breaker %s, picks [%s], exposure %.0f%%\n", s.last_bar.c_str(), names[s.regime],
                    s.breaker ? "ON" : "off", picks.empty() ? "none" : picks.c_str(), d.exposure * 100.0);
        if (d.traded) std::printf("Rebalanced; fees paid $%s\n", money(d.fee).c_str());
        else std::printf("No trade (same picks, weights within %.0f points).\n", P.drift * 100.0);
    }

    const Snapshot sn = snapshot(s, pn, pi);
    std::printf("Value $%s, invested %.1f%%\n", money(sn.equity).c_str(), sn.invested * 100.0);

    const fs::path eq = cfg.state_dir / "equity.csv";
    const bool exists = fs::exists(eq);
    std::ofstream out(eq, std::ios::app);
    if (!exists) out << "Timestamp" << HEADER_TAIL;
    out << cfg.now << "," << row_tail(s, sn);
    out.close();
    write_trades(cfg.state_dir / "trades.csv", trades, true);
    save_ledger(cfg.state_dir, s, pn, pi);
    save_state(cfg.state_dir, s);
    return 0;
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
        if (a == "--backtest") cfg.backtest = true;
        else if (a == "--data-dir") cfg.data_dir = next();
        else if (a == "--state-dir") cfg.state_dir = next();
        else if (a == "--prices") cfg.prices = next();
        else if (a == "--out") cfg.out = next();
        else if (a == "--trades-out") cfg.trades_out = next();
        else if (a == "--now") cfg.now = next();
        else if (a == "--start") cfg.start = next();
        else if (a == "--end") cfg.end = next();
        else if (a == "--capital") cfg.capital = std::stod(next());
        else if (a == "--universe") {
            UNIVERSE = split_csv(next());
            if (std::find(UNIVERSE.begin(), UNIVERSE.end(), BTC) == UNIVERSE.end()) {
                std::cerr << "--universe must include BTC-USD (its trend drives the regime)\n";
                return 2;
            }
        } else if (a == "--set") {
            const std::string kv = next();
            const auto eq = kv.find('=');
            if (eq == std::string::npos || !set_param(kv.substr(0, eq), kv.substr(eq + 1))) {
                std::cerr << "unknown parameter in --set " << kv << "\n";
                return 2;
            }
        } else {
            std::cerr << "usage: quantbot_v2 [--backtest --prices F --start D --out F] [--data-dir D --state-dir D --now T]"
                         " [--capital X] [--end D] [--trades-out F] [--universe A,B,..] [--set name=value]\n";
            return 2;
        }
    }
    if (cfg.now.empty()) cfg.now = now_utc();
    try {
        return cfg.backtest ? run_backtest(cfg) : run_live(cfg);
    } catch (const std::exception& e) {
        std::cerr << "Fatal: " << e.what() << "\n";
        return 1;
    }
}
