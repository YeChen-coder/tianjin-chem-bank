# 会话 2 测试报告

交付日期：2026-10-11。公共基线 S4-20261010-v1；协议 v1。测试仅写 .work/session-2，各真实来源和公共资源只读；未启动 AI 任务或正式迁移。

## 浏览器交互

固定本地资源，Edge headless + bundled Playwright，1440×1000 与 390×844。主报告：`.work/session-2/qa/browser-report.json`；截图同目录 `*-desktop.png`、`*-mobile.png`、`formula-mobile.png`。

| 检查 | 结果 |
| --- | --- |
| 会话 1 真实整卷 | 26 卡片、96 公式、12 图片、4 表格；与 bundle 节点计数一致，无显示错误、坏图 |
| 历史整卷 | 26 题 + 卷首说明，91 公式/12 图片/4 表格；178 原块账本完整 |
| 公共固定样例 | 4 题、9 公式/4 图片位置/2 表格；图片由测试路由提供对应 fixtures 原字节，没有模拟保存成功 |
| 普通文字 | 对话框取消和全题取消保留原稿；修改不碰相邻公式/其他 inlines；撤销/重做与刷新草稿恢复通过 |
| 选中文字转公式 | 使用 textarea 的 UTF-16 选区；中文、emoji、公式前后文字和邻接节点 ID 保留 |
| 公式 | MathLive 数值修改、完整源码往返、mock 保存后刷新；编译 422 与 request_id 不符不改变草稿 |
| 取消与迟到结果 | 取消编译后切到另一题，无视不遵守 AbortSignal 的迟到结果；新公式段落取消不插入空块 |
| 图片 | 小问之间移动/缩放保留 ID、资产和比例；插入登记资产在预览解码后使用真实比例 |
| 表格 | 横向合并格拆分/撤销，向下合并保留两格内容，增加行列；嵌套表格可读且不撑大手机页面 |
| 保存 | 500、409 不丢草稿；冲突提供最新/草稿对照；非法跨度提交真实公共校验产生 422；重复保存仅一次 PUT |
| 刷新竞争 | 整卷读取在本地新编辑之后返回，保留新草稿并提示，不重新挂载覆盖 |
| AI 候选/销毁 | origin=ai 结构候选用同一编辑器；销毁后的迟到保存响应不触发 onSaved |
| 导出 | 未保存修改阻止导出；mock 明示只返回保存版本 JSON 快照 |
| 手机与资源 | 页面无横向溢出；长公式/表格内部滚动；无 pageerror，无外部网络请求 |

控制样例刻意有一个 latex=null/status=unsupported 公式，显示“尚未恢复”并保留 source_ref。这是预期提示，不作为恢复成功记录。

## 真实接口联调

命令会启动临时本机端口，将会话 1 学生卷及资产注册到独立 ContentStore，再用会话 4 的真实 ContentAPI/编译器/导出模块处理浏览器操作。进程结束后关闭临时服务，8902 mock 不变。

最新证据：`.work/session-2/integration/46b4481e72994c8e908b07af0dea3d36/`。

- 实际公共编译器 `s4-latex2mathml-3.78.1-v2/fdbb663cbbf0ab0b`，返回真实 MathML/OMML；不是 mock。
- 网页编辑真实导入结构的一条公式为测试值，保存版本从 0 到 1；来源、题目 ID、选项与答案结构保留。
- 刷新读回一致；单独 Python 进程重新打开 content.sqlite，与完整 after.json 一致。
- 下载实际 DOCX，与保存快照一致：96 个 m:oMath、4 个 w:tbl、12 个 w:drawing。版本及编译日志记录在 browser-integration-report.json。
- Word 文件为 browser-edited-paper.docx，本会话没有做该文件的 Office 打开/页面渲染；wordVisualVerified=false。科学答案和原件逐页视觉核对未由本测试证明。

编辑前后 JSON：私有 qa/edit-before.json 与 edit-after.json，另有真实联调 before.json 与 after.json。永久合成示例在本目录 examples/；测试值不是原卷修正建议。

## 复现命令

先启动 README 中的 8902 mock。Node/Playwright 路径用 load_workspace_dependencies 发现，本次路径如下：

```powershell
$env:NODE_PATH='C:\Users\yeche\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules'
& 'C:\Users\yeche\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' tests/word_editor_v2/browser_qa.cjs

& .venv/Scripts/python.exe -X utf8 tests/word_editor_v2/integration_qa.py `
  --node 'C:\Users\yeche\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' `
  --node-path 'C:\Users\yeche\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules'
```

浏览器交互脚本会修改自己的合成 mock 题目并递增测试版本；不修改真实导入卷、原件或正式数据。真实 API 测试每次生成新的隔离运行目录。没有新依赖安装要求。
