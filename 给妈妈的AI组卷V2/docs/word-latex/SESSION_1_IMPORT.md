# 会话 1：Word 导入与分题

先读项目根目录 `LATEX_HANDOFF.md`、`WORD_LATEX_REFACTOR_PLAN.md` 的第 3、8、9、11 节，以及本目录 `PARALLEL_CONTRACT.md`。

项目根目录均指 `C:/Users/yeche/Documents/ChatGPT/给妈妈的AI组卷/给妈妈的AI组卷V2`。只按需复制有用文件，旧代码只读参考；原试卷统一只读读取 `C:/Users/yeche/Documents/ChatGPT/给妈妈的AI组卷/chem-papers/mirror`。

## 任务与边界

负责四步的第一步：原始 DOC/DOCX 结构恢复、分题、题内小问、选项/表格/配图归属与答案关联。公式来源恢复为 LaTeX，纯图片只生成待确认候选。

可修改：`docx_import.py`、新增 `question_import_v2/`、`tests/test_word_import_v2*.py`、`docs/word-latex/session-1/`。不要直接修改 `app.py`、`banklib.py`、公共编译器或别的会话文件。

先核对 V2 共同基线及复制清单；按需复制自己负责的源码，不整份复制旧项目，不覆盖其他会话成果。测试数据与输出放 V2 的 `.work/session-1`，端口如有需要建议 8901，先检查占用。

## 可以马上并行做

1. 盘点已有真实样本及当前解析器支持/限制；用户全部原文件未到时不停止代表性样本开发。
2. 在副本上解析 Word 段落、编号、表格、脚标、公式对象及媒体锚点，保留来源定位。
3. 按接口 v1 输出 ImportResult。依赖公共服务时通过 services 参数注入固定替身，而不是各建一个生产编译器。
4. 用固定样例测试分题、小问与答案归属；明确列出不支持或需核对的原结构。
5. 真实文件就绪后做整卷对照；旧库来源匹配可独立分析，但只输出映射/冲突报告，不执行正式迁移。

## 交付与衔接

- `import_paper` 可调用入口与说明。
- 私有数据目录中的真实 ImportResult/资产清单和分题核对报告，附来源摘要。
- 模块测试结果，原件不变、内容不丢失的证据。
- 交会话 2/4 的完整 PaperBundle。用户可指示这两个会话读取具体产物路径，无须等本会话所有批量工作结束。
- 对共享编译器/旧存储入口的改动需求写 `docs/word-latex/session-1/INTEGRATION_REQUESTS.md`，会话 4 实际接线。

在 `docs/word-latex/session-1/STATUS.md` 写共同基线、协议版本、当前产物位置、能否联调、未解决项及正式数据是否写入。状态文件不要附凭据。

完成标准：代表性整卷边界/小问/答案及媒体归属对照通过，契约合法，未支持内容可定位且保留；不能只报告“成功导入 N 道题”。

## 禁止混淆的状态

当前实验已验证一部分公式转换，不等于通用导入已完成。原件目录 mirror 已由用户确认，无须重复询问，先盘点其中 DOC/DOCX。不得依据题意补关系符号，不能修改旧根题库、用户实验稿或启动正式迁移。

## 用户可复制的开工提示

请创建或复用 `C:/Users/yeche/Documents/ChatGPT/给妈妈的AI组卷/给妈妈的AI组卷V2` 作为唯一开发目录，只按需从旧项目复制有用文件，原试卷统一只读读取 `C:/Users/yeche/Documents/ChatGPT/给妈妈的AI组卷/chem-papers/mirror`。

请阅读 docs/word-latex/SESSION_1_IMPORT.md、PARALLEL_CONTRACT.md 和根目录交接说明，按会话 1 的分工开始导入模块开发。先确认共同基线和私有测试目录；遵守文件所有权，用接口替身推进独立工作，保留原文件，不写正式题库。完成后更新会话 1 的 STATUS.md，给我真实整卷结果、未解决清单以及交给会话 2/4 的具体产物路径。
