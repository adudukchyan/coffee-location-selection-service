
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
GREEN  = "#4A7C59"
BLUE   = "#3D5A8A"
TYPE_COLORS = {"Метро": BLUE, "Ж/д": GREEN, "ТЦ": ORANGE}


HEADERS = {"User-Agent": "Mozilla/5.0 (CoffeeSpot/1.0)"}




# ИСТОЧНИК 1 — OpenStreetMap




def fetch_osm(lat=55.7558, lon=37.6173, radius_m=10000):
    query = f"""
    [out:json][timeout:60];
    (
      node["railway"="station"](around:{radius_m},{lat},{lon});
      node["station"="subway"](around:{radius_m},{lat},{lon});
      node["public_transport"="station"]["subway"="yes"](around:{radius_m},{lat},{lon});
      node["shop"="mall"](around:{radius_m},{lat},{lon});
      node["amenity"="marketplace"](around:{radius_m},{lat},{lon});
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
            resp = requests.post(url, data={"data": query},
                                 headers={"User-Agent": "CoffeeSpotAnalytics/1.0",
                                          "Content-Type": "application/x-www-form-urlencoded"},
                                 timeout=60)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            print(f"   зеркало недоступно: {e}")
    raise RuntimeError("Все зеркала Overpass недоступны")




def parse_osm(raw):
    records = []
    for el in raw.get("elements", []):
        if el.get("lat") is None:
            continue
        tags = el.get("tags", {})
        if tags.get("station") == "subway" or tags.get("subway") == "yes":
            cat = "Метро"
        elif tags.get("railway") == "station":
            cat = "Ж/д"
        elif tags.get("shop") == "mall" or tags.get("amenity") == "marketplace":
            cat = "ТЦ"
        else:
            continue
        records.append({
            "lat": el["lat"], "lon": el["lon"],
            "name": tags.get("name", "Без названия"),
            "category": cat,
        })
    return pd.DataFrame(records)






# ИСТОЧНИК 2 — moskva-map.ru




def fetch_districts():
    """На moskva-map.ru структура: <p>...округ...</p> + <table> с районами.
    Сайт в кодировке windows-1251."""
    url = "http://moskva-map.ru/rajony-moscow.htm"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.encoding = "windows-1251"  # сайт не в utf-8
    soup = BeautifulSoup(resp.text, "html.parser")


    results = []
    # <p> с упоминанием округа → следующий <table> с районами
    for p in soup.find_all("p"):
        m = re.search(r"(\w[\w\-\s]*?административный округ)",
                      p.get_text(strip=True), re.IGNORECASE)
        if not m:
            continue
        okrug = m.group(1).replace(" Москвы", "").strip()


        table = p.find_next("table")
        if table is None:
            continue
        for cell in table.find_all(["td", "th"]):
            for line in cell.get_text("\n", strip=True).split("\n"):
                name = line.replace("Район", "").strip()
                if 2 < len(name) < 50:
                    results.append({"district": name, "okrug": okrug})


    return pd.DataFrame(results, columns=["district", "okrug"])




# ИСТОЧНИК 3 — 2GIS




GIS_MALL_URL = "https://2gis.ru/moscow/search/%D0%A2%D0%BE%D1%80%D0%B3%D0%BE%D0%B2%D1%8B%D0%B5%20%D1%86%D0%B5%D0%BD%D1%82%D1%80%D1%8B"




def fetch_malls(max_pages=3):
    driver = webdriver.Chrome()
    results, seen = [], set()


    for page in range(1, max_pages + 1):
        url = GIS_MALL_URL if page == 1 else f"{GIS_MALL_URL}/page/{page}"
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
            addr, rating = "—", "—"
            for ln in lines:
                if ln == name:
                    continue
                if rating == "—" and re.fullmatch(r"\d[.,]?\d*", ln):
                    rating = ln.replace(",", ".")
                if (addr == "—" and "," in ln and any(c.isdigit() for c in ln)
                        and "оцен" not in ln.lower() and "отзыв" not in ln.lower()):
                    addr = ln[:100]


            results.append({"name": name, "address": addr, "rating": rating})
            page_count += 1


        print(f"  2GIS стр.{page}: +{page_count} ТЦ")
        if page_count == 0:
            break


    driver.quit()
    df = pd.DataFrame(results, columns=["name", "address", "rating"])
    df["rating_num"] = pd.to_numeric(df["rating"], errors="coerce")
    return df




# ЗАГРУЗКА ИСТОЧНИКОВ




print("\n Источник 1 — OpenStreetMap (метро, ж/д, ТЦ)...")
try:
    df_osm = parse_osm(fetch_osm())
    print(f"    {len(df_osm)} объектов")
except Exception as e:
    print(f"    {e}")
    df_osm = pd.DataFrame(columns=["lat", "lon", "name", "category"])


print("\n Источник 2 — moskva-map.ru: районы Москвы (статика)...")
try:
    df_districts = fetch_districts()
    print(f"    {len(df_districts)} районов в {df_districts['okrug'].nunique()} округах")
except Exception as e:
    print(f"    {e}")
    df_districts = pd.DataFrame(columns=["district", "okrug"])


print("\n Источник 3 — 2GIS Торговые центры (Selenium)...")
print("   (откроется окно Chrome — это нормально, ~30 сек)")
try:
    df_malls = fetch_malls()
    print(f"    {len(df_malls)} торговых центров")
except Exception as e:
    print(f"    {e}")
    df_malls = pd.DataFrame(columns=["name", "address", "rating", "rating_num"])






#ВЫВОДЫ




CONCLUSIONS = [
    {
        "q": "Какие данные были собраны?",
        "a": (
            "На первом этапе были собраны географические данные по Москве из нескольких источников. "
            "Из OpenStreetMap получены объекты с координатами: станции метро, железнодорожные станции, "
            "торговые центры и рынки. С помощью Selenium из 2GIS были получены данные о торговых центрах: "
            "названия, адреса и рейтинги. Также были собраны данные о районах Москвы и административных округах."
        ),
    },
    {
        "q": "Как данные были подготовлены для анализа?",
        "a": (
            "Собранные данные были преобразованы в таблицы pandas. Объекты из OpenStreetMap были разделены "
            "на категории: метро, железнодорожные станции и торговые центры. Для каждого объекта сохранены "
            "координаты, название и тип. Данные из 2GIS структурированы по названию, адресу и рейтингу, "
            "а рейтинг дополнительно преобразован в числовой формат."
        ),
    },
    {
        "q": "Почему торговые центры учитываются в анализе?",
        "a": (
            "Торговые центры рассматриваются как точки притяжения людей и индикатор коммерческой активности. "
            "Расположение рядом с ТЦ может быть перспективным для кофейни, так как такие места обычно связаны "
            "с повышенным потоком посетителей."
        ),
    },
    {
        "q": "Как используются данные о районах Москвы?",
        "a": (
            "Данные о районах и административных округах используются как справочная территориальная основа. "
            "Они помогают структурировать город и в дальнейшем могут использоваться для группировки объектов "
            "по территориям, но сами по себе не являются главным критерием выбора места."
        ),
    },
    {
        "q": "Какие предварительные выводы можно сделать?",
        "a": (
            "На текущем этапе можно предположить, что наиболее перспективными для размещения кофейни являются зоны "
            "с высокой концентрацией точек притяжения: метро, железнодорожных станций, торговых центров и рынков. "
            "Эти объекты могут указывать на повышенный пешеходный и потребительский поток."
        ),
    },
]
print("\n  Данные собраны и подготовлены для первичного географического анализа.\n")


print("OSM-объекты:", df_osm.shape)
print("Районы Москвы:", df_districts.shape)
print("ТЦ из 2GIS:", df_malls.shape)


print("\n Предварительные выводы:")
for item in CONCLUSIONS:
    print("\n" + item["q"])
    print(item["a"])

# DASHBOARD

app = dash.Dash(__name__, suppress_callback_exceptions=True)
app.title = "Географический анализ"

BLOCK = {
    "border": "1px solid #E5DCD2",
    "borderRadius": "8px",
    "padding": "16px",
    "background": "white",
    "marginBottom": "16px",
}

ROW = {
    "display": "flex",
    "gap": "16px",
    "marginBottom": "16px",
}


def kpi(value, label):
    return html.Div(
        [
            html.Div(
                str(value),
                style={"fontSize": "28px", "fontWeight": "600"},
            ),
            html.Div(
                label,
                style={"fontSize": "12px", "color": "#888"},
            ),
        ],
        style={**BLOCK, "flex": "1", "marginBottom": 0},
    )


def block(title, *children, flex=1):
    return html.Div(
        [
            html.H4(
                title,
                style={"margin": "0 0 10px", "fontSize": "14px"},
            ),
            *children,
        ],
        style={**BLOCK, "flex": flex, "marginBottom": 0},
    )


def qa_block(item):
    return html.Div(
        [
            html.Div(
                item["q"],
                style={
                    "fontWeight": "600",
                    "color": BROWN,
                    "marginBottom": "6px",
                    "fontSize": "14px",
                },
            ),
            html.Div(
                item["a"],
                style={
                    "fontSize": "13px",
                    "color": "#444",
                    "lineHeight": "1.6",
                },
            ),
        ],
        style={**BLOCK, "marginBottom": "12px"},
    )


TABLE_STYLE = dict(
    page_size=15,
    sort_action="native",
    filter_action="native",
    style_cell={
        "textAlign": "left",
        "padding": "6px 10px",
        "fontSize": "13px",
        "whiteSpace": "normal",
        "height": "auto",
    },
    style_header={
        "background": "#F5EEE5",
        "fontWeight": "600",
        "border": "none",
    },
)


def clean_fig(fig):
    fig.update_layout(
        margin=dict(r=10, t=10, l=10, b=10),
        paper_bgcolor="white",
        plot_bgcolor="white",
    )
    return fig


# ВКЛАДКА 1 — ВЫВОДЫ

tab_summary = html.Div(
    [
        block(
            "Цель анализа",
            html.Div(
                "Определить, какие городские объекты могут указывать "
                "на перспективность места для открытия кофейни: метро, "
                "железнодорожные станции, торговые центры, рынки и районы Москвы.",
                style={"fontSize": "13px", "lineHeight": "1.6"},
            ),
        ),

        html.H3(
            "Предварительные выводы",
            style={"fontSize": "16px", "marginTop": "20px"},
        ),

        *[qa_block(item) for item in CONCLUSIONS],
    ]
)


# ВКЛАДКА 2 — КАРТА OSM

tab_osm = html.Div(
    [
        html.Div(
            [
                dcc.Checklist(
                    id="geo-cat",
                    inline=True,
                    options=[
                        {"label": " Метро ", "value": "Метро"},
                        {"label": " Ж/д ", "value": "Ж/д"},
                        {"label": " ТЦ ", "value": "ТЦ"},
                    ],
                    value=["Метро", "Ж/д", "ТЦ"],
                ),
            ],
            style={"marginBottom": "16px"},
        ),

        html.Div(id="geo-kpi", style=ROW),

        html.Div(
            [
                block(
                    "Карта точек притяжения",
                    dcc.Graph(id="geo-map", style={"height": "430px"}),
                    flex=2,
                ),
                block(
                    "Тепловая карта плотности объектов OSM",
                    dcc.Graph(id="geo-heat", style={"height": "430px"}),
                    flex=1,
                ),
            ],
            style=ROW,
        ),
    ]
)


# ВКЛАДКА 3 — РАЙОНЫ МОСКВЫ

if not df_districts.empty:
    dist_by_okrug = df_districts["okrug"].value_counts().reset_index()
    dist_by_okrug.columns = ["okrug", "count"]
else:
    dist_by_okrug = pd.DataFrame(columns=["okrug", "count"])


if not dist_by_okrug.empty:
    dist_fig = px.bar(
        dist_by_okrug,
        x="count",
        y="okrug",
        orientation="h",
        color_discrete_sequence=[BLUE],
    )
    dist_fig.update_layout(
        yaxis=dict(categoryorder="total ascending"),
        xaxis_title="Количество районов",
        yaxis_title="Округ",
    )
    dist_fig = clean_fig(dist_fig)
else:
    dist_fig = clean_fig(go.Figure())


tab_districts = html.Div(
    [
        html.Div(
            [
                kpi(len(df_districts), "Районов всего"),
                kpi(
                    df_districts["okrug"].nunique()
                    if not df_districts.empty
                    else 0,
                    "Округов",
                ),
            ],
            style=ROW,
        ),

        block(
            "Количество районов по административным округам",
            dcc.Graph(figure=dist_fig, style={"height": "420px"}),
        ),

        block(
            "Таблица районов Москвы",
            dash_table.DataTable(
                data=df_districts.to_dict("records")
                if not df_districts.empty
                else [],
                columns=[
                    {"name": "Район", "id": "district"},
                    {"name": "Округ", "id": "okrug"},
                ],
                **TABLE_STYLE,
            ),
        ),
    ]
)


# ВКЛАДКА 4 — ТОРГОВЫЕ ЦЕНТРЫ 2GIS

tab_malls = html.Div(
    [
        html.Div(
            [
                kpi(len(df_malls), "ТЦ найдено"),
                kpi(
                    f"{df_malls['rating_num'].mean():.2f}"
                    if not df_malls.empty and df_malls["rating_num"].notna().any()
                    else "—",
                    "Средний рейтинг",
                ),
                kpi("Selenium + BS4", "Метод сбора"),
            ],
            style=ROW,
        ),

        block(
            "Распределение рейтингов торговых центров",
            dcc.Graph(id="mall-hist", style={"height": "340px"}),
        ),

        block(
            "Таблица торговых центров из 2GIS",
            dash_table.DataTable(
                data=df_malls[["name", "address", "rating"]].to_dict("records")
                if not df_malls.empty
                else [],
                columns=[
                    {"name": "Название", "id": "name"},
                    {"name": "Адрес", "id": "address"},
                    {"name": "Рейтинг", "id": "rating"},
                ],
                **TABLE_STYLE,
            ),
        ),
    ]
)


# ОСНОВНОЙ ВИД DASHBOARD

app.layout = html.Div(
    [
        html.H1(
            "Географический анализ для выбора локации кофейни",
            style={
                "padding": "16px 24px",
                "margin": 0,
                "fontSize": "22px",
                "borderBottom": "1px solid #E5DCD2",
                "color": BROWN,
            },
        ),

        dcc.Tabs(
            id="tabs",
            value="summary",
            children=[
                dcc.Tab(label="Выводы", value="summary"),
                dcc.Tab(label="Карта OSM", value="osm"),
                dcc.Tab(label="Районы Москвы", value="districts"),
                dcc.Tab(label="ТЦ 2GIS", value="malls"),
            ],
        ),

        html.Div(
            id="tab-content",
            style={"padding": "20px 24px"},
        ),
    ],
    style={
        "fontFamily": "system-ui, sans-serif",
        "background": "#FAF8F5",
        "minHeight": "100vh",
    },
)


# CALLBACKS

@app.callback(
    Output("tab-content", "children"),
    Input("tabs", "value"),
)
def render_tab(tab):
    if tab == "osm":
        return tab_osm
    if tab == "districts":
        return tab_districts
    if tab == "malls":
        return tab_malls
    return tab_summary


@app.callback(
    [
        Output("geo-kpi", "children"),
        Output("geo-map", "figure"),
        Output("geo-heat", "figure"),
    ],
    Input("geo-cat", "value"),
)
def update_osm(selected_categories):
    fdf = df_osm.copy()

    if not fdf.empty:
        fdf = fdf[fdf["category"].isin(selected_categories or [])]

    kpis = [
        kpi(len(fdf), "Точек на карте"),
        kpi(
            int((fdf["category"] == "Метро").sum()) if not fdf.empty else 0,
            "Метро",
        ),
        kpi(
            int((fdf["category"] == "Ж/д").sum()) if not fdf.empty else 0,
            "Ж/д",
        ),
        kpi(
            int((fdf["category"] == "ТЦ").sum()) if not fdf.empty else 0,
            "ТЦ",
        ),
    ]

    empty_fig = clean_fig(go.Figure())

    if fdf.empty:
        return kpis, empty_fig, empty_fig

    center = {"lat": 55.7558, "lon": 37.6173}

    map_fig = px.scatter_map(
        fdf,
        lat="lat",
        lon="lon",
        hover_name="name",
        color="category",
        color_discrete_map=TYPE_COLORS,
        zoom=10,
        center=center,
    )

    map_fig.update_layout(
        map_style="open-street-map",
    )

    heat_fig = go.Figure(
        go.Densitymap(
            lat=fdf["lat"],
            lon=fdf["lon"],
            radius=20,
            showscale=False,
        )
    )

    heat_fig.update_layout(
        map_style="open-street-map",
        map_center=center,
        map_zoom=10,
    )

    return kpis, clean_fig(map_fig), clean_fig(heat_fig)


@app.callback(
    Output("mall-hist", "figure"),
    Input("tabs", "value"),
)
def update_malls(tab):
    if tab != "malls" or df_malls.empty:
        return clean_fig(go.Figure())

    rated = df_malls.dropna(subset=["rating_num"])

    if rated.empty:
        return clean_fig(go.Figure())

    fig = px.histogram(
        rated,
        x="rating_num",
        nbins=10,
        color_discrete_sequence=[ORANGE],
    )

    fig.update_layout(
        xaxis_title="Рейтинг",
        yaxis_title="Количество ТЦ",
    )

    return clean_fig(fig)


if __name__ == "__main__":
    app.run(debug=True, port=8052)