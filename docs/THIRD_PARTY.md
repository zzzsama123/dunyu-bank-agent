# 第三方组件与模型许可

本项目为仿真 Demo，不连接真实银行、不产生真实资金。所有第三方组件均本地运行，不联网、不上传数据。

| 组件 | 版本/文件 | 许可 | 来源 | 修改说明 |
|---|---|---|---|---|
| BAAI/bge-small-zh-v1.5 | ONNX int8 `runtime/semantic/model.onnx` | MIT | https://huggingface.co/BAAI/bge-small-zh-v1.5 | 转 ONNX 并 int8 量化，用于语义相似度兜底 |
| BGE tokenizer | `runtime/semantic/tokenizer.json` | MIT | 同上 | 随模型发布，未改词表 |
| Vosk 中文小模型 | `runtime/asr/model` | Apache-2.0 | https://alphacephei.com/vosk/models | 原样使用，仅离线语音识别 |
| Python Embedded | `runtime/python` | PSF License | https://www.python.org/ | 原样分发 |
| 前端/后端代码 | 本仓库 | 仅参赛演示使用 | — | 无 |

## 模型卡片

见 `runtime/semantic/MODEL_CARD.md`。模型只用于“把口语说法映射到候选意图”，不参与授权、不直接调用工具。

## 许可原文

- BGE：`runtime/semantic/LICENSE`（MIT）
- Vosk：见上游 Apache-2.0；本项目不修改模型文件

## 安全边界

- 语义层可失败、可回退规则；模型输出永远不是授权；
- 所有工具调用经 validator + policy + 审计；
- 不包含真实客户数据，模型不接受用户上传训练。
