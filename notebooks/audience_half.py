"""
Анализ целевой аудитории — потенциальные посетители кофейни в Москве

Планируемые источники данных:
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



# ИСТОЧНИК 2 — mskvuz.com (requests + BS4) — СТАТИЧЕСКИЙ ПАРСИНГ
#   список ВУЗов Москвы → концентрация студенческой ЦА


# ИСТОЧНИК 3 — 2GIS (Selenium + BS4) — ДИНАМИЧЕСКИЙ ПАРСИНГ
#   бизнес-центры Москвы → концентрация офисной ЦА

GIS_BC_URL = "https://2gis.ru/moscow/search/%D0%91%D0%B8%D0%B7%D0%BD%D0%B5%D1%81-%D1%86%D0%B5%D0%BD%D1%82%D1%80%D1%8B"



# ═════════════════════════════════════════════════════════════
# ЗАГРУЗКА ИСТОЧНИКОВ
# ═════════════════════════════════════════════════════════════

def load_data():
    print("\nЗагрузка данных для анализа целевой аудитории...")

    try:
        df_osm = parse_osm(fetch_osm())
        print(f"OpenStreetMap: {len(df_osm)} объектов")
    except Exception as e:
        print(f"Ошибка OpenStreetMap: {e}")
        df_osm = pd.DataFrame(columns=["lat", "lon", "name", "category"])

    df_osm.to_csv(DATA_DIR / "audience_osm.csv", index=False)

    summary = {
        "total_osm": len(df_osm),
        "osm_counts": df_osm["category"].value_counts().to_dict() if not df_osm.empty else {}
    }

    print("Данные загружены и сохранены в папку data/")
    return df_osm, summary


df_osm, summary = load_data()
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


def _fig(fig):
    fig.update_layout(margin=dict(r=10, t=10, l=10, b=10),
                      paper_bgcolor="white", plot_bgcolor="white")
    return fig

# ── Вкладка 1: Портреты ЦА ──────────────────────────────────



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

    content_block(
        "Предварительный вывод",
        html.P(
            "На текущем этапе выполнен первичный анализ городской инфраструктуры Москвы на основе OpenStreetMap. "
            "Предварительно можно предположить, что для кофейни наиболее перспективны локации рядом с университетами "
            "и офисными объектами, поскольку они формируют устойчивый ежедневный поток потенциальных посетителей.",
            style={"fontSize": "13px", "color": "#444", "lineHeight": "1.6"},
        ),
    ),
])
# ── Вкладка 3: mskvuz.com (ВУЗы) ────────────────────────────

# ── Вкладка 4: 2GIS (бизнес-центры) ─────────────────────────



# ── Layout ────────────────────────────────────────────────

app.layout = html.Div(
    [
        html.H1(
            "Анализ целевой аудитории — кофейни Москвы",
            style={
                "padding": "16px 24px",
                "margin": 0,
                "fontSize": "20px",
                "borderBottom": "1px solid #E5DCD2",
            },
        ),
        html.Div(tab_osm, style={"padding": "20px 24px"}),
    ],
    style={
        "fontFamily": "system-ui, sans-serif",
        "background": "#FAF8F5",
        "minHeight": "100vh",
    },
)


# ═════════════════════════════════════════════════════════════
# CALLBACKS
# ═════════════════════════════════════════════════════════════

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