"""离线语音识别（Windows SAPI / System.Speech，zh-CN）。

不联网、不依赖第三方 Python 包：调用系统自带的中文识别器。
前端录 16kHz 单声道 WAV，走 /v1/asr 进来，这里落临时文件并调用 PowerShell 识别。
"""

from __future__ import annotations

import base64
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import wave
from functools import lru_cache
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPT = _ROOT / "scripts" / "asr_sapi.ps1"
_VENDOR = _ROOT / "backend" / "vendor"
_MODEL_DIR = _ROOT / "backend" / "models" / "vosk-model-small-cn-0.22"
_MODEL_CACHE = Path(os.environ.get("PUBLIC") or "C:/Users/Public") / "BankAgent" / "vosk-model-small-cn-0.22"
if _VENDOR.exists() and str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))
MAX_BYTES = 2 * 1024 * 1024
_VOSK_LOCK = threading.Lock()

# 常用命令短语（受限语法，提升识别率）
_BASE_PHRASES = [
    "查余额", "我还有多少钱", "查这个月账单", "这个月账单", "本月账单", "上个月账单",
    "最近流水", "查流水", "只看收入", "只看支出", "收入", "支出",
    "我的订阅", "我开了什么会员", "视频会员多少钱", "我的理财", "理财收益",
    "有哪些理财产品", "我有几张卡", "我卡丢了", "挂失", "解挂我的卡",
    "把额度调到一万元", "申请虚拟卡", "今天还能转多少", "一年会员花多少钱",
    "最近有没有乱扣费", "有没有异常交易", "为什么需要验证", "转账多久到账",
    "退掉视频会员A", "把我的订阅都退了", "我有几个联系人", "我的联系人",
]
_AMOUNTS = [
    "十元", "十块", "二十元", "二十块", "五十元", "五十块", "一百元", "一百块",
    "两百元", "两百块", "三百元", "三百块", "五百元", "五百块", "一千元", "一千块",
]
_ALIASES = {"妈": ["妈妈", "老妈"], "老婆": ["媳妇", "爱人"], "房东": ["房东先生"]}


def _model_source() -> Path:
    env = os.environ.get("VOSK_MODEL")
    if env:
        return Path(env)
    if _MODEL_CACHE.is_dir():
        return _MODEL_CACHE
    return _MODEL_DIR


def _ensure_ascii_model() -> Path | None:
    """Vosk 不认中文路径：必要时把模型复制到 %LOCALAPPDATA%\\BankAgent 下。"""
    source = _model_source()
    if not source.is_dir():
        return None
    if all(ord(ch) < 128 for ch in str(source)) and source == _MODEL_CACHE:
        return source
    if _MODEL_CACHE.is_dir():
        return _MODEL_CACHE
    try:
        shutil.copytree(source, _MODEL_CACHE)
        return _MODEL_CACHE
    except OSError:
        return source if all(ord(ch) < 128 for ch in str(source)) else None


@lru_cache(maxsize=1)
def _vosk_available() -> bool:
    if not _model_source().is_dir():
        return False
    try:
        import vosk  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


@lru_cache(maxsize=1)
def _load_model():
    from vosk import Model

    path = _ensure_ascii_model()
    if path is None:
        raise RuntimeError("Vosk 模型路径不可用")
    return Model(str(path))


def status() -> dict:
    """优先 Vosk 离线模型，其次 Windows 系统识别器。"""
    if _vosk_available():
        return {"available": True, "engine": "vosk-small-cn"}
    if platform.system() != "Windows":
        return {"available": False, "reason": "需要 Vosk 模型或 Windows 系统识别器"}
    if not _SCRIPT.exists():
        return {"available": False, "reason": "缺少 asr_sapi.ps1"}
    return {"available": True, "engine": "sapi-zh-CN"}


def _phrases(contacts: list[str]) -> list[str]:
    out = list(_BASE_PHRASES)
    for contact in contacts:
        names = [contact] + _ALIASES.get(contact, [])
        for who in names:
            for amount in _AMOUNTS:
                out.append(f"给{who}转{amount}")
                out.append(f"转{amount}给{who}")
                out.append(f"给{who}打{amount}")
    return out


def _score(text: str, contacts: list[str]) -> float:
    """用理解层给候选打分：能解析成已知意图/带收款人的优先。"""
    try:
        from . import nlu, understanding
        from .normalize import normalize

        clean = normalize(text)
        people = [{"name": c, "alias": c} for c in contacts]
        frame = understanding.parse(clean, {}, people)
        intent = frame or nlu.parse(clean, {})
        score = 0.0
        if intent.get("name") not in (None, "unknown"):
            score += 2.0
        slots = intent.get("slots") or {}
        if slots.get("payee") or slots.get("counterparty"):
            score += 1.0
        return score
    except Exception:  # noqa: BLE001
        return 0.0


_ASR_FIXES = [
    ("说账单", "月账单"), ("这个说", "这个月"), ("查这个说", "查这个月"),
    ("转帐", "转账"), ("帐户", "账户"), ("余额宝", "余额"),
]


def _correct(text: str, contacts: list[str]) -> str:
    """ASR 常见错词纠正：只做确定性替换，不改语义。"""
    out = (text or "").strip()
    for who in contacts:
        if who and (out.startswith("那" + who) or out.startswith("那，" + who)):
            out = "给" + who + out[len("那" + who):] if out.startswith("那" + who) else out
    for src, dst in _ASR_FIXES:
        out = out.replace(src, dst)
    return out

def _candidate_score(text: str, contacts: list[str]) -> float:
    fixed = _correct(text, contacts)
    score = _score(fixed, contacts)
    if any(who and who in fixed for who in contacts):
        score += 2.0
    if re.search(r"(转|账单|流水|余额|订阅|理财|额度|收入|支出|卡|验证|到账)", fixed):
        score += 0.5
    return score

def _recognize_vosk(raw: bytes, contacts: list[str]) -> dict:
    from vosk import KaldiRecognizer

    with wave.open(io.BytesIO(raw), "rb") as handle:
        if handle.getnchannels() != 1:
            raise ValueError("只支持单声道 WAV")
        rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    with _VOSK_LOCK:
        model = _load_model()
        rec = KaldiRecognizer(model, rate)
        rec.SetMaxAlternatives(3)
        rec.AcceptWaveform(frames)
        result = json.loads(rec.FinalResult())

    candidates: list[tuple[str, float]] = []
    for item in result.get("alternatives") or []:
        text = str(item.get("text") or "").replace(" ", "").strip()
        if text:
            candidates.append((text, float(item.get("confidence") or 0)))
    if not candidates:
        text = str(result.get("text") or "").replace(" ", "").strip()
        if text:
            candidates.append((text, 0.0))
    if not candidates:
        return {"text": "", "confidence": 0, "engine": "vosk-small-cn", "alternatives": []}

    best_text, best_conf = max(candidates, key=lambda pair: _candidate_score(pair[0], contacts))
    return {
        "text": _correct(best_text, contacts),
        "confidence": round(best_conf, 4),
        "engine": "vosk-small-cn",
        "alternatives": [{"text": t, "confidence": c} for t, c in candidates[:5]],
    }


def recognize(audio_b64: str, contacts: list[str] | None = None) -> dict:
    """音频（WAV base64）→ {text, confidence, engine}。"""
    if not status().get("available"):
        raise RuntimeError("离线语音识别不可用")
    try:
        raw = base64.b64decode(audio_b64, validate=True)
    except Exception as exc:  # noqa: BLE001
        raise ValueError("音频不是合法 base64") from exc
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError("音频为空或超过 2MB")

    if _vosk_available():
        return _recognize_vosk(raw, contacts or [])

    with tempfile.TemporaryDirectory() as tmp:
        wav_path = Path(tmp) / "speech.wav"
        wav_path.write_bytes(raw)
        try:
            with wave.open(str(wav_path), "rb") as handle:
                if handle.getnchannels() != 1 or handle.getframerate() < 8000:
                    raise ValueError("只支持单声道、8kHz 以上的 WAV")
        except wave.Error as exc:
            raise ValueError("不是合法的 WAV 音频") from exc

        phrases_file = Path(tmp) / "phrases.txt"
        phrases_file.write_text("\n".join(_phrases(contacts or [])), encoding="utf-8")
        out_file = Path(tmp) / "result.json"
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
               "-File", str(_SCRIPT), "-Wav", str(wav_path),
               "-PhrasesFile", str(phrases_file), "-OutFile", str(out_file)]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=20,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("语音识别超时") from exc
        if not out_file.exists():
            err = (proc.stderr or b"").decode("utf-8", "ignore")[-200:]
            raise RuntimeError(f"识别器无输出：{err}")
        try:
            data = json.loads(out_file.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise RuntimeError("识别结果不是 JSON") from exc

        candidates = []
        for item in data.get("alternatives") or []:
            text = str(item.get("text") or "").strip()
            if text:
                candidates.append((text, float(item.get("confidence") or 0)))
        if not candidates:
            text = str(data.get("text") or "").strip()
            candidates = [(text, float(data.get("confidence") or 0))] if text else []
        if not candidates:
            return {"text": "", "confidence": 0, "engine": str(data.get("engine") or "sapi-zh-CN"),
                    "alternatives": []}

        people = contacts or []
        best_text, best_conf = max(candidates, key=lambda pair: _candidate_score(pair[0], people))
        return {
            "text": _correct(best_text, people),
            "confidence": best_conf,
            "engine": str(data.get("engine") or "sapi-zh-CN"),
            "alternatives": [{"text": t, "confidence": c} for t, c in candidates[:5]],
        }
