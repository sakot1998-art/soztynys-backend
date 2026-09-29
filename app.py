import os,re,math,requests
from flask import Flask,request,jsonify,send_from_directory
from flask_cors import CORS
from urllib.parse import quote
app=Flask(__name__,static_folder="static",static_url_path="")
CORS(app, resources={r"/api/*": {"origins": "*"}})

def search(w,tbs=None):
    key=os.getenv("SERPAPI_KEY","").strip()
    if not key:return None,"API key табылмады"
    p={"engine":"google","q":f'"{w}"',"gl":"kz","hl":"kk","api_key":key,"num":10}
    if tbs:p["tbs"]=tbs
    try:r=requests.get("https://serpapi.com/search.json",params=p,timeout=20);d=r.json()
    except Exception as e:return None,str(e)
    if r.status_code!=200 or d.get("error"):return None,d.get("error",f"API {r.status_code}")
    return d,None

def total(d):
    try:return int((d.get("search_information") or {}).get("total_results") or 0)
    except:return 0

def freq(n):
    return round(max(0,min(100,(math.log10(max(n,1))-2)/6*100)))

def contexts(d):
    cats={"Жаңалық":0,"Білім":0,"Әдебиет/мәдениет":0,"Сөздік/анықтамалық":0,"Қазіргі web":0}
    rules={
      "Жаңалық":["news","жаңалық","inform","24.kz","tengri","zakon.kz"],
      "Білім":["edu","мектеп","университет","оқу","bilim","ustaz"],
      "Әдебиет/мәдениет":["әдеби","кітап","мәдениет","adebiportal","museum","кітапхана"],
      "Сөздік/анықтамалық":["sozdik","сөздік","dictionary","wikipedia","wiktionary"],
      "Қазіргі web":["youtube","instagram","tiktok","telegram","facebook","blog","forum","massaget"]
    }
    for x in (d.get("organic_results") or []):
        blob=(" ".join([str(x.get("title","")),str(x.get("snippet","")),str(x.get("link",""))])).lower()
        hits=[c for c,ks in rules.items() if any(k in blob for k in ks)]
        if hits:
            for c in hits:cats[c]+=1
        else:cats["Қазіргі web"]+=1
    s=max(1,sum(cats.values()))
    return {k:round(v/s*100) for k,v in cats.items()}

def dictionary_from_search(word):
    # Use Google result snippets restricted to SOZDIKQOR instead of scraping its navigation HTML.
    key=os.getenv("SERPAPI_KEY","").strip()
    url="https://sozdikqor.kz/search?q="+quote(word)
    if not key:return None,url
    p={"engine":"google","q":f'site:sozdikqor.kz "{word}"',"gl":"kz","hl":"kk","api_key":key,"num":10}
    try:
        d=requests.get("https://serpapi.com/search.json",params=p,timeout=20).json()
        for r in d.get("organic_results") or []:
            title=str(r.get("title","")); snip=re.sub(r"\s+"," ",str(r.get("snippet",""))).strip()
            link=str(r.get("link",""))
            if "sozdikqor.kz" in link and len(snip)>35:
                # Avoid obvious navigation-only snippets.
                low=snip.lower()
                if not all(x in low for x in ["меню","жүктеу"]):
                    return snip[:420],link
    except:pass
    return None,url

@app.get("/api/analyze")
def analyze():
    w=(request.args.get("word") or "").strip().lower()
    if not re.fullmatch(r"[а-яәіңғүұқөһё-]+",w,re.I):return jsonify(error="Бір қазақ сөзін енгізіңіз"),400
    base,e=search(w)
    if e:return jsonify(live=False,message=e)
    n=total(base); f=freq(n)
    recent,_=search(w,"qdr:y"); rn=total(recent or {})
    recent_share=min(1.0, rn/max(n,1))
    recent_signal=round(recent_share*100)
    # Balanced model: low total web presence must also mean low/medium activity.
    # 70% frequency + 30% recent-use signal.
    if n < 1000: ceiling=10
    elif n < 10000: ceiling=25
    elif n < 50000: ceiling=40
    elif n < 250000: ceiling=55
    elif n < 1000000: ceiling=70
    elif n < 10000000: ceiling=85
    else: ceiling=100
    activity=min(ceiling,round(0.75*f + 0.25*recent_signal))
    life=round(0.75*f + 0.25*activity)
    status="🟢 Белсенді" if life>=70 else ("🟡 Орташа таралған" if life>=40 else ("🟠 Сирек" if life>=20 else "🔴 Өте сирек"))
    ctx=contexts(base)
    meaning,dicturl=dictionary_from_search(w)
    dominant=max(ctx,key=ctx.get)
    if ctx["Сөздік/анықтамалық"]>=40 and activity<50:
        conclusion=f"«{w}» цифрлық кеңістікте сақталған, бірақ қазіргі белсенді қолданысы шектеулі. Нәтижелердің елеулі бөлігі сөздік/анықтамалық ортаға тиесілі."
    elif activity>=65:
        conclusion=f"«{w}» қазіргі цифрлық кеңістікте белсенді қолданылады. Іздеу нәтижелерінде басым орта — {dominant.lower()}."
    else:
        conclusion=f"«{w}» интернетте кездеседі. Іздеу нәтижелерінде басым орта — {dominant.lower()}."
    return jsonify(live=True,word=w,total_results=n,frequency_score=f,current_activity=activity,
      vitality=life,status=status,context=ctx,meaning=meaning,dictionary_url=dicturl,conclusion=conclusion,
      note="Google/SerpApi көрсеткіштері шамаланған. Қолданылу ортасы алғашқы іздеу нәтижелері негізінде есептеледі.")

@app.get("/")
def home():return send_from_directory("static","index.html")
if __name__=="__main__":
    port=int(os.getenv("PORT","8080"))
    app.run(host="0.0.0.0",port=port,debug=False,use_reloader=False,threaded=True)
