"""
RAG 召回率评测集 — 长隆欢乐世界知识库

评测逻辑：
  对每条测试用例，用 query 调用 RAG 检索，检查返回的文档片段中
  是否包含 expected_keywords 中的关键词。

指标：
  - 单条召回率 = 命中关键词数 / 期望关键词数
  - 整体召回率 = 所有用例召回率的平均值

运行方式：
  python tests/eval_rag_recall.py
"""
import sys
import os
import json
import asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.rag_tool import get_rag_instance

# ─── 评测数据集 ───────────────────────────────────────────────────────────────

EVAL_DATASET = [
    # === 基本信息类 ===
    {
        "id": "basic_01",
        "query": "长隆欢乐世界什么时候开业的？",
        "expected_keywords": ["2006", "4月7日"],
        "category": "基本信息",
    },
    {
        "id": "basic_02",
        "query": "长隆欢乐世界的营业时间是什么？",
        "expected_keywords": ["10:00", "20:00"],
        "category": "基本信息",
    },
    {
        "id": "basic_03",
        "query": "长隆欢乐世界客服电话多少？",
        "expected_keywords": ["400-883-0083"],
        "category": "基本信息",
    },
    {
        "id": "basic_04",
        "query": "长隆欢乐世界有哪些设施暂停开放？",
        "expected_keywords": ["龙卷风暴", "桑巴气球", "空中警察", "欢乐摩天轮"],
        "category": "基本信息",
    },

    # === 区域信息类 ===
    {
        "id": "area_01",
        "query": "长隆欢乐世界有哪些主题区域？",
        "expected_keywords": ["欢乐嘉年华", "朋克奇境", "尖叫地带", "律动天地", "丛林探险"],
        "category": "区域信息",
    },
    {
        "id": "area_02",
        "query": "朋克奇境是什么风格的区域？",
        "expected_keywords": ["机械朋克", "霓虹赛博", "潮流", "夜间"],
        "category": "区域信息",
    },
    {
        "id": "area_03",
        "query": "尖叫地带有什么特点？",
        "expected_keywords": ["刺激", "十环过山车", "摩托火箭过山车", "翻转"],
        "category": "区域信息",
    },
    {
        "id": "area_04",
        "query": "丛林探险区域有什么特色？",
        "expected_keywords": ["热带雨林", "超级激流", "21.5", "水上"],
        "category": "区域信息",
    },
    {
        "id": "area_05",
        "query": "律动天地在哪个位置？有什么王牌项目？",
        "expected_keywords": ["南门", "垂直过山车", "自由落体", "超级大摆锤"],
        "category": "区域信息",
    },

    # === 项目推荐类（亲子/低龄） ===
    {
        "id": "family_01",
        "query": "带小孩去长隆欢乐世界玩什么比较合适？",
        "expected_keywords": ["迷你飞行器", "奇妙车队", "蹦跳车", "梦幻转马"],
        "category": "亲子推荐",
    },
    {
        "id": "family_02",
        "query": "有什么不刺激的亲子项目？",
        "expected_keywords": ["滑翔飞翼", "欢乐跳跳", "摇摆屋"],
        "category": "亲子推荐",
    },
    {
        "id": "family_03",
        "query": "飞马家庭过山车适合小孩吗？",
        "expected_keywords": ["全年龄段", "温柔", "平缓", "无陡坡骤降"],
        "category": "亲子推荐",
    },

    # === 项目推荐类（刺激/年轻人） ===
    {
        "id": "thrill_01",
        "query": "长隆欢乐世界最刺激的项目是什么？",
        "expected_keywords": ["垂直过山车", "超级大摆锤", "自由落体"],
        "category": "刺激项目",
    },
    {
        "id": "thrill_02",
        "query": "弹射过山车好玩吗？有什么特点？",
        "expected_keywords": ["弹射", "零延迟", "螺旋弯道", "减震"],
        "category": "刺激项目",
    },
    {
        "id": "thrill_03",
        "query": "火箭过山车是什么体验？",
        "expected_keywords": ["火箭", "弹射冲刺", "直线加速", "回旋弯道"],
        "category": "刺激项目",
    },
    {
        "id": "thrill_04",
        "query": "超级大摆锤有多高？刺激吗？",
        "expected_keywords": ["360", "旋转", "失重", "高速自转"],
        "category": "刺激项目",
    },

    # === 具体项目详情类 ===
    {
        "id": "detail_01",
        "query": "自由落体有多高？",
        "expected_keywords": ["65", "米", "金箍棒"],
        "category": "项目详情",
    },
    {
        "id": "detail_02",
        "query": "四维影院有什么特效？",
        "expected_keywords": ["4D", "吹风", "震动", "喷水", "座椅晃动"],
        "category": "项目详情",
    },
    {
        "id": "detail_03",
        "query": "星际决战是什么类型的项目？",
        "expected_keywords": ["射击", "激光武器", "轨道战车", "组队"],
        "category": "项目详情",
    },
    {
        "id": "detail_04",
        "query": "梦回兰若是什么项目？",
        "expected_keywords": ["古风", "光影", "国风", "平缓漫步"],
        "category": "项目详情",
    },
    {
        "id": "detail_05",
        "query": "超级激流会不会湿身？",
        "expected_keywords": ["水上", "俯冲", "水花", "雨衣"],
        "category": "项目详情",
    },
    {
        "id": "detail_06",
        "query": "碰碰车怎么样？",
        "expected_keywords": ["碰碰车", "防撞", "碰撞", "转向"],
        "category": "项目详情",
    },

    # === 场景/体验类 ===
    {
        "id": "scene_01",
        "query": "长隆欢乐世界晚上哪里好玩？",
        "expected_keywords": ["朋克奇境", "夜晚", "灯光", "电音"],
        "category": "场景体验",
    },
    {
        "id": "scene_02",
        "query": "哪里适合拍照打卡？",
        "expected_keywords": ["梦幻转马", "垂直过山车", "露台", "出片"],
        "category": "场景体验",
    },
    {
        "id": "scene_03",
        "query": "夏天去长隆玩水有什么项目？",
        "expected_keywords": ["超级激流", "丛林探险", "水花", "解暑"],
        "category": "场景体验",
    },

    # === 安全/限制类 ===
    {
        "id": "safety_01",
        "query": "哪些人不能玩尖叫地带的项目？",
        "expected_keywords": ["身高", "健康限制", "高血压", "孕期"],
        "category": "安全须知",
    },
]


def evaluate_recall(results_text: str, expected_keywords: list[str]) -> dict:
    """评估单条查询的召回率"""
    hits = []
    misses = []
    for kw in expected_keywords:
        if kw in results_text:
            hits.append(kw)
        else:
            misses.append(kw)
    recall = len(hits) / len(expected_keywords) if expected_keywords else 0
    return {
        "recall": recall,
        "hits": hits,
        "misses": misses,
    }


def run_evaluation():
    """运行完整评测"""
    rag = get_rag_instance()

    if not rag.vector_store:
        print("错误：向量数据库未初始化，请先运行 build_knowledge_base()")
        sys.exit(1)

    retriever = rag.vector_store.as_retriever(
        search_type="similarity",
        search_kwargs={"k": 3}
    )

    results = []
    category_scores = {}

    print(f"\n{'='*60}")
    print(f"  RAG 召回率评测 — 长隆欢乐世界知识库")
    print(f"  评测用例数: {len(EVAL_DATASET)}")
    print(f"  检索 top_k: 3")
    print(f"{'='*60}\n")

    for case in EVAL_DATASET:
        docs = retriever.invoke(case["query"])
        combined_text = "\n".join(doc.page_content for doc in docs)

        eval_result = evaluate_recall(combined_text, case["expected_keywords"])
        eval_result["id"] = case["id"]
        eval_result["query"] = case["query"]
        eval_result["category"] = case["category"]
        results.append(eval_result)

        # 按类别汇总
        cat = case["category"]
        if cat not in category_scores:
            category_scores[cat] = []
        category_scores[cat].append(eval_result["recall"])

        # 打印单条结果
        status = "PASS" if eval_result["recall"] >= 0.5 else "FAIL"
        print(f"  [{status}] {case['id']}: recall={eval_result['recall']:.0%}")
        if eval_result["misses"]:
            print(f"         未召回: {eval_result['misses']}")

    # 汇总报告
    overall_recall = sum(r["recall"] for r in results) / len(results)

    print(f"\n{'─'*60}")
    print(f"  分类召回率:")
    print(f"{'─'*60}")
    for cat, scores in category_scores.items():
        avg = sum(scores) / len(scores)
        print(f"  {cat:12s}  {avg:.0%}  ({len(scores)} 条)")

    print(f"\n{'─'*60}")
    print(f"  整体召回率: {overall_recall:.1%}")
    print(f"  PASS (>=50%): {sum(1 for r in results if r['recall'] >= 0.5)}/{len(results)}")
    print(f"  FAIL (<50%):  {sum(1 for r in results if r['recall'] < 0.5)}/{len(results)}")
    print(f"{'─'*60}\n")

    # 输出 JSON 详细结果
    output_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "tests", "eval_rag_results.json"
    )
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump({
            "overall_recall": round(overall_recall, 4),
            "category_scores": {k: round(sum(v)/len(v), 4) for k, v in category_scores.items()},
            "details": results,
        }, f, ensure_ascii=False, indent=2)
    print(f"  详细结果已保存: {output_path}")

    return overall_recall


if __name__ == "__main__":
    run_evaluation()
