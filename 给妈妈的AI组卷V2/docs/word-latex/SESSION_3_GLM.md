# 会话 3：GLM 改写与公式往返

先读项目根目录 `LATEX_HANDOFF.md`、`WORD_LATEX_REFACTOR_PLAN.md` 的第 5、7、9、11 节，以及本目录 `PARALLEL_CONTRACT.md`。

项目根目录均指 `C:/Users/yeche/Documents/ChatGPT/给妈妈的AI组卷/给妈妈的AI组卷V2`。只按需复制有用文件，旧代码只读参考；原试卷统一只读读取 `C:/Users/yeche/Documents/ChatGPT/给妈妈的AI组卷/chem-papers/mirror`。

## 任务与边界

负责四步的第三步：完整题目结构传给 GLM，接收含公式/表格/图片引用的变式与答案解析，检查并接入原 AI 候选、核对和版本流程。用户以 GLM 为主用。

可修改：`aivariant.py`、`aianswers.py`、新增 `ai_formula_v2/`、`tests/test_ai_formula_v2*.py`、`docs/word-latex/session-3/`。不要修改 `app.py`、`banklib.py`、公共编译器或别的前端；后端路由接线交会话 4。

私有数据/作业队列放 V2 的 `.work/session-3`；如需 mock 服务建议端口 8903，先检查占用。先用模拟响应，避免与现有后台作业重复请求。按需复制自己的模块并登记清单；不复制旧凭据文件或整个运行环境。

## 可以马上并行做

1. 读取共同 Question/允许资产样例，构建 GLM 请求；不只发送扁平正文或题目画像。
2. 建立契约 v1 返回解析和 ID/目标重映射，覆盖题干、选项、小问、答案和解析。
3. 用固定响应测试正常公式、错误 JSON、反斜杠损坏、截断、缺图、悬空答案关联及取消。
4. 通过注入公共编译替身测试流程；不在 AI 模块另外实现一套公式编译器。
5. 用户要求开工且现有配置可用后，安排受控的真实 GLM 测试。实际型号的图像能力与 JSON 行为必须核对；记录数量/用量，不打印凭据，不读取/改写无关配置。

## 交付与衔接

- 请求构建、候选规范化、结构/公式检查模块；保留原改写强度、教师意见和审核流程。
- mock 响应、私有测试候选 Question、异常/取消报告。
- 当前主用 GLM 的真实往返报告，区分格式正确与内容核对结论；不以 DeepSeek 成功代替。
- 会话 2 直接使用合法候选，显示/编辑逻辑无需认识 GLM 特殊格式。
- 会话 4 使用同一候选导出 Word，并接入公共校验、版本存储与原作业路由。

在 `docs/word-latex/session-3/STATUS.md` 记录基线、协议版本、实际测试模型、mock/真实范围、待依赖事项、能否联调及正式数据写入情况。共享文件需求写同目录 `INTEGRATION_REQUESTS.md`。

完成标准：请求含完整公式上下文；返回候选不丢结构/关联，格式失败不冒充成功，题目答案配图核对仍有效；至少一组真实 GLM 往返及跨模块导出验收。不得自动覆盖母题或把 AI 声称“已确认”当教师确认。

## 用户可复制的开工提示

请创建或复用 `C:/Users/yeche/Documents/ChatGPT/给妈妈的AI组卷/给妈妈的AI组卷V2` 作为唯一开发目录，只按需从旧项目复制有用文件，原试卷统一只读读取 `C:/Users/yeche/Documents/ChatGPT/给妈妈的AI组卷/chem-papers/mirror`。

请阅读 docs/word-latex/SESSION_3_GLM.md、PARALLEL_CONTRACT.md 和根目录交接说明，按会话 3 分工开始 GLM 公式改写开发。先用共同样例和模拟响应，配置可用时进行受控真实 GLM 测试，不以 DeepSeek 替代；保留母题、现有审核与其他服务能力。遵守文件所有权，使用私有题库/队列，更新 STATUS.md，给我候选数据、测试结论及会话 2/4 的接入清单。
