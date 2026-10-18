# 四会话共同接口约定 v1

日期：2026-10-10。接口约定 v1。用户已指定会话分工开始开发；公共最小底座与独立导出已实现，实际调用/范围见 `PUBLIC_API.md`，集成验收见 `INTEGRATION_STATUS.md`。主方案见 `../../WORD_LATEX_REFACTOR_PLAN.md`。正式迁移尚未开始。

## 1. 约定的意义与负责人

导入、网页、GLM、Word 都交换同一种题目数据。各会话先依据本文使用固定样例/替身独立开发，再替换成真实模块。

版本标识为整数 `schema_version: 1`。会话 4 负责将本文落实成公共校验代码、样例与集成 API；其他会话不能自行变更字段名称或含义。以下新目录和函数名是交付目标，不代表已有代码。

唯一开发根目录是 `C:/Users/yeche/Documents/ChatGPT/给妈妈的AI组卷/给妈妈的AI组卷V2`。本文所有可修改相对路径都以 V2 为根；旧项目只读参考。原试卷只读目录是 `C:/Users/yeche/Documents/ChatGPT/给妈妈的AI组卷/chem-papers/mirror`。

开发基线只包含实际要用的源码、固定资源及必要许可证。由会话 4 在 V2 发布 `docs/word-latex/BASELINE.md` 和 `COPY_MANIFEST.md`，逐项记录旧来源路径/摘要、新路径、用途、复制负责人。各会话只复制自己负责的文件；共享模块由会话 4 复制，已有目标文件不得盲目覆盖。

不整份复制旧项目、旧虚拟环境、旧数据库、缓存、模型下载、媒体全集、历史输出或凭据。少量固定测试材料按需要选取，记录来源；大的只读模型/历史证据可在旧目录引用，由私有缓存处理，不全部搬进 V2。四个测试工作区使用 V2 下 `.work/session-1` 至 `.work/session-4`，加入忽略规则，互不共享写入。新运行环境由会话 4 建立，其他会话不同时安装/升级公共依赖。

## 2. 数据模型

### 2.1 顶层

`PaperBundle` 包含：`schema_version`、`paper`、`questions`、`assets`、`issues`。

- `paper`：`id`、`title`、`question_ids`（按题目顺序）、`source_sha256`（无原件的合成/新组卷为 null）。
- `questions`：Question 数组。
- `assets`：Asset 数组。只描述资产身份、类型与预览，不携带任意磁盘路径或凭据。服务器另管理 asset_id 到本地文件的映射。
- `issues`：Issue 数组。题目内也可列自己的 issues，用 issue.id 去重。

导入结果 `ImportResult` 为 `{bundle, report}`。report 包含题目/公式/配图数量、未支持项、来源摘要和处理状态。程序处理成功与用户确认正确是两种状态。

### 2.2 Question

| 字段 | 类型与含义 |
| --- | --- |
| `id` | 稳定字符串 ID；与旧库整数题目 ID 的映射由存储层维护，不能因编辑或重新显示变化 |
| `revision` | 服务端管理的非负整数；编辑/保存版本，不由 AI 决定 |
| `number`、`qtype` | 原题号与题型字符串，允许未知 |
| `body` | Block 数组，题干的实际顺序 |
| `options` | `{id,label,blocks}` 数组，无选项则空数组 |
| `subquestions` | `{id,label,blocks}` 数组，保持小问归属；嵌套小问用递归 `subquestions` 数组 |
| `answers`、`explanations` | `{id,target_id,blocks}` 数组；target_id 指向题目、小问或作答位置 ID，不靠字符串顺序猜对应 |
| `source` | 原文件摘要、原题号和内容位置；AI 版本保留母题引用，不伪造原件定位 |
| `origin` | `import` / `teacher` / `ai` |
| `issues` | 待核对/失败记录 |

分类、组卷引用、审核结果、AI 工作流元数据继续由原存储层管理；桥接时必须保留，不交给模型自由改写。普通文本搜索摘要是派生结果，不替代结构化内容。

### 2.3 Block 与 Inline

每个 Block/Inline 有稳定字符串 `id`。维护已有节点时保留 ID；新增/复制节点重新分配，文档内不能重号。

| `type` | 字段与约束 |
| --- | --- |
| `paragraph` | `inlines: Inline[]`、`align: left/center/right`；默认 left |
| `table` | `columns_mm: number[]`、`rows: {id,cells}[]`；cells 为单元格数组，每格是 `{id,col_index,row_span,col_span,blocks}`，blocks 可有段落、图片、表格。row_span 从该行起计算，col_index 为从 0 开始的列号。被上一行合并覆盖的位置不重复建格；行列覆盖无重叠，跨度为正整数，不越过表格边界；不支持嵌套表格的实现必须明确提示 |
| `image`（独立块） | `asset_id`、`width_mm`、`height_mm`、`align`。尺寸均为正数；放置位置就是其在 blocks 中的顺序 |
| `unresolved` | `source_ref`、`reason`、`fallback_blocks`。保存可显示的原内容和源定位，不静默丢掉 |

Inline 有：

- `text`：`text` 字符串，`style` 可含 bold/italic、vertical（normal/subscript/superscript）。尚未转公式的样式不能丢失。
- `formula`：`latex`（未恢复时可 null）、`status`、`source_ref`。status 为 restored/review_required/confirmed/unsupported。状态由后端/教师审核决定，AI 不能自行声明 confirmed。
- `image`：`asset_id`、`width_mm`、`height_mm`，排列位置由 inlines 顺序决定。
- `blank`：`width_mm`，作为作答位置，其 ID 可被 answers.target_id 引用。

段落可以是文字→公式→文字→图片。公式的 MathML/OMML/原图保存在服务端来源记录与派生缓存中；不接受 AI 提交任意 XML 或 HTML。未确认原生公式保留原结构，编辑后的编译缓存必须更新或失效。

表格列宽使用毫米；网页根据可用宽度适配，Word 保留物理尺寸或按页面宽度明确缩放。缩放不能改变图片比例。超宽表格有明示策略，不挤掉内容。

源定位字段允许渐进丰富，但必须能找到原文件和目标对象。raw OMML/OLE 等来源保留在隔离存储，可由 source_ref 定位；不要把所有二进制塞进 JSON。

### 2.4 Asset 与 Issue

Asset：`{id, sha256, mime_type, preview_url}`。摘要标识原媒体字节，不能混淆 WMF 原文件与 PNG 预览的摘要。URL 由本机资产服务生成；原媒体路径不由 AI 或浏览器控制。

Issue：`{id, code, severity, question_id, node_id, message}`。severity 为 info/review/error；不确定题目或节点 ID 为 null。建议 code：UNSUPPORTED_SOURCE、UNMAPPED_SYMBOL、OCR_REVIEW、SPLIT_REVIEW、ANSWER_MAPPING_REVIEW、FORMULA_COMPILE_FAILED、ASSET_MISSING、REVISION_CONFLICT。

### 2.5 最小格式示例

下例为格式演示，不是完整 PaperBundle，也不是科学正确性验收：

```json
{
  "id": "q-demo-1", "revision": 0, "number": "1", "qtype": "填空题",
  "body": [{"id": "p-1", "type": "paragraph", "align": "left", "inlines": [
    {"id": "t-1", "type": "text", "text": "向50 mL溶液中加入"},
    {"id": "f-1", "type": "formula", "latex": "\\mathrm{NaOH}", "status": "restored", "source_ref": "source-f-1"},
    {"id": "t-2", "type": "text", "text": "，观察现象。"}
  ]}],
  "options": [], "subquestions": [], "answers": [], "explanations": [],
  "source": {"source_sha256": null, "number": "1", "locator": "synthetic-demo"},
  "origin": "import", "issues": []
}
```

JSON 中的 `\\` 解码后是一个公式反斜杠；请求/返回不能二次错误转义。

## 3. 模块接口：先可调用，再由主程序接线

| 所有者 | 交付接口 | 输入与输出 |
| --- | --- | --- |
| 会话 1 | `question_import_v2.import_paper(source_path, workspace, services)` | 原文件只读，workspace 为私有目录；输出 ImportResult；services 注入公共编译/资产登记能力，便于使用替身 |
| 会话 2 | `mountQuestionEditor(container, {question, assets, api, onSaved})` | 显示/编辑 Question；api 可替换为 mock；销毁实例时取消未完成的本地编辑请求 |
| 会话 2 | `mountPaperPreview(container, {bundle, api})` | 整卷显示，复用卡片的内容渲染器 |
| 会话 3 | `ai_formula_v2` 的请求构建、返回规范化及验证适配 | 消费 Question/允许资产清单，产生完整候选 Question 与诊断，接入原异步 AI 工作流；候选先不覆盖母题 |
| 会话 4 | `question_content.validate_bundle/validate_question` | 输出验证报告，失败不静默删字段；不负责判断科学答案是否正确 |
| 会话 4 | `question_content.compile_formula(latex, source_ref=None)` | 返回编译结果或明确失败；唯一的生产公式编译桥，复用并修复现有 formula_engine |
| 会话 4 | `word_export_v2.export_paper(bundle, output_path, options, asset_resolver)` | 使用私有输出路径；生成 DOCX 与报告，asset_resolver 只解析已登记资产 |
| 会话 4 | 旧 segments ↔ 新模型的存储/读写适配 | 保留旧库 ID、分类、引用、教师编辑与 AI 状态；不能通过扁平字符串往返丢结构 |

各模块内部可以增加函数，但不能改变边界对象。新依赖请求交会话 4 统一锁版本；公式源读取特有代码可放会话 1 私有目录，公共编译器由会话 4 独占维护。

导入 services 最少提供 `compile_formula(latex, source_ref=None)` 与 `register_asset(local_path, preview_path=None)`。资产登记返回 Asset，并在服务器私有清单保存 ID→文件映射；导入报告给出 `asset_manifest_path` 供集成方读取。该内部路径必须位于此次 workspace 内，不接受来自 AI/浏览器的路径。会话 1 将实际原媒体与清单一并交付，不能只交网页 URL 导致会话 4 找不到可导出的原图。

编译结果：`{ok, latex, mathml, omml, compiler_version, diagnostics}`。失败时 mathml/omml 可以 null，diagnostics 必须有原因；不能返回空公式且 ok=true。服务端检查网页显示与 Word 转换的支持范围；库只能显示、不能导出的命令要明确拒绝或标为待处理。

## 4. 网页与服务端接口

以下是新内容 API 约定，均由会话 4 接入 `app.py`。原 AI 作业路由保留，由会话 3 交付内容适配模块，不另造第二套作业系统。

| 接口 | 请求/响应 |
| --- | --- |
| `GET /api/v2/content/questions/<question_id>` | `{schema_version:1, question, assets}` |
| `PUT /api/v2/content/questions/<question_id>` | 请求 `{schema_version:1, expected_revision, question}`；成功返回最新 question/资产；允许编辑字段明确校验，不信任客户端传入分类或原库身份 |
| `POST /api/v2/content/formulas/compile` | `{latex, source_ref, request_id}`；返回编译结果及同一 request_id，不写题库 |
| `GET /api/v2/content/papers/<paper_id>` | 当前版本 PaperBundle |
| `POST /api/v2/content/papers/<paper_id>/export` | `{expected_revisions, options}`，expected_revisions 为题目 ID→版本的映射；返回 `{artifact_id, download_url, report}` |

上传/导入继续复用现有入口，经会话 4 接入会话 1 模块。任务式导入若有新状态字段由会话 1 提出、会话 4 落实，不暗改已有响应。

错误统一为 `{error:{code,message,details},request_id}`；格式错误 400，内容验证失败 422，版本冲突 409。未找到资源 404；服务端失败不能伪装成保存成功。无 request_id 时返回 null。

保存为整题原子更新；expected_revision 不一致拒绝覆盖。公式编译响应必须先核对当前文档、节点和编辑会话，过期响应直接丢弃。导出锁定保存版本快照，不能拼接几个不同时间版本；未保存修改先保存或明确另导出草稿，不假称“当前已保存题目”。

模拟服务必须实现这些成功、失败与冲突响应。会话 2/3 可以独立模拟，不必等待真实后端启动。

## 5. GLM 输入与返回边界

请求提供：协议版本、当前题目内容、允许资产清单、改写要求、教师意见与必要图片。既有题目画像/独立核对可保留，但不能代替完整公式上下文。

返回 `{schema_version:1, candidate:{body,options,subquestions,answers,explanations}, warnings:[]}`，使用第 2 节内容类型。服务器分配候选 ID、新节点 ID、版本与状态；重新关联 target_id/资产并验证。不允许模型改变母题的身份、分类、教师确认状态或文件路径。

回答与小问/空的关联必须保留：允许候选内临时 ID，后端统一映射为新的稳定 ID，并检查悬空或重复引用。不能只保留段落文字而删除关联。

兼容旧字符串输出时先保守识别明确公式，无法确定则待核对；禁止“剩余英文一律公式”。GLM 看图须核对实际型号能力。返回 JSON 成功不等于 LaTeX 可编译，更不等于题目正确。

## 6. 共同样例与契约检查

会话 4 维护只读 `fixtures/word_latex_v1/`，各会话测试在私有副本工作。每个样例记录 synthetic/real、来源摘要与预期节点：

- 普通题干＋NaOH/H₂O 上下标、独立用量单位与 pH 比较。
- 带温度/加热条件的完整反应式、离子电荷与括号组下标。
- 四个选项，其中一个有行内图片。
- 同一题两个小问，图片在小问前后；答案对应作答位置。
- 合并单元格表格，单元格含公式/配图；跨页控制另有长表格。
- 纯图片公式候选、未知字体符号、编译失败、缺失资产、保存版本冲突。
- GLM 规范响应、字符串兼容响应、截断/错误格式、异步取消。

至少验证：ID 唯一、所有目标/资产引用存在、表格跨度一致、状态合法、JSON 反斜杠保留、无静默内容损失、修改失效旧编译缓存。网页/导出分别检查可读性与原生结构。

会话 1 可新增真实样例建议，会话 4 纳入共享 fixtures；不能偷偷覆盖别人依赖的固定样例。mock 的成功结果也应来自验证过的固定编译结果或明确标记的替身，不把“假的 ok”当转换证明。

## 7. 文件所有权、交付与集成

| 会话 | 可修改范围（建议的新目录待创建） |
| --- | --- |
| 1 | `docx_import.py`、`question_import_v2/`、`tests/test_word_import_v2*.py`、`docs/word-latex/session-1/` |
| 2 | `static/question_editor_v2/`、`tests/word_editor_v2/`、`docs/word-latex/session-2/`；现有 KaTeX/MathLive 资源只读复用 |
| 3 | `aivariant.py`、`aianswers.py`、`ai_formula_v2/`、`tests/test_ai_formula_v2*.py`、`docs/word-latex/session-3/` |
| 4 | `app.py`、`banklib.py`、`document_display.py`、`formula_engine/`、`question_content/`、`word_export_v2/`、依赖配置、共享 fixtures、契约、集成测试及其他现有测试的必要适配 |

实验源码与用户稿默认只读；要修改实验由提出者明确交接给会话 4。`question_analysis.py` 等未分配文件默认不改，需要时通过接入请求确定唯一负责人。

每份交付必须有 `STATUS.md`：共同基线、schema_version、接口入口、改动文件、验证命令/结果、样例摘要、已知限制、接入请求、正式数据写入情况。可用状态：独立开发中 / 可以联调 / 联调通过 / 待用户查看。

会话 4 集成时核对上述清单，在私有数据库复测连接。产物缺契约版本或只演示一个孤立页面不能宣称模块完成。基线/协议变更逐项处理，不以整文件覆盖解决冲突。

四个会话不是自动互通聊天的代理。使用持久文件/提交交付，用户在各会话粘贴指示。读取其他会话状态不等于获准自动给那个会话发消息。
