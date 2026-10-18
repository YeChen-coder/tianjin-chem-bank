# 给妈妈的 AI 组卷 V2

当前运行和开发版本。保留原来的蓝色题库界面、筛选与勾选组卷，底层按 Word 导入、结构化内容与公式编辑、AI 变式、Word 导出四个模块重构。仓库根目录的旧程序保留作历史参考。

## 本次程序更新

- Word 导入清理题干前的原题号、分值及重复的字母占位选项；原题号保留在来源记录中，非试题材料不导入。
- 正文、LaTeX 公式、图片、表格、选项、小问和答案统一保存。MathLive 编辑与 KaTeX 显示沿用原题库交互；改进化学式中英文括号混用和符号处理。
- 保留分类、人工修改、母题候选版本及组卷排序；保存和导出检查内容版本。
- GLM 变式保留结构与图片，教师采纳前保留审核状态，不把自动校验当成科学答案正确性保证。
- Word 导出包含可编辑原生公式、图片、合并表格和作答空；按新选题顺序编号，支持逐题或集中答案。
- Grok 接口读取真实结构化公式、选项和图片，修复带图题因旧 segments 为空被误报缺图的问题；损坏图片和过期选题计划仍会被拦截。

## 安装与运行

使用 Python 3.11 或以上版本，在本目录创建本机虚拟环境：

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-lock.txt
.venv/Scripts/python.exe app.py serve
```

Linux/macOS 对应使用 `.venv/bin/python`。浏览器打开 <http://127.0.0.1:8904/>，可通过 `CHEM_PORT` 指定端口。

运行数据默认位于 `.work/session-4/`，包括配套的 `bank.sqlite` 和 `content-v2/content.sqlite`。不要把旧版单库直接放入新版目录。公共仓库不含私人题库、图片全集、原卷或密钥；首次启动不会导入真实题目，可从页面导入自己的 Word。已部署的私有发布包含 10226 道题、8010 条图片记录和 348 份登记来源，这些运行数据不随源码公开。

需要 AI 时，将 `CHEM_GLM_CONFIG` 指向本机现有的 GLM 配置文件，配置 `GLM_API_KEY` 等字段，不要提交该文件。没有配置仍可浏览、编辑和组卷。`CHEM_DISABLE_AI=1` 关闭 AI；默认不恢复旧任务队列，由用户主动提交时启动 worker。

原 `.doc` 转换需要本机 LibreOffice。Office 转换与渲染工具见 `scripts/`；跨机器部署须绑定本机图片/原卷路径，不复制另一台机器的虚拟环境或沿用它的绝对路径。

## 验证

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests
```

全套 131 项自动验证，包括 6 项 Grok 结构化带图组卷验证。测试使用临时题库、合成材料及本地接口，不调用付费 AI。公开合成材料在 `fixtures/word_latex_v1/synthetic/`；真实原卷片段、私有部署日志和数据清单不公开。

## 模块与接口

- `question_import_v2/`：Word 解析、分题、来源追踪及导入清理。
- `question_content/`：结构化契约、校验、双库桥接、编辑和工作流。
- `ai_formula_v2/`：GLM 变式及审核。
- `word_export_v2/`、`formula_engine/`：公式转换和 Word 导出。
- `static/`：前端、公式编辑器、本地资源和许可证。
- [公共接口与启动配置](docs/word-latex/PUBLIC_API.md)、[内容契约](docs/word-latex/PARALLEL_CONTRACT.md)。

第三方转换代码、KaTeX、MathLive 和字体许可证随对应目录保留。Office 自带转换文件属于 Office，不随本仓库分发。

## 已知限制

分类、分题边界、公式和答案对应仍需教师核对。整题图片像素中的原题号和分值不会自动擦除。旧版全题库批量补答案未接入新协议，保持禁用；答案与解析可在题卡编辑器中保存。旧卷和人工修改不能按相同数字 ID 合并，应保留备份并在确认来源与内容后迁移。
