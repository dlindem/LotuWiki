import json
import time
import re
import wikibase
from collections import defaultdict
import requests
from flask import Flask, render_template, request, session, redirect, url_for, jsonify
from SPARQLWrapper import SPARQLWrapper, JSON
from flask_session import Session
import os
from pathlib import Path

TASKS_DIR = Path(__file__).parent / "tasks"
TASKS_DIR.mkdir(exist_ok=True)

DEFAULT_SEARCH_LANG = "eu"

app = Flask(__name__)
app.secret_key = "change-me-in-production"
# --- server-side session storage -------------------------------------------
# Filesystem is fine for a single-instance tool; use Redis if you ever run
# more than one worker. The Flask session cookie now only carries the session
# id (a few dozen bytes), so list size is no longer limited by cookie size.
app.config["SESSION_TYPE"] = "filesystem"
app.config["SESSION_FILE_DIR"] = str(Path(__file__).parent / "flask_session")
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_USE_SIGNER"] = True
Path(app.config["SESSION_FILE_DIR"]).mkdir(exist_ok=True)
Session(app)
# ---------------------------------------------------------------------------

HEADERS = {"User-Agent": "User:DL2204 python script david.lindemann@ehu.eus"}

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"

DEFAULT_SOURCE_QUERY = """PREFIX ehwb: <https://ehkultura.wikibase.cloud/entity/>
PREFIX ehdp: <https://ehkultura.wikibase.cloud/prop/direct/>
PREFIX ehp: <https://ehkultura.wikibase.cloud/prop/>
PREFIX ehps: <https://ehkultura.wikibase.cloud/prop/statement/>
PREFIX ehpq: <https://ehkultura.wikibase.cloud/prop/qualifier/>
PREFIX ehpr: <https://ehkultura.wikibase.cloud/prop/reference/>
PREFIX ehno: <https://ehkultura.wikibase.cloud/prop/novalue/>

SELECT ?item ?itemLabel (xsd:integer(?ej) as ?fitxa) ?wd ?placeLabel
       (GROUP_CONCAT(?class; separator=" | ") as ?classes)
WHERE {
  ?item ehdp:P56 ?ej.
  OPTIONAL { ?item ehdp:P36 ?place. }
  FILTER NOT EXISTS { ?item ehdp:P1 ?wd. }
  OPTIONAL { ?item ehdp:P5 [ rdfs:label ?class ]. FILTER(lang(?class)="eu") }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "eu,es,mul,en". }
}
GROUP BY ?item ?itemLabel ?ej ?wd ?placeLabel ?classes
ORDER BY ?fitxa
"""
# ---------------------------------------------------------------------------
# Write Alignment
# ---------------------------------------------------------------------------
def do_alignment(source, target):
    wikibase_name = re.search(r'https?://(.*)/entity/Q\d+', source).group(1)
    if not wikibase.wbi:
        wikibase.wbi_setup(wikibase_name)
    wb_item = wikibase.wbi.item.get(entity_id=re.search(r'Q\d+$', source).group(0))
    wd_qid = re.search(r'Q\d+$', target).group(0)
    references = wikibase.References()
    reference1 = wikibase.Reference()
    reference1.add(wikibase.String(prop_nr="P37", value="manually set link"))
    reference1.add(wikibase.Time(prop_nr="P8", time="now"))
    references.add(reference1)
    wb_item.claims.add(wikibase.ExternalID(prop_nr="P1", value=wd_qid, references=references), action_if_exists=wikibase.ActionIfExists.MERGE_REFS_OR_APPEND)
    wb_item.write()
    print(f"Wrote link to Wikibase: {source} -> {target}")
    return source.replace("/entity/", "/wiki/Item:") + "#P1"

def do_no_match(source):
    wikibase_name = re.search(r'https?://(.*)/entity/Q\d+', source).group(1)
    if not wikibase.wbi:
        wikibase.wbi_setup(wikibase_name)
    wb_item = wikibase.wbi.item.get(entity_id=re.search(r'Q\d+$', source).group(0))
    references = wikibase.References()
    reference1 = wikibase.Reference()
    reference1.add(wikibase.String(prop_nr="P37", value="manual decision"))
    reference1.add(wikibase.Time(prop_nr="P8", time="now"))
    references.add(reference1)
    wb_item.claims.add(wikibase.ExternalID(prop_nr="P1", value="CREATE", references=references),
                       action_if_exists=wikibase.ActionIfExists.MERGE_REFS_OR_APPEND)
    wb_item.write()
    return f"Wrote 'CREATE' to P1 in {source}"

# ---------------------------------------------------------------------------
# SPARQL helpers
# ---------------------------------------------------------------------------
def run_sparql(endpoint_url: str, query: str, user_agent: str = HEADERS["User-Agent"]):
    sparql = SPARQLWrapper(endpoint_url, agent=user_agent)
    sparql.setQuery(query)
    sparql.setReturnFormat(JSON)
    return sparql.query().convert()["results"]["bindings"]


def get_list_to_align(wikibase_name: str, query: str):
    """Fetch the list of source entities to align, using the user-edited query."""
    if not wikibase.wbi:
        wikibase.wbi_setup(wikibase_name)
    results = wikibase.wbi_helpers.execute_sparql_query(query=query, user_agent=wikibase.user_agent)["results"]["bindings"]
    return results

# ---------------------------------------------------------------------------
# task helpers
# ---------------------------------------------------------------------------
def list_tasks():
    """Return a sorted list of task names (filenames without .json)."""
    return sorted(
        p.stem for p in TASKS_DIR.glob("*.json")
        if (TASKS_DIR / (p.stem + ".rq")).exists()
    )


def load_task(name: str) -> dict:
    """Load the .json metadata and the .rq query for a task."""
    meta_path = TASKS_DIR / f"{name}.json"
    query_path = TASKS_DIR / f"{name}.rq"
    with open(meta_path, encoding="utf-8") as fh:
        meta = json.load(fh)
    meta["query"] = query_path.read_text(encoding="utf-8-sig")
    meta.setdefault("name", name)
    meta.setdefault("description", "")
    meta.setdefault("default_search_language", "en")
    meta.setdefault("wikidata_properties", [])
    return meta


def save_task(name: str, query: str, description: str, wikibase_name:str, properties: list[dict]):
    safe = re.sub(r"[^A-Za-z0-9_\-]", "_", name).strip("_") or "task"
    # strip BOM, normalise newlines, ensure trailing newline
    clean_query = query.lstrip("\ufeff").replace("\r\n", "\n").rstrip() + "\n"
    (TASKS_DIR / f"{safe}.rq").write_text(clean_query, encoding="utf-8")
    meta = {
        "name": safe,
        "description": description,
        "wikibase": wikibase_name,
        "wikidata_properties": properties,
    }
    (TASKS_DIR / f"{safe}.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return safe

# ---------------------------------------------------------------------------
# Candidate retrieval
# ---------------------------------------------------------------------------
def search_wikidata(querystring: str, language: str = "eu", limit: int = 20):
    params = {
        "action": "wbsearchentities",
        "search": querystring,
        "language": language,
        "uselang": language,          # label/description shown in that language
        "limit": limit,
        "format": "json",
    }
    response = requests.get(WIKIDATA_API, params=params, headers=HEADERS, timeout=10)
    time.sleep(1)
    content = json.loads(response.content.decode("utf-8"))
    return [res["id"] for res in content.get("search", [])]


def get_matching_candidates(querystring: str, language: str = "eu",
                            extra_properties: list[dict] | None = None,
                            source_classes: str = ""):
    qids = search_wikidata(querystring, language=language)
    if not qids:
        return []

    extra_properties = extra_properties or []
    values_string = "VALUES ?item { wd:" + " wd:".join(qids) + " }"
    time.sleep(1)

    label_langs = f"{language},eu,es,en,mul"
    valid_props = [e for e in extra_properties
                   if re.fullmatch(r"P\d+", e.get("property", ""))]

    select_extras = ""
    group_extras = ""
    triple_extras = ""
    select_vars = ""
    for entry in valid_props:
        pid = entry["property"]
        select_extras += f"(GROUP_CONCAT(DISTINCT ?x_{pid}Label; SEPARATOR = \", \") AS ?x_{pid}) "
        group_extras += f"?x_{pid} "
        triple_extras += f"OPTIONAL {{ ?item wdt:{pid} ?x_{pid}. }}\n"
        select_vars += f" ?x_{pid} ?x_{pid}Label"

    query = (
        "SELECT ?item ?itemLabel ?itemDescription ?placeLabel "
        "(GROUP_CONCAT(DISTINCT ?classLabel; SEPARATOR = \", \") AS ?classes) "
        "(GROUP_CONCAT(DISTINCT ?subcOfLabel; SEPARATOR = \", \") AS ?subclassOf) "
        "(GROUP_CONCAT(DISTINCT ?pOfLabel; SEPARATOR = \", \") AS ?partOf) "
        "(SAMPLE(?img) AS ?image) "
        + select_extras
        + "WHERE {\n"
        + values_string
        + """
        {
            SELECT ?item ?itemLabel ?itemDescription ?class ?classLabel
                   ?subcOfLabel ?pOfLabel ?placeLabel ?img
        """
        + select_vars
        + """
            WHERE {
              ?item wdt:P31 ?class.
              OPTIONAL { ?item wdt:P279 ?subcOf. }
              OPTIONAL { ?item wdt:P361 ?pOf. }
              OPTIONAL { ?item wdt:P131 ?place. }
              OPTIONAL { ?item wdt:P18 ?img. }
        """
        + triple_extras
        + f'      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "{label_langs}". }}\n'
        + """
            }
          }
        }
        GROUP BY ?item ?itemLabel ?itemDescription ?placeLabel ?image
                 ?classes ?subclassOf ?partOf
        """
        + group_extras
    )

    raw = flatten_bindings(run_sparql(WIKIDATA_SPARQL, query))

    prop_keys = [f"x_{e['property']}" for e in valid_props]
    return _conflate_and_rank(raw, source_classes=source_classes,
                              prop_keys=prop_keys)


def flatten_bindings(bindings):
    """Turn SPARQL bindings into simple dicts keyed by variable name."""
    out = []
    for row in bindings:
        out.append({k: v.get("value", "") for k, v in row.items()})
    return out

_WEIGHT_CLASS = 0.6
_WEIGHT_COVERAGE = 0.4
_MIN_TOKEN_LEN = 2


def _tokens(text: str) -> set[str]:
    """Lowercase word tokens of length >= _MIN_TOKEN_LEN, split on non-alnum."""
    if not text:
        return set()
    raw = re.split(r"[^\w]+", text.lower(), flags=re.UNICODE)
    return {t for t in raw if len(t) >= _MIN_TOKEN_LEN}


def _dice(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    if not a or not b:
        return 0.0
    return 2 * len(a & b) / (len(a) + len(b))


def _score_candidate(source_classes: str, cand: dict,
                     prop_keys: list[str]) -> float:
    class_sim = _dice(_tokens(source_classes), _tokens(cand.get("classes", "")))

    if prop_keys:
        filled = sum(1 for k in prop_keys if cand.get(k))
        coverage = filled / len(prop_keys)
    else:
        coverage = 1.0  # neutral when the task defines no extra properties

    return _WEIGHT_CLASS * class_sim + _WEIGHT_COVERAGE * coverage


def _conflate_and_rank(rows: list[dict], source_classes: str,
                       prop_keys: list[str]) -> list[dict]:
    """Merge rows sharing a QID, then sort by descending score."""
    merged: dict[str, dict] = {}

    # Fields that hold many values and should be unioned, not overwritten.
    multi_fields = ("classes", "subclassOf", "partOf") + tuple(prop_keys)

    for row in rows:
        qid = row.get("item")
        if not qid:
            continue
        if qid not in merged:
            merged[qid] = dict(row)
            for f in multi_fields:
                merged[qid][f] = row.get(f, "") or ""
            continue

        target = merged[qid]
        for f in ("itemLabel", "itemDescription", "placeLabel"):
            if not target.get(f) and row.get(f):
                target[f] = row[f]
        for f in multi_fields:
            # Accumulate raw strings; deduplication happens below.
            if row.get(f):
                target[f] = (target.get(f, "") + ", " + row[f]).strip(", ")

    # Deduplicate the multi-valued fields (case-insensitive on each piece).
    for cand in merged.values():
        for f in multi_fields:
            pieces = [p.strip() for p in cand.get(f, "").split(",") if p.strip()]
            seen, uniq = set(), []
            for p in pieces:
                key = p.lower()
                if key not in seen:
                    seen.add(key)
                    uniq.append(p)
            cand[f] = ", ".join(uniq)

    # Score and sort.
    scored = []
    for cand in merged.values():
        cand["_score"] = _score_candidate(source_classes, cand, prop_keys)
        scored.append(cand)
    scored.sort(key=lambda c: (-c["_score"], c.get("itemLabel", "").lower()))
    return scored

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/", methods=["GET", "POST"])
def index():
    error = None
    tasks = list_tasks()

    if not tasks:
        return render_template(
            "index.html",
            tasks=[], default_task="", default_query=DEFAULT_SOURCE_QUERY,
            error=None, message="No tasks found in tasks/. Create one first.",
        )

    # Resolve the task to work with: URL param (GET), form field (POST),
    # falling back to the session's last task, then the first one.
    task_name = (
        request.form.get("task")
        or request.args.get("task")
        or session.get("task")
        or tasks[0]
    )
    if task_name not in tasks:
        task_name = tasks[0]

    # Load the task's metadata + .rq up front so both branches can use them.
    try:
        task = load_task(task_name)
    except Exception as exc:  # noqa: BLE001
        task = {"query": "", "wikidata_properties": [], "name": task_name}
        error = f"Could not load task '{task_name}': {exc}"

    if request.method == "POST":
        # The textarea holds whatever the user wants to run; if empty, use the
        # task's default query. The *task file* is never used as the source of
        # truth for the run — only the visible textarea.
        query = request.form.get("query", "").strip() or task["query"]
        try:
            sources = flatten_bindings(get_list_to_align(task['wikibase'], query))
        except Exception as exc:  # noqa: BLE001
            error = f"SPARQL error: {exc}"
        else:
            session["sources"] = sources
            session["query"] = query
            session["task"] = task_name
            session["position"] = 0
            session["results"] = []
            return redirect(url_for("align"))

    return render_template(
        "index.html",
        tasks=tasks,
        default_task=task_name,
        default_query=task["query"],
        error=error,
        message=None,
    )

@app.route("/task/new", methods=["GET", "POST"])
def task_new():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        wikibase_name = request.form.get("wikibase_name", "").strip()
        default_search_language = request.form.get("default_search_language", "").strip()
        query = request.form.get("query", "").strip()
        # properties come as parallel lists from the form
        prop_ids = request.form.getlist("prop_id")
        prop_labels = request.form.getlist("prop_label")
        properties = [
            {"property": pid.strip(), "label": plab.strip() or pid.strip()}
            for pid, plab in zip(prop_ids, prop_labels)
            if pid.strip()
        ]
        if not name or not query:
            return render_template(
                "task_form.html",
                error="Name and query are required.",
                name=name, description=description, wikibase_name =wikibase_name, default_search_language=default_search_language, query=query, properties=properties,
            )
        save_task(name, query, description, wikibase_name, properties)
        return redirect(url_for("index"))

    return render_template(
        "task_form.html",
        error=None,
        name="",
        description="",
        query=DEFAULT_SOURCE_QUERY,
        properties=[{"property": "P131", "label": "place"}],
    )

@app.route("/align")
def align():
    sources = session.get("sources")
    if not sources:
        return redirect(url_for("index"))

    position = session.get("position", 0)
    if position >= len(sources):
        return redirect(url_for("finish"))

    current = sources[position]
    task_name = session.get("task")
    task = load_task(task_name) if task_name else {"wikidata_properties": []}

    default_label = current.get("itemLabel", "")
    search_string = request.args.get("search", default_label).strip() or default_label
    search_lang = request.args.get("lang", task['default_search_language']).strip().lower()
    if not re.fullmatch(r"[a-z]{2,3}", search_lang):
        search_lang = DEFAULT_SEARCH_LANG

    candidates = get_matching_candidates(
        search_string,
        language=search_lang,
        extra_properties=task.get("wikidata_properties", []),
        source_classes=current.get("classes", ""),
    )

    return render_template(
        "align.html",
        current=current,
        candidates=candidates,
        position=position,
        total=len(sources),
        search_string=search_string,
        search_lang=search_lang,
        default_label=default_label,
        default_lang=DEFAULT_SEARCH_LANG,
        task=task,
    )

@app.route("/choose", methods=["POST"])
def choose():
    sources = session.get("sources", [])
    position = session.get("position", 0)
    if position >= len(sources):
        return redirect(url_for("finish"))

    current = sources[position]
    choice = request.form.get("choice", "")

    if choice == "no_match":
        print(do_no_match(current.get("item", None)))
        record = {
            "source": current.get("item", ""),
            "source_label": current.get("itemLabel", ""),
            "target": None,
            "target_label": None,
            "decision": "New Wikidata item needed",
        }
    elif choice == "no_decision":
        record = {
            "source": current.get("item", ""),
            "source_label": current.get("itemLabel", ""),
            "target": None,
            "target_label": None,
            "decision": "No decision (skipped)",
        }
    else:
        candidate = json.loads(request.form.get("candidate_payload", "{}"))
        alignment_url = do_alignment(current.get("item", None), candidate.get("item", None))

        record = {
            "source": current.get("item", ""),
            "source_label": current.get("itemLabel", ""),
            "target": candidate.get("item", ""),
            "target_label": candidate.get("itemLabel", ""),
            "decision": "Wikidata match (has been written)",
            "alignment_url": alignment_url
        }

    results = session.get("results", [])
    results.append(record)
    session["results"] = results
    session["position"] = position + 1
    return redirect(url_for("align"))


@app.route("/finish")
def finish():
    results = session.get("results", [])
    return render_template("finish.html", results=results)


@app.route("/reset")
def reset():
    session.clear()
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(debug=True)