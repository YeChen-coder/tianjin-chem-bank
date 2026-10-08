# -*- coding: utf-8 -*-
"""Offline tests for AI variants. Never opens the live bank and never calls the network."""
import hashlib
import json
import os
import sqlite3
import tempfile

import banklib
import aivariant


def _q(con, body):
    key = hashlib.sha256(body.encode("utf-8")).hexdigest()
    segs = [[{"t": "text", "s": body}]]
    cur = con.execute(
        """INSERT INTO questions
           (dedup_key, qnum, body, answer, segments, major, minor, image_count, qtype)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (key, "", body, "A", json.dumps(segs, ensure_ascii=False), "未分类", "未分类", 0, "单选题"),
    )
    con.commit()
    return cur.lastrowid


def main():
    live = banklib.DB_PATH
    tmp = tempfile.mkdtemp(prefix="chem-ai-test-")
    path = os.path.join(tmp, "bank.sqlite")
    banklib.DB_PATH = path
    try:
        con = banklib.init_db()
        assert os.path.abspath(path) != os.path.abspath(live)
        ids = [_q(con, "母题 %d 氯化钠溶于水" % i) for i in (1, 2, 3)]
        paper, err = banklib.apply_paper(con, {"name": "练习", "ids": ids})
        assert err is None and paper and len(paper["ids"]) == 3

        first, err = aivariant.enqueue_paper(con, paper["id"], "medium", None)
        assert err is None, err
        assert len(first["jobs"]) == 3
        assert len({j["id"] for j in first["jobs"]}) == 3
        assert all(j["duplicate"] is False for j in first["jobs"])
        assert con.execute("SELECT COUNT(*) FROM ai_jobs").fetchone()[0] == 3
        assert con.execute("SELECT COUNT(*) FROM ai_versions").fetchone()[0] == 3

        second, err = aivariant.enqueue_paper(con, paper["id"], "light", ids)
        assert err is None, err
        assert len(second["jobs"]) == 3
        assert all(j["duplicate"] is True for j in second["jobs"])
        assert con.execute("SELECT COUNT(*) FROM ai_jobs").fetchone()[0] == 3
        assert con.execute("SELECT COUNT(*) FROM ai_versions").fetchone()[0] == 3

        job = first["jobs"][0]
        v1 = con.execute(
            "SELECT seq, parent_version_id, status FROM ai_versions WHERE id=?",
            (job["version_id"],),
        ).fetchone()
        assert v1["seq"] == 1 and v1["parent_version_id"] is None
        con.execute("UPDATE ai_jobs SET status='done' WHERE id=?", (job["id"],))
        con.execute(
            "UPDATE ai_versions SET status='PASS', candidate_json=? WHERE id=?",
            (json.dumps({"stem": "变式", "answer": "B"}, ensure_ascii=False), job["version_id"]),
        )
        con.commit()
        child, err = aivariant.enqueue_base(
            con, paper["id"], job["base_question_id"], job["version_id"],
            "把质量改成 20 g", "deep", "branch",
        )
        assert err is None, err
        assert child["duplicate"] is False
        v2 = con.execute(
            "SELECT seq, parent_version_id FROM ai_versions WHERE id=?",
            (child["version_id"],),
        ).fetchone()
        assert v2["seq"] == 2, v2["seq"]
        assert v2["parent_version_id"] == job["version_id"]

        # retry of a failed slot keeps the same seq
        bad_id = con.execute(
            "SELECT id, seq FROM ai_versions WHERE base_question_id=? AND id!=?",
            (ids[1], job["version_id"]),
        ).fetchone()
        # ids[1] still has only its queued V1
        v = con.execute(
            "SELECT id, seq FROM ai_versions WHERE base_question_id=?",
            (ids[1],),
        ).fetchone()
        con.execute("UPDATE ai_jobs SET status='done' WHERE version_id=?", (v["id"],))
        con.execute(
            "UPDATE ai_versions SET status='GENERATION_ERROR' WHERE id=?",
            (v["id"],),
        )
        con.commit()
        before = con.execute("SELECT COUNT(*) FROM ai_versions").fetchone()[0]
        retried, err = aivariant.retry_version(con, v["id"])
        assert err is None, err
        assert retried["version_id"] == v["id"]
        again = con.execute("SELECT seq, status FROM ai_versions WHERE id=?", (v["id"],)).fetchone()
        assert again["seq"] == v["seq"] == 1
        assert again["status"] == "QUEUED"
        assert con.execute("SELECT COUNT(*) FROM ai_versions").fetchone()[0] == before

        hidden = con.execute(
            """INSERT INTO questions
               (dedup_key, qnum, body, answer, segments, major, minor, image_count, qtype,
                origin, base_question_id, in_bank)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "ai-hidden-key", "", "草稿不应出现在题库搜索", "C",
                json.dumps([[{"t": "text", "s": "草稿不应出现在题库搜索"}]], ensure_ascii=False),
                "未分类", "未分类", 0, "填空题", "ai", ids[0], 0,
            ),
        ).lastrowid
        con.commit()
        clause = banklib.questions_visible_clause()
        assert "in_bank" in clause
        found = [r[0] for r in con.execute("SELECT id FROM questions WHERE " + clause)]
        assert hidden not in found
        assert ids[0] in found
        by_id = con.execute("SELECT id FROM questions WHERE id=?", (hidden,)).fetchone()
        assert by_id is not None
        src = open(os.path.join(os.path.dirname(__file__), "app.py"), encoding="utf-8").read()
        assert "questions_visible_clause()" in src

        fenced = aivariant.parse_model_json(
            "说明文字\n```json\n{\"status\": \"SUSPECT\", \"issues\": []}\n```\n"
        )
        assert fenced["status"] == "SUSPECT"
        assert aivariant.parse_judge_status({"status": "pass"}) == "PASS"
        assert aivariant.parse_judge_status(fenced) == "SUSPECT"
        assert aivariant.parse_judge_status("```json\n{\"status\":\"FAIL\"}\n```") == "FAIL"
        assert aivariant.parse_judge_status({"status": "MAYBE"}) is None
        assert aivariant.parse_judge_status("不是 JSON") is None

        # human rows stay searchable by default
        origin = con.execute("SELECT origin, in_bank FROM questions WHERE id=?", (ids[0],)).fetchone()
        assert origin["origin"] == "human" and int(origin["in_bank"]) == 1

        # Provider routing stays offline: GLM first, one DeepSeek fallback, no sockets.
        saved = (
            aivariant._glm_key, aivariant._ds_key,
            aivariant._glm_flash, aivariant._glm_pro, aivariant._glm_flashx,
            aivariant._ds_flash, aivariant._ds_pro,
            aivariant._glm_chat, aivariant._deepseek_chat,
        )
        try:
            aivariant._glm_key = "not-a-real-key"
            aivariant._ds_key = "not-a-real-key"
            aivariant._glm_flash = "glm-5.3-flash"
            aivariant._glm_flashx = ""
            aivariant._glm_pro = "glm-5.3"
            aivariant._ds_flash = "deepseek-flash"
            aivariant._ds_pro = "deepseek-v4-pro"
            calls = []

            def glm_bad(model, system, user, image_paths=None):
                calls.append(("glm", model, image_paths))
                raise aivariant.ModelError("http")

            def ds_ok(model, system, user, image_paths=None):
                calls.append(("ds", model, image_paths))
                return '{"status":"PASS"}'

            aivariant._glm_chat = glm_bad
            aivariant._deepseek_chat = ds_ok
            text, model = aivariant.complete_role("flash", "sys", "user", ["/tmp/pic.png"])
            assert model == "deepseek-flash", model
            assert calls == [
                ("glm", "glm-5.3-flash", ["/tmp/pic.png"]),
                ("ds", "deepseek-flash", ["/tmp/pic.png"]),
            ]

            calls.clear()

            def glm_junk(model, system, user, image_paths=None):
                calls.append(("glm", model, image_paths))
                return "not json"

            aivariant._glm_chat = glm_junk
            text, model = aivariant.complete_role("pro", "sys", "user", ["/tmp/pic.png"])
            assert model == "deepseek-v4-pro", model
            assert calls[0] == ("glm", "glm-5.3", None)
            assert calls[1] == ("ds", "deepseek-v4-pro", None)

            calls.clear()

            def glm_ok(model, system, user, image_paths=None):
                calls.append(("glm", model, image_paths))
                return '{"ok": 1}'

            aivariant._glm_chat = glm_ok
            text, model = aivariant.complete_role("flash", "sys", "user", None)
            assert model == "glm-5.3-flash" and calls == [("glm", "glm-5.3-flash", None)]

            rich, plain = aivariant._glm_bodies("glm-5.3-flash", "s", "u", None)
            assert rich["stream"] is False
            assert rich["temperature"] == 1 and rich["top_p"] == 0.95
            assert rich["thinking"]["type"] == "enabled"
            assert plain["thinking"]["type"] == "enabled"
            assert "response_format" not in plain
            assert "disabled" not in json.dumps(rich["thinking"])
            text_body, _plain_pro = aivariant._glm_bodies("glm-5.3", "s", "u", ["/tmp/x.png"])
            assert text_body["model"] == "glm-5.3"
            assert isinstance(text_body["messages"][1]["content"], str)
            assert text_body["thinking"]["type"] == "enabled"

            assert aivariant._only_keepalive(b": keep-alive\n\n")
            assert aivariant._json_payload(b": keep-alive\n\n") is None
            got = aivariant._json_payload(b': keep-alive\n{"choices":[{"message":{"content":"pong"}}]}')
            assert got["choices"][0]["message"]["content"] == "pong"

            aivariant._glm_key = ""
            aivariant._ds_key = ""
            try:
                aivariant.complete_role("flash", "s", "u", None)
                raise AssertionError("missing keys should fail")
            except aivariant.ModelError as exc:
                assert exc.kind == "config"
        finally:
            (
                aivariant._glm_key, aivariant._ds_key,
                aivariant._glm_flash, aivariant._glm_pro, aivariant._glm_flashx,
                aivariant._ds_flash, aivariant._ds_pro,
                aivariant._glm_chat, aivariant._deepseek_chat,
            ) = saved

        def _mark_judged(con, version_id, status, stem):
            con.execute(
                """UPDATE ai_jobs SET status='done'
                   WHERE version_id=? AND status IN ('queued','running')""",
                (version_id,),
            )
            con.execute(
                """UPDATE ai_versions
                   SET status=?, judge_status=?, judge_json=?, candidate_json=?, excluded=?
                   WHERE id=?""",
                (
                    status,
                    status,
                    json.dumps({"status": status, "issues": []}, ensure_ascii=False),
                    json.dumps({
                        "stem": stem,
                        "answer": "A" if status == "PASS" else "不应展示",
                    }, ensure_ascii=False),
                    0 if status == "PASS" else 1,
                    version_id,
                ),
            )
            con.commit()

        # One SUSPECT retries from the good parent, not from the hidden version.
        chain_base = _q(con, "母题 链式 氧气制备")
        paper_chain, err = banklib.apply_paper(con, {"name": "链式", "ids": [chain_base]})
        assert err is None and paper_chain
        good_job, err = aivariant.enqueue_base(
            con, paper_chain["id"], chain_base, None, "", "medium", "another",
        )
        assert err is None and good_job["duplicate"] is False
        _mark_judged(con, good_job["version_id"], "PASS", "好的变式")
        suspect_job, err = aivariant.enqueue_base(
            con, paper_chain["id"], chain_base, good_job["version_id"],
            "把质量改成 20 g", "light", "branch",
        )
        assert err is None, err
        _mark_judged(con, suspect_job["version_id"], "SUSPECT", "SUSPECT_STEM_SHOULD_HIDE")
        retried = aivariant.after_hidden_judge(con, suspect_job["version_id"])
        assert retried and retried["retried"] is True, retried
        child_row = con.execute(
            "SELECT parent_version_id, status FROM ai_versions WHERE id=?",
            (retried["job"]["version_id"],),
        ).fetchone()
        assert child_row["parent_version_id"] == good_job["version_id"]
        assert child_row["parent_version_id"] != suspect_job["version_id"]
        hidden_row = con.execute(
            "SELECT status, excluded, judge_json, candidate_json FROM ai_versions WHERE id=?",
            (suspect_job["version_id"],),
        ).fetchone()
        assert hidden_row["status"] == "SUSPECT"
        assert int(hidden_row["excluded"]) == 1
        assert hidden_row["judge_json"] and "SUSPECT_STEM_SHOULD_HIDE" in hidden_row["candidate_json"]
        mid = aivariant.paper_status(con, paper_chain["id"])
        mid_base = [b for b in mid["bases"] if b["base_question_id"] == chain_base][0]
        mid_blob = json.dumps(mid_base, ensure_ascii=False)
        assert "SUSPECT_STEM_SHOULD_HIDE" not in mid_blob
        assert "不应展示" not in mid_blob
        assert any(v["status"] == "PASS" and v["candidate"]["stem"] == "好的变式" for v in mid_base["versions"])
        assert all(v["status"] == "PASS" for v in mid_base["versions"])
        assert any(n["message"] == "有一版未通过，正在重新生成" for n in mid_base["notices"])
        assert all(n["message"] != "该题多次尝试但结果存疑，请自行更改" for n in mid_base["notices"])

        # An excluded version is not chosen as parent; the prompt parent is the visible ancestor.
        con.execute("UPDATE ai_jobs SET status='done' WHERE id=?", (retried["job"]["id"],))
        con.execute(
            """UPDATE ai_versions
               SET status='PASS', excluded=1, candidate_json=?
               WHERE id=?""",
            (json.dumps({"stem": "已删除的V", "answer": "B"}, ensure_ascii=False), retried["job"]["version_id"]),
        )
        con.commit()
        assert aivariant._load_parent_candidate(con, retried["job"]["version_id"])["stem"] == "好的变式"
        branched, err = aivariant.enqueue_base(
            con, paper_chain["id"], chain_base, retried["job"]["version_id"],
            "再改", "medium", "branch",
        )
        assert err is None, err
        chosen = con.execute(
            "SELECT parent_version_id FROM ai_versions WHERE id=?",
            (branched["version_id"],),
        ).fetchone()
        assert chosen["parent_version_id"] == good_job["version_id"]
        assert chosen["parent_version_id"] != retried["job"]["version_id"]

        # Three consecutive non-PASS results stay hidden. The third is not shown.
        fail_base = _q(con, "母题 三次未通过")
        paper_fail, err = banklib.apply_paper(con, {"name": "三次", "ids": [fail_base]})
        assert err is None and paper_fail
        current, err = aivariant.enqueue_base(
            con, paper_fail["id"], fail_base, None, "", "medium", "another",
        )
        assert err is None, err
        stems = []
        for i in range(3):
            stem = "FAILSTEM_%d_DO_NOT_SHOW" % (i + 1)
            stems.append(stem)
            _mark_judged(con, current["version_id"], "SUSPECT", stem)
            outcome = aivariant.after_hidden_judge(con, current["version_id"])
            assert outcome, outcome
            if i < 2:
                assert outcome["retried"] is True, outcome
                assert outcome["streak"] == i + 1
                parent = con.execute(
                    "SELECT parent_version_id FROM ai_versions WHERE id=?",
                    (outcome["job"]["version_id"],),
                ).fetchone()
                assert parent["parent_version_id"] is None
                current = outcome["job"]
            else:
                assert outcome["stopped"] is True, outcome
                assert outcome["retried"] is False
                assert outcome["streak"] == 3
        kept = con.execute(
            "SELECT status, judge_json, candidate_json, excluded FROM ai_versions WHERE base_question_id=? ORDER BY seq",
            (fail_base,),
        ).fetchall()
        assert len(kept) == 3
        assert all(r["status"] == "SUSPECT" and r["judge_json"] and r["candidate_json"] and int(r["excluded"]) == 1 for r in kept)
        assert con.execute(
            "SELECT COUNT(*) FROM ai_jobs WHERE base_question_id=? AND status IN ('queued','running')",
            (fail_base,),
        ).fetchone()[0] == 0
        shown = aivariant.paper_status(con, paper_fail["id"])
        fail_view = shown["bases"][0]
        blob = json.dumps(fail_view, ensure_ascii=False)
        assert fail_view["versions"] == []
        for stem in stems:
            assert stem not in blob
        assert "不应展示" not in blob
        assert [n["message"] for n in fail_view["notices"]] == ["该题多次尝试但结果存疑，请自行更改"]
        assert "该题多次尝试但结果存疑，请自行更改" in open(
            os.path.join(os.path.dirname(__file__), "app.py"), encoding="utf-8"
        ).read()

        print("ok")
    finally:
        banklib.DB_PATH = live
        con.close()


if __name__ == "__main__":
    main()
