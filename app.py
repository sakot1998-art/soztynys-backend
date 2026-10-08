import os, re, math, time, threading, requests
from urllib.parse import quote
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

app = Flask(__name__, static_folder='static', static_url_path='')
CORS(app, resources={r'/api/*': {'origins': '*'}})
SESSION = requests.Session()
CACHE = {}
LOCK = threading.Lock()
TTL = 12 * 3600

# Verified definitions can be added here only after checking a reliable dictionary.
VERIFIED_DEFINITIONS = {}

def google_search(word, tbs=None):
    key = os.getenv('SERPAPI_KEY', '').strip()
    if not key:
        return None, 'SerpApi кілті орнатылмаған'
    params = {'engine': 'google', 'q': '"' + word + '"', 'gl': 'kz', 'hl': 'kk', 'api_key': key, 'num': 10}
    if tbs:
        params['tbs'] = tbs
    try:
        response = SESSION.get('https://serpapi.com/search.json', params=params, timeout=25)
        data = response.json()
    except (requests.RequestException, ValueError):
        return None, 'Іздеу қызметімен байланыс орнатылмады'
    if response.status_code != 200 or data.get('error'):
        return None, str(data.get('error') or 'Іздеу қызметінің қатесі')
    return data, None

def count_results(data):
    info = data.get('search_information') or {}
    value = info.get('total_results')
    if value is None:
        return None
    try:
        n = int(str(value).replace(',', '').replace(' ', ''))
        return n if n >= 0 else None
    except (TypeError, ValueError):
        return None

def frequency(n):
    return round(max(0, min(100, (math.log10(max(n, 1)) - 2) / 6 * 100)))

def contexts(data):
    """Classify each visible search result once; not the whole internet."""
    categories = {
        'Жаңалықтар мен БАҚ': 0,
        'Білім және ғылым': 0,
        'Әдебиет және мәдениет': 0,
        'Әлеуметтік желілер': 0,
        'Сөздіктер мен анықтамалықтар': 0,
        'Басқа дереккөздер': 0,
    }
    from urllib.parse import urlparse
    for item in (data.get('organic_results') or []):
        link = str(item.get('link', '')).lower()
        host = (urlparse(link).hostname or '').removeprefix('www.')
        blob = ' '.join(str(item.get(k, '')) for k in ('title', 'snippet')).lower()
        def domain_matches(names):
            return any(host == name or host.endswith('.' + name) for name in names)
        # Prioritize site identity, then content indicators.
        if domain_matches(('sozdikqor.kz','sozdik.kz','wiktionary.org','wikipedia.org','termincom.kz')):
            category = 'Сөздіктер мен анықтамалықтар'
        elif domain_matches(('instagram.com','tiktok.com','facebook.com','youtube.com','youtu.be','telegram.me','t.me','threads.net','vk.com','reddit.com')):
            category = 'Әлеуметтік желілер'
        elif domain_matches(('adebiportal.kz','kitap.kz','massaget.kz','museum.kz')) or any(k in blob for k in ('өлең','роман','шығарма','әдебиет','музей','мәдени мұра')):
            category = 'Әдебиет және мәдениет'
        elif domain_matches(('edu.kz','qazcorpus.kz','bilimland.kz','ust.kz','ustaz.kz')) or any(k in blob for k in ('ғылыми мақала','университет','оқулық','зерттеу жұмысы')):
            category = 'Білім және ғылым'
        elif domain_matches(('inform.kz','24.kz','tengrinews.kz','zakon.kz','qazaqstan.tv','azattyq.org')) or any(k in blob for k in ('жаңалықтар','ақпарат агенттігі','хабарлады')):
            category = 'Жаңалықтар мен БАҚ'
        else:
            category = 'Басқа дереккөздер'
        categories[category] += 1
    total = sum(categories.values())
    if total == 0:
        return {name: 0 for name in categories}
    # Largest-remainder method: rounded percentages sum exactly to 100.
    raw = {k: 100 * v / total for k, v in categories.items()}
    values = {k: int(v) for k, v in raw.items()}
    remainder = 100 - sum(values.values())
    for k in sorted(categories, key=lambda k: raw[k] - values[k], reverse=True)[:remainder]:
        values[k] += 1
    return values

def ceiling(n):
    for limit, cap in ((1000, 10), (10000, 25), (50000, 40), (250000, 55), (1000000, 70), (10000000, 85)):
        if n < limit:
            return cap
    return 100

def ai_definition(word):
    """Generate an AI explanation, never label it a verified dictionary quotation."""
    key = os.getenv('GEMINI_API_KEY', '').strip()
    if not key:
        return None, 'ЖИ түсіндірмесі қолжетімсіз: GEMINI_API_KEY орнатылмаған'
    model = os.getenv('GEMINI_MODEL', 'gemini-2.5-flash')
    endpoint = 'https://generativelanguage.googleapis.com/v1beta/models/' + model + ':generateContent'
    prompt = (
        'Қазақ тілінің оқушыға түсінікті сөз түсіндірушісі бол. '
        'Төмендегі бір сөздің мағынасын қазақша 1-3 қысқа сөйлеммен түсіндір. '
        'Егер бірнеше кең таралған мағынасы болса, оларды ажырат. '
        'Ойдан дерек, шығарма үзіндісі немесе жалған дәйексөз қоспа. '
        'Мағынасына сенімді болмасаң, дәл «Мағынасы анық емес» деп жауап бер. '
        'Тек түсіндірмені жаз. Сөз: ' + word
    )
    try:
        response = SESSION.post(endpoint, headers={'x-goog-api-key': key},
            json={'contents': [{'parts': [{'text': prompt}]}],
                  'generationConfig': {'temperature': 0.1, 'maxOutputTokens': 350}}, timeout=35)
        if response.status_code != 200:
            return None, 'ЖИ қызметі уақытша қолжетімсіз (HTTP ' + str(response.status_code) + ')'
        data = response.json()
        candidates = data.get('candidates') or []
        parts = ((candidates[0].get('content') or {}).get('parts') or []) if candidates else []
        text = ' '.join(p.get('text', '') for p in parts if isinstance(p, dict)).strip()
        if not text or 'Мағынасы анық емес' in text:
            return None, 'ЖИ сөздің мағынасын сенімді түсіндіре алмады'
        return text[:1000], None
    except (requests.RequestException, ValueError, KeyError, TypeError):
        return None, 'ЖИ қызметімен байланыс орнатылмады'

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
    base, error = google_search(word)
    if error:
        return jsonify(live=False, message=error), 503
    n = count_results(base)
    organic = base.get('organic_results') or []
    # Google estimated counts can be extremely low even for frequent words.
    # Do not assign a misleading low score when the count looks unreliable.
    unreliable = n is None or (n < len(organic)) or (n <= 100 and len(organic) >= 5)
    ctx = contexts(base)
    meaning, ai_error = ai_definition(word)
    if not meaning:
        meaning = ai_error or 'ЖИ түсіндірмесі әзірге қолжетімсіз'
    dictionary_url = ''
    if unreliable:
        result = dict(live=False, word=word, total_results=None, frequency_score=None,
                      current_activity=None, vitality=None, status='⚪ Дерек нақтыланбады',
                      context=ctx, meaning=meaning, dictionary_url=dictionary_url,
                      conclusion='Google нәтижелерінің жалпы саны сенімді анықталмады. Сөздің белсенділігіне баға берілмейді.',
                      note='ЖИ түсіндірмесі автоматты түрде жасалады, қате болуы мүмкін. ' + (ai_error or '') + ' Google нәтижесі күмәнді болғандықтан индекс есептелмеді.',
                      data_quality='unverified')
        # Do not cache unreliable counts; allow retry later.
        return jsonify(result), 503
    f = frequency(n)
    recent, recent_error = google_search(word, 'qdr:y')
    rn = count_results(recent) if not recent_error and recent else None
    recent_signal = round(min(1.0, rn / max(n, 1)) * 100) if rn is not None else None
    activity = min(ceiling(n), round(.75 * f + .25 * recent_signal)) if recent_signal is not None else min(ceiling(n), f)
    life = round(.75 * f + .25 * activity)
    status = '🟢 Белсенді' if life >= 70 else ('🟡 Орташа таралған' if life >= 40 else ('🟠 Сирек' if life >= 20 else '🔴 Өте сирек'))
    dominant = max(ctx, key=ctx.get) if any(ctx.values()) else 'дерек жеткіліксіз'
    if ctx['Сөздіктер мен анықтамалықтар'] >= 40 and activity < 50:
        conclusion = f'«{word}» цифрлық кеңістікте кездеседі, бірақ қазіргі қолданысының көрсеткіші шектеулі. Нәтижелердің елеулі бөлігі сөздіктер мен анықтамалықтарға тиесілі.'
    elif activity >= 65:
        conclusion = f'«{word}» цифрлық кеңістікте белсенді көрінеді. Іздеу нәтижелерінде басым орта — {dominant.lower()}.'
    else:
        conclusion = f'«{word}» интернетте кездеседі. Іздеу нәтижелерінде басым орта — {dominant.lower()}.'
    result = dict(live=True, word=word, total_results=n, frequency_score=f,
                  current_activity=activity, vitality=life, status=status, context=ctx,
                  meaning=meaning, dictionary_url=dictionary_url, conclusion=conclusion,
                  note='ЖИ түсіндірмесі автоматты түрде жасалды, қате болуы мүмкін. ' + (ai_error or '') + ' Google/SerpApi саны шамаланған; индекс болашақты дәл болжамайды.',
                  data_quality='estimated', context_sample_size=len(base.get('organic_results') or []))
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
