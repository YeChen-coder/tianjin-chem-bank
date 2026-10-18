# 会话 3：GLM 改写与公式往返

- 状态：可以联调；第三部分模块和交付数据已就绪，原作业/存储接线仍由会话 4 完成。
- 基线：S4-20261010-v1；schema_version：1；开发与所有输出均在 V2。
- 负责范围：`aivariant.py`、`aianswers.py`、`ai_formula_v2/`、`tests/test_ai_formula_v2*.py`、本文档目录。
- 正式数据写入：没有。旧项目、旧题库、mirror、其他会话私有工作区只读；没有启动或重启旧 worker/8893/8894。

## 已实现

- 完整 Question 请求（题干、选项、递归小问、表格、公式、答案、解析）与允许资产；保留改写强度、教师意见、辅助画像。
- 严格 JSON v1 返回边界、重复字段/ID 检查、服务器分配 ID、答案关联映射、结构丢失拦截、公式状态重置；模型不能改母题身份、版本、教师确认或文件路径。
- 注入公共 `validate_question` 与 `compile_formula`；失败不当成功；格式检查与科学核对分开。
- 生成与独立核对两阶段、实际图片重传、取消/过期版本检查、不启动新队列、不自动保存或覆盖母题。
- 原旧模块只按需复制，新增结构入口，旧行为保留；结构答案支持三次独立作答与逐目标语义共识核对，禁止多数票直接发布。
- 旧字符串兼容只识别明确公式分隔，普通英文、pH、单位保留文字；不把所有英文猜成公式。

## 接口入口

`ai_formula_v2.build_request`、`normalize_response`、`generate_candidate`、`review_candidate`。

旧异步流程接入入口：`aivariant.generate_content_candidate`、`content_complete`；答案入口：`aianswers.evaluate_content`、`review_content_candidate`。返回结构及提交事务要求见 INTEGRATION_REQUESTS.md。

## 验证

命令：`.venv/Scripts/python.exe -m unittest discover -s tests -p "test_ai_formula_v2*.py" -v`。

32 项测试通过。覆盖完整上下文、公式反斜杠、合并表格、递归小问、缺图/图片比例、悬空/重复/遗漏答案、字段注入、错误 JSON/截断、结构丢失、编译失败、取消和版本冲突、三次独立答案及共识、旧模块适配、GLM JSON 模式/用量/调用上限。模拟编译器明确标记 `test-double-only`，不是转换证明。

受控真实测试：读取会话 1 的真实 26 题导入结果中的第 15/22/25 题，只复制所需小样本/配图到 `.work/session-3`。主用型号配置为 `glm-5.3` 与 `glm-5.3-flash`；只读取原 keys.local 中 GLM 字段到内存，不复制或打印凭据。真实请求 JSON 模式；首轮 high，复测 low，只在测试进程内覆盖强度，未改原配置。

| 验收范围 | 证据与结论 |
| --- | --- |
| 模拟协议 + 真实公共编译/导出 | 共同固定样例 4 道；8 个 Word 原生公式、2 张表格、4 个图片位置，图片字节保留。AI 响应明确是替身，未验收科学答案。见 `.work/session-3/mock-01/report.json`。 |
| 真实 GLM 第 15、25 题 | `glm-5.3` 生成并独立核对均 PASS。第 15 题含 260℃、900℃完整反应式与计算/解析；两题共 22 个原生公式，Word 导出结构通过。仍未教师确认；第 15 题额外标记独立导出前补齐相对原子质量数据。 |
| 真实 GLM 第 22 题 | `glm-5.3-flash` 两次均传实际原图。最终格式/编译通过，导出 11 个原生公式及 1 个原图，字节不变；独立核对发现“仪器 M 为锥形瓶”与图中分液漏斗不符，FAIL。候选状态为 candidate_rejected，ok=false，保留 AI_JUDGE_FAIL，不能发布。 |
| 真实失败路径 | 首轮第 15 题超时；第 22 题重复答案目标被拒绝；复测第 22 题合法 `\\mathrm{g/L}` 被公共编译桥拒绝。后续要求标准单位分式通过，未自动改数字/条件。原失败响应和最终回放诊断均保留。 |
| 调用与原件 | 共 11 次真实 GLM 请求：7 次文本、4 次带图（图像生成 3 次，图像独立审核 1 次）。已返回用量合计 63,102 tokens；1 次超时没有返回用量，不记为 0。原 Word 字节哈希未变，无正式题库写入。 |

真实试验使用的是当时的导入 JSON 快照。会话 1 在并行更新自己的 import-result.json，因此共享 JSON 的前后摘要变化不表示本模块写入原件。输入私有快照与来源摘要保留在每轮目录。

最终交付目录 `.work/session-3/delivery/`：`accepted-candidates.json/.docx`（两题待教师确认）、`rejected-candidates.json/.docx`（失败样例，仅供查看）、`results.json`、`recorded-failures.json`、`report.json`。最终交付由已保存响应回放得到，无新增 API 请求。汇总见本目录 `ACCEPTANCE_REPORT.json`。

复现模拟：`.venv/Scripts/python.exe -m ai_formula_v2.acceptance --mock --run-name <新的私有目录名>`。

复现最终回放：`.venv/Scripts/python.exe -m ai_formula_v2.acceptance --replay-final --run-name <新的私有目录名>`。不读取密钥、不调用模型。

## 依赖与限制

- 原作业路由/公共存储接线由会话 4 完成；本模块未宣称正式程序已经切换。
- Word 实际视觉渲染由会话 4 顺序执行，结构导出成功不代替视觉验收或教师内容确认。
- 真实样本主要覆盖反应式条件、计算与答案解析、配图审查；pH/离子、行内选项图、合并/嵌套表格及异常用控制样例验证，不能称这些都通过真实 GLM 科学验收。三次结构化独立作答采用模拟证据测试，未额外跑真实模型全套。
- `\\mathrm{g/L}` 公共编译兼容性问题已向会话 4 留下最小复现；新提示词采用标准分式，原问题仍不冒充已修好。
- 没有创建提交或 PR，也没有向其他会话自动发消息。源码复制来源与摘要见本目录 COPY_MANIFEST.json。

图像能力核对：[GLM-5.3-Flash 官方模型卡](https://huggingface.co/zai-org/GLM-5.3-Flash)。JSON 模式依据：[Z.AI 结构化输出](https://docs.z.ai/guides/capabilities/struct-output)。当前国内端点能力还须以本次实际调用结果为准。
