import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.rag.citydata_loader import load_citydata_documents


def _write_city_csv(path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames = [
        "名字",
        "链接",
        "地址",
        "介绍",
        "开放时间",
        "图片链接",
        "评分",
        "建议游玩时间",
        "建议季节",
        "门票",
        "小贴士",
        "Page",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_load_citydata_documents_creates_one_document_per_complete_spot(tmp_path):
    _write_city_csv(
        tmp_path / "北京.csv",
        [
            {
                "名字": "故宫博物院The Palace Museum",
                "链接": "http://example.com/gugong",
                "地址": "北京市东城区景山前街4号",
                "介绍": "故宫又称紫禁城，是明清两代的皇宫。",
                "开放时间": "08:30-17:00",
                "图片链接": "",
                "评分": "4.8",
                "建议游玩时间": "半天到一天",
                "建议季节": "四季皆宜",
                "门票": "60元",
                "小贴士": "周一闭馆",
                "Page": "1",
            },
            {
                "名字": "空介绍景点",
                "链接": "",
                "地址": "",
                "介绍": "",
                "开放时间": "",
                "图片链接": "",
                "评分": "",
                "建议游玩时间": "",
                "建议季节": "",
                "门票": "",
                "小贴士": "",
                "Page": "1",
            },
        ],
    )

    docs = load_citydata_documents(tmp_path)

    assert len(docs) == 1
    doc = docs[0]
    assert "城市：北京" in doc.page_content
    assert "景点：故宫博物院The Palace Museum" in doc.page_content
    assert "介绍：故宫又称紫禁城，是明清两代的皇宫。" in doc.page_content
    assert doc.metadata["source_city"] == "北京"
    assert doc.metadata["spot_name"] == "故宫博物院The Palace Museum"
    assert doc.metadata["spot_name_norm"] == "故宫博物院thepalacemuseum"
    assert doc.metadata["type"] == "attraction"
    assert doc.metadata["rating"] == "4.8"
    assert doc.metadata["url"] == "http://example.com/gugong"
