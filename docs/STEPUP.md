# 强验证因子映射（答辩口径）

当前实现（演示）：

- 因子：`sms`（短信验证码）+ `face`（人脸，模拟）；
- 演示码固定 `123456`，由服务端随 stepup 事件下发，仅用于本地演示；
- 验证通过后执行；失败累计 3 次进入 OBSERVE，5 次会话 LOCKED 15 分钟。

生产替换（接口已抽象）：

| 演示因子 | 生产实现 | 接口 |
|---|---|---|
| 短信验证码 | 行方短信网关 / OTP | `StepUpAuthProvider.send_sms` |
| 人脸（模拟） | 行方活体检测 / 人脸比对 | `StepUpAuthProvider.verify_face` |
| 设备指纹 | 设备风控 SDK | `StepUpAuthProvider.device_risk` |
| U 盾（可选） | 证书签名挑战 | `StepUpAuthProvider.verify_ukey` |

答辩口径：**“多因子框架已落地，演示环境用固定码替代短信/人脸通道；生产只替换 provider，不改业务编排与策略。”**