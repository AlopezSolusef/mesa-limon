"""
Mesa del limón persa · Ele Foods
Histórico de precios: descarga/actualiza datos de USDA, Banxico y UN Comtrade y genera dashboard.html.

Uso:
  py -m pip install requests python-dotenv truststore pandas
  py historico.py          <- la primera vez baja todo el histórico (~1 min); después solo lo nuevo
Abre dashboard.html en el navegador. Vuelve a correr el script para actualizarlo.
"""
import json, os, sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import truststore
truststore.inject_into_ssl()  # usa el almacén de certificados del sistema (en esta PC, el antivirus intercepta HTTPS)

import pandas as pd
import requests
from dotenv import load_dotenv

AQUI = Path(__file__).parent
DATOS = AQUI / "datos"
load_dotenv(AQUI / ".env")
USDA_KEY = os.getenv("USDA_KEY", "").strip()
BANXICO_TOKEN = os.getenv("BANXICO_TOKEN", "").strip()
COMTRADE_KEY = os.getenv("COMTRADE_KEY", "").strip()
CENSUS_KEY = os.getenv("CENSUS_KEY", "").strip()  # opcional: https://api.census.gov/data/key_signup.html

INICIO = 2010
HORA_CDMX = timezone(timedelta(hours=-6))  # México ya no cambia de horario; GitHub corre en UTC
ANIOS_COMPARACION = 10  # la estacionalidad compara contra los últimos 10 años (precios nominales)
KG_POR_CAJA_40LB = 40 * 0.45359237  # 18.144 kg
CALIBRES = ["110s", "150s", "175s", "200s", "230s", "250s"]
TIERS = ["Estándar", "Fine", "Fair"]

# FOB McAllen: limón mexicano cruzando por Texas (Phoenix Shipping Point Fruit, IX_FV110)
USDA_SLUG, USDA_DISTRITO = "2402", "MEXICO CROSSINGS THROUGH TEXAS"
# Banxico: FIX USD/MXN y EUR/MXN
# Mayoreo (mercados terminales) de limón mexicano: precio "puesto" en California y Nueva York
TERMINALES = {"la": ("2306", "Los Ángeles"), "ny": ("2314", "Nueva York")}
# SNIIM (Secretaría de Economía): limón sin semilla de primera, origen Veracruz, precio por kg en centrales de abasto.
# Referencia pública diaria para la compra en origen. Producto 426; destinos: Iztapalapa (100) y Jalapa (301).
SNIIM_MERCADOS = {"iztapalapa": ("100", "Central de Abasto CDMX (Iztapalapa)"), "jalapa": ("301", "Central de Abasto de Jalapa")}
BANXICO_SERIES = {"SF43718": "USD/MXN", "SF46410": "EUR/MXN"}
# Comtrade: HS 080550 (limones y limas), exportaciones de México (484)
EUROPA = {40, 56, 100, 191, 196, 203, 208, 233, 246, 251, 276, 300, 348, 372, 380, 428, 440, 442,
          470, 528, 616, 620, 642, 703, 705, 724, 752, 826, 757, 578}  # UE27 + Reino Unido, Suiza, Noruega


def get(url, **kw):
    r = requests.get(url, timeout=300, **kw)
    r.raise_for_status()
    return r.json()


def leer(nombre):
    p = DATOS / nombre
    return pd.read_csv(p) if p.exists() else None


def reciente(nombre, dias=7):
    """True si la fuente se descargó hace menos de `dias` (para fuentes mensuales).
    Se registra en datos/estado.json porque en GitHub la fecha de los archivos no sirve."""
    p = DATOS / "estado.json"
    estado = json.loads(p.read_text()) if p.exists() else {}
    fecha = estado.get(nombre)
    return (DATOS / nombre).exists() and fecha is not None and         (date.today() - date.fromisoformat(fecha)).days < dias


def marcar(nombre):
    p = DATOS / "estado.json"
    estado = json.loads(p.read_text()) if p.exists() else {}
    estado[nombre] = date.today().isoformat()
    p.write_text(json.dumps(estado, indent=1))


def guardar(df, nombre):
    DATOS.mkdir(exist_ok=True)
    df.to_csv(DATOS / nombre, index=False)


# ---------------------------------------------------------------- USDA

def num(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def precio_medio(fila):
    """Punto medio de 'mostly' si existe; si no, de low/high; si solo hay low, low."""
    for a, b in (("mostly_low_price", "mostly_high_price"), ("low_price", "high_price")):
        lo, hi = num(fila.get(a)), num(fila.get(b))
        if lo is not None:
            return (lo + (hi if hi is not None else lo)) / 2
    return None


def apariencia(fila):
    a = str(fila.get("appear") or fila.get("appearance") or "")
    return {"Fine Appearance": "Fine", "Fair Appearance": "Fair"}.get(a, "Estándar")


def es_fob(x):
    return (x.get("district") == USDA_DISTRITO and x.get("var") == "SEEDLESS TYPE"
            and x.get("pkg") == "40 lb cartons" and x.get("organic") != "Y" and x.get("item_size") in CALIBRES)


def es_terminal(x):
    # antes de 2023 USDA registraba la caja como "40 lb containers"
    return (x.get("origin") == "Mexico" and x.get("variety") == "SEEDLESS TYPE"
            and x.get("package") in ("40 lb cartons", "40 lb containers") and x.get("organic") != "Y"
            and x.get("item_size") in CALIBRES)


def actualizar_usda_reporte(slug, archivo, filtro, etiqueta, inicio=INICIO):
    """Baja/actualiza un reporte de USDA (solo filas de limón que pasan `filtro`)."""
    previo = leer(archivo)
    if previo is None:
        desde = date(inicio, 1, 1)
    else:  # re-baja 2 semanas para captar correcciones
        desde = pd.to_datetime(previo["fecha"]).max().date() - timedelta(days=14)
    filas = []
    for y in range(desde.year, date.today().year + 1):
        ini = max(desde, date(y, 1, 1))
        rango = f"{ini:%m/%d/%Y}:12/31/{y}"
        data = get(f"https://marsapi.ams.usda.gov/services/v1.2/reports/{slug}/Report Details",
                   params={"q": f"commodity=Limes;report_begin_date={rango}"}, auth=(USDA_KEY, ""))
        for x in data.get("results", []):
            if not filtro(x):
                continue
            filas.append({
                "fecha": datetime.strptime(x["report_date"], "%m/%d/%Y").date().isoformat(),
                "calibre": x["item_size"], "apariencia": apariencia(x),
                "low": num(x.get("low_price")), "high": num(x.get("high_price")),
                "mostly_low": num(x.get("mostly_low_price")), "mostly_high": num(x.get("mostly_high_price")),
                "precio": precio_medio(x),
            })
    nuevo = pd.DataFrame(filas)
    if previo is not None:
        nuevo = pd.concat([previo[previo["fecha"] < desde.isoformat()], nuevo])
    nuevo = (nuevo.dropna(subset=["precio"])
             .drop_duplicates(["fecha", "calibre", "apariencia"], keep="last")
             .sort_values(["fecha", "apariencia", "calibre"]))
    guardar(nuevo, archivo)
    print(f"  USDA {etiqueta}: {len(nuevo):,} filas, {nuevo['fecha'].min()} a {nuevo['fecha'].max()}")
    return nuevo


def actualizar_usda():
    return actualizar_usda_reporte(USDA_SLUG, "usda_fob_mcallen.csv", es_fob, "FOB McAllen")


def actualizar_terminales():
    """Precio de mayoreo (mercado terminal) del limón mexicano en Los Ángeles y Nueva York."""
    return {nombre: actualizar_usda_reporte(slug, f"usda_terminal_{clave}.csv", es_terminal, f"mayoreo {nombre}")
            for clave, (slug, nombre) in TERMINALES.items()}


# ---------------------------------------------------------------- Banxico

def actualizar_banxico():
    ids = ",".join(BANXICO_SERIES)
    data = get(f"https://www.banxico.org.mx/SieAPIRest/service/v1/series/{ids}/datos/{INICIO}-01-01/{date.today()}",
               headers={"Bmx-Token": BANXICO_TOKEN})
    filas = []
    for s in data["bmx"]["series"]:
        for d in s.get("datos", []):
            v = num(d["dato"])
            if v is not None:
                filas.append({"fecha": datetime.strptime(d["fecha"], "%d/%m/%Y").date().isoformat(),
                              "serie": BANXICO_SERIES[s["idSerie"]], "valor": v})
    df = pd.DataFrame(filas).pivot(index="fecha", columns="serie", values="valor").reset_index()
    guardar(df, "banxico_fx.csv")
    print(f"  Banxico FIX: {len(df):,} días, {df['fecha'].min()} a {df['fecha'].max()}")
    return df


# ---------------------------------------------------------------- Comtrade

def actualizar_comtrade():
    previo = leer("comtrade_mx_080550.csv")
    if reciente("comtrade_mx_080550.csv"):
        return previo
    # re-baja los últimos 2 años (Comtrade revisa cifras recientes)
    desde = INICIO if previo is None else int(str(previo["periodo"].max())[:4]) - 1
    filas = []
    for y in range(desde, date.today().year + 1):
        periodos = ",".join(f"{y}{m:02d}" for m in range(1, 13))
        data = get("https://comtradeapi.un.org/data/v1/get/C/M/HS",
                   params={"reporterCode": "484", "cmdCode": "080550", "flowCode": "X", "period": periodos,
                           "motCode": "0", "customsCode": "C00", "partner2Code": "0"},
                   headers={"Ocp-Apim-Subscription-Key": COMTRADE_KEY})
        for x in data.get("data", []):
            filas.append({"periodo": int(x["period"]), "socio": int(x["partnerCode"]),
                          "kg": x.get("netWgt"), "usd": x.get("primaryValue")})
    nuevo = pd.DataFrame(filas)
    if previo is not None:
        nuevo = pd.concat([previo[previo["periodo"] < desde * 100], nuevo])
    nuevo = nuevo.drop_duplicates(["periodo", "socio"], keep="last").sort_values(["periodo", "socio"])
    guardar(nuevo, "comtrade_mx_080550.csv")
    marcar("comtrade_mx_080550.csv")
    print(f"  Comtrade exportaciones MX: {nuevo['periodo'].nunique()} meses, {nuevo['periodo'].min()} a {nuevo['periodo'].max()}")
    return nuevo


def actualizar_comtrade_espejo():
    """Importaciones desde México reportadas por EE. UU. y Europa.
    México declara casi siempre el mismo USD/kg para todos los destinos, así que el precio
    por destino se toma del lado importador (valor en aduana; CIF en Europa)."""
    previo = leer("comtrade_espejo_080550.csv")
    if reciente("comtrade_espejo_080550.csv"):
        return previo
    desde = INICIO if previo is None else int(str(previo["periodo"].max())[:4]) - 1
    reporteros = ",".join(str(r) for r in sorted(EUROPA | {842}))
    filas = []
    for y in range(desde, date.today().year + 1):
        data = get("https://comtradeapi.un.org/data/v1/get/C/M/HS",
                   params={"reporterCode": reporteros, "partnerCode": "484", "cmdCode": "080550",
                           "flowCode": "M", "period": ",".join(f"{y}{m:02d}" for m in range(1, 13)),
                           "motCode": "0", "customsCode": "C00", "partner2Code": "0"},
                   headers={"Ocp-Apim-Subscription-Key": COMTRADE_KEY})
        for x in data.get("data", []):
            filas.append({"periodo": int(x["period"]), "reportero": int(x["reporterCode"]),
                          "kg": x.get("netWgt"), "usd": x.get("primaryValue")})
    nuevo = pd.DataFrame(filas)
    if previo is not None:
        nuevo = pd.concat([previo[previo["periodo"] < desde * 100], nuevo])
    nuevo = nuevo.drop_duplicates(["periodo", "reportero"], keep="last").sort_values(["periodo", "reportero"])
    guardar(nuevo, "comtrade_espejo_080550.csv")
    marcar("comtrade_espejo_080550.csv")
    print(f"  Comtrade importaciones desde MX: {nuevo['periodo'].nunique()} meses, hasta {nuevo['periodo'].max()}")
    return nuevo


# ---------------------------------------------------------------- SNIIM (México)

def sniim_consulta(destino_id, ini, fin):
    """Filas (fecha, mín, máx, frecuente) de limón persa de primera con origen Veracruz en un mercado."""
    import html, re
    url = ("http://www.economia-sniim.gob.mx/NUEVO/Consultas/MercadosNacionales/PreciosDeMercado/Agricolas/"
           "ResultadosConsultaFechaFrutasYHortalizas.aspx")
    # la combinación origen+destino no responde; se piden todos los orígenes y se filtra Veracruz
    r = requests.get(url, timeout=120, headers={"User-Agent": "Mozilla/5.0"}, params={
        "fechaInicio": f"{ini:%d/%m/%Y}", "fechaFinal": f"{fin:%d/%m/%Y}", "ProductoId": "426",
        "OrigenId": "-1", "Origen": "Todos", "DestinoId": destino_id, "Destino": "Todos",
        "PreciosPorId": "2", "RegistrosPorPagina": "1000"})
    r.raise_for_status()
    filas = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", r.text, re.S):
        c = [html.unescape(re.sub("<[^>]+>", "", x)).strip() for x in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)]
        if len(c) == 7 and re.match(r"\d\d/\d\d/\d{4}$", c[0]) and c[2] == "Veracruz":
            filas.append({"fecha": datetime.strptime(c[0], "%d/%m/%Y").date().isoformat(),
                          "min": num(c[3]), "max": num(c[4]), "frec": num(c[5])})
    return filas


def actualizar_sniim():
    """Precio diario del limón persa de Veracruz en centrales de abasto (MXN/kg)."""
    salida = {}
    for clave, (destino_id, nombre) in SNIIM_MERCADOS.items():
        archivo = f"sniim_{clave}.csv"
        previo = leer(archivo)
        desde = date(INICIO, 1, 1) if previo is None else pd.to_datetime(previo["fecha"]).max().date() - timedelta(days=14)
        filas = []
        for y in range(desde.year, date.today().year + 1):
            filas += sniim_consulta(destino_id, max(desde, date(y, 1, 1)), min(date.today(), date(y, 12, 31)))
        nuevo = pd.DataFrame(filas, columns=["fecha", "min", "max", "frec"])
        if previo is not None:
            nuevo = pd.concat([previo[previo["fecha"] < desde.isoformat()], nuevo])
        nuevo = nuevo.dropna(subset=["frec"]).drop_duplicates("fecha", keep="last").sort_values("fecha")
        guardar(nuevo, archivo)
        print(f"  SNIIM {nombre}: {len(nuevo):,} días, {nuevo['fecha'].min()} a {nuevo['fecha'].max()}")
        salida[clave] = nuevo
    return salida


def resumen_sniim(sniim):
    """Último precio de cada mercado (para el Pulso) y serie semanal (para la gráfica)."""
    out = {}
    for clave, df in (sniim or {}).items():
        if df is None or not len(df):
            continue
        d = df.assign(fecha=pd.to_datetime(df["fecha"])).set_index("fecha")
        u = d.iloc[-1]
        out[clave] = {"nombre": SNIIM_MERCADOS[clave][1], "fecha": d.index[-1].strftime("%d/%m/%Y"),
                      "frec": u["frec"], "min": u["min"], "max": u["max"],
                      "serie": serie_con_cortes(d["frec"].resample("W-FRI").mean())}
    return out


# ---------------------------------------------------------------- Census (EE. UU.)

def actualizar_census():
    """Importaciones de EE. UU. desde México de limón persa (Citrus latifolia).
    A diferencia de Comtrade, no mezcla limón amarillo ni limón con semilla. Requiere CENSUS_KEY.
    Fracciones HTS: 0805.50.3000 hasta 2023; desde 2024 se divide en 3020 (orgánico) y 3040 (convencional).
    Se suman para mantener la serie continua."""
    if not CENSUS_KEY or reciente("census_persa_mx.csv"):
        return leer("census_persa_mx.csv")
    filas = []
    for fraccion in ("0805503000", "0805503020", "0805503040"):
        r = requests.get("https://api.census.gov/data/timeseries/intltrade/imports/hs", timeout=300, params={
            "get": "GEN_VAL_MO,GEN_QY1_MO", "I_COMMODITY": fraccion, "CTY_CODE": "2010",
            "time": "from 2013-01", "key": CENSUS_KEY})
        r.raise_for_status()
        if r.status_code == 204 or not r.text.lstrip().startswith("["):
            continue
        datos = r.json()
        filas += [dict(zip(datos[0], x)) for x in datos[1:]]
    df = pd.DataFrame(filas)
    df = pd.DataFrame({"periodo": df["time"].str.replace("-", "").astype(int),
                       "kg": pd.to_numeric(df["GEN_QY1_MO"]), "usd": pd.to_numeric(df["GEN_VAL_MO"])})
    df = df.groupby("periodo")[["kg", "usd"]].sum().reset_index()
    df = df[df["kg"] > 0]
    guardar(df, "census_persa_mx.csv")
    marcar("census_persa_mx.csv")
    print(f"  Census limón persa desde MX: {len(df)} meses, {df['periodo'].min()} a {df['periodo'].max()}")
    return df


# ---------------------------------------------------------------- Dashboard

def serie_con_cortes(s):
    """Serie diaria -> listas x/y, cortando la línea donde hay más de 10 días sin reporte."""
    s = s.dropna()
    cortes = s.index[1:][(s.index[1:] - s.index[:-1]) > pd.Timedelta(days=10)] - pd.Timedelta(days=1)
    s = pd.concat([s, pd.Series(index=cortes, dtype=float)]).sort_index()
    return {"x": s.index.strftime("%Y-%m-%d").tolist(), "y": s.round(2).tolist()}


def cadena_precios(usda, terminales, fx_hoy):
    """Precio del limón mexicano en cada punto de la cadena: FOB McAllen y mayoreo LA / NY."""
    mercados = {"McAllen (FOB)": usda, **{f"Mayoreo {n}": df for n, df in terminales.items()}}
    series, ultimo = {}, {}
    for nombre, df in mercados.items():
        d = df.assign(fecha=pd.to_datetime(df["fecha"]))
        por_tier = d.groupby(["fecha", "apariencia"])["precio"].mean().unstack()
        # promedio semanal (semana que termina en viernes): suficiente para ver la historia y aligera la página
        series[nombre] = (serie_con_cortes(por_tier["Estándar"].resample("W-FRI").mean())
                          if "Estándar" in por_tier else {"x": [], "y": []})
        ultimo[nombre] = {}
        for t in TIERS:
            if t in por_tier:
                s = por_tier[t].dropna()
                s = s[s.index >= por_tier.index.max() - pd.Timedelta(days=14)]  # solo si es reciente
                if len(s):
                    ultimo[nombre][t] = {"usd": round(s.iloc[-1], 2), "fecha": s.index[-1].strftime("%d/%m/%Y"),
                                         "mxn_kg": round(s.iloc[-1] * fx_hoy / KG_POR_CAJA_40LB, 2)}
    return {"series": series, "ultimo": ultimo}


def construir(usda, fx, ct, espejo, census=None, terminales=None, sniim=None):
    fx = fx.assign(fecha=pd.to_datetime(fx["fecha"])).sort_values("fecha")
    u = usda.assign(fecha=pd.to_datetime(usda["fecha"]))

    # Precio diario por tier = promedio simple de calibres
    diario = u.groupby(["fecha", "apariencia"])["precio"].mean().unstack()
    diario = pd.merge_asof(diario.reset_index().sort_values("fecha"), fx[["fecha", "USD/MXN"]],
                           on="fecha", direction="backward").set_index("fecha")
    serie_fob = {}
    for t in TIERS:
        if t in diario:
            s = diario[[t, "USD/MXN"]].dropna()
            s = s.assign(mxn=s[t] * s["USD/MXN"] / KG_POR_CAJA_40LB)
            # huecos de más de 10 días sin reporte: se corta la línea en vez de unir con una recta
            cortes = s.index[1:][(s.index[1:] - s.index[:-1]) > pd.Timedelta(days=10)] - pd.Timedelta(days=1)
            s = pd.concat([s, pd.DataFrame(index=cortes)]).sort_index()
            serie_fob[t] = {"x": s.index.strftime("%Y-%m-%d").tolist(),
                            "usd": s[t].round(2).tolist(), "mxn": s["mxn"].round(2).tolist()}

    # Estacionalidad (Estándar, semanal)
    est = diario["Estándar"].dropna()
    sem = pd.DataFrame({"precio": est, "anio": est.index.isocalendar().year, "semana": est.index.isocalendar().week})
    sem = sem[sem["semana"] <= 52].groupby(["anio", "semana"])["precio"].mean().reset_index()
    anio = int(sem["anio"].max())
    base = sem[(sem["anio"] < anio) & (sem["anio"] >= anio - ANIOS_COMPARACION)]
    hist = base.groupby("semana")["precio"]
    estacional = {
        "semanas": list(range(1, 53)),
        "p10": hist.quantile(0.1).reindex(range(1, 53)).round(2).tolist(),
        "p90": hist.quantile(0.9).reindex(range(1, 53)).round(2).tolist(),
        "mediana": hist.median().reindex(range(1, 53)).round(2).tolist(),
        "actual": sem[sem["anio"] == anio].set_index("semana")["precio"].reindex(range(1, 53)).round(2).tolist(),
        "anterior": sem[sem["anio"] == anio - 1].set_index("semana")["precio"].reindex(range(1, 53)).round(2).tolist(),
        "anio": anio, "rango": f"{anio - ANIOS_COMPARACION}–{anio - 1}",
    }

    # KPIs
    ult = diario.index.max()
    fob_hoy = diario.loc[ult, "Estándar"]
    fx_hoy = fx["USD/MXN"].dropna().iloc[-1]
    def cerca(d):
        s = diario["Estándar"].dropna()
        return s.iloc[s.index.get_indexer([d], method="nearest")[0]]
    semana_ant, anio_ant = cerca(ult - timedelta(days=7)), cerca(ult - timedelta(days=364))
    semana_hoy = ult.isocalendar().week
    mismas = base[base["semana"] == semana_hoy]["precio"]
    percentil = round((mismas < fob_hoy).mean() * 100) if len(mismas) else None

    # Tabla último día
    tabla = (u[u["fecha"] == ult].pivot_table(index="calibre", columns="apariencia", values="precio")
             .reindex(index=CALIBRES, columns=[t for t in TIERS if t in u["apariencia"].unique()]))

    # Comtrade: volúmenes según México; se descartan registros absurdos (>10x la mediana del socio,
    # p. ej. 108,000 t a Países Bajos en may-2012)
    c = ct.copy()
    med = c[c["kg"] > 0].groupby("socio")["kg"].median()
    c = c[~((c["socio"] != 0) & (c["kg"] > 10 * c["socio"].map(med)))]
    c["destino"] = c["socio"].map(lambda s: "Mundo" if s == 0 else "EE. UU." if s == 842
                                  else "Europa" if s in EUROPA else "Otros")
    # suma por socio (no el total "Mundo", que conserva los registros absurdos)
    kg = c[c["destino"] != "Mundo"].groupby(["periodo", "destino"])["kg"].sum().unstack()
    kg = kg.reindex(columns=["EE. UU.", "Europa", "Otros"]).fillna(0)
    meses = pd.to_datetime(kg.index.astype(str), format="%Y%m").strftime("%Y-%m-01").tolist()
    comercio = {
        "x": meses,
        "eeuu": (kg["EE. UU."] / 1000).round(0).tolist(),
        "europa": (kg["Europa"] / 1000).round(0).tolist(),
        "otros": (kg["Otros"] / 1000).round(0).tolist(),
        "ultimo": pd.to_datetime(str(kg.index.max()), format="%Y%m").strftime("%m/%Y"),
    }
    # Precio de importación desde México (lado importador); Europa solo meses con >= 20 t
    e = espejo[espejo["kg"] > 0].copy()
    e["destino"] = e["reportero"].map(lambda r: "EE. UU." if r == 842 else "Europa")
    e = e.groupby(["periodo", "destino"])[["kg", "usd"]].sum().unstack()
    vu = (e["usd"] / e["kg"]).where(e["kg"] >= 20_000)
    comercio["vu_x"] = pd.to_datetime(e.index.astype(str), format="%Y%m").strftime("%Y-%m-01").tolist()
    comercio["fuente_eeuu"] = "UN Comtrade, HS 0805.50 (incluye limón amarillo y con semilla)"
    if census is not None and len(census):
        # EE. UU. con la fracción exclusiva de limón persa (Census)
        cs = census[census["kg"] > 0].set_index("periodo")
        vu["EE. UU."] = (cs["usd"] / cs["kg"]).reindex(vu.index)
        comercio["fuente_eeuu"] = "US Census, HTS 0805.50.30 (solo limón persa)"
        comercio["ultimo_census"] = pd.to_datetime(str(cs.index.max()), format="%Y%m").strftime("%m/%Y")
    comercio["vu_eeuu"] = vu["EE. UU."].round(3).tolist()
    comercio["vu_europa"] = vu["Europa"].round(3).tolist()

    cadena = cadena_precios(usda, terminales or {}, fx_hoy)

    datos = {
        "cadena": cadena,
        "origen": resumen_sniim(sniim),
        "insights": generar_insights(diario, sem, fx, kg, vu),
        "fob": serie_fob, "estacional": estacional, "comercio": comercio,
        "fx": (lambda w: {"x": w.index.strftime("%Y-%m-%d").tolist(),  # cierre semanal
                          "usd": w["USD/MXN"].round(3).tolist(), "eur": w["EUR/MXN"].round(3).tolist()})(
            fx.set_index("fecha")[["USD/MXN", "EUR/MXN"]].resample("W-FRI").last()),
        "kpi": {"fecha": ult.strftime("%d/%m/%Y"), "fob": round(fob_hoy, 2),
                "fob_mxn": round(fob_hoy * fx_hoy / KG_POR_CAJA_40LB, 2),
                "d_sem": round(fob_hoy - semana_ant, 2), "d_anio": round(fob_hoy - anio_ant, 2),
                "fx": round(fx_hoy, 4), "fx_fecha": fx["fecha"].iloc[-1].strftime("%d/%m/%Y"),
                "percentil": percentil, "semana": int(semana_hoy), "rango": estacional["rango"]},
        "tabla": {"cols": list(tabla.columns), "filas": [[i] + [None if pd.isna(v) else round(v, 2) for v in r]
                                                          for i, r in zip(tabla.index, tabla.values)],
                  "fx": fx_hoy, "kg": KG_POR_CAJA_40LB},
        "generado": datetime.now(HORA_CDMX).strftime("%d/%m/%Y %H:%M") + " (hora CDMX)",
    }
    html = (AQUI / "plantilla_dashboard.html").read_text(encoding="utf-8")
    html = html.replace("/*__DATOS__*/null", json.dumps(limpiar(datos), ensure_ascii=False, allow_nan=False, default=str))
    (AQUI / "dashboard.html").write_text(html, encoding="utf-8")
    (AQUI / "sitio").mkdir(exist_ok=True)
    (AQUI / "sitio" / "index.html").write_text(html, encoding="utf-8")  # lo que publica GitHub Pages
    print(f"  Dashboard: {AQUI / 'dashboard.html'}")


MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def usd(v):
    return f"${v:,.2f}"


def pct(v, d=0):
    return f"{v:+.{d}%}".replace("-", "−")


def generar_insights(diario, sem, fx, kg, vu):
    """Lecturas automáticas del mercado. Cada una: tono (alza/baja/neutro), título y texto."""
    out = []
    tono = lambda v, umbral=0.02: "alza" if v > umbral else "baja" if v < -umbral else "neutro"
    s = diario["Estándar"].dropna()
    ult, p = s.index[-1], s.iloc[-1]
    w, anio = int(ult.isocalendar().week), int(ult.isocalendar().year)
    hist = sem[(sem["anio"] < anio) & (sem["anio"] >= anio - ANIOS_COMPARACION)]

    # 1. Precio vs. la misma semana en años anteriores
    mismas = hist[hist["semana"] == w]["precio"]
    if len(mismas):
        med = mismas.median()
        out.append({"tono": tono(p / med - 1, 0.1),
                    "titulo": f"{'Arriba' if p > med else 'Abajo'} de lo normal para la semana {w}",
                    "texto": f"El FOB Estándar está en {usd(p)} por caja. La mediana de la semana {w} entre "
                             f"{int(hist['anio'].min())} y {anio - 1} fue {usd(med)} ({pct(p / med - 1)}). "
                             f"Solo {int((mismas > p).sum())} de {len(mismas)} años tuvieron un precio más alto esa semana."})

    # 2. Tendencia de 4 semanas y rango de 52 semanas
    hace4 = s[:ult - timedelta(days=28)]
    ult52 = s[ult - timedelta(days=364):]
    if len(hace4):
        cambio = p / hace4.iloc[-1] - 1
        pos = (p - ult52.min()) / (ult52.max() - ult52.min()) if ult52.max() > ult52.min() else 0.5
        out.append({"tono": tono(cambio),
                    "titulo": f"{'Subió' if cambio > 0 else 'Bajó'} {abs(cambio):.0%} en 4 semanas",
                    "texto": f"Hace 4 semanas estaba en {usd(hace4.iloc[-1])}. En los últimos 12 meses el precio "
                             f"se movió entre {usd(ult52.min())} y {usd(ult52.max())}; hoy está al "
                             f"{pos:.0%} de ese rango."})

    # 3. Qué suele pasar en las próximas 8 semanas
    tabla = hist.set_index(["anio", "semana"])["precio"]
    cambios = []
    for y in sorted(hist["anio"].unique()):
        w2, y2 = (w + 8, y) if w + 8 <= 52 else (w + 8 - 52, y + 1)
        if (y, w) in tabla.index and (y2, w2) in tabla.index and y2 < anio:
            cambios.append(tabla[(y2, w2)] / tabla[(y, w)] - 1)
    if len(cambios) >= 5:
        cambios = pd.Series(cambios)
        subio = int((cambios > 0).sum())
        out.append({"tono": tono(cambios.median()),
                    "titulo": f"Históricamente, las próximas 8 semanas suelen ir a la {'alza' if cambios.median() > 0 else 'baja'}",
                    "texto": f"De la semana {w} a la {w + 8 if w + 8 <= 52 else w + 8 - 52}, el precio subió en "
                             f"{subio} de {len(cambios)} años. Cambio mediano: {pct(cambios.median())} "
                             f"(entre {pct(cambios.min())} y {pct(cambios.max())}). Es un patrón, no un pronóstico."})

    # 4. Primas por calidad
    if {"Fine", "Fair"} <= set(diario.columns):
        d = diario[["Fine", "Estándar", "Fair"]].dropna()
        if len(d):
            hoy = d.iloc[-1]
            prima, prima_media = hoy["Fine"] - hoy["Estándar"], (d["Fine"] - d["Estándar"]).mean()
            out.append({"tono": "neutro",
                        "titulo": f"La calidad Fine paga {usd(prima)} más por caja",
                        "texto": f"Fine {usd(hoy['Fine'])}, Estándar {usd(hoy['Estándar'])}, Fair {usd(hoy['Fair'])} "
                                 f"({d.index[-1]:%d/%m/%Y}). Desde {d.index[0].year}, la prima promedio de Fine sobre "
                                 f"Estándar ha sido {usd(prima_media)}; hoy está "
                                 f"{'por encima' if prima > prima_media else 'por debajo'} de ese promedio."})

    # 5. Tipo de cambio: cuánto vale en pesos el mismo precio en dólares
    f = fx.set_index("fecha")["USD/MXN"].dropna()
    fx_hoy, fx_anio = f.iloc[-1], f[:f.index[-1] - timedelta(days=365)].iloc[-1]
    efecto = fx_hoy / fx_anio - 1
    out.append({"tono": tono(efecto),
                "titulo": f"El peso {'se depreció' if efecto > 0 else 'se apreció'} {abs(efecto):.1%} en un año",
                "texto": f"FIX {fx_hoy:.4f} hoy vs. {fx_anio:.4f} hace un año. Con el mismo precio en dólares, cada "
                         f"caja vale {abs(efecto):.1%} {'más' if efecto > 0 else 'menos'} en pesos. Hoy el FOB "
                         f"Estándar equivale a {p * fx_hoy / KG_POR_CAJA_40LB:.2f} MXN/kg."})

    # 6. Volumen exportado (Comtrade)
    total = kg.sum(axis=1)
    um = int(total.index.max())
    y_um, m_um = divmod(um, 100)
    if um - 100 in total.index:
        cambio = total[um] / total[um - 100] - 1
        ytd = total[(total.index >= y_um * 100) & (total.index <= um)].sum()
        ytd_ant = total[(total.index >= (y_um - 1) * 100) & (total.index <= um - 100)].sum()
        out.append({"tono": tono(cambio, 0.05),
                    "titulo": f"Exportaciones de {MESES[m_um - 1]} {y_um}: {pct(cambio)} vs. el año anterior",
                    "texto": f"México exportó {total[um] / 1000:,.0f} t en {MESES[m_um - 1]} {y_um} contra "
                             f"{total[um - 100] / 1000:,.0f} t un año antes. En lo que va de {y_um}: "
                             f"{ytd / 1000:,.0f} t ({pct(ytd / ytd_ant - 1, 1)} vs. el mismo periodo de {y_um - 1})."})

    # 7. Europa: participación y precio vs. EE. UU.
    ult12 = kg[kg.index > um - 100]
    part = ult12["Europa"].sum() / ult12.sum().sum()
    v = vu.dropna().tail(12)
    if len(v) >= 3:
        prima = v["Europa"].mean() / v["EE. UU."].mean() - 1
        out.append({"tono": "neutro",
                    "titulo": f"Europa recibe {part:.1%} del volumen y paga {abs(prima):.0%} {'más' if prima > 0 else 'menos'} por kilo",
                    "texto": f"En los últimos 12 meses con dato, el limón mexicano entró a Europa a "
                             f"{v['Europa'].mean():.2f} USD/kg (CIF, incluye flete marítimo) contra "
                             f"{v['EE. UU.'].mean():.2f} USD/kg en EE. UU. ({len(v)} meses comparables)."})
    return out


def limpiar(obj):
    """NaN -> None para que el JSON sea válido."""
    if isinstance(obj, float) and obj != obj:
        return None
    if isinstance(obj, dict):
        return {k: limpiar(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [limpiar(v) for v in obj]
    return obj


def main():
    if not (USDA_KEY and BANXICO_TOKEN and COMTRADE_KEY):
        sys.exit("Falta USDA_KEY, BANXICO_TOKEN o COMTRADE_KEY en el archivo .env")
    print("Actualizando datos...")
    usda = actualizar_usda()
    fx = actualizar_banxico()
    try:
        ct = actualizar_comtrade()
    except Exception as e:  # Comtrade es mensual; si falla, usa lo guardado
        print("  Comtrade no respondió, uso lo guardado:", e)
        ct = leer("comtrade_mx_080550.csv")
    try:
        espejo = actualizar_comtrade_espejo()
    except Exception as e:
        print("  Comtrade (espejo) no respondió, uso lo guardado:", e)
        espejo = leer("comtrade_espejo_080550.csv")
    try:
        census = actualizar_census()
    except Exception as e:
        print("  Census no respondió, uso lo guardado:", e)
        census = leer("census_persa_mx.csv")
    if census is None:
        print("  Census: sin CENSUS_KEY en .env; el precio de EE. UU. sigue saliendo de Comtrade")
    terminales = actualizar_terminales()
    try:
        sniim = actualizar_sniim()
    except Exception as e:  # si el SNIIM no responde, usa lo guardado
        print("  SNIIM no respondió, uso lo guardado:", e)
        sniim = {k: leer(f"sniim_{k}.csv") for k in SNIIM_MERCADOS}
    construir(usda, fx, ct, espejo, census, terminales, sniim)


if __name__ == "__main__":
    main()
