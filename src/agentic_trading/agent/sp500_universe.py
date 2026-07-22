"""
S&P 500 ticker universe for scan → deep-research.

Primary: fetch current constituents (network).
Fallback: embedded liquid large-cap sample (offline / tests).
Russell 3000 is intentionally not supported as a trade scan universe.
"""

from __future__ import annotations

import csv
import io
import json
import urllib.error
import urllib.request
from pathlib import Path

# Liquid large-cap sample used when network fetch fails (tests / offline).
# Not a full index — full list comes from remote constituents when available.
SP500_FALLBACK: list[str] = [
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "GOOG", "BRK-B", "AVGO", "TSLA",
    "JPM", "LLY", "V", "UNH", "XOM", "MA", "COST", "JNJ", "PG", "HD",
    "ABBV", "NFLX", "BAC", "CRM", "KO", "CVX", "MRK", "AMD", "PEP", "TMO",
    "WMT", "CSCO", "ACN", "LIN", "MCD", "ABT", "WFC", "IBM", "GE", "CAT",
    "PM", "QCOM", "TXN", "INTU", "AMAT", "ISRG", "DIS", "VZ", "CMCSA", "NOW",
    "AXP", "MS", "PFE", "T", "NEE", "UBER", "GS", "RTX", "SPGI", "PGR",
    "BKNG", "LOW", "UNP", "HON", "BLK", "SYK", "ELV", "TJX", "VRTX", "C",
    "ADP", "PLTR", "PANW", "ADI", "LRCX", "DE", "MDT", "SBUX", "BMY", "MMC",
    "GILD", "CB", "REGN", "AMT", "SO", "PLD", "FI", "BSX", "KLAC", "CI",
    "SCHW", "MO", "DUK", "EQIX", "CME", "SHW", "ZTS", "ICE", "CDNS", "SNPS",
    "WM", "MCK", "PH", "TT", "CL", "CMG", "ORLY", "MSI", "APH", "EOG",
    "BDX", "CTAS", "ITW", "AON", "WELL", "MCO", "CSX", "PNC", "USB", "EMR",
    "NOC", "HCA", "ECL", "FDX", "AJG", "MAR", "PXD", "SLB", "NSC", "FCX",
    "ADSK", "AFL", "TGT", "ROP", "CARR", "PCAR", "AZO", "SPG", "COF", "GM",
    "F", "OXY", "NEM", "DLR", "SRE", "AEP", "D", "O", "PSA", "ALL",
    "MET", "TRV", "AIG", "PRU", "KMB", "GIS", "KHC", "STZ", "MNST", "KDP",
    "ROST", "YUM", "CMI", "CTVA", "DXCM", "IDXX", "EW", "HUM", "CNC", "CI",
    "ORCL", "INTC", "MU", "AMGN", "PYPL", "BA", "LMT", "GD", "CRWD", "SNOW",
    "DDOG", "NET", "TEAM", "WDAY", "FTNT", "ZS", "ANET", "DELL", "HPQ", "HPE",
    "NXPI", "ON", "MCHP", "MPWR", "TER", "KEYS", "FTV", "TDY", "ZBRA", "VRSN",
    "CPRT", "FAST", "PAYX", "ODFL", "URI", "PWR", "JCI", "IR", "ROK", "DOV",
    "XYL", "IEX", "WAB", "HWM", "ETN", "AME", "GGG", "NDSN", "SNA", "SWK",
    "CHTR", "TMUS", "EA", "TTWO", "WBD", "PARA", "NWS", "NWSA", "FOX", "FOXA",
    "OKE", "KMI", "WMB", "MPLX", "PSX", "VLO", "MPC", "HES", "DVN", "FANG",
    "HAL", "BKR", "APA", "CTRA", "EQT", "TRGP", "LNG", "CF", "MOS", "NUE",
    "STLD", "RS", "MLM", "VMC", "PKG", "IP", "WRK", "AVY", "SEE", "BALL",
    "AMCR", "IFF", "ALB", "CE", "DD", "DOW", "EMN", "FMC", "LYB", "PPG",
    "SHW", "APD", "ECL", "LIN", "NEM", "FCX", "AA", "X", "CLF",
    "KR", "SYY", "ADM", "BG", "TSN", "HRL", "CPB", "CAG", "SJM", "MKC",
    "CLX", "CHD", "EL", "COTY", "PG", "CL", "KMB", "KVUE", "TAP",
    "ABNB", "BKNG", "EXPE", "MAR", "HLT", "H", "WH", "RCL", "CCL", "NCLH",
    "DAL", "UAL", "AAL", "LUV", "ALK", "JBHT", "CHRW", "XPO", "SAIA", "KNX",
    "CSX", "UNP", "NSC", "CP", "CNI", "WCN", "RSG", "WM", "CLH", "SRCL",
]

# Prefer non-class dual listings once (keep BRK-B, drop pure GOOG if both present later).
_DUPLICATE_PREF = {
    "GOOG": "GOOGL",
    "FOX": "FOXA",
    "NWS": "NWSA",
}

CONSTITUENTS_CSV_URLS = (
    # Community-maintained S&P 500 constituents (Symbol column)
    "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv",
    "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/master/data/constituents.csv",
)

INDEX_SKIP = frozenset(
    {
        "SPY",
        "QQQ",
        "IWM",
        "DIA",
        "XLK",
        "XLF",
        "XLE",
        "SMH",
        "XBI",
        "ARKK",
        "VOO",
        "IVV",
        "SPX",
    }
)


def _normalize_symbol(raw: str) -> str:
    s = str(raw).strip().upper().replace(".", "-")
    # Yahoo uses BRK-B not BRK.B
    return s


def _dedupe(symbols: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for s in symbols:
        s = _normalize_symbol(s)
        if not s or s in INDEX_SKIP:
            continue
        # map dual-class aliases to preferred
        s = _DUPLICATE_PREF.get(s, s)
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _ssl_context():
    """Prefer certifi CA bundle (macOS/venv often lacks system certs for urllib)."""
    import ssl

    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def fetch_sp500_remote(timeout: float = 20.0) -> list[str] | None:
    """Download current S&P 500 symbols. Returns None on failure."""
    ctx = _ssl_context()
    for url in CONSTITUENTS_CSV_URLS:
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "agentic-trading/0.1 (research scanner)"},
            )
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                text = resp.read().decode("utf-8", errors="replace")
            reader = csv.DictReader(io.StringIO(text))
            syms: list[str] = []
            for row in reader:
                # datasets repo uses Symbol; some mirrors use ticker
                raw = row.get("Symbol") or row.get("symbol") or row.get("Ticker") or ""
                if raw:
                    syms.append(raw)
            clean = _dedupe(syms)
            if len(clean) >= 400:  # sanity: real SPX is ~500
                return clean
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, csv.Error):
            continue
    return None


def load_sp500_universe(
    *,
    cache_path: Path | None = None,
    prefer_cache: bool = True,
    allow_remote: bool = True,
    fallback: list[str] | None = None,
) -> tuple[list[str], str]:
    """
    Return (symbols, source) where source is remote|cache|fallback.

    Cache written on successful remote fetch for offline reuse.
    """
    fb = _dedupe(list(fallback or SP500_FALLBACK))

    if prefer_cache and cache_path and cache_path.is_file():
        try:
            data = json.loads(cache_path.read_text())
            syms = _dedupe(list(data.get("symbols") or []))
            if len(syms) >= 100:
                return syms, "cache"
        except (json.JSONDecodeError, OSError):
            pass

    if allow_remote:
        remote = fetch_sp500_remote()
        if remote:
            if cache_path:
                try:
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    cache_path.write_text(
                        json.dumps(
                            {
                                "source": "remote",
                                "count": len(remote),
                                "symbols": remote,
                            },
                            indent=2,
                        )
                    )
                except OSError:
                    pass
            return remote, "remote"

    if cache_path and cache_path.is_file():
        try:
            data = json.loads(cache_path.read_text())
            syms = _dedupe(list(data.get("symbols") or []))
            if syms:
                return syms, "cache"
        except (json.JSONDecodeError, OSError):
            pass

    return fb, "fallback"
