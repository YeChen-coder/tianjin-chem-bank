# 智谱新手礼包与 FlashX

调查日期：2026-10-07（用户时区）。

## 截图与官方规则

截图显示独立的 FlashX 专用包 3,000,000 tokens、Flash 专用包 9,074,161 tokens，以及通用按 token 计费资源包 358,252 tokens。另有按次模型包和搜索包，不能直接当成文本推理 token。

[官方模型说明](https://docs.bigmodel.cn/cn/guide/models/vlm/glm-5.3-flash) 确认模型代码为 `glm-5.3-flashx` 与 `glm-5.3-flash`，两者支持图片、Base64 Data URL 和结构化输出，文本参数相同。使用既有标准 API 端点，不使用 Coding Plan 端点。

[官方费用说明](https://docs.bigmodel.cn/cn/faq/fee-issues) 规定先扣适用资源包、再扣现金；多个相同适用场景的包优先扣即将到期的包。没有找到专用包与通用包重叠时一律专用优先的明确规定。截图没有到期时间或现金余额，因此不能确定本账户实际抵扣顺序，也不能保证 FlashX 专用包用完就会报错。GLM-5.3 不适用截图中的两个 Flash 专用包；从适用场景判断，它使用通用按 token 包或现金，具体以账单为准。

[官方错误码](https://docs.bigmodel.cn/cn/faq/api-code) 中 `1113` 表示欠费，HTTP 状态为 429；`1302` 为速率限制，`1305` 为模型繁忙。程序只在 FlashX 返回 `1113` 或 HTTP 402 时使用 Flash，不把普通限流、输出长度不足或不完整答案误当成礼包不足。

## 程序与本机配置

- GLM flash 角色：FlashX → 收到余额不足响应 → Flash；保留相同提示词和所有图片。
- GLM pro 角色：保留 GLM-5.3 的纯文字审核。
- FlashX 的暂停按密钥、端点及模型区分，只在内存中保存十分钟；成功用 Flash 不暂停整个 GLM 服务。换配置或重启后可重新尝试。
- 结果返回并保存实际成功的模型名称，Flash 失败时保留两次错误原因。
- 本机 `keys.local` 设 `CHEM_AI_PROVIDER=glm`，礼包阶段固定 GLM 服务，仍允许同服务的 FlashX → Flash。原配置 `auto` 优先 Kimi，结束礼包阶段可改回 `auto`。固定 GLM 时两者失败直接报错；auto 模式保留原有跨服务备用。
- `GLM_FLASHX=off` 直接用 Flash。`GLM_FLASH` 仍指 Flash 备用模型，`GLM_PRO` 仍指旗舰模型。现有 API 密钥保留。

## 验证

新增离线测试覆盖 FlashX 优先、数字/字符串 `1113`、HTTP 402、切换保留图片和提示词、实际模型记录、冷却与恢复、换密钥/端点、限流/鉴权/输出长度不足不接替、双包失败与 auto 模式备用、禁用及重复模型配置。真实接口验证结果另记在下方；不通过耗尽账户来制造错误。

使用本机已有智谱密钥，通过共用 `complete_role` 接口实际发起两次小请求，均成功使用 `glm-5.3-flashx`：文字请求返回水的化学式（2.126 秒），自制纯红色 PNG 的看图请求正确返回 `red`（1.817 秒）。这验证模型权限、现有参数、JSON 和图片通路，不代表完整化学题的质量或耗时。测试不读写正式题库。脱敏结果保存在本机 `.dev/flashx-validation/result.json`，额度不足由离线测试模拟。

完整 pytest 回归 255 项通过（33.39 秒）；独立 `test_aivariant.py` 回归通过（`ok`）。
