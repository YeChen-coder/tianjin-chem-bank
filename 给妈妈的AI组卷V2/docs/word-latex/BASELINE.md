# 公共基线 S4-20261010-v1

接口版本：`schema_version: 1`。会话 4 正在交付公共校验、公式编译、私有存储与原生 Word 导出。

开发目录是 V2，旧项目和 mirror 只读。新环境为 V2 `.venv`；私有数据默认 `.work/session-4`。依赖仅由会话 4 管理。已有目标文件跳过复制，不覆盖其他会话成果。逐文件来源与复制时摘要见 `COPY_MANIFEST.json`。

旧主入口、存储、显示以及固定网页资源按需保留。会话 1/3 所有的导入与 AI 文件由各自会话交付；共享入口接线采用延迟加载，缺失模块明确返回未就绪。禁止从父目录隐式导入旧模块。

公共 Python 入口：`question_content.validate_bundle`、`validate_question`、`compile_formula`；导出入口：`word_export_v2.export_paper`。接口及运行方式在 `PUBLIC_API.md`。功能/结构/视觉证据分别记录，公共底座可联调不表示四模块已经验收。
