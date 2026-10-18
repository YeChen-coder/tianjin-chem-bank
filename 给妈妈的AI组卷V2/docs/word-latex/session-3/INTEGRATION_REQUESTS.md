# 会话 3 接入请求（协议 v1）

公共文件仍由会话 4 修改。本模块不新建队列、不启动后台线程、不写题库。

1. 提供 `services.validate_question(question, assets=assets)` 返回 `{ok,issues}`，`compile_formula(latex, source_ref=None)` 使用唯一生产公式桥。
2. 在原 AI 作业的生成阶段调用 `aivariant.generate_content_candidate(question, assets, services, intensity=..., teacher_feedback=..., profile=...)`。传入真实已保存 Question 快照，不要仅传 body/segments；输出包含完整 candidate 和 judgement。已有分类、母题、版本、教师审核数据在存储层保持。
3. `services.complete(role, system, user, image_paths)` 默认可由 `aivariant.content_complete` 复用旧 provider 选择、JSON 模式、配额与取消。GLM 验收必须 pin GLM，不以其他服务代替。
4. `services.cancelled()` 接原 job_id/epoch 取消检查；`current_revision(question_id)` 接当前版本。返回 cancelled/stale/format_failed/judge_error 时不保存成功候选。科学/配图审核 FAIL 返回 candidate_rejected、ok=false，但保留候选和 AI_JUDGE_FAIL issue，仅存失败证据并沿用原隐藏/有限重试策略；SUSPECT 返回 candidate_review_required。最终插入必须在同一事务再核对取消、版本和母题身份，避免检查后提交前竞态。
5. `services.resolve_image(asset_id)` 只解析服务器登记的可读预览路径；生成与独立核对都重新传真实图。AI 不能控制本地文件路径，未配置图片能力时拒绝图片请求。
6. 完整 candidate 存入原 ai_versions 的候选内容及新公共存储，不得通过旧 build_variant_segments 扁平化。compiled_formulas 是派生缓存，不写入公式节点。母题保持原样，候选 revision=0，teacher_confirmed=false。PASS 仅为 AI 检查结果，继续原教师审核流程；SUSPECT/FAIL 沿用隐藏/重试策略。
7. 独立答案路径入口 `aianswers.review_content_candidate` 可用于结构化候选复核；原三次独立作答流程继续保留。需要完整结构答案生成时使用 `aianswers.evaluate_content`，输出审核证据与候选答案，不自动发布。
8. V2 旧模块依赖 `question_analysis.py` 的 `curriculum.json`，会话 4 已补齐，现已验证 aivariant/aianswers 可导入；不从父目录隐式导入旧配置。
9. 会话 2 直接读取 result.candidate；会话 4 使用同一 candidate 组成 schema-v1 PaperBundle 并 export_paper，需检查答案、图像、表格和原生公式。

验收材料及运行命令见 STATUS.md。结构格式通过、AI 科学核对、教师确认是三个独立状态。

## 可以直接取用的交付

- 会话 2：`.work/session-3/delivery/accepted-candidates.json` 的 questions 可直接渲染/编辑；rejected-candidates.json 用于展示“图像答案错误”的失败审核，不能按可发布候选使用。
- 会话 4：同目录两个 PaperBundle 与 DOCX、report.json 给出 22 个通过 AI 核对的原生公式、失败候选 11 个原生公式及 1 张原图；模拟样例见 `.work/session-3/mock-01/`，另有 2 张表格/4 个图片位置。格式结构均通过，无教师确认或视觉验收。
- 新程序仅从 V2 环境变量/显式安全配置加载 provider；没有复制旧 keys.local，不能默认从父目录读取凭据。真实验收客户端只在显式 --keys-path 时读取旧 GLM 字段。

## 公共编译兼容性证据

真实 GLM 返回 `m(\mathrm{CO}_{2})=0.448\ \mathrm{L}\times1.98\ \mathrm{g/L}\approx0.89\ \mathrm{g}` 被公共桥拒绝，原因是“公式转换没有完整保留符号”。把空格命令改成 `\,` 仍失败；等价标准单位分式 `\frac{\mathrm{g}}{\mathrm{L}}` 与 `\mathrm{g}\,\mathrm{L}^{-1}` 通过。模块保留失败原候选，不自动改数值或公式。提示词已要求复合单位采用标准分式，若需支持合法 `\mathrm{g/L}`，由会话 4 修公共编译器并复测该样例。
