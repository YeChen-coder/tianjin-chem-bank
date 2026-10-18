# 导入模块使用说明

V2 根目录执行，运行环境由会话 4 管理。依赖 lxml、olefile；Pillow 用于可用的预览生成。原始来源文件必须在输出 workspace 外。

```python
from question_import_v2 import import_paper
from question_content import compile_formula, validate_bundle
from question_content.storage import ContentStore

services = ContentStore('.work/session-1/my-private-store')
services.compile_formula = compile_formula
result = import_paper(source_path, '.work/session-1/my-import', services)
validation = validate_bundle(result['bundle'])
# 核对 result['report'] 和全部 issues；这里不自动保存题目到任何题库。
```

`EvidenceServices` 仅供单元测试，其 compile 明确失败，资产 URL 是可由资产服务器接管的固定协议 URL；离线核对页用原资产字节，不依赖替身 URL 能访问。真实整卷实验注入了公共 compile_formula，编译结果存 compilations.json，没有用虚假的成功编译代替实际转换。

每份输入按完整原 SHA256 分配私有目录及稳定节点 ID。`import-result.json`、`source-records.json`、`asset-manifest.json`、`ordered-document.json`、`retained-content.json`、`boundaries.json` 和 `compilations.json` 一并交付；raw 来源不塞入 PaperBundle。公式/未知对象由 source_ref 回查，原图哈希始终指向原文件字节。

同一 workspace 下相同导入模块版本复用既有结果，不覆盖已有 JSON；返回 report.reused=true。想用不同的转换/编译服务重新做恢复实验，应使用新的私有 workspace。重复导入不替代存储层的版本冲突控制。

未支持内容明确给出 issues；无法确认主编号的正文保留在 retained-content 和 ordered-document 中。主入口负责显示处理失败/待核对/教师确认三种状态，不能只看 question_count 判断成功。

完整真实样本核对：[STATUS.md](STATUS.md)。接入清单：[INTEGRATION_REQUESTS.md](INTEGRATION_REQUESTS.md)。重建离线整卷页：

```powershell
& .venv/Scripts/python.exe docs/word-latex/session-1/build_review.py
```
