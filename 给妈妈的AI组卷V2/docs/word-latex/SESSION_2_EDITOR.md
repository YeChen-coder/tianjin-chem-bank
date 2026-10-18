# 会话 2：网页展示、编辑与保存交互

先读项目根目录 `LATEX_HANDOFF.md`、`WORD_LATEX_REFACTOR_PLAN.md` 的第 4、7、9、11 节，以及本目录 `PARALLEL_CONTRACT.md`。

项目根目录均指 `C:/Users/yeche/Documents/ChatGPT/给妈妈的AI组卷/给妈妈的AI组卷V2`。只按需复制有用文件，旧代码只读参考；原试卷统一只读读取 `C:/Users/yeche/Documents/ChatGPT/给妈妈的AI组卷/chem-papers/mirror`。

## 任务与边界

负责四步的第二步：真实整卷/卡片显示，文字、可视化公式、图片、表格及答案解析编辑，保存/取消/版本冲突交互。用户不需要学习 LaTeX。

可修改：新增 `static/question_editor_v2/`、`tests/word_editor_v2/`、`docs/word-latex/session-2/`。本地 KaTeX/MathLive 资源只读复用。不要直接改 `app.py` 内联 PAGE、`banklib.py` 或别人的后端；入口接线交会话 4。

先确认 V2 共同基线与复制清单，测试数据/输出放 `.work/session-2`；mock 服务建议端口 8902，先检查占用。用户 8893 验收稿不修改。公共网页资源由会话 4 按需复制，其他会话不重复覆盖。

## 可以马上并行做

1. 根据固定 PaperBundle 样例搭建 mountQuestionEditor / mountPaperPreview，渲染器两处复用。
2. 按接口 v1 模拟读取、编译、保存、409 冲突及导出响应。未有后端时这些工作也能完成。
3. 完成可视化公式编辑、段落间图片放置、选项/小问/表格编辑；保留 ID 和结构。
4. 验证取消、撤销、刷新、保存失败、迟到响应与跨题编辑，不能丢公式或选区外文字。
5. 同步展示正文、答案、解析里的公式；整卷是主要验收入口，不只给一个孤立题目演示。

## 交付与衔接

- 前端模块和独立模拟页面的启动方式。
- 含成功、验证失败和冲突的 mock API；只用于私有测试，不伪装生产保存成功。
- 编辑前后结构化 JSON 样例、桌面/手机/取消及保存测试报告。
- 将会话 1 的真实 bundle 替换 mock 数据，查图片与表格归属；可先联调一套真实试卷。
- 接收会话 3 的候选 Question，复用同一渲染/编辑器，不增加第二套 AI 题目显示逻辑。
- 向会话 4 提交资源入口与调用接口清单，由它接入主项目与保存桥。

在 `docs/word-latex/session-2/STATUS.md` 记录基线、协议版本、界面入口、真实/mock 数据范围、能否联调和限制。共享文件需求写同目录 `INTEGRATION_REQUESTS.md`。

完成标准：无技术背景的用户能改公式/文字/图片位置并保存；表格必要结构有效；真实整卷可读，编辑后内容和关联保留。mock 页面通过只能叫“可以联调”，真实保存和导出尚未接上时不能宣称主项目完成。

## 用户可复制的开工提示

请创建或复用 `C:/Users/yeche/Documents/ChatGPT/给妈妈的AI组卷/给妈妈的AI组卷V2` 作为唯一开发目录，只按需从旧项目复制有用文件，原试卷统一只读读取 `C:/Users/yeche/Documents/ChatGPT/给妈妈的AI组卷/chem-papers/mirror`。

请阅读 docs/word-latex/SESSION_2_EDITOR.md、PARALLEL_CONTRACT.md 和根目录交接说明，按会话 2 分工开始网页展示编辑模块。先用共同样例和 mock API 并行开发，不等待导入或后端全部完成；遵守文件所有权，不改正式题库或用户验收稿。给我整卷操作界面，更新 STATUS.md，并列出交给会话 4 的接入清单。
