# Grok Bot 接入与交接

适用于 xAI 的 Grok Bot。日期：2026-10-03。

## 用户最终怎么用

用户在指定 bot 中说：“给我一套二氧化碳专项练习，8 道选择题、2 道带图实验题，尽量不要计算题。”

bot 把要求转成检索条件，调用用户电脑上的题库，逐题核对后创建一套新试卷。完成时打开用户电脑上的试卷页面，并回复试卷名称、实际题量、需要教师核对的地方和查看链接。原题及其图像保留在原题库，试卷保存的是题目引用，无需再导入 Word 或复制题干。

这是“从题库选题组卷”的接入。此流程不会新发起 GLM 改写请求；如需改写，可随后在题库页面使用已有 AI 功能。自然语言理解和选题判断由 Grok Bot 完成，本程序负责检索、校验和保存。

## 必须先分清两台电脑

Grok Bot 默认使用云端电脑。本项目的 `127.0.0.1` 题库在实际用户的 Windows 电脑上，必须让 Grok Bot 使用目标电脑的本地执行能力。把命令在云端终端运行，访问到的是云端自己的地址，不能连接家人的本机题库。[官方电脑说明](https://docs.x.ai/grok-bot/computer-and-apps)

本地执行权限由你或实际用户在 Grok Bot 设置中配置：`Settings → General → Bot → Execution on Local Computer`；已登记多台电脑时，位于 `Settings → Computer → Computers` 的对应电脑设置。以安装版本实际界面为准；本项目没有修改 Grok Bot 设置或关闭它的审批机制。[官方本地权限说明](https://docs.x.ai/grok-bot/approvals-security-and-privacy)

公共分享 bot 模板与多人使用同一个账号/聊天不是同一件事。公共模板应只包含通用流程；同一账号下的 bot 共用云端文件和登录状态。用户电脑路径和数据目录应在实际使用的会话中单独配置，普通回复只发试卷摘要和链接，不输出 API 密钥、全库数据或其他任务的内容。Prompt 是操作约定，不是不同用户之间的访问隔离。[官方共享说明](https://docs.x.ai/grok-bot/overview)

## 维护者一次性安装

1. 把最新版程序放到**实际用户电脑**的固定目录，例如 `D:\ChemBank`。保持已有 `bank.sqlite`、`media`、`imports` 及原有试卷，不清库，不盲目重新导入。
2. 在该目录按 README 准备 `.venv` 并安装 `requirements.txt`。只安装到该虚拟环境。
3. 明确数据目录。如果程序和题库同目录，不必设置 `CHEM_DATA_DIR`；如果分开存放，配置成已有题库的绝对路径，并由维护者把这个设置写进实际用户的启动脚本。临时 PowerShell 环境变量不会自动带到其他终端；Grok Bot 每次启动时也要使用同一设置。启动工具在没有 `bank.sqlite` 时会停止，避免静默新建空题库。
4. 允许指定 bot 在**这台用户电脑**执行下方专用命令。不要把“云端电脑已能用终端”误当成“已连接本地电脑”。共享 bot 也不能靠一句 prompt 保证只访问某人的电脑。
5. 运行 `doctor`，核对 `data_directory`、`question_count` 和图片题数量。开发样本只有 57 题，正式库应该是你已经导入的完整题库；不能因为工具能连接，就忽略题量差异。
6. 做一次实际选题组卷验收，并从用户电脑打开返回链接，检查题量、顺序和图片。

默认端口为 `8765`。如果已有题库运行在其他端口，每个命令都传 `--server`，或把 `CHEM_BOT_URL` 配成该网址。当前开发预览的 `8876` 不应直接写死成家人电脑的地址。

下面命令均在**用户电脑的 PowerShell**运行（按实际目录/端口修改）：

```powershell
Set-Location 'D:\ChemBank'
New-Item -ItemType Directory -Path '.bot-plans' -Force | Out-Null
# 仅在数据和程序分目录时设置；不要随意指向开发样本。
# $env:CHEM_DATA_DIR = 'D:\ChemBankData'
& '.\.venv\Scripts\python.exe' '.\chem_bot.py' --server 'http://127.0.0.1:8765' start
& '.\.venv\Scripts\python.exe' '.\chem_bot.py' --server 'http://127.0.0.1:8765' doctor
& '.\.venv\Scripts\python.exe' '.\chem_bot.py' --server 'http://127.0.0.1:8765' catalog
```

`start` 复用已启动的新版题库；端口被旧版本或其他程序占用时会报错，不另开一个假题库。启动程序仍遵循原有 AI 设置，可能恢复以前已提交的 AI 工作；专用检索/组卷接口本身不创建 AI 任务、不读取模型密钥。安装好后，普通用户也可双击 `打开化学题库.cmd`。

## 检索、检查、创建的具体命令

```powershell
# 支持多个关键词任意匹配，也可 --match all。
& '.\.venv\Scripts\python.exe' '.\chem_bot.py' search --keyword '二氧化碳' --keyword 'CO₂' --types '单选题,填空题,实验题' --limit 20
# 只查带图题；知识点 id 必须从 catalog 读取。
& '.\.venv\Scripts\python.exe' '.\chem_bot.py' search --knowledge 'exp.gases' --images yes
# 第一页不足时，使用上次返回的 next_offset 继续；不是把第一页当成全库。
& '.\.venv\Scripts\python.exe' '.\chem_bot.py' search --query '二氧化碳' --offset 20 --limit 20
# 读取实际返回过的题号。不要猜题号。
& '.\.venv\Scripts\python.exe' '.\chem_bot.py' show --ids '1,2'
# 如需避开过去的卷子，先读已有试卷，再将题号传给检索的 --exclude-ids。
& '.\.venv\Scripts\python.exe' '.\chem_bot.py' papers
& '.\.venv\Scripts\python.exe' '.\chem_bot.py' paper --id 1
```

检索结果包含总题量、分页位置、题干、答案、现有题型、识别建议、图片路径、来源及需要核对的提示。图像是本机 `/media/...` 引用，不是 Base64 全库传输。bot 应打开本机题库页面查看图题；仅凭文字无法确认图意时，换用可核对的题或说明需要教师核对。知识点标签是关键词建议；没有保证每道旧题的类型、难度或分值都准确。

题目确定后，把以下计划保存为 `.bot-plans` 下的 UTF-8 JSON 文件。示例的题号只是格式示例，必须换成实际检索出的题号；`bank_revision` 必须完整复制本次检索或 `show` 的返回值。

```json
{
  "request_id": "chem-20261003-example-001",
  "bank_revision": "用本次检索返回的64位bank_revision替换",
  "name": "二氧化碳专项练习",
  "question_ids": [1, 2]
}
```

`request_id` 为每次用户新任务生成一个新编号（建议 UUID）；**重试同一份计划时沿用原编号**。顺序按 `question_ids` 保存，每份最多 200 道，不自动把几道题打乱或补成虚构题。

```powershell
& '.\.venv\Scripts\python.exe' '.\chem_bot.py' create --plan '.\.bot-plans\plan.json' --dry-run
& '.\.venv\Scripts\python.exe' '.\chem_bot.py' create --plan '.\.bot-plans\plan.json' --open
```

成功结果包含 `paper`、实际 `count`、`duplicate`、`preview_url` 和需核对的 `warnings`。`--open` 会在执行命令的用户电脑上打开浏览器。也可从题库“我的试卷”找到它。`preview_url` 只在这台电脑上有效；不能在技术维护者的另一台电脑或手机上点同一个 localhost 链接查看家人的卷子。

只有拿到成功结果，并再用 `paper --id` 核对题号和实际题量后，bot 才能说“已生成”。重复请求返回已有试卷；同编号换计划、题库变化、题号不存在、重复题号、隐藏草稿或缺图都会停止，不会偷偷只保存剩下的题。`--dry-run` 校验而不创建试卷。它会准备去重记录表，但不新增去重任务记录。

## 可以直接给 Grok Bot 的一次性交接 prompt

先替换尖括号中的内容，不要把 API 密钥写进这份说明。

```text
请配置并验收“本地九年级化学组卷”流程，以后让用户直接说要求就能得到可查看的试卷。

目标电脑：<实际用户电脑的名称>。程序目录：<实际用户电脑上的完整项目路径>。
题库网址：<例如 http://127.0.0.1:8765>。正式数据目录：<bank.sqlite 和 media 所在目录>。
所有 chem_bot.py 命令必须在这台用户电脑上执行，不要在 Grok 云端终端运行。

先阅读项目 docs/Grokbot接入与交接.md。
使用项目 .venv 中的 Python，不安装全局依赖。
执行 start 和 doctor，核对数据目录、题量、含图题量与实际已有题库是否相符。
发现空库、57 道开发样本、旧程序或连错电脑时停止并说明，不清库、不重新导入来凑题量。
本地执行权限或环境未准备好时告诉我需要配置哪一步，不绕过审批、不公开题库服务。

接着做一次验收：按我给定的知识点检索，选两道真实题（至少一道带图），用 show 核对。
保存 UTF-8 JSON 计划，其中 request_id 是这次任务的新 UUID，bank_revision 来自本次检索，题号来自真实结果。
先 create --dry-run，再 create --open；用 paper --id 核对实际题号、题量与页面图片。
再提交同一份计划验证 duplicate=true，不能产生第二套重复试卷。
验收后把已成功执行的方法保存为“本地九年级化学组卷”流程供指定 bot 重用。
告诉我：目标电脑、程序/数据目录、题库网址、题量、验收试卷名和查看链接，以及尚未完成的配置。
不要在回复中输出密钥、全库题干或其他用户的任务。仅新建本次试卷，不修改或删除原题和原有试卷。
```

## 指定组卷 bot 的长期工作 prompt

```text
你是九年级化学组卷助手，服务不懂技术的教师。用户说要求，你负责检索并在已配置的本地题库程序中保存一套新试卷。

已配置目标电脑：<名称>；项目目录：<路径>；题库网址：<网址>；正式数据目录：<路径>。
每次任务使用指定目标电脑的本地执行能力。命令均通过项目 .venv 的 Python 调用 chem_bot.py。如果程序和数据分目录，每次启动时先在同一终端设置 CHEM_DATA_DIR 为已配置的正式数据目录。

1. 理解主题、题量、题型、是否含图及排除要求。影响选题的关键信息缺失时简短询问；其他小偏好按已保存习惯处理。
2. start/doctor 确认本机程序、数据目录和题量；不能把云端、空库或开发样本当正式库。
3. catalog 获取可用标签，search 查真实题目。查看 total/next_offset，必要时翻页或换关键词；如果数量不足，说明缺多少，问是否调整要求，不擅自用无关题填满。
4. 使用 show 核对候选题正文、类型建议、图片和警告。图题在用户电脑的题库页面查看；看不懂图时换题或明确提示教师核对。现有标签不是准确性保证；难度和分值未明确标注时不要虚构。
5. 按用户要求确定真实题号和顺序，将计划保存为项目 .bot-plans 下的 UTF-8 JSON 文件。新任务用新 request_id，同一计划重试保持原编号，bank_revision 使用本次检索结果。
6. create --dry-run 校验后执行 create --open 新建试卷。遇到题库变化、缺图或无效题号先修正选题，不能绕过校验或直接写 SQLite。
7. 用 paper --id 读取实际保存的题量和题号。只有结果成功并核对一致后才说“已生成”，返回名称、题量和工具返回的 preview_url。用户电脑浏览器应已打开这套试卷；用户还可在“我的试卷”找到。

用户只需要听“正在找题”“正在核对题目”“试卷已经准备好了”及需要确认的地方，不显示命令或专业参数。
本任务默认只从题库选题，不主动调用 GLM 改写。不要改程序、清库、删题、覆盖已有试卷、读取/发送 keys.local、把服务公开到互联网或关闭本地审批。
共享会话只回复本次试卷摘要和链接，不贴全库内容、密钥、无关文件或他人对话。目标电脑不确定时先确认，不能沿用另一位用户的路径。
```

## 程序接口和验证边界

接口只监听原程序的 `127.0.0.1`。专用接口为：`GET /api/bot/info`，`POST /api/bot/search`，`POST /api/bot/questions`，`POST /api/bot/papers`。命令工具绕开 HTTP 代理，限制目标为本机地址；专用接口拒绝外站 Origin、非本机 Host 和非 JSON 提交，没有开放 CORS。专用入口只提供检索、读取、新建试卷；原有网页的编辑功能仍存在，这不是操作系统权限沙箱。

接入自动测试覆盖检索/分页/过滤、隐藏草稿、错库/内容变化、非法题号/重复题号、缺图、计划校验、并发重复提交、保留既有试卷和图片、题目顺序及命令工具的完整 HTTP 流程。已在开发样本中实际通过命令工具检索并新建可查看试卷，不发送新 AI 请求。

尚未在家人电脑上配置目标 bot 或发送任务给它。这里完成的是题库侧工具与可交接的流程；需要维护者按前面的步骤在实际用户电脑做一次 Grok Bot 本地执行验收。
