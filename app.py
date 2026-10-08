import os, re, math, time, threading, requests
from urllib.parse import urlparse
from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app, resources={r'/api/*': {'origins': '*'}})
SESSION = requests.Session()
CACHE = {}
LOCK = threading.Lock()
TTL = 12 * 3600
CATEGORIES = ['Жаңалықтар мен БАҚ','Білім және ғылым','Әдебиет және мәдениет','Әлеуметтік желілер','Сөздіктер мен анықтамалықтар','Басқа дереккөздер']


def serp(word):
    key = os.environ.get('SERPAPI_KEY', '').strip()
    if not key:
        return {}, 'SERPAPI_KEY орнатылмаған'
    try:
        r = SESSION.get('https://serpapi.com/search.json', params={
            'engine':'google','q':'"'+word+'"','hl':'kk','gl':'kz','api_key':key,'num':10}, timeout=22)
        d = r.json()
        if r.status_code != 200 or d.get('error'):
            return {}, str(d.get('error') or 'SerpApi HTTP '+str(r.status_code))[:160]
        return d, None
    except (requests.RequestException, ValueError):
        return {}, 'SerpApi байланысы уақытша қолжетімсіз'


def estimate(d):
    value = (d.get('search_information') or {}).get('total_results')
    if value is None or isinstance(value, bool):
        return None
    try:
        n = int(str(value).replace(',','').replace(' ',''))
        organic = d.get('organic_results') or []
        if n < 0 or (n < len(organic)) or (n <= 100 and len(organic) >= 5):
            return None
        return n
    except (ValueError, TypeError):
        return None


def classify(d):
    counts = dict.fromkeys(CATEGORIES, 0)
    for item in d.get('organic_results') or []:
        host = (urlparse(str(item.get('link',''))).hostname or '').lower().removeprefix('www.')
        blob = (str(item.get('title',''))+' '+str(item.get('snippet',''))).lower()
        def site(domains):
            return any(host == x or host.endswith('.'+x) for x in domains)
        if site(('sozdikqor.kz','sozdik.kz','wiktionary.org','wikipedia.org','termincom.kz')):
            category = CATEGORIES[4]
        elif site(('instagram.com','tiktok.com','youtube.com','youtu.be','facebook.com','threads.net','t.me','telegram.me','reddit.com','vk.com')):
            category = CATEGORIES[3]
        elif site(('adebiportal.kz','kitap.kz','museum.kz')) or any(s in blob for s in ('өлең','роман','әдебиет','мәдени мұра','көркем шығарма')):
            category = CATEGORIES[2]
        elif site(('edu.kz','bilimland.kz','ust.kz','ustaz.kz','qazcorpus.kz')) or any(s in blob for s in ('ғылыми мақала','университет','оқулық','зерттеу жұмысы')):
            category = CATEGORIES[1]
        elif site(('inform.kz','24.kz','tengrinews.kz','zakon.kz','qazaqstan.tv','azattyq.org')) or any(s in blob for s in ('жаңалықтар','ақпарат агенттігі','хабарлады')):
            category = CATEGORIES[0]
        else:
            category = CATEGORIES[5]
        counts[category] += 1
    count = sum(counts.values())
    if not count:
        return {x:0 for x in CATEGORIES}, 0
    raw = {x:100*counts[x]/count for x in CATEGORIES}
    pct = {x:int(raw[x]) for x in CATEGORIES}
    for x in sorted(CATEGORIES,key=lambda x:raw[x]-pct[x],reverse=True)[:100-sum(pct.values())]:
        pct[x] += 1
    return pct, count


@app.get('/api/health')
def health():
    return jsonify(ok=True, serpapi_configured=bool(os.getenv('SERPAPI_KEY')), version='3.1-serpapi')


@app.get('/api/analyze')
def analyze():
    word = (request.args.get('word') or '').strip().lower()
    if not re.fullmatch(r'[а-яәіңғүұқөһё-]{1,45}',word,re.I):
        return jsonify(error='Бір қазақ сөзін енгізіңіз'),400
    with LOCK:
        cached = CACHE.get(word)
        if cached and time.time()-cached[0]<TTL:
            return jsonify(cached[1])
    data, search_error = serp(word)
    ctx, sample_size = classify(data)
    n = estimate(data)
    f = round(max(0,min(100,(math.log10(max(n,1))-2)/6*100))) if n is not None else None
    # This is a one-time proxy score, NOT a historical trend or future prediction.
    activity = f
    vitality = f
    if vitality is None:
        status = '⚪ Дерек нақтыланбады'
        conclusion = 'Google жалпы нәтиже санын сенімді қайтармады. Өміршеңдік индексі есептелмейді.'
    else:
        status = '🟢 Жоғары таралған' if vitality>=70 else ('🟡 Орташа таралған' if vitality>=40 else '🟠 Шектеулі таралған')
        conclusion = 'Бұл — сөздің интернеттегі таралуына негізделген шартты баға. Болашақта сөздің азаятынын немесе көбейетінін дәлелдемейді.'
    result = dict(live=True,word=word,total_results=n,frequency_score=f,current_activity=activity,
                  vitality=vitality,status=status,context=ctx,context_sample_size=sample_size,
                  meaning=None,meaning_error=None,dictionary_url='https://sozdikqor.kz/search?q='+word,
                  conclusion=conclusion,search_error=search_error,
                  note='Google/SerpApi нәтижелерінің жалпы саны шамаланған және кейде қолжетімсіз. Қолданылу салалары алғашқы іздеу нәтижелері бойынша жіктеледі.',
                  data_quality='estimated' if n is not None else 'unverified')
    if not search_error and (n is not None or sample_size > 0):
        with LOCK:
            CACHE[word]=(time.time(),result)
    return jsonify(result)


@app.get('/')
def home():
    return jsonify(service='SÖZTYNYS API',status='online',version='3.1-serpapi')

if __name__ == '__main__':
    app.run(host='0.0.0.0',port=int(os.getenv('PORT','8080')))
