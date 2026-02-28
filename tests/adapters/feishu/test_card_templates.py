from pathlib import Path
import sys
from datetime import date, timedelta


ROOT = Path(__file__).resolve().parents[3]
AGENT_HOST_ROOT = ROOT / "apps" / "agent-host"
sys.path.insert(0, str(AGENT_HOST_ROOT))

from src.adapters.channels.feishu.ui_cards.card_template_registry import CardTemplateRegistry
from src.adapters.channels.feishu.ui_cards.card_template_config import reset_template_config_cache


def _elements(payload: object) -> list[dict]:
    if isinstance(payload, dict):
        raw = payload.get("elements")
        return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _wrapper(payload: object) -> dict:
    if not isinstance(payload, dict):
        return {}
    raw = payload.get("wrapper")
    return raw if isinstance(raw, dict) else {}


def _markdown_text(payload: object) -> str:
    texts: list[str] = []

    def _collect(items: list[dict]) -> None:
        for item in items:
            tag = item.get("tag")
            if tag == "markdown":
                content = item.get("content")
                if isinstance(content, str):
                    texts.append(content)
                continue

            if tag == "column_set":
                columns_raw = item.get("columns")
                columns = columns_raw if isinstance(columns_raw, list) else []
                for column in columns:
                    if not isinstance(column, dict):
                        continue
                    column_elements_raw = column.get("elements")
                    column_elements = column_elements_raw if isinstance(column_elements_raw, list) else []
                    _collect([entry for entry in column_elements if isinstance(entry, dict)])

    _collect(_elements(payload))
    return "\n".join(texts)


def _button_texts(payload: object) -> list[str]:
    texts: list[str] = []

    def _collect(items: list[dict]) -> None:
        for item in items:
            tag = item.get("tag")
            if tag == "button":
                text_raw = item.get("text")
                text = text_raw if isinstance(text_raw, dict) else {}
                content = text.get("content")
                if isinstance(content, str) and content:
                    texts.append(content)
                continue

            if tag == "action":
                actions_raw = item.get("actions")
                actions = actions_raw if isinstance(actions_raw, list) else []
                _collect([entry for entry in actions if isinstance(entry, dict)])
                continue

            if tag == "column_set":
                columns_raw = item.get("columns")
                columns = columns_raw if isinstance(columns_raw, list) else []
                for column in columns:
                    if not isinstance(column, dict):
                        continue
                    column_elements_raw = column.get("elements")
                    column_elements = column_elements_raw if isinstance(column_elements_raw, list) else []
                    _collect([entry for entry in column_elements if isinstance(entry, dict)])

    _collect(_elements(payload))
    return texts


def _buttons(payload: object) -> list[dict]:
    buttons: list[dict] = []

    def _collect(items: list[dict]) -> None:
        for item in items:
            tag = item.get("tag")
            if tag == "button":
                buttons.append(item)
                continue

            if tag == "action":
                actions_raw = item.get("actions")
                actions = actions_raw if isinstance(actions_raw, list) else []
                _collect([entry for entry in actions if isinstance(entry, dict)])
                continue

            if tag == "column_set":
                columns_raw = item.get("columns")
                columns = columns_raw if isinstance(columns_raw, list) else []
                for column in columns:
                    if not isinstance(column, dict):
                        continue
                    column_elements_raw = column.get("elements")
                    column_elements = column_elements_raw if isinstance(column_elements_raw, list) else []
                    _collect([entry for entry in column_elements if isinstance(entry, dict)])

    _collect(_elements(payload))
    return buttons


def test_render_query_list_v1() -> None:
    registry = CardTemplateRegistry()

    elements = registry.render(
        template_id="query.list",
        version="v1",
        params={
            "title": "查询结果",
            "total": 2,
            "records": [
                {"fields_text": {"案号": "A-1", "法院": "一审"}},
                {"fields_text": {"案号": "A-2", "法院": "二审"}},
            ],
        },
    )

    assert len(elements) >= 2
    assert elements[0]["tag"] == "markdown"
    assert "共 2 条" in elements[0]["content"]


def test_render_query_list_v2_shows_top3_and_actions() -> None:
    registry = CardTemplateRegistry()

    elements = registry.render(
        template_id="query.list",
        version="v2",
        params={
            "title": "案件查询结果",
            "total": 4,
            "records": [
                {"record_id": "rec_1", "record_url": "https://example.com/1", "fields_text": {"案号": "A-1", "案由": "合同纠纷"}},
                {"record_id": "rec_2", "record_url": "https://example.com/2", "fields_text": {"案号": "A-2", "案由": "借款纠纷"}},
                {"record_id": "rec_3", "record_url": "https://example.com/3", "fields_text": {"案号": "A-3", "案由": "侵权纠纷"}},
                {"record_id": "rec_4", "record_url": "https://example.com/4", "fields_text": {"案号": "A-4", "案由": "劳动纠纷"}},
            ],
            "style": "T2",
            "domain": "case",
            "actions": {
                "next_page": {"callback_action": "query_list_next_page"},
                "today_hearing": {"callback_action": "query_list_today_hearing"},
                "week_hearing": {"callback_action": "query_list_week_hearing"},
            },
        },
    )

    elements_list = (
        [item for item in elements.get("elements", []) if isinstance(item, dict)]
        if isinstance(elements, dict)
        else [item for item in elements if isinstance(item, dict)]
    )
    wrapper_raw = elements.get("wrapper") if isinstance(elements, dict) else {}
    wrapper = wrapper_raw if isinstance(wrapper_raw, dict) else {}
    assert wrapper.get("header", {}).get("title", {}).get("content") == "案件查询结果"

    markdown_blocks = [item for item in elements_list if item.get("tag") == "markdown"]
    assert any("找到 4 个相关案件（显示前4条）" in str(item.get("content", "")) for item in markdown_blocks)
    assert not any(item.get("tag") == "action" for item in elements_list)


def test_render_query_list_v2_next_page_uses_callback_value_without_behaviors() -> None:
    registry = CardTemplateRegistry()

    elements = registry.render(
        template_id="query.list",
        version="v2",
        params={
            "title": "案件查询结果",
            "total": 6,
            "records": [
                {"fields_text": {"案号": "A-1", "案由": "合同纠纷"}},
                {"fields_text": {"案号": "A-2", "案由": "借款纠纷"}},
            ],
            "style": "T2",
            "domain": "case",
            "actions": {
                "next_page": {"callback_action": "query_list_next_page"},
            },
        },
    )

    next_buttons = [
        button
        for button in _buttons(elements)
        if isinstance(button.get("text"), dict)
        and "下一页" in str(button.get("text", {}).get("content", ""))
    ]
    assert next_buttons
    next_button = next_buttons[0]
    assert isinstance(next_button.get("value"), dict)
    assert next_button["value"].get("callback_action") == "query_list_next_page"
    assert "behaviors" not in next_button


def test_render_query_list_v2_case_t2_uses_template_files_and_wrapper() -> None:
    registry = CardTemplateRegistry()

    elements = registry.render(
        template_id="query.list",
        version="v2",
        params={
            "title": "案件查询结果",
            "total": 2,
            "records": [
                {
                    "record_id": "rec_1",
                    "record_url": "https://example.com/rec_1",
                    "fields_text": {
                        "案号": "A-1",
                        "委托人": "委托人甲",
                        "对方当事人": "对方乙",
                        "案由": "合同纠纷",
                        "案件状态": "进行中",
                        "主办律师": "张三",
                        "重要紧急程度": "一般",
                    },
                },
                {
                    "record_id": "rec_2",
                    "record_url": "https://example.com/rec_2",
                    "fields_text": {
                        "案号": "A-2",
                        "委托人": "委托人丙",
                        "对方当事人": "对方丁",
                        "案由": "借款纠纷",
                        "案件状态": "待开庭",
                        "主办律师": "李四",
                        "重要紧急程度": "重要紧急",
                    },
                },
            ],
            "style": "T2",
            "domain": "case",
            "table_name": "案件项目总库",
            "table_id": "tbl_case_demo",
        },
    )

    assert isinstance(elements, dict)
    wrapper_raw = elements.get("wrapper")
    wrapper = wrapper_raw if isinstance(wrapper_raw, dict) else {}
    assert wrapper.get("header", {}).get("title", {}).get("content") == "案件查询结果"
    assert wrapper.get("header", {}).get("icon", {}).get("token") == "search_outlined"

    elements_list = [item for item in elements.get("elements", []) if isinstance(item, dict)]
    assert any(item.get("tag") == "hr" for item in elements_list)

    nested_markdown: list[str] = []
    for item in elements_list:
        if item.get("tag") != "column_set":
            continue
        columns_raw = item.get("columns")
        columns = columns_raw if isinstance(columns_raw, list) else []
        for column in columns:
            if not isinstance(column, dict):
                continue
            column_elements_raw = column.get("elements")
            column_elements = column_elements_raw if isinstance(column_elements_raw, list) else []
            for element in column_elements:
                if isinstance(element, dict) and element.get("tag") == "markdown":
                    content = element.get("content")
                    if isinstance(content, str):
                        nested_markdown.append(content)
    merged = "\n".join(nested_markdown)
    assert "1️⃣ 委托人甲 vs 对方乙" in merged
    assert "🔖 A-2" in merged


def test_render_query_list_v2_case_t1_uses_layout_template_and_detail_header() -> None:
    registry = CardTemplateRegistry()

    elements = registry.render(
        template_id="query.list",
        version="v2",
        params={
            "title": "案件项目总库查询结果",
            "total": 1,
            "records": [
                {
                    "record_id": "rec_case_1",
                    "record_url": "https://example.com/rec_case_1",
                    "fields_text": {
                        "项目 ID": "JFTD-20260001",
                        "项目类型": "争议解决",
                        "案件分类": "劳动争议",
                        "案号": "一审：（2023）粤0118民初9131号\n二审：（2024）粤01民终28497号",
                        "委托人": "香港华艺设计顾问（深圳）有限公司",
                        "对方当事人": "广州市荔富汇景房地产有限公司",
                        "联系人": "陈桂媚",
                        "联系方式": "15019446008",
                        "案由": "劳动仲裁案件",
                        "审理法院": "广州中院",
                        "承办法庭": "第78法庭",
                        "程序阶段": "一审, 再审二审",
                        "承办法官": "二审：俞颖（020-83210730）",
                        "主办律师": "管理员",
                        "协办律师": "房怡康",
                        "开庭日": "2026-02-07 15:30",
                        "管辖权异议截止日": "2026-02-04",
                        "举证截止日": "2026-02-04",
                        "案件状态": "未结",
                        "重要紧急程度": "重要紧急",
                        "待做事项": "对方可能6个月后再次起诉，注意关注",
                        "进展": "2024-11-04 收到广州中院传票\n2024-10-16 广州中院回复，10月14日移送",
                        "备注": "对方当事人住址待查",
                        "关联合同": "20250131",
                    },
                }
            ],
            "style": "T1",
            "domain": "case",
            "table_name": "案件项目总库",
            "table_id": "tbl_case_demo",
        },
    )

    assert isinstance(elements, dict)
    wrapper_raw = elements.get("wrapper")
    wrapper = wrapper_raw if isinstance(wrapper_raw, dict) else {}
    assert wrapper.get("header", {}).get("title", {}).get("content") == "案件详情"
    assert wrapper.get("header", {}).get("icon", {}).get("token") == "law_outlined"

    elements_list = [item for item in elements.get("elements", []) if isinstance(item, dict)]
    assert any(item.get("tag") == "column_set" for item in elements_list)


def test_render_query_list_v2_contract_ht_t1_uses_template_file_and_wrapper() -> None:
    registry = CardTemplateRegistry()

    elements = registry.render(
        template_id="query.list",
        version="v2",
        params={
            "title": "合同管理表查询结果",
            "total": 1,
            "records": [
                {
                    "record_id": "rec_contract_1",
                    "record_url": "https://example.com/rec_contract_1",
                    "fields_text": {
                        "合同编号": "20250131",
                        "合同名称": "委托代理合同",
                        "客户名称": "香港华艺设计顾问（深圳）有限公司",
                        "合同状态": "履约中",
                        "甲方": "甲方公司",
                        "乙方": "乙方公司",
                        "合同金额": "100000",
                        "签约日期": "2026-02-04",
                        "合同开始日期": "2026-02-04",
                        "合同结束日期": "2026-01-28",
                        "盖章日期": "2026-02-04",
                        "主办律师": "管理员",
                        "开票付款状态": "未开票未付款",
                        "盖章状态": "待盖章",
                        "关联项目": "JFTD-20260001",
                    },
                }
            ],
            "style": "HT-T1",
            "domain": "contracts",
            "table_name": "合同管理表",
            "table_id": "tbl_contract_demo",
        },
    )

    assert isinstance(elements, dict)
    wrapper_raw = elements.get("wrapper")
    wrapper = wrapper_raw if isinstance(wrapper_raw, dict) else {}
    assert wrapper.get("header", {}).get("title", {}).get("content") == "合同详情"
    assert wrapper.get("header", {}).get("icon", {}).get("token") == "contract_outlined"

    elements_list = [item for item in elements.get("elements", []) if isinstance(item, dict)]
    assert any(item.get("tag") == "column_set" for item in elements_list)

    nested_markdown: list[str] = []
    button_labels: list[str] = []
    for item in elements_list:
        if item.get("tag") == "markdown":
            content = item.get("content")
            if isinstance(content, str):
                nested_markdown.append(content)
            continue
        if item.get("tag") != "column_set":
            continue
        columns_raw = item.get("columns")
        columns = columns_raw if isinstance(columns_raw, list) else []
        for column in columns:
            if not isinstance(column, dict):
                continue
            column_elements_raw = column.get("elements")
            column_elements = column_elements_raw if isinstance(column_elements_raw, list) else []
            for element in column_elements:
                if not isinstance(element, dict):
                    continue
                if element.get("tag") == "markdown":
                    content = element.get("content")
                    if isinstance(content, str):
                        nested_markdown.append(content)
                if element.get("tag") == "button":
                    text_raw = element.get("text")
                    text = text_raw if isinstance(text_raw, dict) else {}
                    label = text.get("content") if isinstance(text, dict) else ""
                    if isinstance(label, str):
                        button_labels.append(label)

    merged = "\n".join(nested_markdown)
    assert "20250131" in merged
    assert "金额与付款" in merged
    assert "未开票未付款" in merged
    assert "待盖章" in merged
    assert "查看关联案件" in button_labels
    assert "修改合同" in button_labels


def test_render_query_list_v2_contract_ht_t2_uses_template_files_and_wrapper() -> None:
    registry = CardTemplateRegistry()

    elements = registry.render(
        template_id="query.list",
        version="v2",
        params={
            "title": "合同管理表查询结果",
            "total": 2,
            "records": [
                {
                    "record_id": "rec_contract_1",
                    "record_url": "https://example.com/rec_contract_1",
                    "fields_text": {
                        "合同编号": "20250131",
                        "合同名称": "委托代理合同",
                        "客户名称": "香港华艺设计顾问",
                        "合同状态": "履约中",
                        "合同金额": "100000",
                        "开票付款状态": "未开票未付款",
                        "合同开始日期": "2026-02-04",
                        "合同结束日期": "2026-01-28",
                        "盖章状态": "待盖章",
                        "关联项目": "JFTD-20260001",
                        "主办律师": "王五",
                    },
                },
                {
                    "record_id": "rec_contract_2",
                    "record_url": "https://example.com/rec_contract_2",
                    "fields_text": {
                        "合同编号": "20250132",
                        "合同名称": "服务合同",
                        "客户名称": "中嘉建科",
                        "合同状态": "审批中",
                        "合同金额": "180000",
                        "开票付款状态": "部分开票",
                        "合同开始日期": "2026-02-06",
                        "合同结束日期": "2026-12-30",
                        "盖章状态": "已盖章",
                        "关联项目": "JFTD-20260023",
                        "主办律师": "赵六",
                    },
                },
            ],
            "style": "HT-T2",
            "domain": "contracts",
            "table_name": "合同管理表",
            "table_id": "tbl_contract_demo",
        },
    )

    assert isinstance(elements, dict)
    wrapper_raw = elements.get("wrapper")
    wrapper = wrapper_raw if isinstance(wrapper_raw, dict) else {}
    assert wrapper.get("header", {}).get("title", {}).get("content") == "合同查询结果"
    assert wrapper.get("header", {}).get("icon", {}).get("token") == "contract_outlined"

    elements_list = [item for item in elements.get("elements", []) if isinstance(item, dict)]
    markdown_text = "\n".join(item.get("content", "") for item in elements_list if item.get("tag") == "markdown")
    assert "找到 **2** 份合同（显示前2条）" in markdown_text

    nested_markdown: list[str] = []
    for item in elements_list:
        if item.get("tag") != "column_set":
            continue
        columns_raw = item.get("columns")
        columns = columns_raw if isinstance(columns_raw, list) else []
        for column in columns:
            if not isinstance(column, dict):
                continue
            column_elements_raw = column.get("elements")
            column_elements = column_elements_raw if isinstance(column_elements_raw, list) else []
            for element in column_elements:
                if isinstance(element, dict) and element.get("tag") == "markdown":
                    content = element.get("content")
                    if isinstance(content, str):
                        nested_markdown.append(content)

    merged = "\n".join(nested_markdown)
    assert "1️⃣ 20250131 | 委托代理合同" in merged
    assert "❌ 未开票未付款" in merged
    assert "2️⃣ 20250132 | 服务合同" in merged


def test_render_query_list_v2_bidding_zb_t1_uses_template_file_and_wrapper() -> None:
    registry = CardTemplateRegistry()

    elements = registry.render(
        template_id="query.list",
        version="v2",
        params={
            "title": "招投标台账查询结果",
            "total": 1,
            "records": [
                {
                    "record_id": "rec_bid_1",
                    "record_url": "https://example.com/rec_bid_1",
                    "fields_text": {
                        "项目号": "BID-0001",
                        "投标项目名称": "城市更新项目-1",
                        "招标方名称": "城建集团",
                        "阶段": "投标准备",
                        "标书购买截止时间": "2026-03-01",
                        "投标截止日": "2026-03-18",
                        "开标时间": "2026-03-20",
                        "保证金截止日期": "2026-03-10",
                        "标书领取状态": "已领取",
                        "保证金缴纳状态": "待缴纳",
                        "文件编制进度": "编制中",
                        "标书类型": "电子标",
                        "是否中标": "待定",
                        "中标金额": "300000",
                        "备注": "重点关注资格审查",
                        "承办律师": "赵六",
                    },
                }
            ],
            "style": "ZB-T1",
            "domain": "bidding",
            "table_name": "招投标台账",
            "table_id": "tbl_bid_demo",
        },
    )

    assert isinstance(elements, dict)
    wrapper_raw = elements.get("wrapper")
    wrapper = wrapper_raw if isinstance(wrapper_raw, dict) else {}
    assert wrapper.get("header", {}).get("title", {}).get("content") == "招投标详情"
    assert wrapper.get("header", {}).get("icon", {}).get("token") == "search_outlined"

    elements_list = [item for item in elements.get("elements", []) if isinstance(item, dict)]
    assert any(item.get("tag") == "column_set" for item in elements_list)

    nested_markdown: list[str] = []
    for item in elements_list:
        if item.get("tag") == "markdown":
            content = item.get("content")
            if isinstance(content, str):
                nested_markdown.append(content)
            continue
        if item.get("tag") != "column_set":
            continue
        columns_raw = item.get("columns")
        columns = columns_raw if isinstance(columns_raw, list) else []
        for column in columns:
            if not isinstance(column, dict):
                continue
            column_elements_raw = column.get("elements")
            column_elements = column_elements_raw if isinstance(column_elements_raw, list) else []
            for element in column_elements:
                if isinstance(element, dict) and element.get("tag") == "markdown":
                    content = element.get("content")
                    if isinstance(content, str):
                        nested_markdown.append(content)

    merged = "\n".join(nested_markdown)
    assert "项目基础" in merged
    assert "BID-0001" in merged
    assert "关键时间" in merged
    assert "结果与备注" in merged


def test_render_query_list_v2_bidding_zb_t2_uses_template_files_and_wrapper() -> None:
    registry = CardTemplateRegistry()

    elements = registry.render(
        template_id="query.list",
        version="v2",
        params={
            "title": "招投标台账查询结果",
            "total": 2,
            "records": [
                {
                    "record_id": "rec_bid_1",
                    "record_url": "https://example.com/rec_bid_1",
                    "fields_text": {
                        "项目号": "BID-0001",
                        "投标项目名称": "城市更新项目-1",
                        "招标方名称": "城建集团",
                        "阶段": "投标准备",
                        "投标截止日": "2026-03-18",
                        "是否中标": "待定",
                        "承办律师": "赵六",
                    },
                },
                {
                    "record_id": "rec_bid_2",
                    "record_url": "https://example.com/rec_bid_2",
                    "fields_text": {
                        "项目号": "BID-0002",
                        "投标项目名称": "园区改造项目-2",
                        "招标方名称": "园区集团",
                        "阶段": "已投标",
                        "投标截止日": "2026-03-25",
                        "是否中标": "中标",
                        "承办律师": "孙七",
                    },
                },
            ],
            "style": "ZB-T2",
            "domain": "bidding",
            "table_name": "招投标台账",
            "table_id": "tbl_bid_demo",
        },
    )

    assert isinstance(elements, dict)
    wrapper_raw = elements.get("wrapper")
    wrapper = wrapper_raw if isinstance(wrapper_raw, dict) else {}
    assert wrapper.get("header", {}).get("title", {}).get("content") == "招投标查询结果"
    assert wrapper.get("header", {}).get("icon", {}).get("token") == "search_outlined"

    elements_list = [item for item in elements.get("elements", []) if isinstance(item, dict)]
    markdown_text = "\n".join(item.get("content", "") for item in elements_list if item.get("tag") == "markdown")
    assert "找到 **2** 个招投标项目（显示前2条）" in markdown_text

    nested_markdown: list[str] = []
    for item in elements_list:
        if item.get("tag") != "column_set":
            continue
        columns_raw = item.get("columns")
        columns = columns_raw if isinstance(columns_raw, list) else []
        for column in columns:
            if not isinstance(column, dict):
                continue
            column_elements_raw = column.get("elements")
            column_elements = column_elements_raw if isinstance(column_elements_raw, list) else []
            for element in column_elements:
                if isinstance(element, dict) and element.get("tag") == "markdown":
                    content = element.get("content")
                    if isinstance(content, str):
                        nested_markdown.append(content)

    merged = "\n".join(nested_markdown)
    assert "1️⃣ 城市更新项目-1 | 投标准备" in merged
    assert "2️⃣ 园区改造项目-2 | 已投标" in merged


def test_render_query_list_v2_case_t3_uses_single_template_with_variant_content() -> None:
    registry = CardTemplateRegistry()
    today = date.today()

    elements = registry.render(
        template_id="query.list",
        version="v2",
        params={
            "title": "案件项目总库查询结果",
            "total": 2,
            "records": [
                {
                    "record_id": "rec_case_1",
                    "record_url": "https://example.com/rec_case_1",
                    "fields_text": {
                        "案号": "A-1",
                        "委托人": "委托人甲",
                        "对方当事人": "对方乙",
                        "案由": "合同纠纷",
                        "案件状态": "进行中",
                        "举证截止日": (today - timedelta(days=1)).isoformat(),
                    },
                },
                {
                    "record_id": "rec_case_2",
                    "record_url": "https://example.com/rec_case_2",
                    "fields_text": {
                        "案号": "A-2",
                        "委托人": "委托人丙",
                        "对方当事人": "对方丁",
                        "案由": "借款纠纷",
                        "案件状态": "待开庭",
                        "举证截止日": (today + timedelta(days=2)).isoformat(),
                    },
                },
            ],
            "style": "T3",
            "style_variant": "T3B",
            "domain": "case",
            "table_name": "案件项目总库",
            "table_id": "tbl_case_demo",
        },
    )

    assert isinstance(elements, dict)
    wrapper_raw = elements.get("wrapper")
    wrapper = wrapper_raw if isinstance(wrapper_raw, dict) else {}
    assert wrapper.get("header", {}).get("title", {}).get("content") == "重要日期提醒"
    assert wrapper.get("header", {}).get("template") == "orange"
    assert wrapper.get("header", {}).get("icon", {}).get("token") == "alert_outlined"
    markdown_text = "\n".join(
        item.get("content", "") for item in elements.get("elements", []) if isinstance(item, dict) and item.get("tag") == "markdown"
    )
    assert "已过期 / 今日到期" in markdown_text
    assert "未来7天" in markdown_text
    assert "统计：" in markdown_text

    nested_markdown: list[str] = []
    for item in elements.get("elements", []):
        if not isinstance(item, dict) or item.get("tag") != "column_set":
            continue
        columns_raw = item.get("columns")
        columns = columns_raw if isinstance(columns_raw, list) else []
        for column in columns:
            if not isinstance(column, dict):
                continue
            column_elements_raw = column.get("elements")
            column_elements = column_elements_raw if isinstance(column_elements_raw, list) else []
            for element in column_elements:
                if isinstance(element, dict) and element.get("tag") == "markdown":
                    content = element.get("content")
                    if isinstance(content, str):
                        nested_markdown.append(content)

    merged_nested = "\n".join(nested_markdown)
    assert "A-1" in merged_nested
    assert "A-2" in merged_nested


def test_render_query_list_v2_case_t5_uses_single_template_with_variant_content() -> None:
    registry = CardTemplateRegistry()

    elements = registry.render(
        template_id="query.list",
        version="v2",
        params={
            "title": "案件项目总库查询结果",
            "total": 3,
            "records": [
                {
                    "record_id": "rec_case_1",
                    "record_url": "https://example.com/rec_case_1",
                    "fields_text": {
                        "案号": "A-1",
                        "委托人": "委托人甲",
                        "对方当事人": "对方乙",
                        "案由": "合同纠纷",
                        "案件状态": "进行中",
                        "待做事项": "补充证据目录",
                    },
                },
                {
                    "record_id": "rec_case_2",
                    "record_url": "https://example.com/rec_case_2",
                    "fields_text": {
                        "案号": "A-2",
                        "委托人": "委托人丙",
                        "对方当事人": "对方丁",
                        "案由": "借款纠纷",
                        "案件状态": "进行中",
                        "待做事项": "准备开庭材料",
                    },
                },
                {
                    "record_id": "rec_case_3",
                    "record_url": "https://example.com/rec_case_3",
                    "fields_text": {
                        "案号": "A-3",
                        "委托人": "委托人戊",
                        "对方当事人": "对方己",
                        "案由": "侵权纠纷",
                        "案件状态": "已结案",
                        "待做事项": "归档",
                    },
                },
            ],
            "style": "T5",
            "style_variant": "T5C",
            "domain": "case",
            "table_name": "案件项目总库",
            "table_id": "tbl_case_demo",
        },
    )

    assert isinstance(elements, dict)
    wrapper_raw = elements.get("wrapper")
    wrapper = wrapper_raw if isinstance(wrapper_raw, dict) else {}
    assert wrapper.get("header", {}).get("title", {}).get("content") == "待办事项与案件进展"
    assert wrapper.get("header", {}).get("template") == "orange"
    assert wrapper.get("header", {}).get("icon", {}).get("token") == "alert_outlined"
    markdown_text = "\n".join(
        item.get("content", "") for item in elements.get("elements", []) if isinstance(item, dict) and item.get("tag") == "markdown"
    )
    assert "### 状态筛选" in markdown_text
    assert "进行中（2）" in markdown_text
    assert "已结案（1）" in markdown_text
    assert "A-3" in markdown_text
    assert "进行中 2 条" in markdown_text

    elements_list = [item for item in elements.get("elements", []) if isinstance(item, dict)]
    assert not any(item.get("tag") == "action" for item in elements_list)


def test_render_query_list_v2_uses_not_found_template_for_empty() -> None:
    registry = CardTemplateRegistry()

    elements = registry.render(
        template_id="query.list",
        version="v2",
        params={
            "title": "合同管理表查询结果",
            "total": 0,
            "records": [],
            "style": "HT-T2",
            "domain": "contracts",
        },
    )

    assert len(elements) == 1
    assert "未找到相关记录" in elements[0]["content"]
    assert "建议" in elements[0]["content"]


def test_render_query_list_v2_compact_for_large_results_with_remaining_hint() -> None:
    registry = CardTemplateRegistry()

    records = [
        {
            "record_id": f"rec_{index}",
            "record_url": f"https://example.com/rec_{index}",
            "fields_text": {"案号": f"A-{index}", "案件状态": "进行中", "主办律师": "张三"},
        }
        for index in range(1, 13)
    ]

    elements = registry.render(
        template_id="query.list",
        version="v2",
        params={
            "title": "案件项目总库查询结果",
            "total": 12,
            "records": records,
            "style": "T2",
            "domain": "case",
            "actions": {
                "next_page": {"callback_action": "query_list_next_page"},
                "today_hearing": {"callback_action": "query_list_today_hearing"},
                "week_hearing": {"callback_action": "query_list_week_hearing"},
            },
        },
    )

    elements_list = (
        [item for item in elements.get("elements", []) if isinstance(item, dict)]
        if isinstance(elements, dict)
        else [item for item in elements if isinstance(item, dict)]
    )
    markdown_blocks = [item for item in elements_list if item.get("tag") == "markdown"]
    body_text = "\n".join(str(item.get("content", "")) for item in markdown_blocks)
    assert "显示前5条" in body_text
    assert "缩小范围" not in body_text

    item_cards = [
        item
        for item in elements_list
        if item.get("tag") == "column_set"
        and isinstance(item.get("columns"), list)
        and any(
            isinstance(column, dict) and column.get("background_style") == "grey-50"
            for column in item.get("columns", [])
        )
    ]
    assert len(item_cards) == 5

    next_buttons = [item for item in elements_list if item.get("tag") == "button"]
    assert next_buttons[-1].get("text", {}).get("content") == "下一页（剩余 7 条）"


def test_render_query_list_v2_detail_mode_shows_placeholder_for_empty_fields() -> None:
    registry = CardTemplateRegistry()

    elements = registry.render(
        template_id="query.list",
        version="v2",
        params={
            "title": "案件项目总库查询结果",
            "total": 1,
            "records": [
                {
                    "record_id": "rec_1",
                    "record_url": "https://example.com/rec_1",
                    "fields_text": {"案号": "A-1", "委托人": "", "对方当事人": ""},
                }
            ],
            "style": "T1",
            "domain": "case",
        },
    )

    elements_list = (
        [item for item in elements.get("elements", []) if isinstance(item, dict)]
        if isinstance(elements, dict)
        else [item for item in elements if isinstance(item, dict)]
    )
    markdown_texts: list[str] = []
    for item in elements_list:
        if item.get("tag") == "markdown":
            content = item.get("content")
            if isinstance(content, str):
                markdown_texts.append(content)
            continue
        if item.get("tag") != "column_set":
            continue
        columns_raw = item.get("columns")
        columns = columns_raw if isinstance(columns_raw, list) else []
        for column in columns:
            if not isinstance(column, dict):
                continue
            col_elements_raw = column.get("elements")
            col_elements = col_elements_raw if isinstance(col_elements_raw, list) else []
            for element in col_elements:
                if not isinstance(element, dict) or element.get("tag") != "markdown":
                    continue
                content = element.get("content")
                if isinstance(content, str):
                    markdown_texts.append(content)

    assert any("委托人：" in content for content in markdown_texts)

    button_labels: list[str] = []
    for item in elements_list:
        if item.get("tag") != "column_set":
            continue
        columns_raw = item.get("columns")
        columns = columns_raw if isinstance(columns_raw, list) else []
        for column in columns:
            if not isinstance(column, dict):
                continue
            col_elements_raw = column.get("elements")
            col_elements = col_elements_raw if isinstance(col_elements_raw, list) else []
            for element in col_elements:
                if not isinstance(element, dict):
                    continue
                if element.get("tag") != "button":
                    continue
                text_raw = element.get("text")
                text = text_raw if isinstance(text_raw, dict) else {}
                label = text.get("content") if isinstance(text, dict) else ""
                if isinstance(label, str):
                    button_labels.append(label)

    assert "查看关联合同" in button_labels
    assert "修改" in button_labels


def test_render_query_list_v2_multiple_records_do_not_auto_expand_first_detail() -> None:
    registry = CardTemplateRegistry()

    elements = registry.render(
        template_id="query.list",
        version="v2",
        params={
            "title": "案件查询结果",
            "total": 3,
            "records": [
                {"record_id": "rec_1", "fields_text": {"案号": "A-1", "案由": "合同纠纷"}},
                {"record_id": "rec_2", "fields_text": {"案号": "A-2", "案由": "借款纠纷"}},
                {"record_id": "rec_3", "fields_text": {"案号": "A-3", "案由": "侵权纠纷"}},
            ],
            "style": "T2",
            "domain": "case",
        },
    )

    elements_list = (
        [item for item in elements.get("elements", []) if isinstance(item, dict)]
        if isinstance(elements, dict)
        else [item for item in elements if isinstance(item, dict)]
    )
    markdown_text = "\n".join(item["content"] for item in elements_list if item.get("tag") == "markdown")
    assert "首条详情" not in markdown_text


def test_render_query_list_v2_marks_source_table_and_style() -> None:
    registry = CardTemplateRegistry()

    elements = registry.render(
        template_id="query.list",
        version="v2",
        params={
            "title": "招投标台账查询结果",
            "total": 2,
            "records": [
                {"fields_text": {"项目名称": "城中村改造", "负责人": "王五", "阶段": "投标准备"}},
                {"fields_text": {"项目名称": "园区更新", "负责人": "赵六", "阶段": "已投标"}},
            ],
            "style": "ZB-T4",
            "domain": "bidding",
            "table_name": "招投标台账",
            "table_id": "tbl_bid_001",
        },
    )

    markdown_blocks = [item for item in elements if item.get("tag") == "markdown"]
    all_text = "\n".join(item["content"] for item in markdown_blocks)
    assert "数据表: 招投标台账" in all_text
    assert "tbl_bid_001" in all_text
    assert "模板: ZB-T4" in all_text


def test_render_query_list_v2_supports_source_keys_in_style_dsl(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "card_templates.yaml"
    config_path.write_text(
        """
default_versions:
  query.list: v2
enabled:
  query.list.v2: true
render_templates:
  query_list_v2:
    template_dsl:
      case:
        styles:
          T2:
            list_fields:
              - name: custom_case_code
                label: 自定义案号
                source_keys: [案件编号, 案号]
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setenv("CARD_TEMPLATE_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("CARD_TEMPLATE_CONFIG_YAML_ENABLED", "true")
    reset_template_config_cache()
    try:
        registry = CardTemplateRegistry()
        elements = registry.render(
            template_id="query.list",
            version="v2",
            params={
                "title": "案件查询结果",
                "total": 2,
                "records": [
                    {"fields_text": {"案件编号": "X-001"}},
                    {"fields_text": {"案件编号": "X-002"}},
                ],
                "domain": "case",
                "style": "T2",
            },
        )
        markdown_blocks = [item for item in elements if item.get("tag") == "markdown"]
        body_text = "\n".join(item["content"] for item in markdown_blocks)
        assert "自定义案号: X-001" in body_text
    finally:
        monkeypatch.delenv("CARD_TEMPLATE_CONFIG_PATH", raising=False)
        monkeypatch.delenv("CARD_TEMPLATE_CONFIG_YAML_ENABLED", raising=False)
        reset_template_config_cache()


def test_render_query_list_v2_supports_field_mapping_key_lookup(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "card_templates.yaml"
    config_path.write_text(
        """
default_versions:
  query.list: v2
enabled:
  query.list.v2: true
render_templates:
  query_list_v2:
    field_mapping:
      case:
        项目 ID: project_id
    template_dsl:
      case:
        styles:
          T2:
            list_fields:
              - key: project_id
                label: 项目 ID
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setenv("CARD_TEMPLATE_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("CARD_TEMPLATE_CONFIG_YAML_ENABLED", "true")
    reset_template_config_cache()
    try:
        registry = CardTemplateRegistry()
        elements = registry.render(
            template_id="query.list",
            version="v2",
            params={
                "title": "案件查询结果",
                "total": 2,
                "records": [
                    {"fields_text": {"项目 ID": "JFTD-2026001"}},
                    {"fields_text": {"项目 ID": "JFTD-2026002"}},
                ],
                "domain": "case",
                "style": "T2",
            },
        )
        markdown_blocks = [item for item in elements if item.get("tag") == "markdown"]
        body_text = "\n".join(item["content"] for item in markdown_blocks)
        assert "项目 ID: JFTD-2026001" in body_text
    finally:
        monkeypatch.delenv("CARD_TEMPLATE_CONFIG_PATH", raising=False)
        monkeypatch.delenv("CARD_TEMPLATE_CONFIG_YAML_ENABLED", raising=False)
        reset_template_config_cache()


def test_render_query_list_v2_supports_section_and_summary(monkeypatch, tmp_path) -> None:
    today = date.today()
    config_path = tmp_path / "card_templates.yaml"
    config_path.write_text(
        f"""
default_versions:
  query.list: v2
enabled:
  query.list.v2: true
render_templates:
  query_list_v2:
    field_mapping:
      case:
        项目 ID: project_id
        开庭日: hearing_date
        重要紧急程度: urgency
    template_dsl:
      case:
        styles:
          T4A:
            sections:
              - name: 最近开庭
                icon: ⏰
                filter: "hearing_date >= today, sort: hearing_date asc, limit: 2"
                list_fields:
                  - key: project_id
                    label: 项目 ID
                  - key: hearing_date
                    label: 开庭
                    format: date_countdown_short
            summary:
              template: "统计：共 {{total}} 条"
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setenv("CARD_TEMPLATE_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("CARD_TEMPLATE_CONFIG_YAML_ENABLED", "true")
    reset_template_config_cache()
    try:
        registry = CardTemplateRegistry()
        elements = registry.render(
            template_id="query.list",
            version="v2",
            params={
                "title": "案件查询结果",
                "total": 3,
                "records": [
                    {"fields_text": {"项目 ID": "A-1", "开庭日": (today + timedelta(days=1)).isoformat()}},
                    {"fields_text": {"项目 ID": "A-2", "开庭日": (today + timedelta(days=2)).isoformat()}},
                    {"fields_text": {"项目 ID": "A-3", "开庭日": (today + timedelta(days=3)).isoformat()}},
                ],
                "domain": "case",
                "style": "T4",
                "style_variant": "T4A",
            },
        )

        elements_list = _elements(elements)
        markdown_text = "\n".join(item.get("content", "") for item in elements_list if item.get("tag") == "markdown")
        assert "最近开庭" in markdown_text
        assert "A-1" in markdown_text
        assert "A-2" in markdown_text
        assert "A-3" not in markdown_text
        assert "统计：共 3 条" in markdown_text
    finally:
        monkeypatch.delenv("CARD_TEMPLATE_CONFIG_PATH", raising=False)
        monkeypatch.delenv("CARD_TEMPLATE_CONFIG_YAML_ENABLED", raising=False)
        reset_template_config_cache()


def test_render_uses_yaml_config_for_action_button_text(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "card_templates.yaml"
    config_path.write_text(
        """
default_versions:
  query.list: v2
enabled:
  query.list.v2: true
render_templates:
  query_list_v2:
    actions:
      next_page: 下一批
      next_page_with_remaining: 下一批（剩余 {remaining} 条）
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setenv("CARD_TEMPLATE_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("CARD_TEMPLATE_CONFIG_YAML_ENABLED", "true")
    reset_template_config_cache()
    try:
        registry = CardTemplateRegistry()
        elements = registry.render(
            template_id="query.list",
            version="v2",
            params={
                "title": "案件查询结果",
                "total": 12,
                "records": [{"fields_text": {"案号": f"A-{index}"}} for index in range(1, 13)],
                "actions": {"next_page": {"callback_action": "query_list_next_page"}},
                "domain": "contracts",
                "style": "HT-T2",
            },
        )

        button_texts = _button_texts(elements)
        assert button_texts[-1] == "下一批（剩余 7 条）"
    finally:
        monkeypatch.delenv("CARD_TEMPLATE_CONFIG_PATH", raising=False)
        monkeypatch.delenv("CARD_TEMPLATE_CONFIG_YAML_ENABLED", raising=False)
        reset_template_config_cache()


def test_render_query_detail_v1() -> None:
    registry = CardTemplateRegistry()

    elements = registry.render(
        template_id="query.detail",
        version="v1",
        params={"record": {"fields_text": {"案号": "A-1", "原告": "张三"}}},
    )

    assert len(elements) >= 2
    assert "案号" in elements[1]["content"]


def test_render_action_confirm_v1() -> None:
    registry = CardTemplateRegistry()

    elements = registry.render(
        template_id="action.confirm",
        version="v1",
        params={"message": "确认删除", "action": "delete_record"},
    )

    wrapper = _wrapper(elements)
    assert wrapper.get("header", {}).get("title", {}).get("content") == "危险操作确认"

    elements_list = _elements(elements)
    markdown_text = _markdown_text(elements)
    assert "确认删除" in markdown_text
    assert "不可撤销" in markdown_text
    button_texts = _button_texts(elements)
    assert "⛔ 确认删除" in button_texts
    assert "❌ 取消" in button_texts


def test_render_action_confirm_v1_create_record_shows_fields_and_missing() -> None:
    registry = CardTemplateRegistry()

    elements = registry.render(
        template_id="action.confirm",
        version="v1",
        params={
            "message": "请确认新增",
            "action": "create_record",
            "table_name": "案件项目总库",
            "payload": {
                "fields": {"案号": "(2026)粤0101民初100号", "委托人": ""},
                "required_fields": ["案号", "委托人"],
            },
        },
    )

    wrapper = _wrapper(elements)
    assert wrapper.get("header", {}).get("title", {}).get("content") == "新增案件 - 请确认"

    elements_list = _elements(elements)
    text = _markdown_text(elements)
    assert "待新增字段" in text
    assert "以下字段未提供" in text
    button_texts = _button_texts(elements)
    assert "✏️ 修改" in button_texts

    callback_buttons = [
        button
        for button in _buttons(elements)
        if any(token in str(button.get("text", {}).get("content", "")) for token in ("确认", "修改", "取消"))
    ]
    assert callback_buttons
    for button in callback_buttons:
        assert isinstance(button.get("value"), dict)
        assert "behaviors" not in button


def test_render_action_confirm_v1_update_record_shows_diff_and_suggestion() -> None:
    registry = CardTemplateRegistry()

    elements = registry.render(
        template_id="action.confirm",
        version="v1",
        params={
            "message": "请确认修改",
            "action": "update_record",
            "payload": {
                "diff": [
                    {
                        "field": "进展",
                        "old": "已立案",
                        "new": "开庭时间变更为2026-03-15",
                    }
                ]
            },
        },
    )

    wrapper = _wrapper(elements)
    assert wrapper.get("header", {}).get("title", {}).get("content") == "修改确认"

    elements_list = _elements(elements)
    text = _markdown_text(elements)
    assert "变更明细" in text
    assert "建议同步确认字段：开庭日" in text


def test_render_action_confirm_v1_close_record_uses_profile_texts() -> None:
    registry = CardTemplateRegistry()

    elements = registry.render(
        template_id="action.confirm",
        version="v1",
        params={
            "message": "请确认关闭",
            "action": "close_record",
            "confirm_text": "确认结案",
            "cancel_text": "暂不结案",
            "payload": {
                "close_title": "案件结案",
                "close_status_field": "案件状态",
                "close_status_from": "进行中",
                "close_status_value": "已结案",
                "close_consequences": ["案件将从在办视角移出"],
            },
        },
    )

    wrapper = _wrapper(elements)
    assert wrapper.get("header", {}).get("title", {}).get("content") == "操作确认"

    elements_list = _elements(elements)
    text = _markdown_text(elements)
    assert "状态变更" in text
    assert "操作后影响" in text
    button_texts = _button_texts(elements)
    assert any("确认结案" in text for text in button_texts)


def test_render_action_confirm_v1_create_reminder_shows_items() -> None:
    registry = CardTemplateRegistry()

    elements = registry.render(
        template_id="action.confirm",
        version="v1",
        params={
            "message": "请确认自动创建提醒",
            "action": "create_reminder",
            "payload": {
                "reminders": [
                    {
                        "content": "开庭提醒（开庭日）",
                        "remind_time": "2099-01-10 09:00",
                    }
                ]
            },
        },
    )

    wrapper = _wrapper(elements)
    assert wrapper.get("header", {}).get("title", {}).get("content") == "自动提醒创建确认"

    elements_list = _elements(elements)
    text = _markdown_text(elements)
    assert "待创建提醒" in text
    assert "2099-01-10 09:00" in text


def test_render_error_notice_v1() -> None:
    registry = CardTemplateRegistry()

    elements = registry.render(
        template_id="error.notice",
        version="v1",
        params={"message": "权限不足", "skill_name": "DeleteSkill"},
    )

    wrapper = _wrapper(elements)
    assert wrapper.get("header", {}).get("title", {}).get("content") == "操作失败"
    assert wrapper.get("header", {}).get("template") == "red"

    elements_list = _elements(elements)
    text = _markdown_text(elements)
    assert "权限不足" in text
    assert "DeleteSkill" in text


def test_render_todo_reminder_v1() -> None:
    registry = CardTemplateRegistry()

    elements = registry.render(
        template_id="todo.reminder",
        version="v1",
        params={
            "message": "提醒创建成功",
            "content": "提交材料",
            "remind_time": "2026-02-23 10:00",
        },
    )

    assert "提醒创建成功" in elements[0]["content"]
    assert "提交材料" in elements[0]["content"]


def test_render_create_success_v1() -> None:
    registry = CardTemplateRegistry()

    elements = registry.render(
        template_id="create.success",
        version="v1",
        params={
            "record": {
                "record_id": "rec_001",
                "fields_text": {"案号": "A-1", "委托人": "张三"},
                "record_url": "https://example.com/rec_001",
            }
        },
    )

    wrapper = _wrapper(elements)
    assert wrapper.get("header", {}).get("title", {}).get("content") == "新增成功"

    elements_list = _elements(elements)
    markdown_text = _markdown_text(elements)
    assert "案号" in markdown_text

    button_texts = _button_texts(elements)
    assert "查看详情" in button_texts
    detail_buttons = [
        button
        for button in _buttons(elements)
        if str(button.get("text", {}).get("content", "")) == "查看详情"
    ]
    assert detail_buttons
    detail_button = detail_buttons[0]
    behaviors = detail_button.get("behaviors")
    assert isinstance(behaviors, list) and behaviors
    assert behaviors[0].get("type") == "open_url"
    assert "value" not in detail_button


def test_render_create_success_v1_shows_auto_reminders() -> None:
    registry = CardTemplateRegistry()

    elements = registry.render(
        template_id="create.success",
        version="v1",
        params={
            "table_name": "案件项目总库",
            "record": {
                "record_id": "rec_001",
                "fields_text": {"案号": "A-1", "开庭日": "2099-01-10"},
                "record_url": "https://example.com/rec_001",
            },
        },
    )

    elements_list = _elements(elements)
    markdown_text = _markdown_text(elements)
    assert "提醒已设置" in markdown_text
    assert "开庭日" in markdown_text


def test_render_update_success_v1() -> None:
    registry = CardTemplateRegistry()

    elements = registry.render(
        template_id="update.success",
        version="v1",
        params={
            "changes": [
                {"field": "状态", "old": "待办", "new": "已完成"},
                {"field": "负责人", "old": "张三", "new": "李四"},
            ],
            "record_id": "rec_002",
            "record_url": "https://example.com/rec_002",
        },
    )

    wrapper = _wrapper(elements)
    assert wrapper.get("header", {}).get("title", {}).get("content") == "操作成功"

    elements_list = _elements(elements)
    markdown_text = _markdown_text(elements)
    assert "状态" in markdown_text
    assert "待办 -> 已完成" in markdown_text

    button_texts = _button_texts(elements)
    assert "查看详情" in button_texts


def test_render_update_guide_v1_shows_record_summary_and_cancel_button() -> None:
    registry = CardTemplateRegistry()

    elements = registry.render(
        template_id="update.guide",
        version="v1",
        params={
            "title": "修改案件",
            "record_id": "rec_guide_1",
            "table_type": "case",
            "record_case_no": "JFTD-20260001",
            "record_identity": "香港华艺设计顾问 vs 广州荔富汇景",
            "cancel_action": {"callback_action": "update_collect_fields_cancel"},
        },
    )

    wrapper = _wrapper(elements)
    assert wrapper.get("header", {}).get("title", {}).get("content") == "修改案件"

    text = _markdown_text(elements)
    assert "已定位到案件" in text
    assert "JFTD-20260001" in text
    assert "开庭日改成2024-12-01" in text

    cancel_buttons = [
        button
        for button in _buttons(elements)
        if "取消" in str(button.get("text", {}).get("content", ""))
    ]
    assert cancel_buttons
    assert cancel_buttons[0].get("value", {}).get("callback_action") == "update_collect_fields_cancel"


def test_render_delete_confirm_v1() -> None:
    registry = CardTemplateRegistry()

    elements = registry.render(
        template_id="delete.confirm",
        version="v1",
        params={
            "summary": {"案号": "A-3", "记录 ID": "rec_003"},
            "actions": {
                "confirm": {"callback_action": "delete_record_confirm", "intent": "confirm"},
                "cancel": {"callback_action": "delete_record_cancel", "intent": "cancel"},
            },
        },
    )

    wrapper = _wrapper(elements)
    assert "危险操作确认" in str(wrapper.get("header", {}).get("title", {}).get("content", ""))

    elements_list = _elements(elements)
    body = _markdown_text(elements)
    assert "案号" in body
    button_texts = _button_texts(elements)
    assert "⛔ 确认删除" in button_texts
    assert "✏️ 修改" in button_texts
    assert "❌ 取消" in button_texts


def test_render_delete_confirm_v1_uses_profile_warning_and_suggestion() -> None:
    registry = CardTemplateRegistry()

    elements = registry.render(
        template_id="delete.confirm",
        version="v1",
        params={
            "title": "删除确认",
            "subtitle": "请再次确认",
            "summary": {"案号": "A-3", "记录 ID": "rec_003"},
            "warnings": ["该操作将永久删除记录"],
            "suggestion": "如仅需结束流程，建议优先使用关闭/结案。",
            "confirm_text": "确认删除",
            "cancel_text": "取消",
            "confirm_type": "danger",
            "actions": {
                "confirm": {"callback_action": "delete_record_confirm", "intent": "confirm"},
                "cancel": {"callback_action": "delete_record_cancel", "intent": "cancel"},
            },
        },
    )

    elements_list = _elements(elements)
    body = _markdown_text(elements)
    assert "警告" in body
    assert "建议" in body
    button_texts = _button_texts(elements)
    assert "⛔ 确认删除" in button_texts


def test_render_delete_result_cards_v1() -> None:
    registry = CardTemplateRegistry()

    success = registry.render(
        template_id="delete.success",
        version="v1",
        params={"message": "已删除案件 A-4"},
    )
    cancelled = registry.render(
        template_id="delete.cancelled",
        version="v1",
        params={"message": "已取消本次删除"},
    )

    success_wrapper = _wrapper(success)
    cancelled_wrapper = _wrapper(cancelled)
    assert "删除成功" in str(success_wrapper.get("header", {}).get("title", {}).get("content", ""))
    assert "已取消" in str(cancelled_wrapper.get("header", {}).get("title", {}).get("content", ""))


def test_render_error_notice_v1_with_error_class_guidance() -> None:
    registry = CardTemplateRegistry()

    elements = registry.render(
        template_id="error.notice",
        version="v1",
        params={
            "message": "当前账号权限不足，无法删除",
            "error_class": "permission_denied",
        },
    )

    elements_list = _elements(elements)
    text = _markdown_text(elements)
    assert "权限不足" in text
    assert "建议下一步" in text


def test_render_upload_result_v1_with_failure_reason_and_next_step() -> None:
    registry = CardTemplateRegistry()

    elements = registry.render(
        template_id="upload.result",
        version="v1",
        params={
            "status": "failed",
            "reason_code": "extractor_timeout",
            "guidance": "已收到文件，但解析超时，请稍后重试或补充文字说明。",
            "provider": "llm",
            "message_type": "file",
            "file_name": "合同.pdf",
            "file_type": "pdf",
            "file_size": 2048,
        },
    )

    text = elements[0]["content"]
    assert "文件解析失败" in text
    assert "合同.pdf" in text
    assert "2.0 KB" in text
    assert "解析服务响应超时" in text
    assert "下一步" in text


def test_render_upload_result_v1_with_success_preview() -> None:
    registry = CardTemplateRegistry()

    elements = registry.render(
        template_id="upload.result",
        version="v1",
        params={
            "status": "success",
            "provider": "mineru",
            "message_type": "image",
            "file_name": "证据截图.png",
            "markdown_preview": "第一行\n第二行\n第三行",
        },
    )

    text = elements[0]["content"]
    assert "文件解析成功" in text
    assert "MinerU" in text
    assert "图片" in text
    assert "识别摘要" in text
