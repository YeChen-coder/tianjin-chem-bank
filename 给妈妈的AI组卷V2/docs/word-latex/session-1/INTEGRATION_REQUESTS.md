# 会话 1 接入请求

日期：2026-10-10。公共基线：S4-20261010-v1。schema_version：1。

1. 用 `question_import_v2.import_paper(source_path, workspace, services)` 接原有上传入口。生产 `services` 注入 `question_content.compile_formula` 和资产登记；不使用 `EvidenceServices` 的失败编译替身。导入不写题库，调用方先验证 bundle，再由原存储管理重复导入、人工编辑与分类关联。
2. 导入返回 `report.asset_manifest_path`，格式为 `{asset_id: {asset, local_path, preview_path, package_part}}`。路径全在此次私有 workspace 下；会话 4 可读取并逐项 `register_asset`，不能把这个清单给浏览器/AI。包含 OLE 预览原图，未引用预览不算正文丢图。
3. `source_records_path` 是 `{source_ref: record}`。OMML 记录提供 `xml/omml/mathml/latex/locator/source_sha256`；OLE 原二进制在 `original_copy_path` 指向的原包 `package_part` 中，另有 `mathml/latex/fallback_blocks`。文字恢复记录有原 `parts`。`structural_label_remainder` 记录用 `parent_source_ref` 指回完整原对象，只有剩余 latex，不可复用带旧小问标记的整段 OMML。
4. 持久化公式前逐项调用 `register_source` 或保留可读取的 source-records 侧文件。未知结构、未匹配答案、未分题说明不可丢掉：卷首/栏目在 `retained_content_path`，完整原块序列在 `ordered_document_path`。只展示 PaperBundle 会省略卷首说明；整卷阅读模式应额外读取 ordered-document 或 retained-content。公共纸面元数据若要正式携带卷首说明，请由会话 4 扩展契约后统一调整，本会话不暗加字段。
5. 旧 DOC 的实际转换由会话 4 顺序处理，注入可选 `services.convert_doc(private_copy_path, private_output_docx_path)`。模块不会自行启动 Word。转换器禁宏、只用隐藏隔离实例，不接管现有 Word；输出必须含 Word document.xml。未注入时返回 unsupported_source 并保留原件副本，不能声称 DOC 已恢复。
6. WMF/EMF 的 PNG 预览并非本机所有源文件都能用 Pillow 生成；资产服务需提供转换或显示明确回退。本次 38+25 个 OLE 的源结构恢复成功，但纯图片未执行 OCR，不能把 OCR_REVIEW 自动清除。
7. 需要会话 2/4 接入分题核对、合并/拆分和人工改归属操作。程序处理状态与教师确认分开；本次所有真实样本 user_confirmation=pending。不要用重导入覆盖已编辑版本。

## 可直接取用的产物

全部位于 V2 `.work/session-1/real-paper/<SHA256>/`，每份包括 `import-result.json`、资产原文件/清单、源记录、原件副本、完整块序列、分题边界、公共协议校验和 `source-audit.json`。

- 学生整卷：`e4fe1b55db17b1d7ba694e2fd0cbbac18d8c2acc08f93521530df1c765c3cc8c`。
- 带明确答案/解析整卷（首选联调）：`560f47df6e1832032bdfcce2bd10de460be8d2b5c220ef2fb58f9e1941b764c1`。
- 无明确答案标记的教师卷：`4b12eee89574747ded2aca5114fcaa5a1f4d3faae97ad03d70a9596adf400ac4`；其中说明文字的角色需要教师确认。
- 额外诊断副本：`1054df8b4c892b1af8f43c5712d588490dd9328a48ced09df6d7a0c537e5bdba`。卷尾含重复 26 题及无编号追加练习，不能把其全部归属当成通过；源稿完整保留，先用上面的干净副本联调。

未改 app.py/banklib.py/公共编译器，未运行正式迁移，没有跨会话自动发消息。
