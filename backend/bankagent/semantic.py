"""可选语义匹配层：BGE 中文小模型（ONNX）+ 本地示例语料。

只有同时满足以下条件才启用：
- 安装 onnxruntime / numpy / tokenizers；
- 存在 model.onnx 与 tokenizer.json。

不可用时自动回退 lexicon 的字符相似度，不影响主流程。
"""

from __future__ import annotations

import os
import re
import sys
from functools import lru_cache
from pathlib import Path

from . import lexicon

_MODEL_NAME = "model.onnx"
_TOKENIZER_NAME = "tokenizer.json"
_THRESHOLD = 0.45
_HIGH_CONFIDENCE = 0.75
_ALLOWED = {"bill_analysis", "subscription", "wealth", "transfer", "standing_plan"}


def _candidate_dirs() -> list[Path]:
    dirs = []
    env = os.environ.get("SEMANTIC_MODEL")
    if env:
        dirs.append(Path(env))
    dirs.append(Path(__file__).resolve().parent.parent.parent / "backend" / "models" / "semantic")
    exe = Path(sys.executable).resolve()
    if exe.parent.name.lower() == "python":
        dirs.append(exe.parent.parent / "semantic")
    return dirs


def model_dir() -> Path | None:
    for directory in _candidate_dirs():
        if (directory / _MODEL_NAME).is_file() and (directory / _TOKENIZER_NAME).is_file():
            return directory
    return None


@lru_cache(maxsize=1)
def _session():
    from tokenizers import Tokenizer
    import onnxruntime as ort

    directory = model_dir()
    if directory is None:
        return None
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(str(directory / _MODEL_NAME), options,
                                   providers=["CPUExecutionProvider"])
    tokenizer = Tokenizer.from_file(str(directory / _TOKENIZER_NAME))
    tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
    tokenizer.enable_truncation(max_length=64)
    return session, tokenizer


@lru_cache(maxsize=1)
def available() -> bool:
    if model_dir() is None:
        return False
    try:
        return _session() is not None
    except Exception:  # noqa: BLE001
        return False


def status() -> dict:
    directory = model_dir()
    if not available():
        return {"available": False, "reason": "未安装语义模型或 onnxruntime"}
    return {"available": True, "engine": "bge-small-zh", "path": str(directory),
            "threshold": _THRESHOLD}


def embed_texts(texts: list[str]):
    """公开：返回文本向量列表（numpy 数组）。"""
    return _embed(texts)


def _embed(texts: list[str]):
    import numpy as np

    session, tokenizer = _session()
    encodings = tokenizer.encode_batch(texts)
    ids = np.array([item.ids for item in encodings], dtype=np.int64)
    mask = np.array([item.attention_mask for item in encodings], dtype=np.int64)
    outputs = session.run(None, {
        "input_ids": ids,
        "attention_mask": mask,
        "token_type_ids": np.zeros_like(ids),
    })
    vectors = outputs[0][:, 0, :]  # BGE 使用 CLS pooling
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vectors / norms


@lru_cache(maxsize=1)
def _examples():
    texts = []
    items = []
    for item in lexicon.exemplars():
        texts.append(lexicon.canonicalize(str(item["text"])))
        items.append(item)
    if not texts:
        return None
    try:
        vectors = _embed(texts)
    except Exception:  # noqa: BLE001
        return None
    return vectors, items


def _amount_cents(text: str) -> int | None:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(万|千)?\s*(?:元|块|块钱)?", text)
    if not m:
        return None
    value = float(m.group(1))
    if m.group(2) == "万":
        value *= 10000
    elif m.group(2) == "千":
        value *= 1000
    return int(round(value * 100)) if value > 0 else None


def match(text: str, contacts: list[dict] | None = None) -> dict | None:
    data = _examples()
    if not data:
        return None
    vectors, items = data
    clean = lexicon.canonicalize(text)
    if not re.search(r"[\u4e00-\u9fff]", clean):
        return None
    if len(clean) <= 2:
        return None
    if re.search(r"(弄一下|搞一下|办一下|整一下|处理一下|安排一下)", clean):
        return None
    try:
        query = _embed([clean])[0]
    except Exception:  # noqa: BLE001
        return None
    scores = vectors @ query
    best_index = int(scores.argmax())
    best_score = float(scores[best_index])
    if best_score < _THRESHOLD:
        return None

    item = items[best_index]
    name = str(item["name"])
    if name not in _ALLOWED:
        return None
    slots = dict(item.get("slots") or {})
    if name == "subscription":
        slots["action"] = "detail"      # 语义层不直接执行退订，先列出让用户选
    if name == "wealth" and slots.get("action") == "redeem":
        slots["action"] = "holdings"    # 赎回属于资金动作，不在语义层兜底
    if name == "transfer":
        amount = _amount_cents(clean)
        payee = None
        for contact in contacts or []:
            for token in (contact.get("alias"), contact.get("name")):
                if token and str(token) in clean:
                    payee = str(token)
                    break
            if payee:
                break
        if not amount or not payee:
            return None
        slots.update({"amount": amount, "payee": payee})
    return {
        "name": name,
        "slots": slots,
        "missing": [],
        "ambiguity": [],
        "spans": {},
        "confidence": round(min(0.95, 0.5 + best_score * 0.5), 2),
        "source": "semantic",
        "similarity": round(best_score, 3),
        "needs_confirm": best_score < _HIGH_CONFIDENCE,
    }
