"""
Mesa del limón persa · Ele Foods
Prueba 1: tipo de cambio (Banxico) + precios de limón (USDA) + spread de exportación.

Uso:
  1) py -m pip install requests python-dotenv truststore
  2) Crea un archivo .env junto a este script (USDA_KEY, BANXICO_TOKEN)
  3) py desk_prueba.py
Opcional: py desk_prueba.py 15.5   <- precio de origen en MXN/kg para calcular el spread
"""
import json, os, sys
from datetime import date, datetime, timedelta

import truststore
truststore.inject_into_ssl()  # usa el almacén de certificados de Windows (Banxico no envía su cert intermedio)

import requests
from dotenv import load_dotenv

load_dotenv()
USDA_KEY = os.getenv("USDA_KEY", "").strip()
BANXICO_TOKEN = os.getenv("BANXICO_TOKEN", "").strip()

USDA_BASE = "https://marsapi.ams.usda.gov/services/v1.2"
KG_POR_CAJA_40LB = 40 * 0.45359237  # 18.144 kg

# Referencia principal: FOB McAllen, limón mexicano cruzando por Texas (precio en frontera)
FOB = ("2402", "MEXICO CROSSINGS THROUGH TEXAS")
# Referencias secundarias: mercados terminales (precio de mayoreo ya puesto en la ciudad de EE. UU.)
TERMINALES = {
    "2306": "Los Angeles",
    "2290": "Chicago",
    "2314": "New York",
    "2310": "Miami",
}


def tipo_de_cambio():
    """Último tipo de cambio FIX (serie SF43718)."""
    url = "https://www.banxico.org.mx/SieAPIRest/service/v1/series/SF43718/datos/oportuno"
    r = requests.get(url, headers={"Bmx-Token": BANXICO_TOKEN}, timeout=30)
    r.raise_for_status()
    dato = r.json()["bmx"]["series"][0]["datos"][0]
    return float(dato["dato"].replace(",", "")), dato["fecha"]


def usda(path, params=None):
    r = requests.get(f"{USDA_BASE}{path}", params=params, auth=(USDA_KEY, ""), timeout=120)
    r.raise_for_status()
    return r.json()


def filas_limon_mexico(slug, dias=14, distrito=None):
    """Filas de limón de México en caja de 40 lb del último día publicado del reporte."""
    hoy = date.today()
    rango = f"{(hoy - timedelta(days=dias)):%m/%d/%Y}:{hoy:%m/%d/%Y}"
    data = usda(f"/reports/{slug}", {"q": f"report_begin_date={rango}", "allSections": "true"})
    json.dump(data, open(f"usda_{slug}_raw.json", "w"), indent=1, default=str)
    detalle = [x for sec in data if sec.get("reportSection") == "Report Details"
               for x in sec.get("results", [])]
    filas = []
    for x in detalle:
        # Terminales usan package/variety/origin; shipping point usa pkg/var/district
        paquete = x.get("package") or x.get("pkg") or ""
        if x.get("commodity") != "Limes" or paquete != "40 lb cartons" or x.get("organic") == "Y":
            continue
        if distrito and x.get("district") != distrito:
            continue
        if not distrito and x.get("origin") != "Mexico":
            continue
        filas.append(x)
    if not filas:
        return None, []
    ultima = max(filas, key=lambda x: datetime.strptime(x["report_date"], "%m/%d/%Y"))["report_date"]
    return ultima, [x for x in filas if x["report_date"] == ultima]


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


def resumen(filas):
    """Promedio simple por calibre -> USD/caja 40 lb."""
    precios = [p for p in map(precio_medio, filas) if p is not None]
    return sum(precios) / len(precios) if precios else None


TIERS = ("Fine", "Estándar", "Fair")


def apariencia(fila):
    """Tier de apariencia de USDA: 'Fine Appearance', 'Fair Appearance' o N/A (estándar)."""
    a = str(fila.get("appear") or fila.get("appearance") or "")
    return a.split()[0] if a.endswith("Appearance") else "Estándar"


def a_mxn_kg(usd_caja, fx):
    return usd_caja * fx / KG_POR_CAJA_40LB


def main():
    if not USDA_KEY or not BANXICO_TOKEN:
        sys.exit("Falta USDA_KEY o BANXICO_TOKEN en el archivo .env")
    origen = float(sys.argv[1]) if len(sys.argv) > 1 else None

    print("\n== 1. Banxico ==")
    fx, fecha = tipo_de_cambio()
    print(f"Tipo de cambio FIX: {fx:.4f} MXN/USD ({fecha})")

    print("\n== 2. USDA · FOB McAllen (limón de México cruzando por Texas, caja 40 lb) ==")
    slug, distrito = FOB
    fecha_fob, filas = filas_limon_mexico(slug, distrito=distrito)
    fob = resumen(filas)
    tiers_fob = {}
    if fob is None:
        print(f"  Sin filas de limón en el reporte {slug} en los últimos 14 días.")
    else:
        print(f"  Reporte {slug}, {fecha_fob}")
        print(f"  {'calibre':>8} {'apariencia':<11} {'low':>5} {'high':>5} {'mostly':>7} {'medio':>6} {'MXN/kg':>7}")
        for f in sorted(filas, key=lambda f: (str(f.get("item_size")), -(precio_medio(f) or 0))):
            p = precio_medio(f)
            mostly = f"{f.get('mostly_low_price') or ''}-{f.get('mostly_high_price') or ''}".strip("-")
            print(f"  {f.get('item_size'):>8} {apariencia(f):<11} {f.get('low_price') or '':>5}"
                  f" {f.get('high_price') or '':>5} {mostly:>7} {p:>6.2f} {a_mxn_kg(p, fx):>7.2f}")
        for tier in TIERS:
            p = resumen([f for f in filas if apariencia(f) == tier])
            if p is not None:
                tiers_fob[tier] = p
                print(f"  FOB {tier:<9}: {p:6.2f} USD/caja = {a_mxn_kg(p, fx):6.2f} MXN/kg")
        print(f"  FOB promedio : {fob:6.2f} USD/caja = {a_mxn_kg(fob, fx):6.2f} MXN/kg")

    print("\n== 3. USDA · Mercados terminales (limón de México, caja 40 lb, referencia) ==")
    for slug_t, ciudad in TERMINALES.items():
        try:
            fecha_t, filas_t = filas_limon_mexico(slug_t)
        except Exception as e:
            print(f"  {ciudad:<12} error {e}")
            continue
        p = resumen(filas_t)
        if p is None:
            print(f"  {ciudad:<12} sin datos")
        else:
            print(f"  {ciudad:<12} {fecha_t}  {p:6.2f} USD/caja = {a_mxn_kg(p, fx):6.2f} MXN/kg  ({len(filas_t)} calibres)")

    print("\n== 4. Spread de exportación (FOB McAllen - origen) ==")
    if fob is None:
        print("  Sin precio FOB de USDA; revisa usda_2402_raw.json.")
    elif origen is None:
        print(f"  FOB McAllen: {a_mxn_kg(fob, fx):.2f} MXN/kg")
        print("  Agrega el precio de origen al correr: py desk_prueba.py 15.5")
    else:
        print(f"  Precio origen: {origen:.2f} MXN/kg")
        for nombre, usd in [*tiers_fob.items(), ("promedio", fob)]:
            fob_mxn = a_mxn_kg(usd, fx)
            print(f"  {nombre:<9}: FOB {fob_mxn:6.2f} - origen {origen:6.2f} = SPREAD {fob_mxn - origen:6.2f} MXN/kg")
        print("  (bruto: antes de empaque, flete a frontera, cruce y agente aduanal)")


if __name__ == "__main__":
    main()
