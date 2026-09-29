"""
Mesa del limón persa · Ele Foods
Correo diario con el Pulso McAllen. Lee pulso.json (lo genera historico.py) y lo envía por SMTP.

Uso:
  py enviar_correo.py --vista     <- solo genera correo_vista.html para revisarlo, no envía nada
  py enviar_correo.py --prueba    <- envía solo a la cuenta remitente (SMTP_USUARIO)
  py enviar_correo.py             <- envía a CORREO_PARA (con copia a CORREO_CC)

Variables (en GitHub van como Secrets):
  SMTP_USUARIO   cuenta que envía (p. ej. un Gmail dedicado)
  SMTP_CLAVE     contraseña de aplicación de esa cuenta
  CORREO_PARA    destinatarios, separados por coma
  CORREO_CC      copia, separados por coma (opcional)
  SMTP_SERVIDOR  opcional, por defecto smtp.gmail.com (puerto 587)
"""
import json, os, smtplib, sys
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from html import escape
from pathlib import Path

from dotenv import load_dotenv

AQUI = Path(__file__).parent
load_dotenv(AQUI / ".env")
URL_DASHBOARD = "https://alopezsolusef.github.io/mesa-limon/"
HOY = datetime.now(timezone(timedelta(hours=-6)))  # hora CDMX

MARCA, MARCA_F = "#2e7d32", "#1b5e20"  # verde Ele Foods (estructura); los positivos van en negro, lo negativo en rojo
TINTA, SEC, MUTED, BORDE, FONDO, VERDE, ROJO, AZUL = (
    "#0b0b0b", "#52514e", "#7a7974", "#e3e2de", "#f4f3f0", "#1f7a3d", "#b3261e", "#2a78d6")


def peso(v, d=0):
    return f"{'−' if v < 0 else ''}${abs(v):,.{d}f}"


def grafica_holgura(hh, anios=2):
    """PNG de la holgura histórica (precio máximo pagable vs. SNIIM), últimos `anios` años, para incrustar en el correo."""
    import io
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    import pandas as pd

    d = pd.DataFrame({"max": hh["max"], "sniim": hh["sniim"]}, index=pd.to_datetime(hh["x"])).dropna()
    d = d[d.index >= d.index.max() - pd.DateOffset(years=anios)]
    azul, naranja = "#2a78d6", "#eb6834"
    fig, ax = plt.subplots(figsize=(6.0, 2.5), dpi=200)
    fig.patch.set_facecolor("#ffffff")
    ax.fill_between(d.index, d["max"], d["sniim"], where=d["max"] >= d["sniim"], color=azul, alpha=0.08, linewidth=0)
    ax.plot(d.index, d["max"], color=azul, linewidth=1.8, label="Precio máximo pagable")
    ax.plot(d.index, d["sniim"], color=naranja, linewidth=1.8, label="SNIIM (referencia de la fruta)")
    for serie, color in ((d["max"], azul), (d["sniim"], naranja)):  # valor de la última semana
        ax.annotate(f"${serie.iloc[-1]:.0f}", (serie.index[-1], serie.iloc[-1]), xytext=(4, 0), textcoords="offset points",
                    va="center", fontsize=7, color=color, fontweight="bold")
    meses = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
    ax.xaxis.set_major_locator(mdates.MonthLocator(bymonth=[1, 4, 7, 10]))
    ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{meses[mdates.num2date(x).month - 1]} {mdates.num2date(x):%y}"))
    ax.set_ylim(0, max(d["max"].max(), d["sniim"].max()) * 1.12)
    ax.set_ylabel("MXN por kg", fontsize=7, color="#52514e")
    ax.tick_params(labelsize=7, colors="#52514e", length=0)
    ax.grid(axis="y", color="#ecebe7", linewidth=0.8)
    for lado in ("top", "right", "left"):
        ax.spines[lado].set_visible(False)
    ax.spines["bottom"].set_color("#e3e2de")
    ax.legend(loc="upper left", fontsize=7, frameon=False, ncol=2, bbox_to_anchor=(0, 1.15))
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()


def tarjeta(etiqueta, valor, detalle, color=TINTA):
    return f"""<td style="padding:6px;width:50%;vertical-align:top">
  <div style="border:1px solid {BORDE};border-radius:8px;padding:12px 14px;background:#ffffff">
    <div style="font-size:12px;color:{SEC}">{etiqueta}</div>
    <div style="font-size:24px;font-weight:700;color:{color};margin:2px 0">{valor}</div>
    <div style="font-size:12px;color:{MUTED}">{detalle}</div>
  </div></td>"""


def armar(d, src_grafica="cid:holgura"):
    p, costos = d["pulso"], d["costos"]
    color = TINTA if p["margen"] >= 0 else ROJO
    fecha_hoy = HOY.strftime("%d/%m/%Y")
    h = p["holgura"]
    signo_h = "+" if h >= 0 else "−"
    color_h = TINTA if h >= 0 else ROJO
    ctx = p.get("contexto", {})
    aviso = {"sin": "⚠️ Sin holgura · ", "baja": "⚠️ Holgura baja · "}.get(ctx.get("semaforo"), "")
    asunto = f"{aviso}Pulso McAllen · {fecha_hoy} · holgura {signo_h}${abs(h):.2f}/kg · margen est. {peso(p['margen'])}/caja"

    def cambio(v, etiqueta):  # ▲ $0.29 vs. el 25/09
        if v is None:
            return ""
        flecha, col = ("▲", TINTA) if v > 0.005 else ("▼", ROJO) if v < -0.005 else ("=", SEC)
        return f"<span style='color:{col}'>{flecha} ${abs(v):.2f}</span> {etiqueta}"
    partes = [cambio(ctx.get("vs_ayer"), f"vs. el {ctx.get('fecha_ayer', '')}"),
              cambio(ctx.get("vs_semana"), "vs. hace una semana")]
    if "pct_temporada" in ctx:
        partes.append(f"mejor que el {ctx['pct_temporada']}% de las semanas de esta temporada ({ctx['rango_temporada']})")
    linea_contexto = " · ".join(x for x in partes if x)
    alerta_html = (f"<div style='font-size:13px;font-weight:700;color:{ROJO};margin-bottom:4px'>"
                   f"{aviso.replace(' · ', '')}: por debajo de ${ctx.get('umbral', 2)}/kg</div>") if aviso else ""

    fuentes = (f"Precio FOB McAllen del {p['fecha_usda']} ({p['tier']}): {p['usd_caja']:.2f} USD/caja × FIX {p['fx']:.4f} "
               f"({d['fx_fecha']}) · fruta a {p['precio_fruta_kg']:.2f} MXN/kg ({escape(p['fuente_fruta'])})")
    desglose = "".join(
        f"<tr><td style='padding:4px 0;color:{SEC}'>{escape(n)}</td><td style='padding:4px 0;text-align:right'>{v:,.2f}</td></tr>"
        for n, v in p["conceptos"])
    hh = d.get("holgura_hist")
    grafica = "" if not (hh and src_grafica) else f"""<tr><td style="padding:14px 6px 0">
    <div style="font-size:14px;font-weight:700;border-left:3px solid {MARCA};padding-left:8px">Holgura histórica (últimos 2 años)</div>
    <div style="font-size:12px;color:{SEC};margin:2px 0 6px">Cuánto se podía pagar por la fruta cada semana vs. lo que marcaba el SNIIM ·
      desde {hh['desde']}, hubo holgura en {hh['semanas_con_holgura']}% de las semanas</div>
    <img src="{src_grafica}" width="588" alt="Gráfica de holgura histórica" style="width:100%;max-width:588px;height:auto;border:1px solid {BORDE};border-radius:8px;display:block">
  </td></tr>"""
    insights = "".join(
        f"<li style='margin:0 0 8px'><b>{escape(i['titulo'])}.</b> <span style='color:{SEC}'>{escape(i['texto'])}</span></li>"
        for i in d["insights"][:3])

    html = f"""<!doctype html><html><body style="margin:0;background:{FONDO};font-family:Segoe UI,Roboto,Arial,sans-serif;color:{TINTA}">
<div style="display:none;max-height:0;overflow:hidden">Podemos pagar hasta ${p['precio_max_fruta']:,.2f}/kg por la fruta; SNIIM ${p['precio_fruta_kg']:,.2f}/kg</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{FONDO}"><tr><td align="center" style="padding:20px 10px">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%">
  <tr><td style="padding:0 6px 10px">
    <div style="background:{MARCA_F};border-radius:10px;padding:14px 18px">
      <div style="font-size:12px;color:#d7e9d5;letter-spacing:.02em">ELE FOODS · MESA DEL LIMÓN PERSA</div>
      <div style="font-size:20px;font-weight:700;color:#ffffff;margin-top:2px">Pulso McAllen · {fecha_hoy}</div>
    </div>
    <div style="font-size:12px;color:{SEC};margin-top:8px">Pesos por caja de 40 lb, con precios del último cierre. {fuentes}.</div>
  </td></tr>
  <tr><td style="padding:6px">
    <div style="border:1px solid {BORDE};border-left:5px solid {MARCA};border-radius:10px;padding:14px 16px;background:#ffffff">
      {alerta_html}<div style="font-size:13px;color:{SEC}">Holgura de hoy</div>
      <div style="font-size:32px;font-weight:700;color:{color_h}">{signo_h}${abs(h):,.2f}/kg</div>
      <div style="font-size:15px">Hoy podemos pagar hasta <b>${p['precio_max_fruta']:,.2f}/kg</b> por la fruta sin perder.
        El SNIIM marca <b>${p['precio_fruta_kg']:,.2f}/kg</b>.</div>
      <div style="font-size:13px;margin-top:6px">{linea_contexto}</div>
      <div style="font-size:12px;color:{MUTED};margin-top:6px">Precio de venta en McAllen menos empaque, transporte y aduanas, más venta de merma,
        entre los kilos de fruta de un camión (rendimiento {p['rendimiento']:g}%). El SNIIM es el precio público en central de abasto:
        una referencia, no lo que pagamos exactamente. Por eso el costo y el margen de abajo son estimados.</div>
    </div></td></tr>
  <tr><td><table role="presentation" width="100%" cellpadding="0" cellspacing="0">
    <tr>{tarjeta("Precio de venta / caja", peso(p['venta']), f"{p['usd_caja']:.2f} USD · FOB McAllen")}
        {tarjeta("Costo puesto (estimado)", peso(p['costo']), f"{peso(p['costo'] * p['cajas'])} por camión")}</tr>
    <tr>{tarjeta("Margen bruto / caja (est.)", peso(p['margen']), f"{p['pct']:.1%} sobre venta", color)}
        {tarjeta("Utilidad / camión (est.)", peso(p['utilidad_camion']), f"{p['cajas']:,} cajas", color)}</tr>
  </table></td></tr>
  <tr><td style="padding:10px 6px 0">
    <div style="font-size:14px;font-weight:700;border-left:3px solid {MARCA};padding-left:8px">Proyección semanal</div>
    <div style="font-size:12px;color:{SEC};margin:2px 0 6px">{p['camiones']} camiones de {p['cajas']:,} cajas</div>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="font-size:14px">
      <tr><td style="color:{SEC}">Volumen</td><td style="text-align:right">{p['semana_cajas']:,} cajas</td></tr>
      <tr><td style="color:{SEC}">Ventas</td><td style="text-align:right">{peso(p['semana_ventas'])}</td></tr>
      <tr><td style="color:{SEC}">Utilidad bruta</td><td style="text-align:right;font-weight:700;color:{color}">{peso(p['semana_utilidad'])}</td></tr>
    </table>
  </td></tr>
  <tr><td style="padding:14px 6px 0">
    <div style="font-size:14px;font-weight:700;margin-bottom:4px;border-left:3px solid {MARCA};padding-left:8px">Costo puesto por caja (MXN)</div>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="font-size:14px">{desglose}
      <tr><td style="padding:4px 0;font-weight:700;border-top:1px solid {MUTED}">Total</td>
          <td style="padding:4px 0;text-align:right;font-weight:700;border-top:1px solid {MUTED}">{p['costo']:,.2f}</td></tr>
      <tr><td style="padding:6px 0 0;color:{SEC}">Precio máximo de la fruta sin perder</td>
          <td style="padding:6px 0 0;text-align:right">{p['precio_max_fruta']:.2f} MXN/kg</td></tr>
    </table>
  </td></tr>
  <tr><td style="padding:14px 6px 0">
    <div style="font-size:14px;font-weight:700;margin-bottom:6px;border-left:3px solid {MARCA};padding-left:8px">Lo relevante del mercado</div>
    <ul style="margin:0;padding-left:18px;font-size:13px;line-height:1.45">{insights}</ul>
  </td></tr>
  {grafica}
  <tr><td align="center" style="padding:18px 6px">
    <a href="{URL_DASHBOARD}" style="background:{MARCA_F};color:#ffffff;text-decoration:none;padding:10px 20px;border-radius:6px;font-size:14px;font-weight:600;display:inline-block">Abrir dashboard</a>
  </td></tr>
  <tr><td style="padding:0 6px;font-size:11px;color:{MUTED}">
    Costos: {escape(costos['fuente'])}. Por confirmar: {escape(costos['por_confirmar'])}.
    Precios: USDA (FOB McAllen), Banxico (FIX) y SNIIM (fruta). Correo automático de lunes a viernes.
  </td></tr>
</table></td></tr></table></body></html>"""

    texto = (f"Pulso McAllen · {fecha_hoy}\n{fuentes}\n\n"
             f"Holgura: {signo_h}${abs(h):.2f}/kg. Podemos pagar hasta ${p['precio_max_fruta']:.2f}/kg; "
             f"SNIIM marca ${p['precio_fruta_kg']:.2f}/kg.\n\n"
             f"Precio de venta / caja: {peso(p['venta'])}\nCosto puesto McAllen: {peso(p['costo'])}\n"
             f"Margen bruto / caja: {peso(p['margen'])} ({p['pct']:.1%})\n"
             f"Utilidad bruta / camión: {peso(p['utilidad_camion'])}\n"
             f"Semana ({p['camiones']} camiones): ventas {peso(p['semana_ventas'])}, utilidad {peso(p['semana_utilidad'])}\n\n"
             f"Dashboard: {URL_DASHBOARD}\n")
    return asunto, html, texto


def lista(v):
    return [x.strip() for x in (v or "").split(",") if x.strip()]


def main():
    d = json.loads((AQUI / "pulso.json").read_text(encoding="utf-8"))
    if not d.get("pulso"):
        sys.exit("pulso.json no tiene precio de USDA; no se envía el correo")
    png = grafica_holgura(d["holgura_hist"]) if d.get("holgura_hist") else None

    if "--vista" in sys.argv:
        asunto, html, texto = armar(d, "correo_grafica.png" if png else None)
        if png:
            (AQUI / "correo_grafica.png").write_bytes(png)
        (AQUI / "correo_vista.html").write_text(html, encoding="utf-8")
        print("Asunto:", asunto)
        print("Vista previa:", AQUI / "correo_vista.html")
        return

    usuario, clave = os.getenv("SMTP_USUARIO", "").strip(), os.getenv("SMTP_CLAVE", "").replace(" ", "").strip()
    if not usuario or not clave:
        sys.exit("Falta SMTP_USUARIO o SMTP_CLAVE")
    para, cc = ([usuario], []) if "--prueba" in sys.argv else (lista(os.getenv("CORREO_PARA")), lista(os.getenv("CORREO_CC")))
    if not para:
        sys.exit("Falta CORREO_PARA")

    asunto, html, texto = armar(d, "cid:holgura" if png else None)
    msg = EmailMessage()
    msg["Subject"] = asunto
    msg["From"] = f"Pulso McAllen <{usuario}>"
    msg["To"] = ", ".join(para)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg.set_content(texto)
    msg.add_alternative(html, subtype="html")
    if png:  # la gráfica va incrustada en el cuerpo, no como adjunto
        msg.get_payload()[1].add_related(png, maintype="image", subtype="png", cid="<holgura>", filename="holgura.png")

    servidor = os.getenv("SMTP_SERVIDOR", "smtp.gmail.com").strip()
    with smtplib.SMTP(servidor, 587, timeout=60) as s:
        s.starttls()
        s.login(usuario, clave)
        s.send_message(msg)
    print(f"Enviado a {len(para) + len(cc)} destinatario(s): {asunto}")


if __name__ == "__main__":
    main()
