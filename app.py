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
    categories = {'Жаңалық': 0, 'Білім': 0, 'Әдебиет/мәдениет': 0, 'Сөздік/анықтамалық': 0, 'Қазіргі web': 0}
    rules = {
        'Жаңалық': ['news', 'жаңалық', 'inform', '24.kz', 'tengri', 'zakon.kz'],
        'Білім': ['edu', 'мектеп', 'университет', 'оқу', 'bilim', 'ustaz'],
        'Әдебиет/мәдениет': ['әдеби', 'кітап', 'мәдениет', 'adebiportal', 'museum', 'кітапхана'],
        'Сөздік/анықтамалық': ['sozdik', 'сөздік', 'dictionary', 'wikipedia', 'wiktionary'],
        'Қазіргі web': ['youtube', 'instagram', 'tiktok', 'telegram', 'facebook', 'blog', 'forum', 'massaget']
    }
    for item in data.get('organic_results') or []:
        blob = ' '.join(str(item.get(k, '')) for k in ('title', 'snippet', 'link')).lower()
        hits = [name for name, words in rules.items() if any(term in blob for term in words)]
        if hits:
            for name in hits:
                categories[name] += 1
        else:
            categories['Қазіргі web'] += 1
    total = max(1, sum(categories.values()))
    return {name: round(count / total * 100) for name, count in categories.items()}

def ceiling(n):
    for limit, cap in ((1000, 10), (10000, 25), (50000, 40), (250000, 55), (1000000, 70), (10000000, 85)):
        if n < limit:
            return cap
    return 100

def dictionary(word):
    # Do not use Google snippets as dictionary definitions: they may quote fiction.
    return VERIFIED_DEFINITIONS.get(word), 'https://sozdikqor.kz/search?q=' + quote(word)

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
    meaning, dictionary_url = dictionary(word)
    if unreliable:
        result = dict(live=True, word=word, total_results=None, frequency_score=None,
                      current_activity=None, vitality=None, status='⚪ Дерек нақтыланбады',
                      context=ctx, meaning=meaning, dictionary_url=dictionary_url,
                      conclusion='Google нәтижелерінің жалпы саны сенімді анықталмады. Сөздің белсенділігіне баға берілмейді.',
                      note='Бұл іздеу нәтижесі толық емес немесе күмәнді. Қате санның негізінде индекс есептелмеді.',
                      data_quality='unverified')
        # Do not cache unreliable counts; allow retry later.
        return jsonify(result)
    f = frequency(n)
    recent, recent_error = google_search(word, 'qdr:y')
    rn = count_results(recent) if not recent_error and recent else None
    recent_signal = round(min(1.0, rn / max(n, 1)) * 100) if rn is not None else 0
    activity = min(ceiling(n), round(.75 * f + .25 * recent_signal))
    life = round(.75 * f + .25 * activity)
    status = '🟢 Белсенді' if life >= 70 else ('🟡 Орташа таралған' if life >= 40 else ('🟠 Сирек' if life >= 20 else '🔴 Өте сирек'))
    dominant = max(ctx, key=ctx.get)
    if ctx['Сөздік/анықтамалық'] >= 40 and activity < 50:
        conclusion = f'«{word}» цифрлық кеңістікте кездеседі, бірақ қазіргі қолданысының көрсеткіші шектеулі. Нәтижелердің елеулі бөлігі сөздік/анықтамалық ортаға тиесілі.'
    elif activity >= 65:
        conclusion = f'«{word}» цифрлық кеңістікте белсенді көрінеді. Іздеу нәтижелерінде басым орта — {dominant.lower()}.'
    else:
        conclusion = f'«{word}» интернетте кездеседі. Іздеу нәтижелерінде басым орта — {dominant.lower()}.'
    result = dict(live=True, word=word, total_results=n, frequency_score=f,
                  current_activity=activity, vitality=life, status=status, context=ctx,
                  meaning=meaning, dictionary_url=dictionary_url, conclusion=conclusion,
                  note='Google/SerpApi саны шамаланған. Индекс — ғылыми дәлелденген болашақ болжамы емес. Сөздік анықтамасы тек тексерілген жағдайда көрсетіледі.',
                  data_quality='estimated')
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
