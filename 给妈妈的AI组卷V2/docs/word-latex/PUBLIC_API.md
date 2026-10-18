# 公共接口 S4-20261010-v1

协议为 `PARALLEL_CONTRACT.md` 的 schema v1，已实现以下最小底座。正式数据没有写入。

## 环境与启动

在 V2 运行 `.venv/Scripts/python.exe app.py serve`。默认监听 `127.0.0.1:8904`，使用 `.work/session-4/bank.sqlite` 和 `.work/session-4/content-v2/content.sqlite`。启动不自动加载样例；登记已导入试卷到原题库界面时保留老师保存的选题。提交改写时启动原 AI worker；可用 `CHEM_ENABLE_AI_WORKERS=1` 在启动时恢复队列。AI 配置通过 `CHEM_GLM_CONFIG` 显式指定现有 GLM 配置文件，仅读取所需字段；不复制凭据，不读取正式旧库。根页面复用原蓝色题库；`/workflow` 仅为早期技术对照页。

`.venv/Scripts/python.exe scripts/export_controls.py` 显式登记合成固定样例，并输出 `.work/session-4/outputs/controls.docx` 与报告。重复运行保留已保存题目，拒绝覆盖重复导入。其他会话测试应另建自己的 ContentStore，不共用会话 4 的库。

## Python 入口

S1 导入现在只把原题号保存在 `Question.number`、来源编号及原文件记录中；清理正文前缀分值和前面已展示选项内容时的字母重复项。题卡不追加来源编号。Word 导出 `auto_number=true` 按新选题顺序编号，`false` 不加编号。纯图片题的像素文字仍保留，需另外处理。清理日志在导入产物 `content-cleanup.json`。

```python
from question_content import validate_bundle, validate_question, compile_formula
report = validate_bundle(bundle)  # {ok, errors:[{path,code,message}], issues:Issue[], warnings:[]}
report = validate_question(question, assets)  # assets 是 Asset 数组或 ID 集合
result = compile_formula(latex, source_ref=None)
# {ok,latex,mathml,omml,compiler_version,diagnostics}; 失败明确返回 ok=False

from question_content.storage import ContentStore
services = ContentStore('.work/session-1/private-content')
# 导入服务可注入：
# ContentStore 自带 compile_formula，使用同一公共桥。
asset = services.register_asset(local_path, preview_path=None)
services.register_source(source_ref, {'latex': original_latex, 'omml': original_omml})
manifest_path = services.write_asset_manifest()
services.put_bundle(bundle)  # 仅受信任的 Python 集成入口，无任意路径 HTTP API

from question_content.http_api import AssetResolver
from word_export_v2 import export_paper
result = export_paper(bundle, '.work/session-4/out.docx', {}, AssetResolver(services))
# {output_path,report_path,report}；report 有 revisions/counts/issues/visual_verified
```

`ContentError` 含 code/status/details。资产文件复制到私有目录、按原字节 SHA256 登记；预览字节与原媒体的摘要不会混用。清单格式是 `[{id,json,path,preview}]`，其中 json 是序列化 Asset，path/preview 是服务器私有路径；不得传给 AI 或浏览器。会话 1 若有自己的清单格式，可提出接入说明，不必为此变更 PaperBundle。

`validate_question(question)` 省略资产时仅校验引用类型，完整资产存在性校验须传入允许清单。`validate_bundle` 总是检查全部图片引用、文档节点唯一性、表格覆盖、答案目标与问题记录引用。支持递归小问和嵌套表格；不会检查化学答案是否正确。

校验报告的 errors 提供精确 JSON 路径，issues 是同一错误的契约 Issue 表达，供 AI/前端消费；两者都不会写回输入。旧内容桥是 `question_content.legacy.question_from_legacy(row, store, asset_register=None, metadata=None)` 与 `legacy_segments_from_question(question, store)`。未编辑的旧内容精确返回原 segments，并在私有映射中保留整行和人工分类/审核元数据；编辑后拒绝扁平写回，必须存入 v2。此入口不是正式迁移程序。

## HTTP 内容接口

实现契约的 GET question、PUT question、POST formula compile、GET paper、POST paper export；另有 GET `/api/v2/content/assets/<asset_id>` 与 `/api/v2/content/artifacts/<artifact_id>`。静态资源由 `/static/` 提供。写请求需 JSON；不允许浏览器传 XML、任意磁盘路径或额外保存字段。

保存请求为 `{schema_version:1,expected_revision:0,question}`。revision/id/source/origin/issues 由服务端保护；成功 revision 加一、origin 改为 teacher。已有 confirmed 公式修改后应提交 review_required，浏览器不得自行把新公式声明 confirmed。新增公式 source_ref 设 null，不能伪造原件引用。保存修改仅发生在私有 content.sqlite，旧分类/组卷/审核不由新接口重写。

导出请求为 `{expected_revisions:{"q-id":1}, options:{}}`，版本映射必须完整匹配整卷。读取在同一事务中锁定快照，随后导出的报告注明该版本；并发新保存不改变已取得的快照。409 表示重新读取/保存后再导出。

错误为 `{error:{code,message,details},request_id}`。400 格式错误，422 内容验证/编译失败，409 版本冲突，404 不存在，500 服务端失败。公式编译成功返回 result 与原 request_id；失败 details 保留编译结果和诊断。前端必须丢弃过期响应。不会返回假的成功公式。

## Word 导出与支持范围

原生 Word 段落、OMML 公式、表格（行列合并/嵌套）、正文/选项/小问的行内及独立图片、文字上下标、作答空与单独答案页已实现。图片按原比例，过宽表格默认等比缩小并报告。options：include_answers/include_explanations（默认 true）、table_width_policy（scale/error）、fail_on_issues（默认 false）、repeat_table_header（默认 false）。未解决来源/缺图/编译失败保留可见提示和报告；fail_on_issues=true 则不生成文档。

公式唯一入口在 question_content；底层复用修复后的 formula_engine。Windows 安装 Office 时使用本机 MML2OMML.XSL，许可证属于 Office，不复制分发。无 Office 时只支持基础化学/数学结构，复杂结构明确失败。缓存含完整 LaTeX、转换版本和 XSL 摘要；编辑公式不会沿用旧缓存。浏览器本地 KaTeX 0.16.22 / MathLive 资源及许可证已复制。

compile 的 ok 仅表示结构转换通过；导出 report.ok 仅表示无 error，visual_verified 默认 false。视觉结果须针对该文档摘要另行登记，不把自动结构通过等同实际 Word 或科学内容通过。

## 原题库界面桥接

`GET /api/questions` 与单题读取附加 `structured` 和 `content_context`，原分类、整数 ID 和试卷顺序继续使用私有 bank.sqlite；正文以 content-v2 保存的结构为准，旧 body 仅为搜索摘要。

- `POST /api/v2/content/import` 接受 `{name,content_base64}`，返回导入收据和原界面可打开的 `paper`；重复导入不覆盖编辑。
- `GET /api/v2/content/bank/questions/<整数ID>/candidates` 返回母题下候选、完整结构、审核状态与活动任务。
- `POST /api/v2/content/bank/questions/<整数ID>/ai` 接受 `{expected_revision,intensity,teacher_feedback}`。
- `POST /api/v2/content/bank/candidates/<候选ID>/select` 接受 `{paper_id,on,expected_source_revision,expected_revision}`；加入时保留母题并紧随母题，拒绝失败和过期候选；移出只改试卷选择。
- `POST /api/v2/content/bank/questions/<整数ID>/split` 接受 `{expected_revision,at,paper_id}`；仅支持无关联答案/小问/选项的独立题干段落边界。
- `POST /api/v2/content/bank/selection/export` 接受 `{ids,title,expected_revisions,keep_source,options}`；完整版本映射不一致返回 409。options 新增 `auto_number` 布尔和 `answers_position`（`inline` 或 `end`），沿用答案、解析等开关。

旧版批量补答案写入接口在 V2 明确返回 409，避免只修改旧文本而结构化 Word 导出仍用旧答案。可通过新的题卡编辑器编辑并保存答案和解析。

## Grok 组卷接口（结构化内容修补）

`GET /api/bot/info`、`POST /api/bot/search`、`POST /api/bot/questions`、`POST /api/bot/papers` 继续使用整数题目 ID。V2 路由向 Bot 桥传入当前 `CONTENT_API.bank`，不能只读取旧 `body`/`segments` 摘要。

检索和单题结果包含 `structured`（已保存的完整结构与资产记录）、`content_question_id`、`content_revision`，以及可通过本机 `/api/v2/content/assets/<asset_id>` 读取的图片 URL。正文、选项、表格、小问和答案中的图片均按结构化引用查找；原件摘要或预览不可用时标记 `available=false`，不能带入新试卷。

`bank_revision` 同时绑定搜索摘要及结构化题目版本，公式和图片排版变更也使旧计划失效。创建试卷时再次核验版本和图片，版本不一致返回 409；格式非法的版本返回 400。`dry_run=true` 只预检，不新增试卷，实际创建沿用 `request_id` 幂等机制。
