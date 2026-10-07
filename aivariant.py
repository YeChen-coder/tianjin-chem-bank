# -*- coding: utf-8 -*-
"""AI variants for questions already on a paper.

Environment (names only; values are read when the worker starts, never stored):
  GLM_API_KEY        preferred provider. Quota exhaustion pauses GLM for 10 minutes
                     while DeepSeek continues. Never write keys to DB, UI, or logs.
  GLM_FLASH          default glm-5.3-flash (vision / first generation / vision judge).
                     thinking.type only supports enabled. Temperature 1, top_p 0.95.
  GLM_PRO            default glm-5.3 (text flagship). Never send images to it.
  GLM_API_URL        default https://open.bigmodel.cn/api/paas/v4/chat/completions
  GLM_REASONING_EFFORT default high (low / high / max on GLM-5.3).
  GLM_MAX_TOKENS     default 8192; includes the model's reasoning budget.
  DEEPSEEK_MAX_TOKENS default 8192; non-thinking mode retained on format retry.
  DEEPSEEK_API_KEY   fallback. Used once when GLM is unset or that call fails.
                     Do not write it to the DB, UI, or logs.
  DEEPSEEK_FLASH     default deepseek-flash
                     (DeepSeek-V4.1-Flash; vision supported. Docs checked 2026-10-02:
                     https://api-docs.deepseek.com/quick_start/pricing
                     and https://api-docs.deepseek.com/api/create-chat-completion.
                     deepseek-chat / deepseek-reasoner were retired after 2026-07-24
                     and must not be the default.)
  DEEPSEEK_PRO       default deepseek-v4-pro
                     (text-only role for later text variants and text judging.
                     Every picture-dependent variant and judge uses the flash role.)

GLM is non-streaming with a hard deadline around 90s. A body that is only
keep-alives is a failed call. DeepSeek remains the fallback and is not removed.
Prompt version stored on each ai_versions row: chem-g9-v2-answers
"""
import base64
import hashlib
import http.client
import json
import os
import re
import socket
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import banklib
import question_analysis

PROMPT_VERSION = "chem-g9-v2-answers"
API_URL = "https://api.deepseek.com/chat/completions"
GLM_API_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
INTENSITIES = ("light", "medium", "deep")
JOB_ACTIVE = ("queued", "running")
VERSION_NONTERMINAL = ("QUEUED", "GENERATING", "GENERATED", "JUDGING")
JUDGE_OK = ("PASS", "SUSPECT", "FAIL")
BRANCH_OK = ("PASS", "SUSPECT", "FAIL", "JUDGE_ERROR")
HIDDEN_JUDGE = ("SUSPECT", "FAIL")
AUTO_RETRY_LIMIT = 3
STOP_NOTICE = "该题多次尝试但结果存疑，请自行更改"
RETRY_NOTICE = "有一版未通过，正在重新生成"

_FENCE = re.compile(r"```(?:json|JSON)?\s*([\s\S]*?)```")
_KEY_RE = re.compile(r"(Bearer\s+)\S+|sk-[A-Za-z0-9_\-]+")

_start_lock = threading.Lock()
_run_lock = threading.Lock()
_cancel_lock = threading.Lock()
_running_bases = set()
_started = False
# Bumped by stop_all so a worker blocked inside the model call can see the stop
# before the next SQL write. Job ids recorded here are never claimed or saved.
_generation_epoch = 0
_cancelled_jobs = set()
_job_epochs = {}
_api_key = ""
_flash_model = "deepseek-flash"
_pro_model = "deepseek-v4-pro"
_glm_key = ""
_glm_flash = "glm-5.3-flash"
_glm_pro = "glm-5.3"
_glm_url = GLM_API_URL
_ds_key = ""
_ds_flash = "deepseek-flash"
_ds_pro = "deepseek-v4-pro"
_CALL_TIMEOUT = 90
_KEEPALIVE_LIMIT = 60
_provider_lock = threading.Lock()
_glm_pause_until = 0.0
_glm_pause_key = ''
_GLM_QUOTA_CODES = {'1113', '1308', '1309', '1310', '1314', '1316', '1317', '1318', '1319', '1320', '1321'}

SYSTEM_PROFILE = """你是天津市九年级化学教师的题目分析助手。范围是人民教育出版社《义务教育教科书 化学》九年级上册、下册的常规教学内容。不要使用大学化学，也不要超纲。
请阅读母题（含必须看的图），只输出一个 JSON 对象，不要 Markdown，不要额外解释。
格式：
{"profile": {
  "grade": "九年级",
  "subject": "化学",
  "qtype": "",
  "knowledge": "",
  "major": "",
  "minor": "",
  "skill": "",
  "difficulty": "",
  "original_answer": "",
  "key_conditions": "",
  "editable": "",
  "locked": "",
  "has_image": false,
  "image_required": false,
  "calculation": false,
  "experiment": false,
  "equation": false,
  "chart": false
}, "image": null}
如果题目有图，image 改为对象：
{"requires_image": true, "image_type": "", "labels": [], "objects": [], "relationships": "", "question_depends_on": "", "must_not_change": ""}
没有图时 image 必须为 null。profile 里的 knowledge、skill、difficulty、key_conditions、locked 要足够具体，使后一步只看画像也能守住考点和大致难度。"""

SYSTEM_GENERATOR = """你是天津市九年级化学教师的出题助手。范围只限人民教育出版社《义务教育教科书 化学》九年级上册、下册的常规内容。
必须遵守：
1. 不超纲。不要大学化学，不要高中选修，不要教材里没学过的复杂机理。
2. 科学正确。方程式、价态、现象、单位和计算都要对。
3. 守住题目画像中的核心考点、技能和大致难度。深度改写可以换情景，但不能换掉这些。
4. 禁止只把原题换成近义词。要成为一道新的、能直接使用的题。
5. 答案按新题重算或重新推理，禁止照抄旧答案。
6. 条件要充分，学生只看题干和必须保留的原图就能作答。
7. 单选题只能有一个正确选项；多选题的正确项要明确；选项不能重复，也不能互相包含导致多解。
8. 不要生成新图片。如果新题仍依赖原来的图，depends_on_image 为 true，并在 segments 里原样引用给定的 /media 图片；不要编造新的 src。
9. 只输出一个 JSON 对象，不要 Markdown，不要解释。
10. 必须返回非空且完整的新答案，不能写“略”“待补充”或让老师自行计算。按母题画像保留题型。单选给出唯一正确选项字母；多选给出完整正确选项集合；填空逐空作答；简答和实验逐小问作答；计算给出结果、单位，并在 analysis 写出方程式或计算步骤。
11. 答案和解析只能写入 answer、analysis，不得在 stem、segments 的题干中附上答案、解析或标出正确选项。
12. 输出前重新独立解答新题，逐一核对 answer 与 analysis 的结论一致。改动选项表述后，多选的正确项集合也必须重新计算，不能保留旧选项字母。选择题每个选项单独一段，并明确编号 A、B、C、D。
13. 图像事实以实际原图为准，不得给装置凭空加入试剂、液体或反应。设问中的装置字母必须与对应操作或反应一致；制气反应应问发生装置，不能移到只有蜡烛的收集瓶。
14. 题型格式也必须保持：多选题在题干明确写“多选”；填空题逐空设问；简答题用“说明、解释、列举、判断并给出理由”等问句让学生完整作答，不能把所有小问都改成填空；计算题要保留数值计算，并在题干提供所需相对原子质量等数据，不能依赖未随题导出的原试卷卷头。
字段：
stem（题干，选择题把选项写进题干）,
answer（新答案）,
analysis（简要解析）,
qtype（单选题、多选题、填空题、简答题、实验题、计算题之一）,
options（选择题为字符串数组，否则 []）,
depends_on_image（true 或 false）,
segments（段落数组。每个段落是部件数组。部件只能是 {"t":"text","s":"..."} 或 {"t":"img","sha":"...","src":"/media/....png"}。img 的 src 只能来自给定原图。）"""

SYSTEM_JUDGE = """你是天津市九年级化学的独立审题人。范围是人教版九年级化学常规内容。
你不改写题目，只审核。请先根据题干（和你看到的原图）独立解答，把你自己的答案写入 independent_answer；然后再对照生成答案，判断对错。
只输出一个 JSON 对象，不要 Markdown，不要额外文字。
字段：
status（只能是 PASS、SUSPECT、FAIL）,
confidence（0 到 1 的小数）,
independent_answer,
generator_answer,
knowledge_correct（布尔）,
conditions_sufficient（布尔）,
answer_unique（布尔，单选题必须唯一正确项，多选题必须有明确且完整的正确选项集合；填空若有多种等价写法且题干允许，可视为唯一）,
calculation_verified（布尔，没有计算则为 true）,
grade_level_appropriate（布尔，超纲则为 false）,
image_consistent（布尔，不用图或图与题一致则为 true）,
issues（数组，元素为 {"type":"","message":""}）,
recommendation（给老师的短说明，不要改写全题）。
PASS 表示自动检查未发现科学错误、条件充分、答案正确且难度大致合适。SUSPECT 表示大体可用但有需要老师看的疑点。FAIL 表示科学错误、条件不足、答案错误、多解或明显超纲。
看图时以实际原图为准，不得给装置补设未出现的试剂或液体。图像描述只是辅助，不是事实保证；只有蜡烛的瓶不能推断含澄清石灰水。
核对题型与作答方式是否一致：简答题不能把全部小问改成填空。明显偏离指定题型时判 SUSPECT。"""

INTENSITY_TEXT = {
    "light": "轻：只改数字、物质的具体用量或设问角度，情景和考点保持不变。",
    "medium": "中：可以更换具体物质或生活情景，核心知识、技能和大致难度不变。",
    "deep": "深：可以重新设计情景，但必须守住题目画像里的核心知识、技能和大致难度，仍然不能超纲。",
}


class ModelError(Exception):
    def __init__(self, kind, message=None, http_status=None, provider_code=None):
        self.kind = kind
        self.http_status = http_status
        self.provider_code = str(provider_code or '')
        messages = {"config":"AI 配置不完整", "auth":"API 密钥或模型权限不足",
                    "http":"模型请求失败", "empty":"模型未返回正文",
                    "parse":"模型未返回有效题目 JSON",
                    "answer":"AI 已返回题目，但没有完整答案，请重新生成",
                    "output_limit":"模型输出额度耗尽，未形成完整题目 JSON；请降低推理强度或增大 GLM_MAX_TOKENS"}
        super().__init__(message or messages.get(kind, kind))


def questions_search_hidden():
    """Alias used by tests and the list SQL. Drafts stay out of bank search."""
    return banklib.questions_visible_clause()


def ensure_schema(con):
    """Additive columns and tables. Safe to run more than once. Does not rewrite stems."""
    cols = [r[1] for r in con.execute("PRAGMA table_info(questions)")]
    if "origin" not in cols:
        con.execute("ALTER TABLE questions ADD COLUMN origin TEXT DEFAULT 'human'")
    if "base_question_id" not in cols:
        con.execute("ALTER TABLE questions ADD COLUMN base_question_id INTEGER")
    if "ai_version_id" not in cols:
        con.execute("ALTER TABLE questions ADD COLUMN ai_version_id INTEGER")
    cols = [r[1] for r in con.execute("PRAGMA table_info(questions)")]
    if "in_bank" not in cols:
        con.execute("ALTER TABLE questions ADD COLUMN in_bank INTEGER NOT NULL DEFAULT 1")
    con.execute("UPDATE questions SET origin='human' WHERE origin IS NULL OR origin=''")
    con.execute("UPDATE questions SET in_bank=1 WHERE in_bank IS NULL")
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS ai_sessions (
            id INTEGER PRIMARY KEY,
            base_question_id INTEGER UNIQUE,
            profile_json TEXT,
            image_json TEXT,
            profile_model TEXT,
            profile_status TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS ai_versions (
            id INTEGER PRIMARY KEY,
            session_id INTEGER,
            base_question_id INTEGER,
            parent_version_id INTEGER,
            question_id INTEGER,
            seq INTEGER,
            status TEXT,
            intensity TEXT,
            teacher_feedback TEXT,
            generator_model TEXT,
            prompt_version TEXT,
            candidate_json TEXT,
            judge_model TEXT,
            judge_status TEXT,
            judge_json TEXT,
            error TEXT,
            created_at TEXT,
            excluded INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS ai_jobs (
            id INTEGER PRIMARY KEY,
            paper_id INTEGER,
            base_question_id INTEGER,
            version_id INTEGER,
            phase TEXT,
            status TEXT,
            attempts INTEGER,
            created_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_ai_jobs_base ON ai_jobs(base_question_id, status);
        CREATE INDEX IF NOT EXISTS idx_ai_versions_base ON ai_versions(base_question_id, seq);
        """
    )
    vcols = [r[1] for r in con.execute("PRAGMA table_info(ai_versions)")]
    if vcols and "excluded" not in vcols:
        con.execute("ALTER TABLE ai_versions ADD COLUMN excluded INTEGER NOT NULL DEFAULT 0")
    con.commit()


def parse_model_json(text):
    """Parse a JSON object. Accepts raw JSON or a markdown fence around it."""
    if isinstance(text, dict):
        return text
    if text is None:
        raise ValueError("empty")
    s = str(text).strip()
    if not s:
        raise ValueError("empty")
    fence = _FENCE.search(s)
    if fence:
        s = fence.group(1).strip()
    try:
        data = json.loads(s)
    except json.JSONDecodeError:
        i = s.find("{")
        j = s.rfind("}")
        if i < 0 or j <= i:
            raise
        data = json.loads(s[i:j + 1])
    if not isinstance(data, dict):
        raise ValueError("not an object")
    return data


def parse_judge_status(payload):
    """Return PASS, SUSPECT, or FAIL. Anything else is unusable (caller stores JUDGE_ERROR)."""
    if isinstance(payload, str):
        try:
            payload = parse_model_json(payload)
        except Exception:
            return None
    if not isinstance(payload, dict):
        return None
    st = payload.get("status")
    if not isinstance(st, str):
        return None
    st = st.strip().upper()
    if st not in JUDGE_OK:
        return None
    return st


def _safe_text(exc):
    s = str(exc)
    for key in (_glm_key, _ds_key, _api_key):
        if key:
            s = s.replace(key, "[redacted]")
    s = s[:240]
    return _KEY_RE.sub("[redacted]", s)


def _now():
    return banklib._paper_now()


def _clean_intensity(value):
    if value in (None, ""):
        return "medium", None
    if not isinstance(value, str) or value not in INTENSITIES:
        return None, "改动程度只能是轻、中、深"
    return value, None


def _job_public(row, duplicate=False):
    return {
        "id": row["id"],
        "paper_id": row["paper_id"],
        "base_question_id": row["base_question_id"],
        "version_id": row["version_id"],
        "phase": row["phase"],
        "status": row["status"],
        "duplicate": bool(duplicate),
        "service_message": '原来的 AI 服务暂时不可用，正在用备用服务继续处理。' if _ds_key and _glm_paused() else '',
    }


def _active_job(con, base_id):
    return con.execute(
        """
        SELECT * FROM ai_jobs
        WHERE base_question_id=? AND status IN ('queued','running')
        ORDER BY id DESC LIMIT 1
        """,
        (int(base_id),),
    ).fetchone()


def _rows(con):
    con.row_factory = sqlite3.Row
    return con


def _load_question(con, qid):
    return con.execute("SELECT * FROM questions WHERE id=?", (int(qid),)).fetchone()


def _origin_of(row):
    if row is None:
        return ""
    keys = row.keys()
    if "origin" in keys and row["origin"]:
        return row["origin"]
    return "human"


def _is_human_base(row):
    return row is not None and _origin_of(row) != "ai"


def _excluded_flag(row):
    if row is None:
        return 0
    keys = row.keys()
    if "excluded" not in keys or row["excluded"] in (None, ""):
        return 0
    try:
        return 1 if int(row["excluded"]) else 0
    except (TypeError, ValueError):
        return 0


def _is_shown_version(row):
    """Teacher-facing card: a PASS that was not excluded or auto-hidden."""
    return row is not None and row["status"] == "PASS" and not _excluded_flag(row)


def _usable_parent_id(con, base_id, parent_version_id):
    """Latest still-visible ancestor. Excluded and hidden SUSPECT/FAIL are skipped."""
    seen = set()
    pid = parent_version_id
    while pid:
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            return None
        if pid in seen:
            return None
        seen.add(pid)
        parent = con.execute(
            "SELECT * FROM ai_versions WHERE id=? AND base_question_id=?",
            (pid, int(base_id)),
        ).fetchone()
        if not parent:
            return None
        if _is_shown_version(parent):
            return parent["id"]
        pid = parent["parent_version_id"]
    return None


def _chain_rows(con, base_id, parent_version_id):
    if parent_version_id:
        return con.execute(
            """SELECT * FROM ai_versions
               WHERE base_question_id=? AND parent_version_id=?
               ORDER BY seq, id""",
            (int(base_id), int(parent_version_id)),
        ).fetchall()
    return con.execute(
        """SELECT * FROM ai_versions
           WHERE base_question_id=? AND parent_version_id IS NULL
           ORDER BY seq, id""",
        (int(base_id),),
    ).fetchall()


def _trailing_bad_count(rows):
    """Consecutive SUSPECT/FAIL at the end of one chain. In-flight rows are skipped.
    A PASS or any other finished status ends the streak. Not a lifetime count.
    """
    n = 0
    for row in reversed(list(rows)):
        st = row["status"]
        if st in VERSION_NONTERMINAL:
            continue
        if st in HIDDEN_JUDGE:
            n += 1
            continue
        break
    return n


def chain_bad_count(con, base_id, parent_version_id):
    """How many hidden SUSPECT/FAIL results are consecutive for this parent chain."""
    return _trailing_bad_count(_chain_rows(con, base_id, parent_version_id))


def after_hidden_judge(con, version_id, paper_id=None, epoch=None, job_id=None):
    """Keep a SUSPECT/FAIL row, hide it, and enqueue one retry from its good parent.

    The retry parent is the hidden version's parent (a shown PASS, or the base),
    never the hidden text. Stops at AUTO_RETRY_LIMIT consecutive bad judges on
    that same chain. Does not enqueue when this base already has an active job.
    A stop (epoch change, cancelled job, or CANCELLED version) does not enqueue.
    """
    _rows(con)
    try:
        version_id = int(version_id)
    except (TypeError, ValueError):
        return None
    if _should_stop(con, job_id, version_id, epoch):
        return {
            "retried": False,
            "stopped": True,
            "duplicate": False,
            "streak": 0,
            "version_id": version_id,
        }
    ver = con.execute("SELECT * FROM ai_versions WHERE id=?", (version_id,)).fetchone()
    if not ver or ver["status"] not in HIDDEN_JUDGE:
        return None
    if not _excluded_flag(ver):
        con.execute("UPDATE ai_versions SET excluded=1 WHERE id=?", (version_id,))
        con.commit()
        ver = con.execute("SELECT * FROM ai_versions WHERE id=?", (version_id,)).fetchone()
    streak = chain_bad_count(con, ver["base_question_id"], ver["parent_version_id"])
    if streak >= AUTO_RETRY_LIMIT:
        return {
            "retried": False,
            "stopped": True,
            "duplicate": False,
            "streak": streak,
            "version_id": version_id,
        }
    active = _active_job(con, ver["base_question_id"])
    if active:
        return {
            "retried": False,
            "stopped": False,
            "duplicate": True,
            "streak": streak,
            "version_id": version_id,
        }
    if paper_id in ("",):
        paper_id = None
    if paper_id is None:
        job = con.execute(
            "SELECT paper_id FROM ai_jobs WHERE version_id=? ORDER BY id DESC LIMIT 1",
            (version_id,),
        ).fetchone()
        paper_id = job["paper_id"] if job else None
    parent_id = _usable_parent_id(con, ver["base_question_id"], ver["parent_version_id"])
    mode = "branch" if parent_id else "another"
    if _should_stop(con, job_id, version_id, epoch):
        return {
            "retried": False,
            "stopped": True,
            "duplicate": False,
            "streak": streak,
            "version_id": version_id,
        }
    result, err = enqueue_base(
        con,
        paper_id,
        ver["base_question_id"],
        parent_id,
        ver["teacher_feedback"] or "",
        ver["intensity"] or "medium",
        mode,
    )
    if err or not result:
        return {
            "retried": False,
            "stopped": False,
            "duplicate": False,
            "streak": streak,
            "version_id": version_id,
            "error": err,
        }
    return {
        "retried": not result.get("duplicate"),
        "stopped": False,
        "duplicate": bool(result.get("duplicate")),
        "streak": streak,
        "version_id": version_id,
        "job": result,
    }


def _current_epoch():
    with _cancel_lock:
        return _generation_epoch


def _bump_epoch():
    global _generation_epoch
    with _cancel_lock:
        _generation_epoch += 1
        return _generation_epoch


def _remember_cancelled(job_ids):
    with _cancel_lock:
        for job_id in job_ids:
            _cancelled_jobs.add(int(job_id))


def _cancelled_now():
    with _cancel_lock:
        return set(_cancelled_jobs)


def _should_stop(con, job_id, version_id, epoch):
    """True when this in-flight job must not write or enqueue anything else."""
    if epoch is not None and epoch != _current_epoch():
        return True
    if job_id is not None and int(job_id) in _cancelled_now():
        return True
    if con is None:
        return False
    if job_id is not None:
        job = con.execute(
            "SELECT status FROM ai_jobs WHERE id=?", (int(job_id),)
        ).fetchone()
        if job is not None and job["status"] == "cancelled":
            return True
    if version_id is not None:
        ver = con.execute(
            "SELECT status FROM ai_versions WHERE id=?", (int(version_id),)
        ).fetchone()
        if ver is not None and ver["status"] == "CANCELLED":
            return True
    return False


def _begin_immediate(con):
    """Start a write transaction. False means the caller already has one."""
    try:
        con.execute("BEGIN IMMEDIATE")
        return True
    except sqlite3.OperationalError as exc:
        if "transaction" in str(exc).lower():
            return False
        raise


def stop_all(con):
    """Stop every in-progress variant job. Finished PASS/FAIL rows stay.

    Queued jobs are not claimable afterwards. Versions still in QUEUED,
    GENERATING, GENERATED, or JUDGING become CANCELLED. The epoch and the
    cancelled-job set flip first so a thread inside the model call will not
    save, judge, or enqueue the next version when it returns.
    """
    _rows(con)
    _bump_epoch()
    _begin_immediate(con)
    try:
        jobs = con.execute(
            "SELECT id, version_id FROM ai_jobs WHERE status IN ('queued', 'running')"
        ).fetchall()
        job_ids = [int(r["id"]) for r in jobs]
        job_versions = set()
        for r in jobs:
            if r["version_id"] is not None:
                job_versions.add(int(r["version_id"]))
        _remember_cancelled(job_ids)
        vers = con.execute(
            """SELECT id FROM ai_versions
               WHERE status IN ('QUEUED', 'GENERATING', 'GENERATED', 'JUDGING')"""
        ).fetchall()
        ver_ids = [int(r["id"]) for r in vers]
        if job_ids:
            con.execute(
                "UPDATE ai_jobs SET status='cancelled' WHERE status IN ('queued', 'running')"
            )
        if ver_ids:
            marks = ",".join("?" * len(ver_ids))
            con.execute(
                "UPDATE ai_versions SET status='CANCELLED' WHERE id IN (%s) "
                "AND status IN ('QUEUED', 'GENERATING', 'GENERATED', 'JUDGING')" % marks,
                ver_ids,
            )
        con.commit()
    except Exception:
        try:
            con.rollback()
        except Exception:
            pass
        raise
    orphans = [vid for vid in ver_ids if vid not in job_versions]
    return {"ok": True, "stopped": len(job_ids) + len(orphans)}


def _maybe_retry_hidden(con, version_id, paper_id=None, epoch=None, job_id=None):
    try:
        if _should_stop(con, job_id, version_id, epoch):
            return None
        return after_hidden_judge(
            con, version_id, paper_id=paper_id, epoch=epoch, job_id=job_id
        )
    except Exception:
        return None


def enqueue_base(con, paper_id, base_id, parent_version_id, feedback, intensity, mode):
    """Queue one new version for one base question. Same base cannot have two active jobs."""
    _rows(con)
    intensity, err = _clean_intensity(intensity)
    if err:
        return None, err
    if mode not in ("branch", "another", None, ""):
        return None, "请求格式不对"
    if mode in (None, "", "another"):
        mode = "another"
        parent_version_id = None
    try:
        base_id = int(base_id)
    except (TypeError, ValueError):
        return None, "题目不存在"
    if paper_id not in (None, ""):
        try:
            paper_id = int(paper_id)
        except (TypeError, ValueError):
            return None, "试卷不存在"
    else:
        paper_id = None
    if feedback is None:
        feedback = ""
    if not isinstance(feedback, str):
        return None, "修改意见格式不对"
    feedback = feedback.strip()
    if len(feedback) > 2000:
        feedback = feedback[:2000]
    base = _load_question(con, base_id)
    if not base:
        return None, "题目不存在"
    if not _is_human_base(base):
        return None, "请对试卷上的原题生成变式"
    try:
        con.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError:
        return None, "请稍后再试"
    try:
        active = _active_job(con, base_id)
        if active:
            con.commit()
            return _job_public(active, duplicate=True), None
        if mode == "branch":
            try:
                parent_version_id = int(parent_version_id)
            except (TypeError, ValueError):
                con.rollback()
                return None, "请选择要修改的版本"
            parent = con.execute(
                "SELECT * FROM ai_versions WHERE id=? AND base_question_id=?",
                (parent_version_id, base_id),
            ).fetchone()
            if not parent:
                con.rollback()
                return None, "找不到要修改的版本"
            parent_version_id = _usable_parent_id(con, base_id, parent["id"])
        else:
            parent_version_id = None
        sess = con.execute(
            "SELECT id FROM ai_sessions WHERE base_question_id=?", (base_id,)
        ).fetchone()
        now = _now()
        if sess:
            session_id = sess[0]
        else:
            cur = con.execute(
                """INSERT INTO ai_sessions
                   (base_question_id, profile_json, image_json, profile_model, profile_status, created_at)
                   VALUES (?,?,?,?,?,?)""",
                (base_id, None, None, None, "", now),
            )
            session_id = cur.lastrowid
        seq = con.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM ai_versions WHERE base_question_id=?",
            (base_id,),
        ).fetchone()[0] + 1
        cur = con.execute(
            """INSERT INTO ai_versions (
                   session_id, base_question_id, parent_version_id, question_id, seq, status,
                   intensity, teacher_feedback, generator_model, prompt_version, candidate_json,
                   judge_model, judge_status, judge_json, error, created_at
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                session_id, base_id, parent_version_id, None, seq, "QUEUED",
                intensity, feedback, None, PROMPT_VERSION, None,
                None, None, None, None, now,
            ),
        )
        version_id = cur.lastrowid
        cur = con.execute(
            """INSERT INTO ai_jobs
               (paper_id, base_question_id, version_id, phase, status, attempts, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (paper_id, base_id, version_id, "generate", "queued", 0, now),
        )
        job_id = cur.lastrowid
        con.commit()
    except Exception:
        try:
            con.rollback()
        except Exception:
            pass
        raise
    row = con.execute("SELECT * FROM ai_jobs WHERE id=?", (job_id,)).fetchone()
    return _job_public(row, duplicate=False), None


def enqueue_paper(con, paper_id, intensity, question_ids):
    """One job per human question on the paper. Does not batch them into one model call."""
    _rows(con)
    intensity, err = _clean_intensity(intensity)
    if err:
        return None, err
    paper = banklib.get_paper(con, paper_id)
    if not paper:
        return None, "试卷不存在"
    allowed = list(paper["ids"])
    if question_ids is not None:
        want = []
        if not isinstance(question_ids, list):
            return None, "请求格式不对"
        for raw in question_ids:
            try:
                want.append(int(raw))
            except (TypeError, ValueError):
                continue
        want_set = set(want)
        allowed = [qid for qid in allowed if qid in want_set]
    jobs = []
    for qid in allowed:
        row = _load_question(con, qid)
        if not _is_human_base(row):
            continue
        job, jerr = enqueue_base(con, paper["id"], qid, None, "", intensity, "another")
        if jerr:
            return None, jerr
        jobs.append(job)
    return {"jobs": jobs}, None


def retry_version(con, version_id):
    """Reuse a GENERATION_ERROR row. Does not allocate a new seq."""
    _rows(con)
    try:
        version_id = int(version_id)
    except (TypeError, ValueError):
        return None, "版本不存在"
    try:
        con.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError:
        return None, "请稍后再试"
    try:
        ver = con.execute("SELECT * FROM ai_versions WHERE id=?", (version_id,)).fetchone()
        if not ver:
            con.rollback()
            return None, "版本不存在"
        if ver["status"] != "GENERATION_ERROR":
            con.rollback()
            return None, "只有生成没有完成的版本可以重试"
        active = _active_job(con, ver["base_question_id"])
        if active:
            con.commit()
            return _job_public(active, duplicate=True), None
        now = _now()
        con.execute(
            """UPDATE ai_versions
               SET status='QUEUED', error=NULL, candidate_json=NULL, question_id=NULL
               WHERE id=?""",
            (version_id,),
        )
        cur = con.execute(
            """INSERT INTO ai_jobs
               (paper_id, base_question_id, version_id, phase, status, attempts, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (None, ver["base_question_id"], version_id, "generate", "queued", 0, now),
        )
        job_id = cur.lastrowid
        con.commit()
    except Exception:
        try:
            con.rollback()
        except Exception:
            pass
        raise
    row = con.execute("SELECT * FROM ai_jobs WHERE id=?", (job_id,)).fetchone()
    return _job_public(row, duplicate=False), None


def rejudge_version(con, version_id):
    """Rerun judge only. The candidate question stays."""
    _rows(con)
    try:
        version_id = int(version_id)
    except (TypeError, ValueError):
        return None, "版本不存在"
    try:
        con.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError:
        return None, "请稍后再试"
    try:
        ver = con.execute("SELECT * FROM ai_versions WHERE id=?", (version_id,)).fetchone()
        if not ver:
            con.rollback()
            return None, "版本不存在"
        if ver["status"] != "JUDGE_ERROR":
            con.rollback()
            return None, "只有审核没有完成的版本可以重新审核"
        if not ver["candidate_json"]:
            con.rollback()
            return None, "没有可审核的题目"
        active = _active_job(con, ver["base_question_id"])
        if active:
            con.commit()
            return _job_public(active, duplicate=True), None
        now = _now()
        con.execute(
            "UPDATE ai_versions SET status='JUDGING', error=NULL WHERE id=?",
            (version_id,),
        )
        cur = con.execute(
            """INSERT INTO ai_jobs
               (paper_id, base_question_id, version_id, phase, status, attempts, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (None, ver["base_question_id"], version_id, "judge", "queued", 0, now),
        )
        job_id = cur.lastrowid
        con.commit()
    except Exception:
        try:
            con.rollback()
        except Exception:
            pass
        raise
    row = con.execute("SELECT * FROM ai_jobs WHERE id=?", (job_id,)).fetchone()
    return _job_public(row, duplicate=False), None


def exclude_version(con, version_id):
    """Hide a shown version from the page and from future model context.

    The ai_versions row stays. excluded=1 keeps it out of parent selection.
    """
    _rows(con)
    try:
        version_id = int(version_id)
    except (TypeError, ValueError):
        return None, "版本不存在"
    ver = con.execute("SELECT * FROM ai_versions WHERE id=?", (version_id,)).fetchone()
    if not ver:
        return None, "版本不存在"
    con.execute("UPDATE ai_versions SET excluded=1 WHERE id=?", (version_id,))
    if ver["question_id"]:
        con.execute(
            "UPDATE questions SET in_bank=0 WHERE id=? AND id!=?",
            (ver["question_id"], ver["base_question_id"]),
        )
        con.execute("DELETE FROM paper_items WHERE question_id=?", (ver["question_id"],))
    con.commit()
    return {"ok": True, "version_id": version_id, "excluded": 1}, None


def update_version_text(con, version_id, payload):
    """Save stem and answer onto this version's question row only. Not the base."""
    _rows(con)
    try:
        version_id = int(version_id)
    except (TypeError, ValueError):
        return None, "版本不存在"
    ver = con.execute("SELECT * FROM ai_versions WHERE id=?", (version_id,)).fetchone()
    if not ver:
        return None, "版本不存在"
    if not _is_shown_version(ver):
        return None, "这一版不能修改"
    if not ver["question_id"]:
        return None, "这版还没有题目"
    if not isinstance(payload, dict) or not isinstance(payload.get("body"), str):
        return None, "请求格式不对"
    if "answer" in payload and not isinstance(payload.get("answer"), str):
        return None, "请求格式不对"
    body = payload["body"]
    q = _load_question(con, ver["question_id"])
    if not q:
        return None, "题目不存在"
    if int(q["id"]) == int(ver["base_question_id"]) or _origin_of(q) != "ai":
        return None, "不能修改原题"
    try:
        segments = json.loads(q["segments"] or "[]")
    except Exception:
        segments = []
    if not isinstance(segments, list):
        segments = []
    segments = banklib.sync_stem_segments(segments, body)
    if "answer" in payload:
        answer = payload["answer"]
    else:
        answer = q["answer"] or ""
    try:
        cand = json.loads(ver["candidate_json"] or "{}")
    except Exception:
        cand = {}
    if not isinstance(cand, dict):
        cand = {}
    cand["stem"] = body
    cand["segments"] = segments
    if "answer" in payload:
        cand["answer"] = answer
    con.execute(
        "UPDATE questions SET body=?, segments=?, answer=?, body_manual=1 WHERE id=?",
        (body, json.dumps(segments, ensure_ascii=False), answer, int(q["id"])),
    )
    con.execute(
        "UPDATE ai_versions SET candidate_json=? WHERE id=?",
        (json.dumps(cand, ensure_ascii=False), version_id),
    )
    con.commit()
    return {
        "ok": True,
        "version_id": version_id,
        "question_id": int(q["id"]),
        "body": body,
        "answer": cand.get("answer") if isinstance(cand.get("answer"), str) else answer,
        "segments": segments,
    }, None


def keep_version(con, version_id):
    """收入题库：the draft becomes searchable. Does not by itself add it to a paper."""
    _rows(con)
    try:
        version_id = int(version_id)
    except (TypeError, ValueError):
        return None, "版本不存在"
    ver = con.execute("SELECT * FROM ai_versions WHERE id=?", (version_id,)).fetchone()
    if not ver:
        return None, "版本不存在"
    if not ver["question_id"]:
        return None, "这版还没有题目"
    con.execute("UPDATE questions SET in_bank=1 WHERE id=?", (ver["question_id"],))
    con.commit()
    return {"ok": True, "question_id": ver["question_id"], "in_bank": 1, "version_id": version_id}, None


def _profile_summary(raw):
    if not raw:
        return None
    try:
        profile = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return None
    if not isinstance(profile, dict):
        return None
    out = {}
    for key in ("qtype", "knowledge", "major", "minor", "skill", "difficulty"):
        val = profile.get(key)
        if isinstance(val, str) and val.strip():
            out[key] = val.strip()
    return out or None


def _candidate_public(raw, segments_fallback=None, body_fallback=None, answer_fallback=None):
    data = None
    if raw:
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            data = None
    if not isinstance(data, dict):
        data = {}
    segments = data.get("segments")
    if not isinstance(segments, list) and segments_fallback is not None:
        segments = segments_fallback
    stem = data.get("stem") or data.get("body") or body_fallback or ""
    answer = data.get("answer") or ""
    analysis = data.get("analysis") or ""
    if not answer and answer_fallback and not data:
        answer = answer_fallback
    return {
        "stem": stem,
        "answer": answer,
        "analysis": analysis,
        "qtype": data.get("qtype") or "",
        "depends_on_image": bool(data.get("depends_on_image")),
        "segments": segments if isinstance(segments, list) else [],
    }


def _judge_public(raw, judge_status):
    data = None
    if raw:
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            data = None
    if not isinstance(data, dict):
        data = {}
    issues = []
    for item in data.get("issues") or []:
        if isinstance(item, dict):
            issues.append({
                "type": str(item.get("type") or ""),
                "message": str(item.get("message") or ""),
            })
        elif isinstance(item, str):
            issues.append({"type": "", "message": item})
    return {
        "status": judge_status or data.get("status") or "",
        "confidence": data.get("confidence"),
        "issues": issues,
        "recommendation": data.get("recommendation") or "",
        "knowledge_correct": data.get("knowledge_correct"),
        "conditions_sufficient": data.get("conditions_sufficient"),
        "answer_unique": data.get("answer_unique"),
        "calculation_verified": data.get("calculation_verified"),
        "grade_level_appropriate": data.get("grade_level_appropriate"),
        "image_consistent": data.get("image_consistent"),
    }


def _public_base(con, qid, on_paper):
    """One human base in the shape paper_status returns. (None, False) if not a human question."""
    row = _load_question(con, qid)
    if not _is_human_base(row):
        return None, False
    active = False
    sess = con.execute(
        "SELECT * FROM ai_sessions WHERE base_question_id=?", (qid,)
    ).fetchone()
    versions = con.execute(
        "SELECT * FROM ai_versions WHERE base_question_id=? ORDER BY seq, id",
        (qid,),
    ).fetchall()
    by_id = {v["id"]: v for v in versions}
    job = _active_job(con, qid)
    if job:
        active = True
    notices = []
    grouped = {}
    group_order = []
    for v in versions:
        key = v["parent_version_id"]
        if key not in grouped:
            grouped[key] = []
            group_order.append(key)
        grouped[key].append(v)
    for key in group_order:
        streak = _trailing_bad_count(grouped[key])
        if streak <= 0:
            continue
        group_inflight = any(v["status"] in VERSION_NONTERMINAL for v in grouped[key])
        if streak >= AUTO_RETRY_LIMIT and not group_inflight:
            notices.append({
                "parent_version_id": key,
                "stopped": True,
                "message": STOP_NOTICE,
            })
        else:
            notices.append({
                "parent_version_id": key,
                "stopped": False,
                "message": RETRY_NOTICE,
            })
    for v in versions:
        if v["status"] in ("GENERATION_ERROR", "JUDGE_ERROR"):
            notices.append({"stopped": False, "message": _safe_text(v["error"] or "模型请求没有完成"),
                            "error": True, "version_id": v["id"]})
    pub_versions = []
    for v in versions:
        if v["status"] in VERSION_NONTERMINAL:
            active = True
        if not _is_shown_version(v):
            continue
        parent_seq = None
        if v["parent_version_id"] and v["parent_version_id"] in by_id:
            parent_seq = by_id[v["parent_version_id"]]["seq"]
        segs = None
        body = None
        answer = None
        in_bank = 0
        if v["question_id"]:
            q = _load_question(con, v["question_id"])
            if q:
                try:
                    segs = json.loads(q["segments"] or "[]")
                except Exception:
                    segs = []
                body = q["body"]
                answer = q["answer"]
                keys = q.keys()
                in_bank = 1 if ("in_bank" in keys and q["in_bank"] not in (0, "0")) else 0
        pub_versions.append({
            "id": v["id"],
            "seq": v["seq"],
            "parent_version_id": v["parent_version_id"],
            "parent_seq": parent_seq,
            "status": v["status"],
            "intensity": v["intensity"] or "medium",
            "teacher_feedback": v["teacher_feedback"] or "",
            "question_id": v["question_id"],
            "in_bank": in_bank,
            "on_paper": bool(v["question_id"] and v["question_id"] in on_paper),
            "candidate": _candidate_public(v["candidate_json"], segs, body, answer),
            "judge": _judge_public(v["judge_json"], v["judge_status"]),
        })
    return {
        "base_question_id": qid,
        "profile_summary": _profile_summary(sess["profile_json"]) if sess else None,
        "profile_status": (sess["profile_status"] if sess else "") or "",
        "active_job": _job_public(job, duplicate=False) if job else None,
        "versions": pub_versions,
        "notices": notices,
    }, active


def paper_status(con, paper_id):
    _rows(con)
    paper = banklib.get_paper(con, paper_id)
    if not paper:
        return None
    on_paper = set(paper["ids"])
    bases = []
    active = False
    for qid in paper["ids"]:
        base, live = _public_base(con, qid, on_paper)
        if base is None:
            continue
        if live:
            active = True
        bases.append(base)
    return {"paper_id": paper["id"], "active": active, "bases": bases}


def questions_status(con, ids, paper_id=None):
    """Same per-base shape as paper_status, for the ids on the current page.

    paper_id is optional. When it names an open paper, on_paper is set from that paper.
    """
    _rows(con)
    on_paper = set()
    if paper_id not in (None, ""):
        try:
            paper_id = int(paper_id)
        except (TypeError, ValueError):
            paper_id = None
    if paper_id is not None:
        paper = banklib.get_paper(con, paper_id)
        if paper:
            on_paper = set(paper["ids"])
    bases = []
    active = False
    seen = set()
    for raw in ids or []:
        try:
            qid = int(raw)
        except (TypeError, ValueError):
            continue
        if qid in seen:
            continue
        seen.add(qid)
        base, live = _public_base(con, qid, on_paper)
        if base is None:
            continue
        if live:
            active = True
        bases.append(base)
    return {"active": active, "bases": bases}


def _iter_parts(segments):
    for para in segments or []:
        if isinstance(para, dict) and para.get("t") == "table":
            for row in para.get("rows") or []:
                for cell in row or []:
                    for part in cell or []:
                        if isinstance(part, dict):
                            yield part
        elif isinstance(para, list):
            for part in para:
                if isinstance(part, dict):
                    yield part
        elif isinstance(para, dict):
            yield para


def collect_imgs(segments):
    out = []
    seen = set()
    for part in _iter_parts(segments):
        if part.get("t") != "img":
            continue
        src = part.get("src") or ""
        sha = part.get("sha") or ""
        key = sha or src
        if not src or key in seen:
            continue
        seen.add(key)
        out.append({"t": "img", "sha": sha, "src": src})
    return out


def _segments_of_row(row):
    try:
        data = json.loads(row["segments"] or "[]")
    except Exception:
        data = []
    return data if isinstance(data, list) else []


def _has_image(row, segments):
    try:
        if int(row["image_count"] or 0) > 0:
            return True
    except (TypeError, ValueError):
        pass
    return bool(collect_imgs(segments))


def _image_paths(segments):
    paths = []
    images = collect_imgs(segments)
    if len(images) > 32:
        raise ModelError("image", "单题超过程序的 32 张图片上限，请核对是否存在粘连题并拆分")
    for img in images:
        name = os.path.basename(img.get("src") or "")
        if not re.fullmatch(r"[0-9a-f]{64}\.(png|jpg|jpeg|gif|bmp|webp)", name):
            raise ModelError("image", "题目图片格式无法送入 AI，请先生成 PNG/JPEG 预览：" + name)
        path = os.path.join(banklib.MEDIA, name)
        if not os.path.isfile(path):
            raise ModelError("image", "题目图片文件缺失：" + name)
        paths.append(path)
    return paths


def _data_url(path):
    ext = path.rsplit(".", 1)[-1].lower()
    mime = {
        "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "gif": "image/gif", "webp": "image/webp", "bmp": "image/bmp",
    }.get(ext)
    if not mime:
        return None
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    if not data or len(data) > 8 * 1024 * 1024:
        return None
    b64 = base64.b64encode(data).decode("ascii")
    return "data:%s;base64,%s" % (mime, b64)


def _only_keepalive(blob):
    """True when the bytes so far are empty or only SSE keep-alive comments."""
    if not blob or not bytes(blob).strip():
        return True
    try:
        text = bytes(blob).decode("utf-8")
    except UnicodeDecodeError:
        return False
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return True
    for ln in lines:
        low = ln.lower()
        if low.startswith(":") or low in ("keep-alive", "event: ping", "event:ping"):
            continue
        return False
    return True


def _json_payload(blob):
    """Parse a non-streaming JSON body. Ignore leading keep-alive lines."""
    if _only_keepalive(blob):
        return None
    text = bytes(blob).decode("utf-8", errors="replace")
    lines = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s:
            continue
        if s.startswith(":") or s.lower() in ("keep-alive", "event: ping", "event:ping"):
            continue
        lines.append(ln)
    text = "\n".join(lines).strip()
    if not text:
        return None
    if text[0] not in "{[":
        return None
    data = json.loads(text)
    if not isinstance(data, dict):
        return None
    return data


def _http_post(url, key, body, timeout):
    """POST JSON without streaming. Hard deadline `timeout` seconds.

    A socket that only delivers keep-alives, with no content within 60s, fails.
    The key is sent on the Authorization header and is never logged.
    Returns (status, payload or None).
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        return 503, None
    path = parsed.path or "/"
    if parsed.query:
        path = path + "?" + parsed.query
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    conn = http.client.HTTPSConnection(parsed.hostname, parsed.port or 443, timeout=timeout)
    started = time.monotonic()
    try:
        conn.request(
            "POST",
            path,
            body=raw,
            headers={
                "Authorization": "Bearer " + key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        if conn.sock is not None:
            conn.sock.settimeout(max(0.1, timeout - (time.monotonic() - started)))
        resp = conn.getresponse()
        status = resp.status
        if status != 200:
            try:
                error_data = json.loads(resp.read(4096).decode("utf-8"))
            except Exception:
                error_data = None
            return status, error_data
        buf = bytearray()
        first_byte = None
        while True:
            now = time.monotonic()
            if now - started >= timeout:
                return 503, {"error": {"message": "模型请求超过 %s 秒限时" % timeout}}
            # Keep-alives alone are not progress. Content must show up within 60s
            # of the first body byte. A quiet non-streaming response may use the
            # full socket deadline (about 90s) before any byte arrives.
            if (
                first_byte is not None
                and _only_keepalive(buf)
                and now - first_byte >= _KEEPALIVE_LIMIT
            ):
                return 503, {"error": {"message": "模型只返回心跳，没有有效结果"}}
            sock = conn.sock or getattr(getattr(resp.fp, "raw", None), "_sock", None)
            if sock is not None:
                remaining = timeout - (now - started)
                if first_byte is not None and _only_keepalive(buf):
                    remaining = min(remaining, _KEEPALIVE_LIMIT - (now - first_byte))
                sock.settimeout(max(0.1, remaining))
            part = resp.read1(8192)
            if not part:
                break
            if first_byte is None:
                first_byte = time.monotonic()
            buf.extend(part)
            if len(buf) > 8 * 1024 * 1024:
                return 503, None
        if _only_keepalive(buf):
            return 503, None
        try:
            payload = _json_payload(buf)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return 503, None
        if payload is None:
            return 503, None
        return 200, payload
    except (TimeoutError, socket.timeout):
        return 503, {"error": {"message": "模型请求超时（%s 秒限时）" % timeout}}
    except (http.client.HTTPException, OSError, ValueError) as exc:
        return 503, {"error": {"message": "模型连接异常：" + type(exc).__name__}}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _post_chat(key, body):
    """DeepSeek chat completion. Same request shape as before; finite deadline."""
    return _http_post(API_URL, key, body, _CALL_TIMEOUT)


def _message_text(data):
    if not isinstance(data, dict):
        raise ModelError("empty")
    choices = data.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        raise ModelError("empty")
    if choices[0].get("finish_reason") == "length":
        raise ModelError("output_limit")
    msg = choices[0].get("message") or {}
    content = msg.get("content")
    if isinstance(content, list):
        bits = []
        for part in content:
            if isinstance(part, str):
                bits.append(part)
            elif isinstance(part, dict) and part.get("type") in (None, "text"):
                bits.append(str(part.get("text") or ""))
        content = "\n".join(bits)
    if not isinstance(content, str) or not content.strip():
        raise ModelError("empty")
    return content


def _user_content(user_text, image_paths):
    urls = []
    for path in image_paths or []:
        url = _data_url(path)
        if not url:
            raise ModelError("image", "图片缺失、格式不支持或超过 8 MB，无法提交 AI：" + os.path.basename(path))
        urls.append(url)
    if not urls:
        return user_text
    content = [{"type": "text", "text": user_text}]
    for url in urls:
        content.append({"type": "image_url", "image_url": {"url": url}})
    return content


def _load_local_keys():
    """Fill empty provider env vars from keys.local next to this file.

    The file is never committed. Existing environment variables win.
    """
    if os.environ.get("CHEM_DISABLE_AI") == "1":
        return
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "keys.local")
    if not os.path.isfile(path):
        return
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and value and not (os.environ.get(key) or "").strip():
            os.environ[key] = value


def _load_provider_config():
    """Read provider env vars. Values stay in memory and are never written out."""
    global _api_key, _flash_model, _pro_model
    global _glm_key, _glm_flash, _glm_pro, _glm_url
    global _ds_key, _ds_flash, _ds_pro
    _load_local_keys()
    _glm_key = (os.environ.get("GLM_API_KEY") or "").strip()
    _glm_flash = (os.environ.get("GLM_FLASH") or "").strip() or "glm-5.3-flash"
    _glm_pro = (os.environ.get("GLM_PRO") or "").strip() or "glm-5.3"
    _glm_url = (os.environ.get("GLM_API_URL") or "").strip() or GLM_API_URL
    _ds_key = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    _ds_flash = (os.environ.get("DEEPSEEK_FLASH") or "").strip() or "deepseek-flash"
    _ds_pro = (os.environ.get("DEEPSEEK_PRO") or "").strip() or "deepseek-v4-pro"
    _api_key = _ds_key
    _flash_model = _ds_flash
    _pro_model = _ds_pro


def _preferred_model(role):
    if _glm_key:
        return _glm_flash if role == "flash" else _glm_pro
    return _ds_flash if role == "flash" else _ds_pro


def _glm_bodies(model, system, user_text, image_paths):
    """GLM request bodies. thinking.type is enabled. Images only on the flash model."""
    flash = model == _glm_flash
    content = _user_content(user_text, image_paths if flash else None)
    thinking = {"type": "enabled"}
    if flash:
        thinking["clear_thinking"] = False
    rich = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ],
        "temperature": 1,
        "top_p": 0.95,
        "max_tokens": 8192,
        "stream": False,
        "thinking": thinking,
        "response_format": {"type": "json_object"},
    }
    if model.lower().startswith(("glm-5.2", "glm-5.3")):
        effort = (os.environ.get("GLM_REASONING_EFFORT") or "high").lower().strip()
        if effort not in ("low", "high", "max"):
            raise ModelError("config", "GLM_REASONING_EFFORT 只能是 low、high 或 max")
        rich["reasoning_effort"] = effort
    try:
        rich["max_tokens"] = int(os.environ.get("GLM_MAX_TOKENS") or 8192)
        if not 512 <= rich["max_tokens"] <= 32768:
            raise ValueError()
    except ValueError:
        raise ModelError("config", "GLM_MAX_TOKENS 必须是 512 至 32768 的整数")
    plain = dict(rich)
    plain.pop("response_format", None)
    plain["thinking"] = dict(thinking)
    return rich, plain


def _glm_chat(model, system, user_text, image_paths=None):
    """One non-streaming GLM call. Does not send thinking disabled. No image on glm-5.3."""
    key = _glm_key
    if not key:
        raise ModelError("config")
    rich, plain = _glm_bodies(model, system, user_text, image_paths)
    status, data = _http_post(_glm_url, key, rich, _CALL_TIMEOUT)
    if status == 400:
        status, data = _http_post(_glm_url, key, plain, _CALL_TIMEOUT)
    if status == 200 and data is not None:
        return _message_text(data)
    if status in (401, 403):
        raise ModelError("auth")
    error = data.get("error", {}) if isinstance(data, dict) else {}
    message = error.get("message") if isinstance(error, dict) else ""
    code = error.get("code") if isinstance(error, dict) else ""
    raise ModelError("http", "GLM 请求失败（HTTP %s%s）%s" %
                     (status, "，代码 " + str(code) if code else "", "：" + _safe_text(message) if message else ""),
                     http_status=status, provider_code=code)


def chat_complete(model, system, user_text, image_paths=None):
    """DeepSeek chat completion. No shared history. Key stays in the Authorization header only."""
    return _deepseek_chat(model, system, user_text, image_paths)


def _deepseek_chat(model, system, user_text, image_paths=None):
    """DeepSeek call; complete_role strips images for text roles, not by model-name equality."""
    key = _ds_key or _api_key
    if not key:
        raise ModelError("config")
    content = _user_content(user_text, image_paths)
    try:
        budget = int(os.environ.get('DEEPSEEK_MAX_TOKENS') or 8192)
        if not 512 <= budget <= 32768:
            raise ValueError()
    except ValueError:
        raise ModelError('config', 'DEEPSEEK_MAX_TOKENS 必须是 512 至 32768 的整数')
    base = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ],
        "temperature": 0.3,
        "max_tokens": budget,
        "stream": False,
        "thinking": {"type": "disabled"},
    }
    rich = dict(base)
    rich["response_format"] = {"type": "json_object"}
    rich["thinking"] = {"type": "disabled"}
    bodies = (rich, base)
    for bi, body in enumerate(bodies):
        for attempt in (0, 1):
            status, data = _post_chat(key, body)
            if status == 200:
                return _message_text(data)
            if status in (429, 500, 502, 503, 504) and attempt == 0:
                time.sleep(1.5)
                continue
            if status == 400 and bi == 0:
                break
            if status in (401, 403):
                raise ModelError("auth")
            error = data.get('error', {}) if isinstance(data, dict) else {}
            message = error.get('message') if isinstance(error, dict) else ''
            code = error.get('code') if isinstance(error, dict) else ''
            raise ModelError('http', 'DeepSeek 请求失败（HTTP %s%s）%s' %
                             (status, '，代码 '+str(code) if code else '', '：'+_safe_text(message) if message else ''),
                             http_status=status, provider_code=code)
    raise ModelError("http")


def _glm_paused():
    with _provider_lock:
        return bool(_glm_key and _glm_pause_key == _glm_key and time.monotonic() < _glm_pause_until)


def _pause_glm_if_quota(exc):
    global _glm_pause_until, _glm_pause_key
    if isinstance(exc, ModelError) and (exc.http_status == 402 or exc.provider_code in _GLM_QUOTA_CODES):
        with _provider_lock:
            _glm_pause_key = _glm_key
            _glm_pause_until = time.monotonic() + 600


def _validate_role_response(system, text):
    data = parse_model_json(text)
    if system == SYSTEM_GENERATOR:
        answer = data.get('answer')
        if not isinstance(answer, str) or not answer.strip() or answer.strip() in ('略', '待补充', '暂无答案', 'N/A', '-'):
            raise ModelError('answer')
    return data


def complete_role(role, system, user_text, image_paths=None, preferred_provider=None):
    """One question. GLM first when configured, then one DeepSeek call of the same role.

    Returns (text, model_id) for the call that actually returned parseable JSON.
    Images are attached only on the flash / vision role.
    """
    if os.environ.get("CHEM_DISABLE_AI") == "1":
        raise ModelError("config")
    if role != "flash":
        image_paths = None
    ds_error = None
    # Answer-only independent checking can use a different available provider.
    # This preference is local to the call; never switch global keys in worker threads.
    if preferred_provider == 'deepseek' and _ds_key:
        model = _ds_flash if role == 'flash' else _ds_pro
        try:
            text = _deepseek_chat(model, system, user_text, image_paths)
            _validate_role_response(system, text)
            return text, model
        except (ModelError, ValueError, json.JSONDecodeError) as exc:
            if isinstance(exc, ModelError) and exc.kind in ('config', 'image'):
                raise
            ds_error = exc
    glm_error = None
    if _glm_key and not (_ds_key and _glm_paused()):
        model = _glm_flash if role == "flash" else _glm_pro
        try:
            text = _glm_chat(model, system, user_text, image_paths)
            _validate_role_response(system, text)
            return text, model
        except (ModelError, ValueError, json.JSONDecodeError) as exc:
            if isinstance(exc, ModelError) and exc.kind in ("config", "image"):
                raise
            if not _ds_key:
                raise
            _pause_glm_if_quota(exc)
            glm_error = _safe_text(exc)
    if ds_error is not None:
        if glm_error:
            raise ModelError('http', '独立核对服务：' + _safe_text(ds_error) + '；GLM：' + glm_error) from None
        raise ds_error
    if not _ds_key:
        raise ModelError("config")
    model = _ds_flash if role == "flash" else _ds_pro
    try:
        text = _deepseek_chat(model, system, user_text, image_paths)
        _validate_role_response(system, text)
    except (ModelError, ValueError, json.JSONDecodeError) as exc:
        if glm_error:
            raise ModelError("http", "GLM：" + glm_error + "；备用模型：" + _safe_text(exc)) from None
        raise
    return text, model


def _plain(segments):
    return "\n".join(banklib.item_plain(p) for p in segments).strip()


def build_variant_segments(candidate, base_segments):
    """Turn model JSON into card segments. Reuse original /media images only."""
    if not isinstance(candidate, dict):
        return None
    depends = bool(candidate.get("depends_on_image"))
    base_imgs = collect_imgs(base_segments)
    by_src = {img["src"]: img for img in base_imgs}
    by_sha = {img["sha"]: img for img in base_imgs if img.get("sha")}

    def clean_part(part):
        if not isinstance(part, dict):
            return None
        if part.get("t") == "text":
            return {"t": "text", "s": "" if part.get("s") is None else str(part.get("s"))}
        if part.get("t") == "img":
            if not depends:
                return None
            img = None
            if part.get("src") in by_src:
                img = by_src[part["src"]]
            elif part.get("sha") in by_sha:
                img = by_sha[part["sha"]]
            if not img:
                return None
            return {"t": "img", "sha": img.get("sha") or "", "src": img["src"]}
        return None

    segments = None
    raw = candidate.get("segments")
    if isinstance(raw, list) and raw:
        if all(isinstance(p, list) for p in raw):
            out = []
            for para in raw:
                parts = [p for p in (clean_part(x) for x in para) if p]
                if parts:
                    out.append(parts)
            if out:
                segments = out
        elif all(isinstance(p, dict) for p in raw):
            parts = [p for p in (clean_part(x) for x in raw) if p]
            if parts:
                segments = [parts]

    stem = candidate.get("stem")
    if not isinstance(stem, str) or not stem.strip():
        stem = candidate.get("body") if isinstance(candidate.get("body"), str) else ""
    options = candidate.get("options") if isinstance(candidate.get("options"), list) else []
    extra = []
    for opt in options:
        if isinstance(opt, str) and opt.strip() and opt.strip() not in stem:
            extra.append(opt.strip())
    if extra:
        stem = (stem or "").rstrip() + "\n" + "\n".join(extra)

    if not segments:
        lines = stem.split("\n") if stem else []
        if not any(ln.strip() for ln in lines):
            return None
        segments = [[{"t": "text", "s": ln}] for ln in lines]
        if depends and base_imgs:
            segments.append([dict(img) for img in base_imgs])
    elif depends and base_imgs and not collect_imgs(segments):
        segments.append([dict(img) for img in base_imgs])

    body = _plain(segments)
    if not body:
        return None
    qtype = candidate.get("qtype") if isinstance(candidate.get("qtype"), str) else ""
    analysis = candidate.get("analysis") if isinstance(candidate.get("analysis"), str) else ""
    answer = candidate.get("answer") if isinstance(candidate.get("answer"), str) else ""
    if not answer.strip():
        return None
    opt_out = [o.strip() for o in options if isinstance(o, str) and o.strip()]
    normalized = {
        "stem": body,
        "answer": answer.strip(),
        "analysis": analysis.strip(),
        "qtype": qtype,
        "options": opt_out,
        "depends_on_image": bool(depends and collect_imgs(segments)),
        "segments": segments,
    }
    return normalized


def _store_question(con, version_id, base, normalized, epoch=None, job_id=None):
    """Insert the draft and point the version at it. in_bank stays 0. One transaction.

    If the job was cancelled while the model call was in flight, insert nothing.
    """
    segments = normalized["segments"]
    body = normalized["stem"]
    answer = normalized["answer"]
    if normalized.get("analysis"):
        answer = answer + "\n\n" + normalized["analysis"]
    qtype = normalized.get("qtype") or ""
    if qtype not in banklib.QTYPES:
        qtype = base["qtype"] if base["qtype"] in banklib.QTYPES else "简答题"
    majors, minors = banklib.load_labels(con, base["id"], base["major"], base["minor"])
    major = majors[0] if majors else base["major"]
    minor = minors[0] if minors else base["minor"]
    imgs = collect_imgs(segments)
    key = hashlib.sha256(
        ("ai-v|%s|%s" % (version_id, uuid.uuid4().hex)).encode("utf-8")
    ).hexdigest()
    con.execute("BEGIN IMMEDIATE")
    try:
        if _should_stop(con, job_id, version_id, epoch):
            con.rollback()
            return None
        held = con.execute(
            "SELECT question_id, status FROM ai_versions WHERE id=?", (version_id,)
        ).fetchone()
        if not held or held["status"] == "CANCELLED":
            con.rollback()
            return None
        if held and held["question_id"]:
            qid = held["question_id"]
            con.execute(
                """UPDATE questions
                   SET body=?, answer=?, segments=?, image_count=?, qtype=?
                   WHERE id=?""",
                (body, answer, json.dumps(segments, ensure_ascii=False), len(imgs), qtype, qid),
            )
        else:
            cur = con.execute(
                """INSERT INTO questions (
                       dedup_key, qnum, body, answer, segments, major, minor, image_count, qtype,
                       category_manual, qtype_manual, body_manual, origin, base_question_id,
                       ai_version_id, in_bank
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    key, "", body, answer, json.dumps(segments, ensure_ascii=False),
                    major, minor, len(imgs), qtype,
                    1, 1, 1, "ai", base["id"], version_id, 0,
                ),
            )
            qid = cur.lastrowid
            banklib.set_question_labels(con, qid, majors or [major], minors or [minor])
            con.execute(
                "INSERT INTO sources (question_id, rel_path, orig_qnum) VALUES (?,?,?)",
                (qid, "变式题（母题 %s）" % base["id"], ""),
            )
        cur = con.execute(
            """UPDATE ai_versions
               SET question_id=?, candidate_json=?, status='GENERATED', error=NULL
               WHERE id=? AND status!='CANCELLED'""",
            (qid, json.dumps(normalized, ensure_ascii=False), version_id),
        )
        if cur.rowcount != 1:
            con.rollback()
            return None
        con.commit()
    except Exception:
        try:
            con.rollback()
        except Exception:
            pass
        raise
    return qid


def _set_version(con, version_id, status, error=None, **extra):
    sets = ["status=?", "error=?"]
    args = [status, error]
    for key, val in extra.items():
        sets.append(key + "=?")
        args.append(val)
    args.append(version_id)
    con.execute(
        "UPDATE ai_versions SET " + ", ".join(sets) + " WHERE id=? AND status!='CANCELLED'",
        args,
    )
    con.commit()


def _finish_job(con, job_id, status="done"):
    con.execute(
        "UPDATE ai_jobs SET status=? WHERE id=? AND status!='cancelled'",
        (status, job_id),
    )
    con.commit()


def _profile_user(base, segments, need_image):
    imgs = collect_imgs(segments)
    payload = {
        "qtype": base["qtype"],
        "major": base["major"],
        "minor": base["minor"],
        "body": base["body"],
        "answer": base["answer"] or "",
        "has_image": need_image,
        "images": imgs,
        "curriculum_tags": question_analysis.knowledge_info(base["body"]),
    }
    return question_analysis.curriculum_prompt() + "\n下面是母题，请写题目画像。只输出 JSON。\n" + json.dumps(payload, ensure_ascii=False)


def _ensure_profile(con, session_id, base, segments):
    sess = con.execute("SELECT * FROM ai_sessions WHERE id=?", (session_id,)).fetchone()
    if sess and sess["profile_status"] == "ok" and sess["profile_json"]:
        profile = json.loads(sess["profile_json"])
        image = json.loads(sess["image_json"]) if sess["image_json"] else None
        return profile, image
    need_image = _has_image(base, segments)
    text, profile_model = complete_role(
        "flash",
        SYSTEM_PROFILE,
        _profile_user(base, segments, need_image),
        _image_paths(segments) if need_image else None,
    )
    data = parse_model_json(text)
    profile = data.get("profile") if isinstance(data.get("profile"), dict) else None
    image = data.get("image")
    if profile is None and "knowledge" in data:
        profile = data
        image = data.get("image")
    if not isinstance(profile, dict) or not str(profile.get("knowledge") or "").strip():
        raise ModelError("parse")
    if not str(profile.get("skill") or "").strip():
        raise ModelError("parse")
    if need_image:
        if not isinstance(image, dict):
            raise ModelError("parse")
    else:
        image = None
    con.execute(
        """UPDATE ai_sessions
           SET profile_json=?, image_json=?, profile_model=?, profile_status='ok'
           WHERE id=?""",
        (
            json.dumps(profile, ensure_ascii=False),
            json.dumps(image, ensure_ascii=False) if image is not None else None,
            profile_model,
            session_id,
        ),
    )
    con.commit()
    return profile, image


def _generation_user(profile, image, parent, feedback, intensity, base_imgs, retry=False):
    profile_context = {key:value for key,value in profile.items() if key != 'original_answer'}
    blocks = [question_analysis.curriculum_prompt(),
        INTENSITY_TEXT.get(intensity, INTENSITY_TEXT["medium"]),
        "题目画像：\n" + json.dumps(profile_context, ensure_ascii=False),
    ]
    if image:
        blocks.append("图像结构（不要发明新图）：\n" + json.dumps(image, ensure_ascii=False))
    if parent:
        slim = {
            "stem": parent.get("stem") or parent.get("body") or "",
            "answer": parent.get("answer") or "",
            "analysis": parent.get("analysis") or "",
            "qtype": parent.get("qtype") or "",
            "options": parent.get("options") or [],
            "depends_on_image": bool(parent.get("depends_on_image")),
        }
        blocks.append("只在下面这一版上修改。不要参考其他版本：\n" + json.dumps(slim, ensure_ascii=False))
    else:
        blocks.append("这是从母题新出的一版。上下文里没有其他变式，不要假设你见过它们。")
    if feedback:
        blocks.append("老师这次的意见：\n" + feedback)
    else:
        blocks.append("老师这次没有额外意见。")
    if base_imgs:
        blocks.append("可以原样引用的原图：\n" + json.dumps(base_imgs, ensure_ascii=False))
    if retry:
        blocks.append('上一版未通过独立检查。这次从画像重新出题，不沿用被否决的题干或答案；逐一独立解答，再核对答案与解析、原图装置、每个空和计算结果保持一致。')
    blocks.append("只输出候选题目 JSON。")
    return "\n\n".join(blocks)


def _judge_user(normalized, profile, image):
    question = {
        "qtype": normalized.get("qtype") or "",
        "stem": normalized.get("stem") or "",
        "options": normalized.get("options") or [],
        "depends_on_image": bool(normalized.get("depends_on_image")),
    }
    tail = {
        "generator_answer": normalized.get("answer") or "",
        "generator_analysis": normalized.get("analysis") or "",
        "profile": {key:value for key,value in profile.items() if key != 'original_answer'},
    }
    if image:
        tail["image"] = image
    return (
        question_analysis.curriculum_prompt() + "\n先不要看生成答案。请独立解答下面这道题，并把你的答案写入 independent_answer。\n"
        + json.dumps(question, ensure_ascii=False)
        + "\n\n独立解答之后，再对照下面的生成答案。不要改写题目。\n"
        + json.dumps(tail, ensure_ascii=False)
        + "\n\n只输出审核 JSON。"
    )


def _candidate_from_version(con, parent):
    if parent["candidate_json"]:
        try:
            data = json.loads(parent["candidate_json"])
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    if parent["question_id"]:
        q = _load_question(con, parent["question_id"])
        if q:
            return {
                "stem": q["body"],
                "answer": q["answer"] or "",
                "segments": _segments_of_row(q),
                "qtype": q["qtype"],
            }
    return None


def _load_parent_candidate(con, parent_id):
    """Only a shown PASS. Walks past excluded and hidden SUSPECT/FAIL siblings."""
    seen = set()
    pid = parent_id
    while pid and pid not in seen:
        seen.add(pid)
        parent = con.execute("SELECT * FROM ai_versions WHERE id=?", (pid,)).fetchone()
        if not parent:
            return None
        if _is_shown_version(parent):
            return _candidate_from_version(con, parent)
        pid = parent["parent_version_id"]
    return None


def _process(con, job_id):
    epoch = _current_epoch()
    with _cancel_lock:
        _job_epochs[int(job_id)] = epoch
    job = con.execute("SELECT * FROM ai_jobs WHERE id=?", (job_id,)).fetchone()
    if not job or job["status"] not in ("running", "queued"):
        return
    if _should_stop(con, job_id, job["version_id"], epoch):
        return
    ver = con.execute("SELECT * FROM ai_versions WHERE id=?", (job["version_id"],)).fetchone()
    if not ver:
        _finish_job(con, job_id, "error")
        return
    if _should_stop(con, job_id, ver["id"], epoch):
        return
    base = _load_question(con, ver["base_question_id"])
    if not base:
        _set_version(con, ver["id"], "GENERATION_ERROR", "原题不在了")
        _finish_job(con, job_id, "done")
        return
    segments = _segments_of_row(base)
    phase = job["phase"] or "generate"
    if phase == "judge":
        if _should_stop(con, job_id, ver["id"], epoch):
            return
        ok = _run_judge(con, job, ver, base, segments, epoch=epoch)
        if not ok or _should_stop(con, job_id, ver["id"], epoch):
            return
        _finish_job(con, job_id, "done")
        _maybe_retry_hidden(con, ver["id"], job["paper_id"], epoch=epoch, job_id=job_id)
        return
    try:
        if _should_stop(con, job_id, ver["id"], epoch):
            return
        con.execute(
            "UPDATE ai_jobs SET phase='profile' WHERE id=? AND status!='cancelled'",
            (job_id,),
        )
        con.commit()
        profile, image = _ensure_profile(con, ver["session_id"], base, segments)
    except ModelError as exc:
        if _should_stop(con, job_id, ver["id"], epoch):
            return
        _set_version(con, ver["id"], "GENERATION_ERROR", _safe_text(exc))
        _finish_job(con, job_id, "done")
        return
    except Exception as exc:
        if _should_stop(con, job_id, ver["id"], epoch):
            return
        _set_version(con, ver["id"], "GENERATION_ERROR", _safe_text(exc) or "生成没有完成")
        _finish_job(con, job_id, "done")
        return
    if _should_stop(con, job_id, ver["id"], epoch):
        return
    need_image = _has_image(base, segments)
    role = "flash" if need_image or int(ver["seq"] or 1) == 1 else "pro"
    _set_version(con, ver["id"], "GENERATING", None, generator_model=_preferred_model(role))
    con.execute(
        "UPDATE ai_jobs SET phase='generate' WHERE id=? AND status!='cancelled'",
        (job_id,),
    )
    con.commit()
    if _should_stop(con, job_id, ver["id"], epoch):
        return
    parent = _load_parent_candidate(con, ver["parent_version_id"])
    paths = _image_paths(segments) if (role == "flash" and need_image) else None
    user = _generation_user(
        profile, image, parent, ver["teacher_feedback"] or "", ver["intensity"] or "medium",
        collect_imgs(segments) if need_image else [],
        retry=chain_bad_count(con, base['id'], ver['parent_version_id']) > 0,
    )
    try:
        text, model = complete_role(role, SYSTEM_GENERATOR, user, paths)
        if _should_stop(con, job_id, ver["id"], epoch):
            return
        con.execute(
            "UPDATE ai_versions SET generator_model=? WHERE id=? AND status!='CANCELLED'",
            (model, ver["id"]),
        )
        con.commit()
        candidate = parse_model_json(text)
        normalized = build_variant_segments(candidate, segments)
        if not normalized:
            raise ModelError("parse")
    except ModelError as exc:
        if _should_stop(con, job_id, ver["id"], epoch):
            return
        _set_version(con, ver["id"], "GENERATION_ERROR", _safe_text(exc))
        _finish_job(con, job_id, "done")
        return
    except Exception as exc:
        if _should_stop(con, job_id, ver["id"], epoch):
            return
        _set_version(con, ver["id"], "GENERATION_ERROR", _safe_text(exc) or "生成没有完成")
        _finish_job(con, job_id, "done")
        return
    if _should_stop(con, job_id, ver["id"], epoch):
        return
    try:
        stored = _store_question(con, ver["id"], base, normalized, epoch=epoch, job_id=job_id)
    except Exception as exc:
        if _should_stop(con, job_id, ver["id"], epoch):
            return
        _set_version(con, ver["id"], "GENERATION_ERROR", _safe_text(exc) or "生成没有完成")
        _finish_job(con, job_id, "done")
        return
    if stored is None or _should_stop(con, job_id, ver["id"], epoch):
        return
    ver = con.execute("SELECT * FROM ai_versions WHERE id=?", (ver["id"],)).fetchone()
    if not ver or _should_stop(con, job_id, ver["id"], epoch):
        return
    ok = _run_judge(con, job, ver, base, segments, profile, image, normalized, epoch=epoch)
    if not ok or _should_stop(con, job_id, ver["id"], epoch):
        return
    _finish_job(con, job_id, "done")
    _maybe_retry_hidden(con, ver["id"], job["paper_id"], epoch=epoch, job_id=job_id)


def _run_judge(con, job, ver, base, base_segments, profile=None, image=None, normalized=None, epoch=None):
    if _should_stop(con, job["id"], ver["id"], epoch):
        return False
    con.execute(
        "UPDATE ai_jobs SET phase='judge' WHERE id=? AND status!='cancelled'",
        (job["id"],),
    )
    if normalized is None:
        try:
            normalized = json.loads(ver["candidate_json"] or "{}")
        except Exception:
            normalized = None
    if not isinstance(normalized, dict) or not normalized.get("stem"):
        if _should_stop(con, job["id"], ver["id"], epoch):
            return False
        _set_version(con, ver["id"], "JUDGE_ERROR", "审核没有完成")
        return True
    if profile is None:
        sess = con.execute(
            "SELECT * FROM ai_sessions WHERE id=?", (ver["session_id"],)
        ).fetchone()
        if not sess or sess["profile_status"] != "ok" or not sess["profile_json"]:
            if _should_stop(con, job["id"], ver["id"], epoch):
                return False
            _set_version(con, ver["id"], "JUDGE_ERROR", "审核没有完成")
            return True
        profile = json.loads(sess["profile_json"])
        image = json.loads(sess["image_json"]) if sess["image_json"] else None
    depends = bool(normalized.get("depends_on_image")) or bool(collect_imgs(normalized.get("segments")))
    if depends:
        role = "flash"
        paths = _image_paths(base_segments)
        if not paths:
            if _should_stop(con, job["id"], ver["id"], epoch):
                return False
            _set_version(con, ver["id"], "JUDGE_ERROR", "审核没有完成", judge_model=_preferred_model(role))
            return True
    else:
        role = "pro"
        paths = None
    if _should_stop(con, job["id"], ver["id"], epoch):
        return False
    model = _preferred_model(role)
    cur = con.execute(
        "UPDATE ai_versions SET status='JUDGING', judge_model=? WHERE id=? AND status!='CANCELLED'",
        (model, ver["id"]),
    )
    con.commit()
    if cur.rowcount != 1:
        return False
    try:
        text, model = complete_role(role, SYSTEM_JUDGE, _judge_user(normalized, profile, image), paths)
        if _should_stop(con, job["id"], ver["id"], epoch):
            return False
        data = parse_model_json(text)
        status = parse_judge_status(data)
        if not status:
            raise ModelError("parse")
        data["status"] = status
        if not isinstance(data.get("issues"), list):
            data["issues"] = []
    except ModelError as exc:
        if _should_stop(con, job["id"], ver["id"], epoch):
            return False
        _set_version(con, ver["id"], "JUDGE_ERROR", _safe_text(exc), judge_model=model)
        return True
    except Exception as exc:
        if _should_stop(con, job["id"], ver["id"], epoch):
            return False
        _set_version(con, ver["id"], "JUDGE_ERROR", _safe_text(exc) or "审核没有完成", judge_model=model)
        return True
    if _should_stop(con, job["id"], ver["id"], epoch):
        return False
    hidden = 1 if status in HIDDEN_JUDGE else 0
    cur = con.execute(
        """UPDATE ai_versions
           SET status=?, judge_status=?, judge_json=?, judge_model=?, error=NULL, excluded=?
           WHERE id=? AND status!='CANCELLED'""",
        (status, status, json.dumps(data, ensure_ascii=False), model, hidden, ver["id"]),
    )
    con.commit()
    if cur.rowcount != 1:
        return False
    return True


def _claim(con):
    con.execute("BEGIN IMMEDIATE")
    try:
        rows = con.execute(
            "SELECT * FROM ai_jobs WHERE status='queued' ORDER BY id"
        ).fetchall()
        chosen = None
        with _run_lock:
            running = set(_running_bases)
        cancelled = _cancelled_now()
        if len(running) >= 10:
            con.commit()
            return None
        for row in rows:
            if int(row["id"]) in cancelled:
                continue
            ver = con.execute(
                "SELECT status FROM ai_versions WHERE id=?",
                (row["version_id"],),
            ).fetchone()
            if not ver or ver["status"] == "CANCELLED":
                continue
            base = row["base_question_id"]
            if base in running:
                continue
            busy = con.execute(
                """SELECT 1 FROM ai_jobs
                   WHERE base_question_id=? AND status='running' AND id!=?""",
                (base, row["id"]),
            ).fetchone()
            if busy:
                continue
            chosen = row
            break
        if not chosen:
            con.commit()
            return None
        phase = chosen["phase"] or "generate"
        cur = con.execute(
            """UPDATE ai_jobs
               SET status='running', phase=?, attempts=COALESCE(attempts,0)+1
               WHERE id=? AND status='queued'""",
            (phase, chosen["id"]),
        )
        if cur.rowcount != 1:
            con.commit()
            return None
        if phase == "judge":
            con.execute(
                "UPDATE ai_versions SET status='JUDGING' WHERE id=? AND status!='CANCELLED'",
                (chosen["version_id"],),
            )
        else:
            con.execute(
                """UPDATE ai_versions SET status='GENERATING'
                   WHERE id=? AND status IN ('QUEUED','GENERATION_ERROR')""",
                (chosen["version_id"],),
            )
        con.commit()
    except Exception:
        try:
            con.rollback()
        except Exception:
            pass
        raise
    job = con.execute("SELECT * FROM ai_jobs WHERE id=?", (chosen["id"],)).fetchone()
    if not job or job["status"] != "running" or int(job["id"]) in _cancelled_now():
        if job and job["status"] == "running":
            con.execute(
                "UPDATE ai_jobs SET status='cancelled' WHERE id=? AND status='running'",
                (job["id"],),
            )
            con.commit()
        return None
    ver = con.execute(
        "SELECT status FROM ai_versions WHERE id=?", (job["version_id"],)
    ).fetchone()
    if not ver or ver["status"] == "CANCELLED":
        con.execute(
            "UPDATE ai_jobs SET status='cancelled' WHERE id=? AND status='running'",
            (job["id"],),
        )
        con.commit()
        return None
    return job


def _run_job(job_id, base_id):
    con = None
    try:
        con = _connect()
        _process(con, job_id)
    except Exception as exc:
        if con is not None:
            try:
                with _cancel_lock:
                    start = _job_epochs.get(int(job_id))
                job = con.execute("SELECT * FROM ai_jobs WHERE id=?", (job_id,)).fetchone()
                if job and _should_stop(con, job_id, job["version_id"], start):
                    if job["status"] in ("queued", "running"):
                        con.execute(
                            "UPDATE ai_jobs SET status='cancelled' WHERE id=? AND status IN ('queued','running')",
                            (job_id,),
                        )
                        con.commit()
                elif job:
                    ver = con.execute(
                        "SELECT status FROM ai_versions WHERE id=?",
                        (job["version_id"],),
                    ).fetchone()
                    if ver and ver["status"] == "CANCELLED":
                        pass
                    elif job["phase"] == "judge" or (ver and ver["status"] in ("JUDGING", "GENERATED", "JUDGE_ERROR")):
                        if ver and ver["status"] not in JUDGE_OK:
                            _set_version(con, job["version_id"], "JUDGE_ERROR", _safe_text(exc) or "审核没有完成")
                    elif not ver or ver["status"] not in JUDGE_OK + ("JUDGE_ERROR", "CANCELLED"):
                        _set_version(con, job["version_id"], "GENERATION_ERROR", _safe_text(exc) or "生成没有完成")
                    _finish_job(con, job_id, "done")
            except Exception:
                pass
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:
                pass
        with _cancel_lock:
            _job_epochs.pop(int(job_id), None)
        with _run_lock:
            _running_bases.discard(base_id)


def _connect():
    con = sqlite3.connect(banklib.DB_PATH, timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=30000")
    return con


def _loop():
    con = _connect()
    try:
        ensure_schema(con)
    except Exception:
        pass
    while True:
        with _run_lock:
            full = len(_running_bases) >= 10
        if full:
            time.sleep(0.3)
            continue
        try:
            job = _claim(con)
        except sqlite3.OperationalError:
            time.sleep(0.4)
            continue
        except Exception:
            time.sleep(0.4)
            continue
        if not job:
            time.sleep(0.4)
            continue
        base_id = job["base_question_id"]
        with _run_lock:
            if base_id in _running_bases or len(_running_bases) >= 10:
                # Put it back if we lost the race with the in-memory set.
                try:
                    if int(job["id"]) not in _cancelled_now():
                        con.execute(
                            "UPDATE ai_jobs SET status='queued' WHERE id=? AND status='running'",
                            (job["id"],),
                        )
                        con.commit()
                except Exception:
                    pass
                time.sleep(0.2)
                continue
            _running_bases.add(base_id)
        threading.Thread(
            target=_run_job, args=(job["id"], base_id), name="ai-variant-job", daemon=True
        ).start()


def start_worker():
    """Start the in-process worker once. Reads API keys and model ids from the environment."""
    if os.environ.get("CHEM_DISABLE_AI") == "1":
        return
    global _started
    with _start_lock:
        if _started:
            return
        _load_provider_config()
        _started = True
        threading.Thread(target=_loop, name="ai-variant-worker", daemon=True).start()
