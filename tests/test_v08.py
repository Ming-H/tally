"""v0.8 回归钉：D27 章节路由 + D28 归因通道。

设计文档：docs/DESIGN-v0.8-structure-routing.md（实测动机见 §一）。
全部确定性（heuristic 模式，conftest 剥 key 封网）。
"""

from __future__ import annotations

import re

import pytest

from tally.schemas import new_state

AR_DOC_ID = "甲电子_2025FY"


def _annual_report():
    """合成年报：前置章节污染可复现（页码结构与巨潮年报实测一致）。

    p1 封面 / p2 重要提示 / p3 目录（含'管理层讨论与分析'关键词——
    v0.7 行级打分无法区分目录与正文的实证根因）/ p4 备查 /
    p7 主要会计数据 / p11-12 MD&A。
    """
    from tally.ingest import Doc
    from tally.schemas import Chunk

    pages: dict[int, list[str]] = {
        1: ["甲电子股份有限公司 2025 年年度报告 证券代码：000000 证券简称：甲电子"],
        2: ["第一节 重要提示 本公司董事会及全体董事保证本报告内容的真实、准确、完整，"
            "不存在虚假记载。"],
        3: ["目 录 第一节 重要提示、目录和释义 .............. - 2 - "
            "第二节 公司简介和主要财务指标 .................. - 7 - "
            "第三节 管理层讨论与分析 ........................ - 11 - "
            "第四节 公司治理 ................................ - 45 -"],
        4: ["三、报告期内公开披露过的所有公司文件的正本及公告的原稿；备查文件。"],
        7: ["二、主要会计数据 营业收入 5,000,000,000.00 元 同比增长 30% "
            "归属于上市公司股东的净利润 800,000,000.00 元 同比增长 25%"],
        8: [  # 季度数据表：指标词+数字密度远高于 MD&A 叙述句（真库同构噪声）
            "季度 主要会计数据 第一季度 第二季度 第三季度 第四季度",
            "的净利润 158,287,612.86 241,223,925.60 313,681,705.20 366,532,186.37",
            "归属于上市公司股东的净利润 156,838,528.71 240,699,630.76 310,831,811.78 362,635,353.38",
            "归属于上市公司股东的扣除非经常性损益的净利润 150,000,000.00 235,000,000.00 305,000,000.00 356,000,000.00",
        ],
        11: ["第三节 管理层讨论与分析 一、经营情况讨论与分析 "
             "2025 年公司实现营业收入 50 亿元，同比增长 30%，"
             "主要系高端产品放量所致。",
             # 真库同构：归因句不含指标词字面（说"提升所致"不说"净利润"），
             # 纯词面检索下会被 p8 季度表的指标词密度挤出 top-8
             "报告期内公司盈利能力显著改善，同比增长 25%，"
             "主要由于毛利率提升所致。"],
        12: ["十一、公司未来发展的展望 预计 2026 年营业收入继续保持增长。"],
        86: ["注册会计师姓名 甲乙、丙丁 审计报告正文 我们审计了甲电子股份有限公司"
             "的财务报表，包括 2025 年 12 月 31 日的合并资产负债表以及财务报表附注。",
             "我们认为，后附的财务报表在所有重大方面按照企业会计准则的规定编制，"
             "公允反映了公司 2025 年度的经营成果和现金流量。"],
    }
    doc = Doc(doc_id=AR_DOC_ID, company="甲电子", period="2025FY",
              kind="年报", source="synthetic://annual-report")
    chs = []
    seq = 0
    for p in sorted(pages):
        for t in pages[p]:
            chs.append(Chunk(chunk_id=f"{AR_DOC_ID}#p{p}#{seq}", doc_id=AR_DOC_ID,
                             page=p, seq=seq, text=t))
            seq += 1
    return doc, chs


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    """samples 全集 + 合成年报（D28 需真实季报落账，D27 需带目录年报）。"""
    from pathlib import Path

    from tally.index import Corpus
    from tally.ingest import ingest_file, parse_filename
    from tally.store import Store

    db = tmp_path_factory.mktemp("dz8") / "t.db"
    store = Store(db)
    samples = Path(__file__).resolve().parents[1] / "samples"
    from tally import source as _src

    for f in sorted(samples.glob("*.pdf")) + sorted(samples.glob("*.md")):
        try:
            doc, _chs = parse_filename(f) and ingest_file(f)
        except ValueError:
            continue
        store.add_doc(doc, _chs)
        # cli 摄取路径会写 manifest（指纹）；不写则 L1 读探针永远
        # stale（current_fingerprints 为空）——v0.8 台账读命中测试
        # 首次暴露的测试基建缺口
        store.set_manifest(str(f), doc.doc_id, _src._fingerprint(f))
    ar_doc, ar_chs = _annual_report()
    store.add_doc(ar_doc, ar_chs)
    store.set_manifest(ar_doc.source, ar_doc.doc_id, "sha256:synthetic-ar-v08")
    store.close()
    return Corpus.load(db)


@pytest.fixture(scope="module")
def app(corpus):
    from tally.graph import build_graph

    return build_graph(corpus)


# ── D27：章节路由 ────────────────────────────────────────


class TestDocSections:
    def test_parses_toc_into_ordered_sections(self, corpus):
        from tally.graph import doc_sections

        secs = doc_sections(corpus, AR_DOC_ID)
        assert secs, "带目录年报必须解析出章节映射"
        names = [n for n, _ in secs]
        assert any("管理层讨论与分析" in n for n in names)
        by_name = dict(secs)
        assert by_name.get("第三节 管理层讨论与分析") == 11

    def test_no_toc_degrades_to_none(self, corpus):
        from tally.graph import doc_sections

        assert doc_sections(corpus, "海伦哲_2026Q1") is None

    def test_mda_start_page(self, corpus):
        from tally.graph import mda_start

        assert mda_start(corpus, AR_DOC_ID) == 11
        assert mda_start(corpus, "海伦哲_2026Q1") is None


class TestSummarizeSectionFilter:
    def test_summary_bullets_exclude_front_matter(self, app):
        """「经营情况怎么样」要点不得引用目录/提示/备查页（p1-4），
        且必须带出 MD&A 叙述句——v0.7 实测此题被目录页开场。"""
        st = app.invoke(new_state("甲电子 2025 经营情况怎么样", "heuristic"))
        assert st["verdict"] == "pass", st.get("answer", "")[:200]
        pages = [int(p) for p in re.findall(r"第(\d+)页", st.get("answer", ""))]
        assert pages, "要点应带页内引用"
        assert all(p >= 7 for p in pages), f"前置章节页泄漏: {pages}"
        assert any(p >= 11 for p in pages), "MD&A 要点缺席"
        assert "实现营业收入" in st["answer"]
        assert any(t.startswith("sections:") for t in st.get("trace", []))

    def test_no_toc_doc_summary_still_works(self, app):
        """无目录文档（季报）保持 v0.7 行为：sections:none 诚实降级，不炸。"""
        st = app.invoke(new_state("海伦哲 2026 一季度经营情况怎么样", "heuristic"))
        joined = " ".join(st.get("trace", []))
        assert "sections:none" in joined
        assert st["verdict"] in ("pass", "refused")  # 降级不等于失败


# ── D28：归因通道 ────────────────────────────────────────


class TestAttributionGuard:
    @pytest.fixture(autouse=True)
    def _memory_on(self, monkeypatch):
        monkeypatch.delenv("TALLY_MEMORY", raising=False)

    def test_why_query_not_short_circuited(self, app, corpus):
        """归因问句不得被 L1 数字事实命中短路（why ≠ what）。
        先让 what 题落账（写闸真实路径），再问 why——须走全链路。"""
        st_what = app.invoke(new_state("海伦哲 2026 年一季度归母净利润多少", "heuristic"))
        assert st_what["verdict"] == "pass"

        st = app.invoke(new_state("海伦哲 2026 年一季度归母净利润为什么增长", "heuristic"))
        assert st.get("memory_hit") is not True, "归因问句被台账短路成数字复读"
        assert any("memory:skip" in (t or "") for t in st.get("trace", []))

    def test_what_query_still_hits_ledger(self, app, corpus):
        """守卫不矫枉过正：what 问句照常台账命中短路（公理 ② 维持）。"""
        app.invoke(new_state("海伦哲 2026 年一季度归母净利润多少", "heuristic"))
        st = app.invoke(new_state("海伦哲 2026 年一季度归母净利润多少", "heuristic"))
        assert st.get("memory_hit") is True

    def test_why_query_answer_carries_attribution(self, app):
        """归因问句的答案必须带出归因句（主要系/主要由于），不是数字表行。
        MD&A 定向检索（D28 配套）：词面检索下归因句会被指标表页挤出
        top-8（年报实测），复用 D27 章节映射定向捞回。"""
        st = app.invoke(new_state("甲电子 2025 年净利润为什么增长", "heuristic"))
        assert st["verdict"] == "pass", st.get("answer", "")[:150]
        assert ("主要系" in st["answer"]) or ("主要由于" in st["answer"]), \
            f"归因句缺席: {st['answer'][:200]}"
