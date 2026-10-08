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


def gemini(word):
    key = os.environ.get('GEMINI_API_KEY','').strip()
    if not key:
        return None, 'GEMINI_API_KEY орнатылмаған'
    headers = {'x-goog-api-key':key}
    try:
        r = SESSION.get('https://generativelanguage.googleapis.com/v1beta/models', headers=headers, timeout=14)
        if r.status_code != 200:
            return None, 'Gemini модельдер тізімі қолжетімсіз (HTTP '+str(r.status_code)+')'
        models = [m['name'].split('/',1)[1] for m in r.json().get('models',[]) if 'generateContent' in m.get('supportedGenerationMethods',[]) and m.get('name','').startswith('models/')]
        preferred = os.environ.get('GEMINI_MODEL','').strip().replace('models/','')
        candidates = ([preferred] if preferred in models else []) + [m for m in models if 'flash' in m and 'image' not in m and 'preview' not in m and 'tts' not in m]
        candidates += [m for m in models if 'flash' in m]
        candidates = list(dict.fromkeys(candidates))
        if not candidates:
            return None, 'Gemini мәтіндік моделі қолжетімсіз'
        prompt = ('Қазақ тіліндегі мына бір сөздің мағынасын 7-сынып оқушысына 1-3 қысқа сөйлеммен түсіндір. '
                  'Бірнеше кең таралған мағынасы болса, ажырат. Жалған дәйексөз қоспа. '
                  'Белгісіз сөз болса, «Мағынасы анық емес» деп жаз. Тек түсіндірме бер. Сөз: '+word)
        last_error = 'Gemini жауабы алынбады'
        for model in candidates[:3]:
            url = 'https://generativelanguage.googleapis.com/v1beta/models/'+model+':generateContent'
            response = SESSION.post(url, headers=headers, json={'contents':[{'parts':[{'text':prompt}]}],
                'generationConfig':{'temperature':0.1,'maxOutputTokens':500}},timeout=28)
            if response.status_code in (404, 400):
                last_error = 'Gemini моделі қолжетімсіз (HTTP '+str(response.status_code)+')'
                continue
            if response.status_code != 200:
                return None, 'Gemini қатесі (HTTP '+str(response.status_code)+')'
            parts = ((response.json().get('candidates') or [{}])[0].get('content') or {}).get('parts') or []
            text = ' '.join(str(p.get('text','')) for p in parts if isinstance(p,dict)).strip()
            if text and 'Мағынасы анық емес' not in text:
                return text[:900], None
            last_error = 'ЖИ сенімді түсіндірме бере алмады'
        return None, last_error
    except (requests.RequestException, ValueError, KeyError, IndexError, TypeError):
        return None, 'Gemini байланысы уақытша қолжетімсіз'


@app.get('/api/health')
def health():
    return jsonify(ok=True, serpapi_configured=bool(os.getenv('SERPAPI_KEY')), gemini_configured=bool(os.getenv('GEMINI_API_KEY')))


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
    meaning, ai_error = gemini(word)
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
                  meaning=meaning,meaning_error=ai_error,dictionary_url='',
                  conclusion=conclusion,search_error=search_error,
                  note='ЖИ түсіндірмесі қате болуы мүмкін. Google нәтижесі шамаланған. Қолданылу салалары тек алғашқы іздеу нәтижелеріне негізделеді.',
                  data_quality='estimated' if n is not None else 'unverified')
    with LOCK:
        CACHE[word]=(time.time(),result)
    return jsonify(result)


@app.get('/')
def home():
    return jsonify(service='SÖZTYNYS API',status='online',version='3.0')

if __name__ == '__main__':
    app.run(host='0.0.0.0',port=int(os.getenv('PORT','8080')))
