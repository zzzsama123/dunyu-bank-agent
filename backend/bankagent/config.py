"""全局配置与时间/金额工具。所有模块共用，禁止各自硬编码阈值。"""

from __future__ import annotations

import datetime
import os
from pathlib import Path


def _load_dotenv() -> None:
    """读取 backend/.env（KEY=VALUE），已存在的环境变量优先。

    这样不用每次开新终端都设 $env:LLM_API_KEY；.env 已在 .gitignore 里，不会进仓库。
    """
    p = Path(__file__).resolve().parent.parent / ".env"
    if not p.is_file():
        return
    try:
        for raw in p.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                os.environ.setdefault(key, value)
    except OSError:
        pass


_load_dotenv()

# ---------- 路径 ----------
BACKEND_DIR = Path(__file__).resolve().parent.parent          # bankagent/backend
DATA_DIR = Path(os.environ.get("BANK_DATA_DIR") or (BACKEND_DIR / "data"))
DB_PATH = os.environ.get("BANK_DB") or str(DATA_DIR / "bank.db")
FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"

# ---------- 时区 ----------
TZ = datetime.timezone(datetime.timedelta(hours=8), "Asia/Shanghai")

# ---------- 金额阈值（单位：分） ----------
LIMIT_DAILY = 100_000            # 日累计 1,000 元
LIMIT_NEW_PAYEE = 50_000         # 新收款人单笔 500 元
LIMIT_SUB_CANCEL = 20_000        # 单条订阅 200 元
LIMIT_IM_CHANNEL = 30_000        # IM 渠道 300 元
LIMIT_NIGHT = 50_000             # 夜间 500 元
HARD_CAP_PER_NODE = 5_000_000    # 单笔硬上限 5 万
HARD_CAP_PER_PLAN = 5_000_000    # 计划硬上限 5 万
LIMIT_PLAN_OUTFLOW = 100_000     # 计划净流出 1,000 元
MAX_PLAN_NODES = 8

# ---------- 时效 ----------
CONFIRM_TTL_SECONDS = 300
STEPUP_TTL_SECONDS = 300
STEPUP_LOCK_SECONDS = 900
STEPUP_MAX_FAILS = 3
DEMO_STEPUP_CODE = "123456"

# ---------- 业务 ----------
DEFAULT_USER_ID = 1
DEFAULT_CHANNEL = "app"
NIGHT_START_HOUR = 23
NIGHT_END_HOUR = 6
VERSION = "1.0.0"

# ---------- 大模型（OpenAI 兼容接口） ----------
# AGENT_BRAIN: auto（有 key 用模型，否则规则）/ rules / llm
AGENT_BRAIN = (os.environ.get("AGENT_BRAIN") or "auto").strip().lower()
LLM_BASE_URL = (os.environ.get("LLM_BASE_URL") or "https://api.deepseek.com/v1").rstrip("/")
LLM_API_KEY = (os.environ.get("LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
               or os.environ.get("OPENAI_API_KEY") or "").strip()
LLM_MODEL = os.environ.get("LLM_MODEL") or "deepseek-chat"
LLM_TIMEOUT = int(os.environ.get("LLM_TIMEOUT") or "20")
LLM_TEMPERATURE = float(os.environ.get("LLM_TEMPERATURE") or "0")
# 理解模式：model=模型先理解成命令码；rules=确定性优先（默认 model，失败自动回退）
AGENT_MODE = (os.environ.get("AGENT_MODE") or "model").strip().lower()

# 离题守卫：非银行业务一律不接（模型返回 out_of_scope 或规则命中时）
SCOPE_STRICT = (os.environ.get("SCOPE_STRICT") or "1").strip() not in ("0", "false", "off")


# ---------- 时间 ----------
# 演示时钟固定在种子数据所在月份，避免跨月后“本月”为空、账实看起来不一致。
# 需要真实时钟时设置 BANK_REAL_CLOCK=1。
_DEMO_NOW = os.environ.get("BANK_DEMO_NOW", "2026-10-31T20:00:00+08:00")


def now_dt() -> datetime.datetime:
    if os.environ.get("BANK_REAL_CLOCK") == "1":
        return datetime.datetime.now(TZ).replace(microsecond=0)
    try:
        return datetime.datetime.fromisoformat(_DEMO_NOW)
    except ValueError:
        return datetime.datetime.now(TZ).replace(microsecond=0)


def now_iso() -> str:
    return now_dt().isoformat()


def today_str() -> str:
    return now_dt().date().isoformat()


def iso_after(seconds: int) -> str:
    return (now_dt() + datetime.timedelta(seconds=seconds)).isoformat()


def parse_iso(s: str) -> datetime.datetime:
    dt = datetime.datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt


def seconds_since(iso_ts: str | None) -> int | None:
    if not iso_ts:
        return None
    return int((now_dt() - parse_iso(iso_ts)).total_seconds())


# ---------- 区间 ----------
def range_bounds(name: str | None) -> tuple[str, str, str]:
    """把自然语言区间映射为 (start_date, end_date, label)。

    支持：this_month / last_month / this_year / last_7d / last_30d / all / 'YYYY-MM'
    """
    today = now_dt().date()
    if not name or name in ("this_month", "本月", "这个月"):
        start = today.replace(day=1)
        return start.isoformat(), today.isoformat(), f"{start.year}年{start.month}月"
    if name in ("last_month", "上个月", "上月"):
        first = today.replace(day=1)
        last_end = first - datetime.timedelta(days=1)
        return last_end.replace(day=1).isoformat(), last_end.isoformat(), f"{last_end.year}年{last_end.month}月"
    if name in ("today", "今天", "今日"):
        return today.isoformat(), today.isoformat(), "今天"
    if name in ("yesterday", "昨天", "昨日"):
        day = today - datetime.timedelta(days=1)
        return day.isoformat(), day.isoformat(), "昨天"
    if name in ("day_before", "前天"):
        day = today - datetime.timedelta(days=2)
        return day.isoformat(), day.isoformat(), "前天"
    if name in ("this_week", "本周", "这周", "这礼拜"):
        monday = today - datetime.timedelta(days=today.weekday())
        return monday.isoformat(), today.isoformat(), "本周"
    if name in ("this_year", "今年"):
        return today.replace(month=1, day=1).isoformat(), today.isoformat(), f"{today.year}年"
    if name in ("last_7d", "近7天", "最近一周"):
        return (today - datetime.timedelta(days=7)).isoformat(), today.isoformat(), "近 7 天"
    if name in ("last_3m", "最近3个月", "近3个月", "最近三个月", "近三个月"):
        return (today - datetime.timedelta(days=90)).isoformat(), today.isoformat(), "近 3 个月"
    if name in ("last_30d", "近30天", "最近一个月"):
        return (today - datetime.timedelta(days=30)).isoformat(), today.isoformat(), "近 30 天"
    if name in ("all", "全部"):
        return "1970-01-01", today.isoformat(), "全部"
    # YYYY-MM
    try:
        y, m = name.split("-")[:2]
        start = datetime.date(int(y), int(m), 1)
        nxt = (start.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
        return start.isoformat(), (nxt - datetime.timedelta(days=1)).isoformat(), f"{y}年{m}月"
    except Exception:
        start = today.replace(day=1)
        return start.isoformat(), today.isoformat(), f"{start.year}年{start.month}月"
