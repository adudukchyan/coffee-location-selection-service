"""
Анализ целевой аудитории — потенциальные посетители кофейни в Москве

Источники данных:
  1. OpenStreetMap (Overpass API)     — ВУЗы, школы, офисы, жильё (карта)
  2. mskvuz.com    (requests + BS4)   — статический парсинг списка ВУЗов Москвы
  3. 2GIS          (Selenium + BS4)   — динамический парсинг бизнес-центров

Целевая аудитория кофейни — это B2C: конечные потребители разных групп
(студенты, офисные сотрудники, фрилансеры, родители с детьми, туристы).
"""

import time
from pathlib import Path
import dash
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
from bs4 import BeautifulSoup
from dash import dcc, html, dash_table
from dash.dependencies import Input, Output
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

MOSCOW_LAT = 55.7558
MOSCOW_LON = 37.6173
OSM_RADIUS_M = 8000
MAX_BC_PAGES = 7
DASH_PORT = 8051
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
BROWN = "#6F4E37"
LIGHT = "#C8A882"
ORANGE = "#C8783A"
GREEN = "#4A7C59"
BLUE = "#3D5A8A"
CAT_COLORS = {
    "Университет": BLUE,
    "Школа": LIGHT,
    "Офис": ORANGE,
}
HEADERS = {"User-Agent": "Mozilla/5.0 (CoffeeSpot/1.0)"}
# ═════════════════════════════════════════════════════════════
# ИСТОЧНИК 1 — OpenStreetMap (Overpass API)
#   объекты притяжения ЦА: ВУЗы, школы, офисы, жилые дома
# ═════════════════════════════════════════════════════════════

def fetch_osm(lat=MOSCOW_LAT, lon=MOSCOW_LON, radius_m=OSM_RADIUS_M):
    query = f"""
    [out:json][timeout:60];
    (
      node["amenity"="university"](around:{radius_m},{lat},{lon});
      node["amenity"="college"](around:{radius_m},{lat},{lon});
      node["amenity"="school"](around:{radius_m},{lat},{lon});
      node["office"](around:{radius_m},{lat},{lon});
    );
    out body;

    """
    servers = [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
        "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    ]
    for url in servers:
        try:
            resp = requests.post(
                url,
                data={"data": query},
                headers={
                    "User-Agent": "CoffeeSpotAnalytics/1.0",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                timeout=60,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            print(f"Overpass недоступен: {url} -> {e}")
    raise RuntimeError("Не удалось получить данные OpenStreetMap ни с одного зеркала")


def parse_osm(raw):
    records = []
    for el in raw.get("elements", []):
        if el.get("lat") is None:
            continue
        tags = el.get("tags", {})
        if tags.get("amenity") in ("university", "college"):
            cat = "Университет"
        elif tags.get("amenity") == "school":
            cat = "Школа"
        elif tags.get("office"):
            cat = "Офис"
        else:
            continue
        records.append({
            "lat": el["lat"], "lon": el["lon"],
            "name": tags.get("name", "Без названия"),
            "category": cat,
        })
    return pd.DataFrame(records)

# ИСТОЧНИК 2
# список ВУЗов Москвы → концентрация студенческой ЦА


def fetch_msk_universities():
    urls = [
        "https://mskvuz.com/",
        "https://mskvuz.com/gosudarstvennye-vuzy",
    ]
    results = []
    seen = set()
    for url in urls:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
        except Exception as e:
            print(f"Не удалось открыть {url}: {e}")
            continue

        for el in soup.find_all(class_="card-title"):
            name = el.get_text(strip=True)

            if len(name) < 6 or name in seen:
                continue

            seen.add(name)

            lower_name = name.lower()
            if "университет" in lower_name:
                kind = "Университет"
            elif "институт" in lower_name:
                kind = "Институт"
            elif "академия" in lower_name:
                kind = "Академия"
            else:
                kind = "Другое"

            results.append({"name": name, "kind": kind})

    return pd.DataFrame(results, columns=["name", "kind"])

# ИСТОЧНИК 3 — 2GIS
#   бизнес-центры Москвы → концентрация офисной ЦА
GIS_BC_URL = "https://2gis.ru/moscow/search/%D0%91%D0%B8%D0%B7%D0%BD%D0%B5%D1%81-%D1%86%D0%B5%D0%BD%D1%82%D1%80%D1%8B"

def fetch_business_centers(max_pages=MAX_BC_PAGES):
    driver = webdriver.Chrome()
    results, seen = [], set()

    for page in range(1, max_pages + 1):
        url = GIS_BC_URL if page == 1 else f"{GIS_BC_URL}/page/{page}"
        driver.get(url)
        try:
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "a[href*='/firm/']"))
            )
        except Exception:
            time.sleep(8)

        for _ in range(8):
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
            txt = name
            for _ in range(5):
                if container is None:
                    break
                txt = container.get_text("\n", strip=True)
                if any("," in l and any(c.isdigit() for c in l)
                       for l in txt.split("\n") if l != name):
                    break
                container = container.find_parent()

            lines = [l.strip() for l in txt.split("\n") if l.strip()]
            addr = "—"
            for ln in lines:
                if (ln != name and "," in ln and any(c.isdigit() for c in ln)
                        and "оцен" not in ln.lower() and "отзыв" not in ln.lower()):
                    addr = ln[:100]
                    break

            results.append({"name": name, "address": addr})
            page_count += 1

        print(f"  2GIS стр.{page}: +{page_count} БЦ")
        if page_count == 0:
            break

    driver.quit()
    return pd.DataFrame(results, columns=["name", "address"])


# ═════════════════════════════════════════════════════════════
# ЗАГРУЗКА ИСТОЧНИКОВ
# ═════════════════════════════════════════════════════════════

def save_dataframes(df_osm, df_uni, df_bc):
    df_osm.to_csv(DATA_DIR / "audience_osm.csv", index=False)
    df_uni.to_csv(DATA_DIR / "audience_universities.csv", index=False)
    df_bc.to_csv(DATA_DIR / "audience_business_centers.csv", index=False)


def build_summary(df_osm, df_uni, df_bc):
    total_osm = len(df_osm)
    total_uni = len(df_uni)
    total_bc = len(df_bc)

    osm_counts = df_osm["category"].value_counts().to_dict() if not df_osm.empty else {}

    audience_focus = []
    if osm_counts.get("Университет", 0) > 0 or total_uni > 0:
        audience_focus.append("студенты")
    if osm_counts.get("Офис", 0) > 0 or total_bc > 0:
        audience_focus.append("офисные сотрудники")
    if osm_counts.get("Школа", 0) > 0:
        audience_focus.append("семьи с детьми")

    if not audience_focus:
        audience_focus.append("смешанная городская аудитория")

    summary = {
        "total_osm": total_osm,
        "total_uni": total_uni,
        "total_bc": total_bc,
        "osm_counts": osm_counts,
        "audience_focus": audience_focus,
    }
    return summary


def load_data():
    print("\nЗагрузка данных для анализа целевой аудитории...")

    try:
        df_osm = parse_osm(fetch_osm())
        print(f"OpenStreetMap: {len(df_osm)} объектов")
    except Exception as e:
        print(f"Ошибка OpenStreetMap: {e}")
        df_osm = pd.DataFrame(columns=["lat", "lon", "name", "category"])

    try:
        df_uni = fetch_msk_universities()
        print(f"mskvuz.com: {len(df_uni)} вузов")
    except Exception as e:
        print(f"Ошибка mskvuz.com: {e}")
        df_uni = pd.DataFrame(columns=["name", "kind"])

    try:
        df_bc = fetch_business_centers()
        print(f"2GIS: {len(df_bc)} бизнес-центров")
    except Exception as e:
        print(f"Ошибка 2GIS: {e}")
        df_bc = pd.DataFrame(columns=["name", "address"])

    save_dataframes(df_osm, df_uni, df_bc)
    summary = build_summary(df_osm, df_uni, df_bc)

    print("Данные загружены и сохранены в папку data/")
    return df_osm, df_uni, df_bc, summary


# ═════════════════════════════════════════════════════════════
# ПЕРСОНАЖИ ЦЕЛЕВОЙ АУДИТОРИИ
# ═════════════════════════════════════════════════════════════
# Сформированы на основе собранных данных:
# - Много ВУЗов → большая студенческая аудитория
# - Много БЦ → офисная аудитория
# - Жилые дома → семьи / молодые мамы

PERSONAS = [

    {

        "emoji": "🎓",

        "name": "Студент, 19–23 года",

        "tag": "Студенческая аудитория",

        "desc": "Ищет недорогой кофе рядом с вузом, часто берет напитки навынос или заходит между парами.",

        "budget": "200–350 ₽",

    },

    {

        "emoji": "💼",

        "name": "Офисный сотрудник, 24–35 лет",

        "tag": "Деловая аудитория",

        "desc": "Покупает кофе утром по пути на работу или во время короткого перерыва. Важны скорость и стабильное качество.",

        "budget": "250–450 ₽",

    },

    {

        "emoji": "💻",

        "name": "Фрилансер, 25–35 лет",

        "tag": "Гибкий формат потребления",

        "desc": "Может использовать кофейню как место для работы или встреч. Ценит Wi-Fi, розетки и спокойную атмосферу.",

        "budget": "300–600 ₽",

    },

    {

        "emoji": "👨‍👩‍👧",

        "name": "Жители района, 28–45 лет",

        "tag": "Локальная аудитория",

        "desc": "Заходят в кофейню рядом с домом во время прогулки или по пути по делам. Ожидают понятное меню и комфортный формат.",

        "budget": "250–500 ₽",

    },

]

df_osm, df_uni, df_bc, summary = load_data()
# ═════════════════════════════════════════════════════════════
# DASH
# ═════════════════════════════════════════════════════════════

app = dash.Dash(__name__, suppress_callback_exceptions=True)
app.title = "Целевая аудитория"

CARD_STYLE = {
    "border": "1px solid #E5DCD2",
    "borderRadius": "8px",
    "padding": "16px",
    "background": "white",
    "marginBottom": "16px",
}

ROW_STYLE = {
    "display": "flex",
    "gap": "16px",
    "marginBottom": "16px",
}


def kpi(value, label):
    return html.Div(
        [
            html.Div(str(value), style={"fontSize": "28px", "fontWeight": "600"}),
            html.Div(label, style={"fontSize": "12px", "color": "#888"}),
        ],
        style={**CARD_STYLE, "flex": "1", "marginBottom": 0},
    )


def content_block(title, *children, flex=1):
    return html.Div(
        [
            html.H4(title, style={"margin": "0 0 10px", "fontSize": "14px"}),
            *children,
        ],
        style={**CARD_STYLE, "flex": flex, "marginBottom": 0},
    )


TABLE_STYLE = dict(
    page_size=15, sort_action="native", filter_action="native",
    style_cell={"textAlign": "left", "padding": "6px 10px", "fontSize": "13px"},
    style_header={"background": "#F5EEE5", "fontWeight": "600", "border": "none"},
)


def _fig(fig):
    fig.update_layout(margin=dict(r=10, t=10, l=10, b=10),
                      paper_bgcolor="white", plot_bgcolor="white")
    return fig


def persona_card(persona):
    return html.Div(
        [
            html.Div(
                persona["emoji"],
                style={"fontSize": "38px", "textAlign": "center", "marginBottom": "8px"},
            ),
            html.Div(
                persona["name"],
                style={"fontWeight": "700", "fontSize": "15px", "textAlign": "center"},
            ),
            html.Div(
                persona["tag"],
                style={
                    "fontSize": "12px",
                    "color": ORANGE,
                    "textAlign": "center",
                    "marginBottom": "8px",
                },
            ),
            html.Div(
                persona["desc"],
                style={"fontSize": "13px", "color": "#444", "lineHeight": "1.4"},
            ),
            html.Div(
                f"Средний чек: {persona['budget']}",
                style={
                    "fontSize": "12px",
                    "color": "#888",
                    "marginTop": "10px",
                    "textAlign": "center",
                    "fontWeight": "600",
                },
            ),
        ],
        style={**CARD_STYLE, "flex": "1", "marginBottom": 0, "minHeight": "190px"},
    )


# ── Вкладка 1: Портреты ЦА ──────────────────────────────────

tab_personas = html.Div([
    content_block(
        "Целевая аудитория кофейни",
        html.Div(
            [
                html.P(
                    "Для проекта кофейни в Москве основная модель — B2C. "
                    "По собранным данным наиболее заметны студенческая и офисная аудитория, "
                    "а также жители района как постоянные гости."
                ),
                html.Ul(
                    [
                        html.Li("Студенты — чувствительны к цене и удобству локации"),
                        html.Li("Офисные сотрудники — ценят скорость и стабильность"),
                        html.Li("Фрилансеры — важен комфорт для короткой работы"),
                        html.Li("Жители района — формируют повторный спрос"),
                    ]
                ),
            ],
            style={"fontSize": "13px", "color": "#444", "lineHeight": "1.6"},
        ),
    ),

    html.Div(
        [
            kpi(summary["total_osm"], "Объектов инфраструктуры"),
            kpi(summary["total_uni"], "Вузов"),
            kpi(summary["total_bc"], "Бизнес-центров"),
        ],
        style=ROW_STYLE,
    ),

    content_block(
        "Основной вывод по аудитории",
        html.P(
            "На основе собранных данных можно сделать вывод, что основную целевую аудиторию будущей кофейни составляют студенты и офисные сотрудники, так как именно они формируют регулярный поток спроса в течение дня. Вторичными сегментами выступают фрилансеры и жители района, для которых важны комфорт и удобство локации. Это означает, что кофейню целесообразно ориентировать на быстрые повседневные покупки и размещать рядом с вузами, бизнес-центрами и точками высокой проходимости.",
            style={"fontSize": "13px", "color": "#444", "lineHeight": "1.6"},
        ),
    ),

    html.H3("Сегменты целевой аудитории", style={"fontSize": "16px", "marginTop": "20px"}),
    html.Div([persona_card(p) for p in PERSONAS[:2]], style=ROW_STYLE),
    html.Div([persona_card(p) for p in PERSONAS[2:]], style=ROW_STYLE),
])

# ── Вкладка 2: OSM (карта объектов) ─────────────────────────

tab_osm = html.Div([
    html.Div([
        dcc.Checklist(
            id="aud-cat",
            inline=True,
            options=[{"label": f" {c} ", "value": c} for c in ["Университет", "Школа", "Офис"]],
            value=["Университет", "Школа", "Офис"],
        ),
    ], style={"marginBottom": "16px"}),
    html.Div(id="aud-kpi", style=ROW_STYLE),
    html.Div([
        content_block("Карта объектов", dcc.Graph(id="aud-map", style={"height": "420px"}), flex=2),
        content_block("По категориям", dcc.Graph(id="aud-bar-cat", style={"height": "420px"}), flex=1),
    ], style=ROW_STYLE),
])
# ── Вкладка 3: mskvuz.com (ВУЗы) ────────────────────────────

_uni_by_kind = (df_uni["kind"].value_counts().reset_index()
                if not df_uni.empty else pd.DataFrame(columns=["kind", "count"]))
if not _uni_by_kind.empty:
    _uni_by_kind.columns = ["kind", "count"]

tab_uni = html.Div([
    html.Div([
        kpi(len(df_uni),                       "ВУЗов в каталоге"),
        kpi(int((df_uni["kind"] == "Университет").sum())
            if not df_uni.empty else 0,        "Университетов"),
        kpi(int((df_uni["kind"] == "Институт").sum())
            if not df_uni.empty else 0,        "Институтов"),
        kpi(int((df_uni["kind"] == "Академия").sum())
            if not df_uni.empty else 0,        "Академий"),
    ], style=ROW_STYLE),

    content_block("Распределение по типу",
          dcc.Graph(
              id="uni-pie",
              figure=_fig(px.pie(_uni_by_kind, names="kind", values="count",
                                  color="kind",
                                  color_discrete_sequence=[BLUE, ORANGE, GREEN, LIGHT])
                          .update_traces(textposition="inside", textinfo="percent+label"))
              if not _uni_by_kind.empty else _fig(go.Figure()),
              style={"height": "340px"},
          )),

    content_block("Все ВУЗы Москвы (mskvuz.com)",
        dash_table.DataTable(
            id="uni-table",
            data=df_uni.to_dict("records") if not df_uni.empty else [],
            columns=[{"name": "Название", "id": "name"},
                     {"name": "Тип",      "id": "kind"}],
            **TABLE_STYLE,
        )),
])

# ── Вкладка 4: 2GIS (бизнес-центры) ─────────────────────────

tab_bc = html.Div([
    html.Div([
        kpi(len(df_bc),     "Бизнес-центров"),
        kpi("2GIS",         "Источник"),
        kpi("Selenium+BS4", "Метод парсинга"),
    ], style=ROW_STYLE),

    content_block("Все бизнес-центры Москвы (2GIS)",
        dash_table.DataTable(
            id="bc-table",
            data=df_bc.to_dict("records") if not df_bc.empty else [],
            columns=[{"name": "Название", "id": "name"},
                     {"name": "Адрес",    "id": "address"}],
            **TABLE_STYLE,
        )),
])

# ── Layout ────────────────────────────────────────────────

app.layout = html.Div([
    html.H1("Анализ целевой аудитории — кофейни Москвы",
            style={"padding": "16px 24px", "margin": 0, "fontSize": "20px",
                   "borderBottom": "1px solid #E5DCD2"}),

    dcc.Tabs(id="tabs", value="personas", children=[
        dcc.Tab(label="Портреты ЦА",                 value="personas"),
        dcc.Tab(label="OpenStreetMap (API)",         value="osm"),
        dcc.Tab(label="ВУЗы Москвы (статик)",        value="uni"),
        dcc.Tab(label="БЦ (Selenium 2GIS)",          value="bc"),
    ]),
    html.Div(id="tab-content", style={"padding": "20px 24px"}),
], style={"fontFamily": "system-ui, sans-serif", "background": "#FAF8F5",
          "minHeight": "100vh"})


# ═════════════════════════════════════════════════════════════
# CALLBACKS
# ═════════════════════════════════════════════════════════════

@app.callback(Output("tab-content", "children"), Input("tabs", "value"))
def render_tab(tab):
    if tab == "osm": return tab_osm
    if tab == "uni": return tab_uni
    if tab == "bc":  return tab_bc
    return tab_personas


@app.callback(
    [Output("aud-kpi", "children"), Output("aud-map", "figure"),
     Output("aud-bar-cat", "figure")],
    Input("aud-cat", "value"),
)
def update_osm(cats):
    filtered_df = df_osm.copy()

    if not filtered_df.empty:
        filtered_df = filtered_df[filtered_df["category"].isin(cats or [])]

    kpis = [
        kpi(len(filtered_df), "Всего объектов"),
        kpi(int((filtered_df["category"] == "Университет").sum()), "ВУЗов"),
        kpi(int((filtered_df["category"] == "Школа").sum()), "Школ"),
        kpi(int((filtered_df["category"] == "Офис").sum()), "Офисов"),
    ]

    empty_figure = _fig(go.Figure())
    if filtered_df.empty:
        return kpis, empty_figure, empty_figure

    map_fig = px.scatter_map(
        filtered_df,
        lat="lat",
        lon="lon",
        hover_name="name",
        color="category",
        color_discrete_map=CAT_COLORS,
        zoom=10,
        center={"lat": MOSCOW_LAT, "lon": MOSCOW_LON},
    )
    map_fig.update_layout(map_style="open-street-map")

    counts_df = filtered_df["category"].value_counts().reset_index()
    counts_df.columns = ["category", "count"]

    bar_fig = px.bar(
        counts_df,
        x="count",
        y="category",
        orientation="h",
        color="category",
        color_discrete_map=CAT_COLORS,
    )
    bar_fig.update_layout(
        showlegend=False,
        xaxis_title="Количество",
        yaxis_title="",
        yaxis=dict(categoryorder="total ascending"),
    )

    return kpis, _fig(map_fig), _fig(bar_fig)


if __name__ == "__main__":
    app.run(debug=True, port=DASH_PORT, use_reloader=False)
