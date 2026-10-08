# Kimi 国内版接入与 46 道疑难题对比

这次功能直接接入 `aivariant.complete_role`，AI 改题和补答案共用；benchmark 调用项目已有的 `aianswers.evaluate`，没有另写简化答题逻辑。

## Harvey 的配置

在项目旁的 `keys.local` 填写 `KIMI_API_KEY=`。国内 API 端点为 `https://api.moonshot.cn/v1/chat/completions`；也接受 `MOONSHOT_API_KEY` 环境变量。文件被 Git 忽略，密钥不写入题库、报告或日志。已有环境变量优先于文件，保存后重启服务才会生效。

默认使用支持视觉和思考的 `kimi-k2.6`，开启 thinking，输出额度 32768，单次请求限时 300 秒。K2.x 不支持 reasoning_effort，所以不发送这个字段，也不发送固定采样参数。若用 `KIMI_MODEL=kimi-k3`，程序使用 high 与 `max_completion_tokens`。可用 KIMI_FLASH / KIMI_PRO 分别覆盖带图与纯文字模型。

Harvey 的真实 Kimi 接口反馈组织并发上限为 1。程序因此在改题和补答案共用接口处串行发送 Kimi 请求，429 使用有界指数退避，保持思考和输出额度。benchmark 使用 --workers 1，避免大量线程争抢该账户；这个限制影响速度，不降低答题配置。

DeepSeek 带图步骤使用 `deepseek-flash`，纯文字第三次作答和核对使用 `deepseek-v4-pro`。默认开启思考、high、32768 输出额度、300 秒限时。图片 detail 为 original，保留原始分辨率。格式重试只去掉 response_format，保留思考强度、额度和全部原图。可用 DEEPSEEK_REASONING_EFFORT=none|low|high|max 调整，额度程序允许 512–131072。

CHEM_AI_PROVIDER=auto 时优先使用已配置的 Kimi；未配置时沿用 GLM / DeepSeek。第二次独立作答与核对可优先 DeepSeek。CHEM_AI_PROVIDER=kimi|deepseek|glm 时整个流程固定指定服务，服务失败明确报错。对比测试始终使用独立的调用上下文固定服务，不受 auto 或独立核对偏好影响。

部署到实际使用的机器时，复制代码更新，不复制 Harvey 的 keys.local、正式题库或 benchmark 数据。参考项目根目录 keys.example，在目标机器填好密钥和参数，重启原来的启动方式即可。

## 运行 benchmark

在项目目录中运行：

```powershell
.\.venv\Scripts\python.exe -u app.py benchmark `
  --source "C:\Users\yeche\Documents\chem-benchmark\hard-26mock-20261008" `
  --output "C:\Users\yeche\Documents\chem-benchmark\results-hard-26mock-kimi-deepseek" `
  --providers deepseek kimi --workers 3
```

输入必须是 manifest 对应的独立 benchmark 库，所有文件 SHA256 在运行前后校验。每个服务各自复制数据库和 media；原始副本和 Harvey 已有题库不会被写入。

每题重新拆小问、三次互不参考的独立作答、再次带原图核对，没有旧答案保留或跨服务回退。参数和代码指纹记录在 run.json；已经落盘的题目可断点续跑。输入、参数或代码变化时要求新的输出目录，防止混合配置的结果。输出目录的 .run.lock 防止同时启动两份任务；若进程被强行关闭，请先确认原进程已结束，再移除此锁文件。

每道题的 results/<原题号>.json 记录结果、候选答案、存疑原因、实际模型、每次请求额度、思考参数、HTTP 状态、耗时和 API 返回的 token 用量。无法获得 usage 的失败请求不会凭空估算用量。comparison.json / comparison.html 会随完成题目更新；不显示未完成结果为已通过。

只刷新报告、不调用 API：

```powershell
.\.venv\Scripts\python.exe app.py benchmark --source "C:\Users\yeche\Documents\chem-benchmark\hard-26mock-20261008" --output "C:\Users\yeche\Documents\chem-benchmark\results-hard-26mock-kimi-deepseek" --report-only
```

此次实测还发现负向选择题的本地误判：原题问“错误的是”，模型选 D 并说明“D错误”，旧关键词规则却把这个说明视为模型作答有误。修复后仍检查三次答案、条件、覆盖、科学性和逐次核对，仅区分选项错误与作答错误。8871 的真实记录已用于验证；答案分歧、图不清、条件不足、解析有误仍保持存疑。

每个服务完成全部题目后，可以用当前规则重新判定已保存证据，不重复调用 API：

```powershell
.\.venv\Scripts\python.exe app.py benchmark --source "C:\Users\yeche\Documents\chem-benchmark\hard-26mock-20261008" --output "C:\Users\yeche\Documents\chem-benchmark\results-hard-26mock-kimi-deepseek" --recheck
```

raw-results 保存初次结果，results 保存重判后的结果，三次解答和核对原文、API 参数和 token 用量不变。run.json 分别保留生成指纹与本地判定指纹。用于评价模型的正式统计以同一版规则重判后的结果为准。

余额或权限不足（HTTP 401/402/403）时暂停该组后续 API 请求，其他服务仍可继续。恢复账户后加 --retry-service-errors，仅续跑曾发生服务错误的未完成题；完整成功题跳过，已有审题计划及成功的独立解答复用，补齐缺失解答后重新核对。历史记录保存在 attempts；没有设问导致的审题失败不会因余额恢复而重复调用。

```powershell
.\.venv\Scripts\python.exe -u app.py benchmark --source "C:\Users\yeche\Documents\chem-benchmark\hard-26mock-20261008" --output "C:\Users\yeche\Documents\chem-benchmark\results-hard-26mock-kimi-deepseek" --providers deepseek kimi --workers 3 --retry-service-errors
```

## 本次范围调整

DeepSeek 首次批跑在 25 题完成完整流程后返回 HTTP 402，余额接口确认账户不可用。另有 19 题未进入完整作答、9651 只完成两次作答；9212 缺少设问。按用户后续指示，不再恢复或调用 DeepSeek，Kimi 仅运行已完整完成的 25 题：

286、374、375、376、381、8815、8841、8846、8848、8850、8871、8994、9024、9089、9112、9224、9347、9489、9562、9594、9640、9642、9643、9644、9647。

范围记录在输出目录 scope.json，原始副本仍为 46 题。当前对比报告按同一 25 题过滤两组结果。Kimi 结果库的测试卷 1 仅包含这 25 题，未测试题仍保留在该私有副本的题库中。

```powershell
.\.venv\Scripts\python.exe -u app.py benchmark --source "C:\Users\yeche\Documents\chem-benchmark\hard-26mock-20261008" --output "C:\Users\yeche\Documents\chem-benchmark\results-hard-26mock-kimi-deepseek" --scope "C:\Users\yeche\Documents\chem-benchmark\results-hard-26mock-kimi-deepseek\scope.json" --providers kimi --workers 3
```

结果库可以按原项目方式查看，例如先设 CHEM_DATA_DIR 为输出目录的 deepseek 子目录，CHEM_PORT=8766，再运行 app.py serve。仅查看结果时设置 CHEM_DISABLE_AI=1。查看 Kimi 时换成 kimi 子目录及其他端口。不要把任何结果库合并回正式库。

## 如何解释结果

本轮已按用户要求停止Kimi和DeepSeek测试，不因程序更新续跑。取得原卷后的人工核查见 [疑难题人工核查](疑难题人工核查.md)：25题中7题有题干/图片问题，另9题多选误标单选；旧自检通过数不能评价模型准确率。以下命令为工具用法，不是当前待执行任务。

源 manifest 提供旧的已确认/存疑小问与原因，没有人工标准答案。因此报告展示的是流程完成率、整题自检通过数、小问自检通过数、待复核原因和用量；不能称为模型真实正确率。两个模型自行拆小问，数量可能不同，跨服务的小问通过百分比不能直接当准确率比较。

374、375、381、9024 的原始题干残缺，保留在全量 46 题里，但完整题干组单独统计。即使模型把残缺题判为通过，也需要教师重点核对；保留存疑可能是正确的行为。

直接核对又发现 286 缺所需曲线图、376 只有答案片段、8841 配图为食谱而题干为铈元素周期表、9212 缺计算设问。这四题未在 manifest 的四道残缺名单中，解释模型能力时也应单独看待。证据见 [46 题输入核对](46题输入核对.md)。

原 GLM 记录只有输出状态与存疑原因，没有这次同条件运行的时间和用量。可作为旧结果参考，不把它当成新的 GLM 对照组。

接口参数依据：[Kimi 模型参数](https://platform.kimi.com/docs/api/models-overview)、[Kimi 国内 API](https://platform.kimi.com/docs/get-api-key)、[DeepSeek Chat Completions](https://api-docs.deepseek.com/zh-cn/api/create-chat-completion/)。
