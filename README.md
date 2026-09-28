# Mesa del limón persa · Ele Foods

Dashboard de precios del limón persa mexicano: FOB McAllen, mayoreo en Los Ángeles y Nueva York,
estacionalidad, tipo de cambio, exportaciones y calculadora de costo puesto.

Se actualiza solo de lunes a viernes a las 17:00 y 20:00 (hora CDMX) con GitHub Actions
y se publica en GitHub Pages.

## Fuentes
- USDA AMS Market News: FOB McAllen (IX_FV110) y mercados terminales de LA (HC_FV010) y NY (NX_FV010)
- Banxico SIE: FIX USD/MXN (SF43718) y EUR/MXN (SF46410)
- UN Comtrade: exportaciones de México HS 0805.50 e importaciones espejo de Europa
- US Census: importaciones de EE. UU. de limón persa desde México (HTS 0805.50.30)

## Archivos
- `historico.py`: descarga los datos (incremental) y genera el dashboard
- `plantilla_dashboard.html`: diseño del dashboard
- `datos/`: histórico descargado (CSV)
- `.github/workflows/actualizar.yml`: actualización automática

## Correr en local
```
py -m pip install -r requirements.txt
py historico.py
```
Requiere un archivo `.env` (no se sube) con `USDA_KEY`, `BANXICO_TOKEN`, `COMTRADE_KEY` y `CENSUS_KEY`.
En GitHub, esas llaves van en Settings → Secrets and variables → Actions.

## Costos
Los costos de la calculadora son supuestos de ejemplo. Lo que cada persona captura se guarda
solo en su navegador; no se publica.
