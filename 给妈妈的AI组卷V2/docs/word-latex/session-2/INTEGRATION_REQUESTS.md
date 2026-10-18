# 会话 4 接入清单

模块基线 S4-20261010-v1 / schema_version 1；会话 2 未修改公共入口、旧代码或数据库。以下由会话 4 在主程序落实。

1. 加载 `/static/question_editor_v2/editor.css`，以 ES module 导入 `editor.js`。复用固定本地 KaTeX 0.16.22、MathLive 0.111.1 和字体；在调用前设置 `MathfieldElement.fontsDirectory='/static/mathlive/fonts'`、`soundsDirectory=null`。模拟页面的 `/vendor/` 地址不用于主程序。
2. 整卷入口读取真实 PaperBundle 并调用 `mountPaperPreview`，卡片、GLM 候选调用 `mountQuestionEditor`。不要把 AI 候选渲染成另一套扁平 HTML；待候选服务器 ID/版本和资产登记完成后复用这一入口。
3. 使用 `createContentApi()` 接五个公共 HTTP 路由。保存响应需要完整 `schema_version/question/assets`，最新版本用于更新主程序的题目引用；分类、旧库 ID、组卷、审核元数据仍由桥维护。本模块保留 id/source/origin/issues，修改已确认公式会提交 review_required，不伪造 confirmed。
4. 主程序切题、切卷、关闭编辑器时先检查实例 `isDirty/isSaving`，处理用户未保存内容后再 destroy。浏览器草稿是恢复辅助，服务器保存才是持久内容；已发出的 PUT 无法靠 AbortSignal保证撤销。
5. 设置按运行环境隔离的 `draftNamespace`。当前默认 `question-editor-v1:<question_id>`；模拟页面使用 session-2-demo。若同站点有多个数据库，主程序必须用不同 namespace。
6. 图片上传及替换资产由主程序资产入口处理，本模块接收登记过的资产清单。预览和下载 URL 要为同源 HTTP(S)；新增任意路径/HTML/XML 的浏览器入口不在本模块范围。
7. 将本目录测试结论纳入集成验收。会话 1 真实学生卷已经经浏览器 -> 真实编译/保存 -> DOCX 下载联通；数据库和导出均在会话 2 隔离目录，不能替代正式旧库桥验收。对该 DOCX 的 Word 页面检查交会话 4 顺序执行。
8. 更新公共基线/复制清单及根 README 的“仅有方案”说明，登记会话 2 新代码与少量测试材料。会话 2 没有覆盖这些公共文档；私有复用来源摘要见 COPY_MANIFEST.json，完整媒体/模型来源见 .work/session-2/*provenance.json。

当前无新增依赖请求，无公共模型字段变更请求。冲突交互采用对照后手动重编，不提供自动合并或强制覆盖。
