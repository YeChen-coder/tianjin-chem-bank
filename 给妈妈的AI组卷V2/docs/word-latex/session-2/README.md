# 会话 2 展示编辑模块

整卷界面：<http://127.0.0.1:8902/static/question_editor_v2/demo.html?paper=imported-paper>。服务运行时可用；保存只写 `.work/session-2/state.json`，公式编译和导出为页面明确提示的 mock。真实接口联调使用另一个临时服务器和隔离 ContentStore，不改变此模拟页面的含义。

## 教师操作

1. 点击文字修改这一段；点击公式中的数字或符号，用可视化公式框和常用模板修改。公式源码折叠在高级入口，日常操作不需要。
2. 点击图片设置宽度、对齐和放置区域；选择某段内容之前，可在段落间、选项、小问、表格单元格之间移动，保留资产 ID 和比例。
3. 段落右上角 `⋯` 可添加行内文字、公式、作答空或已有图片。内容区域末尾可以添加段落、独立图片和表格。点击选项或小问标记可改编号。
4. 表格设置可以调整列宽、新增行列、删除末尾空行列；单元格设置可向右/向下合并和拆分。合并保留两格内容，拆分保留在左上格。递归表格可显示和编辑。
5. 点“保存本题”提交整道题；“取消本题修改”恢复最近读到的保存版本。撤销/重做作用于本题未保存的操作。刷新后可选择恢复浏览器草稿。
6. 冲突时草稿保留，读取最新版本对照后手动重新编辑；不能强制覆盖。整卷存在未保存修改时，导出按钮提示先保存或取消。

宽表格和长公式在自己的区域内滚动，手机不需要横向拖动整页。图片上传/替换原媒体继续由主程序提供，本模块只选用已登记资产。

## 独立启动

在 V2 目录运行，使用会话 4 的环境，不安装新的公共依赖：

```powershell
# 首次建立私有控制样例及历史整卷样例；已有保存测试稿保留。
& .venv/Scripts/python.exe -X utf8 tests/word_editor_v2/prepare_fixtures.py

# 接入会话 1 的真实结果（需要该文件已存在）。
& .venv/Scripts/python.exe -X utf8 tests/word_editor_v2/prepare_fixtures.py --import-result '.work/session-1/real-paper/e4fe1b55db17b1d7ba694e2fd0cbbac18d8c2acc08f93521530df1c765c3cc8c/import-result.json'

# 先检查 8902 占用；已有本会话服务时直接使用，不重复启动。
& .venv/Scripts/python.exe -X utf8 tests/word_editor_v2/mock_server.py --port 8902
```

`--reset-state` 只用于明确重建会话 2 私有测试稿，会覆盖自己的 mock 保存结果，不应在有需保留的测试修改时使用。未指定该开关时，已有 state 不会被新 fixture 覆盖；新增导入 fixture 后如 state 已存在，需要先保留 state，再显式重建。无旧实验模型时仍可使用合成控制样例。

默认使用 V2 `static/katex` / `static/mathlive`；资源尚未复制时模拟服务可只读回退到旧项目同名固定资源。没有复制这些公共资源，也没有升级版本。

## 主程序调用

```html
<link rel="stylesheet" href="/static/katex/katex.min.css">
<link rel="stylesheet" href="/static/question_editor_v2/editor.css">
<script src="/static/katex/katex.min.js"></script>
<script src="/static/mathlive/mathlive.min.js"></script>
<div id="paper-editor"></div>
<script type="module">
import {mountPaperPreview, mountQuestionEditor, createContentApi}
  from '/static/question_editor_v2/editor.js';
MathfieldElement.fontsDirectory='/static/mathlive/fonts';
MathfieldElement.soundsDirectory=null;
const api=createContentApi();
const bundle=await api.getPaper(paperId); // paperId 由主程序提供
const view=mountPaperPreview(document.querySelector('#paper-editor'), {bundle,api});
// 离开页面、切换试卷或替换候选前，主程序先处理未保存修改，再调用 view.destroy()。
</script>
```

单题和服务器分配身份后的 AI 候选共用 `mountQuestionEditor(container,{question,assets,api,onSaved})`。输入被复制；调用方的原对象不会因本地编辑改变。`onSaved(question,assets)` 仅在真实成功响应后调用。销毁后取消请求，并忽略不遵守 AbortSignal 的迟到结果。发送中的 PUT 可能已经在服务器提交，销毁不能当作撤销服务器写入；下次读取核对。

返回值：单题 `getDraft/getSaved/isDirty/isSaving/save/cancel/readLatest/destroy`；整卷 `getBundle(includeDrafts=false)/editors/destroy`。`editors` 是题目 ID 到单题实例的 Map。`draftNamespace` 可由主程序指定，避免不同数据环境的本地草稿重名。

api 方法：`getQuestion(id,{signal})`、`saveQuestion(id,{schema_version,expected_revision,question},{signal})`、`compileFormula({latex,source_ref,request_id},{signal})`、`getPaper(id,{signal})`、`exportPaper(id,{expected_revisions,options},{signal})`。返回结构和错误状态遵循契约 v1；`createContentApi` 已实现 fetch 适配。可通过 headers 注入主程序要求的请求头。

样例、测试及限制见 [STATUS.md](STATUS.md) 和 [TEST_REPORT.md](TEST_REPORT.md)。接线清单见 [INTEGRATION_REQUESTS.md](INTEGRATION_REQUESTS.md)。
