"""Offline check of adf/adf_template.json (no Azure needed): valid JSON, every reference points to
something that exists, the pipeline order is right and the trigger is weekly. Run: py test_adf_local.py"""
import json
from pathlib import Path

TEMPLATE = Path(__file__).with_name("adf") / "adf_template.json"
passed = failed = 0


def check(name, ok):
    global passed, failed
    print(("PASS  " if ok else "FAIL  ") + name)
    passed, failed = passed + ok, failed + (not ok)


t = json.loads(TEMPLATE.read_text(encoding="utf-8"))
check("template is valid JSON", True)

by_type = {}
for r in t["resources"]:
    short = r["type"].split("/")[-1]
    name = r["name"] if short == "factories" else r["name"].split("'/")[-1].rstrip("')]")
    by_type.setdefault(short, {})[name] = r
check("factory has a managed identity", by_type["factories"]["[parameters('factoryName')]"]["identity"]["type"] == "SystemAssigned")
check("2 linked services", set(by_type["linkedservices"]) == {"LS_DataLake", "LS_Synapse"})
check("2 datasets", set(by_type["datasets"]) == {"DS_RawZone", "DS_CuratedFolder"})

for name, ds in by_type["datasets"].items():
    check(f"{name} uses an existing linked service",
          ds["properties"]["linkedServiceName"]["referenceName"] in by_type["linkedservices"])

pipe = by_type["pipelines"]["PL_SmartEnergy_Weekly"]["properties"]["activities"]
acts = {a["name"]: a for a in pipe}
for a in pipe:
    for d in a.get("dependsOn", []):
        check(f"{a['name']} depends on an existing activity", d["activity"] in acts)
    ds = a.get("typeProperties", {}).get("dataset")
    if ds:
        check(f"{a['name']} uses an existing dataset", ds["referenceName"] in by_type["datasets"])
    ls = a.get("linkedServiceName")
    if ls:
        check(f"{a['name']} uses an existing linked service", ls["referenceName"] in by_type["linkedservices"])

check("raw check runs first", "dependsOn" not in acts["CheckRawFiles"])
check("both notebooks wait for the raw check",
      all(acts[n]["dependsOn"][0]["activity"] == "CheckRawFiles" for n in ("RunCsvNotebook", "RunJsonNotebook")))
check("notebooks run in parallel", acts["RunCsvNotebook"]["dependsOn"] == acts["RunJsonNotebook"]["dependsOn"])
check("notebook names match the Spark document",
      t["parameters"]["csvNotebook"]["defaultValue"] == "Milestone2_CSV_Transformation"
      and t["parameters"]["jsonNotebook"]["defaultValue"] == "Milestone2_JSON_Transformation")
check("curated checks match the notebook output folders",
      {acts[n]["typeProperties"]["dataset"]["parameters"]["folder"] for n in ("CheckCleanedData", "CheckAverages", "CheckJsonData")}
      == {"cleaned_power_data", "avg_consumption", "json_cleaned_data"})

trig = by_type["triggers"]["TR_Weekly"]["properties"]
rec = trig["typeProperties"]["recurrence"]
check("trigger is weekly on Friday", rec["frequency"] == "Week" and rec["schedule"]["weekDays"] == ["Friday"])
check("trigger runs the pipeline", trig["pipelines"][0]["pipelineReference"]["referenceName"] == "PL_SmartEnergy_Weekly")

print(f"\n{passed} passed, {failed} failed")
print("RESULT:", "PASS" if failed == 0 else "FAIL")
