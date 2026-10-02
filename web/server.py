"""Serveur local "CalTrack" : suivi des calories et des macros.

Lance un serveur HTTP accessible depuis tout le réseau local (wifi de la maison).
Aucune dépendance : uniquement la bibliothèque standard de Python.

    python server.py            -> port 8001
    python server.py 8080       -> autre port
"""

import json
import os
import re
import socket
import sys
import threading
import time
import unicodedata
import uuid
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, urlencode
from urllib.error import HTTPError
from urllib.request import Request, urlopen

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FOODS_FILE = os.path.join(BASE_DIR, "foods.json")  # base d'aliments (Ciqual / USDA)
STATIC_DIR = os.path.join(BASE_DIR, "static")
LOCK = threading.Lock()

# Chaque profil a son propre journal, ses objectifs et ses aliments perso,
# dans son fichier data-<identifiant>.json. La base d'aliments est commune.
# La liste des profils est dans profiles.json, créée depuis l'appli.
PROFILES_FILE = os.path.join(BASE_DIR, "profiles.json")

# Avant la première modification de chaque jour, le fichier d'un profil est
# copié dans backups/data-<identifiant>-<date>.json (état de la veille au soir).
# On garde les BACKUP_KEEP plus récentes de chaque profil.
BACKUP_DIR = os.path.join(BASE_DIR, "backups")
BACKUP_KEEP = 14

# Valeurs nutritionnelles, toujours exprimées pour 100 g.
NUTRIENTS = ["calories", "proteins", "carbs", "sugars", "fat", "saturated_fat", "fiber", "salt"]
MEALS = ["petit-dejeuner", "dejeuner", "diner", "collation"]

DEFAULT_DATA = {
    "goals": {"calories": 2000, "proteins": 100, "carbs": 250, "fat": 70},
    "journal": {},       # {"2026-09-29": [entrée, ...]}
    "customFoods": [],   # aliments créés à la main ou enregistrés depuis Open Food Facts
}


# ---------------------------------------------------------------- données

def data_file(user):
    return os.path.join(BASE_DIR, f"data-{user}.json")


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_profiles():
    if os.path.exists(PROFILES_FILE):
        with open(PROFILES_FILE, encoding="utf-8") as f:
            return json.load(f)
    # Installation d'avant les profils : on les retrouve d'après les fichiers de données
    profiles = []
    for name in sorted(os.listdir(BASE_DIR)):
        m = re.fullmatch(r"data-([a-z0-9-]+)\.json", name)
        if m:
            profiles.append({"id": m.group(1), "name": m.group(1).replace("-", " ").title()})
    if profiles:
        write_json(PROFILES_FILE, profiles)
    return profiles


def create_profile(name):
    name = " ".join(str(name or "").split())[:30]
    if not name:
        raise ValueError("Indiquez un prénom")
    profiles = load_profiles()
    if any(fold(p["name"]) == fold(name) for p in profiles):
        raise ValueError("Ce profil existe déjà")
    # identifiant sans accents ni espaces : il sert de nom de fichier
    base = re.sub(r"[^a-z0-9]+", "-", fold(name)).strip("-") or "profil"
    ids, pid, n = {p["id"] for p in profiles}, base, 2
    while pid in ids or os.path.exists(data_file(pid)):
        pid, n = f"{base}-{n}", n + 1
    profile = {"id": pid, "name": name}
    save(pid, DEFAULT_DATA)
    write_json(PROFILES_FILE, profiles + [profile])
    return profile


def load(user):
    if not os.path.exists(data_file(user)):
        save(user, DEFAULT_DATA)
    with open(data_file(user), encoding="utf-8") as f:
        data = json.load(f)
    for key, value in DEFAULT_DATA.items():
        data.setdefault(key, json.loads(json.dumps(value)))
    return data


def backup_file(user, day):
    return os.path.join(BACKUP_DIR, f"data-{user}-{day}.json")


def list_backups(user):
    """Dates des sauvegardes automatiques d'un profil, de la plus récente à la plus ancienne."""
    if not os.path.isdir(BACKUP_DIR):
        return []
    pattern = re.compile(rf"data-{re.escape(user)}-(\d{{4}}-\d{{2}}-\d{{2}})\.json")
    return sorted((m.group(1) for m in map(pattern.fullmatch, os.listdir(BACKUP_DIR)) if m), reverse=True)


def backup_daily(user, today=None):
    """Copie le fichier du profil s'il n'a pas encore été sauvegardé aujourd'hui.

    Ne s'arrête jamais sur une erreur : une sauvegarde ratée ne doit pas
    empêcher d'enregistrer ce qu'on vient de manger.
    """
    src = data_file(user)
    today = today or time.strftime("%Y-%m-%d")
    try:
        if not os.path.exists(src) or os.path.exists(backup_file(user, today)):
            return
        os.makedirs(BACKUP_DIR, exist_ok=True)
        with open(src, "rb") as f:
            content = f.read()
        tmp = backup_file(user, today) + ".tmp"
        with open(tmp, "wb") as f:
            f.write(content)
        os.replace(tmp, backup_file(user, today))
        for old in list_backups(user)[BACKUP_KEEP:]:
            os.remove(backup_file(user, old))
    except OSError as exc:
        print(f"Sauvegarde automatique impossible pour {user} : {exc}", file=sys.stderr)


def save(user, data):
    backup_daily(user)
    tmp = data_file(user) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, data_file(user))


def num(value, default=0.0):
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return default


def clean_food(body, food_id=None, default_source="manual"):
    name = str(body.get("name", "")).strip()
    if not name:
        raise ValueError("L'aliment doit avoir un nom")
    food = {
        "id": food_id or str(body.get("id") or "") or "manual_" + uuid.uuid4().hex[:10],
        "name": name,
        "detail": str(body.get("detail", "")).strip(),
        "category": str(body.get("category") or "autre"),
        "source": body.get("source") if body.get("source") in ("base", "manual", "online") else default_source,
    }
    for k in NUTRIENTS:
        food[k] = max(0.0, num(body.get(k)))
    return food


def clean_entry(body):
    grams = num(body.get("grams"))
    if grams <= 0:
        raise ValueError("Quantité invalide")
    meal = body.get("meal") if body.get("meal") in MEALS else "collation"
    food = body.get("food") or {}
    food = clean_food(food, food_id=str(food.get("id") or ""), default_source="base")
    return {
        "id": uuid.uuid4().hex[:12],
        "food": food,          # valeurs pour 100 g, copiées : le journal ne change
        "grams": grams,        # pas si l'aliment est modifié ou supprimé ensuite
        "meal": meal,
        "addedAt": int(time.time() * 1000),
    }


def find_entry(data, entry_id):
    for day, entries in data["journal"].items():
        for e in entries:
            if e["id"] == entry_id:
                return day, e
    raise KeyError("Entrée introuvable")


def copy_meal(data, body):
    """Recopie les aliments d'un repas d'un jour (`from`) vers un autre (`to`).

    Les copies sont de nouvelles entrées (nouvel identifiant) : modifier ou
    supprimer l'une ne touche pas l'autre. Renvoie le nombre d'aliments copiés.
    """
    body = body or {}
    src, dst = str(body.get("from", "")), str(body.get("to", ""))
    for d in (src, dst):
        time.strptime(d, "%Y-%m-%d")  # ValueError si la date est invalide
    if src == dst:
        raise ValueError("Choisissez un autre jour")
    meal = body.get("meal")
    if meal not in MEALS:
        raise ValueError("Repas inconnu")
    to_meal = body.get("toMeal") if body.get("toMeal") in MEALS else meal
    source = [e for e in data["journal"].get(src, []) if e["meal"] == meal]
    if not source:
        raise ValueError("Rien à copier pour ce repas")
    now = int(time.time() * 1000)
    target = data["journal"].setdefault(dst, [])
    for i, e in enumerate(sorted(source, key=lambda e: e.get("addedAt", 0))):
        target.append({
            "id": uuid.uuid4().hex[:12],
            "food": json.loads(json.dumps(e["food"])),
            "grams": e["grams"],
            "meal": to_meal,
            "addedAt": now + i,  # garde l'ordre d'origine dans le repas
        })
    return len(source)


# ---------------------------------------------------------------- Open Food Facts

OFF_HEADERS = {"User-Agent": "CalTrack-web/1.0 (usage personnel)", "Accept": "application/json"}
OFF_FIELDS = "code,product_name,product_name_fr,product_name_en,brands,categories_tags,nutriments"

# Même correspondance que le proxy Vercel de l'appli mobile.
OFF_CATEGORIES = [
    (("poulet", "volaille", "chicken"), "volaille"),
    (("viande", "boeuf", "porc"), "viande"),
    (("charcuterie", "jambon"), "charcuterie"),
    (("poisson", "fish", "saumon"), "poisson"),
    (("oeuf", "egg"), "oeuf"),
    (("fromage", "cheese"), "fromage"),
    (("laitage", "lait", "yaourt"), "laitier"),
    (("pate", "riz", "pasta"), "feculents"),
    (("pain", "bread"), "pain"),
    (("legume", "vegetable"), "legumes"),
    (("fruit",), "fruits"),
    (("legumineuse", "lentille"), "legumineuses"),
    (("noix", "nuts", "amande"), "noix"),
    (("huile", "beurre"), "matieresgr"),
    (("boisson", "drink", "jus"), "boissons"),
    (("alcool", "biere", "vin"), "alcool"),
    (("sucre", "chocolat", "sweet"), "sucre"),
    (("sauce", "condiment"), "sauces"),
    (("plat", "prepared"), "platcuisine"),
    (("fast-food", "burger", "pizza"), "fastfood"),
]


def off_category(tags):
    joined = " ".join(tags or [])
    for words, cat in OFF_CATEGORIES:
        if any(w in joined for w in words):
            return cat
    return "autre"


def first_brand(brands):
    # L'ancienne recherche renvoie "Marque A, Marque B", la nouvelle une liste
    if isinstance(brands, list):
        brands = ",".join(b for b in brands if b)
    return (brands or "").split(",")[0].strip()


def off_product(p):
    n = p.get("nutriments") or {}
    kcal = n.get("energy-kcal_100g")
    if kcal is None and n.get("energy_100g") is not None:
        kcal = float(n["energy_100g"]) / 4.184  # kJ -> kcal
    food = {
        "id": "off_" + str(p.get("code") or p.get("_id") or uuid.uuid4().hex[:10]),
        "name": (p.get("product_name_fr") or p.get("product_name") or p.get("product_name_en") or "").strip(),
        "detail": first_brand(p.get("brands")),
        "category": off_category(p.get("categories_tags")),
        "source": "online",
        "calories": round(num(kcal)),
    }
    for k, off_key in [("proteins", "proteins_100g"), ("carbs", "carbohydrates_100g"),
                       ("sugars", "sugars_100g"), ("fat", "fat_100g"),
                       ("saturated_fat", "saturated-fat_100g"), ("fiber", "fiber_100g"),
                       ("salt", "salt_100g")]:
        food[k] = round(num(n.get(off_key)), 1)
    return food


def off_get(url, timeout=15):
    with urlopen(Request(url, headers=OFF_HEADERS), timeout=timeout) as res:
        text = res.read().decode("utf-8", "replace")
    if text.lstrip().startswith("<"):
        raise ValueError("réponse HTML")
    return json.loads(text)


def clean_products(raw):
    products = [off_product(p) for p in raw]
    return [p for p in products if len(p["name"]) > 1 and p["calories"] > 0]


def off_search(query, page):
    # 1) Nouveau moteur de recherche d'Open Food Facts : rapide et fiable,
    #    limité aux produits vendus en France.
    words = "".join(c if c.isalnum() or c in " -'" else " " for c in query).split()
    params = urlencode({
        "q": " ".join(words) + ' countries_tags:"en:france"', "page_size": 24, "page": page,
        "langs": "fr", "fields": OFF_FIELDS,
    })
    last_error = None
    try:
        data = off_get(f"https://search.openfoodfacts.org/search?{params}")
        return {"products": clean_products(data.get("hits") or []), "count": data.get("count", 0), "page": page}
    except Exception as exc:  # on tente l'ancienne recherche
        last_error = exc

    # 2) Ancienne recherche, souvent indisponible (erreur 503), gardée en secours
    params = urlencode({
        "search_terms": query, "search_simple": 1, "action": "process", "json": 1,
        "page_size": 24, "page": page, "fields": OFF_FIELDS, "lc": "fr",
    })
    for base in ("https://fr.openfoodfacts.org", "https://world.openfoodfacts.org"):
        try:
            data = off_get(f"{base}/cgi/search.pl?{params}")
            # Produits avec un nom en français d'abord (la base est mondiale)
            raw = sorted(data.get("products") or [], key=lambda p: not p.get("product_name_fr"))
            products = clean_products(raw)
            return {"products": products, "count": data.get("count", len(products)), "page": page}
        except Exception as exc:  # réseau, délai, JSON invalide : on tente l'autre adresse
            last_error = exc
    if getattr(last_error, "code", None) in (429, 503):
        raise ConnectionError("Open Food Facts est surchargé : réessayez dans une minute")
    raise ConnectionError(f"Open Food Facts injoignable ({last_error})")


def off_barcode(code):
    code = "".join(c for c in code if c.isdigit())
    if not code:
        raise ValueError("Code-barres invalide")
    data = off_get(f"https://world.openfoodfacts.org/api/v2/product/{code}?fields={OFF_FIELDS}")
    if data.get("status") != 1:
        return None
    return off_product(data["product"])


# ---------------------------------------------------------------- photo du repas (modèle local)

# Modèle d'analyse d'images (Qwen3.5-9B) qui tourne sur la carte graphique de
# ce PC avec llama.cpp : les photos ne quittent jamais la maison.
# llama.cpp est lancé à la première photo et arrêté après VISION_IDLE secondes
# sans photo, pour libérer la carte graphique (jeux, etc.).
LLAMA_EXE = os.path.join(os.environ.get("LOCALAPPDATA", ""), "llama.cpp", "llama-server.exe")
MODEL_DIR = os.path.join(os.path.expanduser("~"), ".lmstudio", "models", "lmstudio-community", "Qwen3.5-9B-GGUF")
VISION_MODEL = os.path.join(MODEL_DIR, "Qwen3.5-9B-Q6_K.gguf")
VISION_MMPROJ = os.path.join(MODEL_DIR, "mmproj-Qwen3.5-9B-BF16.gguf")
VISION_PORT = 8081
VISION_URL = f"http://127.0.0.1:{VISION_PORT}"
VISION_IDLE = 600

VISION_PROMPT = """Tu es nutritionniste. Analyse la photo de ce repas et liste chaque aliment visible séparément.
Pour chaque aliment :
- name : nom court et générique en français, sans parenthèses (ex. "Riz blanc", "Blanc de poulet", "Pâtes", "Haricots verts", "Oeuf frit") ;
- state : "cuit" ou "cru", selon ce qui est dans l'assiette ;
- grams : poids estimé de la portion dans l'assiette, en grammes (aide-toi de la taille de l'assiette et des couverts) ;
- calories, proteins, carbs, sugars, fat, fiber : valeurs POUR 100 g de cet aliment (pas pour la portion), comme sur une étiquette. Exemple : huile = 900 kcal et 100 g de lipides pour 100 g.
Si de la matière grasse de cuisson est visible (brillance, friture), ajoute-la à part avec son type ("Huile d'olive", "Huile de tournesol" ou "Beurre").
S'il n'y a pas de nourriture, renvoie une liste vide."""

VISION_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"}, "state": {"type": "string"},
                    "grams": {"type": "number"}, "calories": {"type": "number"},
                    "proteins": {"type": "number"}, "carbs": {"type": "number"},
                    "sugars": {"type": "number"}, "fat": {"type": "number"},
                    "fiber": {"type": "number"},
                },
                "required": ["name", "state", "grams", "calories", "proteins", "carbs", "sugars", "fat", "fiber"],
            },
        },
    },
    "required": ["items"],
}


class VisionEngine:
    """Lance llama.cpp à la demande et l'arrête quand il ne sert plus."""

    def __init__(self):
        self.proc = None
        self.last_use = 0.0
        self.busy = 0
        self.lock = threading.Lock()

    @staticmethod
    def healthy():
        try:
            with urlopen(VISION_URL + "/health", timeout=2) as res:
                return res.status == 200
        except OSError:  # éteint, ou encore en train de charger le modèle (503)
            return False

    def ensure_started(self):
        import subprocess
        with self.lock:
            if self.healthy():
                return
            for path, what in [(LLAMA_EXE, "llama.cpp"), (VISION_MODEL, "Le modèle Qwen3.5"), (VISION_MMPROJ, "Le module d'images du modèle")]:
                if not os.path.exists(path):
                    raise ConnectionError(f"{what} est introuvable sur le PC ({path})")
            if self.proc is None or self.proc.poll() is not None:
                self.proc = subprocess.Popen(
                    [LLAMA_EXE, "-m", VISION_MODEL, "--mmproj", VISION_MMPROJ, "-ngl", "99", "-c", "8192",
                     "--host", "127.0.0.1", "--port", str(VISION_PORT), "--no-webui", "--reasoning", "off"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            deadline = time.time() + 180
            while time.time() < deadline:
                if self.proc.poll() is not None:
                    raise ConnectionError("Le modèle local n'a pas pu démarrer (carte graphique occupée ?)")
                if self.healthy():
                    return
                time.sleep(1)
            raise ConnectionError("Le modèle local met trop de temps à démarrer")

    def chat(self, body):
        self.busy += 1
        try:
            self.ensure_started()
            req = Request(VISION_URL + "/v1/chat/completions", data=json.dumps(body).encode("utf-8"),
                          headers={"Content-Type": "application/json"})
            with urlopen(req, timeout=180) as res:
                return json.loads(res.read().decode("utf-8"))
        finally:
            self.busy -= 1
            self.last_use = time.time()

    def watchdog(self):
        # Un moteur resté ouvert par une session précédente de CalTrack (arrêtée
        # brutalement) occuperait la carte graphique pour rien : on le ferme.
        import subprocess
        subprocess.run(["taskkill", "/F", "/IM", os.path.basename(LLAMA_EXE)], capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        while True:
            time.sleep(30)
            with self.lock:
                idle = time.time() - self.last_use > VISION_IDLE
                if self.proc and self.proc.poll() is None and not self.busy and idle:
                    self.proc.terminate()
                    self.proc = None


VISION = VisionEngine()


def vision_call(image_data_url, hint):
    text = VISION_PROMPT + (f"\nPrécision donnée par l'utilisateur : {hint}" if hint else "")
    data = VISION.chat({
        "temperature": 0.2,
        "max_tokens": 1500,
        "chat_template_kwargs": {"enable_thinking": False},
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": image_data_url}},
            {"type": "text", "text": text},
        ]}],
        "response_format": {"type": "json_schema", "json_schema": {"name": "repas", "strict": True, "schema": VISION_SCHEMA}},
    })
    content = data["choices"][0]["message"]["content"] or ""
    start, end = content.find("{"), content.rfind("}")
    return json.loads(content[start:end + 1]).get("items", [])


def fold(s):
    s = "".join(c for c in unicodedata.normalize("NFD", str(s).lower()) if unicodedata.category(c) != "Mn")
    return s.replace("œ", "oe")


def words(s):
    # mots significatifs, au singulier (tomates -> tomate, choux -> chou)
    out = set()
    for w in re.findall(r"[a-z0-9]+", fold(s)):
        if len(w) > 2 and w not in ("les", "des", "aux", "avec", "sans", "une", "pour", "extra"):
            out.add(w[:-1] if len(w) > 3 and w[-1] in "sx" else w)
    return out


# Mots qui disent si un aliment de la base est cru ou cuit, pour ne pas
# confondre "pâtes cuites" (131 kcal) et "pâtes sèches" (357 kcal).
RAW_WORDS = {"cru", "crue", "seche", "sec", "fraiche", "frai"}
COOKED_WORDS = {"cuit", "cuite", "frit", "frite", "grille", "roti", "poele", "vapeur", "dur", "cuisinee", "maison", "dente"}
# Noms vus sur la photo -> nom utilisé dans la base
SYNONYMS = {"spaghetti": "pate", "tagliatelle": "pate", "penne": "pate", "coquillette": "pate", "fusilli": "pate",
            "macaroni": "pate", "laitue": "salade", "plat": "frit"}

_BASE_FOODS = None


def food_state(ws):
    if ws & COOKED_WORDS:
        return "cuit"
    if ws & RAW_WORDS:
        return "cru"
    return None


def match_food(name, state):
    """Aliment de la base le plus proche du nom reconnu sur la photo (ou None)."""
    global _BASE_FOODS
    if _BASE_FOODS is None:
        with open(FOODS_FILE, encoding="utf-8") as f:
            _BASE_FOODS = [(food, words(food["name"]), words(food.get("detail", "")))
                           for food in json.load(f)["foods"]]
    wanted = words(name)
    wanted |= {SYNONYMS[w] for w in wanted if w in SYNONYMS}
    want_state = food_state(words(state)) or food_state(wanted)
    best, best_score = None, 0.0
    for food, fw, dw in _BASE_FOODS:
        common = len(wanted & fw)
        if not common:
            continue
        score = (common + 0.5 * len(wanted & dw)) / len(wanted | fw)
        cand_state = food_state(dw) or food_state(fw)
        if want_state and cand_state:
            score += 0.15 if want_state == cand_state else -0.5
        # variante trop précise (ex. "shiitake") : légère pénalité
        score -= 0.08 * len(dw - wanted - RAW_WORDS - COOKED_WORDS)
        if score > best_score:
            best, best_score = food, score
    # 0,5 : un seul mot générique en commun ("sauce", "boeuf"…) ne suffit pas
    return best if best_score >= 0.5 else None


def estimated_food(it, name):
    """Aliment absent de la base : valeurs du modèle, vérifiées avec les macros."""
    food = clean_food({**it, "name": name, "detail": str(it.get("state", "")).strip(), "source": "manual"},
                      food_id="photo_" + uuid.uuid4().hex[:8])
    # 4 kcal par g de protéines et de glucides, 9 par g de lipides
    calc = 4 * food["proteins"] + 4 * food["carbs"] + 9 * food["fat"]
    if calc > 0 and (food["calories"] <= 0 or abs(food["calories"] - calc) > 0.35 * max(calc, food["calories"])):
        food["calories"] = round(calc)
    food["calories"] = min(food["calories"], 900)
    return food


def analyse_photo(image_data_url, hint=""):
    if not str(image_data_url).startswith("data:image/"):
        raise ValueError("Image invalide")
    results = []
    for it in vision_call(image_data_url, hint.strip()[:300]):
        grams = round(max(0.0, num(it.get("grams"))))
        name = re.sub(r"\s*\([^)]*\)", "", str(it.get("name", ""))).strip()
        if not name or grams <= 0:
            continue
        found = match_food(name, str(it.get("state", "")))
        food = dict(found, source="base") if found else estimated_food(it, name)
        results.append({"guess": name, "grams": grams, "matched": bool(found), "food": food})
    return results


# ---------------------------------------------------------------- HTTP

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

    def log_message(self, fmt, *args):
        pass  # console silencieuse

    def end_headers(self):
        # Pages et scripts : le navigateur revérifie à chaque fois, pour que les
        # téléphones voient tout de suite les mises à jour de l'appli.
        if getattr(self, "static_file", False):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def send_json(self, obj, status=200, cache="no-store"):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"null") if length else None

    def user(self):
        """Personne concernée par la requête (en-tête X-User envoyé par la page)."""
        user = (self.headers.get("X-User") or "").strip().lower()
        if user not in {p["id"] for p in load_profiles()}:
            raise ValueError("Profil inconnu")
        return user

    def do_GET(self):
        url = urlparse(self.path)
        query = parse_qs(url.query)
        if url.path == "/api/users":
            with LOCK:
                return self.send_json(load_profiles())
        if url.path == "/api/state":
            try:
                user = self.user()
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, 400)
            with LOCK:
                return self.send_json(load(user))
        if url.path == "/api/backups":
            try:
                user = self.user()
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, 400)
            day = (query.get("date") or [""])[0]
            if not day:
                return self.send_json({"backups": list_backups(user), "keep": BACKUP_KEEP})
            if day not in list_backups(user):  # aussi la garde contre les chemins forgés
                return self.send_json({"error": "Sauvegarde introuvable"}, 404)
            with open(backup_file(user, day), "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="caltrack-{user}-{day}.json"')
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return self.wfile.write(body)
        if url.path == "/api/foods":
            with open(FOODS_FILE, encoding="utf-8") as f:
                return self.send_json(json.load(f), cache="max-age=3600")
        if url.path == "/api/off":
            try:
                if query.get("barcode"):
                    product = off_barcode(query["barcode"][0])
                    if not product:
                        return self.send_json({"error": "Produit introuvable"}, 404)
                    return self.send_json({"product": product})
                q = (query.get("q") or [""])[0].strip()
                if len(q) < 2:
                    return self.send_json({"error": "Tapez au moins 2 lettres"}, 400)
                page = max(1, int((query.get("page") or ["1"])[0]))
                return self.send_json(off_search(q, page))
            except (ConnectionError, OSError) as exc:
                return self.send_json({"error": str(exc)}, 502)
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, 400)
        self.static_file = True
        return super().do_GET()

    def do_POST(self):
        if urlparse(self.path).path == "/api/photo":
            try:
                body = self.read_json() or {}
                return self.send_json({"items": analyse_photo(body.get("image", ""), str(body.get("hint", "")))})
            except HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:200]
                return self.send_json({"error": f"Le modèle local a refusé la demande ({detail})"}, 502)
            except ConnectionError as exc:
                return self.send_json({"error": str(exc)}, 502)
            except OSError:
                return self.send_json({"error": "Le modèle local ne répond pas"}, 502)
            except (ValueError, KeyError, IndexError):
                return self.send_json({"error": "Réponse du modèle illisible : réessayez"}, 502)
        if urlparse(self.path).path == "/api/users":
            try:
                with LOCK:
                    profile = create_profile((self.read_json() or {}).get("name"))
                    return self.send_json({"created": profile, "profiles": load_profiles()})
            except (ValueError, TypeError, AttributeError) as exc:
                return self.send_json({"error": str(exc)}, 400)
        self.handle_api("POST")

    def do_PUT(self):
        self.handle_api("PUT")

    def do_DELETE(self):
        self.handle_api("DELETE")

    def handle_api(self, method):
        parts = [p for p in urlparse(self.path).path.split("/") if p]
        if not parts or parts[0] != "api":
            return self.send_json({"error": "introuvable"}, 404)
        try:
            user = self.user()
            body = self.read_json()
            with LOCK:
                data = load(user)
                error = self.route(method, parts[1:], body, data)
                if error is not None:
                    return self.send_json(error, 400)
                save(user, data)
                return self.send_json(data)
        except (ValueError, KeyError, TypeError) as exc:
            return self.send_json({"error": str(exc).strip("'")}, 400)

    def route(self, method, parts, body, data):
        """Modifie `data` en place. Retourne un dict d'erreur si besoin."""
        head = parts[0] if parts else ""

        if head == "journal":
            if method == "POST" and parts[1:] == ["copy"]:
                copy_meal(data, body)
            elif method == "POST":
                day = str(body.get("date", ""))
                time.strptime(day, "%Y-%m-%d")  # ValueError si la date est invalide
                data["journal"].setdefault(day, []).append(clean_entry(body))
            elif method == "PUT" and len(parts) == 2:
                _, entry = find_entry(data, parts[1])
                if "grams" in body:
                    grams = num(body["grams"])
                    if grams <= 0:
                        raise ValueError("Quantité invalide")
                    entry["grams"] = grams
                if body.get("meal") in MEALS:
                    entry["meal"] = body["meal"]
            elif method == "DELETE" and len(parts) == 2:
                day, entry = find_entry(data, parts[1])
                data["journal"][day].remove(entry)
                if not data["journal"][day]:
                    del data["journal"][day]
            else:
                return {"error": "requête invalide"}

        elif head == "goals" and method == "PUT":
            data["goals"] = {
                k: max(1, round(num(body.get(k), DEFAULT_DATA["goals"][k])))
                for k in DEFAULT_DATA["goals"]
            }

        elif head == "foods":
            if method == "POST":
                food = clean_food(body)
                if not any(f["id"] == food["id"] for f in data["customFoods"]):
                    data["customFoods"].insert(0, food)
            elif method == "PUT" and len(parts) == 2:
                food = clean_food(body, food_id=parts[1])
                data["customFoods"] = [food if f["id"] == parts[1] else f for f in data["customFoods"]]
            elif method == "DELETE" and len(parts) == 2:
                data["customFoods"] = [f for f in data["customFoods"] if f["id"] != parts[1]]
            else:
                return {"error": "requête invalide"}

        else:
            return {"error": "requête invalide"}
        return None


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8001
    for p in load_profiles():
        load(p["id"])
    threading.Thread(target=VISION.watchdog, daemon=True).start()
    # Sous Windows, SO_REUSEADDR permet à deux programmes d'écouter le même
    # port sans erreur : on le désactive pour échouer proprement si occupé.
    ThreadingHTTPServer.allow_reuse_address = False
    try:
        server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    except OSError:
        sys.exit(f"Le port {port} est déjà utilisé. Essayez : python server.py {port + 1}")
    print("Serveur CalTrack démarré.")
    print(f"  Sur ce PC           : http://localhost:{port}")
    print(f"  Depuis le wifi      : http://{lan_ip()}:{port}")
    print("Ctrl+C pour arrêter.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
