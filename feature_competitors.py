"""
Анализ конкурентов — кофейни и кафе в Москве
Источники данных:
  1. OpenStreetMap (Overpass API)   
  2. Wikipedia     (requests + BS4) 
  3. 2GIS          (Selenium + BS4) 
"""

import re
import time
import requests
import pandas as pd
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import dash
from dash import dcc, html, dash_table
from dash.dependencies import Input, Output
import plotly.express as px
import plotly.graph_objects as go

BROWN  = "#6F4E37"
LIGHT  = "#C8A882"
ORANGE = "#C8783A"
TYPE_COLORS = {"Кофейня": BROWN, "Кафе": LIGHT}

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

KNOWN_CHAINS = [
    "Starbucks", "Stars Coffee", "Coffee Like", "Cofix", "Шоколадница",
    "Кофе Хаус", "Costa Coffee", "Traveler's Coffee", "Surf Coffee",
    "Double B", "Даблби", "Кофемания", "One Price Coffee", "Coffee Bean",
    "Буханка", "Хлеб Насущный", "Кофе и Ваниль", "Правда кофе",
]


def fetch_osm(lat=55.7558, lon=37.6173, radius_m=8000):
    query = f"""
    [out:json][timeout:60];
    (
      node["amenity"="cafe"](around:{radius_m},{lat},{lon});
      node["amenity"="coffee_shop"](around:{radius_m},{lat},{lon});
      node["shop"="coffee"](around:{radius_m},{lat},{lon});
    );
    out body;
    """
    servers = [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
    ]
    headers = {"User-Agent": "CoffeeSpotAnalytics/1.0",
               "Content-Type": "application/x-www-form-urlencoded"}
    for url in servers:
        try:
            resp = requests.post(url, data={"data": query},
                                 headers=headers, timeout=60)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            print(f"   зеркало {url} недоступно: {e}")
    raise RuntimeError("Все зеркала Overpass API недоступны")


def parse_osm(raw):
    records = []
    for el in raw.get("elements", []):
        if el.get("lat") is None:
            continue
        tags = el.get("tags", {})
        records.append({
            "lat": el["lat"], "lon": el["lon"],
            "name": tags.get("name", "Без названия"),
            "amenity": tags.get("amenity", ""),
            "cuisine": tags.get("cuisine", ""),
            "brand": tags.get("brand", ""),
            "opening_hours": tags.get("opening_hours", ""),
            "takeaway": tags.get("takeaway", ""),
            "outdoor_seating": tags.get("outdoor_seating", ""),
            "wifi": tags.get("internet_access", tags.get("wifi", "")),
        })
    df = pd.DataFrame(records)
    if df.empty:
        return df

    def get_type(row):
        if row["amenity"] == "coffee_shop" or "coffee" in str(row["cuisine"]).lower():
            return "Кофейня"
        return "Кафе"

    def detect_chain(row):
        text = f"{row['name']} {row['brand']}".lower()
        for c in KNOWN_CHAINS:
            if c.lower() in text:
                return c
        return "Независимая"

    df["type"]     = df.apply(get_type, axis=1)
    df["chain"]    = df.apply(detect_chain, axis=1)
    df["is_chain"] = df["chain"] != "Независимая"

    yes = {"yes", "free", "customers", "1", "true"}
    df["wifi_label"]     = df["wifi"].apply(lambda v: "Есть" if str(v).lower() in yes else "Нет данных")
    df["takeaway_label"] = df["takeaway"].apply(lambda v: "Есть" if str(v).lower() in yes else "Нет данных")
    df["outdoor_label"]  = df["outdoor_seating"].apply(lambda v: "Есть" if str(v).lower() in yes else "Нет данных")
    return df.reset_index(drop=True)


WIKI_BASE = "https://ru.wikipedia.org"


def fetch_wiki_chains():
    """ Парсим и для каждой статьи читаем infobox: год основания, кол-во точек, город."""
    wiki_headers = {"User-Agent": "CoffeeSpotAnalytics/1.0 (student project; educational use)"}

    cat_url = f"{WIKI_BASE}/wiki/Категория:Сети_кофеен_России"
    resp = requests.get(cat_url, headers=wiki_headers, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    links = []
    cat_div = soup.find("div", class_="mw-category")
    if cat_div:
        for a in cat_div.find_all("a"):
            href = a.get("href", "")
            if href.startswith("/wiki/") and ":" not in href[6:]:
                links.append({"name": a.get_text(strip=True),
                               "url": WIKI_BASE + href})

    print(f"  Wikipedia: найдено {len(links)} статей о сетях")

    year_re = re.compile(r"\b(19|20)\d{2}\b")
    num_re  = re.compile(r"\b(\d{1,5})\b")

    results = []
    for item in links:
        row = {"name": item["name"], "founded": "—",
               "locations": "—", "city": "—", "url": item["url"]}
        try:
            r = requests.get(item["url"], headers=wiki_headers, timeout=15)
            s = BeautifulSoup(r.text, "html.parser")
            infobox = s.find("table", class_=lambda c: c and "infobox" in c)
            if infobox:
                for tr in infobox.find_all("tr"):
                    th = tr.find("th")
                    td = tr.find("td")
                    if not th or not td:
                        continue
                    label = th.get_text(strip=True).lower()
                    value = td.get_text(" ", strip=True)

                    
                    if row["founded"] == "—" and any(
                        k in label for k in ["основан", "созда", "открыт"]
                    ):
                        m = year_re.search(value)
                        if m:
                            row["founded"] = m.group()

                    
                    if row["locations"] == "—" and any(
                        k in label for k in ["заведен", "ресторан", "кофеен",
                                              "точек", "филиал", "магазин"]
                    ):
                        m = num_re.search(value)
                        if m:
                            row["locations"] = m.group(1)

                    
                    if row["city"] == "—" and any(
                        k in label for k in ["город", "штаб", "место",
                                              "располож", "распол."]
                    ):
                        # Берём название города
                        for word in value.split():
                            w = word.strip(",.;()[]")
                            if w and w[0].isupper() and len(w) > 2:
                                row["city"] = w
                                break
            time.sleep(0.4)
        except Exception:
            pass
        results.append(row)
        print(f"  • {row['name']}  основана: {row['founded']}  точек: {row['locations']}")

    df = pd.DataFrame(results) if results else pd.DataFrame(
        columns=["name", "founded", "locations", "city", "url"])

    df["founded_year"] = pd.to_numeric(df["founded"], errors="coerce")
    return df.reset_index(drop=True)


GIS_URL = "https://2gis.ru/moscow/search/%D0%9A%D0%BE%D1%84%D0%B5%D0%B9%D0%BD%D0%B8/rubricId/162"


def fetch_2gis_coffee(max_pages=7):
    """Открываем страницы 2GIS, скроллим, парсим карточки через BS4."""
    driver = webdriver.Chrome()
    results, seen = [], set()

    for page in range(1, max_pages + 1):
        url = GIS_URL if page == 1 else f"{GIS_URL}/page/{page}"
        driver.get(url)

        try:
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "a[href*='/firm/']"))
            )
        except Exception:
            time.sleep(8)

        for _ in range(10):
            cards = driver.find_elements(By.CSS_SELECTOR, "a[href*='/firm/']")
            if not cards:
                break
            driver.execute_script("arguments[0].scrollIntoView(true)", cards[-1])
            time.sleep(1)

        soup = BeautifulSoup(driver.page_source, "html.parser")
        page_count = 0

        for a in soup.find_all("a", href=lambda h: h and "/firm/" in h):
            name = a.get_text(strip=True)
            if len(name) < 2 or name in seen:
                continue
            seen.add(name)

            container = a.find_parent()
            for _ in range(5):
                if container is None:
                    break
                txt = container.get_text("\n", strip=True)
                if any("," in l and any(c.isdigit() for c in l)
                       for l in txt.split("\n") if l != name):
                    break
                container = container.find_parent()

            lines = [l.strip() for l in (txt.split("\n") if container else [name]) if l.strip()]

            rating, reviews, addr = "—", 0, "—"
            for ln in lines:
                if ln == name:
                    continue
                if rating == "—" and re.fullmatch(r"\d[.,]?\d*", ln):
                    rating = ln.replace(",", ".")
                m = re.search(r"(\d+)\s*(оцен|отзыв)", ln.lower())
                if m and reviews == 0:
                    reviews = int(m.group(1))
                if (addr == "—" and "," in ln and any(c.isdigit() for c in ln)
                        and "оцен" not in ln.lower() and "отзыв" not in ln.lower()):
                    addr = ln[:100]

            results.append({"name": name, "address": addr,
                            "rating": rating, "reviews": reviews})
            page_count += 1

        print(f"  2GIS стр.{page}: +{page_count} заведений")
        if page_count == 0:
            break

    driver.quit()

    df = pd.DataFrame(results, columns=["name", "address", "rating", "reviews"])
    df["rating_num"]  = pd.to_numeric(df["rating"], errors="coerce")
    df["reviews_num"] = df["reviews"]
    return df


print("\n Источник 1 — OpenStreetMap...")
try:
    df_osm = parse_osm(fetch_osm())
    print(f"   Спарсил {len(df_osm)} заведений")
except Exception as e:
    print(f"   Че-то не то... {e}")
    df_osm = pd.DataFrame(columns=[
        "lat", "lon", "name", "type", "chain", "is_chain",
        "cuisine", "opening_hours", "wifi_label", "takeaway_label", "outdoor_label",
    ])

print("\n Источник 2 — Wikipedia (статический парсинг)...")
try:
    df_wiki = fetch_wiki_chains()
    print(f"   Спарсил {len(df_wiki)} сетей кофеен")
except Exception as e:
    print(f"   Че-то не то... {e}")
    df_wiki = pd.DataFrame(columns=["name", "founded", "locations", "city", "url", "founded_year"])

print("\n Источник 3 — 2GIS (Selenium)...")
try:
    df_gis = fetch_2gis_coffee()
    print(f"   Спарсил {len(df_gis)} заведений")
except Exception as e:
    print(f"  Че-то не то... {e}")
    df_gis = pd.DataFrame(columns=["name", "address", "rating_num", "reviews_num"])

print("\n Данные загружены. Запускаем дашборд...\n")


app = dash.Dash(__name__, suppress_callback_exceptions=True)
app.title = "Анализ конкурентов"

CARD = {"border": "1px solid #E5DCD2", "borderRadius": "8px",
        "padding": "16px", "background": "white"}
ROW  = {"display": "flex", "gap": "16px", "marginBottom": "16px"}

TABLE_STYLE = dict(
    page_size=15, sort_action="native", filter_action="native",
    style_cell={"padding": "6px 10px", "fontSize": "13px", "textAlign": "left"},
    style_header={"background": "#F5EEE5", "fontWeight": "600"},
)


def kpi(value, label):
    return html.Div([html.H2(str(value), style={"margin": 0}),
                     html.Small(label, style={"color": "#888"})],
                    style={**CARD, "flex": 1})


def block(title, *children, flex=1):
    return html.Div([html.H4(title), *children], style={**CARD, "flex": flex})


def md_card(text, flex=1):
    return html.Div(dcc.Markdown(text), style={**CARD, "flex": flex})


n_total   = len(df_osm)
n_chain   = int(df_osm["is_chain"].sum()) if not df_osm.empty else 0
n_indep   = n_total - n_chain
chain_pct = round(n_chain / n_total * 100) if n_total else 0
top_chain = (df_osm[df_osm["is_chain"]]["chain"].value_counts().head(1).index[0]
             if not df_osm.empty and n_chain else "—")
top_count = (int(df_osm[df_osm["is_chain"]]["chain"].value_counts().iloc[0])
             if not df_osm.empty and n_chain else 0)
avg_rating = (f"{df_gis['rating_num'].mean():.2f}"
              if not df_gis.empty and df_gis["rating_num"].notna().any() else "—")
most_rev   = (df_gis.sort_values("reviews_num", ascending=False).iloc[0]["name"]
              if not df_gis.empty and df_gis["reviews_num"].sum() > 0 else "—")


SUMMARY_MD = f"""
### Цель анализа
Изучить рынок кофеен Москвы - кто на нём играет, какие форматы есть,
в чём их сильные и слабые стороны.

### Чем мы отличаемся от конкурентов?
На рынке Москвы - {n_total} кофеен и кафе, из них {chain_pct}% сетевых.

### Какая будет стратегия выхода на рынок?
1) Точечный запуск - одна кофейня в выбранной по данным локации, а не сеть с первого дня.
2) Дифференциация по нишам - сначала нужно определить нашу особенность.
3) Локальное сообщество - партнёрство с близлежащими ВУЗами/БЦ (скидки сотрудникам, программы лояльности).
4) Знаем, что топ-конкурент по отзывам сейчас: «{most_rev}», средний рейтинг по 2GIS - {avg_rating}. Цель - войти в топ-10% по рейтингу за 6 месяцев.
"""


COMPETITORS = [
    ("Сетевой гигант",       f"Шоколадница, Кофе Хаус, {top_chain} ({top_count} точек)",
     "50+ точек, узнаваемый бренд, типовое меню, акцент на десертах",
     "Узнаваемость, лучшие локации, скорость",
     "Среднее качество кофе, шаблон",            "300–600 ₽"),
    ("Спешелти-сеть",        "Surf Coffee, Double B, Кооператив Чёрный",
     "10–30 точек, фокус на качестве зерна, бариста-культура",
     "Качество, лояльная ЦА, премиум-имидж",
     "Дороже, меньше точек, нужно образовывать клиента", "250–500 ₽"),
    ("Премиум-формат",       "Кофемания, Traveler's Coffee",
     "Ресторанный сервис в центре, бизнес-ланчи, винная карта",
     "Репутация, бизнес-аудитория, высокий чек",
     "Доступен не всем, не для регулярных визитов",       "500–1500 ₽"),
    ("Бюджетный takeaway",   "Cofix, One Price Coffee, Coffee Like",
     "Фиксированная низкая цена, формат «забежал-купил», у метро",
     "Цена, скорость, понятный продукт",
     "Нет места посидеть, базовое качество",              "99–200 ₽"),
    ("Независимая кофейня",  f"{n_indep} независимых по данным OSM",
     "1–3 точки в районе, авторская концепция, постоянные соседи-клиенты",
     "Лояльность местных, гибкость, атмосфера",
     "Ограниченный охват, зависимость от района",         "200–400 ₽"),
]


def competitor_md(name, examples, desc, pro, con, price):
    return (f"**{name}**  \n*{examples}*\n\n{desc}\n\n"
            f"**Плюсы:** {pro}  \n**Минусы:** {con}  \n**Чек:** {price}")


tab_summary = html.Div([
    md_card(SUMMARY_MD),
    html.H3("Портреты конкурентов"),
    html.Div([md_card(competitor_md(*c)) for c in COMPETITORS[:3]], style=ROW),
    html.Div([md_card(competitor_md(*c)) for c in COMPETITORS[3:]]
             + [html.Div(style={"flex": 1})], style=ROW),
])


tab_osm = html.Div([
    html.Div([
        dcc.Checklist(id="osm-type", inline=True,
            options=[{"label": f" {t} ", "value": t} for t in ["Кофейня", "Кафе"]],
            value=["Кофейня", "Кафе"]),
        dcc.RadioItems(id="osm-chain", inline=True,
            options=[{"label": " Все ", "value": "all"},
                     {"label": " Сетевые ", "value": "chain"},
                     {"label": " Независимые ", "value": "indep"}],
            value="all"),
    ], style={"display": "flex", "gap": "20px", "marginBottom": "16px"}),

    html.Div(id="osm-kpi", style=ROW),

    html.Div([
        block("Карта заведений", dcc.Graph(id="osm-map", style={"height": "420px"}), flex=2),
        block("Топ сетей", dcc.Graph(id="osm-bar-chains", style={"height": "420px"}), flex=1),
    ], style=ROW),
])


_n_year = int(df_wiki["founded_year"].notna().sum()) if not df_wiki.empty else 0
_oldest = int(df_wiki["founded_year"].min()) if _n_year else "—"

tab_wiki = html.Div([
    html.Div([
        kpi(len(df_wiki), "Сетей кофеен (рынок РФ)"),
        kpi(_n_year,      "С годом основания"),
        kpi(_oldest,      "Самая старая сеть"),
    ], style=ROW),

    block("Хронология появления сетей",
          dcc.Graph(id="wiki-timeline", style={"height": "420px"})),

    block("Все сети кофеен (Wikipedia)",
        dash_table.DataTable(
            id="wiki-table",
            data=(df_wiki.drop(columns=["url", "founded_year"], errors="ignore")
                  .to_dict("records")) if not df_wiki.empty else [],
            columns=[{"name": "Название", "id": "name"},
                     {"name": "Год основания", "id": "founded"},
                     {"name": "Точек", "id": "locations"},
                     {"name": "Город", "id": "city"}],
            **TABLE_STYLE,
        )),
])


tab_gis = html.Div([
    html.Div([
        kpi(len(df_gis),                                          "Кофеен найдено"),
        kpi(f"{df_gis['rating_num'].mean():.2f}"
            if not df_gis.empty and df_gis["rating_num"].notna().any() else "—",
            "Средний рейтинг"),
        kpi(int(df_gis["reviews_num"].sum()) if not df_gis.empty else 0,
            "Всего отзывов"),
    ], style=ROW),

    html.Div([
        block("Топ-20 по отзывам",
              dcc.Graph(id="gis-bar-reviews", style={"height": "420px"}), flex=2),
        block("Распределение рейтингов",
              dcc.Graph(id="gis-hist-rating", style={"height": "420px"}), flex=1),
    ], style=ROW),

    block("Все кофейни (2GIS)",
        dash_table.DataTable(
            id="gis-table",
            data=(df_gis[["name", "address", "rating", "reviews"]].to_dict("records")
                  if not df_gis.empty else []),
            columns=[{"name": "Название", "id": "name"},
                     {"name": "Адрес",    "id": "address"},
                     {"name": "Рейтинг",  "id": "rating"},
                     {"name": "Отзывов",  "id": "reviews"}],
            **TABLE_STYLE,
        )),
])


app.layout = html.Div([
    html.H1("Анализ конкурентов — кофейни Москвы",
            style={"padding": "16px 24px", "margin": 0, "fontSize": "20px",
                   "borderBottom": "1px solid #E5DCD2"}),

    dcc.Tabs(id="tabs", value="summary", children=[
        dcc.Tab(label="Выводы анализа",       value="summary"),
        dcc.Tab(label="OpenStreetMap (API)",  value="osm"),
        dcc.Tab(label="Wikipedia (статик)",   value="wiki"),
        dcc.Tab(label="2GIS (Selenium)",      value="gis"),
    ]),
    html.Div(id="tab-content", style={"padding": "20px 24px"}),
], style={"fontFamily": "system-ui, sans-serif", "background": "#FAF8F5",
          "minHeight": "100vh"})


@app.callback(Output("tab-content", "children"), Input("tabs", "value"))
def render_tab(tab):
    if tab == "osm":  return tab_osm
    if tab == "wiki": return tab_wiki
    if tab == "gis": return tab_gis
    return tab_summary


def _layout(fig):
    """Общий стиль для всех графиков."""
    fig.update_layout(margin=dict(r=10, t=10, l=10, b=10),
                      paper_bgcolor="white", plot_bgcolor="white")
    return fig


@app.callback(
    [Output("osm-kpi", "children"), Output("osm-map", "figure"),
     Output("osm-bar-chains", "figure")],
    [Input("osm-type", "value"), Input("osm-chain", "value")],
)
def update_osm(types, chain_val):
    fdf = df_osm.copy()
    if not fdf.empty:
        fdf = fdf[fdf["type"].isin(types or [])]
        if chain_val == "chain": fdf = fdf[fdf["is_chain"]]
        elif chain_val == "indep": fdf = fdf[~fdf["is_chain"]]

    total   = len(fdf)
    n_chain = int(fdf["is_chain"].sum()) if total else 0
    kpis = [
        kpi(total,           "Всего заведений"),
        kpi(n_chain,         "Сетевых"),
        kpi(total - n_chain, "Независимых"),
    ]

    empty = _layout(go.Figure())
    if fdf.empty:
        return kpis, empty, empty

    osm_map = px.scatter_map(fdf, lat="lat", lon="lon", hover_name="name",
        color="type", color_discrete_map=TYPE_COLORS,
        zoom=10, center={"lat": 55.7558, "lon": 37.6173})
    osm_map.update_layout(map_style="open-street-map")
    _layout(osm_map)

    top = fdf[fdf["is_chain"]]["chain"].value_counts().head(10).reset_index()
    top.columns = ["chain", "count"]
    bar = px.bar(top, x="count", y="chain", orientation="h",
                 color_discrete_sequence=[ORANGE])
    bar.update_layout(yaxis=dict(categoryorder="total ascending"),
                      xaxis_title="Точек", yaxis_title="")
    _layout(bar)
    return kpis, osm_map, bar


@app.callback(Output("wiki-timeline", "figure"), Input("tabs", "value"))
def update_wiki(tab):
    if tab != "wiki" or df_wiki.empty:
        return _layout(go.Figure())
    dated = df_wiki.dropna(subset=["founded_year"]).sort_values("founded_year")
    if dated.empty:
        return _layout(go.Figure())
    fig = px.scatter(dated, x="founded_year", y="name", text="name",
                     color_discrete_sequence=[ORANGE])
    fig.update_traces(textposition="middle right", marker=dict(size=10))
    fig.update_layout(yaxis=dict(showticklabels=False),
                      xaxis_title="Год основания", yaxis_title="")
    return _layout(fig)


@app.callback(
    [Output("gis-bar-reviews", "figure"), Output("gis-hist-rating", "figure")],
    Input("tabs", "value"),
)
def update_gis(tab):
    if tab != "gis" or df_gis.empty:
        return _layout(go.Figure()), _layout(go.Figure())

    top20 = df_gis.sort_values("reviews_num", ascending=False).head(20)
    bar = px.bar(top20, x="reviews_num", y="name", orientation="h",
                 color_discrete_sequence=[BROWN])
    bar.update_layout(yaxis=dict(categoryorder="total ascending"),
                      xaxis_title="Отзывов", yaxis_title="")

    rated = df_gis.dropna(subset=["rating_num"])
    hist = px.histogram(rated, x="rating_num", nbins=10,
                        color_discrete_sequence=[ORANGE])
    hist.update_layout(xaxis_title="Рейтинг", yaxis_title="Кол-во")
    return _layout(bar), _layout(hist)


if __name__ == "__main__":
    app.run(debug=True, port=8050)

