# 共享固定样例 v1

`synthetic/bundle.json`：4 题合成控制，普通文字/上下标、pH、900℃反应条件、括号组和离子电荷、四选项与行内图、两个小问、作答位置答案、合并单元格及跨页长表格。`apparatus.png` 是脚本绘制的合成配图，不是真题装置图。`compiled.json` 是当前公共编译桥实际成功的 7 种固定结果，含编译器/XSL 摘要；不是假的 ok。失败/缺来源/版本冲突控制见 failure_cases.json。

`real/` 是可选的本机私有原卷片段及来源记录，已忽略，不随公共源码发布。运行自动验证不依赖这些真实内容。服务端原生公式来源记录不允许浏览器或 AI 提交；片段导出控制不能视为完整试卷分题或答案正确性验收。

固定样例只读使用，不在这里保存编辑稿。重建需明确版本变更：`scripts/build_shared_fixtures.py`、`scripts/build_real_excerpt.py`。真实样例不复制整份原件、不改 mirror；私有导出/渲染在 `.work/session-4`。
