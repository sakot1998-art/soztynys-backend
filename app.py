import os, re, math, time, threading, requests
from urllib.parse import urlparse
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

app = Flask(__name__, static_folder='static', static_url_path='')
CORS(app, resources={r'/api/*': {'origins': '*'}})
SESSION = requests.Session()
CACHE = {}
LOCK = threading.Lock()
TTL = 12 * 3600
CATEGORIES = ('Жаңалықтар мен БАҚ', 'Білім және ғылым', 'Әдебиет және мәдениет', 'Әлеуметтік желілер', 'Сөздіктер мен анықтамалықтар', 'Басқа дереккөздер')

def google_search(word, tbs=None):
    key = os.getenv('SERPAPI_KEY', '').strip()
    if not key:
        return None, 'SERPAPI_KEY орнатылмаған'
    params = {'engine': 'google', 'q': '"' + word + '"', 'gl': 'kz', 'hl': 'kk', 'api_key': key, 'num': 10}
    if tbs:
        params['tbs'] = tbs
    try:
        r = SESSION.get('https://serpapi.com/search.json', params=params, timeout=22)
        d = r.json()
    except (requests.RequestException, ValueError):
        return None, 'Іздеу қызметімен байланыс орнатылмады'
    if r.status_code != 200 or d.get('error'):
        return None, str(d.get('error') or ('SerpApi HTTP ' + str(r.status_code)))
    return d, None

def count_results(data):
    if not isinstance(data, dict):
        return None
    raw = (data.get('search_information') or {}).get('total_results')
    if raw is None or isinstance(raw, bool):
        return None
    try:
        n = int(str(raw).replace(',', '').replace(' ', ''))
        return n if n >= 0 else None
    except (TypeError, ValueError):
        return None

def contexts(data):
    counts = dict.fromkeys(CATEGORIES, 0)
    results = (data or {}).get('organic_results') or []
    def match(host, names):
        return any(host == d or host.endswith('.' + d) for d in names)
    for item in results:
        host = (urlparse(str(item.get('link', ''))).hostname or '').lower().removeprefix('www.')
        blob = (str(item.get('title', '')) + ' ' + str(item.get('snippet', ''))).lower()
        if match(host, ('sozdikqor.kz', 'sozdik.kz', 'wiktionary.org', 'wikipedia.org', 'termincom.kz')):
            cat = CATEGORIES[4]
        elif match(host, ('instagram.com', 'tiktok.com', 'facebook.com', 'youtube.com', 'youtu.be', 't.me', 'threads.net', 'vk.com', 'reddit.com')):
            cat = CATEGORIES[3]
        elif match(host, ('adebiportal.kz', 'kitap.kz', 'museum.kz')) or any(k in blob for k in ('өлең', 'роман', 'шығарма', 'әдебиет', 'музей', 'мәдени мұра')):
            cat = CATEGORIES[2]
        elif match(host, ('edu.kz', 'qazcorpus.kz', 'bilimland.kz', 'ust.kz', 'ustaz.kz')) or any(k in blob for k in ('ғылыми мақала', 'университет', 'оқулық', 'зерттеу жұмысы')):
            cat = CATEGORIES[1]
        elif match(host, ('inform.kz', '24.kz', 'tengrinews.kz', 'zakon.kz', 'qazaqstan.tv', 'azattyq.org')) or any(k in blob for k in ('жаңалықтар', 'ақпарат агенттігі', 'хабарлады')):
            cat = CATEGORIES[0]
        else:
            cat = CATEGORIES[5]
        counts[cat] += 1
    total = sum(counts.values())
    if not total:
        return counts
    raw = {k: 100 * v / total for k, v in counts.items()}
    values = {k: int(v) for k, v in raw.items()}
    for k in sorted(counts, key=lambda k: raw[k] - values[k], reverse=True)[:100 - sum(values.values())]:
        values[k] += 1
    return values

def frequency(n):
    return round(max(0, min(100, (math.log10(max(n, 1)) - 2) / 6 * 100)))

def ceiling(n):
    for limit, cap in ((1000, 10), (10000, 25), (50000, 40), (250000, 55), (1000000, 70), (10000000, 85)):
        if n < limit:
            return cap
    return 100

def ai_definition(word):
    key = os.getenv('GEMINI_API_KEY', '').strip()
    if not key:
        return None, 'GEMINI_API_KEY орнатылмаған'
    configured = os.getenv('GEMINI_MODEL', 'gemini-2.5-flash').strip()
    models = list(dict.fromkeys((configured, 'gemini-2.5-flash', 'gemini-2.0-flash')))
    prompt = ('Қазақ тілінде 7-сынып оқушысына түсінікті етіп «' + word + '» сөзінің мағынасын 1–3 сөйлеммен түсіндір. '
              'Бірнеше кең тараған мағынасы болса, ажырат. Ойдан дәйексөз немесе шығарма үзіндісін қоспа. '
              'Мағынасына сенімді болмасаң, «Мағынасы анық емес» деп жауап бер. Тек түсіндірмені жаз.')
    for model in models:
        try:
            r = SESSION.post('https://generativelanguage.googleapis.com/v1beta/models/' + model + ':generateContent',
                headers={'x-goog-api-key': key},
                json={'contents': [{'parts': [{'text': prompt}]}], 'generationConfig': {'temperature': 0.1, 'maxOutputTokens': 500}}, timeout=30)
            if r.status_code == 404:
                continue
            if r.status_code != 200:
                return None, 'Gemini HTTP ' + str(r.status_code) + ' (модельге немесе кілтке қолжетімділікті тексеріңіз)'
            data = r.json()
            candidates = data.get('candidates') or []
            parts = ((candidates[0].get('content') or {}).get('parts') or []) if candidates else []
            answer = ' '.join(str(p.get('text', '')) for p in parts if isinstance(p, dict)).strip()
            if not answer or 'Мағынасы анық емес' in answer:
                return None, 'ЖИ сенімді түсіндірме бере алмады'
            return answer[:1000], None
        except (requests.RequestException, ValueError, TypeError, KeyError):
            return None, 'Gemini қызметімен байланыс орнатылмады'
    return None, 'Gemini моделі табылмады (HTTP 404). GEMINI_MODEL параметрін тексеріңіз'

def base_result(word):
    return dict(live=True, word=word, total_results=None, frequency_score=None,
                current_activity=None, vitality=None, status='⚪ Дерек нақтыланбады',
                context=dict.fromkeys(CATEGORIES, 0), meaning=None, dictionary_url='',
                conclusion='Google дерегі жеткіліксіз. Өміршеңдік индексі есептелмеді.',
                note='ЖИ түсіндірмесі автоматты жасалады және қате болуы мүмкін. Google/SerpApi сандары шамаланған.',
                data_quality='unverified', context_sample_size=0)

@app.get('/api/analyze')
def analyze():
    word = (request.args.get('word') or '').strip().lower()
    if not re.fullmatch(r'[а-яәіңғүұқөһё-]+', word, re.I):
        return jsonify(error='Бір қазақ сөзін енгізіңіз'), 400
    now = time.time()
    with LOCK:
        cached = CACHE.get(word)
        if cached and now - cached[0] < TTL:
            return jsonify(cached[1])
    result = base_result(word)
    base, error = google_search(word)
    if base:
        organic = base.get('organic_results') or []
        result['context'] = contexts(base)
        result['context_sample_size'] = len(organic)
        n = count_results(base)
        unreliable = n is None or n < len(organic) or (n <= 100 and len(organic) >= 5)
        if not unreliable:
            f = frequency(n)
            recent, recent_error = google_search(word, 'qdr:y')
            rn = count_results(recent) if recent and not recent_error else None
            recent_signal = round(min(1.0, rn / max(n, 1)) * 100) if rn is not None else None
            activity = min(ceiling(n), round(.75 * f + .25 * recent_signal)) if recent_signal is not None else min(ceiling(n), f)
            life = round(.75 * f + .25 * activity)
            result.update(total_results=n, frequency_score=f, current_activity=activity, vitality=life,
                          status=('🟢 Белсенді' if life >= 70 else '🟡 Орташа таралған' if life >= 40 else '🟠 Сирек' if life >= 20 else '🔴 Өте сирек'),
                          data_quality='estimated')
            dominant = max(result['context'], key=result['context'].get) if organic else 'дерек жеткіліксіз'
            result['conclusion'] = f'«{word}» сөзінің шартты цифрлық индексі {life}/100. Алғашқы іздеу нәтижелеріндегі басым сала — {dominant.lower()}. Бұл болашақты дәл болжау емес.'
        else:
            result['note'] += ' Google жалпы нәтиже санын сенімді қайтармады; индекс көрсетілмейді.'
    else:
        result['note'] += ' Іздеу қызметі: ' + str(error)
    meaning, ai_error = ai_definition(word)
    result['meaning'] = meaning or ('ЖИ түсіндірмесі қолжетімсіз: ' + str(ai_error))
    if ai_error:
        result['note'] += ' ЖИ қатесі: ' + str(ai_error)
    # Keep live=True so the existing Netlify page can display the AI explanation.
    # Unknown numeric values remain null; frontend must render them as "Дерек жоқ".
    with LOCK:
        CACHE[word] = (now, result)
    return jsonify(result)

@app.get('/')
def home():
    if os.path.isfile(os.path.join(app.static_folder, 'index.html')):
        return send_from_directory(app.static_folder, 'index.html')
    return jsonify(service='SÖZTYNYS API', status='online')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', '8080')), threaded=True)
