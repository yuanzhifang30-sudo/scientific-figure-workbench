"""Anonymous synthetic figures; demo approvals are labeled simulation records."""
import base64
import csv
import io

from PIL import Image, ImageDraw, ImageFont

from .store import Store, WorkflowError


def example_files(index=0, broken=False):
    image = Image.new("RGB", (1200, 700), "#ffffff")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=26)
    title_font = ImageFont.load_default(size=36)
    draw.text((90, 45), ["Latency by scenario", "Throughput comparison", "Cost distribution", "Sensitivity study", "Ablation results", "Scaling curve"][index % 6], fill="#142b4b", font=title_font)
    draw.text((90, 105), "SYNTHETIC DEMO / Not competition results", fill="#738397", font=font)
    draw.line((110, 555, 1100, 555), fill="#90a3ba", width=2)
    draw.line((110, 190, 110, 555), fill="#90a3ba", width=2)
    baseline, candidate = [100, 150, 230, 300], [84, 133, 201, 257]
    if index % 2:
        candidate = [90, 140, 245, 285]
    for i, (b, c) in enumerate(zip(baseline, candidate)):
        x = 180 + i * 225
        draw.rounded_rectangle((x, 555-b, x+60, 555), radius=5, fill="#aabfd7")
        draw.rounded_rectangle((x+68, 555-c, x+128, 555), radius=5, fill="#167e85")
        draw.text((x+12, 580), f"Case {i+1}", fill="#526577", font=font)
    draw.rectangle((880, 178, 901, 199), fill="#aabfd7")
    draw.text((915, 172), "Baseline", fill="#526577", font=font)
    draw.rectangle((880, 215, 901, 236), fill="#167e85")
    draw.text((915, 209), "Candidate", fill="#526577", font=font)
    draw.text((90, 650), "Illustration only - inspect data and caption before approval", fill="#738397", font=ImageFont.load_default(size=19))
    if broken:
        image = image.resize((320, 187))
    png = io.BytesIO()
    image.save(png, format="PNG", dpi=(300, 300))
    csv_stream = io.StringIO(newline="")
    writer = csv.writer(csv_stream, lineterminator="\n")
    writer.writerow(["series", "x", "value"])
    for series, values in (("baseline", baseline), ("candidate", candidate)):
        for i, value in enumerate(values):
            writer.writerow([series, f"case-{i+1}", "NaN" if broken and i == 3 else value])
    return [{"role": role, "name": name, "data_base64": base64.b64encode(data).decode("ascii")}
            for role, name, data in (("figure", "figure.png", png.getvalue()), ("data", "source.csv", csv_stream.getvalue().encode("utf-8")))]


def build_demo(directory):
    store = Store(directory)
    if store.state()["tasks"] or store.state()["releases"]:
        raise WorkflowError("演示目录已有数据，请使用新的空目录")
    titles = ["场景延迟与基线对比", "吞吐率结果对照", "资源成本分布", "参数敏感性分析", "消融实验对照", "规模扩展曲线"]
    for i, title in enumerate(titles):
        owner = ["author-a", "author-a", "author-b", "author-c", "author-b", "author-c"][i]
        result = store.create_task({"expected_revision": store.state()["revision"], "title": title, "owner": owner,
                                    "brief": "核对用例覆盖、单位、基线与图注。示例数据为自造数据，不用于研究结论。"})
        task_id = result["result"]["task_id"]
        if i == 5:
            continue
        store.start(task_id, {"expected_revision": store.state()["revision"]})
        submitted = store.submit(task_id, {"expected_revision": store.state()["revision"], "version": "demo-v1",
                                           "caption": "自造示例：4个用例、2个版本。柱高与CSV值需人工核对；单位与统计口径在真实使用时补充。",
                                           "files": example_files(i, broken=i == 3)})
        if i in (0, 1, 4):
            store.review(task_id, {"expected_revision": store.state()["revision"], "submission_id": submitted["result"]["submission_id"],
                                   "decision": "rework" if i == 4 else "accept",
                                   "note": "演示模拟验收：图注尚需说明统计口径，请返工。" if i == 4 else "演示模拟验收：仅用于展示流程，不是真实人工签署。",
                                   "checklist": {"visual": True, "data": True, "caption": True}})
    return store
