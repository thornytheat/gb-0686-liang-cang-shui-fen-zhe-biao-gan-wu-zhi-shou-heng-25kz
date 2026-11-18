from flask import render_template,request
from app import store
from app.pipeline import run
from app.config import TITLE,MODULES,STAGES,COLUMNS,SAMPLES,DEFAULTS
from app.modules.m8_delivery import service as delivery_service
def view(stage):
    if not 1<=stage<=7: return "页面不存在",404
    run_id=request.args.get("run")
    result=store.fetch(run_id) if run_id else run()
    sample=(SAMPLES/"dirty.csv").read_text(encoding="utf-8-sig")
    data=result["stages"][stage-1]
    return render_template(f"m{stage}.html",title=TITLE,modules=MODULES,stage=stage,result=result,data=data,
                           sample=sample,defaults=DEFAULTS,columns=COLUMNS,history=store.history())
def delivery_view(run_id):
    result=store.fetch(run_id) if run_id else run()
    rid=result["manifest"]["run_id"]
    try:
        overview=delivery_service.overview(rid)
    except Exception:
        overview={"run_id":rid,"bins":[],"vouchers":[],
                  "totals":{"target_amount":"0.00","opened_amount":"0.00","diff_amount":"0.00",
                            "all_closed":False,"all_matched":False}}
    return render_template("m8.html",title=TITLE,modules=MODULES,stage=8,result=result,
                           overview=overview,columns=COLUMNS,history=store.history())
