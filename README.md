# Simulador D'Hondt · Generales 29N 2026

Mapa provincial + hemiciclo + coaliciones drag & drop para las elecciones del 29 de noviembre de 2026
(RD 806/2026: 350 escaños, Madrid 38, Cádiz 8).

## Estructura

```
index.html, lab.html     simulador y visor del laboratorio del modelo
css/, js/                interfaz (js/engine.js: estimación provincial y D'Hondt; js/senate.js: Senado)
data/                    datos que lee la web (generados por el pipeline)
server.py                servidor local + botón «Actualizar encuestas»
pipeline/scrape/         scrapers de encuestas y resultados → raw/
pipeline/model/          aprendizaje del modelo de encuestas (poll_accuracy, model_lab)
pipeline/build/          generación de data/ (media de encuestas, elasticidades)
raw/                     encuestas y resultados descargados
tests/                   pruebas (node tests/*.mjs, python tests/*.py)
```

## Instalación (solo para el pipeline)

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
```

## Ver el simulador

```bash
python server.py
```

Abrir http://localhost:8029. Es HTML/JS estático (D3 desde CDN), sin compilación; `server.py` solo añade el botón
«Actualizar encuestas» (stdlib de Python, escucha en 127.0.0.1).

## Actualizar encuestas (hasta el 29N)

Con `python server.py` en marcha, el botón «Actualizar encuestas» de la cabecera ejecuta los scrapers de encuestas
nacionales y del Senado y `pipeline/build/build_polls.py` (unos minutos, requiere internet), y recarga la media sin perder
coaliciones ni cambios manuales. Equivalente por consola:

```bash
.venv/Scripts/python pipeline/scrape/polls_national.py && .venv/Scripts/python pipeline/scrape/polls_electomania.py && .venv/Scripts/python pipeline/scrape/polls_senate.py && .venv/Scripts/python pipeline/build/build_polls.py
```

## Regenerar elasticidades (solo si cambian resultados históricos o el linaje de partidos)

```bash
.venv/Scripts/python pipeline/build/build_model.py
```

## Modelo de encuestas (solo tras una nueva elección)

`pipeline/model/poll_accuracy.py` aprende de 40 elecciones españolas 2018-2026 (generales, europeas, autonómicas;
~3.300 encuestas de Wikipedia y del archivo de Electomanía) cómo fallan las encuestas, y escribe `data/poll_model.json`:
fiabilidad de cada casa, sesgo histórico de la industria por partido y error de la media según los días que faltan.
Validación: validación cruzada agrupada por elección y estratificada por tipo x época (5 pliegues x 3 repeticiones) y
validación temporal móvil (aprende del pasado, predice cada año 2021-2026). Informe en `raw/polls/accuracy_report.txt`.

```bash
.venv/Scripts/python pipeline/scrape/polls_history.py
.venv/Scripts/python pipeline/scrape/polls_electomania_archive.py --relist
.venv/Scripts/python pipeline/model/poll_accuracy.py
```

`pipeline/model/model_lab.py` (necesita `scikit-learn`) prueba ~100 epochs de algoritmos de agregación: última encuesta,
medias móviles, LOESS, Holt, Kalman, procesos gaussianos, correctores ML (Ridge, kNN, random forest, gradient boosting,
red neuronal; incluye la participación), ensembles, stacking y búsqueda genética. Aparta el 20 % de las elecciones como
prueba final y guarda la receta ganadora en `data/poll_model.json`, que `build_polls.py` aplica. Se sigue en directo en
`lab.html` (botón «Laboratorio»).

```bash
.venv/Scripts/python pipeline/model/model_lab.py
```

## Cómo funciona

1. **Resultados históricos** (`raw/results/`, scrapers en `pipeline/scrape/`): generales 2016-2023, europeas 2019/2024 y
   todas las autonómicas desde 2016, por provincia. La columna `party_ids` asigna cada candidatura histórica a los
   partidos actuales (p. ej. Unidas Podemos → `fa;podemos`).
2. **Media de encuestas** (`pipeline/build/build_polls.py` → `data/polls.json`): aplica la receta ganadora del
   laboratorio (`data/poll_model.json`), con sus partes aprendidas ajustadas a todas las elecciones pasadas.
3. **Elasticidades** (`pipeline/build/build_model.py` → `data/party_elasticity.json`, `data/provinces_data.json`):
   E = % provincial / % nacional en cada elección, promediado con pesos por tipo de elección y antigüedad que se eligen
   por backtest (predecir 10N 2019 y 23J 2023 solo con datos anteriores). `data/backtest.json` guarda el resultado.
4. **Simulador** (`js/engine.js`, probado con `node tests/engine.test.mjs`): % nacional × elasticidad, ajuste
   proporcional iterativo (cada provincia suma 100 % y cada partido reproduce su % nacional), coaliciones
   (a la coalición / trasvase a otro partido / abstención), barrera del 3 % del voto válido y D'Hondt.
